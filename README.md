# GEO-HOU｜中国优先的生成式引擎优化（GEO + SEO）开源工具

> **让你的内容被豆包、通义千问、DeepSeek、文心一言、腾讯元宝，以及 ChatGPT、Perplexity、Claude、Gemini 抓取、识别并引用。**
>
> China‑first Generative Engine Optimization (GEO) + SEO toolkit. Make your content get cited by Doubao, Qwen, DeepSeek, ERNIE, Tencent Yuanbao, ChatGPT, Perplexity, Claude and Gemini.

MIT License

---

## 项目简介

GEO-HOU 是面向中国市场优先的 **GEO（生成式引擎优化）+ SEO** 内容优化套件，覆盖 **诊断 → 产出 → 度量** 全链路。

**三种用法，同一套引擎：**

| 用法 | 适合谁 | 入口 |
|---|---|---|
| 🌐 **Web 控制台** | 团队协作、不想记命令的人 | `docker compose up -d` → 浏览器打开，28 个功能点点就能用 |
| ⌨️ **命令行 CLI** | 接 CI 流水线、批量处理本地文件 | `python3 scripts/geo_cli.py --help`，30 个命令 |
| 🤖 **AI Agent 技能包** | Claude Code / Codex 等 Agent 用户 | `python3 build.py` 构建后接入，自然语言驱动 |

**零第三方依赖，仅 Python 标准库。** 不联网、不落盘、可完全离线运行。

---

## 为什么做这套工具

市面上绝大多数开源 GEO 工具全部聚焦海外 ChatGPT、Perplexity，**几乎没有工具深度适配国内大模型的收录、抓取逻辑**。而国内豆包月活 3.45 亿、通义千问月活 1.66 亿，稳居头部，内容优化必须贴合本土现实。

本工具融合三层能力：

1. **学术理论底座**：基于 KDD 2024《GEO》、ICLR 2026《AutoGEO》两篇顶会论文；内置 9 套内容改写策略、排名分流逻辑、GEU 合规护栏。
2. **工程审计框架**：萃取 4 个主流开源审计项目能力，沉淀 **6 维 22 项内容可被引用度评分卡**。
3. **本土抓取逻辑（核心差异化）**：国内大模型无公开爬虫，依赖博查、搜狗、百度索引与自有生态。

> 💡 **核心认知：国内 GEO 是「争取收录 + 抢占平台」，海外 GEO 是「配置 robots.txt 做权限管控」，两套策略切勿混用。**

---

## 🌐 Web 控制台

把整套能力包成 HTTP 服务 + 网页控制台，团队里不熟命令行的人也能直接用。

### 部署

```bash
git clone <本仓库> geo-hou && cd geo-hou
docker compose up -d --build
curl http://127.0.0.1:8000/healthz     # -> ok
```

镜像基于 `python:3.12-slim`，**不需要 `pip install` 任何包**，构建秒级。默认只绑 `127.0.0.1:8000`，由 nginx 做 TLS 对外暴露。完整部署文档（nginx 配置、systemd 方式、环境变量、扩容）见 **[web/README.md](web/README.md)**。

不用 Docker 也行，有 Python 3.9+ 即可：

```bash
python3 web/server.py --host 127.0.0.1 --port 8000
```

### 页面能做什么

导航按 **诊断 → 产出 → 度量 → 生成** 四组划分，共 **28 个功能**，覆盖 CLI 全部 30 个命令。

**诊断（9）**
评分（6 维 22 项）· 内容工程（11 要素）· 根因诊断（7 类）· Agent 就绪审计 · Core Web Vitals · Token 预算 · 批量审计 · 关键词蚕食 · 内链审计

**产出（4）**
作战手册（旗舰）· 改写指令编译器 · 内容简报 · 信源策略

**度量（10）**
竞品对标 · Prompt 集 · 平台推荐 · 意图分类 · 归因 · 采集工具包 · 综合监测 · 声量份额 · 竞品夺走 · 错误信息

**生成（5）**
robots.txt / llms.txt / JSON‑LD · AI 发现文件组 · 多语言 hreflang · 百度主动推送 · 收录自查

评分、作战手册、对标、改写指令等支持导出 **HTML / Markdown / CSV / SARIF**，作战手册的 HTML 可直接打印分享。

