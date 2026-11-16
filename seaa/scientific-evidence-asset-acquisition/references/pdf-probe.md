# probe_pdf_evidence 可选 PDF 页级文本、表格与图像溯源工具

`scripts/probe_pdf_evidence.py` 是 SEAA 的可选只读 PDF 提取工具，复用 `pypdf==7.0.1` 与 `pdfplumber==0.11.10` 对本地 PDF 生成可复核的页级物证：

1. **源文件指纹与 1-based 文件页码**：记录完整 SHA-256、总页数、本次检查的 1-based 文件页范围（支持 `--pages 1,3-4`）。
2. **文本层与无文字/扫描页分界**：逐页区分 `extractable_text`、`raster_only_no_text_layer`（含位图但无文字层）与 `empty_or_vector_only_no_text`；无文字层页面绝不标记为已读正文。
3. **表格、图像框与候选 DOI 提取**：使用 `pdfplumber` 提取页面表格单元格与图像边界框，可选用 `--extract-images-dir` 导出页面内嵌位图并计算各自 SHA-256；从文本/元数据提取的 DOI 固定标记为 `candidate_dois_unverified`。

## 边界与合规约束

- **不替代 `audit_files.py` 与 `check_evidence.py`**：原有零第三方依赖脚本保持不变。
- **严禁将摘要或元数据冒充全文证据**：工具输出显式声明 `scientific_claim_verified: false`、`metadata_or_abstract_is_not_full_text: true`、`rights_status: UNKNOWN_REQUIRES_SEPARATE_CLEARANCE`。
- **只读与防覆盖**：校验读取前后源文件 SHA-256 不变；若 `--out` 或 `--extract-images-dir` 已存在则拒绝覆盖。

## 隔离安装与运行

```text
python -m pip install -r SKILL_DIR/requirements-pdf-probe.txt
python SKILL_DIR/scripts/probe_pdf_evidence.py INPUT.pdf --pages 1-4 --out pdf-probe.json
```
