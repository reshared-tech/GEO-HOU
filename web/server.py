#!/usr/bin/env python3
"""GEO-HOU HTTP 服务。

把 CLI 的诊断/产出能力包成 HTTP API + 一个网页控制台,供团队在线使用。

设计约束(与本项目主体一致):
  - 零依赖,仅 Python 标准库。Docker 镜像不需要 pip install。
  - 不接受文件路径,不接受 URL 抓取。输入只有请求体里的内联文本。
    评分引擎本身不发网络请求(全仓只用到 urllib.parse.quote),
    因此只要不引入抓取,这个服务就没有 SSRF 和路径穿越面。
  - 无状态,不落盘,不记录用户提交的正文。可随意横向扩容。

  python3 web/server.py --host 0.0.0.0 --port 8000
"""

import argparse
import json
import os
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from lib import (htmldoc, scoring, generators, diagnose as diaglib,  # noqa: E402
                 report as reportlib, content_engineering as celib,
                 playbook as playbooklib, agent_readiness, instruction as instrlib,
                 prompts as promptlib, sourcing as sourcinglib,
                 platform_recommend as recommendlib, intent as intentlib,
                 attribution as attrlib, cwv as cwvlib, token_budget as tblib,
                 measure as measurelib, sov as sovlib, lostprompt as lplib,
                 factcheck as fclib, cannibalize as canlib, internal_links as illib)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 单次请求体上限。评分是纯 Python CPU 计算,篇幅与耗时正相关,
# 用体积上限替代请求超时来兜住最坏情况。2MB 远大于任何正常网页正文。
MAX_BODY = int(os.environ.get("GEO_MAX_BODY", 2 * 1024 * 1024))
# 超限请求体的排空上限。必须先把客户端正在发的数据收完再回 413,否则客户端
# 拿到的是 broken pipe 而不是我们的错误信息;keep-alive 连接上的残留字节
# 还会被当成下一个请求解析。超过这个上限就不陪着读了,直接断连接。
DRAIN_CAP = int(os.environ.get("GEO_DRAIN_CAP", 8 * 1024 * 1024))
# 同时在跑的重计算任务上限,防止并发把 CPU 打满导致所有请求一起劣化
MAX_CONCURRENCY = int(os.environ.get("GEO_MAX_CONCURRENCY", 4))
# 单 IP 限流:窗口秒数内最多多少次 API 调用
RATE_WINDOW = int(os.environ.get("GEO_RATE_WINDOW", 60))
RATE_LIMIT = int(os.environ.get("GEO_RATE_LIMIT", 60))
# 置 1 时按 X-Forwarded-For 最右侧一跳之前的客户端 IP 限流。
# 只有确实挂在自己可信的反向代理后面才开:否则所有请求的来源都是代理 IP,
# 限流会从「按用户」退化成「全局」,一个人刷满就把所有人挡在外面。
TRUST_PROXY = os.environ.get("GEO_TRUST_PROXY", "") in ("1", "true", "yes")

_slots = threading.BoundedSemaphore(MAX_CONCURRENCY)
_hits = {}
_hits_lock = threading.Lock()

MARKETS = ("auto", "cn", "global")


class ApiError(Exception):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------
# 入参校验
# --------------------------------------------------------------------------
def need_html(payload):
    html = payload.get("html")
    if not isinstance(html, str) or not html.strip():
        raise ApiError("缺少 html 字段(把网页源码整段粘进来)")
    return htmldoc.from_string(html)


def opt_text(payload, key):
    v = payload.get(key)
    if v is None or v == "":
        return None
    if not isinstance(v, str):
        raise ApiError("%s 必须是字符串" % key)
    return v


def opt_market(payload):
    m = payload.get("market", "auto") or "auto"
    if m not in MARKETS:
        raise ApiError("market 只能是 %s 之一" % "/".join(MARKETS))
    return m


