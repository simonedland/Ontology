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

import json  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from . import extractors, validate  # noqa: E402
from . import export as X  # noqa: E402
from .analysis import aggregate, communities, coordination, identity  # noqa: E402
from .graph import Graph  # noqa: E402
from .ingest import ingest_desktop_export, ingest_mtproto  # noqa: E402


def discover(input_dir: Path) -> dict:
    src = {"mtproto": [], "export": [], "judgements": None}
    for cj in sorted(input_dir.rglob("collector.json")):
        src["mtproto"].append(cj.parent)
    for rj in sorted(input_dir.rglob("result.json")):
        src["export"].append(rj)
    j = input_dir / "analyst" / "judgements.json"
    if j.exists():
        src["judgements"] = j
    return src


def build_graph(input_dir: str | Path, *, auto_accept: float | None = None, time_window: int = 60,
                log=print) -> tuple[Graph, dict]:
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

    if not src["mtproto"] and not src["export"]:
        raise SystemExit(f"no Telegram sources found under {input_dir} (collector.json / result.json)")
    for d in src["mtproto"]:
        step(f"ingest mtproto {d.name}", ingest_mtproto, g, d)
    for rj in src["export"]:
        step(f"ingest export {rj.parent.name}", ingest_desktop_export, g, rj)
    report["extract"] = step("extract", extractors.run_all, g, Path("."))
    report["aggregate"] = step("aggregate", aggregate.run, g)
    report["coordination"] = step("coordination", coordination.run, g, time_window=time_window)
    report["communities"] = step("communities", communities.run, g)
    decisions = json.loads(src["judgements"].read_text(encoding="utf-8")) if src["judgements"] else None
    report["identity"] = step("identity", identity.run, g, decisions, auto_accept=auto_accept)
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


def run(input_dir: str | Path, out_dir: str | Path, *, auto_accept: float | None = None,
        time_window: int = 60, html: bool = True, log=print) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log(f"TGO pipeline: {input_dir} -> {out}")
    g, report = build_graph(input_dir, auto_accept=auto_accept, time_window=time_window, log=log)

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
    pos = layout(g)
    X.write_gexf(g, out / "graph.gexf", positions=pos)
    report["exports"]["graph.gexf"] = "Gephi"
    if html:
        from .viz.report import build_report

        build_report(g, findings, report, pos, out / "report.html")
        report["exports"]["report.html"] = "viewer"
    (out / "summary.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    v = report["validation"]
    log(f"done: {len(g.nodes)} nodes, {len(g.edges)} edges; validation "
        f"{v['violation']} violations / {v['warning']} warnings / {v['info']} info")
    return report
