"""Command line interface.

    python -m tgo demo  [--out out/demo]                 synthetic data -> full pipeline -> report.html
    python -m tgo run   --input DIR --out DIR           your Desktop export(s) / collected MTProto data
    python -m tgo ontology --out ontology.ttl           TBox only
    python -m tgo collect --target @chan ...            live MTProto collection (the only online step)
    python -m tgo offline-check                         static scan: no network paths in tgo/
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path


def _cfg(a):
    from .config import Config

    cfg = Config.load(getattr(a, "config", None))
    for m in getattr(a, "plugin", None) or []:
        if m not in cfg.plugins.modules:
            cfg.plugins.modules.append(m)
    if getattr(a, "engine", None):
        cfg.scale.engine = a.engine
    if getattr(a, "workers", None) is not None:
        cfg.scale.workers = a.workers
    if getattr(a, "dashboard", False):
        cfg.viewer.dashboard = True
    return cfg


def _config(a) -> int:
    from .config import Config

    print(Config.load(a.config).dumps())
    return 0


def _demo(a) -> int:
    from .demo.synth import generate
    from .pipeline import run

    out = Path(a.out)
    data = generate(out / "input")
    run(data, out, _cfg(a), auto_accept=a.auto_accept)
    print(f"\nopen {out / 'report.html'}")
    if a.open:
        webbrowser.open((out / "report.html").resolve().as_uri())
    return 0


def _run(a) -> int:
    from .pipeline import run

    run(a.input, a.out, _cfg(a), auto_accept=a.auto_accept, time_window=a.time_window,
        html=False if a.no_html else None)
    if a.open and not a.no_html:
        webbrowser.open((Path(a.out) / "report.html").resolve().as_uri())
    return 0


def _serve(a) -> int:
    from .live import LiveEngine
    from .live.server import serve
    from .pipeline import choose_engine

    cfg = _cfg(a)
    Path(a.input).mkdir(parents=True, exist_ok=True)       # an empty inbox is fine: data may arrive later
    engine = a.engine or ("memory" if not any(Path(a.input).rglob("*")) else choose_engine(a.input, cfg))
    def log(*parts):   # flushed: under Docker / a service manager stdout is a pipe
        print(*parts, flush=True)

    eng = LiveEngine(a.input, a.out, cfg, engine=engine, max_nodes=a.max_nodes, snapshots=not a.no_snapshots, log=log)
    opener = (lambda srv, url: webbrowser.open(url)) if a.open else None
    serve(eng, host=a.host, port=a.port, token=a.token, interval=a.interval, ready=opener, log=log)
    return 0


def _dashboard(a) -> int:
    from .dashboard import build_dashboard

    path = build_dashboard(a.out, path=a.path, max_nodes=a.max_nodes)
    print(f"wrote {path}")
    if a.open:
        webbrowser.open(Path(path).resolve().as_uri())
    return 0


def _ontology(a) -> int:
    from .export import write_tbox

    write_tbox(a.out)
    print(f"wrote {a.out}")
    return 0


def _collect(a) -> int:
    from .ingest import collector  # telethon is imported lazily inside collect()

    collector.main(a)
    return 0


def _ontop(a) -> int:
    from .scale.ontop import write

    print(f"wrote {write(a.out)}  (serve it: docker compose --profile ontop up)")
    return 0


def _offline(a) -> int:
    from . import offline

    return int(offline.main([]) or 0)


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` (default ``sys.argv``) and dispatch the sub-command; returns the exit code."""
    p = argparse.ArgumentParser(prog="tgo", description="Telegram Graph Ontology: get and map Telegram data.")
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("demo", help="generate synthetic data and run the full pipeline")
    d.add_argument("--out", default="out/demo")
    d.add_argument("--auto-accept", type=float, default=None, help="auto-accept identity candidates >= score")
    d.add_argument("--open", action="store_true", help="open report.html in a browser")
    d.add_argument("--config", default=None, help="JSON/TOML config file (see `tgo config`)")
    d.add_argument("--plugin", action="append", default=None, help="Python module to load as a plugin (repeatable)")
    d.add_argument("--dashboard", action="store_true", help="also write dashboard.html (interactive overview)")
    d.set_defaults(fn=_demo)

    r = sub.add_parser("run", help="ingest a Desktop export and/or collected MTProto data")
    r.add_argument("--input", required=True, help="dir containing result.json and/or collector.json (searched recursively)")
    r.add_argument("--out", required=True)
    r.add_argument("--auto-accept", type=float, default=None)
    r.add_argument("--time-window", type=int, default=None, help="coordination window in seconds (overrides config)")
    r.add_argument("--config", default=None, help="JSON/TOML config file (see `tgo config`)")
    r.add_argument("--engine", choices=["auto", "memory", "duckdb"], default=None,
                   help="memory = in-process graph; duckdb = columnar engine for millions of entries (default: auto)")
    r.add_argument("--workers", type=int, default=None, help="ingest/extract processes for the duckdb engine")
    r.add_argument("--plugin", action="append", default=None, help="Python module to load as a plugin (repeatable)")
    r.add_argument("--no-html", action="store_true")
    r.add_argument("--open", action="store_true")
    r.add_argument("--dashboard", action="store_true", help="also write dashboard.html (interactive overview)")
    r.set_defaults(fn=_run)

    v = sub.add_parser("serve", help="live dashboard: watch --input, ingest new data continuously, push updates (local web server)")
    v.add_argument("--input", required=True, help="folder to watch (collections, Desktop exports, plugin files); may start empty")
    v.add_argument("--out", required=True, help="where snapshots (graph.json, summary.json, validation.json) are kept current")
    v.add_argument("--host", default="127.0.0.1", help="bind address (default: this machine only; others require a token)")
    v.add_argument("--port", type=int, default=8765)
    v.add_argument("--token", default=None, help="access token (generated automatically when binding beyond localhost)")
    v.add_argument("--interval", type=float, default=5.0, help="seconds between scans of --input")
    v.add_argument("--engine", choices=["memory", "duckdb"], default=None,
                   help="memory = in-process graph (cases); duckdb = corpus store (millions); both ingest incrementally")
    v.add_argument("--max-nodes", type=int, default=500, help="actors in the relation network")
    v.add_argument("--no-snapshots", action="store_true", help="don't write graph.json/summary.json/validation.json each update")
    v.add_argument("--config", default=None, help="JSON/TOML config file (see `tgo config`)")
    v.add_argument("--plugin", action="append", default=None, help="Python module to load as a plugin (repeatable)")
    v.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    v.set_defaults(fn=_serve)

    b = sub.add_parser("dashboard", help="build the interactive dashboard.html for an existing output folder")
    b.add_argument("--out", required=True, help="a TGO output folder (graph.json, or tgo.duckdb from the duckdb engine)")
    b.add_argument("--path", default=None, help="where to write the page (default <out>/dashboard.html)")
    b.add_argument("--max-nodes", type=int, default=500, help="actors in the relation network")
    b.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    b.set_defaults(fn=_dashboard)

    o = sub.add_parser("ontology", help="write the TGO TBox as Turtle")
    o.add_argument("--out", default="ontology.ttl")
    o.set_defaults(fn=_ontology)

    c = sub.add_parser("collect", help="live MTProto collection via Telethon (online; needs a lawful basis)")
    c.add_argument("--api-id", type=int, default=None, help="or env TG_API_ID")
    c.add_argument("--api-hash", default=None, help="or env TG_API_HASH")
    c.add_argument("--session", default=None, help="Telethon session file (or env TG_SESSION; default ~/.tgo/collector)")
    c.add_argument("--target", action="append", required=True, help="@username / t.me link / id (repeatable)")
    c.add_argument("--limit", type=int, default=200)
    c.add_argument("--max-file-mb", type=int, default=20)
    c.add_argument("--no-participants", action="store_true")
    c.add_argument("--out", required=True)
    c.set_defaults(fn=_collect)

    k = sub.add_parser("config", help="print the effective configuration (defaults, or merged with --config) as JSON")
    k.add_argument("--config", default=None)
    k.set_defaults(fn=_config)

    on = sub.add_parser("ontop", help="write an Ontop mapping (SPARQL over the duckdb-engine store, no triple copy)")
    on.add_argument("--out", required=True, help="output dir of a duckdb-engine run (contains tgo.duckdb)")
    on.set_defaults(fn=_ontop)

    x = sub.add_parser("offline-check", help="scan tgo/ for network code paths")
    x.set_defaults(fn=_offline)

    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 0
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
