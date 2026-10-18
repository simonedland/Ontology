# Configuration

TGO is configured on three levels. Use the lowest one that does the job:

| Level | What it controls | How |
|---|---|---|
| **Config file** | every threshold, weight and cap in the analysis, extraction and viewer | `--config tgo.toml` / `Config` in Python |
| **CLI flags** | the common one-off overrides | `--time-window`, `--auto-accept`, `--no-html`, … |
| **Environment variables** | settings read at import time: namespace, local model paths, credentials | `TGO_DOMAIN=…` |

The defaults reproduce the shipped behaviour exactly. You only write what you want to change.

## Quick start

```bash
python -m tgo config > tgo.json                 # dump the effective defaults
# edit tgo.json, or write a small TOML with only your changes (below)
python -m tgo run --input case --out out/case --config tgo.toml
python -m tgo config --config tgo.toml          # print the effective config after your overrides
```

A minimal `tgo.toml` that only changes what differs from the defaults:

```toml
[coordination]
time_window = 300          # 5-minute window for slower-moving campaigns
max_p = 1e-4               # stricter null-model cut-off

[identity.weights]
shared_avatar_hash = 0.2   # partial override: the other weights keep their defaults

[extract]
gazetteer_path = "config/places_no.json"   # your own place/org list for offline NER

[viewer]
title = "Case 42: harbour network"
max_nodes = 3000
```

From Python:

```python
from tgo.config import Config
from tgo.pipeline import run

cfg = Config.load("tgo.toml")          # or Config() / Config.from_dict({"coordination": {"time_window": 300}})
cfg.identity.auto_accept = None        # objects are plain dataclasses; edit freely
summary = run("data/case42", "out/case42", config=cfg)
summary["config"]                      # the effective config is saved in summary.json for reproducibility
```

The rules:

- **Unknown keys raise an error.** A typo never silently falls back to a default.
- **Weight tables merge.** Giving one entry of `identity.weights` or `communities.edge_weights` changes only that
  entry. To drop an entry, set its weight to `0`.
- **Precedence:** explicit CLI flag > config file > default. `--time-window` and `--auto-accept` override
  `coordination.time_window` and `identity.auto_accept`.
- **Every run records its config.** `summary.json["config"]` holds it, and each analysis step also stores its
  parameters on its `run:` node in the graph.

## Common adaptations

| You want to… | Change |
|---|---|
| detect slower or faster coordination | `coordination.time_window` (seconds); `coordination.sync_seconds` for "to the second" |
| see more or fewer coordination clusters | `coordination.max_p` (higher = more permissive; `null` = classic CooRTweet without a significance test), `coordination.min_participation`, `coordination.min_cluster_size` |
| find bigger or smaller communities | `communities.resolution` (higher = smaller), `communities.min_size`, `communities.min_stability` |
| make a relation count more in community detection | `communities.edge_weights.<TYPE>` |
| trust an identity signal more or less | `identity.weights.<feature>`; `identity.min_score` for the candidate cut-off |
| let high-scoring candidates merge without an analyst | `identity.auto_accept` (logged as a policy decision; off by default on purpose) |
| treat another identifier kind as strong evidence | `identity.strong_identifiers` (class names from the [vocabulary](vocabulary.md)) |
| use your own place and organisation names for NER | `extract.gazetteer_path` → JSON `{"surface form": "LOC" \| "ORG" \| "PER"}` |
| loosen or tighten image near-duplicate matching | `extract.phash_threshold` (Hamming bits; default 10 for pHash, 31 for PDQ) |
| change the namespace IRIs in Turtle/RDF exports | env `TGO_DOMAIN=intel.example.no` |
| enable real speech-to-text or OCR | env `TGO_WHISPER_MODEL`, `TGO_OCR_MODELS`; see [offline.md](offline.md) |
| skip the HTML viewer for huge cases | `viewer.html = false` or `--no-html` |

## CLI reference

