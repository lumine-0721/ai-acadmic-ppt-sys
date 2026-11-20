# 阶段 D:现成方案调研 + 融合决定(一页)

日期 2026-11-20。原则:先找成熟方案,能融合就不自造。数据来自 GitHub API 与官方文档实测。

| 需求 | 候选 | 实测/依据 | 决定 |
|---|---|---|---|
| 可许可图片 | [Wikimedia Commons API](https://commons.wikimedia.org/w/api.php) | 实测 `prop=imageinfo&iiprop=extmetadata` 直接返回 LicenseShortName / Artist / Credit / AttributionRequired / LicenseUrl / 描述 / 尺寸 / mime | **采用**(HTTP API,无需新依赖) |
| 命名实体消歧 + 权威配图 | [Wikidata API](https://www.wikidata.org/w/api.php) | `wbsearchentities` 支持中文;`wbgetentities` 取 P18(该实体在 Commons 的代表图)。实测"帕特农神庙"返回 Q10288 与 Q3753726(山形墙浮雕)两个候选,P18 = The_Parthenon_in_Athens.jpg | **采用**;P18 作为命名对象的最高置信候选 |
| 学术文献检索 | [OpenAlex](https://docs.openalex.org) + [pyalex](https://github.com/J535D165/pyalex)(MIT,0.21) | 实测无 key 可用但共享每日额度(接口提示 $0.10/IP/天;免费 key 为 $1/天)。pyalex 支持 `config.api_key` / `email` 与重试 | **采用**,key 从环境变量读取;额度用尽 → 如实记录失败并降级 |
| DOI 核验 / 元数据 | Crossref REST | 已在 B 里用;`/works/{doi}` 可核验 DOI 与题名 | **保留**,专做 DOI 核验与降级检索 |
| 相关性过滤 | 上一步的 `retrieval/relevance.py` | 候选统一走 Candidate → 结构规则 + fastembed | **复用**,不重写 |
| 图片下载校验 | Pillow(python-pptx 已依赖) | 打开校验尺寸与格式 | **采用** |
| 引用管理 | citeproc 等 CSL 工具 | V0.1 只需登记来源与 DOI,不渲染参考文献格式 | **跳过** |
| 统计数据/图表数据 | Wikidata 属性、World Bank 等 | 无统一接口,且需要逐项核验数值与来源 | **V0.1 不做**:记 `missing` 并写明原因(先打通图片与文献两类,与技术方案 6.5 一致) |
| 一页图示/图标 | 自绘(F/G 阶段) | 不是检索问题 | D 记 `missing`(注明留给自绘) |

## 许可策略(写进代码并有测试)
- 接受:CC0、公有领域(PD*)、CC BY、CC BY-SA(含版本号)。
- 拒绝:NC / ND、"fair use"、未知或缺失许可、有 Restrictions 的文件。
- 需署名者(CC BY / BY-SA)登记 `author` 与 `attribution`,供 G 阶段在页脚/备注里标出。

## 诚实边界
- D 不能"看"图,`checks.relevance` 只写"元数据文本匹配得分,未做图像内容目检",不得写成已核实。
- 命名对象(如"某城市热像图")只有实体消歧结果与许可;是否确为对应城市仍标 `to_verify`,由后续阶段核实。
- OpenAlex 额度/网络失败写进 `asset_registry.json.sources`,相关需求降级为 Crossref 或 `missing`,不假装成功。
