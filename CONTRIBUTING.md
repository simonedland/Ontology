# Contributing to TGO

This page covers working *on* TGO: setting up, testing, CI, vendored assets,
air-gapped installs and releases. For how the code is organised and how to add
an ingester, extractor, analysis or exporter, see [`docs/`](docs/) (start with
`docs/architecture.md` and `docs/extending.md`); configuration and env vars
are in `docs/configuration.md`, the offline guarantee in `docs/offline.md`.

## Ground rules

- **Offline first.** Nothing under `tgo/` may reach the network at runtime.
  The only online step is `tgo collect` (`tgo/ingest/collector.py`), and it is
  explicitly allow-listed. No CDN links in HTML, no hosted AI/cloud SDKs, no
  model loaders that download on first use. `python -m tgo offline-check`
  enforces this statically; the test suite and CI enforce it at runtime.
- **Core stays small.** Core dependencies are `networkx`, `numpy`, `scipy` and
  `pillow` (the networkx layouts need numpy/scipy). Anything heavier goes in an
  optional extra and is imported lazily inside the function that uses it, with
  a fallback when it is missing. A test fails if tgo code imports a third-party
  module that is not a declared dependency.
- **Provenance over convenience.** Facts carry `status`/`conf`/`method`
  and evidence; inferred links are never merged silently. Keep that when you add
  features.

## Development setup

Python 3.12+ (3.13 is what CI and the Windows target use).

```bash
git clone https://github.com/simonedland/Ontology.git && cd Ontology
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -e . pytest              # core + tests
pip install -e ".[image,docs,ids]"   # add whichever extras you are working on
```

Extras: `collect` (Telethon), `image` (PDQ), `docs` (pypdf), `asr`
(faster-whisper), `ocr` (rapidocr + onnxruntime), `tesseract`, `ner` (spaCy),
`ids` (phonenumbers), `all`. On Windows quote the brackets: `".[all]"`.

