"""Plugins: a source, a step and an exporter registered by decorators run in both engines."""
from __future__ import annotations

import json
import sys
import textwrap

import pytest

from tgo import plugins
from tgo.config import Config

PLUGIN = textwrap.dedent('''
    import csv, json
    from pathlib import Path
    from tgo.plugins import source, step, exporter

    @source("csv-leads", discover=lambda d: sorted(Path(d).rglob("leads.csv")))
    def ingest_leads(g, path):
        with g.activity("CollectionRun", f"leads:{path.name}", source="analyst-csv"):
            for row in csv.DictReader(open(path, encoding="utf-8")):
                g.node(f"user:{row['user_id']}", "HumanAccount", userId=int(row["user_id"]))
                g.node("case:leads", "CaseGroup", label="Leads from tip line", rationale=row["why"])
                g.edge("IN_CLUSTER", f"user:{row['user_id']}", "case:leads", status="analyst", conf=0.9,
                       method="tip-line", evidence=[str(path)])

    @step("count-leads", after="identity", engines=("memory",))
    def count_leads(g, cfg):
        n = sum(1 for e in g.by_type("IN_CLUSTER") if e.dst == "case:leads")
        g.node("case:leads", leadCount=n)
        return {"leads": n}

    @exporter("leads-json")
    def export_leads(g, out_dir, cfg, store=None):
        keys = sorted(e.src for e in g.by_type("IN_CLUSTER") if e.dst == "case:leads")
        (Path(out_dir) / "leads.json").write_text(json.dumps(keys))
        return {"leads": len(keys)}
''')


@pytest.fixture()
def plugin_env(tmp_path, monkeypatch):
    from tgo.demo.synth import generate

    mod = tmp_path / "mods"
    mod.mkdir()
    (mod / "tgo_test_leads.py").write_text(PLUGIN, encoding="utf-8")
    monkeypatch.syspath_prepend(str(mod))
    data = generate(tmp_path / "input")
    (data / "leads.csv").write_text("user_id,why\n705502011,tip 17\n705502050,tip 18\n", encoding="utf-8")
    saved = (dict(plugins.REGISTRY.sources), dict(plugins.REGISTRY.steps), dict(plugins.REGISTRY.exporters),
             set(plugins.REGISTRY.loaded))
    yield data
    plugins.REGISTRY.sources, plugins.REGISTRY.steps, plugins.REGISTRY.exporters, plugins.REGISTRY.loaded = \
        saved[0], saved[1], saved[2], saved[3]
    sys.modules.pop("tgo_test_leads", None)


@pytest.mark.parametrize("engine", ["memory", "duckdb"])
def test_plugin_source_step_exporter(plugin_env, tmp_path, engine):
    if engine == "duckdb":
        pytest.importorskip("duckdb")
    from tgo.pipeline import run

    cfg = Config.from_dict({"plugins": {"modules": ["tgo_test_leads"], "entry_points": False},
                            "scale": {"engine": engine, "workers": 0}, "viewer": {"html": False}})
    out = tmp_path / f"out-{engine}"
    report = run(plugin_env, out, cfg, log=lambda *a: None)
    assert report["count-leads"] == {"leads": 2}
    assert json.loads((out / "leads.json").read_text()) == ["user:705502011", "user:705502050"]
    assert report["validation"]["violation"] == 0
