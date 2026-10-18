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


def _demo(a) -> int:
    from .demo.synth import generate
    from .pipeline import run

    out = Path(a.out)
    data = generate(out / "input")
    run(data, out, auto_accept=a.auto_accept)
    print(f"\nopen {out / 'report.html'}")
    if a.open:
        webbrowser.open((out / "report.html").resolve().as_uri())
    return 0


def _run(a) -> int:
    from .pipeline import run

    run(a.input, a.out, auto_accept=a.auto_accept, time_window=a.time_window, html=not a.no_html)
    if a.open and not a.no_html:
        webbrowser.open((Path(a.out) / "report.html").resolve().as_uri())
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


def _offline(a) -> int:
    from . import offline

    return int(offline.main([]) or 0)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tgo", description="Telegram Graph Ontology: get and map Telegram data.")
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("demo", help="generate synthetic data and run the full pipeline")
    d.add_argument("--out", default="out/demo")
    d.add_argument("--auto-accept", type=float, default=None, help="auto-accept identity candidates >= score")
    d.add_argument("--open", action="store_true", help="open report.html in a browser")
    d.set_defaults(fn=_demo)

    r = sub.add_parser("run", help="ingest a Desktop export and/or collected MTProto data")
    r.add_argument("--input", required=True, help="dir containing result.json and/or collector.json (searched recursively)")
    r.add_argument("--out", required=True)
    r.add_argument("--auto-accept", type=float, default=None)
    r.add_argument("--time-window", type=int, default=60, help="coordination window in seconds")
    r.add_argument("--no-html", action="store_true")
    r.add_argument("--open", action="store_true")
    r.set_defaults(fn=_run)

    o = sub.add_parser("ontology", help="write the TGO TBox as Turtle")
    o.add_argument("--out", default="ontology.ttl")
    o.set_defaults(fn=_ontology)

    c = sub.add_parser("collect", help="live MTProto collection via Telethon (online; needs a lawful basis)")
    c.add_argument("--api-id", type=int, default=None, help="or env TG_API_ID")
    c.add_argument("--api-hash", default=None, help="or env TG_API_HASH")
    c.add_argument("--session", default=str(Path.home() / ".tgo" / "tgo.session"))
    c.add_argument("--target", action="append", required=True, help="@username / t.me link / id (repeatable)")
    c.add_argument("--limit", type=int, default=200)
    c.add_argument("--max-file-mb", type=int, default=20)
    c.add_argument("--no-participants", action="store_true")
    c.add_argument("--out", required=True)
    c.set_defaults(fn=_collect)

    x = sub.add_parser("offline-check", help="scan tgo/ for network code paths")
    x.set_defaults(fn=_offline)

    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 0
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
