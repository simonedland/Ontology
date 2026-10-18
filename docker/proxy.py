"""Loopback TCP forwarder: the only TGO container on a network that can publish ports.

Viewers and databases sit on an ``internal`` Docker network (no egress, and Docker will not publish
ports from it). This proxy joins that network plus an edge bridge, listens on the routes below and
forwards bytes unchanged. Compose publishes its ports on 127.0.0.1 only.

    TGO_PROXY_ROUTES="8000=viewer:8000,8765=live:8765,7474=neo4j:7474,7687=neo4j:7687,8080=ontop:8080"

Upstreams are resolved per connection, so a route to a service that is not running just refuses.
Stdlib only.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys


def routes(spec: str) -> list[tuple[int, str, int]]:
    out = []
    for item in filter(None, (s.strip() for s in spec.split(","))):
        listen, upstream = item.split("=", 1)
        host, port = upstream.rsplit(":", 1)
        out.append((int(listen), host, int(port)))
    return out


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy one direction; on EOF half-close (the other direction may still be streaming, e.g. SSE)."""
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, asyncio.CancelledError):
        writer.transport.abort()


def handler(host: str, port: int):
    async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
        try:
            ur, uw = await asyncio.wait_for(asyncio.open_connection(host, port), 5)
        except (OSError, asyncio.TimeoutError) as e:
            print(f"proxy: {host}:{port} unavailable ({e.__class__.__name__})", file=sys.stderr, flush=True)
            cw.close()
            return
        try:
            await asyncio.gather(pipe(cr, uw), pipe(ur, cw))
        finally:
            for w in (uw, cw):
                w.close()

    return handle


async def main() -> None:
    rs = routes(os.environ.get("TGO_PROXY_ROUTES", ""))
    if not rs:
        raise SystemExit("proxy: TGO_PROXY_ROUTES is empty")
    servers = [await asyncio.start_server(handler(h, p), "0.0.0.0", lp) for lp, h, p in rs]
    for lp, h, p in rs:
        print(f"proxy: :{lp} -> {h}:{p}", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    for s in servers:
        s.close()


if __name__ == "__main__":
    asyncio.run(main())
