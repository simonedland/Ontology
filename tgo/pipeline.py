"""End-to-end pipeline: ingest -> extract -> analyse -> validate -> export -> visualise.

    from tgo.pipeline import run
    summary = run("data/case42", "out/case42")

``input_dir`` may contain any of:
    mtproto/collector.json (+ entities.jsonl, messages.jsonl, media/)   MTProto / Telethon collection
    export/result.json  (or any **/result.json)                         Telegram Desktop export(s)
    analyst/judgements.json                                             analyst identity decisions
Everything runs offline; optional local models are picked up via TGO_* env vars.
"""
from __future__ import annotations

import os

for _k, _v in {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
               "ORT_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"}.items():
    os.environ.setdefault(_k, _v)

import copy  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from . import extractors, plugins, validate  # noqa: E402
from . import export as X  # noqa: E402
from .config import Config  # noqa: E402
from .analysis import aggregate, communities, coordination, identity  # noqa: E402
from .graph import Graph  # noqa: E402
from .ingest import ingest_desktop_export, ingest_mtproto  # noqa: E402


def discover(input_dir: Path) -> dict:
    """Find MTProto collections, Desktop exports and analyst judgements under ``input_dir``."""
    src = {"mtproto": [], "export": [], "judgements": None}
    for cj in sorted(input_dir.rglob("collector.json")):
        src["mtproto"].append(cj.parent)
    for rj in sorted(input_dir.rglob("result.json")):
        src["export"].append(rj)
    j = input_dir / "analyst" / "judgements.json"
    if j.exists():
        src["judgements"] = j
    return src


def input_bytes(input_dir: str | Path) -> int:
    """Total size of the message-bearing source files under ``input_dir``."""
    src = discover(Path(input_dir))
    files = [d / n for d in src["mtproto"] for n in ("entities.jsonl", "messages.jsonl")] + list(src["export"])
    return sum(f.stat().st_size for f in files if f.exists())


def choose_engine(input_dir: str | Path, cfg: Config) -> str:
    """``memory`` (rich in-process Graph) or ``duckdb`` (columnar corpus engine, tgo.scale)."""
    eng = cfg.scale.engine
    if eng == "auto":
        try:
            import duckdb  # noqa: F401
        except ImportError:
            return "memory"
        return "duckdb" if input_bytes(input_dir) > cfg.scale.auto_engine_bytes else "memory"
    if eng not in ("memory", "duckdb"):
        raise ValueError(f"unknown engine {eng!r} (memory | duckdb | auto)")
    return eng


def build_graph(input_dir: str | Path, config: Config | None = None, *, auto_accept: float | None = None,
                time_window: int | None = None, log=print) -> tuple[Graph, dict]:
    """Ingest every source under ``input_dir`` and run extraction + all analyses.

    ``config`` (see :mod:`tgo.config`) holds every tunable; the ``auto_accept`` and
    ``time_window`` keywords are shortcuts that override the corresponding config fields.
    Returns ``(graph, report)`` where ``report`` holds per-step counts and timings.
    """
    cfg = copy.deepcopy(config) if config is not None else Config()
    if auto_accept is not None:
        cfg.identity.auto_accept = auto_accept
    if time_window is not None:
        cfg.coordination.time_window = time_window
    input_dir = Path(input_dir)
    src = discover(input_dir)
    g = Graph()
    timings: dict[str, float] = {}
    report: dict = {"sources": {k: [str(p) for p in v] if isinstance(v, list) else (str(v) if v else None)
                                for k, v in src.items()}}

    def step(name, fn, *a, **kw):
        t0 = time.perf_counter()
        r = fn(*a, **kw)
        timings[name] = round(time.perf_counter() - t0, 3)
        log(f"  [{timings[name]:6.2f}s] {name}: {r}")
        return r

    report["plugins"] = plugins.load(cfg.plugins.modules, cfg.plugins.entry_points)
    extra = {name: s.discover(input_dir) for name, s in plugins.REGISTRY.sources.items()}
    if not src["mtproto"] and not src["export"] and not any(extra.values()):
        raise SystemExit(f"no Telegram sources found under {input_dir} (collector.json / result.json)")
    for d in src["mtproto"]:
        step(f"ingest mtproto {d.name}", ingest_mtproto, g, d)
    for rj in src["export"]:
        step(f"ingest export {rj.parent.name}", ingest_desktop_export, g, rj)
    for name, paths in extra.items():
        for path in paths:
            step(f"ingest {name} {Path(path).name}", plugins.REGISTRY.sources[name].ingest, g, Path(path))

    def builtin(name, fn, *a, **kw):
        report[name] = step(name, fn, *a, **kw)
        for s in plugins.steps_after(name, "memory"):
            report[s.name] = step(s.name, s.fn, g, cfg)

    builtin("extract", extractors.run_all, g, Path("."),
            gazetteer=extractors.load_gazetteer(cfg.extract.gazetteer_path),
            phash_threshold=cfg.extract.phash_threshold)
    builtin("aggregate", aggregate.run, g)
    builtin("coordination", coordination.run, g, **vars(cfg.coordination))
    c = cfg.communities
    builtin("communities", communities.run, g, seeds=c.seeds, resolution=c.resolution,
            min_size=c.min_size, min_stability=c.min_stability, edge_weights=c.edge_weights,
            deterministic=c.deterministic)
    decisions = json.loads(src["judgements"].read_text(encoding="utf-8")) if src["judgements"] else None
    i = cfg.identity
    builtin("identity", identity.run, g, decisions, min_score=i.min_score,
            auto_accept=i.auto_accept, weights=i.weights,
            strong_identifiers=i.strong_identifiers, min_bio_len=i.min_bio_len)
    for s in plugins.orphan_steps("memory"):
        report[s.name] = step(s.name, s.fn, g, cfg)
    report["config"] = cfg.to_dict()
    report["timings"] = timings
    return g, report


