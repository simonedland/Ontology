# Architecture

TGO turns Telegram data into **one provenance-aware property graph**, then checks it against a set of
guardrails and exports it. This page explains how the pieces fit together, so you know where to make a change.
The step-by-step recipes are in [extending.md](extending.md). The exact shape of the data is in
[data-model.md](data-model.md).

## The one idea to keep in mind

Every component reads from and writes to the same in-memory `tgo.graph.Graph`. Components never call each
other directly; they communicate only through nodes and edges with **deterministic keys**. That is why you can
add, remove or reorder sources and analysis steps without touching the rest:

```text
              ┌──────────── sources (adapters) ─────────────────┐
              │ ingest/mtproto.py  ingest/desktop_export.py       │
              │ + plugin @source(...)  (tgo/plugins.py)           │   ← add yours here
              └───────────────┬──────────────────────────────────┘
                              │ g.node(key, Label, ...) / g.edge(TYPE, src, dst, ...)
                              ▼
     ┌──────────────────────────────────────────────────────────────────┐
     │  tgo.graph.Graph  (MERGE semantics, provenance on every write)    │
     │  vocabulary checked against tgo.ontology (CLASSES / RELATIONS)    │
     └──────────────────────────────────────────────────────────────────┘
        ▲ read + write                         │ read only
        │                                      ▼
  extractors.run_all                  validate.validate  →  validation.json
  analysis.aggregate                  export.*           →  ttl · ftm · stix · cypher · gexf
  analysis.coordination               viz.report         →  report.html
  analysis.communities
  analysis.identity
  + plugin @step(...)                + plugin @exporter(...)
```

The same picture holds for the **DuckDB engine** (`tgo/scale/`, for millions of entries). Adapters still write
to a small in-memory `Graph` per batch. That graph is flushed into a DuckDB store, the analysis runs as SQL
over the corpus, and the viewer and rich exports get a bounded case slice. See [scaling.md](scaling.md).

## Pipeline stages

`tgo.pipeline.run(input_dir, out_dir)` is the orchestrator. Each stage is a plain function that takes the
graph.

| # | Stage | Function | Reads | Writes |
|---|---|---|---|---|
| 1 | Discover | `pipeline.discover(dir)` | the filesystem: `**/collector.json`, `**/result.json`, `analyst/judgements.json` | a source list |
| 2 | Ingest | `ingest.ingest_mtproto(g, dir)`, `ingest.ingest_desktop_export(g, result_json)` | raw source files | Core and Content nodes; `observed` edges; `FieldVisibility` |
| 3 | Extract | `extractors.run_all(g, root)` | media nodes with `localPath` | fingerprints, EXIF, document text and metadata, transcripts, OCR, identifiers, entities |
| 4 | Aggregate | `analysis.aggregate.run(g)` | messages | actor-level projections (`POSTED_IN`, `FORWARDS_FROM`, …) |
| 5 | Coordination | `analysis.coordination.run(g, time_window=…)` | messages and their content | `IDENTICAL_POST`, `SYNC_POST`, `CO_*`, `CoordinationCluster` |
| 6 | Communities | `analysis.communities.run(g)` | actor edges | `CommunityCluster`, roles |
| 7 | Identity | `analysis.identity.run(g, decisions, auto_accept=…)` | account features | `CANDIDATE_SAME_PERSON`, `IdentityJudgement`, `PersonCluster` |
| 8 | Validate | `validate.validate(g)` | everything | a `Finding` list (no graph change except flagging `art9`) |
| 9 | Export | `export.write_*` | everything | files in `out_dir` |
| 10 | Visualise | `viz.report.build_report(...)` | everything plus the layout | `report.html` |

Stages 1–7 are in `pipeline.build_graph()`. Call it directly if you only want the graph, for example to feed
your own store or notebook.

**Plugins** (`tgo/plugins.py`) hook into these stages without editing the pipeline:

- `@source` adds to stage 2;
- `@step(after=…)` runs right after the named stage (`extract`, `aggregate`, `coordination`, `communities`,
  `identity`, or another plugin step);
- `@exporter` adds to stage 9.

Load them with `--plugin`, `[plugins] modules`, or the `tgo.plugins` entry-point group. See
[extending.md](extending.md).

