# Proxy, Mirror, and Vision Model Cache Configuration

`pptv01` uses standard environment variables for all HTTP requests and model downloads so networks behind corporate/academic/local proxies can run Stages B and D without code modifications or stored credentials.

## 1. HTTP Clients (`httpx` `trust_env=True`)

All HTTP calls in Stages B and D—Wikipedia, Crossref, SearXNG, Wikidata, Wikimedia Commons, and OpenAlex—share `pptv01.retrieval.backends.make_http()`, which configures `httpx.Client(..., trust_env=True)`.

Set standard proxy variables in your shell before running `pptv01`:

```powershell
# PowerShell example
$env:HTTP_PROXY  = "http://127.0.0.1:7890"
$env:HTTPS_PROXY = "http://127.0.0.1:7890"
$env:NO_PROXY    = "localhost,127.0.0.1"
```

```bash
# Bash / zsh example
export HTTP_PROXY="http://127.0.0.1:7890"
export HTTPS_PROXY="http://127.0.0.1:7890"
export NO_PROXY="localhost,127.0.0.1"
```

Both lowercase (`http_proxy`, `https_proxy`, `no_proxy`) and uppercase forms are read by `httpx`. SOCKS proxies (`socks5://...`) require `httpx[socks]` / `socksio` if used directly.

## 2. `fastembed` and `huggingface_hub` Model Downloads

Optional local embedding models used by `pptv01`:

- **Stage B / D text relevance (`PPTV01_RELEVANCE=auto`)**: `Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q` via `fastembed` (`pptv01.retrieval.relevance`).
- **Stage D pixel & signage rules (`PPTV01_PIXEL_RULES=auto`)**: `Qdrant/clip-ViT-B-32-vision` + `Qdrant/clip-ViT-B-32-text` via `fastembed` (`pptv01.d_retrieve.pixel_rules`), paired with bundled local `rapidocr-onnxruntime` ONNX models (no network download needed for RapidOCR).

Both `fastembed` and `huggingface_hub` download via `requests`, which automatically inherits `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` from the environment.

To bound slow networks or stalled proxies, model initialization runs in a daemon thread with wall-clock timeouts:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PPTV01_RELEVANCE_LOAD_TIMEOUT_S` | `20` | Max seconds to load/download the Stage B/D text embedder before degrading to structural-only mode. |
| `PPTV01_PIXEL_LOAD_TIMEOUT_S` | `45` (or `PPTV01_RELEVANCE_LOAD_TIMEOUT_S`) | Max seconds to load/download CLIP models before logging `pixel_rules: degraded (...)`. |
| `HF_ENDPOINT` | unset (`https://huggingface.co`) | Hugging Face mirror base URL, e.g. `https://hf-mirror.com`. Respected by both `pptv01.retrieval.relevance` and `pptv01.d_retrieve.pixel_rules`. |

If `HF_ENDPOINT` is set, both modules forward it while loading models and strip any query secrets or embedded credentials via `sanitize_url()` before printing diagnostics.

## 3. Model Cache Directories

Set an explicit persistent cache directory to avoid re-downloading ONNX weights when `%TEMP%` / `/tmp` is cleared:

| Variable | Purpose |
| --- | --- |
| `FASTEMBED_CACHE_PATH` | Primary directory where `fastembed` stores `models--Qdrant--*` ONNX weights. |
| `PPTV01_FASTEMBED_CACHE_DIR` | Fallback override read when `FASTEMBED_CACHE_PATH` is unset. |
| `HF_HOME` / `HF_HUB_CACHE` | Standard `huggingface_hub` cache location (also honored by underlying hub transfers). |

When neither `FASTEMBED_CACHE_PATH` nor `PPTV01_FASTEMBED_CACHE_DIR` is set, `fastembed` uses `<system tempdir>/fastembed_cache`. Automated tests marked `@pytest.mark.vision_model` probe the resolved cache via `is_clip_cached()` and skip offline when CLIP weights are absent.

## 4. Diagnostics (`pptv01 doctor`)

`pptv01 doctor` runs a non-blocking `proxy_env` probe that reports detected `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, and `HF_ENDPOINT` settings. Usernames and passwords in proxy or mirror URLs are stripped so only `scheme://host:port` is displayed or written to reports.