def opt_list(payload, key, limit=40):
    """取一个字符串列表,兼容前端传换行分隔的单串。"""
    v = payload.get(key)
    if v is None or v == "":
        return []
    if isinstance(v, str):
        v = [line for line in v.splitlines()]
    if not isinstance(v, list):
        raise ApiError("%s 必须是数组或换行分隔的字符串" % key)
    out = [str(x).strip() for x in v if str(x).strip()]
    if len(out) > limit:
        raise ApiError("%s 最多 %d 项" % (key, limit))
    return out


# --------------------------------------------------------------------------
# 各端点
# --------------------------------------------------------------------------
def api_score(payload):
    doc = need_html(payload)
    return scoring.score_document(
        doc,
        robots_text=opt_text(payload, "robots"),
        llms_text=opt_text(payload, "llms"),
        llms_full=bool(payload.get("llms_full")),
        ai_txt=bool(payload.get("ai_txt")),
        market=opt_market(payload))


def api_score_html(payload):
    return reportlib.to_html(api_score(payload))


def api_cescore(payload):
    doc = need_html(payload)
    queries = opt_list(payload, "queries")
    res = celib.score(doc, market=opt_market(payload), queries=queries)
    if payload.get("annotate"):
        res["annotation"] = celib.annotate(doc, queries=queries)
    return res


def api_diagnose(payload):
    return diaglib.diagnose(need_html(payload), market=opt_market(payload))


def api_agentready(payload):
    return agent_readiness.audit(need_html(payload))


def _playbook(payload):
    doc = need_html(payload)
    brand = opt_text(payload, "brand")
    category = opt_text(payload, "category")
    ctype = payload.get("content_type") or None
    if ctype and ctype not in ("video", "tech", "种草", "消费", "b2b", "b2c"):
        raise ApiError("content_type 取值非法")
    return playbooklib.generate(
        doc, brand, category,
        engines=opt_list(payload, "engines") or None,
        roots=opt_list(payload, "roots") or None,
        content_type=ctype,
        queries=opt_list(payload, "queries") or None,
        market=opt_market(payload),
        robots_text=opt_text(payload, "robots"),
        llms_text=opt_text(payload, "llms"),
        competitors=opt_list(payload, "competitors") or None)


def api_playbook(payload):
    return _playbook(payload)


def api_playbook_html(payload):
    return playbooklib.render_html(_playbook(payload))


def api_playbook_md(payload):
    return playbooklib.render_markdown(_playbook(payload))


MAX_COMPARE_PAGES = 6
MAX_BATCH_PAGES = 50
MAX_RECORDS = 2000


def _pages(payload, cap, first_required=True):
    """通用多页输入:pages: [{label, html}, ...]。"""
    pages = payload.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ApiError("需要 pages 数组,每项 {label, html}")
    if len(pages) > cap:
        raise ApiError("最多 %d 个页面" % cap)
    out = []
    for i, item in enumerate(pages):
        if not isinstance(item, dict):
            raise ApiError("pages[%d] 必须是对象" % i)
        html = item.get("html")
        if not isinstance(html, str) or not html.strip():
            if first_required or i == 0:
                raise ApiError("pages[%d] 缺少 html" % i)
            continue
        label = str(item.get("label") or ("page%d.html" % (i + 1))).strip()[:120]
        out.append((label, html))
    if not out:
        raise ApiError("没有可用的页面")
    return out


def _records(payload):
    """records 支持三种投法:JSON 数组、{"records":[...]}、整段 JSON 文本。"""
    raw = payload.get("records")
    if isinstance(raw, str):
        if not raw.strip():
            raise ApiError("records 为空")
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ApiError("records 不是合法 JSON:%s" % exc)
    if isinstance(raw, dict):
        raw = raw.get("records", [])
    if not isinstance(raw, list) or not raw:
        raise ApiError("records 需要是非空数组,每条 {prompt, engine, answer}")
    if len(raw) > MAX_RECORDS:
        raise ApiError("最多 %d 条记录" % MAX_RECORDS)
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise ApiError("records[%d] 必须是对象" % i)
        if not str(r.get("answer") or "").strip():
            raise ApiError("records[%d] 缺少 answer(AI 的回答原文)" % i)
    return raw


