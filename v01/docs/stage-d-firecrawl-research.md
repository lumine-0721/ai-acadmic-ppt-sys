# 阶段 D 增补:Firecrawl 接入调研 + 融合决定(一页)

日期 2026-11-26。原则同 `stage-d-research.md`:先找成熟方案,能融合就不自造。数据来自官方文档/GitHub 与本机实测(连接器 `custom.firecrawl` 存档)。

## 背景

D 阶段现有通道(Wikidata / Commons / OpenAlex / Crossref / Wikipedia / Searxng + 接入中的 Keenable)对普通页面够用,但遇到 JS 重页面、反爬页面、正文提取为空/过薄时只能记 `missing`。需要一条「硬页面升级通道」,只在现有通道拿不到、拿不全时启用,不做新检索主力。

| 需求 | 候选 | 实测/依据 | 决定 |
|---|---|---|---|
| JS/反爬页面正文抓取 | [Firecrawl](https://www.firecrawl.dev) hosted MCP,keyless 模式 | 官方 MCP 端点 `https://mcp.firecrawl.dev/v2/mcp`;本机 2026-11-26 实测:`tools/list` 恰为 3 个工具(`firecrawl_search` / `firecrawl_scrape` / `firecrawl_parse`);search ~2.2s、scrape ~1.8s(含建会话),均一次成功。主仓库 ~209.7k GitHub stars,MCP 仓库 ~7.9k stars | **采用**(keyless,opt-in):新增 `retrieval/firecrawl_backend.py` 实现现有 `SearchBackend` 协议 + `scrape()` 补抓;MCP JSON-RPC 客户端逻辑复用本机已验通的 `custom.firecrawl/bin/client.py`(requests 传输、会话初始化、SSE/JSON 双解码),按仓库风格改 httpx 可注入传输 |
| 大批量搜索/正文抓取 | Keenable(已接入中) | 100,000 次/月、search+fetch 通用、失败不计费 | **分工不变**:量归 Keenable;Firecrawl 免费注册号仅 1,000 credits/月(search 2/10 条、scrape 1/页),不适合管量 |
| 整站抓取 / 站点地图 / 浏览器操控 | Firecrawl `crawl` / `map` / `agent` / `interact` | keyless 不开放,需注册 API key 才有(全量 26 工具) | **本轮不做**:无现行需求,且超出 keyless 范围;将来注册免费号后再评估 |
| 文档解析(PDF/DOCX 等) | Firecrawl `firecrawl_parse` | 托管两段式(filePath → 上传指令 → uploadRef),与 seaa 现有 `pdf_processor.py` 职能重叠,且未端到端验通 | **延后登记**:有真实 DOCX/RTF 素材解析需求时再评估,不预先施工 |

## 额度与风险(写进代码并有测试)

- keyless 限额为**按 IP 每日双上限**(请求数 + 额度),超限回 429。额度是全机共享的稀缺资源:单 run 调用上限 env `PPTV01_FIRECRAWL_MAX_CALLS_PER_RUN`(默认 20),计数落 `runs/<run_id>/firecrawl_usage.json`。
- 429 → 本 run 熔断,registry 标 `firecrawl: quota_exhausted`,其余通道照常。
- 总开关 `PPTV01_FIRECRAWL=1` 才启用(默认关);`FIRECRAWL_API_KEY` 将来注册后只读 env 注入,不进代码/提交/日志。

## 诚实边界

- Firecrawl 补回的正文同样要走相关性过滤 + reranker,不开后门直达素材库;命中在 `asset_registry` 标 `source: firecrawl` 以便溯源。
- fail-open:超时/429/服务端失败一律抛现有 `BackendError(provider="firecrawl")`,由 D 的既有回落逻辑处理,绝不让 run 失败于 Firecrawl。
- keyless 实测速度来自单次样本(本 VM 出口),不承诺稳定性;X 上有「匿名被机房 IP 拒」的报告,本机未复现,但熔断与回落设计已覆盖该情况。
