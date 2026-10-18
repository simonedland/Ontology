# Data model and file contracts

This page describes what TGO stores and the exact shape of each file it reads or writes. Code that consumes
TGO output, such as a database loader, a dashboard or a notebook, can rely on these shapes.

## Nodes

```json
{
  "key": "user:900000001",
  "labels": ["Account", "Collector", "HumanAccount"],
  "props": {"userId": 900000001, "first_name": "Field", "label": "Field Analyst", "...": "..."},
  "sources": ["run:mtproto:demo-2026-10-15", "run:export:0a7bac40ef1e"],
  "fields": {
    "first_name": {"visibility": "visible", "source": "mtproto:user.first_name"},
    "lastSeen":   {"visibility": "hidden_by_privacy", "source": "mtproto:user.status=UserStatusRecently"}
  }
}
```

| Field | Meaning |
|---|---|
| `key` | Deterministic identity (see *Keys* below). The same real-world thing gets the same key from every source. |
| `labels` | Ontology classes, **including every ancestor**. `HumanAccount` implies `Account`. |
| `props` | Free-form properties. `label` (or `title`, `name`, `value`) is used for display. |
| `sources` | The runs that wrote or confirmed the node. |
| `fields` | Per-field visibility: `visible`, `hidden_by_privacy`, `not_set`, `not_requested`, `min_object` or `not_exposed_by_source`. |

## Edges

```json
{
  "id": "user:705502011-[CANDIDATE_SAME_PERSON]->user:705502099",
  "type": "CANDIDATE_SAME_PERSON",
  "src": "user:705502011",
  "dst": "user:705502099",
  "props": {
    "status": "inferred", "conf": 0.972, "method": "tgo-xref:noisy-or",
    "evidence": ["device:canon:SN-48213377", "fp:phash64:8ee4f4239a19d8dc"],
    "count": 1, "runs": ["run:xref:noisy-or:min0.3"],
    "t_first": 1760000000, "t_last": 1760000300,
    "scope": "public", "observed_by": "user:900000001"
  }
}
```

The edge `id` is `"{src}-[{TYPE}]->{dst}"`, plus `#discriminator` when several parallel edges of the same type
are needed. Writing the same `id` again **merges**.

| Prop | Always? | Meaning |
|---|---|---|
| `status` | yes | `observed`, `file_declared`, `inferred` or `analyst` |
| `conf` | yes | 0–1. `observed` defaults to 1.0. `inferred` must be < 1.0 (rule R1). |
| `method` | yes | the tool or rule that produced the edge, e.g. `platform`, `phash64`, `exif:BodySerialNumber` |
| `evidence` | derived edges | node keys that justify the edge (max 50) |
| `count` | yes | number of times observed |
| `runs` | when written in an activity | `run:` node keys |
| `t_first`, `t_last` | when timed | epoch seconds |
| `scope` | optional | who could see it: `public`, `members`, `admins`, `owner_only`, `link_holders` or `collector_only`. `admins` and `owner_only` also set `sensitivity: "elevated"`. |
| `observed_by` | viewer-relative facts | the collecting account's key |
| `art9` | set by the validator | membership of a special-category chat |

Relation-specific props are allowed. Examples: `distance` and `threshold` on `NEAR_DUPLICATE_OF`; `selector`
(`TextPositionSelector:start-end`) on `CONTAINS_IDENTIFIER`; `pValue` and `expectedByChance` on coordination
edges; `judgement`, `decidedBy` and `features` on `CANDIDATE_SAME_PERSON`.

## Keys

All keys come from `tgo/keys.py`. Use those helpers rather than formatting strings yourself.

| Entity | Key format | Helper |
|---|---|---|
| User account | `user:{id}` | `K.user(id)` |
| Channel or supergroup | `channel:{id}` | `K.channel(id)` |
| Basic group | `chat:{id}` | `K.chat(id)` |
| Community (layer 230) | `community:{id}` | `K.community(id)` |
| Message | `{peer}/msg:{id}` | `K.message(peer_key, id)` |
| Story / forum topic | `{peer}/story:{id}`, `{peer}/topic:{id}` | `K.story`, `K.topic` |
| Media occurrence | `{msg}/att:{i}` | `K.attachment` |
| File with bytes | `file:{sha256}` | `K.file_sha` |
| File without bytes | `media:{file_unique_id}`, then `doc:{id}` | `K.media_uid`, `K.document` |
| Photo (no original bytes exist) | `photo:{id}`, rendition `photo:{id}:{size}` | `K.photo`, `K.rendition` |
| Username | `username:{lowercase}` | `K.username` |
| Identifier | `id:{kind}:{normalized}`, e.g. `id:iban:GB82…`, `id:eth:0x…` | `K.identifier` |
| Device / geo / fingerprint | `device:{make}:{serial}`, `geo:{lat},{lon}`, `fp:{algo}:{hex}` | `K.device`, `K.geo`, `K.fingerprint` |
| Person cluster | `PC-{shortuuid}` | `K.person_cluster` |
| Run / agent | `run:{key}`, `agent:{tool}:{version}` | created by `Graph.activity` |
| Derived content | `text:{media}`, `ocr:{media}`, `transcript:{media}`, `meta:{media}` | (convention) |