def _json_field(payload, key):
    """facts / aliases 这类既可给对象也可给 JSON 文本。"""
    v = payload.get(key)
    if v is None or v == "":
        return None
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError as exc:
            raise ApiError("%s 不是合法 JSON:%s" % (key, exc))
    return v


def _facts(payload, required=False):
    """facts: [{"attribute":属性, "truth":正确值, "wrong":[错误说法...]}]。

    形状错了要当场给出可读报错——底层 check() 会直接 AttributeError 变 500,
    用户看到的是「处理失败」,根本不知道自己把对象写成了字典。
    """
    facts = _json_field(payload, "facts")
    if not facts:
        if required:
            raise ApiError('factcheck 需要 facts,格式 [{"attribute":"定价",'
                           '"truth":"每月99元","wrong":["每月199元"]}]')
        return None
    if not isinstance(facts, list):
        raise ApiError('facts 必须是数组,形如 [{"attribute":"定价","truth":"每月99元",'
                       '"wrong":["每月199元"]}]')
    for i, f in enumerate(facts):
        if not isinstance(f, dict):
            raise ApiError("facts[%d] 必须是对象,含 attribute / truth / wrong" % i)
        if not str(f.get("truth") or "").strip():
            raise ApiError("facts[%d] 缺少 truth(正确值)" % i)
        wrong = f.get("wrong")
        if wrong is not None and not isinstance(wrong, list):
            raise ApiError("facts[%d].wrong 必须是数组" % i)
    return facts


def _need_brand(payload):
    brand = opt_text(payload, "brand")
    if not brand:
        raise ApiError("需要 brand(品牌名)")
    return brand


# ---- 多页审计 ----
def _batch(payload):
    """等价 report.batch_score,但吃内联内容而非文件路径(服务端不碰文件系统)。"""
    market = opt_market(payload)
    rows = []
    for label, html in _pages(payload, MAX_BATCH_PAGES):
        try:
            r = scoring.score_document(htmldoc.from_string(html), market=market)
            rows.append({"path": label, "score": r["score"], "grade": r["grade"],
                         "geo_score": r["geo_score"], "seo_score": r["seo_score"],
                         "vetoes": r["vetoes"], "weakest": r["weakest"]})
        except Exception as exc:            # noqa: BLE001 — 单页失败不该拖垮整批
            rows.append({"path": label, "score": None, "grade": "错误", "error": str(exc)})
    # 与 CLI 一致:弱页优先,错误页排最前
    rows.sort(key=lambda x: (x["score"] is not None, x["score"] if x["score"] is not None else 0))
    scored = [x["score"] for x in rows if x["score"] is not None]
    return {"count": len(rows), "pages": rows,
            "avg_score": round(sum(scored) / max(1, len(scored)), 1)}


def api_batch(payload):
    return _batch(payload)


def api_batch_html(payload):
    return reportlib.batch_to_html(_batch(payload))


def api_report_sarif(payload):
    doc = need_html(payload)
    result = scoring.score_document(
        doc, robots_text=opt_text(payload, "robots"),
        llms_text=opt_text(payload, "llms"), market=opt_market(payload))
    return reportlib.to_sarif(result, page_uri=opt_text(payload, "page_uri") or "page.html")


def api_cannibalize(payload):
    threshold = payload.get("threshold")
    kw = {}
    if threshold not in (None, ""):
        try:
            kw["threshold"] = float(threshold)
        except (TypeError, ValueError):
            raise ApiError("threshold 必须是数字")
    return canlib.analyze(_pages(payload, MAX_BATCH_PAGES), **kw)


def api_internal_links(payload):
    return illib.analyze(_pages(payload, MAX_BATCH_PAGES),
                         base_hosts=opt_list(payload, "hosts") or None,
                         home=opt_text(payload, "home"))


