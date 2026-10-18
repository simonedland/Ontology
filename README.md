# TGO: Telegram Graph Ontology toolkit (demo)

A Python implementation of the design in
[`Telegram media ontology and visualization.md`](Telegram%20media%20ontology%20and%20visualization.md).
TGO gets everything that can be learned about people, groupings and media from Telegram and maps it into
**one provenance-aware graph**. Facts that were *seen*, facts a *file claims* and facts that were *inferred*
stay apart. **An account is never a person.**

It is designed as a small, strict component of a larger system. Every source writes through the same
deterministic keys. Every fact carries `status, conf, method, evidence, run`. The graph exports to
RDF/Turtle, Neo4j, FtM (OpenAleph), STIX 2.1 (OpenCTI) and GEXF (Gephi). The whole pipeline runs
**offline**.

```text
 Desktop export (result.json) ─┐
 MTProto / Telethon dumps ─────┼─► ingest ─► extract ─► aggregate ─► coordination ─► communities ─► identity
 analyst judgements ───────────┘   (keys)   (hash, EXIF,  (actor      (CooRTweet)     (Louvain +     (scored
                                             PDF, ASR,     projection)                 stability)     candidates +
                                             OCR, IDs)                                               judgements)
                                                            │
                         validate (guardrails) ◄────────────┘──► export: ttl · ftm · stix · cypher · gexf · report.html
```

## Quick start

```bash
pip install -e .                 # core deps: networkx, pillow, numpy, scipy
python -m tgo demo --open        # synthetic data -> full pipeline -> out/demo/report.html
python -m tgo demo --dashboard   # ... plus out/demo/dashboard.html, the interactive overview
```

To run it on real data:

```bash
python -m tgo run --input path/to/case --out out/case --open
```

`--input` is searched recursively for:
- Telegram Desktop exports (`result.json` plus media folders; use *Export chat history* → JSON);
- MTProto collections (`collector.json`, `entities.jsonl`, `messages.jsonl`, `media/`) written by `tgo collect`;
- an optional `analyst/judgements.json` with identity decisions.

Live collection is optional. It needs your own API credentials and a lawful basis:

```bash
pip install -e ".[collect]"
set TG_API_ID=... & set TG_API_HASH=...      # or --api-id/--api-hash
python -m tgo collect --target @somechannel --limit 500 --out data/case/mtproto
```

## Documentation

| Read this | When you want to… |
|---|---|
| [docs/architecture.md](docs/architecture.md) | understand how the pieces fit together and where to make a change |
| [docs/configuration.md](docs/configuration.md) | tune thresholds, weights and caps (`--config tgo.toml`), or set the namespace and model paths |
| [docs/extending.md](docs/extending.md) | add a source, extractor, analysis step, relation type, validation rule, export format or viewer tab |
| [examples/](examples/README.md) | copy a working plugin template (CSV source, watchlist extractor, analysis step, SQL step, CSV exporter) |
| [docs/scaling.md](docs/scaling.md) | run, tune and query the DuckDB engine for millions of entries |
| [docs/dashboard.md](docs/dashboard.md) | explore a store visually: relation network, flows, ontology, activity, evidence quality (`tgo dashboard`) |
| [docs/data-model.md](docs/data-model.md) | consume TGO output: node and edge shapes, key formats, input and output file contracts |
| [docs/vocabulary.md](docs/vocabulary.md) | look up any class or relation (generated from `tgo/ontology.py`) |
| [docs/offline.md](docs/offline.md) | run air-gapped, add local AI models, or check the no-network guarantee |
| [CONTRIBUTING.md](CONTRIBUTING.md) | set up a dev environment, run the tests and CI, re-pin vendored JS, release |

## What's in the box

