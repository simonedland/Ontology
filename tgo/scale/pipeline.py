"""The DuckDB engine: the same pipeline as :mod:`tgo.pipeline`, for millions of entries.

    ingest (streamed batches, optional process pool) -> finalize
    -> aggregate + coordination + perceptual near-dups (SQL / Faiss) -> finalize
    -> communities (NetworKit) + identity (SQL + nomenklatura judgements) -> finalize
    -> SQL guardrails -> bulk exports (Parquet, Neo4j bulk CSV, N-Triples)
    -> bounded "case" subgraph -> in-memory guardrails, FtM/STIX/GEXF, report.html

Outputs in ``out_dir``: ``tgo.duckdb`` (the corpus store), ``parquet/``, ``neo4j/``,
``tgo.nt.gz``, ``ontology.ttl``, and for the case subgraph ``case_graph.json``,
``ftm.jsonl``, ``stix.json``, ``graph.gexf``, ``report.html``; plus
``validation.json`` and ``summary.json``.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path

from .. import extractors, validate
from .. import export as X
from .. import ontology as O
from ..config import Config
from ..graph import Graph
from ..validate import Finding
from . import analytics as A
from . import exports as E
from .store import Store
from .stream import ingest_dir


# ------------------------------------------------------------------ guardrails
def validate_store(store, examples: int = 5) -> list[Finding]:
    """Corpus-wide versions of the TGO guardrails, aggregated (one finding per rule, with examples)."""
    c = store.con
    F: list[Finding] = []

    def rule(name, sev, sql, msg):
        n, ex = c.execute(f"SELECT count(*), list_slice(list(x), 1, {examples}) FROM ({sql})").fetchone()
        if n:
            F.append(Finding(name, sev, f"{n} items", f"{msg}; e.g. {', '.join(map(str, ex))}"))

    forb_r = ",".join(f"'{t}'" for t in O.FORBIDDEN_RELATIONS)
    forb_l = "[" + ",".join(f"'{t}'" for t in O.FORBIDDEN_LABELS) + "]"
    special = ",".join(f"'{t}'" for t in O.SPECIAL_CATEGORY_TOPICS)
    rule("R1-derived-provenance", "violation", """SELECT id AS x FROM edges WHERE status IN ('inferred','file_declared')
         AND (len(evidence) = 0 OR len(runs) = 0 OR method IS NULL OR method = 'unspecified')""",
         "derived edges without evidence / run / method")
    rule("R1-derived-provenance", "violation", "SELECT id AS x FROM edges WHERE status = 'inferred' AND conf >= 1.0",
         "inferred edges with confidence 1.0")
    rule("R2-no-sameas-no-biometrics", "violation", f"SELECT id AS x FROM edges WHERE type IN ({forb_r})",
         "forbidden relationship types")
    rule("R2-no-sameas-no-biometrics", "violation",
         f"SELECT key AS x FROM nodes WHERE len(list_intersect(labels, {forb_l})) > 0", "forbidden labels")
    rule("R4-stable-keys", "violation",
         "SELECT key AS x FROM nodes WHERE regexp_matches(key, 'file_id|access_hash|file_reference')",
         "nodes keyed on volatile Telegram handles")
    rule("R5-photo-mode-metadata", "violation", """SELECT e.id AS x FROM edges e JOIN nodes n ON n.key = e.src
         WHERE e.type IN ('CAPTURED_WITH','HAS_EXIF_GPS') AND (list_contains(n.labels, 'Photo')
         OR json_extract_string(n.props, '$.sendMode') = 'PhotoMode')""", "EXIF facts on server re-encoded photos")
    rule("R6-art9-membership", "warning", f"""SELECT e.id AS x FROM edges e JOIN nodes n ON n.key = e.dst
         WHERE e.type IN ('MEMBER_OF','ADMIN_OF','POSTED_IN') AND json_extract_string(n.props, '$.topic') IN ({special})""",
         "memberships in special-category chats (Art. 9: needs 9(2)(j) basis, DPIA, RBAC)")
    rule("R7-admin-only-signals", "info", "SELECT id AS x FROM edges WHERE scope IN ('admins','owner_only')",
         "admin/owner-only observations reveal non-posting audience members")
    rule("R10-viewer-relative", "violation",
         "SELECT id AS x FROM edges WHERE scope = 'collector_only' AND observed_by IS NULL",
         "viewer-relative facts without observed_by")
    for k, n in c.execute("""SELECT field || ':' || visibility, count(*) FROM fields WHERE visibility <> 'visible'
                             GROUP BY 1 ORDER BY 1""").fetchall():
        F.append(Finding("typed-gap", "info", k, f"{n} occurrences (absence is data)"))
    for k, n in c.execute("""SELECT 'file:' || json_extract_string(props, '$.fileStatus'), count(*) FROM nodes
                             WHERE json_extract_string(props, '$.fileStatus') IS NOT NULL GROUP BY 1""").fetchall():
        F.append(Finding("typed-gap", "info", k, f"{n} occurrences (absence is data)"))
    return F


# ------------------------------------------------------------------ case graph
def case_keys(store, max_nodes: int = 6000) -> list[str]:
    """Bounded slice for the viewer: derived/identity structure first, then their evidence, then top actors."""
    c = store.con
    c.execute(f"""
    CREATE OR REPLACE TEMP TABLE case_rank AS
    WITH derived AS (SELECT src, dst, evidence FROM edges WHERE status <> 'observed' OR type IN
                     ('FORWARDS_FROM','MEMBER_OF_COMMUNITY','FEATURES','GAVE_GIFT_TO','REPOSTED','KNOWS')),
    deg AS (SELECT k, count(*) AS d FROM (SELECT src AS k FROM edges UNION ALL SELECT dst FROM edges) GROUP BY k),
    cand AS (
        SELECT key AS k, 0 AS pri FROM nodes WHERE len(list_intersect(labels,
            ['InferredGroup','PersonCluster','IdentityJudgement','Community','CollectionRun','DetectionRun','ModelAgent'])) > 0
        UNION ALL SELECT src, 1 FROM derived UNION ALL SELECT dst, 1 FROM derived
        UNION ALL SELECT unnest(list_slice(evidence, 1, 5)), 2 FROM derived
        UNION ALL SELECT key, 3 FROM nodes WHERE len(list_intersect(labels, ['Account','Chat'])) > 0)
    SELECT cand.k, min(pri) AS pri, coalesce(max(deg.d), 0) AS d FROM cand
    JOIN nodes ON nodes.key = cand.k LEFT JOIN deg ON deg.k = cand.k GROUP BY cand.k""")
    return [k for (k,) in c.execute(f"SELECT k FROM case_rank ORDER BY pri, d DESC, k LIMIT {int(max_nodes)}").fetchall()]


# ------------------------------------------------------------------ main
def run(input_dir: str | Path, out_dir: str | Path, config: Config | None = None, *,
        auto_accept: float | None = None, time_window: int | None = None, html: bool | None = None,
        log=print) -> dict:
    """DuckDB-engine pipeline; same signature and summary contract as :func:`tgo.pipeline.run`."""
    cfg = copy.deepcopy(config) if config is not None else Config()
    if auto_accept is not None:
        cfg.identity.auto_accept = auto_accept
    if time_window is not None:
        cfg.coordination.time_window = time_window
    if html is not None:
        cfg.viewer.html = html
    sc = cfg.scale
    try:
        import duckdb  # noqa: F401
        import pyarrow  # noqa: F401
    except ImportError as e:
        raise SystemExit(f"the duckdb engine needs the scale extra: pip install 'tgo[scale]' ({e.name} missing)") from e
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    db = Path(sc.store_path) if sc.store_path else out / "tgo.duckdb"
    for p in (db, Path(str(db) + ".wal")):
        if p.exists():
            p.unlink()
    store = Store(db, threads=sc.threads, memory_limit=sc.memory_limit, temp_directory=sc.temp_directory,
                  allowed_directories=[out, Path(input_dir)], restrict_files=sc.restrict_files)
    timings: dict[str, float] = {}
    report: dict = {"engine": "duckdb", "store": str(db)}

    def step(name, fn, *a, **kw):
        t0 = time.perf_counter()
        r = fn(*a, **kw)
        timings[name] = round(time.perf_counter() - t0, 2)
        shown = r if isinstance(r, dict) else (f"{len(r):,} items" if isinstance(r, (list, set)) else type(r).__name__)
        text = str(shown)
        log(f"  [{timings[name]:7.2f}s] {name}: {text if len(text) < 400 else text[:400] + ' ...'}")
        return r

    log(f"TGO scale pipeline (DuckDB): {input_dir} -> {out}")
    ext_opts = {"gazetteer": extractors.load_gazetteer(cfg.extract.gazetteer_path),
                "phash_threshold": cfg.extract.phash_threshold}
    from .. import plugins

    report["plugins"] = plugins.load(cfg.plugins.modules, cfg.plugins.entry_points)
    report["ingest"] = step("ingest+extract (streamed)", ingest_dir, store, Path(input_dir),
                            batch_size=sc.batch_size, workers=sc.workers, ext_opts=ext_opts,
                            min_text_len=cfg.coordination.min_text_len, log=log)
    for name, src in plugins.REGISTRY.sources.items():          # plugin sources: one batch per file
        for path in src.discover(Path(input_dir)):
            def _one(src=src, path=path):
                from ..analysis.coordination import build_shares

                g = Graph()
                src.ingest(g, Path(path))
                extractors.run_all(g, Path("."), **ext_opts)
                store.add_shares((s.object_id, s.kind, s.account_id, s.content_id, s.ts)
                                 for s in build_shares(g, cfg.coordination.min_text_len))
                return store.flush(g)
            step(f"ingest {name} {Path(path).name}", _one)
    step("finalize #1", store.finalize)
    report["aggregate"] = step("aggregate (SQL)", A.aggregate, store)
    report["coordination"] = step("coordination (SQL)", A.coordination, store, **vars(cfg.coordination),
                                  max_object_accounts=sc.max_object_accounts, log=log)
    report["near_duplicates"] = step("near-duplicates", A.near_duplicates, store,
                                     threshold=cfg.extract.phash_threshold, k=sc.neardup_k,
                                     exact_limit=sc.neardup_exact_limit, log=log)
    step("finalize #2", store.finalize)
    c = cfg.communities
    report["communities"] = step("communities", A.communities, store, seeds=c.seeds, resolution=c.resolution,
                                 min_size=c.min_size, min_stability=c.min_stability, edge_weights=c.edge_weights,
                                 deterministic=c.deterministic, log=log)
    jpath = Path(input_dir) / "analyst" / "judgements.json"
    decisions = json.loads(jpath.read_text(encoding="utf-8")) if jpath.exists() else None
    i = cfg.identity
    report["identity"] = step("identity (SQL)", A.identity, store, decisions, min_score=i.min_score,
                              auto_accept=i.auto_accept, weights=i.weights, strong_identifiers=i.strong_identifiers,
                              min_bio_len=i.min_bio_len, max_evidence_accounts=sc.max_evidence_accounts, log=log)
    for s in [s for a in plugins.BUILTIN_STEPS for s in plugins.steps_after(a, "duckdb")] + \
            plugins.orphan_steps("duckdb"):
        report[s.name] = step(s.name, s.fn, store, cfg)
    report["stats"] = step("finalize #3", store.finalize)

    findings = step("guardrails (SQL)", validate_store, store)
    report["exports"] = {}
    if "parquet" in sc.exports:
        report["exports"]["parquet"] = step("export parquet", E.parquet, store, out / "parquet")
    if "neo4j" in sc.exports:
        report["exports"]["neo4j"] = step("export neo4j bulk", E.neo4j_bulk, store, out / "neo4j")
    if "ntriples" in sc.exports:
        report["exports"]["ntriples"] = step("export n-triples", E.ntriples, store, out / "tgo.nt.gz")
    X.write_tbox(out / "ontology.ttl")

    keys = step("case selection", case_keys, store, cfg.viewer.max_nodes)
    case: Graph = step("case load", lambda: store.subgraph(keys))
    case_findings = [f for f in validate.validate(case)
                     if f.rule in ("R3-person-needs-judgement", "R8-judgement-accountability", "R9-sticker-ownership")]
    findings = findings + case_findings
    case.save(out / "case_graph.json")
    report["exports"]["case"] = {"nodes": len(case.nodes), "edges": len(case.edges),
                                 "ftm.jsonl": X.write_ftm(case, out / "ftm.jsonl"),
                                 "stix.json": X.write_stix(case, out / "stix.json")}
    memory_only = [s for a in plugins.BUILTIN_STEPS for s in plugins.steps_after(a, "memory")
                   if "duckdb" not in s.engines] + [s for s in plugins.orphan_steps("memory") if "duckdb" not in s.engines]
    for s in memory_only:                     # memory-only plugin steps see the bounded case subgraph
        report[s.name] = step(f"{s.name} (case slice)", s.fn, case, cfg)
    for name, ex in plugins.REGISTRY.exporters.items():
        report["exports"][name] = ex.fn(case, out, cfg, store=store) if "duckdb" in ex.engines else \
            ex.fn(case, out, cfg)
    from ..pipeline import layout

    pos = layout(case, seed=cfg.viewer.layout_seed, max_full=cfg.viewer.layout_max_full)
    X.write_gexf(case, out / "graph.gexf", positions=pos)
    report["validation"] = validate.summary(findings)
    (out / "validation.json").write_text(json.dumps(validate.to_dicts(findings), indent=1), encoding="utf-8")
    report["timings"] = timings
    report["config"] = cfg.to_dict()
    if cfg.viewer.html:
        from ..viz.report import build_report

        view_report = dict(report)
        view_report["stats"] = case.stats()   # tiles describe what the viewer shows; corpus stats in summary
        view_report["corpus"] = report["stats"]
        build_report(case, findings, view_report, pos, out / "report.html",
                     title=cfg.viewer.title + " (case slice of corpus)", max_nodes=cfg.viewer.max_nodes)
    (out / "summary.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    st = report["stats"]
    v = report["validation"]
    log(f"done: corpus {st['nodes']:,} nodes / {st['edges']:,} edges; case slice {len(case.nodes):,} nodes; "
        f"validation {v['violation']} violations / {v['warning']} warnings / {v['info']} info")
    store.close()
    if cfg.viewer.dashboard:   # opens the store itself, so only after it is closed
        from ..dashboard import build_dashboard

        build_dashboard(out, max_nodes=cfg.viewer.dashboard_max_nodes)
        report["exports"]["dashboard.html"] = "dashboard"
        (out / "summary.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report