# ---- 监测闭环 ----
def _kit(payload):
    brand = opt_text(payload, "brand")
    category = opt_text(payload, "category")
    rows = promptlib.generate(brand or "品牌", category or "品类", limit=20) \
        if (brand and category) else []
    return measurelib.collection_kit(brand, opt_list(payload, "engines"), rows,
                                     competitors=opt_list(payload, "competitors") or None)


def api_measure_kit(payload):
    return _kit(payload)


def api_measure_kit_md(payload):
    return measurelib.render_kit(_kit(payload))


def _measure(payload):
    return measurelib.measure_all(
        _records(payload), _need_brand(payload),
        competitors=opt_list(payload, "competitors") or None,
        facts=_facts(payload),
        aliases=_json_field(payload, "aliases"),
        brand_domain=opt_text(payload, "brand_domain"))


def api_measure(payload):
    return _measure(payload)


def api_measure_md(payload):
    return measurelib.render_measure(_measure(payload))


def api_sov(payload):
    return sovlib.analyze(
        _records(payload), brand=_need_brand(payload),
        competitors=opt_list(payload, "competitors") or None,
        aliases=_json_field(payload, "aliases"),
        brand_domain=opt_text(payload, "brand_domain"),
        competitor_domains=_json_field(payload, "competitor_domains"))


def api_lostprompt(payload):
    return lplib.analyze(_records(payload), _need_brand(payload),
                         opt_list(payload, "competitors"),
                         aliases=_json_field(payload, "aliases"))


def api_factcheck(payload):
    facts = _facts(payload, required=True)
    return fclib.check(_records(payload), _need_brand(payload), facts,
                       aliases=_json_field(payload, "aliases"))


def _compare_pages(payload):
    """pages: [{label, html}, ...],第一个是你自己,其余是竞品。"""
    pages = payload.get("pages")
    if not isinstance(pages, list) or len(pages) < 2:
        raise ApiError("compare 需要 pages 数组,至少 2 项(第一项是你自己的页面)")
    if len(pages) > MAX_COMPARE_PAGES:
        raise ApiError("最多对比 %d 个页面" % MAX_COMPARE_PAGES)
    out = []
    for i, item in enumerate(pages):
        if not isinstance(item, dict):
            raise ApiError("pages[%d] 必须是对象" % i)
        html = item.get("html")
        if not isinstance(html, str) or not html.strip():
            raise ApiError("pages[%d] 缺少 html" % i)
        label = str(item.get("label") or ("你" if i == 0 else "竞品%d" % i)).strip()[:40]
        out.append((label, htmldoc.from_string(html)))
    return out


def _compare(payload):
    return playbooklib.compare(_compare_pages(payload),
                               brand=opt_text(payload, "brand"),
                               queries=opt_list(payload, "queries") or None,
                               market=opt_market(payload))


def api_compare(payload):
    return _compare(payload)


def api_compare_md(payload):
    return playbooklib.render_compare(_compare(payload))


def api_rewrite(payload):
    sc = api_score(payload)
    engines = opt_list(payload, "engines")
    return instrlib.compile_instructions(sc, target_engine=engines[0] if engines else None)


def api_rewrite_md(payload):
    return instrlib.render_markdown(api_rewrite(payload))


def api_prompts(payload):
    brand = opt_text(payload, "brand")
    category = opt_text(payload, "category")
    if not brand or not category:
        raise ApiError("prompts 需要 brand 和 category")
    return {"rows": promptlib.generate(brand, category,
                                       competitors=opt_list(payload, "competitors") or None,
                                       limit=min(int(payload.get("limit") or 20), 60))}


def api_prompts_csv(payload):
    return promptlib.to_csv(api_prompts(payload)["rows"])


def api_sourcing(payload):
    category = opt_text(payload, "category")
    if not category:
        raise ApiError("sourcing 需要 category")
    return sourcinglib.plan(category,
                            opt_list(payload, "roots") or [category],
                            opt_list(payload, "engines") or None,
                            content_type=payload.get("content_type") or None,
                            market=opt_market(payload))


