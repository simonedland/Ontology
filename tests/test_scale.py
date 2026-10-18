"""The DuckDB corpus engine (tgo.scale) must be a drop-in for the in-memory engine."""
from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from tgo.config import Config  # noqa: E402
from tgo.graph import Graph  # noqa: E402
from tgo.scale.store import Store  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SKIP = {"ASSOCIATED_WITH", "GENERATED_BY"}   # provenance names the tools that actually ran (differ per engine)


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    from tgo.demo.synth import generate

    return generate(tmp_path_factory.mktemp("scale") / "input")


@pytest.fixture(scope="module")
def scale_run(demo, tmp_path_factory):
    from tgo.scale.pipeline import run

    out = tmp_path_factory.mktemp("scale_out")
    cfg = Config()
    cfg.scale.workers = 0
    cfg.scale.batch_size = 40          # many batches: exercises cross-batch merging
    report = run(demo, out, cfg, log=lambda *a: None)
    return out, report


def test_engine_parity(demo, scale_run):
    from tgo.pipeline import build_graph

    out, _ = scale_run
    g, _ = build_graph(demo, log=lambda *a: None)
    s = Store(out / "tgo.duckdb")
    s.finalized = True
    h = s.subgraph()
    a = {e.id for e in g.edges.values() if e.type not in SKIP}
    b = {e.id for e in h.edges.values() if e.type not in SKIP}
    assert a == b
    # provenance is idempotent: re-entering a run in every batch is not a new event
    assert all(e.props["count"] == 1 for e in h.edges.values() if e.type in SKIP)
    for i in a:
        ea, eb = g.edges[i].props, h.edges[i].props
        assert (ea.get("count"), round(ea["conf"], 6), ea["status"]) == (eb.get("count"), round(eb["conf"], 6), eb["status"]), i
    for k, n in g.nodes.items():
        if k in h.nodes and not k.startswith(("run:", "agent:")):
            assert n.labels == h.nodes[k].labels, k
            assert n.fields == h.nodes[k].fields, k
            for p in ("label", "collectorDisplayName", "title") + (() if k.startswith(("judg:", "pc:")) else ("createdAt",)):
                assert n.props.get(p) == h.nodes[k].props.get(p), (k, p)   # judgement createdAt = wall clock
    s.close()


def test_scale_outputs_and_guardrails(scale_run):
    out, report = scale_run
    assert report["validation"]["violation"] == 0
    assert report["coordination"]["clusters"] == 1 and report["identity"]["candidates"] == 2
    assert (out / "parquet" / "nodes.parquet").exists()
    assert any(p.name.startswith("type=") for p in (out / "parquet" / "edges").iterdir())
    header = (out / "neo4j" / "relationships.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith(":START_ID,:END_ID,:TYPE")
    lines = gzip.open(out / "tgo.nt.gz", "rt", encoding="utf-8").read().splitlines()
    assert all(l.endswith(" .") for l in lines) and any("#Assertion>" in l for l in lines)
    assert not any("owl#sameAs" in l for l in lines)
    assert (out / "report.html").exists() and json.loads((out / "case_graph.json").read_text(encoding="utf-8"))["nodes"]


def test_store_incremental_finalize_keeps_ids_and_merges():
    s = Store()
    g = Graph()
    g.node("user:1", "HumanAccount", label="a")
    g.edge("KNOWS", "user:1", "user:2", scope="collector_only", observed_by="user:9", t=10)
    s.flush(g)
    s.finalize()
    ids1 = dict(s.sql("SELECT key, id FROM nodes").fetchall())
    g2 = Graph()
    g2.node("user:1", labelX="b")
    g2.edge("KNOWS", "user:1", "user:2", scope="collector_only", observed_by="user:9", t=20)
    g2.node("user:3", "HumanAccount")
    s.flush(g2)
    s.finalize()
    ids2 = dict(s.sql("SELECT key, id FROM nodes").fetchall())
    assert all(ids2[k] == v for k, v in ids1.items()) and len(set(ids2.values())) == 3
    h = s.subgraph()
    e = h.edges["user:1-[KNOWS]->user:2"]
    assert (e.props["count"], e.props["t_first"], e.props["t_last"]) == (2, 10, 20)
    assert h.nodes["user:1"].props == {"label": "a", "labelX": "b"}
    # replace() writes a loaded subgraph back without double counting
    s.replace(h)
    s.finalize()
    assert s.subgraph().edges["user:1-[KNOWS]->user:2"].props["count"] == 2


def test_planted_rings_recovered_in_noise(tmp_path):
    from tgo.scale.pipeline import run

    subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_bench.py"), str(tmp_path / "b"),
                    "--messages", "20000", "--rings", "3"], check=True, capture_output=True)
    cfg = Config()
    cfg.scale.workers = 0
    cfg.viewer.html = False
    run(tmp_path / "b", tmp_path / "o", cfg, log=lambda *a: None)
    s = Store(tmp_path / "o" / "tgo.duckdb")
    found = [sorted(json.loads(x)) for (x,) in s.sql("""SELECT json_extract_string(props, '$.members') FROM nodes
                                                       WHERE list_contains(labels, 'CoordinationCluster')""").fetchall()]
    planted = json.loads((tmp_path / "b" / "planted.json").read_text())["rings"]
    assert sorted(found) == sorted(planted)
    s.close()


