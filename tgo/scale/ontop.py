"""Virtual knowledge graph: SPARQL over the DuckDB store with Ontop (no triple materialisation).

Research (docs/scaling.md): Ontop (Apache-2.0) supports DuckDB and has been used for SPARQL
over ~1B rows; it rewrites SPARQL to SQL against ``tgo.duckdb`` using the mapping written here.
The mapping is generated from :mod:`tgo.ontology`, so it always matches the vocabulary:

* every node -> ``tgr:{key} a tg:<Label>`` (+ ``rdfs:label``)
* every observed edge -> ``tgr:{src} tg:<relation> tgr:{dst}``
* every edge -> an n-ary ``tg:Assertion`` (rdf:subject/object, tg:relationType, tg:confidence,
  tg:epistemicStatus, tg:method, tg:eventCount) - the provenance pattern that works in every
  store today, independent of RDF 1.2 triple terms (still a Candidate Recommendation).

    python -m tgo ontop --out out/corpus          # writes out/corpus/ontop/{tgo.obda, tgo.properties, ...}
    # then (offline once the image and the duckdb_jdbc jar are present):
    docker run --rm -p 127.0.0.1:8080:8080 -v <out>:/data -v <out>/ontop:/opt/ontop/input \\
        -v <dir with duckdb_jdbc.jar>:/opt/ontop/jdbc -e ONTOP_ONTOLOGY_FILE=/opt/ontop/input/ontology.ttl \\
        -e ONTOP_MAPPING_FILE=/opt/ontop/input/tgo.obda -e ONTOP_PROPERTIES_FILE=/opt/ontop/input/tgo.properties \\
        ontop/ontop
"""
from __future__ import annotations

from pathlib import Path

from .. import ontology as O
from ..export.turtle import camel


def mapping() -> str:
    pre = {k: v for k, v in O.PREFIXES.items()}
    lines = ["[PrefixDeclaration]"] + [f"{k}:\t{v}" for k, v in pre.items()] + ["", "[MappingDeclaration] @collection [["]

    def m(mid, target, source):
        lines.extend([f"mappingId\t{mid}", f"target\t\t{target}", f"source\t\t{source}", ""])

    for c in O.CLASSES.values():
        m(f"class-{c.name}", f"tgr:{{key}} a tg:{c.name} .",
          f"SELECT key FROM nodes WHERE list_contains(labels, '{c.name}')")
    m("node-label", "tgr:{key} rdfs:label {name}^^xsd:string .",
      "SELECT key, coalesce(json_extract_string(props, '$.label'), json_extract_string(props, '$.title'), json_extract_string(props, '$.collectorDisplayName')) AS name "
      "FROM nodes WHERE coalesce(json_extract_string(props, '$.label'), json_extract_string(props, '$.title'), json_extract_string(props, '$.collectorDisplayName')) IS NOT NULL")
    for r in O.RELATIONS.values():
        m(f"rel-{r.name}", f"tgr:{{src}} tg:{camel(r.name)} tgr:{{dst}} .",
          f"SELECT src, dst FROM edges WHERE type = '{r.name}' AND status = 'observed'")
    m("assertion",
      "tgr:assertion/{h} a tg:Assertion ; rdf:subject tgr:{src} ; rdf:object tgr:{dst} ; "
      "tg:relationType {type}^^xsd:string ; tg:confidence {conf}^^xsd:double ; "
      "tg:epistemicStatus {status}^^xsd:string ; tg:eventCount {count}^^xsd:long .",
      "SELECT CAST(h AS VARCHAR) AS h, src, dst, type, conf, status, count FROM edges")
    m("assertion-method", "tgr:assertion/{h} tg:method {method}^^xsd:string .",
      "SELECT CAST(h AS VARCHAR) AS h, method FROM edges WHERE method IS NOT NULL")
    m("assertion-run", "tgr:assertion/{h} prov:wasGeneratedBy tgr:{run} .",
      "SELECT CAST(h AS VARCHAR) AS h, unnest(runs) AS run FROM edges")
    lines.append("]]")
    return "\n".join(lines) + "\n"


def write(out_dir: str | Path, db_in_container: str = "/data/tgo.duckdb") -> Path:
    """Write ``<out>/ontop/`` with tgo.obda, tgo.properties, ontology.ttl and a run script."""
    from ..export import write_tbox

    d = Path(out_dir) / "ontop"
    d.mkdir(parents=True, exist_ok=True)
    (d / "tgo.obda").write_text(mapping(), encoding="utf-8")
    (d / "tgo.properties").write_text(
        f"jdbc.url=jdbc:duckdb:{db_in_container}\n"
        "jdbc.driver=org.duckdb.DuckDBDriver\n"
        "jdbc.property.duckdb.read_only=true\n"
        "ontop.inferDefaultDatatype=true\n", encoding="utf-8")
    write_tbox(d / "ontology.ttl")
    return d