def api_recommend(payload):
    # 反查:给平台,看它能喂哪些引擎
    rev = opt_text(payload, "reverse")
    if rev:
        return recommendlib.reverse(rev)
    engines = opt_list(payload, "engines")
    if not engines:
        raise ApiError("recommend 需要 engines(可用 cn-all / overseas-all)或 reverse 平台名")
    res = recommendlib.recommend(engines, content_type=payload.get("content_type") or None,
                                 top=int(payload["top"]) if payload.get("top") else None)
    # 拼错或未收录的引擎名会被静默忽略,显式回传,避免用户拿着空结果猜
    res["unrecognized"] = recommendlib.unrecognized(engines)
    return res


def api_intent(payload):
    q = opt_text(payload, "query")
    if not q:
        raise ApiError("intent 需要 query")
    return intentlib.classify(q)


def _brief(payload):
    topic = opt_text(payload, "topic")
    if not topic:
        raise ApiError("brief 需要 topic")
    question = opt_text(payload, "question") or topic
    brief = instrlib.gen_geo_brief(
        topic=topic, primary_question=question,
        sections=opt_list(payload, "sections"),
        entities=opt_list(payload, "entities") or None,
        paa_questions=opt_list(payload, "paa") or None,
        target_engine=(opt_list(payload, "engines") or [None])[0],
        lang=payload.get("lang") if payload.get("lang") in ("zh", "en") else "zh")
    # 与 CLI 一致:顺带把搜索意图判定挂上,决定内容类型与 schema
    intent_res = intentlib.classify(question)
    brief["search_intent"] = {
        "intent": intent_res["intent"], "confidence": intent_res["confidence"],
        "content_type": intent_res["content_type"],
        "recommended_schema": intent_res["recommended_schema"],
        "tactic": intent_res["geo_seo_tactic"],
    }
    return brief


def api_brief(payload):
    return _brief(payload)


def api_brief_md(payload):
    return instrlib.render_brief_markdown(_brief(payload))


def api_files(payload):
    """CLI 版写一目录文件;Web 版把内容原样返回,由前端逐个展示/下载。"""
    allow_train = bool(payload.get("allow_train"))
    site = opt_text(payload, "site")
    url = opt_text(payload, "url")
    date = opt_text(payload, "date")
    author = opt_text(payload, "author") or "Team"
    files = {
        "ai.txt": generators.gen_ai_txt(allow_train=allow_train),
        "robots.patch": generators.gen_robots_patch(allow_train=allow_train),
        "humans.txt": generators.gen_humans_txt(
            team=[("CTO", author)], site=site,
            standards=["HTML5", "JSON-LD", "llms.txt"], last_update=date),
    }
    if url:
        files["sitemap.xml"] = generators.gen_sitemap([(url, date)])
        files["feed.xml"] = generators.gen_feed_xml(
            site or "Site", url,
            [("最新内容", date or "Sat, 21 Jun 2026 00:00:00 GMT", url)])
    return {"files": files, "count": len(files),
            "note": "url 留空则不生成 sitemap.xml / feed.xml。"}


def api_attribution(payload):
    return attrlib.render_kit(site_url=opt_text(payload, "url"))


def api_attribution_log(payload):
    log = opt_text(payload, "log")
    if not log:
        raise ApiError("需要 log(把访问日志内容粘进来)")
    return attrlib.parse_access_log(log)


def api_hreflang(payload):
    """locales 支持 "zh-CN::https://..." 或 {"lang":..,"url":..}"""
    raw = payload.get("locales")
    pairs = []
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        for i, x in enumerate(raw):
            lang, url = str(x.get("lang", "")).strip(), str(x.get("url", "")).strip()
            if not lang or not url:
                raise ApiError("locales[%d] 需要 lang 和 url" % i)
            pairs.append((lang, url))
    else:
        for item in opt_list(payload, "locales"):
            if "::" not in item:
                raise ApiError('locale 格式应为 "zh-CN::https://example.com/zh",收到: %s' % item)
            lang, url = item.split("::", 1)
            pairs.append((lang.strip(), url.strip()))
    if not pairs:
        raise ApiError("hreflang 需要至少一个 locale")
    return generators.gen_hreflang(pairs, x_default=opt_text(payload, "x_default"))


