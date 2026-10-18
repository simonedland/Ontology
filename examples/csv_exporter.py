"""Example EXPORTER plugin: flat CSV files that open directly in Excel, Power BI or pandas.

Writes ``csv/nodes.csv`` and ``csv/edges.csv`` into the output folder. On the DuckDB engine it also
writes ``csv/edges_full.csv`` for the whole corpus straight from SQL. (The graph it receives there is
only the bounded case slice.)

    python -m tgo run --input case --out out/case --plugin examples.csv_exporter

To adapt: copy this file, rename the exporter, and change the columns marked ``TODO(adapt)``.
"""
from __future__ import annotations

import csv
from pathlib import Path

from tgo import ontology as O
from tgo.plugins import exporter

NAME = "csv"  # TODO(adapt): unique exporter name (also the key in summary.json["exports"])
NODE_COLS = ["key", "family", "label", "labels"]                       # TODO(adapt)
EDGE_COLS = ["src", "dst", "type", "status", "conf", "count", "t_first", "t_last", "method"]  # TODO(adapt)


def _label(n) -> str:
    p = n.props
    return str(next((p[k] for k in ("label", "title", "usernameValue", "value", "name") if p.get(k)), n.key))


@exporter(NAME, engines=("memory", "duckdb"))
def export_csv(g, out_dir, cfg, store=None) -> dict:
    out = Path(out_dir) / "csv"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "nodes.csv", "w", encoding="utf-8-sig", newline="") as f:   # utf-8-sig: Excel-friendly
        w = csv.writer(f)
        w.writerow(NODE_COLS)
        for n in sorted(g.nodes.values(), key=lambda n: n.key):
            w.writerow([n.key, O.family(n.labels) if n.labels else "other", _label(n), ";".join(sorted(n.labels))])
    with open(out / "edges.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(EDGE_COLS)
        for e in sorted(g.edges.values(), key=lambda e: e.id):
            w.writerow([e.src, e.dst, e.type] + [e.props.get(c, "") for c in EDGE_COLS[3:]])
    result = {"nodes": len(g.nodes), "edges": len(g.edges)}
    if store is not None:
        # Full corpus, streamed by DuckDB without loading it into Python. The output folder is one of the
        # store's allowed_directories, so this works even with scale.restrict_files on.
        target = (out / "edges_full.csv").resolve().as_posix()
        store.sql(f"COPY (SELECT {', '.join(EDGE_COLS)} FROM edges ORDER BY src, type, dst) "
                  f"TO '{target}' (HEADER, DELIMITER ',')")
        result["edges_full"] = store.sql("SELECT count(*) FROM edges").fetchone()[0]
    return result
