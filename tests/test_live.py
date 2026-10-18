"""Live mode: continuous ingestion gives exactly the batch result, and the server pushes it."""
from __future__ import annotations

import json
import shutil
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from tgo.config import Config
from tgo.live import LiveEngine


def _split_collection(src: Path, dst: Path, frac: float = 0.5) -> list[str]:
    """Copy a collection with only the first ``frac`` of its message lines; return the rest."""
    shutil.copytree(src, dst)
    lines = (src / "messages.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
    k = int(len(lines) * frac)
    (dst / "messages.jsonl").write_text("".join(lines[:k]), encoding="utf-8")
    return lines[k:]


def _fingerprint(g) -> tuple:
    return (sorted(g.nodes), sorted((e.id, e.props.get("count"), round(e.conf, 6), e.status) for e in g.edges.values()))


@pytest.fixture()
def demo(tmp_path):
    from tgo.demo.synth import generate

    return generate(tmp_path / "demo")


def test_incremental_ingestion_equals_batch(demo, tmp_path):
    from tgo.pipeline import build_graph

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    rest = _split_collection(demo / "mtproto", inbox / "mtproto")
    shutil.copytree(demo / "analyst", inbox / "analyst")
    eng = LiveEngine(inbox, tmp_path / "out", Config(), log=lambda *a: None)
    assert eng.cycle(force=True)
    first = eng.payload["kpi"]["messages"]
    assert not eng.cycle()                                   # nothing new: no re-derive

    # the collector appends: half a line first (must wait), then the rest
    with open(inbox / "mtproto" / "messages.jsonl", "a", encoding="utf-8") as f:
        f.write(rest[0][: len(rest[0]) // 2])
    assert not eng.cycle()
    with open(inbox / "mtproto" / "messages.jsonl", "a", encoding="utf-8") as f:
        f.write(rest[0][len(rest[0]) // 2:] + "".join(rest[1:]))
    assert eng.cycle()
    assert eng.payload["kpi"]["messages"] > first
    assert eng.events[-1]["messages"] == len(rest) and not eng.events[-1]["rebuild"]

    # a Desktop export dropped in mid-session is picked up whole
    shutil.copytree(demo / "export", inbox / "export")
    assert eng.cycle()

    batch, _ = build_graph(inbox, Config(), log=lambda *a: None)
    assert _fingerprint(eng.graph) == _fingerprint(batch)    # live == batch, counts included
    assert json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))["live"] is True


def test_rewritten_file_triggers_rebuild(demo, tmp_path):
    from tgo.pipeline import build_graph

    inbox = tmp_path / "inbox"
    shutil.copytree(demo, inbox)
    eng = LiveEngine(inbox, tmp_path / "out", Config(), log=lambda *a: None)
    eng.cycle(force=True)
    msgs = inbox / "mtproto" / "messages.jsonl"
    lines = msgs.read_text(encoding="utf-8").splitlines(keepends=True)
    msgs.write_text("".join(lines[:-3]), encoding="utf-8")   # shrank: not an append
    assert eng.cycle()
    assert "shrank" in eng.events[-1]["rebuild"]
    batch, _ = build_graph(inbox, Config(), log=lambda *a: None)
    assert _fingerprint(eng.graph) == _fingerprint(batch)


def test_expensive_extraction_is_memoized(demo, tmp_path, monkeypatch):
    from tgo import extractors

    calls = []
    real = extractors.perceptual_hash
    monkeypatch.setattr(extractors, "perceptual_hash", lambda p, *a, **k: calls.append(p) or real(p, *a, **k))
    eng = LiveEngine(demo, tmp_path / "out", Config(), log=lambda *a: None)
    eng.cycle(force=True)
    n = len(calls)
    assert n > 0
    eng.cycle(force=True)                                    # re-derive: no file is hashed twice
    assert len(calls) == n


def test_empty_inbox_starts(tmp_path):
    eng = LiveEngine(tmp_path / "empty", tmp_path / "out", Config(), log=lambda *a: None)
    (tmp_path / "empty").mkdir()
    assert eng.cycle(force=True)
    k = eng.payload["kpi"]
    assert k["messages"] == 0 and k["accounts"] == 0      # only analysis-run provenance exists yet


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, dict(r.headers), r.read()


def test_server_pushes_updates(demo, tmp_path):
    from tgo.live.server import LiveServer, watch

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    rest = _split_collection(demo / "mtproto", inbox / "mtproto")
    eng = LiveEngine(inbox, tmp_path / "out", Config(), log=lambda *a: None)
    srv = LiveServer(eng, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=watch, args=(eng, srv.stopping, 0.2), daemon=True).start()
    try:
        for _ in range(100):
            if eng.version:
                break
            time.sleep(0.1)
        st, hdr, body = _get(base + "/")
        assert st == 200 and b"const LIVE = true" in body
        assert "connect-src 'self'" in hdr["Content-Security-Policy"] and "default-src 'none'" in hdr["Content-Security-Policy"]
        v1 = json.loads(_get(base + "/api/dashboard")[2])
        # subscribe to SSE, then append data: an update event must arrive
        got = threading.Event()

        def listen():
            with urllib.request.urlopen(base + "/api/events", timeout=20) as r:
                for raw in r:
                    if raw.startswith(b"data:") and json.loads(raw[5:])["version"] > v1["live"]["version"]:
                        got.set()
                        return
        threading.Thread(target=listen, daemon=True).start()
        time.sleep(0.3)
        with open(inbox / "mtproto" / "messages.jsonl", "a", encoding="utf-8") as f:
            f.write("".join(rest))
        assert got.wait(15), "no SSE update after new data arrived"
        v2 = json.loads(_get(base + "/api/dashboard")[2])
        assert v2["kpi"]["messages"] > v1["kpi"]["messages"]
        assert v2["live"]["events"][-1]["messages"] == len(rest)
        st, _, body = _get(base + "/report.html")
        assert st == 200 and b"<html" in body[:400].lower()
        assert json.loads(_get(base + "/api/status")[2])["version"] == v2["live"]["version"]
    finally:
        srv.stopping.set()
        srv.shutdown()
        srv.server_close()


def test_non_loopback_requires_token(tmp_path):
    from tgo.live.server import LiveServer

    eng = LiveEngine(tmp_path, tmp_path / "out", Config(), log=lambda *a: None)
    eng.cycle(force=True)
    srv = LiveServer(eng, "0.0.0.0", 0)
    assert srv.token                                         # generated automatically
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(f"http://127.0.0.1:{port}/api/dashboard")
        assert e.value.code == 401
        assert _get(f"http://127.0.0.1:{port}/api/dashboard", {"X-TGO-Token": srv.token})[0] == 200
        st, hdr, _ = _get(f"http://127.0.0.1:{port}/?token={srv.token}")
        assert st == 200 and "HttpOnly" in hdr.get("Set-Cookie", "")
        assert _get(f"http://127.0.0.1:{port}/healthz")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------------------------ duckdb engine: incremental at scale
_EDGES = """SELECT id, count, round(conf, 6), status FROM edges
            WHERE type NOT IN ('ASSOCIATED_WITH', 'GENERATED_BY', 'USED') ORDER BY id"""


def _duck_cfg():
    return Config.from_dict({"scale": {"engine": "duckdb", "workers": 0, "exports": []}})


def test_duckdb_live_equals_batch(demo, tmp_path):
    pytest.importorskip("duckdb")
    from tgo.scale.pipeline import run as scale_run
    from tgo.scale.store import Store

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    rest = _split_collection(demo / "mtproto", inbox / "mtproto")
    shutil.copytree(demo / "analyst", inbox / "analyst")
    eng = LiveEngine(inbox, tmp_path / "live", _duck_cfg(), engine="duckdb", log=lambda *a: None)
    try:
        assert eng.cycle(force=True)
        first = eng.payload["kpi"]["messages"]
        assert not eng.cycle()
        with open(inbox / "mtproto" / "messages.jsonl", "a", encoding="utf-8") as f:
            f.write("".join(rest))
        assert eng.cycle()
        assert eng.payload["kpi"]["messages"] > first
        assert eng.events[-1]["messages"] == len(rest) and not eng.events[-1]["rebuild"]
        shutil.copytree(demo / "export", inbox / "export")          # an export dropped in mid-session
        assert eng.cycle()
        live = eng.store.sql(_EDGES).fetchall()
        assert eng.payload["meta"]["engine"] == "duckdb" and eng.payload["network"]["nodes"]
    finally:
        eng.close()
    scale_run(inbox, tmp_path / "batch", _duck_cfg(), log=lambda *a: None)
    batch = Store(tmp_path / "batch" / "tgo.duckdb", allowed_directories=[tmp_path / "batch"])
    try:
        assert live == batch.sql(_EDGES).fetchall()                  # incremental == one batch run
    finally:
        batch.close()


def test_duckdb_live_rebuilds_on_rewrite(demo, tmp_path):
    pytest.importorskip("duckdb")
    inbox = tmp_path / "inbox"
    shutil.copytree(demo, inbox)
    eng = LiveEngine(inbox, tmp_path / "live", _duck_cfg(), engine="duckdb", log=lambda *a: None)
    try:
        eng.cycle(force=True)
        msgs = inbox / "mtproto" / "messages.jsonl"
        msgs.write_text("".join(msgs.read_text(encoding="utf-8").splitlines(keepends=True)[:-5]), encoding="utf-8")
        assert eng.cycle() and "shrank" in eng.events[-1]["rebuild"]
        n = eng.store.sql("SELECT count(*) FROM nodes WHERE list_contains(labels, 'Message')").fetchone()[0]
        assert n == eng.payload["kpi"]["messages"]
    finally:
        eng.close()


def test_duckdb_live_retracts_removed_judgements(demo, tmp_path):
    pytest.importorskip("duckdb")
    inbox = tmp_path / "inbox"
    shutil.copytree(demo, inbox)
    eng = LiveEngine(inbox, tmp_path / "live", _duck_cfg(), engine="duckdb", log=lambda *a: None)
    try:
        eng.cycle(force=True)
        q = "SELECT count(*) FROM nodes WHERE list_contains(labels, 'PersonCluster')"
        assert eng.store.sql(q).fetchone()[0] == 1                   # minted by the positive judgement
        (inbox / "analyst" / "judgements.json").unlink()
        assert eng.cycle()                                           # the edit alone triggers an update
        assert eng.store.sql(q).fetchone()[0] == 0                   # gone with the judgement
        assert eng.payload["kpi"]["persons"] == 0
    finally:
        eng.close()


def test_editing_judgements_triggers_update(demo, tmp_path):
    inbox = tmp_path / "inbox"
    shutil.copytree(demo, inbox)
    eng = LiveEngine(inbox, tmp_path / "out", Config(), log=lambda *a: None)
    eng.cycle(force=True)
    assert eng.payload["kpi"]["persons"] == 1 and not eng.cycle()
    j = inbox / "analyst" / "judgements.json"
    decisions = [d for d in json.loads(j.read_text(encoding="utf-8")) if d["judgement"] != "positive"]
    j.write_text(json.dumps(decisions), encoding="utf-8")            # analyst withdraws the merge
    assert eng.cycle() and eng.payload["kpi"]["persons"] == 0
    assert "judgements.json" in eng.events[-1]["sources"]
