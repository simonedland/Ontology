"""Columnar corpus store (DuckDB) for millions of nodes / tens of millions of edges.

Same data model as :class:`tgo.graph.Graph` (deterministic string keys, edge
``status/conf/method/evidence/runs/scope/observed_by/t_first/t_last/count``,
per-field visibility), but held on disk in DuckDB and exported as Parquet.

Write path (research-backed: no per-row upserts at this scale).  Count rule: platform
observations sum across batches; edges written only inside DetectionRuns are derivations and
take max(count), so re-extracting a file seen in two sources is not a second observation.
    batch Graph --flush()--> append-only ``staging_*`` tables (Arrow bulk insert)
    finalize()  --> ``nodes`` / ``edges`` / ``fields`` rebuilt with one GROUP BY each
                    (CREATE OR REPLACE ... AS SELECT), reproducing Graph's MERGE semantics:
                    labels/sources/runs/evidence = set union (evidence capped), props =
                    JSON merge-patch in arrival order, conf = max, count = sum,
                    t_first = min, t_last = max, a *visible* field observation beats a hidden one.
Final tables also carry dense BIGINT ids (``nodes.id``, ``edges.src_id/dst_id``) for
graph algorithms and integer-id bulk imports.

The store is the system of record at scale; :meth:`subgraph` loads any bounded
slice back into an in-memory Graph for validation, judgements and the viewer.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..graph import Edge, Graph, Node

MAX_EVIDENCE = 50
_EDGE_COLS = ("status", "conf", "method", "count", "t_first", "t_last", "scope", "observed_by", "evidence", "runs")

SCHEMA = """
CREATE TABLE IF NOT EXISTS staging_nodes (key VARCHAR, labels VARCHAR[], props JSON, sources VARCHAR[], seq BIGINT,
    h UHUGEINT);
CREATE TABLE IF NOT EXISTS staging_fields (key VARCHAR, field VARCHAR, visibility VARCHAR, source VARCHAR,
                                           observed_by VARCHAR, seq BIGINT, h UHUGEINT);
CREATE TABLE IF NOT EXISTS staging_edges (id VARCHAR, type VARCHAR, src VARCHAR, dst VARCHAR, status VARCHAR,
    conf DOUBLE, method VARCHAR, count BIGINT, t_first BIGINT, t_last BIGINT, scope VARCHAR, observed_by VARCHAR,
    evidence VARCHAR[], runs VARCHAR[], props JSON, seq BIGINT, derived BOOLEAN, h UHUGEINT, hs UHUGEINT,
    hd UHUGEINT);
