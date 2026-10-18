"""Example ANALYSIS STEP plugin (DuckDB engine): the amplifier score in SQL, over the whole corpus.

This is the scale-out twin of ``examples/amplifier_step.py``, for runs of millions of messages
(``--engine duckdb``). A DuckDB step receives the ``Store`` instead of a ``Graph``. It reads with
SQL, then writes its results back through a small Graph that ``store.flush()`` merges, so merge
semantics and provenance stay identical to the memory engine.

    python -m tgo run --input big_case --out out/big --engine duckdb --plugin examples.amplifier_sql_step

Tables you can query once the store is finalized: ``nodes(key, labels, props JSON, …)``,
``edges(type, src, dst, status, conf, count, t_first, t_last, evidence[], runs[], props JSON, …)``
and ``fields``. See docs/scaling.md.
"""
from __future__ import annotations

from tgo.graph import Graph
from tgo.plugins import step

NAME = "amplifiers-sql"  # TODO(adapt)


@step(NAME, after="aggregate", engines=("duckdb",))
def score_amplifiers_sql(store, cfg) -> dict:
    opts = (getattr(getattr(cfg, "plugins", None), "options", None) or {}).get(NAME, {})
    min_sources = int(opts.get("min_sources", 2))
    rows = store.sql("""
        SELECT dst, count(DISTINCT src) AS sources, sum(count) AS total
        FROM edges
        WHERE type = 'FORWARDS_FROM' AND src <> dst
        GROUP BY dst HAVING count(DISTINCT src) >= ?
        ORDER BY sources DESC, total DESC, dst
    """, [min_sources]).fetchall()

    g = Graph()  # a scratch graph holding just the updates; flush() merges it into the store
    with g.activity("DetectionRun", f"{NAME}:min{min_sources}", agent={
            "tool": f"tgo-example-{NAME}", "version": "0.1", "codeLicense": "MIT", "weightsLicense": "n/a"},
            min_sources=min_sources):
        for rank, (key, sources, total) in enumerate(rows, 1):
            g.node(key, amplifiedBy=int(sources), forwardedCount=int(total), amplifierRank=rank)
    store.flush(g)  # the pipeline finalizes after DuckDB plugin steps
    return {"scored": len(rows), "top": rows[0][0] if rows else None}
