"""Command line: python -m tgstore {serve, inbox, push, import-desktop, collect}."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import client


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="tgstore")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the HTTP API (needs PostgreSQL, see TGSTORE_DATABASE_URL)")
    s.add_argument("--host", default=os.environ.get("TGSTORE_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("TGSTORE_PORT", "8080")))
    s.add_argument("--workers", type=int, default=int(os.environ.get("TGSTORE_WORKERS", "1")))

    i = sub.add_parser("inbox", help="watch a folder and store every file dropped into it (writes to PostgreSQL)")
    i.add_argument("folder", nargs="?", default=os.environ.get("TGSTORE_INBOX", "inbox"))
    i.add_argument("--interval", type=float, default=2.0)

    for name, help_ in (("push", "send NDJSON/JSON record files (or stdin) to the API"),
                        ("import-desktop", "convert Telegram Desktop result.json files and send them to the API")):
        c = sub.add_parser(name, help=help_)
        c.add_argument("files", nargs="*")
        c.add_argument("--url", default=client.DEFAULT_URL)
        c.add_argument("--token", default=os.environ.get("TGSTORE_TOKEN"))

    sub.add_parser("collect", help="Telegram collector (pass --help after it)", add_help=False)

    args, rest = p.parse_known_args(argv)
    if args.cmd == "collect":
        from .collect import main as collect_main

        return collect_main(rest)
    if rest:
        p.error(f"unrecognized arguments: {' '.join(rest)}")

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("tgstore.api:app", host=args.host, port=args.port, workers=args.workers)
    elif args.cmd == "inbox":
        import logging
        import time

        from .db import Store
        from .inbox import Inbox

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        store = Store()
        store.migrate()
        Inbox(store.ingest, args.folder, args.interval).start()
        while True:
            time.sleep(3600)
    elif args.cmd == "push":
        from pathlib import Path

        from .inbox import read_file  # same formats as the drop folder

        if not args.files:
            print(json.dumps(client.push(client.read_ndjson(sys.stdin), args.url, args.token)))
        for f in args.files:
            print(f, json.dumps(client.push(read_file(Path(f)), args.url, args.token)))
    elif args.cmd == "import-desktop":
        from .sources.desktop import records

        for f in args.files:
            print(f, json.dumps(client.push(records(f), args.url, args.token)))


if __name__ == "__main__":
    main()
