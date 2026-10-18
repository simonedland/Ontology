"""Streaming, optionally parallel ingestion into a :class:`~tgo.scale.store.Store`.

Memory stays flat: each batch of ``batch_size`` messages is ingested into a fresh
in-memory :class:`~tgo.graph.Graph` with the *unchanged* adapters
(``g.node / g.edge / g.activity``), extracted (identifiers, media hashes, EXIF,
documents, ASR/OCR), turned into coordination share rows, flushed to the store,
and dropped.  Cross-batch merging happens once, in SQL, in ``Store.finalize()``.

With ``workers > 1`` message batches are processed in a process pool (the
regex/hash-heavy extraction is CPU bound); results are flushed in the parent.
Entities (users, channels, participants, ...) are ingested sequentially first.
"""
from __future__ import annotations

import itertools
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable, Iterator

from .. import extractors
from ..analysis.coordination import build_shares
from ..graph import Graph
from ..ingest.desktop_export import DesktopExportIngester
from ..ingest.mtproto import MTProtoIngester
from .store import to_arrow


def _chunks(it, n: int) -> Iterator[list]:
    it = iter(it)
    while True:
        chunk = list(itertools.islice(it, n))
        if not chunk:
            return
        yield chunk


def _lines(path: Path) -> Iterator[str]:
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield line


def _post(g: Graph, ext_opts: dict, min_text_len: int) -> list[tuple]:
    """Per-batch extraction + share rows (runs in the worker)."""
    extractors.run_all(g, Path("."), **ext_opts)
    return [(s.object_id, s.kind, s.account_id, s.content_id, s.ts) for s in build_shares(g, min_text_len)]


# ------------------------------------------------------------------ workers
def _mtproto_batch(root: str, lines: list[str], ext_opts: dict, min_text_len: int) -> tuple[dict, list]:
    g = Graph()
    ing = MTProtoIngester(g, Path(root))
    with ing.activity():
        for line in lines:
            ing.message(json.loads(line))
    shares = _post(g, ext_opts, min_text_len)
    return to_arrow(g), shares          # Arrow tables pickle compactly; the parent only inserts


def _export_batch(result_json: str, ckey: str, names: dict, msgs: list[dict], ext_opts: dict,
                  min_text_len: int) -> tuple[dict, list]:
    g = Graph()
    ing = DesktopExportIngester.__new__(DesktopExportIngester)   # skip re-reading the whole JSON
    ing.g, ing.path, ing.root, ing.data = g, Path(result_json), Path(result_json).parent, {}
    ing.collector_key = None
    with ing.activity():
        for m in msgs:
            ing._message(ckey, m, names)
    shares = _post(g, ext_opts, min_text_len)
    return to_arrow(g), shares


# --------------------------------------------------------------------- main
def ingest_dir(store, input_dir: Path, *, batch_size: int = 50_000, workers: int = 0,
               ext_opts: dict | None = None, min_text_len: int = 40, log: Callable = print) -> dict:
    """Stream every source under ``input_dir`` into ``store``.  Returns counts."""
    from ..pipeline import discover

    ext_opts = ext_opts or {}
    src = discover(Path(input_dir))
    workers = (os.cpu_count() or 2) - 1 if workers < 0 else workers
    totals = {"batches": 0, "nodes_staged": 0, "edges_staged": 0, "shares": 0}

    def take(result):
        g, shares = result
        n, e = store.flush(g) if isinstance(g, Graph) else store.insert_arrow(g)
        totals["batches"] += 1
        totals["nodes_staged"] += n
        totals["edges_staged"] += e
        totals["shares"] += store.add_shares(shares)
        if totals["batches"] % 20 == 0:
            log(f"    … {totals['batches']} batches, {totals['edges_staged']:,} edge rows staged")

    pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        for d in src["mtproto"]:
            # entities first (sequential: small relative to messages, order-dependent)
            g = Graph()
            ing = MTProtoIngester(g, d)
            with ing.activity():
                if ing.collector:
                    ing.user(ing.collector, is_collector=True)
                for chunk in _chunks(_lines(d / "entities.jsonl"), batch_size):
                    for line in chunk:
                        ing.entity(json.loads(line))
                    take((g, _post(g, ext_opts, min_text_len)))
                    g = Graph()
                    ing.g = g
                take((g, []))
            jobs = ((str(d), chunk, ext_opts, min_text_len) for chunk in _chunks(_lines(d / "messages.jsonl"), batch_size))
            if pool:
                for res in _imap(pool, _mtproto_batch, jobs, workers):
                    take(res)
            else:
                for job in jobs:
                    take(_mtproto_batch(*job))

        for rj in src["export"]:
            g = Graph()
            ing = DesktopExportIngester(g, rj)
            with ing.activity():
                ing._personal()
                ing._contacts()
                heads = []
                for chat, left in [(c, False) for c in (ing.data.get("chats") or {}).get("list", [])] + \
                                  [(c, True) for c in (ing.data.get("left_chats") or {}).get("list", [])]:
                    h = ing.chat_header(chat, left)
                    if h:
                        heads.append((h, chat.get("messages", [])))
            take((g, []))
            jobs = ((str(rj), ckey, names, chunk, ext_opts, min_text_len)
                    for (ckey, names), msgs in heads for chunk in _chunks(msgs, batch_size))
            if pool:
                for res in _imap(pool, _export_batch, jobs, workers):
                    take(res)
            else:
                for job in jobs:
                    take(_export_batch(*job))
            ing.data = None
    finally:
        if pool:
            pool.shutdown()
    return totals


def _imap(pool, fn, jobs, workers: int):
    """Ordered, bounded-lookahead map (keeps at most 2*workers batches in flight)."""
    from collections import deque

    q = deque()
    for job in jobs:
        q.append(pool.submit(fn, *job))
        if len(q) >= 2 * workers:
            yield q.popleft().result()
    while q:
        yield q.popleft().result()