### HTTP API

**42 个端点**，全部 `POST`，UTF‑8 JSON 进出。`GET /api` 列出全部。

```bash
# 打分（score 字段可直接当 CI 门槛，等价 CLI 的 --fail-under）
curl -sX POST http://127.0.0.1:8000/api/score \
  -H 'Content-Type: application/json' \
  -d '{"html":"<html>...</html>"}'

# 一键出可打印的 HTML 作战手册
curl -sX POST http://127.0.0.1:8000/api/playbook.html \
  -H 'Content-Type: application/json' \
  -d '{"html":"...","brand":"你的品牌","category":"品类","engines":["豆包","元宝"]}' \
  -o playbook.html
```

### 安全边界

| 项 | 说明 |
|---|---|
| **不抓取 URL** | 输入只有请求体里的内联 HTML。评分引擎本身从不发网络请求，因此没有 SSRF 面 |
| **不落盘** | 无状态，不写文件，不记录用户提交的正文（日志只记方法/路径/状态码） |
| **容器加固** | 非 root（uid 999）运行、根文件系统只读、`no-new-privileges` |
| **限流与体积上限** | 每 IP 每分钟 60 次、单请求体 2MB，均可配 |
| ⚠️ **无登录** | 服务本身**不带身份认证**。挂公网前请用 nginx Basic Auth 或 IP 白名单，详见 [web/README.md](web/README.md) |

---

## ⌨️ 命令行

仅需 Python 3，零额外依赖。

```bash
# 网页 GEO 可引用度打分（6 维 22 项）
python3 scripts/geo_cli.py score --input page.html

# ★旗舰：一键出完整 GEO 作战手册
python3 scripts/geo_cli.py playbook --input page.html --brand 你的品牌 --category 品类 \
  --engine 豆包 --engine 元宝 --query "品类哪个好" --competitor 竞品 --html report.html

# 生成面向 AI 爬虫的 robots 配置（仅曝光，禁止训练采集）
python3 scripts/geo_cli.py robots --strategy expose-only --sitemap https://example.com/sitemap.xml

# FAQ 结构化数据，实测可提升 2.7 倍引用概率
python3 scripts/geo_cli.py schema --type faqpage --qa "GEO是什么::优化内容被AI引擎引用的实践"

# 全部 30 个命令
python3 scripts/geo_cli.py --help
```

> **正门是 `playbook` 一条命令**，不是让你记 30 个散命令。它输出八层瓶颈定位、诊断报告、信源策略、逐段改写指引、监测方案和按 ROI 排序的行动清单。

---

## 🤖 AI Agent 技能包

一套标准源文件，构建后输出多套独立适配包：

```bash
python3 build.py
```

适配 **Claude Code、Codex、OpenClaw、Hermes**，产物在 `adapters/` 下，可独立分发。接入后用自然语言驱动，例如「帮我给这个页面做 GEO 优化」。

---

## 📊 运行官方示例

```bash
cd cases && python3 run_cases.py
```

内置 3 套真实场景，附优化前后评分对照与完整产出：

| 场景 | 优化前 | 优化后 | 提升 |
|---|---|---|---|
| 出海 SaaS 落地页 | 15（危急） | 50（待优化） | +35 |
| 国内公众号深度文 | 43（待优化） | 70（良） | +27 |
| 国内产品页 | 41（待优化） | 65（待优化） | +24 |

---

## 🧩 核心能力详解

- 📊 **大模型画像**：9 大模型 DAU 与引用行为分类库，判断内容适配哪些 AI
- 🕵 **抓取逻辑库**：覆盖国内 3 条收录路径、海外真实爬虫 UA 识别
- 📢 **发布渠道库**：一稿多态分发，国内深耕生态平台、海外侧重 Reddit 等渠道
- 🏗 **网页架构库**：语义化 HTML、Schema 结构化数据、SSR 边界、国内 ICP 合规提示
- ✍ **GEO 优化方法库**：引用强化最高 +41% 效果、排名分流策略、合规红线约束
- ⚖ **内容评分**：6 维 22 项量化打分，结果可接入 CI 自动化流程
- 📄 **配置生成**：`robots.txt` / `llms.txt` / `JSON‑LD` 等 9 类 AI 发现文件

### 版本迭代亮点

