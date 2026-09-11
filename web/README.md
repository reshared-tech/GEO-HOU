# GEO-HOU Web 服务 · 部署与使用

把 CLI 的诊断/产出能力包成 HTTP API + 网页控制台。与主体项目一致：**零第三方依赖，纯 Python 标准库**，镜像不需要 `pip install`。

## 设计边界（先读这个）

| 项 | 说明 |
|---|---|
| **不抓取 URL** | 输入只有请求体里的内联 HTML。评分引擎本身从不发网络请求，只要不引入抓取，这个服务就没有 SSRF 面 |
| **不落盘** | 无状态，不写文件，不记录用户提交的正文（日志只记方法/路径/状态码）。可随意横向扩容 |
| **不联网** | 监测类能力（sov / factcheck）仍需人工去目标引擎采样后回传，服务不会替你去问 AI |

---

## 一、部署（Docker Compose，推荐）

```bash
git clone <你的仓库> geo-hou && cd geo-hou
docker compose up -d --build
curl http://127.0.0.1:8000/healthz     # -> ok
```

默认只绑 `127.0.0.1:8000`，由宿主机 nginx 做 TLS 和对外暴露。

### nginx 反向代理

```nginx
server {
    listen 443 ssl http2;
    server_name geo.example.com;

    ssl_certificate     /etc/letsencrypt/live/geo.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/geo.example.com/privkey.pem;

    # 网页源码整段粘贴，正文可能不小；要和服务端 GEO_MAX_BODY 对齐
    client_max_body_size 4m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # playbook 是重计算，给足超时
        proxy_read_timeout 120s;
    }
}
server {
    listen 80;
    server_name geo.example.com;
    return 301 https://$host$request_uri;
}
```

证书：`certbot --nginx -d geo.example.com`。

**注意**：挂在代理后面必须保持 `GEO_TRUST_PROXY=1`（compose 里已默认开）。否则所有请求的来源 IP 都是 nginx，限流会从「按用户」退化成「全局」，一个人刷满就把所有人挡在外面。反过来，**没有代理时绝不要开这个开关**，否则客户端可以伪造 `X-Forwarded-For` 绕过限流。

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `GEO_HOST` / `GEO_PORT` | `0.0.0.0` / `8000` | 监听地址 |
| `GEO_MAX_BODY` | `2097152`（2MB） | 单请求体上限。评分是 CPU 计算，用体积上限兜住最坏耗时 |
| `GEO_MAX_CONCURRENCY` | `4` | 同时在跑的重计算任务数，建议 = CPU 核数 |
| `GEO_RATE_LIMIT` / `GEO_RATE_WINDOW` | `60` / `60` | 每 IP 每窗口请求上限 |
| `GEO_TRUST_PROXY` | 关 | 见上 |

### 不用 Docker（systemd）

```ini
# /etc/systemd/system/geo-hou.service
[Unit]
Description=GEO-HOU Web
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/geo-hou
ExecStart=/usr/bin/python3 web/server.py --host 127.0.0.1 --port 8000
Restart=always
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now geo-hou
```

只要有 Python 3.9+ 就能跑，无需装任何包。

---

## 二、怎么用

### 网页控制台

浏览器打开 `https://geo.example.com`。导航分两级，按项目的「诊断 → 产出 → 度量 → 生成」四组划分，共 28 个功能，覆盖 CLI 全部 30 个命令。

**诊断**

| 功能 | 输入 | 产出 |
|---|---|---|
| 评分 | 源码 | 6 维 22 项，逐维进度条、否决项。下载 HTML 报告 |
| 内容工程 | 源码 + 目标问句 | 11 要素按层分组打分、最该先补清单、段落级改写指引 |
| 根因诊断 | 源码 | 7 类根因，命中的给修法 |
| Agent 就绪 | 源码 | WebMCP/agent 静态审计 + 反模式检测 |
| Core Web Vitals | LCP/INP/CLS | 评级与阈值对照。数值需从 PSI 抄，工具不代跑测速 |
| Token 预算 | 正文 + 预算 | 近似 token 估算 |
| 批量审计 | 多个页面源码 | 逐页得分表，弱页排最前。下载 HTML 报告 |
| 关键词蚕食 | 多个页面源码 | 抢同一意图的页面对 + 修法。静态重叠启发式，非 GSC 数据 |
| 内链审计 | 多个页面源码 + 首页 | 孤儿页、无出链页、各页入链数 |

**产出**

