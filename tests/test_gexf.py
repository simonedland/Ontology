"""GEXF 1.3 export: well-formed, typed, dynamic, and positions survive."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from tgo.export.gexf import NS, VIZ, write_gexf
from tgo.graph import Graph

N = {"g": NS, "viz": VIZ}


def _graph() -> Graph:
    g = Graph()
    g.node("user:1", "Account", label="Alice\x07", premium=True)
    g.node("channel:2", label="News", members=1200)
    g.edge("MEMBER_OF", "user:1", "channel:2", t=1_700_000_000)
    g.edge("MEMBER_OF", "user:1", "channel:2", t=1_700_086_400)
    g.edge("MENTIONS", "user:1", "channel:2", status="inferred", conf=0.4, evidence=["msg:9"])
    return g


def test_gexf_roundtrip(tmp_path):
    p = tmp_path / "g.gexf"
    write_gexf(_graph(), p, positions={"user:1": (1.5, -2.0)})
    root = ET.parse(p).getroot()
    assert root.tag == f"{{{NS}}}gexf" and root.get("version") == "1.3"

    graph = root.find("g:graph", N)
    assert graph.get("mode") == "dynamic" and graph.get("timeformat") == "dateTime"

    nodes = {n.get("id"): n for n in graph.iterfind("g:nodes/g:node", N)}
    assert set(nodes) == {"user:1", "channel:2"}
    assert nodes["user:1"].get("label") == "Alice"  # XML-illegal control char stripped
    pos = nodes["user:1"].find("viz:position", N)
    assert (pos.get("x"), pos.get("y")) == ("1.5000", "-2.0000")
    assert nodes["channel:2"].find("viz:position", N) is None
    assert nodes["user:1"].find("viz:color", N) is not None

    edges = {e.get("kind"): e for e in graph.iterfind("g:edges/g:edge", N)}
    assert set(edges) == {"MEMBER_OF", "MENTIONS"}  # parallel edges of different types kept
    m = edges["MEMBER_OF"]
    assert (m.get("start"), m.get("end")) == ("2023-11-14T22:13:20", "2023-11-15T22:13:20")
    assert m.get("weight") == "2.0"
    assert edges["MENTIONS"].get("start") is None

    titles = {a.get("title"): a.get("type") for a in graph.iterfind("g:attributes[@class='edge']/g:attribute", N)}
    assert titles["conf"] == "double" and titles["count"] == "long" and titles["evidence"] == "string"
    assert "t_first" not in titles
    ntitles = {a.get("title"): a.get("type") for a in graph.iterfind("g:attributes[@class='node']/g:attribute", N)}
    assert ntitles["premium"] == "boolean" and ntitles["members"] == "long"


def test_static_graph_has_no_timeformat(tmp_path):
    g = Graph()
    g.edge("MENTIONS", "user:1", "user:2")
    p = tmp_path / "s.gexf"
    write_gexf(g, p)
    graph = ET.parse(p).getroot().find("g:graph", N)
    assert graph.get("mode") is None and graph.get("timeformat") is None