| 版本 | 核心内容 |
|---|---|
| **v1.11** | 全项目功能审查修复版：真实资产全量冒烟 + 多专家实跑，确认并修复 54 条真问题（robots 解析误判、新鲜度判分方向反转、小数点断句、GBK 页乱码、JSON‑LD 转义 XSS 等）。测试 279 → 437 |
| **v1.10** | 真实采集闭环 dogfood：修复「AI 答『没找到你』却被判成被提及」的覆盖率误报 |
| **v1.9** | 可视化作战手册（静态可打印 HTML）+ 竞品逐维度对标 |
| **v1.8** | `playbook` 一键出完整作战手册，八层瓶颈定位 |
| **v1.7** | 词根问句矩阵、引擎信源偏好诊断、P0/P1/P2 分级投放方案 |
| **v1.6** | 落地 WaytoAGI《GEO 内容工程》方法论，11 项加权要素评估 |
| **v1.5** | 引用质量与风险管控：情感区分、事实校验、Agent 反模式检测、SEO 深水区补齐 |
| **v1.4** | 搜索意图识别、百度主动推送、国内爬虫 UA 适配、hreflang 多语言 |
| **v1.2‑1.3** | 平台权重推荐引擎（新榜 1683.6 万条数据实证）+ 海外 11 引擎全覆盖 |
| **v1.1** | 改写指令编译器 + 效果度量离线工具，补齐产出与度量环节 |

> ⚠️ **定位说明**：本工具以 GEO 为主，附带页内可引用优化与国内收录审计，**不等同 Ahrefs、Semrush 完整 SEO 套件**。关键词搜索量、外部反链不内置抓取，缺失数据一律标记 `unknown`。

---

## 📁 项目结构

```
source/                 # 唯一标准源文件（Agent 技能包的源）
├─ SKILL.md             # 主技能定义，中英双触发
├─ manifest.json        # 元数据
├─ knowledge/           # 八大知识库
├─ methodology/         # GEO 方法论、评分卡规则
├─ workflow/stages.md   # 十阶段工作流 + 人工校验节点
└─ templates/           # 交付模板
scripts/                # Python CLI 工具集（30 个命令）
├─ geo_cli.py           # 统一入口
└─ lib/                 # 评分、改写、度量等核心库
web/                    # Web 服务
├─ server.py            # HTTP 服务（42 端点，标准库实现）
├─ static/index.html    # 网页控制台（28 功能）
└─ README.md            # 部署与使用文档
adapters/               # build.py 生成的多 Agent 适配包
cases/                  # 3 套完整示例案例
tests/                  # 单元测试（461 项）
Dockerfile              # Web 服务容器化
docker-compose.yml
build.py / validate.py  # 构建与完整性自检
```

### 开发

```bash
python3 tests/run_clean.py    # 全量测试 461 项（含 Web 冒烟）
python3 validate.py           # 完整性自检 127 项
python3 build.py              # 构建四套 Agent 适配包
```

---

## 🛡 诚实边界（重要）

1. **绝不编造引用、统计、素材**。素材不足提示人工补充，GEU 护栏防止内容污染；永久禁用关键词堆砌（实测为负效果）。
2. `llms.txt` 是 B2A 底层基础设施，**不是排名提升手段**，营销网站收益极低。
3. **度量层不联网、不替你去问 AI**。监测需要你去目标引擎实际采样，把回答原文喂回来。工具绝不编造 AI 回答。
4. 所有效果数值是参考校准参数，大模型算法持续迭代，请定期自行校验。
5. 保留两个人工审核节点（专家洞察槽位、质量门 BLOCK 裁决），**不能完全跳过人工判断**。工具无法拯救没有真实观点的水文内容。

---

## 📚 方法论来源

- GEO（KDD 2024）：<https://arxiv.org/abs/2311.09735>
- AutoGEO（ICLR 2026）：<https://arxiv.org/abs/2510.11438>
- 内容工程：WaytoAGI《GEO 内容工程》公开课
- 工程参考：多个主流 GEO / SEO 开源审计项目
- 国内数据：QuestMobile 2026 Q1、量子位智库、新榜实证报告

---

## 📜 开源许可

**MIT License**。本项目在 HeiGeAI 的开源项目基础上二次开发，版权声明见 [LICENSE](LICENSE)。
