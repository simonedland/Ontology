"""TGO must run end to end with the network unplugged."""
from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from tgo import offline


def test_static_scan_package_clean():
    assert offline.scan() == []


def test_block_network_actually_blocks():
    with offline.block_network():
        with pytest.raises(offline.NetworkBlocked):
            socket.create_connection(("example.com", 80), timeout=1)
        with pytest.raises(offline.NetworkBlocked):
            socket.getaddrinfo("example.com", 443)


def test_scan_catches_cdn(tmp_path):
    bad = tmp_path / "x.html"
    bad.write_text('<script src="https://cdn.jsdelivr.net/npm/sigma@3"></script>'
                   '<link href="//fonts.googleapis.com/css?family=Inter" rel="stylesheet">')
    assert {why for _, _, why in offline.scan([bad])} == {"remote <script src>", "remote <link href>"}


def test_scan_catches_cloud_ai(tmp_path):
    bad = tmp_path / "x.py"
    bad.write_text("from google.cloud import vision\nimport openai\nimport numpy\n")
    assert [why for _, _, why in offline.scan([bad])] == ["cloud AI/API SDK import"] * 2


def test_pipeline_end_to_end_offline(tmp_path):
    synth = pytest.importorskip("tgo.demo.synth")
    pipeline = pytest.importorskip("tgo.pipeline")
    data, out = tmp_path / "data", tmp_path / "out"
    with offline.block_network():
        synth.generate(data)
        pipeline.run(data, out)
    produced = [p for p in out.rglob("*") if p.is_file()]
    assert produced, "pipeline wrote nothing"
    html = [p for p in produced if p.suffix in {".html", ".htm"}]
    assert html, "no visualization produced"
    assert offline.scan(html) == []


# Native code (Rust/C extensions, subprocesses) bypasses the Python socket patch,
# so also run the pipeline in a fresh interpreter with every proxy pointed at a dead
# port and the HF/transformers offline switches on.
# An audit hook also records attempts that a library might catch and swallow.
_E2E = """
import sys
events = []
_WATCH = ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "urllib.Request",
          "http.client.connect", "subprocess.Popen", "webbrowser.open")
sys.addaudithook(lambda ev, args: events.append((ev, repr(args)[:200])) if ev in _WATCH else None)
from pathlib import Path
from tgo import offline
from tgo.demo import synth
from tgo import pipeline
data, out = Path(sys.argv[1]), Path(sys.argv[2])
with offline.block_network():
    synth.generate(data)
    pipeline.run(data, out)
if events:
    sys.exit("network/subprocess attempts: %r" % events[:20])
"""


def test_pipeline_subprocess_offline_env(tmp_path):
    pytest.importorskip("tgo.demo.synth")
    pytest.importorskip("tgo.pipeline")
    import subprocess
    import sys

    dead = "http://127.0.0.1:9"
    env = {**os.environ, **offline.OFFLINE_ENV, "NO_PROXY": "",
           "HTTP_PROXY": dead, "HTTPS_PROXY": dead, "ALL_PROXY": dead,
           "http_proxy": dead, "https_proxy": dead, "all_proxy": dead,
           "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    r = subprocess.run([sys.executable, "-c", _E2E, str(tmp_path / "d"), str(tmp_path / "o")],
                       env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-4000:]
    assert any((tmp_path / "o").rglob("*.html"))


_CLI = """
import sys
events = []
_WATCH = ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "urllib.Request",
          "http.client.connect", "subprocess.Popen", "webbrowser.open")
sys.addaudithook(lambda ev, args: events.append((ev, repr(args)[:200])) if ev in _WATCH else None)
from tgo import cli, offline
d = sys.argv[1]
with offline.block_network():
    rc = [cli.main(["demo", "--out", d + "/demo"]),
          cli.main(["ontology", "--out", d + "/tbox.ttl"]),
          cli.main(["offline-check"])]
assert "telethon" not in sys.modules, "telethon imported outside `collect`"
if any(rc) or events:
    sys.exit("rc=%r events=%r" % (rc, events[:20]))
"""


def test_cli_offline(tmp_path):
    import subprocess
    import sys

    dead = "http://127.0.0.1:9"
    env = {**os.environ, **offline.OFFLINE_ENV, "HTTP_PROXY": dead, "HTTPS_PROXY": dead, "ALL_PROXY": dead,
           "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    r = subprocess.run([sys.executable, "-c", _CLI, str(tmp_path)], env=env, capture_output=True, text=True,
                       timeout=600)
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]
    assert (tmp_path / "demo" / "report.html").is_file()
    assert (tmp_path / "tbox.ttl").stat().st_size > 0


_DEPS = """
import builtins, json, sys
from pathlib import Path
# Record third-party top-level modules that tgo code imports *directly* and successfully;
# libraries' own optional try-imports (PIL->packaging, pypdf->cryptography, ...) don't count.
direct, _orig = set(), builtins.__import__
def _imp(name, globals=None, locals=None, fromlist=(), level=0):
    mod = _orig(name, globals, locals, fromlist, level)
    if level == 0 and (globals or {}).get("__name__", "").startswith("tgo"):
        direct.add(name.split(".")[0])
    return mod
builtins.__import__ = _imp
from tgo import offline
from tgo.demo import synth
from tgo import pipeline
with offline.block_network():
    synth.generate(Path(sys.argv[1]) / "d")
    pipeline.run(Path(sys.argv[1]) / "d", Path(sys.argv[1]) / "o", log=lambda *a: None)
print(json.dumps(sorted(direct - set(sys.stdlib_module_names) - {"tgo"})))
"""


def _dist_closure(names):
    import re
    from importlib import metadata

    seen, todo = set(), [n.lower() for n in names]
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        try:
            reqs = metadata.requires(n) or []
        except metadata.PackageNotFoundError:
            continue
        todo += [re.split(r"[\s;<>=!~\[(]", r, maxsplit=1)[0].lower() for r in reqs if "extra ==" not in r]
    return seen


def test_runtime_imports_are_declared_dependencies(tmp_path):
    """A core-only install must be able to run the pipeline (numpy/scipy were once missed)."""
    import json
    import re
    import subprocess
    import sys
    import tomllib
    from importlib import metadata

    proj = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    name = lambda r: re.split(r"[\s;<>=!~\[(]", r, maxsplit=1)[0].lower()
    core = {name(r) for r in proj["dependencies"]}
    extras = {name(r) for reqs in proj.get("optional-dependencies", {}).values() for r in reqs} - {"tgo"}
    r = subprocess.run([sys.executable, "-c", _DEPS, str(tmp_path)], capture_output=True, text=True, timeout=600,
                       env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
    assert r.returncode == 0, r.stderr[-4000:]
    loaded = json.loads(r.stdout.strip().splitlines()[-1])
    dists = metadata.packages_distributions()
    core_ok, extra_ok = _dist_closure(core), _dist_closure(extras)
    undeclared, optional_only = [], []
    for mod in loaded:
        ds = {d.lower() for d in dists.get(mod, [])}
        if not ds or ds & core_ok:
            continue
        (optional_only if ds & extra_ok else undeclared).append(mod)
    assert not undeclared, f"imported at runtime but not declared in pyproject: {undeclared}"
    # Extras are fine only if the pipeline degrades without them (they are imported lazily inside try/except).
    print("optional extras exercised:", optional_only)
