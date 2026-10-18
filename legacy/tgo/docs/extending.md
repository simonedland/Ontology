# Extending TGO

TGO is built to be adapted. Most changes are a **plugin**: a small module in your own package that TGO loads
with `--plugin`, with no edits to TGO itself. The rest are deliberate edits to one well-defined table or
function. This guide covers both, with a recipe for each.

- **New to the code?** Read [architecture.md](architecture.md) first (10 minutes).
- **Want to start right away?** Copy a template from [`examples/`](../examples/README.md).

## 1. Pick the right mechanism

| You want to… | Mechanism | Where | Recipe |
|---|---|---|---|
| read a new input (a CSV, another tool's export, another platform) | **plugin** `@source` | your module | [§4.1](#41-add-a-source) |
| derive something from media or text (a model, a parser, a watchlist) | **plugin** `@step(after="extract")` | your module | [§4.2](#42-add-an-extractor) |
| add a detection, score or clustering | **plugin** `@step` | your module | [§4.3](#43-add-an-analysis-step) |
| write a new output format | **plugin** `@exporter` | your module | [§4.4](#44-add-an-exporter) |
| tune thresholds, weights or caps | **config** | `tgo.toml` | [configuration.md](configuration.md) |
| add a node class or relation type | **vocabulary edit** | `tgo/ontology.py` | [§4.5](#45-add-a-class-or-relation-type) |
| enforce a new rule, or relax one | **validator edit** | `tgo/validate.py` (+ `tgo/scale/pipeline.py`) | [§4.6](#46-add-a-validation-rule) |
| recognise a new identifier (a wallet type, a national ID) | **core edit** | `tgo/identifiers.py` | [§4.7](#47-add-an-identifier-type) |
| change colours, shapes or views in the viewer | **viewer edit** | `tgo/viz/report.py` | [§4.8](#48-change-the-viewer) |
| add a setting to the core | **config edit** | `tgo/config.py` | [§4.9](#49-add-a-setting) |
| use TGO for a platform other than Telegram | combination | — | [§5](#5-adapting-tgo-beyond-telegram) |

## 2. Design rules

These rules keep extensions safe to combine and keep the output defensible. Each one is cheap to follow and
expensive to retrofit.

1. **Add, don't change.** Prefer a new property over a new class, and a new class over changing an existing
   one. Never rename or remove vocabulary that others may depend on; outputs and IRIs are public contracts
   (see `CONTRIBUTING.md`).
2. **Use deterministic keys** from `tgo.keys` (or a new helper there). The same real-world thing must get the
   same key from every source, or merging breaks. Never key on volatile ids (rule R4).
3. **Write inside an activity.** `with g.activity("CollectionRun" | "DetectionRun", key, agent={...}, **params):`
   gives every fact a run, parameters, and a tool or model record.
4. **Say how you know.** Derived edges get `status="inferred"` (or `file_declared` / `analyst`), `conf < 1.0`,
   a `method` and `evidence` keys. Rule R1 fails the graph otherwise.
5. **Stay offline and optional.** Load models from a local path with the library's offline flag, import heavy
   libraries lazily inside the function, and degrade to a fallback instead of failing (see
   [offline.md](offline.md)). Never call a hosted API. `offline-check` rejects cloud SDKs.
6. **Be deterministic.** Sort before iterating over sets or dicts that feed algorithms, and seed anything
   random, so re-runs give identical graphs.
7. **Mind the guardrails.** An account is never a person, there is no biometric matching, and there is no
   `sameAs`. Changing those rules is a policy decision; make it in `ontology.py` / `validate.py` where it is
   visible and reviewed, not inside a plugin.

## 3. Quick start: your first plugin in five minutes

```bash
mkdir mytgo && echo "" > mytgo/__init__.py
cp examples/csv_leads.py mytgo/tips.py
grep -n "TODO(adapt)" mytgo/tips.py                   # every spot you'll typically change
python -m tgo demo --out out/demo                     # gives you a test case in out/demo/input
cp examples/sample_data/leads.csv out/demo/input/
python -m tgo run --input out/demo/input --out out/try --plugin mytgo.tips --open
```

Check the result:

- `out/try/summary.json` → `"plugins": ["mytgo.tips"]`;
- the viewer shows a **Leads** case group;
- clicking an edge shows your `method`, `evidence` and run.

Three ways to load plugins:

| How | Use when |
|---|---|
| `--plugin pkg.module` (repeatable) on `tgo run` / `tgo demo` | trying things out |
| `[plugins] modules = ["pkg.module"]` in `tgo.toml` | a case or team configuration |
| a package entry point in group `tgo.plugins` (below) | a reusable, pip-installable extension |

```toml
# pyproject.toml of your plugin package
[project.entry-points."tgo.plugins"]
tips = "mytgo.tips"
```

Entry points are discovered offline through `importlib.metadata`. Turn that off with
`[plugins] entry_points = false`.

**Plugin settings** go in `tgo.toml` under `[plugins.options.<name>]`. Read them with
`cfg.plugins.options.get("<name>", {})`. TGO does not validate them, so validate them in your plugin.

## 4. Recipes

### 4.1 Add a source

Template: [`examples/csv_leads.py`](../examples/csv_leads.py).

```python
from pathlib import Path
from tgo import keys as K
from tgo.plugins import source

@source("my-source", discover=lambda input_dir: sorted(Path(input_dir).rglob("*.myformat")))
def ingest(g, path: Path) -> None:
    with g.activity("CollectionRun", f"my-source:{path.name}", source="my-tool"):
        for rec in parse(path):                                   # your parser
            acct = K.user(rec["id"])
            g.node(acct, "HumanAccount", userId=rec["id"], label=rec["name"])
            g.edge("MEMBER_OF", acct, K.channel(rec["chat_id"]), t=rec["joined"])   # observed by default
```

- `discover(input_dir)` returns the files to read, and `[]` means the source is skipped. The whole `--input`
  tree is searched, so pick a specific pattern.
- `ingest(g, path)` is called once per file. On the DuckDB engine, each file is one streamed batch and the
  built-in extractors run on it afterwards.
- **Write API** (identical on both engines):
  - `g.node(key, *labels, **props)`: merges; `None` props are ignored.
  - `g.edge(TYPE, src, dst, status=, conf=, method=, evidence=[...], t=epoch, scope=, observed_by=, discriminator=, **props)`:
    merges into `count` and `[t_first, t_last]`.
  - `g.field(key, name, value, Visibility.HIDDEN_BY_PRIVACY, source="my-tool:field")`: records *why* a
    value is absent.
- Attach downloaded files with `tgo.ingest.common.attach_file(...)` so they get content keys (`file:{sha256}`)
  and run through the extractors.
- If your records carry identities TGO already knows (Telegram user ids, usernames, file hashes), use the
  same key helpers, and your data merges with everything else automatically.

### 4.2 Add an extractor

Template: [`examples/watchlist_extractor.py`](../examples/watchlist_extractor.py). An extractor is a step
anchored `after="extract"`, so it sees every text and media node the built-in extractors produced.

- **Inputs:**
  - media nodes with `props["localPath"]` (iterate `g.by_label("Media")`);
  - message text (`g.by_label("Message")`, `props["text"]`);
  - derived text via `HAS_TEXT` / `HAS_TRANSCRIPT` edges.
- **Outputs:** new nodes, e.g. `ExtractedEntity`, `ExtractedText`, `MediaFingerprint`, or your own class
  (§4.5). Link them with derived edges, e.g. `MENTIONS_ENTITY`, `HAS_TEXT`, `CONTAINS_IDENTIFIER`.
- **Point into text** with `selector=f"TextPositionSelector:{start}-{end}"`, and into audio or video with
  `mediaFragment=f"#t={s:.2f},{e:.2f}"`. The viewer and exports keep them.
- **Record the tool** in the activity's `agent={"tool", "version", "modelId", "codeLicense", "weightsLicense"}`.
  Analysts see exactly which model produced a fact.
- **Optional local model pattern**:

  ```python
  def _load_model(model_dir):
      if not model_dir or not Path(model_dir).is_dir():
          return None                                  # not configured: use the fallback
      try:
          from some_lib import Model                   # lazy: TGO works without the library
          return Model(model_dir, local_files_only=True)
      except Exception:
          return None
  ```

- To search for identifiers inside new text, call `tgo.extractors.link_identifiers(g, owner_key, text, evidence=text_key)`.
  It validates checksums and writes `CONTAINS_IDENTIFIER` edges with selectors.
- For a built-in, always-on extractor, add it to `extractors.run_all()` instead. The same rules apply.

### 4.3 Add an analysis step

Templates: [`examples/amplifier_step.py`](../examples/amplifier_step.py) (memory) and
[`examples/amplifier_sql_step.py`](../examples/amplifier_sql_step.py) (DuckDB).

```python
@step("my-score", after="coordination", engines=("memory",))
def my_score(g, cfg) -> dict:
    with g.activity("DetectionRun", "my-score:v1", agent={"tool": "my-score", "version": "1"}, threshold=0.7):
        ...
    return {"scored": n}            # lands in summary.json["my-score"]
```

- **`after`** can be `extract`, `aggregate`, `coordination`, `communities`, `identity`, or another plugin
  step's name. Several steps with the same anchor run in registration order. A step whose anchor doesn't
  exist runs at the end, so it is never silently skipped.

  | Anchor | What is available |
  |---|---|
  | `extract` | media-derived nodes |
  | `aggregate` | actor projections (`POSTED_IN`, `FORWARDS_FROM`, …) |
  | `coordination` | coordination edges and clusters |
  | `communities` | `CommunityCluster` |
  | `identity` | candidates, judgements and person clusters |
- **Engines.**
  - `("memory",)`: the step gets `(g, cfg)` with the full graph. On the DuckDB engine it runs on the bounded
    case slice (good for detail work, wrong for corpus-wide statistics).
  - `("duckdb",)`: the step gets `(store, cfg)`. Read with `store.sql(...)`, write by building a scratch
    `Graph` and calling `store.flush(g)`; the pipeline finalizes afterwards. DuckDB steps all run after the
    built-in corpus analytics (communities and identity included). `after` only orders them among
    themselves. See [scaling.md](scaling.md).
  - For both, write the two variants as separate modules and load the one matching the engine. Don't
    register both in one run.
- Writing only **properties** onto existing nodes needs no vocabulary change. New **relation types** must be
  declared first (§4.5).

### 4.4 Add an exporter

Template: [`examples/csv_exporter.py`](../examples/csv_exporter.py).

```python
@exporter("my-format", engines=("memory", "duckdb"))
def export_mine(g, out_dir, cfg, store=None) -> dict:
    ...                                            # write into Path(out_dir) / "..."
    return {"records": n}                          # lands in summary.json["exports"]["my-format"]
```

- **Memory engine:** `g` is the full graph.
- **DuckDB engine:** `g` is the case slice and `store` is the corpus. Stream corpus-wide output with
  `store.sql(f"COPY (SELECT …) TO '{path}' (FORMAT parquet)")`. The output folder is in the store's
  `allowed_directories`; other paths are refused while `scale.restrict_files` is on.
- Map vocabulary through the tables in `tgo/ontology.py` (`RelDef.ftm`, `RelDef.rdf`, `ClassDef.align`)
  instead of hard-coding, so new classes and relations export automatically. `tgo/export/interop.py` shows
  this for FtM and STIX.

### 4.5 Add a class or relation type

Edit the tables in `tgo/ontology.py`:

```python
# _CLASS_LIST
_c("Vessel", (), ("schema:Vehicle",), layer="Economy", doc="A ship named in content.", family="Device"),
# _REL_LIST
RelDef("MENTIONS_VESSEL", "derived", "inferred", "#0984e3", "content names a vessel", ftm=None, rdf=None),
```

1. **A class** needs `parents` (for label inheritance), `align` (for the RDF/FtM mappings), `layer`, the
   default `epistemic` status, and a viewer `family`. Use an existing family, or add one in §4.8.
2. **A relation** needs:
   - a `tier`: `structural`, `pairwise`, `viewer_only`, `admin_only`, `derived` or `provenance`;
   - a default `status`;
   - a colour;
   - optional `ftm` / `rdf` mappings.
3. If it should appear in a specific viewer group, add the type to `EDGE_GROUPS` in `tgo/viz/report.py`. If
   it should influence communities, add it to `communities.edge_weights` in config.
4. Regenerate the reference: `python scripts/gen_docs.py`.
5. Run `pytest`. Writing an undeclared label or type still works, but it adds a `vocabulary` warning to
   `validation.json`, which makes typos visible.

### 4.6 Add a validation rule

The memory engine checks the graph in `tgo/validate.py::validate()`. The DuckDB engine checks the corpus
with SQL in `tgo/scale/pipeline.py::validate_store()`, then checks the case slice with `validate()`. A rule
appends a finding with a severity:

| Severity | Meaning |
|---|---|
| `violation` | breaks a hard rule |
| `warning` | a legal or epistemic risk an analyst must see |
| `info` | e.g. typed gaps |

```python
# validate.py, inside validate()
for e in g.by_type("MENTIONS_VESSEL"):
    if not e.props.get("selector"):
        add("R11-vessel-selector", "warning", e.id, "vessel mention without a text position")
```

```python
# scale/pipeline.py, inside validate_store()
rule("R11-vessel-selector", "warning",
     "SELECT id AS x FROM edges WHERE type = 'MENTIONS_VESSEL' AND json_extract_string(props, '$.selector') IS NULL",
     "vessel mentions without a text position")
```

Give each rule a stable id (`R<n>-short-name`). Downstream gates in CI or case management filter on it.

### 4.7 Add an identifier type

In `tgo/identifiers.py`:

1. add a compiled regex;
2. add a validator, preferably a real checksum so `conf` can be high;
3. add a block in `extract()` that calls `add(Found(kind, label, normalized, raw, start, end, validation, conf, extra))`.

`label` must be an `Identifier` subclass in the ontology (§4.5). `kind` becomes the key namespace
(`id:{kind}:{value}`). Every text source then picks it up automatically: messages, PDFs, OCR and transcripts.
Add test cases for valid, invalid-checksum and lookalike strings in `tests/`.

### 4.8 Change the viewer

All styling lives in tables at the top of `tgo/viz/report.py`:

| Table | Controls |
|---|---|
| `FAMILY_STYLE` | node family → shape, colour token, size and legend name |
| `EDGE_GROUPS` | relation types → colour group and legend name |
| `VIEWS` | which families and edge groups each view tab shows |
| `DIRECTED` | which relation types draw arrows |

The page itself is `tgo/viz/template.html`, plain HTML/CSS/JS. Cytoscape.js is inlined from
`tgo/viz/vendor/`, and the data arrives as `<script id="tgo-data" type="application/json">`. Rules:

- no external URLs: `offline-check` fails the build;
- render names with `textContent`, never `innerHTML`, because names are attacker-controlled;
- keep the cap (`viewer.max_nodes`) in mind for anything per-node.

### 4.9 Add a setting

Add a field with an inline comment to the right dataclass in `tgo/config.py`, read it where it is used
(`cfg.<section>.<field>`), and run `python scripts/gen_docs.py`. The field then appears in
[configuration.md](configuration.md) automatically. Plugins should use `plugins.options` instead (§3).

## 5. Adapting TGO beyond Telegram

The graph, provenance, analysis, validation, exports and viewer are platform-neutral. Only the sources and
some vocabulary are Telegram-specific. To bring in another platform (a forum, another messenger, a social
network):

1. **Sources.** Write a `@source` per export format (§4.1).
2. **Keys.** Add helpers in `tgo/keys.py` with a platform prefix (e.g. `forum:user:{id}`). Never reuse
   Telegram's `user:` namespace for another platform's ids.
3. **Vocabulary.** Reuse the platform-neutral classes (`Account`, `Message`, `Chat`, `Media`, `File`, the
   identifiers) and relations (`SENT`, `IN_CHAT`, `REPLIES_TO`, `MEMBER_OF`, …). Add only what doesn't fit,
   e.g. `Subreddit` as a subclass of `PlatformGroup` (§4.5).
4. **Analysis.** Coordination, communities, identity and the extractors work unchanged on anything written
   with those classes and relations. Thresholds may need different defaults per platform: use a config file
   per platform.
5. **Namespace.** Set `TGO_DOMAIN` so your RDF IRIs are yours.

## 6. Contract reference

| Hook | Decorator | Signature | Return | Engines |
|---|---|---|---|---|
| Source | `@source(name, discover=fn(input_dir) -> list[Path])` | `fn(g, path) -> None` | — | both (streamed per file on duckdb) |
| Step (memory) | `@step(name, after=..., engines=("memory",))` | `fn(g, cfg) -> dict` | `summary.json[name]` | memory; case slice on duckdb |
| Step (duckdb) | `@step(name, after=..., engines=("duckdb",))` | `fn(store, cfg) -> dict` | `summary.json[name]` | duckdb |
| Exporter | `@exporter(name, engines=(...))` | `fn(g, out_dir, cfg, store=None) -> dict` | `summary.json["exports"][name]` | as declared |

- Registry: `tgo.plugins.REGISTRY` (`.sources`, `.steps`, `.exporters`).
- Loader: `tgo.plugins.load(modules, entry_points=True)`.
- Built-in anchors: `tgo.plugins.BUILTIN_STEPS`.
- The loaded plugin modules are recorded in `summary.json["plugins"]`.

## 7. Testing your extension

- **Copy `tests/test_examples.py`.** It generates the fictional demo case, registers plugins through
  `Config.from_dict({"plugins": {...}})`, and runs the full pipeline on both engines under
  `tgo.offline.block_network()`. The fixture restores the plugin registry afterwards.
- **Run the guards:**
  - `python -m tgo.offline path/to/your/package` (static: no network code, no cloud SDKs);
  - `pytest` (including `tests/test_docs.py` if you changed the vocabulary or config);
  - `python scripts/gen_docs.py` after vocabulary or config edits.
- **Assert on meaning, not only on files existing.** For example: the expected edges exist,
  `report["validation"]["violation"] == 0`, and the summary numbers are right.
- The PR checklist and CI details are in `CONTRIBUTING.md`.
