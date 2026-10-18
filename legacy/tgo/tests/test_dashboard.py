"""dashboard.html: built offline from either engine, self-contained, numbers true to the store, injection-safe."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tgo import offline
from tgo.config import Config
from tgo.dashboard import build_dashboard, collect
from tgo.graph import Graph


def _payload(html: str) -> dict:
    m = re.search(r'<script id="tgo-dash" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


@pytest.fixture(scope="module")
def demo_out(tmp_path_factory):
    from tgo.demo.synth import generate
    from tgo.pipeline import run

    d = tmp_path_factory.mktemp("dash")
    cfg = Config()
    cfg.viewer.dashboard = True
    with offline.block_network():
        run(generate(d / "input"), d / "out", cfg, log=lambda *a: None)
    return d


def test_pipeline_flag_writes_dashboard(demo_out):
    out = demo_out / "out"
    html = (out / "dashboard.html").read_text(encoding="utf-8")
    assert offline.scan([out / "dashboard.html"]) == []
    assert "Apache ECharts NOTICE" in html                 # Apache-2.0 4(d): NOTICE travels with the page
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["exports"]["dashboard.html"] == "dashboard"


def test_numbers_match_the_graph(demo_out):
    out = demo_out / "out"
    g = Graph.load(out / "graph.json")
    d = _payload((out / "dashboard.html").read_text(encoding="utf-8"))
    assert d["kpi"]["relations"] == len(g.edges)
    assert d["kpi"]["observations"] == sum(int(e.props.get("count", 1)) for e in g.edges.values())
    assert d["kpi"]["messages"] == len(g.by_label("Message"))
    assert d["kpi"]["violations"] == 0
    assert sum(f[5] for f in d["flows"]) == d["kpi"]["observations"]
    # the network carries the planted 9-channel coordination cluster and fixed positions
    net = d["network"]
    assert all("x" in n and "y" in n for n in net["nodes"])
    ids = {n["id"] for n in net["nodes"]}
    cluster = next(c for c in d["clusters"] if c["kind"] == "CoordinationCluster")
    assert cluster["size"] == 9 and {m["key"] for m in cluster["members"]} <= ids
    # every class in the hierarchy is a declared class or a layer
    from tgo import ontology as O

    def names(n):
        yield n["name"]
        for k in n.get("children", []):
            yield from names(k)
    assert {x for r in d["hierarchy"] for x in names(r)} <= set(O.CLASSES) | {
        "Core", "Content", "Network", "Economy", "Derived", "Provenance"}


def test_attacker_controlled_names_cannot_break_out(tmp_path):
    g = Graph()
    evil = '</script><img src=x onerror=alert(1)>'
    g.node("channel:1", "Channel", title=evil)
    g.node("user:2", "HumanAccount", label=evil)
    g.edge("MEMBER_OF", "user:2", "channel:1")
    from tgo.dashboard.build import write_html

    html = write_html(collect(g), tmp_path / "d.html").read_text(encoding="utf-8")
    assert evil not in html and "<img src=x" not in html
    assert _payload(html)["network"]["nodes"][0]["name"] == evil       # still shown, as text


def test_cli_dashboard_command(demo_out):
    from tgo import cli

    target = demo_out / "elsewhere" / "dash.html"
    with offline.block_network():
        assert cli.main(["dashboard", "--out", str(demo_out / "out"), "--path", str(target), "--max-nodes", "20"]) == 0
    d = _payload(target.read_text(encoding="utf-8"))
    assert len(d["network"]["nodes"]) <= 20


def test_duckdb_engine_dashboard(tmp_path):
    pytest.importorskip("duckdb")
    from tgo.demo.synth import generate
    from tgo.pipeline import run

    cfg = Config.from_dict({"scale": {"engine": "duckdb", "workers": 0}, "viewer": {"dashboard": True}})
    with offline.block_network():
        report = run(generate(tmp_path / "input"), tmp_path / "out", cfg, log=lambda *a: None)
    d = _payload((tmp_path / "out" / "dashboard.html").read_text(encoding="utf-8"))
    assert d["meta"]["engine"] == "duckdb"
    assert d["kpi"]["relations"] == report["stats"]["edges"]          # corpus totals, not the case slice
    assert d["network"]["nodes"] and d["meta"]["scope_note"]
