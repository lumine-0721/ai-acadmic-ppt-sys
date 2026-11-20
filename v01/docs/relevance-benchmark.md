# 检索相关性过滤:基准与决定

对象:`runs/smoke-agent-v2` 的 T02 / T12 / T26 三个任务的真实检索结果(65 条 notes,Wikipedia + Crossref)。
重放副本:`benchmark/relevance/notes/*.json`;人工噪声标签:`benchmark/relevance/smoke-agent-v2.labels.json`
(28 条标为噪声)。复现:`python tools/bench_relevance.py [--sweep]`(需 fastembed;重排器另需 sentence-transformers+torch)。

## 诚实声明(局限)
- 标签由 宝特(agent)人工标注,**与系统作者同一人,非独立标注,非 API 模型**;边界样本按"相关"计,见 labels 文件。
- 样本只有 3 个任务 / 65 条;结构规则是看过这批样本后写的,**对这批数据是"样本内"结果**,不能当泛化指标。
  泛化需要在 Regression 12 上用新检索重测(T36 等未参与)。
- 阈值在这 3 个任务上选取;fastembed 在 0.20–0.30 之间结果是平台(无变化),所以对阈值不敏感。

## 打分器对比(结构规则之后的 57 条幸存者)
| 打分器 | 模型 | AUC(幸存者) | 延迟 | 加载 | 体积 |
|---|---|---|---|---|---|
| fastembed(ONNX, CPU) | paraphrase-multilingual-MiniLM-L12-v2 | 0.781 | ~11 ms/条 | ~2 s | ~0.24 GB |
| CrossEncoder(CPU) | BAAI/bge-reranker-v2-m3 | 0.768 | ~101 ms/条 | ~36 s | ~2.2 GB |

未过滤的全部 65 条上:fastembed AUC 0.76(topic+subtopic 查询)/ 0.73(仅 topic),重排器 0.746 / 0.733。
**重排器没有比小 embedding 更好,慢约 9 倍,且要 torch 与 2.2 GB 模型。** 机器(8 核/15 GB)能跑,不是资源问题,是收益问题。

## 操作点(结构规则 + 打分器,固定阈值,精确率 = 被删的里面确为噪声的比例)
| 方案 | 删除数 | 精确率 | 噪声召回 | 误删相关 |
|---|---|---|---|---|
| 仅结构规则 | 8 | 1.00 | 0.29 | 0 |
| 结构 + fastembed@0.30 | 19 | **0.947** | 0.643 | 1 (Green roof) |
| 结构 + fastembed@0.40 | 25 | 0.80 | 0.714 | 5 |
| 结构 + bge-reranker@0.005 | 35 | 0.686 | 0.857 | 11 |
| 结构 + bge-reranker@0.02 | 42 | 0.643 | 0.964 | 15 |

重排器能删得更多,但同等精确率下误删明显更多;本系统的原则是"宁可多留,不要误删好来源"。

## 决定
- **默认**:结构规则 + fastembed(MiniLM 多语言)@0.30,只删明显离题的。
- **重排器**:`PPTV01_RELEVANCE_SCORER=bge-reranker` 可选,**不作默认**(默认阈值 0.005,仅在需要高召回且能接受误删时用)。
- 没装 fastembed 时过滤器退化为仅结构规则,`research_brief.json.relevance_filter.note` 如实记录。
- 剩余噪声(例:T12 的 Utagoe coffeehouse、Ottoman coffeehouse;T26 的 NYC LPC)词面和语义都"沾边",
  阈值法删不掉,**需要 B 阶段(LLM)在选用 notes 时再判断**;被过滤的与被保留的都写入 `research_brief.json`。