def api_baidu_push(payload):
    site = opt_text(payload, "site")
    urls = opt_list(payload, "urls", limit=500)
    if not site:
        raise ApiError("baidu-push 需要 site")
    if not urls:
        raise ApiError("baidu-push 需要至少一个 url")
    return generators.gen_baidu_push(site, opt_text(payload, "token") or "你的TOKEN",
                                     urls, fast=bool(payload.get("fast")))


def api_baidu_index_check(payload):
    site = opt_text(payload, "site")
    if not site:
        raise ApiError("需要 site(域名)")
    return ("# 百度/国产搜索收录状态自查(工具不联网,给查法)\n"
            "# 1) 收录量粗查:在百度搜索框输入(整页是否被收录):\n"
            "site:%s\n"
            "# 2) 单页是否收录:\n"
            "site:%s inurl:你的路径\n"
            "# 3) 权威看后台:百度搜索资源平台 > 数据监控 > 索引量 / 抓取诊断 / 抓取频次\n"
            "# 4) 神马/搜狗同理用各自 site: 语法;收录是国产 AI 可见的前置(求收录占平台)。\n"
            % (site, site))


def _num(payload, key):
    v = payload.get(key)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ApiError("%s 必须是数字" % key)


def api_cwv(payload):
    metrics = {}
    for k in ("lcp", "inp", "cls"):
        v = _num(payload, k)
        if v is not None:
            metrics[k] = v
    if not metrics:
        raise ApiError("至少填一项 lcp / inp / cls(从 PageSpeed Insights 抄)")
    return cwvlib.assess(metrics)


def api_token(payload):
    text = opt_text(payload, "text") or opt_text(payload, "html") or ""
    if not text.strip():
        raise ApiError("token 需要 text")
    budget = payload.get("budget")
    if budget:
        try:
            return tblib.check(text, int(budget))
        except (TypeError, ValueError):
            raise ApiError("budget 必须是整数")
    return {"approx_tokens": tblib.estimate(text)}


def api_robots(payload):
    strategy = payload.get("strategy", "allow-all")
    if strategy not in ("allow-all", "expose-only", "cn-index"):
        raise ApiError("strategy 只能是 allow-all / expose-only / cn-index")
    return generators.gen_robots(strategy=strategy,
                                 sitemap=opt_text(payload, "sitemap"),
                                 disallow_paths=opt_list(payload, "disallow") or None)


def api_llms(payload):
    site = opt_text(payload, "site")
    summary = opt_text(payload, "summary")
    if not site or not summary:
        raise ApiError("llms 需要 site 和 summary")
    links = opt_text(payload, "links")
    # gen_llms_txt 要的是 [{"title":..., "links":[...]}],parse_links_file 只出 links
    sections = ([{"title": payload.get("section") or "文档",
                  "links": generators.parse_links_file(links)}] if links else None)
    return generators.gen_llms_txt(site, summary, sections=sections,
                                   body=opt_text(payload, "body"))


_SCHEMA = {
    "article": lambda p: generators.gen_article(
        p.get("title", ""), p.get("description", ""), author=p.get("author"),
        org=p.get("org"), url=p.get("url")),
    "faqpage": lambda p: generators.gen_faqpage(
        [tuple(x.split("::", 1)) for x in opt_list(p, "qa") if "::" in x]),
    "howto": lambda p: generators.gen_howto(p.get("name", ""), opt_list(p, "steps")),
    "product": lambda p: generators.gen_product(
        p.get("name", ""), p.get("description", ""), brand=p.get("brand"),
        price=p.get("price"), currency=p.get("currency", "CNY")),
    "organization": lambda p: generators.gen_organization(
        p.get("name", ""), url=p.get("url"), logo=p.get("logo"),
        same_as=opt_list(p, "same_as") or None),
    "website": lambda p: generators.gen_website(p.get("name", ""), p.get("url", "")),
}


