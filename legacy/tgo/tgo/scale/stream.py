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
from typing import Callable, Iterable, Iterator

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
class _Runner:
    """Holds the (optional) spawn process pool and running totals for one ingest session."""

    def __init__(self, store, workers: int, log: Callable):
        import multiprocessing as mp

        cpus = os.process_cpu_count() if hasattr(os, "process_cpu_count") else os.cpu_count()  # respects affinity
        self.workers = (cpus or 2) - 1 if workers < 0 else workers
        self.store, self.log = store, log
        self.totals = {"batches": 0, "nodes_staged": 0, "edges_staged": 0, "shares": 0}
        # spawn (not fork): the parent already runs DuckDB threads; forking them is a deadlock risk on Linux
        self.pool = (ProcessPoolExecutor(max_workers=self.workers, mp_context=mp.get_context("spawn"))
                     if self.workers > 1 else None)

    def take(self, result) -> None:
        g, shares = result
        n, e = self.store.flush(g) if isinstance(g, Graph) else self.store.insert_arrow(g)
        t = self.totals
        t["batches"] += 1
        t["nodes_staged"] += n
        t["edges_staged"] += e
        t["shares"] += self.store.add_shares(shares)
        if t["batches"] % 20 == 0:
            self.log(f"    … {t['batches']} batches, {t['edges_staged']:,} edge rows staged")

    def map(self, fn, jobs) -> None:
        for res in (_imap(self.pool, fn, jobs, self.workers) if self.pool else (fn(*j) for j in jobs)):
            self.take(res)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.pool:
            self.pool.shutdown()


def _lines_into(r: _Runner, collection_dir: Path, lines: Iterable[str], *, entities: bool, batch_size: int,
                ext_opts: dict, min_text_len: int, include_collector: bool = False) -> None:
    if entities:   # sequential: entity order matters (e.g. channels before their participants)
        g = Graph()
        ing = MTProtoIngester(g, collection_dir)
        with ing.activity():
            if include_collector and ing.collector:
                ing.user(ing.collector, is_collector=True)
            for chunk in _chunks(lines, batch_size):
                for line in chunk:
                    ing.entity(json.loads(line))
                r.take((g, _post(g, ext_opts, min_text_len)))
                g = Graph()
                ing.g = g
            r.take((g, []))
    else:
        r.map(_mtproto_batch, ((str(collection_dir), chunk, ext_opts, min_text_len)
                               for chunk in _chunks(lines, batch_size)))


def _export_into(r: _Runner, result_json: Path, *, batch_size: int, ext_opts: dict, min_text_len: int) -> None:
    g = Graph()
    ing = DesktopExportIngester(g, result_json)
    with ing.activity():
        ing._personal()
        ing._contacts()
        heads = []
        for chat, left in [(c, False) for c in (ing.data.get("chats") or {}).get("list", [])] + \
                          [(c, True) for c in (ing.data.get("left_chats") or {}).get("list", [])]:
            h = ing.chat_header(chat, left)
            if h:
                heads.append((h, chat.get("messages", [])))
    r.take((g, []))
    ing.data = None
    r.map(_export_batch, ((str(result_json), ckey, names, chunk, ext_opts, min_text_len)
                          for (ckey, names), msgs in heads for chunk in _chunks(msgs, batch_size)))


def ingest_lines(store, collection_dir: str | Path, lines: Iterable[str], *, entities: bool = False,
                 batch_size: int = 50_000, workers: int = 0, ext_opts: dict | None = None,
                 min_text_len: int = 40, log: Callable = print) -> dict:
    """Ingest MTProto JSONL lines (``entities.jsonl`` if ``entities`` else ``messages.jsonl``) of one collection.

    For live tailing and initial loads alike; call ``store.finalize()`` afterwards, then re-run the
    analyses (each retracts its previous output, so incremental updates stay exact).
    """
    with _Runner(store, workers, log) as r:
        _lines_into(r, Path(collection_dir), lines, entities=entities, batch_size=batch_size,
                    ext_opts=ext_opts or {}, min_text_len=min_text_len)
        return r.totals


def ingest_export(store, result_json: str | Path, *, batch_size: int = 50_000, workers: int = 0,
                  ext_opts: dict | None = None, min_text_len: int = 40, log: Callable = print) -> dict:
    """Ingest one Telegram Desktop ``result.json`` (parsed whole; export very large histories per chat)."""
    with _Runner(store, workers, log) as r:
        _export_into(r, Path(result_json), batch_size=batch_size, ext_opts=ext_opts or {}, min_text_len=min_text_len)
        return r.totals


def ingest_dir(store, input_dir: Path, *, batch_size: int = 50_000, workers: int = 0,
               ext_opts: dict | None = None, min_text_len: int = 40, log: Callable = print) -> dict:
    """Stream every source under ``input_dir`` into ``store`` (one shared worker pool).  Returns counts."""
    from ..pipeline import discover

    ext_opts = ext_opts or {}
    src = discover(Path(input_dir))
    with _Runner(store, workers, log) as r:
        for d in src["mtproto"]:
            _lines_into(r, d, _lines(d / "entities.jsonl"), entities=True, batch_size=batch_size,
                        ext_opts=ext_opts, min_text_len=min_text_len, include_collector=True)
            _lines_into(r, d, _lines(d / "messages.jsonl"), entities=False, batch_size=batch_size,
                        ext_opts=ext_opts, min_text_len=min_text_len)
        for rj in src["export"]:
            _export_into(r, rj, batch_size=batch_size, ext_opts=ext_opts, min_text_len=min_text_len)
        return r.totals


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
