# Scaling to millions of entries

TGO has two engines with the same semantics; a parity test checks that they produce the same edges,
confidences, counts, labels and field visibility. The README's *Scale* section compares them and gives
benchmarks. This page is the operator's guide: how to choose, tune, query and extend the DuckDB engine.

## Choosing an engine

| Situation | Engine |
|---|---|
| one investigation, up to ~100k messages, you want everything in the viewer | `memory` |
| a corpus of millions of messages, a monitoring archive, a data lake | `duckdb` |
| you don't know yet | `auto` (the default): `duckdb` when the input exceeds `scale.auto_engine_bytes` (200 MB) *and* the `scale` extra is installed |

```bash
pip install -e ".[scale]"                                  # duckdb, pyarrow, networkit, faiss-cpu
python -m tgo run --input corpus --out out/corpus --engine duckdb --workers -1
```

An explicit `--engine duckdb` without the extra stops with a clear `pip install 'tgo[scale]'` message. `auto`
quietly stays on `memory`.

## Deployment tiers: where the data lives

There is **no database server by default**. The system of record is the embedded DuckDB file plus Parquet,
which runs in-process and offline. Servers are optional, rebuildable *views* fed from the same exports:

| Tier | Use when | Run it |
|---|---|---|
| **1. Embedded lakehouse** (default) | up to ~10⁸ edges on one workstation | `tgo run --engine duckdb`; query `tgo.duckdb` or `parquet/` (below) |
| **2. Ontology / SPARQL view** | ontology-aligned queries without copying data; RDF tools that speak SPARQL | `tgo ontop --out out/corpus`, then `docker compose --profile ontop up -d` ([docker.md](docker.md)). Ontop (Apache-2.0) translates SPARQL to SQL over `tgo.duckdb`, read-only. Put the DuckDB JDBC jar in `workspace/jdbc/`. |
| **3. Interactive graph server** | several analysts exploring ~3×10⁸ to 10⁹+ edges | `docker compose --profile neo4j up -d` ([docker.md](docker.md)): Neo4j 5.26 LTS community, digest-pinned, localhost-only, telemetry off. Each `up` bulk-imports `out/$TGO_CASE/neo4j/` (set `NEO4J_PASSWORD`). Plan ~120–200 GB store and 128–256 GB RAM per 10⁹ edges. |
| Microsoft Fabric | sanitised aggregates and Power BI only | load the Parquet export into OneLake. Fabric Graph is SaaS-only, so it can't run air-gapped. |

- **Licensing:** Neo4j Community is GPLv3. Enforcing per-field visibility *inside* the database
  (property-level RBAC) needs Neo4j Enterprise.
- **Considered and rejected:** Memgraph (in-memory, BSL), ArangoDB and TigerGraph (size caps on free
  editions), FalkorDB (SSPL), GraphDB Free (non-commercial use only).
- **Details:** the README's *Which storage fits* section has the full comparison.

**Sources** (collected 2026-10-18; ⚠ marks figures that are an estimate or unconfirmed):

