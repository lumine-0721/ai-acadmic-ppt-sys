# 辅助组件调研：Bivia 判定席 + Keenable 检索（opt-in）

## 1. 背景

- **Bivia 判断器**：宝特第 4 轮 HANDOFF 引入「可选：Bivia 判断器」五条，作为 H/E/C 阶段的把关后端做 boolean pass/veto 判断，默认关闭、失败不挡流水线。
- **Keenable 检索**：后续轮次决定 Keenable 检索只做 opt-in（用户主动开启），失败同样走回落路线，不阻塞流水线。

两个组件的设计原则一致：默认关闭；失败均采用 fail-open 策略，记录警告并继续执行。

## 2. 候选方案对比

| 方案 | 描述 | 缺点 |
|------|------|------|
| **(a) Bivia 官方 Python SDK** | PyPI 包名 `bivia`，Articul8 官方 | 新增依赖；其重试/配置体系与仓库现有 `httpx` 模式重复 |
| **(b) Keenable 官方 SDK + 托管 MCP server** | 官方提供 Python SDK 和 MCP server | MCP 适合交互式 agent，不适合嵌进确定性流水线库代码；SDK 同样是新依赖 |
| **(c) 直接 httpx REST** | 用仓库已有 `httpx` 直接调官方 REST 接口 | 无新依赖；复用仓库成熟模式 |

仓库已有两套成熟模式：
- `llm.py`（配置只读 env、transport 注入）
- `retrieval/backends.py`（`BackendError`、退避重试）

## 3. 融合决定

**选方案 (c)**：零新依赖，最大化复用现有代码模式。

- **Bivia**：按 `llm.py` 风格新建 `src/pptv01/bivia_judge.py`
- **Keenable**：按 `backends.py` 风格加 `KeenableBackend`，在 `provider.py` 的 `research_from_env` 里追加 env 门

**许可证**：无第三方代码复制；两个 API 均为官方公开 REST 接口；无新增 NOTICE 条目。

## 4. 接入点与语义

### Bivia（图片 boolean pass/veto）

| 项目 | 值 |
|------|-----|
| 双 env 门 | `PPTV01_BIVIA_JUDGE=1` 或 `on` + `A8_API_KEY`，两者均须设置才生效 |
| 阈值 | 默认 0.90，可通过 `PPTV01_BIVIA_THRESHOLD` 配置 |
| 返回四态 | `pass` / `veto` / `uncertain` / `not_run` |
| 图片专用结果 | `not_vetoed`（图片版「未否决」：不构成证明，仍须叠加元数据/实体规则） |

### Keenable（stage-B 检索后端）

| 项目 | 值 |
|------|-----|
| 双 env 门 | `PPTV01_KEENABLE=1` + `KEENABLE_API_KEY`，两者均须设置才生效 |
| 失败处理 | 抛出 `BackendError`，由 `LiveResearch` 现有机制记录并回落至其他后端 |

## 5. 风险与限制

### Bivia

- **图片判定范围**：只作 `veto`（不符合条件的图片不纳入），不作为 `proof`（不能仅凭 Bivia 判断证明图片合格）。
- **已发现误判案例（B15 基准）**：
  - 纽约照片被判定为北京：`fast p=0.97` / `deep p=0.98`
  - 天津公园照：`p=0.99` 误判
- **图片格式**：必须提供 base64 编码；服务器直接抓外链返回 422 错误。
- **429 处理**：调用超限时按指数退避重试。
- **计费规则**：
  - 每次调用至少按 1000 token 计费
  - 赠送额度 90 天过期
- **密钥管理**：仅读环境变量，不进代码/日志/提交。

### Keenable

- **免费额度**：每月 10 万次免费请求；超额后按 $4/千次计费。
- **账户安全**：账户无绑卡无余额，超额调用直接失败，**不会产生扣款**。
- **密钥管理**：仅读环境变量，不进代码/日志/提交。