| Module | Role |
|---|---|
| `tgo/ontology.py` | The TBox as code: about 75 classes across 6 layers with W3C, schema.org and FtM alignments; about 70 relation types with observability tiers; SKOS schemes; forbidden edges and labels; Art. 9 topics; "not obtainable" facts. Exported as `ontology.ttl`. |
| `tgo/keys.py` | Deterministic keys: `user:`, `channel:`, `channel:X/msg:N`, `file:{sha256}`, `media:{file_unique_id}`, `photo:{id}`. Never `file_id`, `access_hash` or `file_reference`. |
| `tgo/graph.py` | Property graph with MERGE semantics. Repeated observations increment `count` and widen `[t_first, t_last]`. Per-field `FieldVisibility` records why a value is absent. `activity()` contexts give every write a PROV-O run. |
| `tgo/ingest/mtproto.py` | Telethon `to_dict()` objects (layer 229/230), dispatched on constructor **names**. Covers users and `userFull` snapshots, privacy-hidden fields, contacts as viewer-relative facts, channels and username history, Communities (layer 230), participants with inviters (admin-only), stories with media areas, gifts (hidden senders as typed gaps), sticker sets (ownership only via the creator flag or `_by_<bot>`), and messages with forwards, replies, reactions, albums and service actions. |
| `tgo/ingest/desktop_export.py` | `result.json`: export placeholders become typed gaps, invite names are resolved to ids, and the collector's contact names never overwrite an account's own name. |
| `tgo/ingest/collector.py` | Live Telethon collector: incremental, FloodWait-tolerant, stable media paths. This is the only online code path. |
| `tgo/media.py` | Document classification in TDLib order (sticker → emoji → round/voice/animated → video/audio → document), PhotoSize letters, the 5-bit waveform codec, MediaArea → W3C `xywh=percent:` and `SvgSelector`, spoiler/TTL on the attachment. |
| `tgo/identifiers.py` | Checksum-validated identifiers: IBAN mod-97, BTC Base58Check and Bech32/Bech32m, TRON Base58Check, ETH EIP-55 (pure-Python Keccak), phones, canonical URLs, t.me links, @handles, emails. |
| `tgo/extractors.py` | Each extractor runs as a logged `prov:Activity`: sha256; PDQ (if installed) or DCT pHash → `MediaFingerprint`, `NEAR_DUPLICATE_OF`; EXIF serial and GPS on **document-mode** images only; PDF/OOXML text plus file-declared metadata and `xmpMM:DocumentID` lineage; ASR (faster-whisper from a local dir, or a sidecar); OCR (RapidOCR, Tesseract or a sidecar); NER (spaCy or a gazetteer). |
| `tgo/analysis/aggregate.py` | Projections from messages to actors: `POSTED_IN`, `FORWARDS_FROM` (the DFRLab forward graph), handle → account mentions, `NAME_HISTORY_LINK`, `SAME_FILE_AS`. |
| `tgo/analysis/coordination.py` | A port of CooRTweet `detect_groups`: shared object (text hash, SimHash, forwarded original, URL, media fingerprint) within a time window. Produces `IDENTICAL_POST`, `SYNC_POST`, `CO_*` and `SAME_CREATION_DATE` edges plus `CoordinationCluster`. A Poisson **null model** (expected chance co-shares from each account's own posting rate) removes the false positives that plain CooRTweet produces on popular URLs; `pValue` and `expectedByChance` are kept on every edge. |
| `tgo/analysis/communities.py` | Seeded multi-run Louvain → `CommunityCluster` with `stabilityScore` and `coAssignmentFrequency`. Roles: originator, amplifier, bridge. |
| `tgo/analysis/identity.py` | nomenklatura-style judgements. A noisy-OR over **non-biometric** evidence with IDF down-weighting produces `no_judgement` candidates. Only analysts (or an explicit auto-accept policy) say `positive`, which mints a `PersonCluster`. `negative` acts as a cannot-link. `explode()` un-merges. |
| `tgo/validate.py` | Guardrails as code: derived provenance, no `sameAs` and no biometrics, person only by judgement, stable keys, no EXIF on photo-mode media, Art. 9 memberships, admin-only sensitivity, sticker ownership, viewer-relative facts, typed-gap inventory. |
| `tgo/export/` | Turtle (TBox + ABox; derived edges reified as `tg:Assertion`, memberships as `org:Membership`), FtM JSONL, STIX 2.1 bundle, Neo4j Cypher MERGE script, GEXF 1.3 with positions and timeline. |
| `tgo/dashboard/` | `dashboard.html`: an offline ECharts overview of a whole store (either engine). Relation network with message-level facts projected onto actors, Sankey flows, chord, VOWL-style schema graph, class sunburst, activity, evidence quality, findings; global filters and table views. |
| `tgo/viz/` | A self-contained offline `report.html` (Cytoscape.js inlined). Edge hue = relation group; solid, dashed or dotted line = observed, inferred or file-declared; opacity = confidence; width = event count. The "explain this edge" panel shows evidence messages, runs, tools, versions and licences. Tabs: coordination raster, identity judgements, media gallery (blurred avatars, waveforms, transcripts, EXIF, metadata), guardrails and coverage, ontology. |
| `tgo/demo/synth.py` | A fully fictional dataset that plants every scenario (below). |

## The demo scenarios (asserted in `tests/test_pipeline.py`)

1. **Regional channel franchise ("Veloria").** Eight channels created on the same day post identical or near-identical text within seconds of an originator, forward it, re-upload its image and promote one domain. The pipeline finds exactly this 9-channel coordination cluster. An organic gardening channel that forwards the originator once, hours later, is **not** included.
2. **Same operator, two accounts.** The accounts share a camera body serial (EXIF from photo-as-file), a personal channel, the bio text and a re-compressed avatar (perceptual hash). The candidate scores 0.97; the analyst confirms it and a `PersonCluster` is minted.
3. **Shared wallet only.** The candidate scores 0.45; the analyst rejects it as a cannot-link.
4. **Stock avatar on four throwaway accounts.** IDF down-weighting keeps them below the threshold.
5. **Voice note.** The transcript yields a place, a TRON wallet and a phone number, each linked with text-position selectors. The waveform is decoded from the 5-bit Telegram format.
6. **Invoice PDF re-saved.** Same `xmpMM:DocumentID`, different bytes → `SAME_DOCUMENT_LINEAGE`. The IBAN and ETH address are checksum-validated.
7. **Layer-230 Community; gifts** (one with a hidden sender); a **story repost** with a geo media area; a **collectible username** moved between channels; a **sticker set** owned via `_by_<bot>`; an **Art. 9** religious chat (flagged); privacy-hidden fields; a file excluded from the export. All appear as data.
8. The same messages and files arrive from **both** the MTProto dump and the Desktop export, and **merge** through the deterministic keys.

## Deliberately excluded

The following are excluded on GDPR Art. 9 and EU AI Act Art. 5 grounds:
- face recognition and face clustering;
- speaker or voice matching;
- the sticker-set-ID creator heuristic;
- leaked databases;
- password guessing on encrypted archives.

`validate.py` fails the graph if any of these appear (`FORBIDDEN_RELATIONS` / `FORBIDDEN_LABELS`). Avatar linking hashes the *picture* (identical image reuse) and never the face.

## Offline operation

Everything except `tgo collect` runs with no network:
- `import tgo` hardens the environment (`HF_HUB_OFFLINE`, `ORT_DISABLE_TELEMETRY`, …);
- the viewer has no CDN, font or map-tile references;
- optional models load only from local paths.

Two checks enforce this: `python -m tgo offline-check` and `pytest tests/test_offline.py`.

| Optional capability | Install | Configure |
|---|---|---|
| PDQ hashing | `.[image]` | — |
| PDF text (better) | `.[docs]` | — |
| Speech-to-text | `.[asr]` | `TGO_WHISPER_MODEL=<local CTranslate2 dir, e.g. faster-whisper large-v3-turbo>` |
| OCR | `.[ocr]` | `TGO_OCR_MODELS=<dir with det/cls/rec .onnx>` |
| Phone validation | `.[ids]` | — |
| NER | `.[ner]` + the `xx_ent_wiki_sm` wheel | — |

Without them, the pipeline falls back to pHash, the built-in PDF parser, `.transcript.json` / `.ocr.txt` sidecars and a gazetteer. The ModelAgent provenance records which tool actually ran.

**Air-gapped install.** The only other online step is a one-time setup on a connected machine with the same OS and Python:

```bash
uv lock && uv export --frozen --all-extras --no-hashes -o requirements.lock.txt
pip download -r requirements.lock.txt -d wheelhouse --only-binary=:all:
uv build --wheel -o wheelhouse
hf download Systran/faster-whisper-small --local-dir models/faster-whisper-small   # or large-v3-turbo
# offline machine:
pip install --no-index --find-links wheelhouse "tgo[all]"
```

## Outputs (`out/<case>/`)

| File | Contents |
|---|---|
| `report.html` | Interactive investigation viewer (single file) |
| `dashboard.html` | Interactive data dashboard: relation network, flows, ontology, activity, evidence quality (with `--dashboard`; see [docs/dashboard.md](docs/dashboard.md)) |
| `graph.json` | The full property graph: nodes, edges, provenance and field visibility |
| `ontology.ttl` / `tgo.ttl` | TBox / ABox in Turtle |
| `neo4j.cypher` | MERGE script with a uniqueness constraint |
| `ftm.jsonl` | FollowTheMoney entities (UserAccount, Organization, Message, Membership, Similar, CryptoWallet, …) |
| `stix.json` | STIX 2.1 bundle (user-account, channel, cryptocurrency-wallet, url, relationships) |
| `graph.gexf` | GEXF 1.3 with positions and timeline for Gephi |
| `validation.json`, `summary.json` | Guardrail findings, counts, timings |

## Scale: millions of entries

TGO has two engines with **identical semantics**. A parity test asserts that both produce the same edges, confidences, counts, labels and field-visibility records.

| | `memory` engine | `duckdb` engine (`tgo.scale`) |
|---|---|---|
| For | single cases, up to roughly 100k messages | corpora of millions to tens of millions of messages |
| Store | Python `Graph` | DuckDB file (`tgo.duckdb`) + Parquet |
| Ingest | whole input | streamed batches (`batch_size`), process pool (`workers`), Arrow inserts, flat memory |
| Merge | MERGE on write | append-only staging → incremental GROUP BY finalize (watermark, single-row fast path, stable dense int64 ids) |
| Coordination | windowed pairs in Python | SQL equi-join on (object, time bucket) ± 1 bucket, first-share dedupe, object fan-out cap, Poisson null model |
| Near-dup text | SimHash window scan | 4 × 16-bit SimHash block joins (pigeonhole, k ≤ 3) |
| Near-dup images | band index | Faiss binary HNSW k-NN + radius filter |
| Communities | NetworkX Louvain | NetworKit PLM (parallel Louvain), multi-seed stability |
| Identity | Python | SQL pair generation with an evidence-key degree cap and IDF |
| Exports | Turtle, Cypher, FtM, STIX, GEXF | partitioned Parquet, `neo4j-admin import` CSVs (integer ids), gzipped N-Triples, plus FtM/STIX/GEXF/viewer for a bounded **case slice** |

```bash
pip install -e ".[scale]"           # duckdb (<2), pyarrow, networkit, faiss-cpu: all MIT/Apache
python -m tgo run --input corpus --out out/corpus --engine duckdb --workers -1
python scripts/gen_bench.py out/bench1m --messages 1000000   # reproducible fictional corpus with planted rings
```

`--engine auto` (the default) switches to DuckDB when the input exceeds `scale.auto_engine_bytes` (200 MB). Run `tgo config` to see every knob: batch size, workers, DuckDB threads, memory limit and spill directory, fan-out caps, viewer cap.

**Measured** on a 12-core Windows laptop with the fictional `gen_bench.py` corpus (Zipf-skewed channels, popular URLs, forwards, group chatter, plus planted coordination rings):

| Messages | Corpus nodes / edges | Wall time (end to end) | Peak RAM (all processes) | Planted rings recovered |
|---|---|---|---|---|
| 1,000,000 | 1.26 M / 2.85 M | 100 s | 5.1 GB | 5 / 5 exact, no false clusters |
| 10,000,000 | 12.0 M / 28.8 M | 46 min (15 GB laptop, spilling to disk) | 18.6 GB incl. workers | 8 / 8 exact, 0 violations |

### Which storage fits: deployment tiers (researched October 2026)

**There is no database server or Docker container by default.** The system of record is an **embedded DuckDB file plus Parquet**, which runs in-process, offline, on Windows and Linux, MIT-licensed. Servers are optional, rebuildable *views* fed from the same exports:

| Tier | When | What | Status in this repo |
|---|---|---|---|
| 1. Embedded lakehouse (default) | up to ~10⁸ edges on one workstation | DuckDB file + partitioned Parquet; NetworKit/Faiss for algorithms | `tgo run --engine duckdb` (benchmarked below) |
| 2. Ontology/SPARQL view | ontology-aligned queries without copying data | **Ontop** virtual knowledge graph (Apache-2.0) translating SPARQL to SQL over `tgo.duckdb` (read-only) | `tgo ontop --out DIR` + `deploy/ontop/` (pinned image; verified with live SPARQL) |
| 3. Interactive graph server | multi-analyst exploration from ~3×10⁸ to 10⁹+ edges | **Neo4j 5.26 LTS** in Docker on an on-prem Linux host, bulk-loaded with `neo4j-admin import` from `out/neo4j/`; ~120–200 GB store and 128–256 GB RAM per 10⁹ edges (research estimate) | `deploy/neo4j/` (digest-pinned, telemetry off, localhost-bound; import and Cypher verified) |
| Microsoft Fabric | sanitised aggregates and Power BI only | OneLake from the Parquet export; Fabric Graph is SaaS-only (no air-gap), ≤2B elements, read-only GQL | Parquet export is compatible |

Why this mix:
- **Neo4j Community** is GPLv3. Neo4j **Enterprise** is needed if the database itself must enforce per-field privacy visibility (property-level RBAC).
- **Not chosen:**
  - Memgraph: in-memory, about 150 GB+ RAM per 10⁹ edges, BSL licence.
  - ArangoDB: 100 GB cap. TigerGraph: 200 GB cap.
  - FalkorDB: SSPL.
  - GraphDB Free: non-commercial use only.
  - Fabric Graph: not available offline.
- **LadybugDB** (MIT, the Kùzu fork) is a promising embedded Cypher layer over Parquet, but the project is young.
- **RDF 1.2 triple terms** are still a W3C Candidate Recommendation. TGO therefore models edge provenance as n-ary `tg:Assertion` nodes, which works in every store today.

Design notes (sources in `docs/`):
- **DuckDB 1.5** is the system of record; pin `<2` until its new storage format settles. Merges never use per-row `ON CONFLICT`.
- **Parquet** is the durable interchange format for Fabric/OneLake, Spark and Polars.
- The browser never receives the corpus. The viewer gets a ranked case slice: derived structure first, then its evidence, then top actors.
- At ~50M+ edges, move serving to Neo4j (bulk import) or Fabric Graph and overview rendering to cosmos.gl. The Parquet and adapter contracts stay the same.

## Tests

```bash
pytest -q
```

The suite covers semantic scenario assertions, identifier checksums, classification order, the waveform codec, resolver cannot-link and explode, guardrails, exports, offline end-to-end runs and pinned vendor hashes.

## Integrating into the bigger system

- **Library use.** Call `tgo.pipeline.build_graph(input_dir)` to get a `Graph`, then use `g.nodes` / `g.edges` with `Edge.props` (`status, conf, method, evidence, runs, scope, observed_by, t_first, t_last`).
- **Adding a source.** Write an adapter that calls `g.node(key, Label, **props)` / `g.edge(TYPE, src, dst, ...)` inside `with g.activity("CollectionRun", ...)`. Use `tgo.keys`, and everything else (extraction, analysis, validation, exports, viewer) applies unchanged.
- **Persisting.** Load `neo4j.cypher`, or the Turtle into a triple store. Keys are stable across runs, so re-ingesting merges rather than duplicates.

All demo data is synthetic and fictional.
