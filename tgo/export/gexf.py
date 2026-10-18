"""GEXF 1.3 writer for Gephi, stdlib only (``xml.etree``).

GEXF is Gephi's native format; unlike GraphML it carries ``viz:position`` (so a
layout computed in Python survives import), ``viz:color``/``viz:size``, typed
attributes, edge ``kind`` (Gephi edge type, so parallel edges of different
TGO relationship types are kept) and ``start``/``end`` for the timeline.

* Edges with ``t_first``/``t_last`` get ``start``/``end`` as UTC ``dateTime``;
  the graph is then ``mode="dynamic"`` and undated elements are always present.
* Node/edge props that are lists or dicts are written as JSON strings; an
  attribute whose values mix types falls back to ``string``.
* Characters that are illegal in XML 1.0 (Telegram text can contain them) are
  stripped, otherwise Gephi refuses the file.

Spec: https://gexf.net/schema.html (1.3).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from .. import ontology as O
from ..graph import Graph

NS = "http://gexf.net/1.3"
VIZ = "http://gexf.net/1.3/viz"
_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
# Edge props promoted to GEXF structure rather than repeated as attributes.
_EDGE_STRUCTURAL = {"t_first", "t_last"}


def _clean(s: str) -> str:
    return _ILLEGAL_XML.sub("", s)


def _gexf_type(v) -> str | None:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "long"
    if isinstance(v, float):
        return "double"
    if isinstance(v, str | list | dict | tuple | set):
        return "string"
    return None


def _value(v, typ: str) -> str:
    if typ == "boolean":
        return "true" if v else "false"
    if typ in ("long", "double"):
        return repr(v) if isinstance(v, float) else str(v)
    if isinstance(v, set):
        v = sorted(v, key=str)
    if isinstance(v, list | dict | tuple):
        return _clean(json.dumps(v, ensure_ascii=False, sort_keys=True, default=str))
    return _clean(str(v))


def _schema(rows) -> dict[str, str]:
    """attribute name -> GEXF type, widening to ``string`` on conflict."""
    types: dict[str, str] = {}
    for props in rows:
        for k, v in props.items():
            t = _gexf_type(v)
            if t is None:
                continue
            prev = types.get(k)
            if prev is None:
                types[k] = t
            elif prev != t:
                types[k] = "double" if {prev, t} == {"long", "double"} else "string"
    return dict(sorted(types.items()))


def primary_label(labels) -> str:
    """Most specific ontology label, deterministic when several qualify."""
    labels = sorted(labels)
    specific = [lab for lab in labels if not any(lab in O.ancestors(o) for o in labels if o != lab)]
    return (specific or labels or ["Thing"])[0]


def _color(label: str) -> tuple[int, int, int]:
    h = int(hashlib.sha1(label.encode()).hexdigest()[:4], 16) / 0xFFFF  # noqa: S324 - colour, not security
    # HSV(h, 0.55, 0.85) -> RGB; stable per label across runs.
    i, f = int(h * 6), h * 6 - int(h * 6)
    v, s = 0.85, 0.55
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i % 6]
    return round(r * 255), round(g * 255), round(b * 255)


def _iso(t: int) -> str:
    return datetime.fromtimestamp(int(t), UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _attributes(parent: ET.Element, cls: str, schema: dict[str, str]) -> dict[str, str]:
    el = ET.SubElement(parent, "attributes", {"class": cls, "mode": "static"})
    ids = {}
    for i, (name, typ) in enumerate(schema.items()):
        ids[name] = f"{cls[0]}{i}"
        ET.SubElement(el, "attribute", {"id": ids[name], "title": name, "type": typ})
    return ids


def _attvalues(parent: ET.Element, props: dict, ids: dict[str, str], schema: dict[str, str]) -> None:
    vals = [(ids[k], _value(v, schema[k])) for k, v in sorted(props.items()) if k in ids and v is not None]
    if vals:
        el = ET.SubElement(parent, "attvalues")
        for fid, val in vals:
            ET.SubElement(el, "attvalue", {"for": fid, "value": val})


def build(g: Graph, positions: dict[str, tuple[float, float]] | None = None) -> ET.ElementTree:
    """Build the GEXF 1.3 ElementTree for ``g`` (see :func:`write_gexf`)."""
    ET.register_namespace("", NS)
    ET.register_namespace("viz", VIZ)
    positions = positions or {}
    nodes = sorted(g.nodes.values(), key=lambda n: n.key)
    edges = sorted(g.edges.values(), key=lambda e: e.id)
    degree: dict[str, int] = {}
    for e in edges:
        degree[e.src] = degree.get(e.src, 0) + 1
        degree[e.dst] = degree.get(e.dst, 0) + 1

    node_rows = [{"tgoLabel": primary_label(n.labels), "labels": sorted(n.labels),
                  "sources": sorted(n.sources), **n.props} for n in nodes]
    edge_rows = [{"type": e.type, **{k: v for k, v in e.props.items() if k not in _EDGE_STRUCTURAL}}
                 for e in edges]
    dynamic = any("t_first" in e.props for e in edges)

    root = ET.Element(f"{{{NS}}}gexf", {"version": "1.3"})
    meta = ET.SubElement(root, "meta", {"lastmodifieddate": datetime.now(UTC).strftime("%Y-%m-%d")})
    ET.SubElement(meta, "creator").text = "tgo"
    ET.SubElement(meta, "description").text = "Telegram Graph Ontology export"
    gattrs = {"defaultedgetype": "directed"}
    if dynamic:
        gattrs |= {"mode": "dynamic", "timeformat": "dateTime", "timezone": "UTC"}
    graph = ET.SubElement(root, "graph", gattrs)

    nschema, eschema = _schema(node_rows), _schema(edge_rows)
    nids = _attributes(graph, "node", nschema)
    eids = _attributes(graph, "edge", eschema)

    nel = ET.SubElement(graph, "nodes")
    for n, row in zip(nodes, node_rows, strict=True):
        label = n.props.get("label") or n.key
        el = ET.SubElement(nel, "node", {"id": _clean(n.key), "label": _clean(str(label))})
        _attvalues(el, row, nids, nschema)
        r, gr, b = _color(row["tgoLabel"])
        ET.SubElement(el, f"{{{VIZ}}}color", {"r": str(r), "g": str(gr), "b": str(b)})
        ET.SubElement(el, f"{{{VIZ}}}size", {"value": f"{4 + 3 * math.sqrt(degree.get(n.key, 0)):.2f}"})
        if n.key in positions:
            x, y = positions[n.key]
            ET.SubElement(el, f"{{{VIZ}}}position", {"x": f"{float(x):.4f}", "y": f"{float(y):.4f}", "z": "0.0"})

    eel = ET.SubElement(graph, "edges")
    for e, row in zip(edges, edge_rows, strict=True):
        attrs = {"id": _clean(e.id), "source": _clean(e.src), "target": _clean(e.dst),
                 "kind": e.type, "label": e.type, "weight": str(float(e.props.get("count") or 1))}
        if "t_first" in e.props:
            attrs["start"] = _iso(e.props["t_first"])
            attrs["end"] = _iso(e.props.get("t_last", e.props["t_first"]))
        el = ET.SubElement(eel, "edge", attrs)
        _attvalues(el, row, eids, eschema)
    ET.indent(root, space=" ")
    return ET.ElementTree(root)


def write_gexf(g: Graph, path: str | Path, positions: dict[str, tuple[float, float]] | None = None) -> None:
    """Write ``g`` as GEXF 1.3; ``positions`` (node key -> (x, y)) become ``viz:position``."""
    build(g, positions).write(path, encoding="utf-8", xml_declaration=True)