With [uv](https://docs.astral.sh/uv/) instead:

```bash
uv sync                     # creates .venv, installs core + the dev group (pytest, ruff)
uv sync --extra image       # or --all-extras
uv run pytest
```

If you commit a `uv.lock`, CI and the wheelhouse recipe can use `uv sync --locked`
for exact, reproducible versions.

Lint (optional, configured in `pyproject.toml`): `ruff check .`. Ruff also bans
`requests`/`httpx`/`urllib.request` imports inside the package.

## Running tests

```bash
pytest -q                        # whole suite, a few seconds to ~30 s
pytest tests/test_offline.py     # the offline guarantee only
python -m tgo offline-check      # static scan for network code paths
python -m tgo demo --out out/demo  # end-to-end on synthetic data, opens nothing
```

Markers (strict, declared in `pyproject.toml`): `network` (needs network;
never run in offline CI), `slow` (real-media ASR/OCR), `extras` (needs optional
extras). Skip them with `pytest -m "not network and not slow"`.

What the suite covers: scenario assertions on the synthetic demo, identifier
checksums, media classification, the waveform codec, identity resolution,
guardrails, every exporter (GEXF, FtM, STIX, Turtle, Neo4j), the collector
against a stub Telethon (no account needed), pinned vendor hashes, and offline
end-to-end runs (in-process with sockets blocked, and in a subprocess with an
audit hook that fails on any socket, HTTP, subprocess or browser event).

New tests: put them in `tests/test_<area>.py`, keep them offline, and build
inputs with `tgo.demo.synth` or small hand-written dicts rather than real
Telegram data. **Never commit real exports, session files or personal data.**

## Continuous integration

`.github/workflows/ci.yml` runs on every push, on pull requests and on demand
(`workflow_dispatch`). Three jobs:

| Job | Runs on | What it proves |
|---|---|---|
| `test` | Windows + Ubuntu, Python 3.13 | `pip install -e .`, full `pytest`, `tgo offline-check` |
| `wheel-only` | Windows + Ubuntu | Builds the wheel, installs **only the wheel** in a fresh venv and runs `tgo demo`. Catches undeclared dependencies that a dev machine hides (this is how the numpy/scipy gap was found). |
| `no-network` | Ubuntu | Downloads a Linux wheelhouse, then installs and runs the demo inside `docker run --network none`. This is OS-level proof: it also catches network use from native code, Java or subprocesses that Python-level socket guards cannot see. |

Reproduce the `no-network` job locally (Docker running):

```bash
pip wheel . --no-deps -w dist
pip download dist/*.whl -d wheelhouse --only-binary=:all: \
    --python-version 3.13 --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64
cp dist/*.whl wheelhouse/
docker run --rm --network none -v "$PWD/wheelhouse:/wh:ro" python:3.13-slim sh -euc '
  pip install --no-index --find-links /wh tgo
  cd /tmp && python -I -m tgo demo --out /tmp/out && python -I -m tgo offline-check'
```

(On Git Bash for Windows, prefix `docker run` with `MSYS_NO_PATHCONV=1`.)

## Vendored browser assets

The HTML report inlines Cytoscape.js from `tgo/viz/vendor/`, so it works from
`file://` with no CDN. Every vendored file is pinned in
`tgo/viz/vendor/MANIFEST.json` (npm tarball URL, sha512 `integrity`, member
path, sha256 of the file, licence) and `tests/test_vendor.py` fails if the
bytes change or an unpinned `.js`/`.css` appears.

To upgrade or add a library (the only other online step, done by a maintainer):

1. `npm view cytoscape@<version> dist --json` and copy `tarball` and
   `integrity` into the manifest entry (bump `version`).
2. `python scripts/vendor_fetch.py --fetch` downloads the tarball, verifies the
   npm integrity, extracts the file and the LICENSE, and rewrites `sha256`.
3. `python scripts/vendor_fetch.py` (no flag) re-verifies offline; run
   `pytest tests/test_vendor.py` and open a demo report to check it renders.
4. Re-run `python -m tgo offline-check`: once inlined, the bundle is scanned as
   part of the generated HTML, so a new version must not contain remote
   `url(//…)`, `import(//…)` or `fetch(//…)` patterns.
5. Commit the new file, `MANIFEST.json` and `LICENSE-<name>.txt` together.

Apache-2.0 libraries (ECharts) must also ship their `NOTICE` file, and any
third-party licences bundled inside them. List these under `notices` in the
manifest entry (`{"member": "package/NOTICE", "file": "NOTICE-<name>.txt"}`):
`--fetch` extracts them, and `tests/test_vendor.py` fails for an Apache
library that has no NOTICE.

`.gitattributes` marks `tgo/viz/vendor/**` as `-text`, so Windows `autocrlf`
never rewrites the pinned bytes. Keep it that way.

## Air-gapped installation (wheelhouse)

Prepare on a **connected** machine with the same OS, CPU architecture and
Python minor version as the target:

```bash
# 1. Pin the full dependency set (with uv; or write requirements by hand)
uv lock
uv export --frozen --all-extras --no-hashes -o requirements.lock.txt

# 2. Download every dependency as a prebuilt wheel (nothing compiles offline)
pip download -r requirements.lock.txt -d wheelhouse --only-binary=:all:
#   cross-target example: add --python-version 3.13 --platform win_amd64

# 3. Add TGO itself
pip wheel . --no-deps -w wheelhouse        # or: uv build --wheel -o wheelhouse

# 4. Models and tools that are not wheels (only for the extras you use)
hf download Systran/faster-whisper-small --local-dir models/faster-whisper-small
rapidocr download_models                   # then copy the .onnx files to models/ocr
#   spaCy: put xx_ent_wiki_sm-<ver>-py3-none-any.whl (spacy-models releases) in wheelhouse/
#   exiftool / tesseract: ship their installers separately
```

Copy `wheelhouse/` and `models/` to the offline machine, then:

```bash
python -m venv .venv && .venv\Scripts\activate
pip install --no-index --find-links wheelhouse "tgo[all]"     # or a subset of extras
set TGO_WHISPER_MODEL=C:\tgo\models\faster-whisper-small     # must contain tokenizer.json
set TGO_OCR_MODELS=C:\tgo\models\ocr
python -m tgo offline-check && python -m tgo demo --out out\demo
```

`uv pip install --offline --no-index --find-links wheelhouse "tgo[all]"` works
too. `import tgo` sets `HF_HUB_OFFLINE`, `ORT_DISABLE_TELEMETRY`,
`DO_NOT_TRACK` and related variables, unless you set them yourself.
`wheelhouse/` and `models/` are git-ignored.

## Branches, commits and pull requests

- Branch from `main` (`feat/…`, `fix/…`, `docs/…`); open a PR; CI must be green.
- Keep commits focused, with an imperative subject line, and explain *why* in the body.
- Do not commit `out/`, `*.session`, Telegram exports, credentials or model
  weights (`.gitignore` covers the usual paths). Telegram API credentials go in
  `TG_API_ID` / `TG_API_HASH`, never on the command line or in files.

## Versioning and releases

TGO uses [Semantic Versioning](https://semver.org/). The version lives in one
place: `version` in `pyproject.toml`. While it is `0.x`, minor versions may
break things. From 1.0 on, the following count as **public API**, and changing
them incompatibly requires a major bump:

- the CLI (subcommands, flags, exit codes),
- the output files and their schemas (`graph.json`, the exports),
- the ontology IRIs and the class and relation names in `tgo/ontology.py`,
- the collector's on-disk layout (`collector.json`, `entities.jsonl`, `messages.jsonl`, `media/`).

To cut a release:

1. Update `version` in `pyproject.toml` and add a changelog entry (what
   changed, anything that breaks, and whether the ontology changed).
2. Make sure CI is green on `main`, including `wheel-only` and `no-network`.
3. Tag the release: `git tag -a vX.Y.Z -m "TGO X.Y.Z" && git push origin vX.Y.Z`.
4. Build the artefacts: `pip wheel . --no-deps -w dist`, or `uv build` for an
   sdist and a wheel. For offline users, also attach a wheelhouse zip for each
   target platform, built with the recipe above.
5. If the vendored JS or a pinned model changed, say so in the notes, and keep
   the manifest hashes in the release.
