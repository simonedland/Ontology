"""Continuous ingestion: watch an input folder and keep a derived graph + dashboard payload current.

The engine keeps an incrementally ingested **base graph** of observed facts:

* new lines appended to a collection's ``entities.jsonl`` / ``messages.jsonl`` (what ``tgo collect``
  does on every run) are tailed by byte offset and ingested on their own; a half-written last line
  waits for the next scan;
* a new collection, Desktop export or plugin-source file is ingested whole;
* a file that is *rewritten* rather than appended to (a re-exported ``result.json``, a truncated
  JSONL) or removed triggers a full rebuild, so observation counts never double.

Derived layers (extraction, aggregation, coordination, communities, identity and plugin steps) are
not idempotent on the same graph, so every cycle recomputes them on a copy of the base with
:func:`tgo.pipeline.analyse`, exactly as a batch run would. The expensive per-file extractors (hashes,
EXIF, document text, speech-to-text, OCR) are memoized by file signature for the life of the engine,
so a repeat cycle only re-does graph writes and the identifier regex over text.

With the DuckDB engine the cycle is a rebuild-on-change: the corpus pipeline reruns into the output
folder and the dashboard reads the new store.
"""
from __future__ import annotations

import contextlib
import copy
import functools
import json
import threading
import time
from pathlib import Path

from .. import extractors, validate
from ..config import Config
from ..dashboard.build import collect
from ..analysis import coordination
from .. import identifiers
from ..graph import Edge, Graph, Node
from ..ingest.desktop_export import ingest as ingest_desktop_export
from ..ingest.mtproto import MTProtoIngester

MEMO_FUNCS = ("perceptual_hash", "exif_extract", "pdf_extract", "office_extract", "transcribe", "ocr")
# Pure functions of message text, re-run over every message on every update: cached across updates.
TEXT_FUNCS = ((coordination, "normalize"), (coordination, "simhash"), (identifiers, "extract"))


def clone(g: Graph) -> Graph:
    """A copy of ``g`` that analysis can mutate freely, ~10x faster than ``copy.deepcopy``.

    Analysis only rebinds or mutates top-level props, the evidence/run lists and label/source sets, so
    those containers are copied and the immutable values inside them are shared.
    """
    def props(d: dict) -> dict:
        return {k: (v.copy() if isinstance(v, (list, dict, set)) else v) for k, v in d.items()}

    h = Graph()
    h.nodes = {k: Node(k, set(n.labels), props(n.props), set(n.sources), dict(n.fields)) for k, n in g.nodes.items()}
    h.edges = {i: Edge(i, e.type, e.src, e.dst, props(e.props)) for i, e in g.edges.items()}
    h._out = {k: set(v) for k, v in g._out.items()}
    h._in = {k: set(v) for k, v in g._in.items()}
    h.warnings = list(g.warnings)
    return h
MAX_EVENTS = 200


def _sig(p: Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def _tail(path: Path, offset: int) -> tuple[list[str], int] | None:
    """Complete lines appended after ``offset`` (raw JSON text); ``None`` if the file shrank (rewritten)."""
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset
    if size < offset:
        return None
    if size == offset:
        return [], offset
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read(size - offset)
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], offset                     # a half-written line: wait for the rest
    out = []
    for line in chunk[:end].split(b"\n"):
        line = line.strip()
        if line:
            out.append(line.decode("utf-8"))
    return out, offset + end + 1


