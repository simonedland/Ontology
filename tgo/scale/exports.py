"""Bulk exports straight from the store (DuckDB ``COPY``; nothing is materialised in Python).

* ``parquet/``   nodes.parquet, fields.parquet, edges/ partitioned by ``type`` (Hive layout) -
                 the durable interchange format (Fabric/OneLake, Spark, Polars, DuckDB, LadybugDB).
* ``neo4j/``     ``neo4j-admin database import full`` CSVs with integer ids (nodes.csv,
                 relationships.csv) + import.cmd/import.sh.  Bulk import is orders of
                 magnitude faster than Cypher MERGE at millions of rows.
* ``tgo.nt.gz``  gzipped N-Triples (line-oriented, splittable, concatenable): rdf:type per
                 label, one triple per observed edge, and a reified tg:Assertion (confidence,
                 status, method, run) for every non-observed edge.  Never owl:sameAs.
"""
from __future__ import annotations

from pathlib import Path

from .. import ontology as O


def _p(path: Path) -> str:
    return str(path).replace("\\", "/").replace("'", "''")


def parquet(store, out: Path) -> dict:
    """COPY nodes/fields/edges to (partitioned) Parquet."""
    out.mkdir(parents=True, exist_ok=True)
    c = store.con
    c.execute(f"COPY nodes TO '{_p(out / 'nodes.parquet')}' (FORMAT parquet, COMPRESSION zstd)")
    c.execute(f"COPY fields TO '{_p(out / 'fields.parquet')}' (FORMAT parquet, COMPRESSION zstd)")
    c.execute(f"COPY edges TO '{_p(out / 'edges')}' (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (type), "
              f"OVERWRITE_OR_IGNORE true)")
    return {"dir": str(out)}


def neo4j_bulk(store, out: Path) -> dict:
    """COPY to ``neo4j-admin database import`` CSVs with integer ids, plus the import command."""
    out.mkdir(parents=True, exist_ok=True)
    c = store.con
    c.execute(f"""COPY (SELECT id AS "id:ID", key, array_to_string(list_append(labels, 'TGO'), ';') AS ":LABEL",
                         coalesce(json_extract_string(props, '$.label'), json_extract_string(props, '$.title'),
                                  json_extract_string(props, '$.collectorDisplayName'), key) AS name,
                         props::VARCHAR AS props FROM nodes)
                  TO '{_p(out / 'nodes.csv')}' (HEADER true)""")
    c.execute(f"""COPY (SELECT src_id AS ":START_ID", dst_id AS ":END_ID", type AS ":TYPE", id AS edge_key, status,
                         conf AS "conf:double", method, count AS "count:long", t_first AS "t_first:long",
                         t_last AS "t_last:long", scope, observed_by,
                         array_to_string(evidence, ';') AS "evidence:string[]",
                         array_to_string(runs, ';') AS "runs:string[]", props::VARCHAR AS props FROM edges)
                  TO '{_p(out / 'relationships.csv')}' (HEADER true)""")
    cmd = ("neo4j-admin database import full tgo --overwrite-destination --id-type=integer "
           "--array-delimiter=\";\" --nodes=nodes.csv --relationships=relationships.csv")
    (out / "import.sh").write_text("#!/bin/sh\n# run from this directory with Neo4j stopped\n" + cmd + "\n",
                                   encoding="utf-8")
    (out / "import.cmd").write_text("@rem run from this directory with Neo4j stopped\r\n" + cmd + "\r\n",
                                    encoding="utf-8")
    return {"dir": str(out), "command": cmd}


def ntriples(store, path: Path) -> dict:
    """COPY the graph as gzipped N-Triples (types, observed triples, reified derived edges)."""
    tg, tgr = O.TG, O.TGR
    c = store.con
    iri = f"'<{tgr}' || url_encode(%s) || '>'"
    import pyarrow as pa

    from ..export.turtle import camel

    types = [t for (t,) in c.execute("SELECT DISTINCT type FROM edges").fetchall()]
    pm = pa.table({"type": types, "pred": [f"<{tg}{camel(t)}>" for t in types]})
    c.register("pm_arrow", pm)   # explicit: replacement scans are off
    c.execute("CREATE OR REPLACE TEMP TABLE predmap AS SELECT * FROM pm_arrow")
    pred = "(SELECT pred FROM predmap p WHERE p.type = edges.type)"
    lit = "'\"' || replace(replace(replace(CAST(%s AS VARCHAR), '\\\\', '\\\\\\\\'), '\"', '\\\\\"'), chr(10), '\\\\n') || '\"'"
    a = f"'<{tgr}assertion/' || md5(id) || '>'"
    q = f"""
    SELECT {iri % 'key'} || ' <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <{tg}' || lab || '> .' AS l
        FROM (SELECT key, unnest(labels) AS lab FROM nodes)
    UNION ALL
    SELECT {iri % 'src'} || ' ' || {pred} || ' ' || {iri % 'dst'} || ' .' FROM edges WHERE status = 'observed'
    UNION ALL
    SELECT unnest([
        {a} || ' <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <{tg}Assertion> .',
        {a} || ' <http://www.w3.org/1999/02/22-rdf-syntax-ns#subject> ' || {iri % 'src'} || ' .',
        {a} || ' <http://www.w3.org/1999/02/22-rdf-syntax-ns#predicate> ' || {pred} || ' .',
        {a} || ' <http://www.w3.org/1999/02/22-rdf-syntax-ns#object> ' || {iri % 'dst'} || ' .',
        {a} || ' <{tg}confidence> ' || {lit % 'conf'} || '^^<http://www.w3.org/2001/XMLSchema#double> .',
        {a} || ' <{tg}epistemicStatus> ' || {lit % 'status'} || ' .',
        {a} || ' <{tg}method> ' || {lit % "coalesce(method, '')"} || ' .',
        {a} || ' <http://www.w3.org/ns/prov#wasGeneratedBy> ' || {iri % "coalesce(runs[1], 'run:unknown')"} || ' .'])
        FROM edges WHERE status <> 'observed'
    """
    c.execute(f"COPY ({q}) TO '{_p(path)}' (FORMAT csv, HEADER false, QUOTE '', ESCAPE '', DELIMITER '\t', "
              f"COMPRESSION gzip)")
    return {"file": str(path)}