def test_engine_auto_selection(demo):
    from tgo.pipeline import choose_engine

    cfg = Config()
    assert choose_engine(demo, cfg) == "memory"           # tiny input
    cfg.scale.auto_engine_bytes = 1
    assert choose_engine(demo, cfg) == "duckdb"
    cfg.scale.engine = "memory"
    assert choose_engine(demo, cfg) == "memory"


def test_ontop_mapping_covers_vocabulary(scale_run):
    from tgo import ontology as O
    from tgo.scale.ontop import mapping, write

    out, _ = scale_run
    m = mapping()
    assert all(f"mappingId\tclass-{c}" in m for c in O.CLASSES)
    assert all(f"mappingId\trel-{r}" in m for r in O.RELATIONS)
    assert "owl:sameAs" not in m
    d = write(out)
    assert (d / "tgo.obda").exists() and "read_only=true" in (d / "tgo.properties").read_text()
    # every mapping source query must run against the real store schema
    import re

    s = Store(out / "tgo.duckdb")
    for q in re.findall(r"^source\t\t(.+)$", m, flags=re.M):
        s.sql(f"SELECT * FROM ({q}) LIMIT 1").fetchall()
    s.close()


def test_analyses_are_rerunnable_without_stale_results(scale_run):
    """Live/incremental mode: re-running an analysis replaces its previous output (no duplicates, no stale)."""
    from tgo.scale import analytics as A

    out, _ = scale_run
    s = Store(out / "tgo.duckdb", allowed_directories=[out])
    def snap():
        return (s.sql("SELECT count(*), sum(count) FROM edges").fetchone(),
                s.sql("SELECT count(*) FROM nodes WHERE list_contains(labels, 'CoordinationCluster')").fetchone()[0])
    before = snap()
    A.coordination(s, log=lambda *a: None)
    A.aggregate(s)
    s.finalize()
    assert snap() == before                                   # idempotent re-run
    A.coordination(s, min_participation=10_000, log=lambda *a: None)   # nothing qualifies any more
    s.finalize()
    assert snap()[1] == 0                                     # old clusters retracted, not left stale
    assert s.sql("SELECT count(*) FROM edges WHERE type IN ('IDENTICAL_POST','SYNC_POST')").fetchone()[0] == 0
    # analyst decisions survive an identity re-run
    A.identity(s, None, log=lambda *a: None)
    s.finalize()
    assert s.sql("""SELECT count(*) FROM nodes WHERE list_contains(labels, 'PersonCluster')""").fetchone()[0] == 1
    s.close()


def test_incremental_append_matches_batch(demo, tmp_path):
    """Live path at scale: ingest most messages, finalize+analyse, append the rest, re-analyse == one batch run."""
    from tgo.scale import analytics as A
    from tgo.scale.stream import ingest_dir, ingest_lines

    lines = (demo / "mtproto" / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    part = tmp_path / "part"
    import shutil

    shutil.copytree(demo / "mtproto", part / "mtproto")
    (part / "mtproto" / "messages.jsonl").write_text("\n".join(lines[:-40]) + "\n", encoding="utf-8")

    def analyse(s):
        s.finalize()
        A.aggregate(s)
        A.coordination(s, log=lambda *a: None)
        s.finalize()

    inc = Store()
    ingest_dir(inc, part, workers=0, log=lambda *a: None)
    analyse(inc)
    ingest_lines(inc, part / "mtproto", lines[-40:], log=lambda *a: None)
    analyse(inc)
    full = Store()
    shutil.copy(demo / "mtproto" / "messages.jsonl", part / "mtproto" / "messages.jsonl")
    ingest_dir(full, part, workers=0, log=lambda *a: None)
    analyse(full)
    q = """SELECT id, count, round(conf, 6), status FROM edges WHERE type NOT IN ('ASSOCIATED_WITH','GENERATED_BY')
           ORDER BY id"""
    assert inc.sql(q).fetchall() == full.sql(q).fetchall()



def test_analyse_store_twice_with_judgements_equals_batch(demo, tmp_path):
    """analyse_store is idempotent including replayed analyst decisions (live mode)."""
    import json as _j

    from tgo.scale.pipeline import analyse_store
    from tgo.scale.stream import ingest_dir

    cfg = Config()
    decisions = _j.loads((demo / "analyst" / "judgements.json").read_text(encoding="utf-8"))
    q = """SELECT id, count, round(conf, 6), status FROM edges
           WHERE type NOT IN ('ASSOCIATED_WITH','GENERATED_BY','USED') ORDER BY id"""
    once, twice = Store(), Store()
    for s in (once, twice):
        ingest_dir(s, demo, workers=0, log=lambda *a: None)
        analyse_store(s, cfg, decisions, log=lambda *a: None)
    analyse_store(twice, cfg, decisions, log=lambda *a: None)
    assert twice.sql(q).fetchall() == once.sql(q).fetchall()
    assert twice.sql("SELECT count(*) FROM nodes WHERE list_contains(labels,'PersonCluster')").fetchone()[0] == 1