**Engine selection.** `pipeline.choose_engine(input_dir, cfg)` returns `memory` or `duckdb` (from
`scale.engine`, default `auto`). When it returns `duckdb`, `pipeline.run()` hands over to
`tgo.scale.pipeline.run()`, which has the same signature and the same `summary.json` contract.

## Provenance model (why every fact can be explained)

- **Runs.** Each piece of code that writes runs inside `with g.activity(kind, key, agent={...}, **params):`.
  That creates a `run:<key>` node (`CollectionRun` or `DetectionRun`) holding the parameters as JSON. The
  `agent` dict becomes a `ModelAgent` node (tool, version, model, code and weights licence). Every node and
  edge written inside the `with` block records that run (`Node.sources`, `Edge.props["runs"]`).
- **Epistemic status.** Every edge has `status`:
  - `observed`: the platform said so;
  - `file_declared`: the file claims it, e.g. XMP or docProps, which can be forged;
  - `inferred`: computed, as all ML output and every identity link is;
  - `analyst`: a human decision.

  The default comes from the relation's `RelDef.status` in the ontology.
- **Confidence and evidence.** Derived edges carry `conf` (< 1.0), `method` and `evidence` (the keys of the
  nodes that justify the edge). The validator enforces this (rule R1).
- **Merging, not duplicating.** Writing the same node or edge again merges: props are overwritten when the new
  value is not `None`, `count` increments, and `[t_first, t_last]` widens. So any number of sources and re-runs
  converge on one graph.
- **Absence is data.** `g.field(key, name, value, Visibility.X, source=…)` records *why* a value is missing,
  e.g. `hidden_by_privacy`, `not_set` or `not_exposed_by_source`, so the gap is not confused with "no value".

## Module map

| Area | Files | Change it when you want to… |
|---|---|---|
| Vocabulary | `ontology.py` | add or rename a class or relation, change alignments, forbid something |
| Identity of things | `keys.py` | key a new kind of entity |
| Storage | `graph.py` | change merge rules, persistence, or networkx conversion |
| Sources | `ingest/*.py` | read a new format (Bot API dump, another platform, a CSV of leads) |
| Media understanding | `extractors.py`, `media.py`, `identifiers.py` | add an extractor, an identifier type, or a model |
| Analytics | `analysis/*.py` | add a detection or scoring step, or change weights and thresholds |
| Guardrails | `validate.py` | add a rule your legal or method framework requires |
| Interop | `export/*.py` | add an output format or change a mapping |
| Viewer | `viz/report.py`, `viz/template.html` | add a tab, colour or view |
| Orchestration | `pipeline.py`, `cli.py` | wire a new stage or command (usually a plugin is enough) |
| Extension points | `plugins.py` | see [extending.md](extending.md) |
| Scale engine | `scale/store.py`, `scale/stream.py`, `scale/analytics.py`, `scale/exports.py`, `scale/pipeline.py` | see [scaling.md](scaling.md) |
| Configuration | `config.py`, env vars | see [configuration.md](configuration.md) |
| Offline guard | `_env.py`, `offline.py` | see [offline.md](offline.md) |

## Design rules that the code enforces

These rules are deliberate. The validator (`validate.py`) fails the graph if any of them is broken:

1. **An account is never a person.** `Person` and `PersonCluster` nodes exist only with a positive
   `IdentityJudgement` (R3), and a positive judgement needs a `decidedBy` (R8).
2. **No `sameAs` and no biometrics.** `FORBIDDEN_RELATIONS` and `FORBIDDEN_LABELS` in `ontology.py` (R2).
3. **Stable keys only.** Never key on `file_id`, `access_hash` or `file_reference` (R4).
4. **No EXIF claims on server-recompressed photos** (R5).
5. **Viewer-relative facts name their observer** (R10).

If your use case needs different rules, change them in `ontology.py` and `validate.py`. That keeps the change
explicit and reviewable, instead of turning rules off quietly.

## Performance characteristics

- The graph lives in plain Python dicts. Laptop-scale cases (tens of thousands of messages) run in seconds;
  see *Scale* in the README.
- Coordination and near-duplicate search use a sliding time window and a band index, never all pairs.
- The viewer embeds at most a capped number of nodes. The full graph is always in `graph.json`,
  `neo4j.cypher` and `graph.gexf`.
- For millions of elements, use the DuckDB engine (`--engine duckdb`). Adapters and plugins are unchanged;
  see [scaling.md](scaling.md).