| 功能 | 输入 | 产出 |
|---|---|---|
| 作战手册 | 源码 + 品牌/品类/引擎/竞品/问句 | 八层瓶颈定位、ROI 行动清单、信源分层。下载可打印 HTML |
| 改写指令 | 源码 + 目标引擎 | 逐条指令卡（诊断/指令/方法/约束）。下载 Markdown |
| 内容简报 | 话题 + 问句 + 章节 | GEO brief 骨架 + 意图判定。下载 Markdown |
| 信源策略 | 品类 + 引擎 + 词根 | 层2 收录前置、层3 问句矩阵、层4 信源偏好、P0/P1/P2 投放 |

**度量**

| 功能 | 输入 | 产出 |
|---|---|---|
| 竞品对标 | 你的源码 + 1~5 个竞品源码 | 排名表、裁决、加权差距要素。下载 Markdown |
| Prompt 集 | 品牌 + 品类 + 竞品 | buyer prompt 表。下载 CSV |
| 平台推荐 | 目标引擎，或反查平台名 | 优先投放平台排序 / 该平台能喂哪些引擎 |
| 意图分类 | 问句 | 意图判定 + 内容类型 + schema + 战术 |
| 归因 | 站点 URL，或访问日志 | GA4 正则与 UTM 物料 / 日志里的 AI 爬虫中外分桶 |
| 采集工具包 | 品牌 + 品类 + 引擎 | 采样协议、要问的 prompt 清单、records 模板。下载 Markdown |
| 综合监测 | 采样记录 + 品牌 + 真相 | 一次出声量份额 + 竞品夺走 + 错误信息。下载报告 |
| 声量份额 | 采样记录 + 品牌 + 域名 | 三套 SoV 公式、采样置信度、自有/获得型引用拆分 |
| 竞品夺走 | 采样记录 + 品牌 + 竞品 | 竞品出现而你缺席的问句 |
| 错误信息 | 采样记录 + 品牌真相 | AI 说错你事实的地方 + 纠正方向 |

**生成**（均不需要网页源码）

| 功能 | 产出 |
|---|---|
| robots / llms / schema | robots.txt、llms.txt、6 类 JSON-LD |
| AI 发现文件 | ai.txt、robots.patch、humans.txt，填 URL 再加 sitemap.xml、feed.xml，逐个可下载 |
| 多语言 hreflang | hreflang 标注 |
| 百度主动推送 | 推送脚本（国内收录第一杠杆） |
| 收录自查 | 百度/神马/搜狗收录自查语法 |

字段随功能自动显隐。

### 采样记录怎么录

度量组的四个功能（综合监测 / 声量份额 / 竞品夺走 / 错误信息）要你**先去目标引擎实际提问**，把 AI 的回答原文贴回来。页面提供两种录入方式：

- **逐条录入**：每条填 问句 / 引擎 / AI 回答原文 / 引用 URL，可增删
- **粘贴 JSON**：把你维护的 `records.json` 整段贴进来，格式与 CLI 相同

先用「采集工具包」生成采样协议和要问的 prompt 清单，照着采样，再回来喂进这四个功能。

**服务不替你去问 AI，也不编造任何 AI 回答。** 记录只在单次请求内使用，用完即弃，不落盘。

拿网页源码的方式：浏览器右键「查看网页源代码」，全选复制粘贴。**别粘渲染后的 DOM**——评分要判断 CSR 空壳（AI 爬虫拿不到内容是个否决项），粘 DOM 会把这个问题掩盖掉。

### HTTP API

所有端点都是 `POST`，请求与响应均为 UTF-8 JSON（`.html` / `.md` 及生成类端点返回纯文本）。`GET /api` 列出全部端点。

```bash
# 评分
curl -sX POST https://geo.example.com/api/score \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c 'import json,sys;print(json.dumps({"html":open(sys.argv[1]).read()}))' page.html)" \
  | python3 -m json.tool

# 作战手册，直接存成可打印 HTML
curl -sX POST https://geo.example.com/api/playbook.html \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c 'import json,sys;print(json.dumps({
        "html": open(sys.argv[1]).read(), "brand":"智能排版器",
        "category":"公众号排版工具", "engines":["豆包","元宝"],
        "queries":["公众号排版工具哪个好"], "competitors":["壹伴"]}))' page.html)" \
  -o playbook.html

# 生成类不需要网页源码
curl -sX POST https://geo.example.com/api/robots \
  -H 'Content-Type: application/json' \
  -d '{"strategy":"expose-only","sitemap":"https://example.com/sitemap.xml"}'
```