- **Neo4j**
  - [editions, Community GPLv3, Enterprise property-level RBAC](https://neo4j.com/docs/operations-manual/current/introduction/)
  - [`neo4j-admin import`](https://neo4j.com/docs/operations-manual/2026.08/import/full-import/)
  - [GDS Community limited to 4 cores](https://neo4j.com/docs/graph-data-science/current/installation/System-requirements/)
  - [on-disk record sizes](https://support.neo4j.com/s/article/360013978714-Understanding-Neo4j-8217-s-data-on-disk): the basis of the ⚠ 120–200 GB per 10⁹ edges estimate, which is not a vendor figure. ⚠ Bulk-load hours at TGO's property width are unverified.
  - [property sharding](https://www.theregister.com/2025/09/11/neo4j_property_sharding_to_address_scalability_challenge/)
- **Rejected or alternative stores**
  - Memgraph: [licence (BSL, Apache-2.0 in 2029)](https://github.com/memgraph/memgraph/blob/master/LICENSE), [memory per vertex/edge](https://memgraph.com/docs/fundamentals/storage-memory-usage), [Docker/WSL only on Windows](https://memgraph.com/docs/getting-started/first-steps-with-docker)
  - ArangoDB: [BSL, 100 GB Community cap](https://arango.ai/blog/evolving-arangodbs-licensing-model-for-a-sustainable-future/)
  - TigerGraph: [Community edition, 200 GB](https://www.tigergraph.com/community-edition/)
  - FalkorDB: [SSPL](https://docs.falkordb.com/references/license)
  - GraphDB Free: [licence terms](https://graphdb.ontotext.com/documentation/11.5/licensing.html)
  - Stardog: [Stardog Free terms](https://www.stardog.com/legal/stardog-free/)
  - Apache AGE: [releases](https://github.com/apache/age/releases)
  - JanusGraph: [1.1.0](https://github.com/JanusGraph/janusgraph/releases/tag/v1.1.0)
  - Cloud-only: [Spanner Graph](https://docs.cloud.google.com/spanner/docs/graph/overview), [Neptune](https://atlan.com/know/ai-agent/knowledge-graph/amazon-neptune-graph-database/)
- **Embedded options**
  - LadybugDB: [docs](https://docs.ladybugdb.com), [from Kùzu to Ladybug](https://thedataquarry.com/blog/from-kuzu-to-ladybug/) (⚠ Windows support and stability unconfirmed)
  - Kùzu: [17B-edge load](https://blog.kuzudb.com/post/kuzu-0.7.0-release/)
  - DuckPGQ: [extension](https://github.com/cwida/duckpgq-extension)
- **Microsoft Fabric**: [Graph GA](https://community.fabric.microsoft.com/t5/Fabric-Updates-Blog/Graph-in-Fabric-Generally-Available/ba-p/5190748), [limits (~2B elements, 64 MB results, no DML or export)](https://learn.microsoft.com/en-us/fabric/graph/limitations)
- **RDF and SPARQL**
  - RDF 1.2: [Concepts (CR 2026-04-07)](https://www.w3.org/TR/rdf12-concepts/), [N-Triples status](https://www.w3.org/standards/history/rdf12-n-triples/)
  - Ontop: [with DuckDB](https://ontop-vkg.org/guide/databases/duckdb.html), [releases](https://ontop-vkg.org/guide/releases.html), [~1B observations (LinkedIn post, weaker evidence)](https://www.linkedin.com/pulse/scaling-sparql-querying-billion-observations-ontop-duckdb-gschwend-myghf). ⚠ Performance on TGO's join-heavy mappings is verified only at demo scale.
  - QLever: [repo](https://github.com/ad-freiburg/qlever), [Wikidata benchmark](https://www.wikidata.org/wiki/Wikidata:Scaling_Wikidata/Benchmarking/QLever), [RDF 1.2 issue](https://github.com/ad-freiburg/qlever/issues/2169)
  - Oxigraph: [releases (RDF 1.2 triple terms)](https://github.com/oxigraph/oxigraph/releases)
  - Virtuoso: [OSS releases](https://github.com/openlink/virtuoso-opensource/releases) (⚠ RDF 1.2 support unconfirmed)
  - Jena and others: [large triple stores](https://www.w3.org/wiki/LargeTripleStores)

## How the DuckDB engine runs

```text
streamed ingest + extract (batches × worker processes, Arrow inserts) ─► finalize (MERGE)
  ─► aggregate · coordination · near-duplicates (SQL / Faiss) ─► finalize
  ─► communities (NetworKit) · identity (SQL + judgements) ─► DuckDB plugin steps ─► finalize
  ─► corpus guardrails (SQL) ─► bulk exports (Parquet · Neo4j CSV · N-Triples)
  ─► case slice (≤ viewer.max_nodes) ─► memory-only plugin steps · FtM · STIX · GEXF · report.html
```

- **Same adapters.** Every batch runs the normal source adapters into a fresh in-memory `Graph`, which is
  then flushed to the store. A plugin source written for the memory engine works unchanged.
- **Merging** happens in append-only staging tables followed by an incremental `GROUP BY` finalize, with the
  same rules as `Graph`. One subtlety: an edge produced only by detection runs is a derivation, so seeing it
  again in another batch takes `max(count)` instead of summing.
- **The browser never gets the corpus.** The viewer and the FtM/STIX/GEXF exports cover a ranked **case
  slice**: derived structure first, then its evidence, then the top actors. The corpus lives in `tgo.duckdb`
  and the bulk exports.
- **Each run rebuilds the store.** `tgo.duckdb` in the output folder is recreated, and the whole input is
  re-ingested. To keep history, write each run to a new `--out`.

## Tuning

These are `scale.*` keys in your config file unless a section is shown (reference in [configuration.md](configuration.md)):

| Key | Default | Raise it when… | Lower it when… |
|---|---|---|---|
| `workers` | `-1` (cores − 1) | ingest is CPU-bound (extractors, JSON parsing) | RAM is tight: each worker holds one batch |
| `batch_size` | 50,000 messages | there are many small files and overhead dominates | worker memory spikes |
| `threads` | all cores | — | sharing the machine with other work |
| `memory_limit` | DuckDB default (~80% of RAM) | — | other processes need RAM; DuckDB then spills to `temp_directory` |
| `temp_directory` | next to the store | — | point it at a fast local SSD with free space |
| `coordination.max_object_accounts` | 1,000 | a legitimately viral object should still count | popular URLs create noise (fan-out cap; dropped objects are logged) |
| `identity.max_evidence_accounts` | 50 | — | shared stock evidence floods identity candidates |
| `restrict_files` | `true` | — | (keep on; see [Offline and security](#offline-and-security)) |
| `exports` | `["parquet", "neo4j", "ntriples"]` | — | you don't need a format: N-Triples is the slowest (~8 min at 10M messages); drop it unless you load a triple store |

Rules of thumb from the benchmark (12-core laptop):

- **Throughput:** roughly 10k messages per second end to end.
- **Peak RAM:** about 5 GB per million messages across all processes, dominated by workers.

To cut RAM, lower `workers` first.

> **Windows:** worker processes use *spawn*. A script that calls `tgo.pipeline.run(...)` with `workers` > 1
> needs an `if __name__ == "__main__":` guard. `python -m tgo` already has one.

## Querying the corpus

The store is an ordinary DuckDB file. Open it through TGO's `Store`, which keeps the offline lockdown:

```python
from tgo.scale.store import Store

s = Store("out/corpus/tgo.duckdb")
s.sql("SELECT type, count(*) AS n FROM edges GROUP BY 1 ORDER BY n DESC").fetchall()
s.sql("""SELECT key, props->>'$.label' AS name FROM nodes
         WHERE list_contains(labels, 'Channel') LIMIT 20""").fetchall()
s.sql("""SELECT src, dst, conf, evidence FROM edges
         WHERE type = 'CANDIDATE_SAME_PERSON' ORDER BY conf DESC LIMIT 20""").fetchall()
```

| Table | Columns |
|---|---|
| `nodes` | `id` (stable int64), `key`, `labels` (`VARCHAR[]`, including ancestors), `props` (JSON), `sources` |
| `edges` | `id`, `type`, `src`, `dst`, `src_id`, `dst_id`, `status`, `conf`, `method`, `count`, `t_first`, `t_last`, `scope`, `observed_by`, `evidence[]`, `runs[]`, `props` (JSON) |
| `fields` | `key`, `field`, `visibility`, `source`, `observed_by` |
| `shares` | the coordination input: `object_id`, `kind`, `account`, `content`, `ts` |

The semantics match [data-model.md](data-model.md); `props` holds everything that isn't a column. If you open
the file with plain `duckdb.connect(...)` in your own scripts, pass
`config={"autoinstall_known_extensions": False, "autoload_known_extensions": False}`. Otherwise DuckDB may
try to download extensions.

## Using the bulk outputs

| Output | Use it with |
|---|---|
| `parquet/nodes.parquet`, `parquet/fields.parquet`, `parquet/edges/type=*/…` (Hive-partitioned by edge type) | pandas, Polars, Spark, Microsoft Fabric / OneLake, Power BI. E.g. `polars.scan_parquet("out/corpus/parquet/edges/**/*.parquet", hive_partitioning=True)` |
| `neo4j/nodes.csv`, `neo4j/relationships.csv`, `neo4j/import.sh` / `import.cmd` | `neo4j-admin database import` (stop Neo4j first, run the script from that folder); integer ids for fast import |
| `tgo.nt.gz` | any triple store (GraphDB, Fuseki, Oxigraph); derived edges are reified as `tg:Assertion` with confidence and provenance |
| `case_graph.json` | same shape as the memory engine's `graph.json`, for the case slice; `Graph.load()` reads it |
| `summary.json` | contains `"engine": "duckdb"`, `"store"`, per-stage timings, `"plugins"` and `"config"` |

## Writing DuckDB-native steps

Template: [`examples/amplifier_sql_step.py`](../examples/amplifier_sql_step.py). The pattern:

```python
@step("my-sql-step", after="aggregate", engines=("duckdb",))
def my_sql_step(store, cfg):
    rows = store.sql("SELECT dst, count(DISTINCT src) FROM edges WHERE type = ? GROUP BY dst", ["FORWARDS_FROM"]).fetchall()
    g = Graph()                                   # scratch graph with only your additions
    with g.activity("DetectionRun", "my-sql-step:v1", agent={"tool": "my-sql-step", "version": "1"}):
        for key, n in rows:
            g.node(key, myScore=n)                # or g.edge(...) for new derived edges
    store.flush(g)                                # merged at the next finalize
    return {"rows": len(rows)}
```

- Do heavy lifting in SQL and bring back only results.
- Use parameters (`?`) rather than string formatting for values.
- Write through `store.flush(Graph)` so merge rules, provenance and validation stay identical. Never
  `INSERT` into `nodes` or `edges` directly.
- DuckDB steps run after the built-in corpus analytics. `after` orders them among themselves.
- Corpus-wide guardrails for new relation types go in `validate_store()` (see
  [extending.md §4.6](extending.md#46-add-a-validation-rule)).

## Offline and security

The store opens DuckDB with two independent lockdown layers, because DuckDB's networking is native code that
Python-level guards cannot see:

1. **No downloads, ever:**
   - extension auto-install and auto-load are off, and community and unsigned extensions are refused;
   - the extension repositories point to a nonexistent local folder;
   - DuckDB's own `http_proxy` is a dead local port.
2. **No files outside the case.** With `scale.restrict_files = true`, `allowed_directories` is limited to
   the input, output, store and temp folders, and `enable_external_access` is off. An explicit `INSTALL`
   fails with `PermissionException` before touching disk or network.

The configuration is then locked, so a plugin cannot loosen it. Two layers of tests check this:

- `tests/test_offline.py` (`INSTALL` is refused, a file outside the case is refused, nothing appears in
  `~/.duckdb`);
- the CI job that runs the DuckDB engine inside `docker run --network none`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Out of Memory` from DuckDB | set `scale.memory_limit` (e.g. `"12GB"`) and a `temp_directory` on a disk with free space |
| the machine swaps during ingest | lower `scale.workers`, then `scale.batch_size` |
| `PermissionException: Cannot access file …` in your plugin | write inside the output folder, which is allowed; other paths are blocked while `restrict_files` is on |
| `RuntimeError … freeze_support` / spawn errors on Windows | add an `if __name__ == "__main__":` guard around your `run(...)` call |
| a log line says near-duplicate search was skipped (needs faiss) | `pip install faiss-cpu` (part of `.[scale]`) |
| communities differ slightly from the memory engine | NetworKit's parallel Louvain is used when installed. Clusters are seeded and stability-filtered, but tiny differences against NetworkX are expected. |
| the viewer misses some nodes | by design: it shows the case slice (`viewer.max_nodes`). Query `tgo.duckdb` or the Parquet files for the rest. |