Never key on `file_id`, `access_hash` or `file_reference`. They are account-scoped or change over time
(rule R4).

## Vocabulary

The authoritative list is in `tgo/ontology.py`. Run `python -m tgo ontology --out ontology.ttl` for the
RDF/OWL version, or open the **Ontology** tab in `report.html`.

- **Classes** (`CLASSES`, about 75) have a parent chain, W3C, schema.org and FtM alignments (`align`), a layer
  (Core, Content, Network, Economy, Derived or Provenance), a default epistemic status, and a viewer `family`
  that sets shape and colour.
- **Relations** (`RELATIONS`, about 70) have an observability tier, a default status, a colour and optional
  `ftm` and `rdf` mappings. The tier is one of:
  - `structural`
  - `pairwise`
  - `viewer_only`
  - `admin_only`
  - `derived`
  - `provenance`

Writing an undeclared label or relation still works, but it adds a `vocabulary` warning to `validation.json`.

## Input contracts

### Directory layout searched by `tgo run --input DIR`

```text
DIR/
  **/result.json                 Telegram Desktop export (JSON); media paths relative to result.json
  **/collector.json              MTProto collection root (written by `tgo collect`)
     entities.jsonl              one Telethon to_dict() object per line (User, Channel, UserFull, …)
     messages.jsonl              one Message / MessageService per line; optional "_tgo": {"media_path": "media/…"}
     media/…
  analyst/judgements.json        optional identity decisions (below)
```

### `collector.json`

```json
{"collector": {"_": "User", "id": 900000001, "first_name": "Field", "...": "..."},
 "run": {"id": "demo-2026-10-15", "client": "telethon", "clientVersion": "1.4x", "layer": 230, "started": "2026-10-15T08:00:00Z"}}
```

Objects are dispatched on the constructor **name** in `"_"`, never on CRC32 IDs.

### `analyst/judgements.json`

```json
[{"source": "user:705502011", "target": "user:705502099", "judgement": "positive",
  "decidedBy": "analyst:A-17", "legalBasis": "…", "rationale": "…"}]
```

`judgement` is one of `positive` (merge into a `PersonCluster`), `negative` (cannot-link), `unsure` or
`no_judgement`.

### Sidecars (stand-ins when no local model is installed)

| File next to the media | Used by | Shape |
|---|---|---|
| `<file>.transcript.json` | ASR | `{"language": "en", "language_probability": 0.93, "segments": [{"start": 0.0, "end": 5.4, "text": "…"}]}` |
| `<file>.ocr.txt` | OCR | plain UTF-8 text |

## Output files (`out_dir/`)

| File | Shape | Typical consumer |
|---|---|---|
| `graph.json` | `{"nodes": [Node…], "edges": [Edge…]}` exactly as above; `Graph.load(path)` reads it back | your code, notebooks |
| `validation.json` | `[{"rule", "severity": "violation"/"warning"/"info", "subject", "message"}]` | CI gates, analysts |
| `summary.json` | sources, per-stage results and timings, validation counts, `stats` (node families, edge types, statuses), export list | dashboards, regression checks |
| `ontology.ttl` / `tgo.ttl` | Turtle TBox / ABox. Namespace from `TGO_DOMAIN`. Derived edges reified as `tg:Assertion`. | triple stores, SPARQL |
| `neo4j.cypher` | idempotent `MERGE` script with a uniqueness constraint on `key` | Neo4j (`cypher-shell -f`) |
| `ftm.jsonl` | FollowTheMoney entities, one per line | OpenAleph / Aleph, nomenklatura |
| `stix.json` | STIX 2.1 bundle | OpenCTI, MISP |
| `graph.gexf` | GEXF 1.3 with positions and a timeline | Gephi |
| `dashboard.html` | single offline file (with `--dashboard` or `tgo dashboard`); pre-aggregated overview, see [dashboard.md](dashboard.md) | humans, briefings |
| `report.html` | single offline file; data embedded as `<script id="tgo-data" type="application/json">` | humans |

### DuckDB engine outputs

With `--engine duckdb` the corpus lives in `tgo.duckdb` (tables `nodes`, `edges` and `fields`, the same
semantics as above, with `props` as JSON and stable integer ids). Bulk exports go to `parquet/`, `neo4j/` and
`tgo.nt.gz`. The rich exports (`ftm.jsonl`, `stix.json`, `graph.gexf`, `report.html`) and `case_graph.json`
(same shape as `graph.json`) cover a bounded case slice. Table schemas and usage are in
[scaling.md](scaling.md#querying-the-corpus).

### Deep links into `report.html`

`report.html#/node/<key>` and `report.html#/edge/<id>` open the viewer focused on that element. Use them to
link from tickets, case notes or other tools.