| 端点 | 必填 | 可选 |
|---|---|---|
| `/api/score`、`/api/score.html` | `html` | `market` `robots` `llms` `llms_full` `ai_txt` |
| `/api/cescore` | `html` | `market` `queries[]` `annotate` |
| `/api/diagnose`、`/api/agentready` | `html` | `market` |
| `/api/playbook`、`.html`、`.md` | `html` | `brand` `category` `engines[]` `queries[]` `competitors[]` `roots[]` `content_type` `market` |
| `/api/rewrite`、`.md` | `html` | `engines[]`（取第一个作目标引擎） |
| `/api/compare`、`.md` | `pages[]`（`{label,html}`，首项是你，2~6 项） | `brand` `queries[]` `market` |
| `/api/prompts`、`.csv` | `brand` `category` | `competitors[]` `limit` |
| `/api/sourcing` | `category` | `roots[]` `engines[]` `content_type` `market` |
| `/api/recommend` | `engines[]` 或 `reverse` | `content_type` `top` |
| `/api/intent` | `query` | |
| `/api/robots` | | `strategy` `sitemap` `disallow[]` |
| `/api/llms` | `site` `summary` | `section` `links` `body` |
| `/api/schema` | `type` | 随类型而定（`title` `author` `qa[]` …） |
| `/api/brief`、`.md` | `topic` | `question` `sections[]` `entities[]` `paa[]` `engines[]` `lang` |
| `/api/files` | | `site` `url` `author` `date` `allow_train` |
| `/api/attribution` | | `url` |
| `/api/attribution-log` | `log` | |
| `/api/hreflang` | `locales[]`（`"zh-CN::url"` 或 `{lang,url}`） | `x_default` |
| `/api/baidu-push` | `site` `urls[]` | `token` `fast` |
| `/api/baidu-index-check` | `site` | |
| `/api/cwv` | `lcp` / `inp` / `cls` 至少一项 | |
| `/api/token` | `text` | `budget` |
| `/api/batch`、`.html` | `pages[]`（最多 50） | `market` |
| `/api/report.sarif` | `html` | `market` `page_uri` `robots` `llms` |
| `/api/cannibalize` | `pages[]` | `threshold` |
| `/api/internal-links` | `pages[]` | `home` `hosts[]` |
| `/api/measure-kit`、`.md` | | `brand` `category` `engines[]` `competitors[]` |
| `/api/measure`、`.md` | `records` `brand` | `competitors[]` `facts[]` `aliases` `brand_domain` |
| `/api/sov` | `records` `brand` | `competitors[]` `aliases` `brand_domain` `competitor_domains` |
| `/api/lostprompt` | `records` `brand` | `competitors[]` `aliases` |
| `/api/factcheck` | `records` `brand` `facts[]` | `aliases` |

`records` 接受三种投法：JSON 数组、`{"records":[...]}`、整段 JSON 文本（最多 2000 条）。
`facts` 格式为 `[{"attribute":"定价","truth":"每月99元","wrong":["每月199元"]}]`。

数组字段也接受换行分隔的字符串，方便表单直接提交。

**CI 接入**：`/api/score` 返回的 `score` 字段可直接做门槛，等价于 CLI 的 `--fail-under`。

### 状态码

`400` 入参问题（错误信息在 `error` 字段）· `404` 未知端点 · `413` 请求体超限 · `429` 触发限流 · `503` 并发已满，重试即可。

---

## 三、运维

```bash
docker compose logs -f            # 日志(只有方法/路径/状态,无用户正文)
docker compose up -d --build      # 更新代码后重新部署
docker compose ps                 # 健康状态，内置 HEALTHCHECK
```

容器以非 root（uid 999）运行，根文件系统只读，`no-new-privileges`，限 2 核 512MB。

**扩容**：服务无状态，直接起多个副本挂 nginx `upstream` 轮询即可。唯一的进程内状态是限流计数器，多副本下每个副本各算各的，把 `GEO_RATE_LIMIT` 按副本数分摊。

---

## 四、测试

```bash
python3 tests/run_clean.py                              # 全量 461 项，含 Web 冒烟
python3 -m unittest discover -s tests -p "test_web.py"  # 只跑 Web 层
```

`tests/test_web.py` 会起真实 HTTP 服务，校验：全部端点 200、Web 评分与直接调用引擎完全一致、异常输入一律 4xx 不 500、超限体返回 413、静态服务无路径穿越、限流生效、对标排名与评分一致、CSV 可解析、日志归因中外分桶、无 URL 时不编造 sitemap、批量审计弱页优先且单页失败不拖垮整批、错误信息能抓出价格冲突、records 三种投法结果一致。

---

## 五、CLI 与页面的关系

页面已覆盖 CLI 全部 30 个命令。CLI 在这些场景仍然更顺手：接 CI 流水线（`--fail-under` 直接当门槛）、
批量处理本地文件目录、把结果管道给别的工具。

```bash
python3 scripts/geo_cli.py <cmd> --help
```