| Command | Flags |
|---|---|
| `python -m tgo demo` | `--out DIR` (default `out/demo`), `--config FILE`, `--auto-accept X`, `--open` |
| `python -m tgo run` | `--input DIR` (required, searched recursively), `--out DIR` (required), `--config FILE`, `--auto-accept X`, `--time-window S`, `--no-html`, `--open` |
| `python -m tgo serve` | `--input DIR`, `--out DIR` (required), `--interval S` (5), `--host` (127.0.0.1), `--port` (8765), `--token`, `--engine memory\|duckdb`, `--max-nodes N`, `--no-snapshots`, `--config`, `--plugin`, `--open`: live dashboard, see [live.md](live.md) |
| `python -m tgo dashboard` | `--out DIR` (required: an output folder), `--path FILE`, `--max-nodes N` (500), `--open`. `run`/`demo` also take `--dashboard` |
| `python -m tgo ontop` | `--out DIR`: writes the Ontop mapping for SPARQL over `tgo.duckdb` (see [scaling.md](scaling.md#deployment-tiers-where-the-data-lives)) |
| `python -m tgo config` | `--config FILE`: prints the effective config as JSON |
| `python -m tgo ontology` | `--out FILE` (default `ontology.ttl`) |
| `python -m tgo collect` | `--target @x` (repeatable, required), `--out DIR` (required), `--api-id`, `--api-hash`, `--session`, `--limit N` (200), `--max-file-mb N` (20), `--no-participants`. **Online.** |
| `python -m tgo offline-check` | none: exits 1 if any network code path is found |

Run `python -m tgo <command> --help` for the authoritative list.

## Reference

The tables below are generated from `tgo/config.py` by `scripts/gen_docs.py`, and `tests/test_docs.py` fails
if they drift. To add a setting, add a field with an inline `# comment` to the right dataclass, read it where
it's used, and rerun `python scripts/gen_docs.py`.

<!-- BEGIN GENERATED: config reference -->
### `coordination`

CooRTweet-style coordination detection (`tgo.analysis.coordination`).

| Key | Default | Meaning |
|---|---|---|
| `coordination.time_window` | `60` | seconds within which two shares of the same object count as co-sharing |
| `coordination.min_participation` | `2` | distinct shared objects a pair needs before it is considered |
| `coordination.max_p` | `0.001` | Poisson null-model significance cut-off; None = plain CooRTweet (no test) |
| `coordination.sync_seconds` | `5` | min delta at/below which IDENTICAL_POST pairs also get SYNC_POST |
| `coordination.min_text_len` | `40` | normalised characters before text is used as a shared object |
| `coordination.simhash_max_dist` | `3` | Hamming distance (64-bit SimHash) for NEAR_DUP_POST |
| `coordination.min_cluster_size` | `3` | accounts needed for a CoordinationCluster node |
| `coordination.same_day_min` | `3` | chats created on one day needed for SAME_CREATION_DATE edges |
| `coordination.same_day_max` | `50` | ...and the upper bound (avoids cliques on bulk imports) |
| `coordination.max_object_accounts` | `1000` | objects shared by more accounts are dropped before pairing (fan-out cap, both engines) |

### `communities`

Seeded multi-run Louvain with stability (`tgo.analysis.communities`).

| Key | Default | Meaning |
|---|---|---|
| `communities.seeds` | `10` | Louvain runs; stability = mean best-match Jaccard vs run 0 |
| `communities.resolution` | `1.0` | Louvain resolution (higher = smaller communities) |
| `communities.min_size` | `3` | smallest community promoted to a CommunityCluster node |
| `communities.min_stability` | `0.6` | clusters below this are rejected as unstable |
| `communities.deterministic` | `true` | duckdb engine: single-threaded NetworKit PLM so reruns give identical clusters |
| `communities.edge_weights` | see table below |  |

`communities.edge_weights` defaults (a partial override merges into these):

| Entry | Value |
|---|---|
| `FORWARDS_FROM` | 1.0 |
| `IDENTICAL_POST` | 1.0 |
| `SYNC_POST` | 1.0 |
| `CO_FORWARD` | 0.8 |
| `NEAR_DUP_POST` | 0.6 |
| `CO_URL` | 0.5 |
| `CO_FINGERPRINT` | 0.5 |
| `MEMBER_OF_COMMUNITY` | 0.7 |
| `LINKED_DISCUSSION` | 0.7 |
| `POSTED_IN` | 0.3 |
| `GAVE_GIFT_TO` | 0.4 |
| `REPOSTED` | 0.4 |
| `FEATURES` | 0.6 |
| `INVITED_BY` | 0.3 |

### `identity`

Candidate scoring and judgements (`tgo.analysis.identity`).

| Key | Default | Meaning |
|---|---|---|
| `identity.min_score` | `0.3` | noisy-OR score needed to write a CANDIDATE_SAME_PERSON edge |
| `identity.auto_accept` | `null` | if set, candidates >= this are auto-positive (logged as a policy) |
| `identity.weights` | see table below |  |
| `identity.strong_identifiers` | `["CryptoAddress", "IBAN", "PhoneNumber", "Email"]` |  |
| `identity.min_bio_len` | `15` | shorter bios are too generic to count as shared evidence |
| `identity.max_evidence_accounts` | `50` | evidence keys shared by more accounts are ignored (fan-out cap, both engines) |

`identity.weights` defaults (a partial override merges into these):

| Entry | Value |
|---|---|
| `same_phone` | 0.9 |
| `same_personal_channel` | 0.7 |
| `shared_device_serial` | 0.6 |
| `shared_identifier` | 0.45 |
| `username_history` | 0.4 |
| `same_bio_text` | 0.35 |
| `shared_avatar_hash` | 0.35 |

### `extract`

Extractors (`tgo.extractors`).

| Key | Default | Meaning |
|---|---|---|
| `extract.gazetteer_path` | `null` | JSON {"surface form": "LOC\|ORG\|PER"} replacing the demo gazetteer |
| `extract.phash_threshold` | `null` | override near-duplicate Hamming threshold (default 10 pHash / 31 PDQ) |

### `viewer`

report.html and layouts (`tgo.viz.report`, `tgo.pipeline.layout`).

| Key | Default | Meaning |
|---|---|---|
| `viewer.html` | `true` | write report.html at all |
| `viewer.title` | `"TGO investigation view"` |  |
| `viewer.max_nodes` | `6000` | nodes embedded in the viewer (actors + evidence first); rest stays in graph.json |
| `viewer.layout_max_full` | `2500` | above this node count only the actor skeleton is force-laid-out |
| `viewer.layout_seed` | `7` |  |
| `viewer.dashboard` | `false` | also write dashboard.html (interactive overview, tgo.dashboard) after each run |
| `viewer.dashboard_max_nodes` | `500` | actors in the dashboard relation network (highest weighted degree first) |

### `scale`

Engine selection and the DuckDB corpus engine (`tgo.scale`) for millions of entries.

| Key | Default | Meaning |
|---|---|---|
| `scale.engine` | `"auto"` | memory \| duckdb \| auto (duckdb when input exceeds auto_engine_bytes) |
| `scale.auto_engine_bytes` | `200000000` | input size above which "auto" picks the duckdb engine |
| `scale.batch_size` | `50000` | messages per streamed ingest batch (memory stays flat) |
| `scale.workers` | `-1` | ingest/extract processes: -1 = cpu_count - 1, 0/1 = in-process |
| `scale.threads` | `null` | DuckDB threads (None = all cores) |
| `scale.memory_limit` | `null` | DuckDB memory limit, e.g. "16GB" (spills to temp_directory beyond it) |
| `scale.temp_directory` | `null` | DuckDB spill directory |
| `scale.store_path` | `null` | DuckDB file (default <out>/tgo.duckdb) |
| `scale.neardup_exact_limit` | `2000000` | distinct fingerprints up to which near-dup search is exact (Faiss flat) |
| `scale.exports` | `["parquet", "neo4j", "ntriples"]` | bulk exports to write (n-triples is the slowest at 10M+) |
| `scale.restrict_files` | `true` | DuckDB may only touch input/out/db/temp dirs (external access off); network is always off |

### `plugins`

Extensions (`tgo.plugins`): extra sources, analysis steps and exporters.

| Key | Default | Meaning |
|---|---|---|
| `plugins.modules` | `[]` | Python modules to import (their decorators register) |
| `plugins.entry_points` | `true` | also load installed packages exposing entry-point group "tgo.plugins" |
| `plugins.options` | see table below | per-plugin settings keyed by plugin name; free-form (not validated by TGO) |

`plugins.options` defaults (a partial override merges into these):

| Entry | Value |
|---|---|

### Environment variables

| Variable | Meaning |
|---|---|
| `TGO_<SECTION>_<FIELD>` | override any Config field, e.g. TGO_SCALE_WORKERS=4 (applied by Config.load) |
| `TGO_DOMAIN` | namespace host for tg:/tgr: IRIs (default tgo.example.org) |
| `TGO_WHISPER_MODEL` | local faster-whisper (CTranslate2) model dir; enables real ASR |
| `TGO_OCR_MODELS` | local dir with RapidOCR det/cls/rec .onnx files; enables real OCR |
| `TG_API_ID / TG_API_HASH / TG_SESSION` | Telethon credentials for `tgo collect` (the only online step) |
| `HF_HUB_OFFLINE, TRANSFORMERS_OFFLINE, ORT_DISABLE_TELEMETRY, DO_NOT_TRACK` | set automatically by `import tgo` |
<!-- END GENERATED: config reference -->