def layout(g: Graph, seed: int = 7, max_full: int = 2500) -> dict[str, tuple[float, float]]:
    """Deterministic force layout computed in Python (viewer uses preset positions)."""
    import networkx as nx

    G = nx.Graph()
    skip = {"GENERATED_BY", "USED", "ASSOCIATED_WITH"}
    for k in sorted(g.nodes):
        if not k.startswith(("run:", "agent:")):
            G.add_node(k)
    for e in sorted(g.edges.values(), key=lambda e: e.id):
        if e.type not in skip and e.src in G and e.dst in G and e.src != e.dst:
            w = 2.0 if e.type in ("IN_CHAT", "HAS_MEDIA", "ATTACHMENT_OF", "HAS_SEGMENT") else 1.0
            G.add_edge(e.src, e.dst, weight=w)
    if len(G) <= max_full:
        pos = nx.spring_layout(G, seed=seed, k=1.6 / max(1, len(G)) ** 0.5, iterations=120, weight="weight")
        return {k: (round(float(x) * 1400, 1), round(float(y) * 1400, 1)) for k, (x, y) in pos.items()}
    # large graph: lay out the actor skeleton, then place content nodes in rings around an anchor
    from . import ontology as O
    import math
    import random

    content = {"Message", "Rendition", "Extracted", "Snapshot", "Membership", "Judgement", "Media", "other"}
    core = [k for k in G if O.family(g.nodes[k].labels) not in content]
    S = G.subgraph(core)
    pos = nx.spring_layout(S, seed=seed, k=2.0 / max(1, len(S)) ** 0.5, iterations=60) if len(S) <= 2 * max_full         else nx.random_layout(S, seed=seed)
    out = {k: (round(float(x) * 1400, 1), round(float(y) * 1400, 1)) for k, (x, y) in pos.items()}
    rnd = random.Random(seed)
    for k in G:
        if k in out:
            continue
        anchor = next((n for n in G.neighbors(k) if n in out), None)
        ax, ay = out.get(anchor, (0.0, 0.0))
        a, r = rnd.random() * 2 * math.pi, 30 + rnd.random() * 60
        out[k] = (round(ax + r * math.cos(a), 1), round(ay + r * math.sin(a), 1))
    return out


def run(input_dir: str | Path, out_dir: str | Path, config: Config | None = None, *,
        auto_accept: float | None = None, time_window: int | None = None, html: bool | None = None,
        log=print) -> dict:
    """Full pipeline: :func:`build_graph`, validation, every export and (optionally) report.html.

    Writes to ``out_dir`` and returns the summary dict (also saved as ``summary.json``).
    """
    cfg = copy.deepcopy(config) if config is not None else Config()
    if html is not None:
        cfg.viewer.html = html
    if choose_engine(input_dir, cfg) == "duckdb":
        from .scale.pipeline import run as run_scale

        return run_scale(input_dir, out_dir, cfg, auto_accept=auto_accept, time_window=time_window, log=log)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log(f"TGO pipeline: {input_dir} -> {out}")
    g, report = build_graph(input_dir, cfg, auto_accept=auto_accept, time_window=time_window, log=log)

    findings = validate.validate(g)
    report["validation"] = validate.summary(findings)
    report["stats"] = g.stats()
    (out / "validation.json").write_text(json.dumps(validate.to_dicts(findings), indent=1), encoding="utf-8")

    g.save(out / "graph.json")
    X.write_tbox(out / "ontology.ttl")
    X.write_turtle(g, out / "tgo.ttl", include_tbox=False)
    report["exports"] = {
        "graph.json": len(g.nodes), "ontology.ttl": "TBox", "tgo.ttl": "ABox",
        "ftm.jsonl": X.write_ftm(g, out / "ftm.jsonl"), "stix.json": X.write_stix(g, out / "stix.json"),
    }
    X.write_cypher(g, out / "neo4j.cypher")
    report["exports"]["neo4j.cypher"] = "MERGE script"
    pos = layout(g, seed=cfg.viewer.layout_seed, max_full=cfg.viewer.layout_max_full)
    X.write_gexf(g, out / "graph.gexf", positions=pos)
    report["exports"]["graph.gexf"] = "Gephi"
    for name, ex in plugins.REGISTRY.exporters.items():
        if "memory" in ex.engines:
            report["exports"][name] = ex.fn(g, out, cfg)
    if cfg.viewer.html:
        from .viz.report import build_report

        build_report(g, findings, report, pos, out / "report.html", title=cfg.viewer.title,
                     max_nodes=cfg.viewer.max_nodes)
        report["exports"]["report.html"] = "viewer"
    (out / "summary.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    if cfg.viewer.dashboard:   # reads the files just written, so it runs last
        from .dashboard import build_dashboard

        build_dashboard(out, max_nodes=cfg.viewer.dashboard_max_nodes)
        report["exports"]["dashboard.html"] = "dashboard"
        (out / "summary.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    v = report["validation"]
    log(f"done: {len(g.nodes)} nodes, {len(g.edges)} edges; validation "
        f"{v['violation']} violations / {v['warning']} warnings / {v['info']} info")
    return report