def api_schema(payload):
    t = payload.get("type")
    if t not in _SCHEMA:
        raise ApiError("type 只能是 %s 之一" % "/".join(_SCHEMA))
    return generators.to_script(_SCHEMA[t](payload))


# path -> (handler, content_type)。text/* 的 handler 返回字符串,其余返回可 JSON 化对象。
ROUTES = {
    "/api/score":          (api_score,        "json"),
    "/api/score.html":     (api_score_html,   "html"),
    "/api/cescore":        (api_cescore,      "json"),
    "/api/diagnose":       (api_diagnose,     "json"),
    "/api/agentready":     (api_agentready,   "json"),
    "/api/playbook":       (api_playbook,     "json"),
    "/api/playbook.html":  (api_playbook_html, "html"),
    "/api/playbook.md":    (api_playbook_md,  "text"),
    "/api/batch":          (api_batch,        "json"),
    "/api/batch.html":     (api_batch_html,   "html"),
    "/api/report.sarif":   (api_report_sarif, "text"),
    "/api/cannibalize":    (api_cannibalize,  "json"),
    "/api/internal-links": (api_internal_links, "json"),
    "/api/measure":        (api_measure,      "json"),
    "/api/measure.md":     (api_measure_md,   "text"),
    "/api/measure-kit":    (api_measure_kit,  "json"),
    "/api/measure-kit.md": (api_measure_kit_md, "text"),
    "/api/sov":            (api_sov,          "json"),
    "/api/lostprompt":     (api_lostprompt,   "json"),
    "/api/factcheck":      (api_factcheck,    "json"),
    "/api/compare":        (api_compare,      "json"),
    "/api/compare.md":     (api_compare_md,   "text"),
    "/api/rewrite":        (api_rewrite,      "json"),
    "/api/rewrite.md":     (api_rewrite_md,   "text"),
    "/api/prompts":        (api_prompts,      "json"),
    "/api/prompts.csv":    (api_prompts_csv,  "text"),
    "/api/sourcing":       (api_sourcing,     "json"),
    "/api/recommend":      (api_recommend,    "json"),
    "/api/intent":         (api_intent,       "json"),
    "/api/brief":          (api_brief,        "json"),
    "/api/brief.md":       (api_brief_md,     "text"),
    "/api/files":          (api_files,        "json"),
    "/api/attribution":    (api_attribution,  "text"),
    "/api/attribution-log": (api_attribution_log, "json"),
    "/api/hreflang":       (api_hreflang,     "text"),
    "/api/baidu-push":     (api_baidu_push,   "text"),
    "/api/baidu-index-check": (api_baidu_index_check, "text"),
    "/api/cwv":            (api_cwv,          "json"),
    "/api/token":          (api_token,        "json"),
    "/api/robots":         (api_robots,       "text"),
    "/api/llms":           (api_llms,         "text"),
    "/api/schema":         (api_schema,       "text"),
}

_CTYPE = {"json": "application/json; charset=utf-8",
          "html": "text/html; charset=utf-8",
          "text": "text/plain; charset=utf-8"}


