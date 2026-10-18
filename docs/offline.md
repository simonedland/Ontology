# Offline operation

**TGO needs no internet connection to operate.** The only steps that touch the network are the ones you start
on purpose:

| Online step | When | Command |
|---|---|---|
| Live collection from Telegram | whenever you collect | `python -m tgo collect …` (optional `[collect]` extra) |
| Installing packages and downloading local models | once, at setup | `pip install …`, `hf download …`, `rapidocr download_models` |

Everything else runs fully offline: `demo`, `run`, `ontology`, `offline-check`, every extractor, all analysis,
every export, and the HTML viewer.

## What guarantees it

1. **No network code in the package.** `python -m tgo offline-check` (`tgo/offline.py`) statically scans
   `tgo/` and fails on any of the following:
   - remote `<script src>`, `<link href>`, `<img src>`, CSS `@import`/`url()` or `fetch()`;
   - HTTP-client imports (`requests`, `httpx`, `urllib3`, `aiohttp`, `urllib.request`);
   - model downloads (`hf_hub_download`, `snapshot_download`, `spacy download`, `nltk.download`);
   - `WhisperModel`/`from_pretrained` without `local_files_only=True`, or `RapidOCR()` without model paths;
   - **cloud AI/API SDKs** (google, openai, anthropic, azure, boto3, elevenlabs, deepgram, …);
   - Telegram clients outside `ingest/collector.py`.

   Namespace IRIs and citation URLs in docstrings are identifiers, not fetches, so the scan ignores them.
2. **Kill switches on import.** `import tgo` runs `tgo._env.harden_env()`. It sets `HF_HUB_OFFLINE`,
   `TRANSFORMERS_OFFLINE`, `HF_DATASETS_OFFLINE`, `HF_HUB_DISABLE_TELEMETRY`, `HF_HUB_DISABLE_UPDATE_CHECK`,
   `HF_HUB_DISABLE_IMPLICIT_TOKEN`, `DO_NOT_TRACK`, `ORT_DISABLE_TELEMETRY` (onnxruntime telemetry is on by
   default) and `GRADIO_ANALYTICS_ENABLED=False`. It uses `setdefault`, so a value you set explicitly wins.
3. **Models only from local paths.** Optional models load only when a `TGO_*` variable points to a complete
   local directory. Otherwise TGO falls back to a non-ML method or a sidecar file, and records which tool
   actually ran in the run's `ModelAgent`.
4. **A self-contained viewer.** `report.html` inlines Cytoscape.js (pinned by sha256 in
   `tgo/viz/vendor/MANIFEST.json`) and embeds the data. It uses system fonts and no map tiles, and it works
   from `file://`.
5. **Tests that prove it** (`tests/test_offline.py`):
   - the whole pipeline runs under `tgo.offline.block_network()`, which makes every socket connect and DNS
     lookup raise;
   - a fresh subprocess runs it again with an audit hook that fails on any socket, HTTP, subprocess or browser
     event, with all proxies pointed at a dead port;
   - the CLI (`demo`, `ontology`, `offline-check`) runs the same way, and telethon must not be imported;
   - every third-party module that tgo imports directly must be a declared dependency, so a fresh install
     cannot fail.

   CI also runs the demo inside `docker run --network none` (OS-level isolation); see `CONTRIBUTING.md`.

## Local AI: allowed and encouraged, as long as it is local

Heavy local models are fine. Hosted AI APIs are not; the offline check rejects their SDKs. Supported local
models:

| Capability | Install (online, once) | Point TGO at it | Fallback without it |
|---|---|---|---|
| Speech-to-text (faster-whisper) | `pip install ".[asr]"` then `hf download Systran/faster-whisper-large-v3 --local-dir models/whisper` (or `-small`, `large-v3-turbo`) | `TGO_WHISPER_MODEL=models/whisper` (folder must contain `tokenizer.json`) | `<file>.transcript.json` sidecar |
| OCR (RapidOCR / PP-OCR) | `pip install ".[ocr]"` then `rapidocr download_models` (or copy det/cls/rec `.onnx` files) | `TGO_OCR_MODELS=<dir with *det*.onnx, *cls*.onnx, *rec*.onnx>` | Tesseract if installed, then `<file>.ocr.txt` |
| OCR (Tesseract) | `pip install ".[tesseract]"` plus the Tesseract binary | binary on `PATH` | `<file>.ocr.txt` |
| NER (spaCy) | `pip install ".[ner]"` plus the `xx_ent_wiki_sm` wheel from the spacy-models releases | — (an installed package) | built-in gazetteer |
| PDQ perceptual hash | `pip install ".[image]"` | — | DCT pHash (numpy) |
| PDF text | `pip install ".[docs]"` | — | built-in PDF parser |
| Phone validation | `pip install ".[ids]"` | — | built-in country-code table |

Adding another local model (an LLM through llama.cpp, GLiNER, a vision model, …) is covered in
[extending.md → Add an extractor](extending.md#add-an-extractor). Follow three rules:

- load from a local path;
- pass `local_files_only=True` (or the library's equivalent);
- fall back cleanly when the model is missing.

A local HTTP server on `localhost` (e.g. ollama) still counts as offline, but `block_network()` blocks it in
tests. Prefer in-process libraries, or mark the step as optional.

## Air-gapped installation

On a connected machine with the **same OS and Python version** as the target:

```bash
pip download . -d wheelhouse --only-binary=:all:          # core deps
pip download ".[all]" -d wheelhouse --only-binary=:all:   # or every optional extra
pip wheel . --no-deps -w wheelhouse                       # tgo itself
hf download Systran/faster-whisper-small --local-dir models/faster-whisper-small   # optional models
```

Copy `wheelhouse/` (and `models/`) to the target, then:

```bash
pip install --no-index --find-links wheelhouse tgo        # or "tgo[all]"
python -m tgo offline-check && python -m tgo demo
```

`CONTRIBUTING.md` has the `uv`-locked variant used by CI.

## Checking it yourself

```bash
python -m tgo offline-check            # static scan, exit code 1 on findings
pytest tests/test_offline.py -q        # runtime proof
python -m tgo.offline path/to/report.html other.html   # scan any generated HTML
```

To guard your own code paths in a script:

```python
from tgo.offline import block_network
with block_network():
    ...  # any socket connect or DNS lookup raises tgo.offline.NetworkBlocked
```
