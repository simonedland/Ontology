"""The copy-and-adapt templates in examples/ work as documented, offline, on both engines."""
from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

import pytest

from tgo import offline, plugins
from tgo.config import Config

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "examples"
MEMORY_PLUGINS = ["examples.csv_leads", "examples.watchlist_extractor", "examples.amplifier_step",
                  "examples.csv_exporter"]
DUCKDB_PLUGINS = ["examples.csv_leads", "examples.amplifier_sql_step", "examples.csv_exporter"]


@pytest.fixture()
def case(tmp_path, monkeypatch):
    from tgo.demo.synth import generate

    monkeypatch.syspath_prepend(str(ROOT))
    data = generate(tmp_path / "input")
    shutil.copy(EX / "sample_data" / "leads.csv", data / "leads.csv")
    saved = (dict(plugins.REGISTRY.sources), dict(plugins.REGISTRY.steps), dict(plugins.REGISTRY.exporters),
             set(plugins.REGISTRY.loaded))
    yield data
    (plugins.REGISTRY.sources, plugins.REGISTRY.steps, plugins.REGISTRY.exporters,
     plugins.REGISTRY.loaded) = saved
    for m in list(sys.modules):
        if m.startswith("examples"):
            sys.modules.pop(m)


def _cfg(engine, modules):
    return Config.from_dict({
        "plugins": {"modules": modules, "entry_points": False,
                    "options": {"watchlist": {"path": str(EX / "sample_data" / "watchlist.json")}}},
        "scale": {"engine": engine, "workers": 0}, "viewer": {"html": False}})


def test_examples_pass_offline_scan():
    assert offline.scan([EX]) == []


def test_examples_memory_engine(case, tmp_path):
    from tgo.pipeline import run

    out = tmp_path / "out"
    with offline.block_network():
        report = run(case, out, _cfg("memory", MEMORY_PLUGINS), log=lambda *a: None)
    assert report["validation"]["violation"] == 0
    assert report["watchlist"]["hits"] > 0 and report["watchlist"]["tool"] == "keyword"
    assert report["amplifiers"]["scored"] >= 1
    g = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    leads = {e["src"] for e in g["edges"] if e["type"] == "IN_CLUSTER" and e["dst"] == "case:leads"}
    assert leads == {"user:705502011", "user:705502050", "username:veloria_news"}
    with open(out / "csv" / "edges.csv", encoding="utf-8-sig") as f:
        assert sum(1 for _ in csv.DictReader(f)) == len(g["edges"])
    assert report["exports"]["csv"]["nodes"] == len(g["nodes"])


def test_examples_duckdb_engine(case, tmp_path):
    pytest.importorskip("duckdb")
    from tgo.pipeline import run

    out = tmp_path / "out"
    with offline.block_network():
        report = run(case, out, _cfg("duckdb", DUCKDB_PLUGINS), log=lambda *a: None)
    assert report["validation"]["violation"] == 0
    assert report["amplifiers-sql"]["scored"] >= 1
    assert report["exports"]["csv"]["edges_full"] > 0
    with open(out / "csv" / "edges_full.csv", encoding="utf-8") as f:
        assert sum(1 for _ in csv.DictReader(f)) == report["exports"]["csv"]["edges_full"]