CREATE TABLE IF NOT EXISTS shares (object_id VARCHAR, kind VARCHAR, account VARCHAR, content VARCHAR, ts BIGINT);
CREATE TABLE IF NOT EXISTS retracted (kind VARCHAR, h UHUGEINT);
CREATE SEQUENCE IF NOT EXISTS flush_seq;
"""


_EDGE_SCHEMA = None
_PROVENANCE = {"ASSOCIATED_WITH", "GENERATED_BY", "USED"}


def to_arrow(g: Graph) -> dict:
    """Convert a batch Graph into staging-shaped Arrow tables (no ``seq``; picklable, worker-side)."""
    import pyarrow as pa

    global _EDGE_SCHEMA
    if _EDGE_SCHEMA is None:
        _EDGE_SCHEMA = pa.schema([("id", pa.string()), ("type", pa.string()), ("src", pa.string()),
                                  ("dst", pa.string()), ("status", pa.string()), ("conf", pa.float64()),
                                  ("method", pa.string()), ("count", pa.int64()), ("t_first", pa.int64()),
                                  ("t_last", pa.int64()), ("scope", pa.string()), ("observed_by", pa.string()),
                                  ("evidence", pa.list_(pa.string())), ("runs", pa.list_(pa.string())),
                                  ("props", pa.string()), ("derived", pa.bool_())])
    out: dict = {}
    if g.nodes:
        nodes = list(g.nodes.values())
        out["nodes"] = pa.table({
            "key": [n.key for n in nodes],
            "labels": pa.array([sorted(n.labels) for n in nodes], type=pa.list_(pa.string())),
            "props": [json.dumps(n.props, default=str, ensure_ascii=False) for n in nodes],
            "sources": pa.array([sorted(n.sources) for n in nodes], type=pa.list_(pa.string())),
        })
        rows = [(n.key, f, r.get("visibility"), r.get("source"), r.get("observed_by"))
                for n in nodes for f, r in n.fields.items()]
        if rows:
            out["fields"] = pa.table({"key": [r[0] for r in rows], "field": [r[1] for r in rows],
                                      "visibility": [r[2] for r in rows], "source": [r[3] for r in rows],
                                      "observed_by": pa.array([r[4] for r in rows], type=pa.string())})
    if g.edges:
        es = list(g.edges.values())
        # an edge written only inside DetectionRuns is a derivation: re-deriving it in another batch
        # (same file/message seen twice) is not a new observation -> merged with max(count), not sum
        detection_runs = {k for k, n in g.nodes.items() if "DetectionRun" in n.labels}
        cols = {c: [] for c in ("id", "type", "src", "dst", *_EDGE_COLS, "props", "derived")}
        for e in es:
            p = e.props
            cols["id"].append(e.id)
            cols["type"].append(e.type)
            cols["src"].append(e.src)
            cols["dst"].append(e.dst)
            cols["status"].append(p.get("status", "observed"))
            cols["conf"].append(float(p.get("conf", 1.0)))
            cols["method"].append(p.get("method"))
            cols["count"].append(int(p.get("count", 1)))
            cols["t_first"].append(p.get("t_first"))
            cols["t_last"].append(p.get("t_last"))
            cols["scope"].append(p.get("scope"))
            cols["observed_by"].append(p.get("observed_by"))
            cols["evidence"].append([str(x) for x in p.get("evidence", [])])
            runs = list(p.get("runs", []))
            cols["runs"].append(runs)
            cols["props"].append(json.dumps({k: v for k, v in p.items() if k not in _EDGE_COLS},
                                            default=str, ensure_ascii=False))
            # provenance edges (run->agent, cluster->run) are facts about the pipeline, not observations:
            # idempotent across batches like derivations
            cols["derived"].append(e.type in _PROVENANCE or (bool(runs) and all(r in detection_runs for r in runs)))
        out["edges"] = pa.table(cols, schema=_EDGE_SCHEMA)
    return out


class Store:
    """DuckDB corpus store: append-only staging + incremental merge into nodes/edges/fields."""
    def __init__(self, path: str | Path = ":memory:", *, threads: int | None = None,
                 memory_limit: str | None = None, temp_directory: str | None = None,
                 allowed_directories: list[str | Path] | None = None, restrict_files: bool = True):
        """Open (or create) a store.  ``allowed_directories`` are the only paths SQL may read/write
        (exports, plugin reads); everything else - other local files and all network access - is
        blocked and the configuration is locked."""
        import duckdb

        self.path = str(path)
        # Offline lockdown: no extension auto-install/auto-load (native downloads from
        # extensions.duckdb.org that Python-level guards cannot see), no unsigned/community
        # extensions; core/json/parquet/icu are statically linked in the wheel.
        import tempfile

        norepo = str((Path(self.path).resolve().parent if self.path != ":memory:" else Path(tempfile.gettempdir()))
                     / ".tgo-no-extension-repo")
        # TGO never needs DuckDB networking: a dead proxy + a nonexistent local repository make any
        # INSTALL/httpfs fail locally instead of downloading (layer 1); external access off (layer 2, below)
        cfg = {"autoinstall_known_extensions": False, "autoload_known_extensions": False,
               "allow_community_extensions": False, "allow_unsigned_extensions": False,
               "custom_extension_repository": norepo, "autoinstall_extension_repository": norepo,
               "http_proxy": "127.0.0.1:9", "preserve_insertion_order": False}
        if threads:
            cfg["threads"] = int(threads)
        if memory_limit:
            cfg["memory_limit"] = str(memory_limit)
        if temp_directory:
            cfg["temp_directory"] = str(temp_directory)
        self.con = duckdb.connect(self.path, config=cfg)
        self.con.execute("SET enable_progress_bar = false")   # session-level option
        dirs = [Path(d).resolve() for d in (allowed_directories or [])]
        if self.path != ":memory:":
            dirs.append(Path(self.path).resolve().parent)
        if temp_directory:
            dirs.append(Path(temp_directory).resolve())
        dirs = sorted({str(d).replace("\\", "/") for d in dirs})
        if restrict_files:   # layer 2: policy refusal (no network attempt) + local file sandbox
            if dirs:   # must precede enable_external_access=false
                self.con.execute("SET allowed_directories = ?", [dirs])
            self.con.execute("SET enable_external_access = false")   # no http/s3, no files outside allowed dirs
        self.con.execute("SET lock_configuration = true")         # plugins cannot undo any of this
        self.con.execute(SCHEMA)
        self.finalized = False

    # ------------------------------------------------------------------ write
    def _seq(self) -> int:
        return self.con.execute("SELECT nextval('flush_seq')").fetchone()[0]

    def flush(self, g: Graph) -> tuple[int, int]:
        """Append every node/edge/field of ``g`` to staging (one Arrow insert per table)."""
        return self.insert_arrow(to_arrow(g))

    def insert_arrow(self, tables: dict) -> tuple[int, int]:
        """Insert the output of :func:`to_arrow` (built in a worker process) under one new ``seq``."""
        seq = self._seq()
        n = e = 0
        nt, ft, et = tables.get("nodes"), tables.get("fields"), tables.get("edges")
        for name, tbl in (("nt", nt), ("ft", ft), ("et", et)):
            if tbl is not None:
                self.con.register(name, tbl)   # explicit: replacement scans are off (lockdown)
        if nt is not None and nt.num_rows:
            self.con.execute(f"INSERT INTO staging_nodes SELECT key, labels, props::JSON, sources, {seq}, "
                             f"md5_number(key) FROM nt")
            n = nt.num_rows
        if ft is not None and ft.num_rows:
            self.con.execute(f"INSERT INTO staging_fields SELECT key, field, visibility, source, observed_by, {seq}, "
                             f"md5_number(key) FROM ft")
        if et is not None and et.num_rows:
            self.con.execute(f"INSERT INTO staging_edges SELECT id, type, src, dst, status, conf, method, count, "
                             f"t_first, t_last, scope, observed_by, evidence, runs, props::JSON, {seq}, derived, "
                             f"md5_number(id), md5_number(src), md5_number(dst) FROM et")
            e = et.num_rows
        self.finalized = False
        return n, e

    def replace(self, g: Graph) -> tuple[int, int]:
        """Write ``g`` as the new merged state of its nodes/edges (for subgraphs loaded from this store).

        Staging rows of every node key / edge id present in ``g`` are deleted first, then ``g`` is
        flushed; since a loaded subgraph already carries the merged state, nothing is double-counted.
        """
        import pyarrow as pa

        kt = pa.table({"k": list(g.nodes)})
        self.con.register("kt", kt)   # explicit: replacement scans are off
        et = pa.table({"k": list(g.edges)})
        self.con.register("et", et)   # explicit: replacement scans are off
        self.con.execute("DELETE FROM staging_nodes WHERE key IN (SELECT k FROM kt)")
        self.con.execute("DELETE FROM staging_fields WHERE key IN (SELECT k FROM kt)")
        self.con.execute("DELETE FROM staging_edges WHERE id IN (SELECT k FROM et)")
        return self.flush(g)

    def retract_run(self, *run_keys: str) -> tuple[int, int]:
        """Remove everything produced *only* by ``run_keys`` so the analysis can be re-run cleanly.

        Re-running a deterministic analysis (live mode, new data) must not leave stale results
        (old clusters, candidates that no longer hold).  Rows that other runs also vouch for
        (e.g. a candidate an analyst decided on) are kept.  The next :meth:`finalize` drops or
        recomputes the affected final rows.
        """
        c = self.con
        keys = list(run_keys)
        only_e = "len(runs) > 0 AND list_has_all(?::VARCHAR[], runs)"          # every vouching run is retracted
        only_n = "len(sources) > 0 AND list_has_all(?::VARCHAR[], sources)"
        c.execute(f"INSERT INTO retracted SELECT 'e', h FROM staging_edges WHERE {only_e}", [keys])
        ne = c.execute(f"DELETE FROM staging_edges WHERE {only_e}", [keys]).fetchone()[0]
        c.execute(f"INSERT INTO retracted SELECT 'n', h FROM staging_nodes WHERE {only_n}", [keys])
        nn = c.execute(f"DELETE FROM staging_nodes WHERE {only_n}", [keys]).fetchone()[0]
        if ne or nn:
            self.finalized = False
        return nn, ne

    def add_shares(self, rows: Iterable[tuple]) -> int:
        """Append coordination share rows ``(object_id, kind, account, content, ts)``."""
        import pyarrow as pa

        rows = list(rows)
        if not rows:
            return 0
        t = pa.table({"object_id": [r[0] for r in rows], "kind": [r[1] for r in rows],
                      "account": [r[2] for r in rows], "content": [r[3] for r in rows],
                      "ts": [int(r[4]) for r in rows]})
        self.con.register("t", t)   # explicit: replacement scans are off
        self.con.execute("INSERT INTO shares SELECT * FROM t")
        return len(rows)

    # --------------------------------------------------------------- finalize
    # All grouping / joining is on 128-bit md5 hashes (``h``) of the string keys: fixed-width keys keep
    # hash tables and sorts small at tens of millions of rows (strings stay as payload only).
    _EDGE_AGG = f"""
        SELECT h, any_value(hs) AS hs, any_value(hd) AS hd,
               any_value(id) AS id, any_value(type) AS type, any_value(src) AS src, any_value(dst) AS dst,
               arg_min(status, seq) AS status, max(conf) AS conf, arg_max(method, seq) AS method,
               (CASE WHEN bool_and(derived) THEN max(count) ELSE sum(count) END)::BIGINT AS count,
               min(t_first) AS t_first, max(t_last) AS t_last,
               arg_max(scope, seq) FILTER (WHERE scope IS NOT NULL) AS scope,
               arg_max(observed_by, seq) FILTER (WHERE observed_by IS NOT NULL) AS observed_by,
               list_slice(list_distinct(flatten(list(evidence ORDER BY seq))), 1, {MAX_EVIDENCE}) AS evidence,
               list_distinct(flatten(list(runs))) AS runs,
               list_reduce(list(props ORDER BY seq), (a, b) -> json_merge_patch(a, b)) AS props
        FROM s SEMI JOIN multi USING (h) GROUP BY h"""
    _EDGE_ONE = f"""
        SELECT h, hs, hd, id, type, src, dst, status, conf, method, count, t_first, t_last, scope, observed_by,
               evidence, runs, props
        FROM s ANTI JOIN multi USING (h)"""   # single observation: Graph already deduped/capped its lists
    # whole record chosen together (arg_max skips NULLs, so per-column picks could mix observations)
    _FIELDS = """SELECT key, field, r.v AS visibility, r.s AS source, r.o AS observed_by, h FROM (
            SELECT h, any_value(key) AS key, field, arg_max({'v': visibility, 's': source, 'o': observed_by},
                                       (visibility = 'visible')::INT * 1e12 - seq) AS r
            FROM staging_fields SEMI JOIN aff_f USING (h) GROUP BY h, field)"""

    def _watermark(self) -> int:
        self.con.execute("CREATE TABLE IF NOT EXISTS tgo_meta (k VARCHAR PRIMARY KEY, v BIGINT)")
        r = self.con.execute("SELECT v FROM tgo_meta WHERE k = 'finalized_seq'").fetchone()
        return r[0] if r else -1

    def finalize(self) -> dict:
        """Merge staging into ``nodes``/``edges``/``fields`` with Graph-equivalent MERGE semantics.

        Incremental: only ids/keys touched since the last finalize (seq watermark) are recomputed,
        from *all* their staging rows; node ids stay stable; ids seen exactly once skip the
        aggregate (fast path); the first (full) pass reads staging in place without a copy.
        """
        c = self.con
        wm = self._watermark()
        top = c.execute("""SELECT coalesce(max(m), -1) FROM (SELECT max(seq) AS m FROM staging_edges
                           UNION ALL SELECT max(seq) FROM staging_nodes UNION ALL SELECT max(seq) FROM staging_fields)
                        """).fetchone()[0]
        exists = c.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'nodes'").fetchone()[0]
        full = not exists or wm < 0
        if full:
            c.execute("CREATE OR REPLACE TEMP VIEW s AS SELECT * FROM staging_edges")
            c.execute("CREATE OR REPLACE TEMP VIEW sn AS SELECT * FROM staging_nodes")
            c.execute("CREATE OR REPLACE TEMP TABLE aff_f AS SELECT DISTINCT h FROM staging_fields")
        else:
            c.execute(f"""CREATE OR REPLACE TEMP TABLE aff_e AS SELECT DISTINCT h FROM (
                          SELECT h FROM staging_edges WHERE seq > {wm}
                          UNION ALL SELECT h FROM retracted WHERE kind = 'e')""")
            c.execute(f"CREATE OR REPLACE TEMP TABLE aff_f AS SELECT DISTINCT h FROM staging_fields WHERE seq > {wm}")
            c.execute("CREATE OR REPLACE TEMP TABLE s AS SELECT * FROM staging_edges SEMI JOIN aff_e USING (h)")
        # ---- edges (merged; dense node ids added below)
        c.execute("CREATE OR REPLACE TEMP TABLE multi AS SELECT h FROM s GROUP BY h HAVING count(*) > 1")
        c.execute(f"CREATE OR REPLACE TEMP TABLE e_new AS {self._EDGE_ONE} UNION ALL {self._EDGE_AGG}")
        # ---- nodes: new node rows + endpoints of affected edges
        newer = "" if full else f"WHERE seq > {wm}"
        # incremental: only nodes with new staging rows, or endpoints not yet in ``nodes``, need work;
        # existing endpoints of new edges (e.g. 1M accounts behind POSTED_IN) keep their merged row/id
        known = "" if full else "ANTI JOIN nodes USING (h)"
        retracted_nodes = "" if full else             "UNION ALL SELECT r.h, n.key FROM retracted r JOIN nodes n USING (h) WHERE r.kind = 'n'"
        c.execute(f"""CREATE OR REPLACE TEMP TABLE aff_n AS
                      SELECT h, any_value(key) AS key FROM (
                          SELECT h, key FROM staging_nodes {newer}
                          {retracted_nodes}
                          UNION ALL SELECT * FROM (SELECT hs AS h, src AS key FROM e_new UNION ALL
                                                   SELECT hd, dst FROM e_new) {known}) GROUP BY h""")
        if not full:
            c.execute("CREATE OR REPLACE TEMP TABLE sn AS SELECT * FROM staging_nodes SEMI JOIN aff_n USING (h)")
        c.execute("CREATE OR REPLACE TEMP TABLE nmulti AS SELECT h FROM sn GROUP BY h HAVING count(*) > 1")
        c.execute("""CREATE OR REPLACE TEMP TABLE n_new AS
            SELECT h, list_distinct(labels) AS labels, props, list_distinct(sources) AS sources
            FROM sn ANTI JOIN nmulti USING (h)
            UNION ALL
            SELECT h, list_distinct(flatten(list(labels))), list_reduce(list(props ORDER BY seq),
                   (a, b) -> json_merge_patch(a, b)), list_distinct(flatten(list(sources)))
            FROM sn SEMI JOIN nmulti USING (h) GROUP BY h""")
        cols = """k.key, coalesce(n.labels, []::VARCHAR[]) AS labels, coalesce(n.props, '{}'::JSON) AS props,
                  coalesce(n.sources, []::VARCHAR[]) AS sources, k.h"""
        if full:
            c.execute(f"""CREATE OR REPLACE TABLE nodes AS
                SELECT (row_number() OVER (ORDER BY k.h) - 1)::BIGINT AS id, {cols}
                FROM aff_n k LEFT JOIN n_new n USING (h)""")
        else:
            base = c.execute("SELECT coalesce(max(id), -1) FROM nodes").fetchone()[0]
            c.execute("CREATE OR REPLACE TEMP TABLE n_old AS SELECT id, h FROM nodes SEMI JOIN aff_n USING (h)")
            c.execute("DELETE FROM nodes WHERE h IN (SELECT h FROM aff_n)")
            c.execute(f"""INSERT INTO nodes
                SELECT coalesce(o.id, {base} + row_number() OVER (PARTITION BY o.id IS NULL ORDER BY k.h)), {cols}
                FROM aff_n k LEFT JOIN n_new n USING (h) LEFT JOIN n_old o USING (h)""")
        # ---- edges with dense ids
        sel = """SELECT e.* EXCLUDE (hs, hd), sn_.id AS src_id, dn_.id AS dst_id
                 FROM e_new e JOIN nodes sn_ ON sn_.h = e.hs JOIN nodes dn_ ON dn_.h = e.hd"""
        if full:
            c.execute(f"CREATE OR REPLACE TABLE edges AS {sel}")
        else:
            c.execute("DELETE FROM edges WHERE h IN (SELECT h FROM aff_e)")
            c.execute(f"INSERT INTO edges {sel}")
        # ---- field visibility: a visible observation beats a hidden one, else the first seen
        if full:
            c.execute(f"CREATE OR REPLACE TABLE fields AS {self._FIELDS}")
        else:
            c.execute("DELETE FROM fields WHERE h IN (SELECT h FROM aff_f)")
            c.execute(f"INSERT INTO fields {self._FIELDS}")
        if not full:   # nodes left with no staging rows and no incident edge are gone (retracted)
            c.execute("""DELETE FROM nodes WHERE h IN (SELECT h FROM retracted WHERE kind = 'n')
                         AND NOT EXISTS (SELECT 1 FROM staging_nodes sn_ WHERE sn_.h = nodes.h)
                         AND NOT EXISTS (SELECT 1 FROM edges e_ WHERE e_.src_id = nodes.id)
                         AND NOT EXISTS (SELECT 1 FROM edges e_ WHERE e_.dst_id = nodes.id)""")
        c.execute("DELETE FROM retracted")
        c.execute("INSERT OR REPLACE INTO tgo_meta VALUES ('finalized_seq', ?)", [top])
        views = {"s", "sn"} if full else set()
        for t in ("s", "multi", "e_new", "sn", "nmulti", "n_new", "aff_e", "aff_n", "aff_f", "n_old"):
            c.execute(f"DROP {'VIEW' if t in views else 'TABLE'} IF EXISTS {t}")
        self.finalized = True
        return self.stats()

    # ------------------------------------------------------------------- read
    def sql(self, q: str, params=None):
        """Execute SQL on the store connection (read helpers / ad-hoc analysis)."""
        return self.con.execute(q, params or [])

    def stats(self) -> dict:
        """Row counts (and per-type/status edge counts once finalized)."""
        c = self.con
        if not self.finalized:
            return {"staging_nodes": c.execute("SELECT count(*) FROM staging_nodes").fetchone()[0],
                    "staging_edges": c.execute("SELECT count(*) FROM staging_edges").fetchone()[0]}
        return {
            "nodes": c.execute("SELECT count(*) FROM nodes").fetchone()[0],
            "edges": c.execute("SELECT count(*) FROM edges").fetchone()[0],
            "shares": c.execute("SELECT count(*) FROM shares").fetchone()[0],
            "edge_types": dict(c.execute("SELECT type, count(*) FROM edges GROUP BY 1 ORDER BY 2 DESC").fetchall()),
            "edge_status": dict(c.execute("SELECT status, count(*) FROM edges GROUP BY 1").fetchall()),
        }

    def subgraph(self, keys: Iterable[str] | None = None, *, include_edges_between: bool = True) -> Graph:
        """Load nodes (all if ``keys`` is None) and the edges among them into an in-memory Graph."""
        import pyarrow as pa

        g = Graph()
        c = self.con
        if keys is not None:
            kt = pa.table({"key": sorted(set(keys))})
            self.con.register("kt", kt)   # explicit: replacement scans are off
            c.execute("CREATE OR REPLACE TEMP TABLE _sel AS SELECT key FROM kt")
            nq = "SELECT n.key, n.labels, n.props, n.sources FROM nodes n JOIN _sel USING (key)"
            eq = ("SELECT e.id, e.type, e.src, e.dst, e.status, e.conf, e.method, e.count, e.t_first, e.t_last, "
                  "e.scope, e.observed_by, e.evidence, e.runs, e.props FROM edges e "
                  "JOIN _sel a ON a.key = e.src JOIN _sel b ON b.key = e.dst")
            fq = "SELECT f.key, f.field, f.visibility, f.source, f.observed_by FROM fields f JOIN _sel USING (key)"
        else:
            nq = "SELECT key, labels, props, sources FROM nodes"
            eq = ("SELECT id, type, src, dst, status, conf, method, count, t_first, t_last, scope, observed_by, "
                  "evidence, runs, props FROM edges")
            fq = "SELECT key, field, visibility, source, observed_by FROM fields"
        for key, labels, props, sources in c.execute(nq).fetchall():
            g.add_raw_node(Node(key, set(labels or []), json.loads(props) if props else {}, set(sources or [])))
        for key, field, vis, src, ob in c.execute(fq).fetchall():
            if key in g.nodes:
                rec = {"visibility": vis, "source": src}
                if ob:
                    rec["observed_by"] = ob
                g.nodes[key].fields[field] = rec
        if include_edges_between:
            for (eid, typ, s, d, status, conf, method, count, t1, t2, scope, ob, ev, runs, props) in \
                    c.execute(eq).fetchall():
                p = json.loads(props) if props else {}
                p.update(status=status, conf=conf, method=method, count=count, evidence=list(ev or []),
                         runs=list(runs or []))
                for k, v in (("t_first", t1), ("t_last", t2), ("scope", scope), ("observed_by", ob)):
                    if v is not None:
                        p[k] = v
                g.add_raw_edge(Edge(eid, typ, s, d, p))
        return g

    def close(self) -> None:
        """Close the DuckDB connection."""
        self.con.close()