def rate_ok(ip):
    now = time.time()
    with _hits_lock:
        seen = [t for t in _hits.get(ip, ()) if now - t < RATE_WINDOW]
        if len(seen) >= RATE_LIMIT:
            _hits[ip] = seen
            return False
        seen.append(now)
        _hits[ip] = seen
        if len(_hits) > 4096:        # 粗暴回收,防止长跑内存增长
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > RATE_WINDOW]:
                _hits.pop(k, None)
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "GeoHou"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # 只记方法/路径/状态,绝不记请求体——用户粘进来的是他们未发布的页面正文
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status, body, ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _client_ip(self):
        if TRUST_PROXY:
            xff = self.headers.get("X-Forwarded-For")
            if xff:
                # 代理自己追加的是最右一项,取最右是唯一不可被客户端伪造的那一跳
                return xff.split(",")[-1].strip()
        return self.client_address[0]

    def _drain(self, length):
        """收掉(并丢弃)客户端仍在发送的请求体,最多 DRAIN_CAP 字节。"""
        left = min(length, DRAIN_CAP)
        while left > 0:
            chunk = self.rfile.read(min(65536, left))
            if not chunk:
                break
            left -= len(chunk)

    def _err(self, status, msg, extra=None):
        self._send(status, json.dumps({"error": msg}, ensure_ascii=False),
                   "application/json; charset=utf-8", extra=extra)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            return self._send(HTTPStatus.OK, "ok")
        if path in ("/", "/index.html"):
            return self._serve_static("index.html")
        if path == "/api":
            return self._send(HTTPStatus.OK,
                              json.dumps({"endpoints": sorted(ROUTES)}, ensure_ascii=False),
                              "application/json; charset=utf-8")
        return self._err(HTTPStatus.NOT_FOUND, "not found")

    do_HEAD = do_GET

    def _serve_static(self, name):
        # 固定白名单文件名,不拼接用户输入,无穿越面
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as fh:
                body = fh.read()
        except OSError:
            return self._err(HTTPStatus.NOT_FOUND, "not found")
        # 控制台是单文件、无版本号指纹,缓存住会让重新部署后的用户拿到旧前端
        self._send(HTTPStatus.OK, body, "text/html; charset=utf-8",
                   extra={"Cache-Control": "no-cache"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        route = ROUTES.get(path)
        if route is None:
            return self._err(HTTPStatus.NOT_FOUND, "未知端点,GET /api 看可用列表")

        if not rate_ok(self._client_ip()):
            return self._err(HTTPStatus.TOO_MANY_REQUESTS,
                             "请求过于频繁,%d 秒内上限 %d 次" % (RATE_WINDOW, RATE_LIMIT))

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._err(HTTPStatus.BAD_REQUEST, "Content-Length 非法")
        if length <= 0:
            return self._err(HTTPStatus.BAD_REQUEST, "请求体为空")
        if length > MAX_BODY:
            self._drain(length)
            self.close_connection = True
            return self._err(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                             "请求体超过 %d 字节上限" % MAX_BODY,
                             extra={"Connection": "close"})

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return self._err(HTTPStatus.BAD_REQUEST, "请求体必须是 UTF-8 JSON")
        if not isinstance(payload, dict):
            return self._err(HTTPStatus.BAD_REQUEST, "请求体必须是 JSON 对象")

        handler, kind = route
        if not _slots.acquire(timeout=20):
            return self._err(HTTPStatus.SERVICE_UNAVAILABLE, "服务繁忙,请稍后重试")
        try:
            result = handler(payload)
        except ApiError as exc:
            return self._err(exc.status, exc.message)
        except Exception:                      # noqa: BLE001 — 兜底,不把栈泄给调用方
            traceback.print_exc()
            return self._err(HTTPStatus.INTERNAL_SERVER_ERROR, "处理失败,请检查输入")
        finally:
            _slots.release()

        if kind == "json":
            body = json.dumps(result, ensure_ascii=False)
        else:
            body = result
        self._send(HTTPStatus.OK, body, _CTYPE[kind])


def main():
    ap = argparse.ArgumentParser(description="GEO-HOU HTTP 服务")
    ap.add_argument("--host", default=os.environ.get("GEO_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("GEO_PORT", 8000)))
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    sys.stderr.write("GEO-HOU 服务已启动 http://%s:%d  (并发上限 %d, 体积上限 %d 字节)\n"
                     % (args.host, args.port, MAX_CONCURRENCY, MAX_BODY))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