class LiveEngine:
    """Owns the base graph and produces a fresh dashboard payload whenever the input changes."""

    def __init__(self, input_dir: str | Path, out_dir: str | Path, config: Config | None = None, *,
                 engine: str = "memory", max_nodes: int = 500, snapshots: bool = True, log=print):
        self.input = Path(input_dir)
        self.out = Path(out_dir)
        self.cfg = copy.deepcopy(config) if config is not None else Config()
        self.engine = engine
        self.max_nodes = max_nodes
        self.snapshots = snapshots
        self.log = log
        self.version = 0
        self.payload: dict | None = None
        self.graph: Graph | None = None           # latest derived graph (for the investigation view)
        self.findings: list = []
        self.report: dict = {}
        self.events: list[dict] = []
        self.error: str | None = None
        self.last_cycle: float | None = None
        self.cond = threading.Condition()
        self._memo: dict = {}
        self._text = {(mod.__name__, name): functools.lru_cache(maxsize=1 << 20)(getattr(mod, name))
                      for mod, name in TEXT_FUNCS}
        self._reset()
        from .. import plugins

        plugins.load(self.cfg.plugins.modules, self.cfg.plugins.entry_points)

    # ------------------------------------------------------------------ change detection (both engines)
    def _reset(self) -> None:
        """Forget everything ingested: a fresh base graph (memory) or a fresh store (duckdb)."""
        self.base = Graph()
        self.collections: dict[Path, dict] = {}   # dir -> {"offsets": {name: int}, "meta": sig, "ing"}
        self.files: dict[Path, tuple] = {}        # whole-file sources (exports, plugin files) -> signature
        if self.engine == "duckdb":
            self._open_store()

    def _sources(self) -> tuple[list[Path], list[Path], dict[str, list[Path]]]:
        from .. import plugins
        from ..pipeline import discover

        src = discover(self.input)
        extra = {name: [Path(p) for p in s.discover(self.input)] for name, s in plugins.REGISTRY.sources.items()}
        return src["mtproto"], src["export"], extra

    def scan(self) -> dict:
        """Ingest whatever is new; returns a change summary (``changed`` is False when nothing moved)."""
        colls, exports, extra = self._sources()
        whole = {p: ("export", None) for p in exports}
        for name, paths in extra.items():
            for p in paths:
                whole[p] = ("plugin", name)
        rebuild = None
        if set(self.collections) - set(colls):
            rebuild = "a collection was removed"
        for p, sig in self.files.items():
            if p not in whole:
                rebuild = f"{p.name} was removed"
            elif _sig(p) != sig:
                rebuild = f"{p.name} was rewritten"
        for d, st in self.collections.items():
            if d in colls and _sig(d / "collector.json") != st["meta"]:
                rebuild = f"{d.name}/collector.json changed"
        # a JSONL that shrank was rewritten, not appended to
        for d, st in self.collections.items():
            for name, off in st["offsets"].items():
                sz = _sig(d / name)
                if sz is not None and sz[0] < off:
                    rebuild = f"{d.name}/{name} shrank"
        if rebuild:
            self.log(f"  live: full rebuild ({rebuild})")
            self._reset()
        n0, e0 = self._size()
        summary = {"changed": bool(rebuild), "rebuild": rebuild, "messages": 0, "entities": 0, "sources": []}
        for d in colls:
            st = self.collections.get(d)
            if st is None:
                st = self.collections[d] = {"offsets": {"entities.jsonl": 0, "messages.jsonl": 0},
                                            "meta": _sig(d / "collector.json")}
                self._new_collection(d, st)
                summary["sources"].append(str(d))
            new = {}
            for name in ("entities.jsonl", "messages.jsonl"):
                lines, st["offsets"][name] = _tail(d / name, st["offsets"][name]) or ([], st["offsets"][name])
                new[name] = lines
            if new["entities.jsonl"] or new["messages.jsonl"]:
                self._lines(d, st, new["entities.jsonl"], new["messages.jsonl"])
                summary["entities"] += len(new["entities.jsonl"])
                summary["messages"] += len(new["messages.jsonl"])
        for p, (kind, name) in sorted(whole.items()):
            if p in self.files:
                continue
            sig = _sig(p)
            self._whole_file(p, kind, name)
            self.files[p] = sig
            summary["sources"].append(str(p))
        # analyst decisions are applied by the analyses, not ingested: a change just triggers an update
        jsig = _sig(self.input / "analyst" / "judgements.json")
        if jsig != getattr(self, "_jsig", None):
            if hasattr(self, "_jsig"):
                summary["sources"].append("judgements.json")
            self._jsig = jsig
        n1, e1 = self._size()
        summary["nodes"], summary["edges"] = n1 - n0, e1 - e0
        summary["changed"] = summary["changed"] or bool(summary["sources"] or summary["messages"] or summary["entities"])
        return summary

    # ------------------------------------------------------------------ memory engine: base graph
    def _size(self) -> tuple[int, int]:
        if self.engine == "duckdb":
            return self._staged
        return len(self.base.nodes), len(self.base.edges)

    def _new_collection(self, d: Path, st: dict) -> None:
        if self.engine == "duckdb":     # the collector's own account, as ingest_dir adds it
            g = Graph()
            ing = MTProtoIngester(g, d)
            if ing.collector:
                with ing.activity():
                    ing.user(ing.collector, is_collector=True)
                self._take(self.store.flush(g))
            return
        st["ing"] = MTProtoIngester(self.base, d)
        with st["ing"].activity():
            if st["ing"].collector:
                st["ing"].user(st["ing"].collector, is_collector=True)

    def _lines(self, d: Path, st: dict, ent: list[str], msg: list[str]) -> None:
        if self.engine == "duckdb":
            from ..scale.stream import ingest_lines

            for lines, is_ent in ((ent, True), (msg, False)):
                if lines:
                    t = ingest_lines(self.store, d, lines, entities=is_ent, batch_size=self.cfg.scale.batch_size,
                                     workers=self.cfg.scale.workers, **self._stream_opts())
                    self._take((t["nodes_staged"], t["edges_staged"]))
            return
        with st["ing"].activity():
            for line in ent:
                st["ing"].entity(json.loads(line))
            for line in msg:
                st["ing"].message(json.loads(line))

    def _whole_file(self, p: Path, kind: str, name: str | None) -> None:
        from .. import plugins

        if self.engine != "duckdb":
            if kind == "export":
                ingest_desktop_export(self.base, p)
            else:
                plugins.REGISTRY.sources[name].ingest(self.base, p)
            return
        if kind == "export":
            from ..scale.stream import ingest_export

            t = ingest_export(self.store, p, batch_size=self.cfg.scale.batch_size, workers=self.cfg.scale.workers,
                              **self._stream_opts())
            self._take((t["nodes_staged"], t["edges_staged"]))
        else:                            # plugin source: one batch, extracted like the corpus pipeline does
            from ..analysis.coordination import build_shares

            g = Graph()
            plugins.REGISTRY.sources[name].ingest(g, p)
            opts = self._stream_opts()
            extractors.run_all(g, Path("."), **opts["ext_opts"])
            self.store.add_shares((x.object_id, x.kind, x.account_id, x.content_id, x.ts)
                                  for x in build_shares(g, opts["min_text_len"]))
            self._take(self.store.flush(g))

    # ------------------------------------------------------------------ duckdb engine: corpus store
    def _stream_opts(self) -> dict:
        return {"ext_opts": {"gazetteer": extractors.load_gazetteer(self.cfg.extract.gazetteer_path),
                             "phash_threshold": self.cfg.extract.phash_threshold},
                "min_text_len": self.cfg.coordination.min_text_len, "log": lambda *a: None}

    def _take(self, ne) -> None:
        self._staged = (self._staged[0] + ne[0], self._staged[1] + ne[1])

    def _open_store(self) -> None:
        """A fresh corpus store in ``--out`` (the live engine owns it while it runs)."""
        from ..scale.store import Store

        if getattr(self, "store", None) is not None:
            self.store.close()
        sc = self.cfg.scale
        self.out.mkdir(parents=True, exist_ok=True)
        db = Path(sc.store_path) if sc.store_path else self.out / "tgo.duckdb"
        for f in (db, Path(str(db) + ".wal")):
            if f.exists():
                f.unlink()
        self.store = Store(db, threads=sc.threads, memory_limit=sc.memory_limit, temp_directory=sc.temp_directory,
                           allowed_directories=[self.out, self.input], restrict_files=sc.restrict_files)
        self._staged = (0, 0)

    def _derive_store(self):
        """Re-run every corpus analysis in place (each retracts its previous output), then slice a case."""
        from ..dashboard.build import collect_store
        from ..scale.pipeline import analyse_store, case_keys, validate_store

        timings: dict[str, float] = {}

        def step(name, fn, *a, **kw):
            t0 = time.perf_counter()
            r = fn(*a, **kw)
            timings[name] = round(time.perf_counter() - t0, 3)
            return r
        report = analyse_store(self.store, self.cfg, self._decisions(), step=step, log=lambda *a: None)
        findings = validate_store(self.store)
        case = self.store.subgraph(case_keys(self.store, self.cfg.viewer.max_nodes))
        findings += [f for f in validate.validate(case)
                     if f.rule in ("R3-person-needs-judgement", "R8-judgement-accountability", "R9-sticker-ownership")]
        report.update(timings=timings, validation=validate.summary(findings), engine="duckdb", live=True,
                      store=str(self.store.path), config=self.cfg.to_dict())
        report.setdefault("stats", self.store.stats())
        data = collect_store(self.store, case, [f.__dict__ for f in findings], report, max_nodes=self.max_nodes,
                             title=f"TGO · live · {self.input.resolve().name}")
        return case, findings, report, data

    # ------------------------------------------------------------------ derived layers (memory engine)
    @contextlib.contextmanager
    def _memoized_extractors(self):
        """Swap in memoizing wrappers for the duration of one derive (only the watcher thread derives;
        the originals are always restored)."""
        saved = {f: getattr(extractors, f) for f in MEMO_FUNCS}
        saved_text = {(mod, name): getattr(mod, name) for mod, name in TEXT_FUNCS}

        def wrap(name, fn):
            def memo(path, *a, **kw):
                p = Path(path)
                key = (name, str(p.resolve()), _sig(p), a, tuple(sorted(kw.items())))
                if key not in self._memo:
                    self._memo[key] = fn(path, *a, **kw)
                return copy.deepcopy(self._memo[key])     # run_all mutates results (e.g. PHash.threshold)
            return memo
        for f, fn in saved.items():
            setattr(extractors, f, wrap(f, fn))
        for (mod, name) in saved_text:
            setattr(mod, name, self._text[(mod.__name__, name)])
        try:
            yield
        finally:
            for f, fn in saved.items():
                setattr(extractors, f, fn)
            for (mod, name), fn in saved_text.items():
                setattr(mod, name, fn)

    def _decisions(self) -> list:
        """judgements.json is the source of truth: a missing file means no decisions (``[]``, not ``None``,
        so the corpus store retracts decisions that were removed while the server ran)."""
        j = self.input / "analyst" / "judgements.json"
        return json.loads(j.read_text(encoding="utf-8")) if j.exists() else []

    def derive(self) -> tuple[Graph, list, dict]:
        from ..pipeline import analyse

        g = clone(self.base)
        timings: dict[str, float] = {}

        def step(name, fn, *a, **kw):
            t0 = time.perf_counter()
            r = fn(*a, **kw)
            timings[name] = round(time.perf_counter() - t0, 3)
            return r
        with self._memoized_extractors():
            report = analyse(g, self.cfg, self._decisions(), step=step)
        findings = validate.validate(g)
        report.update(timings=timings, validation=validate.summary(findings), stats=g.stats(),
                      config=self.cfg.to_dict(), engine="memory", live=True)
        return g, findings, report

    # ------------------------------------------------------------------ one cycle
    def cycle(self, force: bool = False) -> bool:
        """Scan, and if anything changed (or ``force``), re-derive and publish a new payload."""
        t0 = time.perf_counter()
        try:
            summary = self.scan()
            changed = summary["changed"] or force or self.payload is None
            if changed:
                if self.engine == "duckdb":
                    g, findings, report, data = self._derive_store()
                else:
                    g, findings, report = self.derive()
                    data = collect(g, [f.__dict__ for f in findings], report, max_nodes=self.max_nodes,
                                   title=f"TGO · live · {self.input.resolve().name}")
                self._publish(data, g, findings, report, summary, t0)
            self.error = None
        except Exception as e:                    # keep serving the last good state
            self.error = f"{type(e).__name__}: {e}"
            self.log(f"  live: cycle failed: {self.error}")
            changed = False
        self.last_cycle = time.time()
        return changed

    def close(self) -> None:
        """Release the corpus store (duckdb engine)."""
        if getattr(self, "store", None) is not None:
            self.store.close()
            self.store = None

    def _publish(self, data, g, findings, report, summary, t0, snapshot: bool = True) -> None:
        prev = {n["id"] for n in (self.payload or {}).get("network", {}).get("nodes", [])}
        data["network"]["new"] = [n["id"] for n in data["network"]["nodes"] if prev and n["id"] not in prev]
        dt = round(time.perf_counter() - t0, 2)
        ev = {"t": int(time.time()), "seconds": dt, "rebuild": summary.get("rebuild"),
              "messages": summary.get("messages"), "entities": summary.get("entities"),
              "nodes": summary.get("nodes"), "edges": summary.get("edges"),
              "sources": [Path(s).name for s in summary.get("sources", [])][:8]}
        with self.cond:
            self.events = (self.events + [ev])[-MAX_EVENTS:]
            self.version += 1
            data["live"] = {"version": self.version, "updated": ev["t"], "events": self.events[-60:],
                            "engine": self.engine, "input": str(self.input), "error": None}
            data["meta"]["hasReport"] = True
            self.payload, self.graph, self.findings, self.report = data, g, findings, report
            self._report_cache = None
            self.cond.notify_all()
        self.log(f"  live: v{self.version} in {dt}s · +{ev['messages'] or 0} messages, "
                 f"{len(g.nodes):,} nodes / {len(g.edges):,} edges")
        if snapshot and self.snapshots:
            self._write_snapshot(g, findings, report)

    def _write_snapshot(self, g, findings, report) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        name = "case_graph.json" if self.engine == "duckdb" else "graph.json"   # duckdb: the corpus is tgo.duckdb
        tmp = self.out / (name + ".tmp")
        g.save(tmp)
        tmp.replace(self.out / name)
        (self.out / "validation.json").write_text(json.dumps(validate.to_dicts(findings), indent=1), encoding="utf-8")
        (self.out / "summary.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")

    # ------------------------------------------------------------------ on-demand views
    def report_html(self) -> str:
        """The investigation view of the current derived graph (built on demand, cached per version)."""
        cache = getattr(self, "_report_cache", None)
        if cache and cache[0] == self.version:
            return cache[1]
        import tempfile

        from ..pipeline import layout
        from ..viz.report import build_report

        g, findings, report = self.graph, self.findings, dict(self.report)
        with tempfile.TemporaryDirectory() as d:
            p = build_report(g, findings, report, layout(g), Path(d) / "report.html",
                             title=self.cfg.viewer.title + " (live)", max_nodes=self.cfg.viewer.max_nodes)
            html = p.read_text(encoding="utf-8")
        self._report_cache = (self.version, html)
        return html

    def status(self) -> dict:
        return {"version": self.version, "engine": self.engine, "input": str(self.input), "out": str(self.out),
                "last_cycle": self.last_cycle, "error": self.error, "events": self.events[-20:],
                "nodes": len(self.graph.nodes) if self.graph else 0,
                "edges": len(self.graph.edges) if self.graph else 0, "memoized_extractions": len(self._memo)}
