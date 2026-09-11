"""Web 服务冒烟测试:端点契约、入参校验、滥用防护。

起真实 HTTP 服务跑,确保 web/server.py 的包装层和 CLI 用的是同一套引擎、
同一个结论,并且异常输入不会 500。
"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import _path  # noqa: F401  — 把 scripts/ 挂上 sys.path

ROOT = _path.ROOT
sys.path.insert(0, os.path.join(ROOT, "web"))

import server as websrv  # noqa: E402
from lib import scoring, htmldoc  # noqa: E402


ACCESS_LOG = (
    '1.2.3.4 - - [21/Jun/2026:10:00:00 +0800] "GET /a HTTP/1.1" 200 1 "-" "GPTBot/1.0"\n'
    '5.6.7.8 - - [21/Jun/2026:10:01:00 +0800] "GET /b HTTP/1.1" 200 1 "-" "Bytespider"\n'
    '9.9.9.9 - - [21/Jun/2026:10:02:00 +0800] "GET /c HTTP/1.1" 200 1 "-" "Mozilla/5.0"\n')


RECORDS = [
    {"prompt": "乙哪个好", "engine": "豆包", "answer": "推荐甲和丙。",
     "cited_urls": ["https://a.com/x"]},
    {"prompt": "甲免费吗", "engine": "元宝", "answer": "甲是收费的，每月199元。"},
]
FACTS = [{"attribute": "定价", "truth": "专业版每月99元", "wrong": ["每月199元"]}]


def read_fixture(name):
    with open(_path.fixture(name), encoding="utf-8") as fh:
        return fh.read()


class WebServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), websrv.Handler)
        cls.srv.daemon_threads = True
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()
        cls.good = read_fixture("good_page.html")
        cls.poor = read_fixture("poor_page.html")
        # 限流是进程级共享状态,单测里放开,避免用例之间互相打满窗口
        cls._rate = websrv.RATE_LIMIT
        websrv.RATE_LIMIT = 10 ** 6

    @classmethod
    def tearDownClass(cls):
        websrv.RATE_LIMIT = cls._rate
        cls.srv.shutdown()
        cls.thread.join(timeout=5)
        cls.srv.server_close()

    def post(self, path, payload, raw=False):
        req = urllib.request.Request(
            self.base + path,
            data=payload if raw else json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode("utf-8")

    # ---- 契约 ----
    def test_health_and_index(self):
        for path, needle in (("/healthz", "ok"), ("/", "<!doctype html>")):
            with urllib.request.urlopen(self.base + path, timeout=10) as r:
                self.assertEqual(r.status, 200)
                self.assertIn(needle, r.read().decode("utf-8").lower())

    def test_api_listing_matches_routes(self):
        with urllib.request.urlopen(self.base + "/api", timeout=10) as r:
            listed = json.load(r)["endpoints"]
        self.assertEqual(sorted(websrv.ROUTES), listed)

    def test_score_matches_engine_directly(self):
        """Web 层不得改变评分结论——必须和直接调 scoring 完全一致。"""
        status, body = self.post("/api/score", {"html": self.good})
        self.assertEqual(status, 200)
        direct = scoring.score_document(htmldoc.from_string(self.good))
        self.assertEqual(json.loads(body)["score"], direct["score"])

    def test_every_endpoint_answers_200(self):
        cases = [
            ("/api/score", {"html": self.good}),
            ("/api/score.html", {"html": self.good}),
            ("/api/cescore", {"html": self.good, "queries": ["GEO是什么"], "annotate": True}),
            ("/api/diagnose", {"html": self.good}),
            ("/api/agentready", {"html": self.good}),
            ("/api/playbook", {"html": self.good, "brand": "甲", "category": "乙",
                               "engines": ["豆包"], "competitors": ["丙"]}),
            ("/api/playbook.html", {"html": self.good, "brand": "甲", "category": "乙"}),
            ("/api/playbook.md", {"html": self.good, "brand": "甲", "category": "乙"}),
            ("/api/rewrite", {"html": self.good, "engines": ["perplexity"]}),
            ("/api/prompts", {"brand": "甲", "category": "乙"}),
            ("/api/prompts.csv", {"brand": "甲", "category": "乙"}),
            ("/api/rewrite.md", {"html": self.good, "engines": ["perplexity"]}),
            ("/api/compare", {"pages": [{"label": "你", "html": self.good},
                                        {"label": "竞品A", "html": self.poor}]}),
            ("/api/compare.md", {"pages": [{"html": self.good}, {"html": self.poor}]}),
            ("/api/sourcing", {"category": "乙", "engines": ["豆包"]}),
            ("/api/recommend", {"engines": ["cn-all"]}),
            ("/api/recommend", {"reverse": "知乎"}),
            ("/api/intent", {"query": "哪个好"}),
            ("/api/robots", {"strategy": "expose-only"}),
            ("/api/llms", {"site": "甲", "summary": "乙", "links": "https://x.com | 文档"}),
            ("/api/schema", {"type": "faqpage", "qa": ["问::答"]}),
            ("/api/brief", {"topic": "甲", "question": "甲怎么做", "sections": ["X是什么"]}),
            ("/api/brief.md", {"topic": "甲"}),
            ("/api/files", {"site": "甲", "url": "https://x.com", "author": "乙"}),
            ("/api/attribution", {"url": "https://x.com"}),
            ("/api/attribution-log", {"log": ACCESS_LOG}),
            ("/api/hreflang", {"locales": ["zh-CN::https://x.com/zh"]}),
            ("/api/hreflang", {"locales": [{"lang": "en", "url": "https://x.com/en"}]}),
            ("/api/baidu-push", {"site": "https://x.com", "urls": ["https://x.com/a"]}),
            ("/api/baidu-index-check", {"site": "x.com"}),
            ("/api/cwv", {"lcp": 3.2, "inp": 180, "cls": 0.05}),
            ("/api/token", {"text": "中文 english", "budget": 100}),
            ("/api/batch", {"pages": [{"label": "a", "html": self.good},
                                      {"label": "b", "html": self.poor}]}),
            ("/api/batch.html", {"pages": [{"html": self.good}]}),
            ("/api/report.sarif", {"html": self.good, "page_uri": "a.html"}),
            ("/api/cannibalize", {"pages": [{"label": "a", "html": self.good},
                                            {"label": "b", "html": self.good}]}),
            ("/api/internal-links", {"pages": [{"label": "a", "html": self.good},
                                               {"label": "b", "html": self.poor}],
                                     "home": "a"}),
            ("/api/measure-kit", {"brand": "甲", "category": "乙", "engines": ["豆包"]}),
            ("/api/measure-kit.md", {"brand": "甲", "category": "乙"}),
            ("/api/measure", {"records": RECORDS, "brand": "甲", "competitors": ["丙"],
                              "facts": FACTS, "brand_domain": "a.com"}),
            ("/api/measure.md", {"records": RECORDS, "brand": "甲"}),
            ("/api/sov", {"records": RECORDS, "brand": "甲", "competitors": ["丙"]}),
            ("/api/lostprompt", {"records": RECORDS, "brand": "甲", "competitors": ["丙"]}),
            ("/api/factcheck", {"records": RECORDS, "brand": "甲", "facts": FACTS}),
        ]
        for path, payload in cases:
            with self.subTest(path=path, payload=sorted(payload)):
                status, body = self.post(path, payload)
                self.assertEqual(status, 200, body[:200])
                self.assertTrue(body.strip())

    def test_llms_sections_are_well_formed(self):
        """gen_llms_txt 要的是带 title 的 section,直接塞 links 会 KeyError。"""
        status, body = self.post("/api/llms", {
            "site": "示例", "summary": "简介", "section": "文档",
            "links": "https://x.com/a | 上手 | 五分钟"})
        self.assertEqual(status, 200)
        self.assertIn("## 文档", body)
        self.assertIn("[上手](https://x.com/a): 五分钟", body)

    # ---- 入参校验 / 滥用 ----
    def test_bad_input_is_4xx_never_500(self):
        cases = [
            ("/api/score", b"not json", True, 400),
            ("/api/score", {}, False, 400),
            ("/api/score", {"html": 123}, False, 400),
            ("/api/score", {"html": "<p>x</p>", "market": "evil"}, False, 400),
            ("/api/schema", {"type": "../../etc/passwd"}, False, 400),
            ("/api/prompts", {"brand": "甲"}, False, 400),
            ("/api/sourcing", {}, False, 400),
            ("/api/recommend", {}, False, 400),
            ("/api/intent", {}, False, 400),
            ("/api/compare", {}, False, 400),
            ("/api/compare", {"pages": [{"html": "<p>x</p>"}]}, False, 400),
            ("/api/compare", {"pages": [{"html": "<p>x</p>"}, {"html": "  "}]}, False, 400),
            ("/api/compare", {"pages": [{"html": "<p>x</p>"}, "字符串不是对象"]}, False, 400),
            ("/api/brief", {}, False, 400),
            ("/api/hreflang", {"locales": ["没有分隔符"]}, False, 400),
            ("/api/hreflang", {}, False, 400),
            ("/api/baidu-push", {"site": "https://x.com"}, False, 400),
            ("/api/baidu-index-check", {}, False, 400),
            ("/api/cwv", {}, False, 400),
            ("/api/cwv", {"lcp": "快"}, False, 400),
            ("/api/token", {"text": "x", "budget": "abc"}, False, 400),
            ("/api/token", {}, False, 400),
            ("/api/attribution-log", {}, False, 400),
            ("/api/batch", {}, False, 400),
            ("/api/batch", {"pages": []}, False, 400),
            ("/api/batch", {"pages": [{"html": "  "}]}, False, 400),
            ("/api/cannibalize", {"pages": [{"html": "<p>x</p>"}], "threshold": "高"}, False, 400),
            ("/api/sov", {"brand": "甲"}, False, 400),
            ("/api/sov", {"records": RECORDS}, False, 400),
            ("/api/sov", {"records": "不是JSON", "brand": "甲"}, False, 400),
            ("/api/sov", {"records": [{"prompt": "x"}], "brand": "甲"}, False, 400),
            ("/api/factcheck", {"records": RECORDS, "brand": "甲"}, False, 400),
            ("/api/factcheck", {"records": RECORDS, "brand": "甲",
                                "facts": {"定价": "x"}}, False, 400),
            ("/api/factcheck", {"records": RECORDS, "brand": "甲",
                                "facts": [{"attribute": "a"}]}, False, 400),
            ("/api/nope", {}, False, 404),
        ]
        for path, payload, raw, want in cases:
            with self.subTest(path=path, payload=payload):
                status, _ = self.post(path, payload, raw=raw)
                self.assertEqual(status, want)

    def test_oversized_body_rejected(self):
        big = json.dumps({"html": "a" * (websrv.MAX_BODY + 1)}).encode("utf-8")
        status, _ = self.post("/api/score", big, raw=True)
        self.assertEqual(status, 413)

    def test_json_array_body_rejected(self):
        status, _ = self.post("/api/score", b"[1,2,3]", raw=True)
        self.assertEqual(status, 400)

    def test_static_serving_has_no_traversal(self):
        """静态服务只认白名单文件名,不拼用户输入。"""
        for path in ("/../server.py", "/..%2Fserver.py", "/static/../server.py"):
            req = urllib.request.Request(self.base + path)
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    body = r.read().decode("utf-8", "replace")
                self.assertNotIn("MAX_CONCURRENCY", body)
            except urllib.error.HTTPError as e:
                with e:
                    self.assertIn(e.code, (400, 404))

    def test_compare_ranks_better_page_first(self):
        """对标的排名必须和评分一致:好页在前,且第一项被认作「你」。"""
        status, body = self.post("/api/compare", {
            "pages": [{"label": "我方", "html": self.good},
                      {"label": "竞品A", "html": self.poor}]})
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertEqual(d["you"], "我方")
        self.assertEqual(d["you_rank"], 1)
        ranked = sorted(d["rows"], key=lambda r: r["rank"])
        self.assertEqual(ranked[0]["label"], "我方")
        self.assertGreater(ranked[0]["combined"], ranked[1]["combined"])

    def test_compare_page_cap(self):
        pages = [{"html": self.good} for _ in range(websrv.MAX_COMPARE_PAGES + 1)]
        status, _ = self.post("/api/compare", {"pages": pages})
        self.assertEqual(status, 400)

    def test_prompts_csv_is_parseable(self):
        import csv
        import io as _io
        status, body = self.post("/api/prompts.csv", {"brand": "甲", "category": "乙"})
        self.assertEqual(status, 200)
        rows = list(csv.DictReader(_io.StringIO(body)))
        self.assertTrue(rows)
        self.assertIn("prompt", rows[0])

    def test_attribution_log_splits_cn_and_overseas(self):
        """中外双轨归因:GPTBot 算海外、Bytespider 算国内,普通 UA 不计入。"""
        status, body = self.post("/api/attribution-log", {"log": ACCESS_LOG})
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertEqual(d["total_ai_hits"], 2)
        self.assertEqual(d["by_region"], {"cn": 1, "overseas": 1})

    def test_files_omits_sitemap_without_url(self):
        """没给 url 就不该编出 sitemap/feed。"""
        status, body = self.post("/api/files", {"site": "甲"})
        self.assertEqual(status, 200)
        names = set(json.loads(body)["files"])
        self.assertEqual(names, {"ai.txt", "robots.patch", "humans.txt"})

    def test_cwv_rates_each_metric(self):
        status, body = self.post("/api/cwv", {"lcp": 3.2, "inp": 180, "cls": 0.05})
        d = json.loads(body)
        self.assertEqual(d["metrics"]["lcp"]["rating"], "needs-improvement")
        self.assertEqual(d["metrics"]["inp"]["rating"], "good")
        self.assertIsInstance(d["overall"], str)

    def test_batch_sorts_weak_pages_first(self):
        """批量审计要弱页优先,用户先看到最该修的。"""
        status, body = self.post("/api/batch", {
            "pages": [{"label": "good", "html": self.good},
                      {"label": "poor", "html": self.poor}]})
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertEqual(d["count"], 2)
        self.assertEqual(d["pages"][0]["path"], "poor")
        self.assertLess(d["pages"][0]["score"], d["pages"][1]["score"])

    def test_batch_survives_one_bad_page(self):
        """单页解析失败不该拖垮整批。"""
        status, body = self.post("/api/batch", {
            "pages": [{"label": "ok", "html": self.good},
                      {"label": "weird", "html": "\x00\x01 not really html"}]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["count"], 2)

    def test_factcheck_flags_wrong_price(self):
        """facts 声明的错误说法出现在品牌邻近处,必须报冲突。"""
        status, body = self.post("/api/factcheck", {
            "records": RECORDS, "brand": "甲", "facts": FACTS})
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertEqual(d["conflict_count"], 1)
        self.assertEqual(d["conflicts"][0]["ai_said"], "每月199元")

    def test_measure_bundles_three_analyses(self):
        status, body = self.post("/api/measure", {
            "records": RECORDS, "brand": "甲", "competitors": ["丙"], "facts": FACTS})
        d = json.loads(body)
        for key in ("sov", "lostprompt", "factcheck"):
            self.assertIn(key, d)
        self.assertEqual(d["record_count"], len(RECORDS))

    def test_records_accepts_three_shapes(self):
        """数组、{"records":[...]}、整段 JSON 文本三种投法结果一致。"""
        base = {"brand": "甲"}
        outs = []
        for rec in (RECORDS, {"records": RECORDS}, json.dumps(RECORDS)):
            status, body = self.post("/api/sov", dict(base, records=rec))
            self.assertEqual(status, 200)
            outs.append(json.loads(body)["coverage"])
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[1], outs[2])

    def test_rate_limit_engages(self):
        websrv.RATE_LIMIT = 3
        websrv._hits.clear()
        try:
            codes = [self.post("/api/intent", {"query": "x"})[0] for _ in range(6)]
        finally:
            websrv.RATE_LIMIT = 10 ** 6
            websrv._hits.clear()
        self.assertIn(429, codes)
        self.assertEqual(codes[0], 200)


if __name__ == "__main__":
    unittest.main()
