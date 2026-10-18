"""RDF/Turtle export: the TGO TBox (classes, properties, SKOS schemes) and the ABox.

Observed edges become plain triples.  Every non-observed edge (inferred,
file-declared, analyst) is additionally reified as a ``tg:Assertion`` with
``tg:confidence``, ``tg:epistemicStatus``, ``tg:method``, ``tg:evidence`` and
``prov:wasGeneratedBy``; memberships become n-ary ``org:Membership`` nodes.
No ``owl:sameAs`` is ever emitted.  Dependency-free (rdflib not required).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

from .. import ontology as O
from ..graph import Graph


def camel(s: str) -> str:
    """``RELATION_NAME`` -> ``relationName`` (predicate local name)."""
    parts = s.lower().split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def iri(key: str) -> str:
    """Instance IRI (``tgr:``) for a node key, percent-encoded."""
    return f"<{O.TGR}{quote(key, safe=':/@-._~')}>"


def lit(v) -> str:
    """Turtle literal for a Python value (lists/dicts become JSON strings)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return f'"{v}"^^xsd:long' if abs(v) > 2**31 else str(v)
    if isinstance(v, float):
        return f'"{v}"^^xsd:double'
    if isinstance(v, (list, dict)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{s}"'


_PROP_OK = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def tbox() -> str:
    """Turtle for the ontology itself: classes, object properties, SKOS schemes."""
    out = []
    out.append(f"{iri('ontology')} a owl:Ontology ; rdfs:label \"Telegram Graph Ontology (TGO)\" .\n")
    for c in O.CLASSES.values():
        sup = [f"tg:{p}" for p in c.parents] + list(c.align)
        lines = [f"tg:{c.name} a owl:Class", f"rdfs:label {lit(c.name)}", f"tg:layer {lit(c.layer)}",
                 f"tg:epistemicStatus {lit(c.epistemic)}"]
        if sup:
            lines.append("rdfs:subClassOf " + ", ".join(sup))
        if c.doc:
            lines.append(f"rdfs:comment {lit(c.doc)}")
        out.append(" ;\n    ".join(lines) + " .\n")
    for r in O.RELATIONS.values():
        lines = [f"tg:{camel(r.name)} a owl:ObjectProperty", f"rdfs:label {lit(r.name)}",
                 f"tg:observabilityTier {lit(r.tier)}", f"tg:defaultStatus {lit(r.status)}"]
        if r.rdf:
            lines.append(f"rdfs:subPropertyOf {r.rdf}")
        if r.doc:
            lines.append(f"rdfs:comment {lit(r.doc)}")
        out.append(" ;\n    ".join(lines) + " .\n")
    for scheme, concepts in (("GroupRole", O.GROUP_ROLES), ("GroupKind", O.GROUP_KINDS),
                             ("Algorithm", O.ALGORITHMS), ("Judgement", O.JUDGEMENTS)):
        out.append(f"tg:{scheme} a skos:ConceptScheme .\n")
        for c in concepts:
            out.append(f"tg:{scheme.lower()}_{c} a skos:Concept ; skos:inScheme tg:{scheme} ; skos:prefLabel {lit(c)} .\n")
    return "\n".join(out)


def abox(g: Graph) -> str:
    """Turtle for the instance data, reifying every non-observed edge as tg:Assertion."""
    out = []
    for n in g.nodes.values():
        lines = [f"{iri(n.key)} a " + ", ".join(f"tg:{lab}" for lab in sorted(n.labels) if lab in O.CLASSES or True)
                 if n.labels else f"{iri(n.key)} a tg:Unlabelled"]
        lines.append(f"tg:key {lit(n.key)}")
        for k, v in sorted(n.props.items()):
            if v is None or not _PROP_OK.match(k) or k == "localPath":
                continue
            lines.append(f"tg:{k} {lit(v)}")
        for s in sorted(n.sources):
            lines.append(f"prov:wasGeneratedBy {iri(s)}")
        for f, rec in sorted(n.fields.items()):
            if rec["visibility"] != "visible":
                lines.append(f"tg:fieldVisibility [ tg:field {lit(f)} ; tg:visibility {lit(rec['visibility'])} ; "
                             f"tg:source {lit(rec['source'])} ]")
        out.append(" ;\n    ".join(lines) + " .\n")
    for i, e in enumerate(g.edges.values()):
        pred = f"tg:{camel(e.type)}"
        p = e.props
        if e.type in ("MEMBER_OF", "ADMIN_OF"):
            m = iri("membership/" + e.id)
            lines = [f"{m} a tg:GroupMembership, org:Membership", f"org:member {iri(e.src)}",
                     f"org:organization {iri(e.dst)}", f"tg:assertionStatus {lit(e.status)}"]
            if p.get("role"):
                lines.append(f"org:role tg:grouprole_{p['role']}")
            if p.get("scope"):
                lines.append(f"tg:visibilityScope {lit(p['scope'])}")
            if p.get("since") or p.get("until"):
                iv = "[ a time:Interval"
                if p.get("since"):
                    iv += f" ; time:hasBeginning [ time:inXSDDateTimeStamp {lit(p['since'])} ]"
                if p.get("until"):
                    iv += f" ; time:hasEnd [ time:inXSDDateTimeStamp {lit(p['until'])} ]"
                lines.append(f"org:memberDuring {iv} ]")
            for r in p.get("runs", []):
                lines.append(f"prov:wasGeneratedBy {iri(r)}")
            out.append(" ;\n    ".join(lines) + " .\n")
            continue
        if e.status == "observed":
            out.append(f"{iri(e.src)} {pred} {iri(e.dst)} .")
        if e.status != "observed" or p.get("count", 1) > 1 or p.get("scope"):
            a = iri("assertion/" + e.id)
            lines = [f"{a} a tg:Assertion", f"rdf:subject {iri(e.src)}", f"rdf:predicate {pred}",
                     f"rdf:object {iri(e.dst)}", f"tg:confidence {lit(float(p.get('conf', 1.0)))}",
                     f"tg:epistemicStatus {lit(e.status)}", f"tg:method {lit(p.get('method', ''))}",
                     f"tg:eventCount {lit(p.get('count', 1))}"]
            for k in ("scope", "observed_by", "t_first", "t_last", "judgement", "distance", "selector", "sensitivity"):
                if p.get(k) is not None:
                    lines.append(f"tg:{camel(k)} {lit(p[k])}")
            for ev in p.get("evidence", [])[:20]:
                lines.append(f"tg:evidence {iri(ev) if ':' in ev and ' ' not in ev else lit(ev)}")
            for r in p.get("runs", []):
                lines.append(f"prov:wasGeneratedBy {iri(r)}")
            out.append(" ;\n    ".join(lines) + " .\n")
    return "\n".join(out)


def write(g: Graph, path: str | Path, include_tbox: bool = True) -> None:
    """Write prefixes + (optionally) TBox + ABox to ``path``."""
    head = "\n".join(f"@prefix {k}: <{v}> ." for k, v in O.PREFIXES.items()) + "\n\n"
    body = (tbox() + "\n\n" if include_tbox else "") + abox(g)
    Path(path).write_text(head + body, encoding="utf-8")


def write_tbox(path: str | Path) -> None:
    """Write only the TBox (``ontology.ttl``)."""
    head = "\n".join(f"@prefix {k}: <{v}> ." for k, v in O.PREFIXES.items()) + "\n\n"
    Path(path).write_text(head + tbox(), encoding="utf-8")
