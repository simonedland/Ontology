"""Build ``dashboard.html``: a self-contained, offline, interactive overview of a TGO data store.

    python -m tgo dashboard --out out/case            # reads graph.json (memory) or tgo.duckdb (duckdb engine)

Everything the page shows is pre-aggregated here, so the browser only receives compact tables:

* stat tiles (accounts, chats, messages, media, identifiers, derived edges, guardrail findings);
* a relation-flow table (source family x relation x target family x status x confidence bin) behind the
  Sankey, chord and evidence-quality charts, re-aggregated client-side when filters change;
* an actor-level relation network: message- and media-level facts are *projected* onto the chat or
  account they belong to, so identifiers, devices and clusters connect to the actors that carry them;
* the ontology as used: a class-level schema graph (WebVOWL style) and the class hierarchy with
  instance counts;
* activity over time (per day, top chats) and an hour-by-weekday heatmap;
* findings: coordination clusters, identity candidates, guardrails, top identifiers.

With the DuckDB engine, totals, flows and activity come from SQL over the whole corpus, while the
network and schema graph use the bounded case slice (``case_graph.json``).
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .. import ontology as O
from ..graph import Graph
from ..viz.report import _TYPE_GROUP, EDGE_GROUPS

HERE = Path(__file__).parent
VENDOR = HERE.parent / "viz" / "vendor"

# Families shown as actors in the relation network (others are projected onto these).
ACTOR_FAMILIES = ["Account", "Chat", "Community", "InferredGroup", "Group", "Person", "Identifier", "Device"]
# Fixed family -> categorical slot (colour follows the entity, never its rank). Shape is the secondary channel.
FAMILY_SLOT = {"Account": 1, "Chat": 2, "Community": 3, "InferredGroup": 7, "Group": 7, "Person": 5,
               "Identifier": 4, "Device": 6, "Media": 8, "Message": 0, "Extracted": 0, "Snapshot": 0,
               "Rendition": 0, "Judgement": 0, "Membership": 0, "Run": 0, "other": 0}
FAMILY_SHAPE = {"Account": "circle", "Chat": "roundRect", "Community": "path://M50 0 L100 25 L100 75 L50 100 L0 75 L0 25Z",
                "InferredGroup": "diamond", "Group": "diamond", "Person": "pin", "Identifier": "triangle",
                "Device": "rect", "Media": "rect", "other": "circle"}
LAYER_SLOT = {"Core": 1, "Content": 2, "Network": 3, "Economy": 4, "Derived": 7, "Provenance": 0}
STATUSES = ["observed", "inferred", "file_declared", "analyst"]
HIDDEN_GROUPS = {"provenance"}          # off by default in the UI (toggleable)
DAY = 86400
MAX_GROUP_MEMBERS = 40                  # IN_CLUSTER edges drawn per inferred group in the network


def _family(labels) -> str:
    return O.family(labels) if labels else "other"


def _specific(labels) -> str:
    """Most specific declared class in a label set (a label none of the others descends from)."""
    labs = [l for l in labels if l in O.CLASSES]
    for lab in sorted(labs):
        if not any(lab in O.ancestors(o) for o in labs if o != lab):
            return lab
    return sorted(labels)[0] if labels else "Unlabelled"


def _label(n) -> str:
    p = n.props
    # collectorDisplayName (the collector's contact name) is a last resort: it is the collector's view, not self-presentation
    for k in ("label", "title", "usernameValue", "value", "name", "fileName", "collectorDisplayName"):
        if p.get(k):
            v = str(p[k])
            return ("@" + v) if k == "usernameValue" else v
    return n.key


def _group(t: str) -> str:
    return _TYPE_GROUP.get(t, "content")


def _cbin(conf: float) -> int:
    return min(9, max(0, int(float(conf) * 10)))


# ===================================================================== memory engine
def _chat_of(g: Graph, key: str) -> str | None:
    for e in g.out_edges(key, "IN_CHAT"):
        return e.dst
    return None


def _actor_of(g: Graph, key: str, fam: dict, depth: int = 0) -> str | None:
    """Project a node onto the actor that carries it (message -> chat, media -> message -> chat, ...)."""
    f = fam.get(key)
    if f in ACTOR_FAMILIES:
        return key
    if depth > 2 or f is None:
        return None
    if f == "Message":
        return _chat_of(g, key)
    for et in ("HAS_MEDIA", "HAS_TEXT", "HAS_TRANSCRIPT", "HAS_METADATA", "FINGERPRINT_OF"):
        for e in (g.in_edges(key, et) if et != "FINGERPRINT_OF" else g.out_edges(key, et)):
            up = e.src if et != "FINGERPRINT_OF" else e.dst
            a = _actor_of(g, up, fam, depth + 1)
            if a:
                return a
    return None


def _network(g: Graph, fam: dict, max_nodes: int, max_links: int | None = None) -> dict:
    agg: dict[tuple, dict] = {}
    for e in g.edges.values():
        grp = _group(e.type)
        if grp == "provenance":
            continue
        a, b = _actor_of(g, e.src, fam), _actor_of(g, e.dst, fam)
        if not a or not b or a == b:
            continue
        projected = (a != e.src) or (b != e.dst)
        if projected and e.type in ("IN_CHAT", "SENT", "HAS_MEDIA", "ATTACHMENT_OF", "HAS_RENDITION", "FORWARD_OF"):
            continue            # structural plumbing, already summarised by POSTED_IN / FORWARDS_FROM
        k = (a, e.type, b)
        r = agg.get(k)
        p = e.props
        if r is None:
            r = agg[k] = {"s": a, "t": b, "type": e.type, "group": grp, "status": e.status, "conf": e.conf,
                          "count": 0, "ev": 0, "t0": p.get("t_first"), "t1": p.get("t_last"), "proj": projected}
        r["count"] += int(p.get("count", 1))
        r["conf"] = max(r["conf"], e.conf)
        r["ev"] += len(p.get("evidence", []))
        if p.get("t_first") is not None:
            r["t0"] = min(x for x in (r["t0"], p["t_first"]) if x is not None)
        if p.get("t_last") is not None:
            r["t1"] = max(x for x in (r["t1"], p["t_last"]) if x is not None)
    deg: Counter = Counter()
    for r in agg.values():
        deg[r["s"]] += r["count"]
        deg[r["t"]] += r["count"]
    must = {k for k in deg if fam.get(k) in ("InferredGroup", "Person", "Community")}
    ranked = sorted(deg, key=lambda k: (k not in must, -deg[k], k))[:max_nodes]
    keep = set(ranked)
    nodes = []
    for k in ranked:
        n = g.nodes[k]
        f = fam[k]
        props = {pk: (pv[:160] + "…" if isinstance(pv, str) and len(pv) > 160 else pv) for pk, pv in n.props.items()
                 if isinstance(pv, (str, int, float, bool)) and pk not in ("text", "label")}
        nodes.append({"id": k, "name": _label(n), "fam": f, "deg": deg[k], "cls": _specific(n.labels),
                      "props": dict(list(props.items())[:14]),
                      "members": n.props.get("members") if isinstance(n.props.get("members"), list) else None})
    links = [r for r in agg.values() if r["s"] in keep and r["t"] in keep]
    # A huge inferred group would turn into one giant star: show its strongest members only (the full
    # membership stays in the findings table and the store).
    members_by_group: dict[str, list[dict]] = defaultdict(list)
    for r in links:
        if r["type"] == "IN_CLUSTER":
            members_by_group[r["t"]].append(r)
    for grp, rs in members_by_group.items():
        if len(rs) > MAX_GROUP_MEMBERS:
            drop = {id(r) for r in sorted(rs, key=lambda r: (-deg[r["s"]], r["s"]))[MAX_GROUP_MEMBERS:]}
            links = [r for r in links if id(r) not in drop]
    # Browsers stay smooth up to a few thousand edges: keep every node's strongest link, then derived
    # structure, then the heaviest observed links. The counts shown in the UI say what was left out.
    cap = max_links if max_links is not None else 4 * max_nodes
    if len(links) > cap:
        strength = lambda r: (r["status"] != "observed", r["count"], r["conf"])
        best: dict[str, dict] = {}
        for r in links:
            for k in (r["s"], r["t"]):
                if k not in best or strength(r) > strength(best[k]):
                    best[k] = r
        chosen = {id(r): r for r in best.values()}
        for r in sorted(links, key=strength, reverse=True):
            if len(chosen) >= cap:
                break
            chosen.setdefault(id(r), r)
        links = list(chosen.values())
    pos = _layout(ranked, links)
    for n in nodes:
        n["x"], n["y"] = pos.get(n["id"], (0.0, 0.0))
    return {"nodes": nodes, "links": links, "total_actors": len(deg), "total_links": len(agg)}


def _layout(keys: list[str], links: list[dict], seed: int = 7) -> dict[str, tuple[float, float]]:
    """Deterministic force layout (stable across filters and re-runs); the browser fits it to the canvas."""
    import networkx as nx

    G = nx.Graph()
    G.add_nodes_from(keys)
    for l in sorted(links, key=lambda l: (l["s"], l["type"], l["t"])):
        w = 1.0 + math.log1p(l["count"])
        if G.has_edge(l["s"], l["t"]):
            G[l["s"]][l["t"]]["weight"] += w
        else:
            G.add_edge(l["s"], l["t"], weight=w)
    if not len(G):
        return {}
    # Lay out each connected component on its own (sized by sqrt(n)), then pack them in rows: a single
    # spring layout flings components to the corners and leaves the canvas mostly empty.
    comps = sorted((sorted(c) for c in nx.connected_components(G)), key=lambda c: (-len(c), c[0]))
    placed = []
    for comp in comps:
        H = G.subgraph(comp)
        if len(H) == 1:
            local = {comp[0]: (0.0, 0.0)}
        else:
            local = nx.spring_layout(H, seed=seed, k=1.8 / math.sqrt(len(H)), iterations=200, weight="weight")
            if len(H) <= 200:   # Kamada-Kawai spaces dense cores by graph distance (O(n^2) per step: small only)
                try:
                    local = nx.kamada_kawai_layout(H, pos=local)
                except Exception:
                    pass
        xs, ys = [p[0] for p in local.values()], [p[1] for p in local.values()]
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
        r = 0.5 * math.sqrt(len(H))          # target radius grows with component size
        placed.append((r, {k: ((x - cx) / span * 2 * r, (y - cy) / span * 2 * r) for k, (x, y) in local.items()}))
    total_w = math.sqrt(sum((2 * r + 1) ** 2 for r, _ in placed)) * 1.3   # row width ~ square overall
    out, x, y, row_h = {}, 0.0, 0.0, 0.0
    for r, local in placed:
        d = 2 * r + 1
        if x > 0 and x + d > total_w:
            x, y, row_h = 0.0, y + row_h, 0.0
        for k, (lx, ly) in local.items():
            out[k] = (round((x + r + lx) * 100, 1), round((y + r + ly) * 100, 1))
        x, row_h = x + d, max(row_h, d)
    return out


def _schema(g: Graph) -> dict:
    cls_count: Counter = Counter()
    spec = {}
    for n in g.nodes.values():
        c = _specific(n.labels)
        spec[n.key] = c
        cls_count[c] += 1
    rel: Counter = Counter()
    for e in g.edges.values():
        rel[(spec.get(e.src, "?"), e.type, spec.get(e.dst, "?"))] += 1
    return {"classes": dict(cls_count), "relations": [[s, t, d, n] for (s, t, d), n in rel.items()]}


def _activity(times_by_chat: dict[str, list[int]], labels: dict[str, str], top: int = 5) -> dict:
    allt = [t for ts in times_by_chat.values() for t in ts]
    if not allt:
        return {"days": [], "series": [], "heat": []}
    d0, d1 = min(allt) // DAY, max(allt) // DAY
    if d1 - d0 > 3650:                       # bad timestamps: clamp to the last 10 years of data
        d0 = d1 - 3650
    days = list(range(d0, d1 + 1))
    tops = sorted(times_by_chat, key=lambda c: -len(times_by_chat[c]))[:top]
    series = []
    for c in tops + ["__other__"]:
        ts = times_by_chat.get(c, []) if c != "__other__" else \
            [t for k, v in times_by_chat.items() if k not in tops for t in v]
        cnt = Counter(t // DAY for t in ts)
        if c == "__other__" and not ts:
            continue
        series.append({"name": labels.get(c, c) if c != "__other__" else "Other chats", "key": c,
                       "data": [cnt.get(d, 0) for d in days]})
    heat = Counter()
    for t in allt:
        dt = datetime.fromtimestamp(t, tz=timezone.utc)
        heat[(dt.weekday(), dt.hour)] += 1
    return {"days": [datetime.fromtimestamp(d * DAY, tz=timezone.utc).strftime("%Y-%m-%d") for d in days],
            "series": series, "heat": [[h, w, n] for (w, h), n in heat.items()]}


def _hierarchy(class_counts: dict[str, int]) -> list[dict]:
    """Ontology class tree (by layer) with direct instance counts; every declared class is included."""
    children: dict[str, list[str]] = defaultdict(list)
    roots_by_layer: dict[str, list[str]] = defaultdict(list)
    for c in O.CLASSES.values():
        if c.parents:
            children[c.parents[0]].append(c.name)
        else:
            roots_by_layer[c.layer].append(c.name)

    def node(name: str) -> dict:
        cd = O.CLASSES[name]
        kids = [node(k) for k in sorted(children.get(name, []))]
        own = class_counts.get(name, 0)
        total = own + sum(k["total"] for k in kids)
        d = {"name": name, "own": own, "total": total, "layer": cd.layer, "status": cd.epistemic,
             "align": list(cd.align), "doc": cd.doc}
        if kids:
            d["children"] = kids
        return d

    out = []
    for layer in ["Core", "Content", "Network", "Economy", "Derived", "Provenance"]:
        kids = [node(r) for r in sorted(roots_by_layer.get(layer, []))]
        if kids:
            out.append({"name": layer, "layer": layer, "own": 0, "total": sum(k["total"] for k in kids),
                        "children": kids})
    return out


def collect(g: Graph, findings: list | None = None, summary: dict | None = None, *, max_nodes: int = 500,
            title: str = "TGO data dashboard") -> dict:
    """Aggregate a :class:`~tgo.graph.Graph` into the dashboard payload."""
    fam = {k: _family(n.labels) for k, n in g.nodes.items()}
    fam_count = Counter(fam.values())
    flows: Counter = Counter()
    for e in g.edges.values():
        flows[(fam.get(e.src, "other"), e.type, fam.get(e.dst, "other"), e.status, _cbin(e.conf))] += \
            int(e.props.get("count", 1))
    times_by_chat: dict[str, list[int]] = defaultdict(list)
    for m in g.by_label("Message"):
        t = m.props.get("date")
        if isinstance(t, (int, float)) and t > 0:
            times_by_chat[_chat_of(g, m.key) or "?"].append(int(t))
    chat_labels = {k: _label(g.nodes[k]) for k in times_by_chat if k in g.nodes}
    sch = _schema(g)
    data = _payload(title, "memory", fam_count, flows, _activity(times_by_chat, chat_labels), sch,
                    _network(g, fam, max_nodes), findings, summary, relations=len(g.edges),
                    messages=len(g.by_label("Message")))
    data.update(_findings(g))
    data["top_chats"] = [{"key": k, "name": chat_labels.get(k, k), "n": len(v)}
                         for k, v in sorted(times_by_chat.items(), key=lambda kv: -len(kv[1]))[:15] if k != "?"]
    return data


def _findings(g: Graph) -> dict:
    """Inferred groups, identity candidates and most-mentioned identifiers of a graph."""
    data = {}
    data["clusters"] = [{"key": n.key, "name": _label(n), "kind": _specific(n.labels),
                         "size": n.props.get("size") or len(n.props.get("members") or []),
                         "algorithm": n.props.get("algorithm"), "stability": n.props.get("stabilityScore"),
                         "quality": n.props.get("qualityScore"), "members": [
                             {"key": m, "name": _label(g.nodes[m]) if m in g.nodes else m}
                             for m in (n.props.get("members") or [])][:50]}
                        for n in g.by_label("InferredGroup")]
    data["identity"] = sorted(({"a": e.src, "b": e.dst, "an": _label(g.nodes[e.src]), "bn": _label(g.nodes[e.dst]),
                                "score": e.conf, "judgement": e.props.get("judgement", "no_judgement"),
                                "features": e.props.get("features", []), "decidedBy": e.props.get("decidedBy")}
                               for e in g.by_type("CANDIDATE_SAME_PERSON")), key=lambda r: -r["score"])[:200]
    ident = Counter()
    for e in g.by_type("CONTAINS_IDENTIFIER"):
        ident[e.dst] += 1
    data["top_identifiers"] = [{"key": k, "name": _label(g.nodes[k]), "cls": _specific(g.nodes[k].labels), "n": n}
                               for k, n in ident.most_common(15)]
    return data


# ===================================================================== duckdb engine
def collect_store(store, case: Graph | None = None, findings: list | None = None, summary: dict | None = None, *,
                  max_nodes: int = 500, title: str = "TGO data dashboard") -> dict:
    """Corpus-wide totals, flows and activity from SQL; network and schema graph from the case slice."""
    c = store.con
    labsets = {tuple(r[0]): r[1] for r in c.execute("SELECT labels, count(*) FROM nodes GROUP BY 1").fetchall()}
    fam_count: Counter = Counter()
    for labs, n in labsets.items():
        fam_count[_family(set(labs))] += n
    flows: Counter = Counter()
    for sl, t, dl, st, b, n in c.execute("""
            SELECT s.labels, e.type, d.labels, e.status, least(9, greatest(0, floor(e.conf * 10)))::INT, sum(e.count)
            FROM edges e JOIN nodes s ON s.key = e.src JOIN nodes d ON d.key = e.dst
            GROUP BY ALL""").fetchall():
        flows[(_family(set(sl)), t, _family(set(dl)), st, int(b))] += int(n)
    times_by_chat: dict[str, list[int]] = defaultdict(list)
    for chat, t in c.execute("""
            SELECT e.dst, TRY_CAST(json_extract(m.props, '$.date') AS BIGINT) AS t
            FROM nodes m JOIN edges e ON e.src = m.key AND e.type = 'IN_CHAT'
            WHERE list_contains(m.labels, 'Message') AND t IS NOT NULL AND t > 0""").fetchall():
        times_by_chat[chat].append(int(t))
    labels = {k: v for k, v in c.execute(
        "SELECT key, coalesce(props->>'$.label', props->>'$.title', props->>'$.collectorDisplayName', key) FROM nodes WHERE key IN (SELECT unnest(?))",
        [list(times_by_chat)]).fetchall()}
    case = case or Graph()
    fam = {k: _family(n.labels) for k, n in case.nodes.items()}
    sch = _schema(case)
    sch["classes_corpus"] = dict(Counter({_specific(set(l)): 0 for l in labsets}) + Counter(
        {_specific(set(l)): n for l, n in labsets.items()}))
    data = _payload(title, "duckdb", fam_count, flows, _activity(times_by_chat, labels), sch,
                    _network(case, fam, max_nodes), findings, summary,
                    relations=c.execute("SELECT count(*) FROM edges").fetchone()[0],
                    messages=c.execute("SELECT count(*) FROM nodes WHERE list_contains(labels, 'Message')").fetchone()[0])
    data["hierarchy"] = _hierarchy(sch["classes_corpus"])
    data.update(_findings(case))
    data["top_chats"] = [{"key": k, "name": labels.get(k, k), "n": len(v)}
                         for k, v in sorted(times_by_chat.items(), key=lambda kv: -len(kv[1]))[:15]]
    data["meta"]["scope_note"] = "Totals, flows and activity: whole corpus. Network and schema graph: case slice."
    return data


# ===================================================================== shared
def _payload(title, engine, fam_count, flows, activity, schema, network, findings, summary,
             relations: int | None = None, messages: int | None = None) -> dict:
    sev = Counter(f["severity"] if isinstance(f, dict) else f.severity for f in (findings or []))
    rules = Counter((f["rule"], f["severity"]) if isinstance(f, dict) else (f.rule, f.severity)
                    for f in (findings or []))
    status_tot = Counter()
    for (sf, t, df, st, b), n in flows.items():
        status_tot[st] += n
    kpi = {
        "accounts": fam_count.get("Account", 0), "chats": fam_count.get("Chat", 0) + fam_count.get("Community", 0),
        # by class, not family: the Message family also holds attachments and stories
        "messages": messages if messages is not None else fam_count.get("Message", 0), "media": fam_count.get("Media", 0),
        "identifiers": fam_count.get("Identifier", 0),
        "clusters": fam_count.get("InferredGroup", 0), "persons": fam_count.get("Person", 0),
        "nodes": sum(fam_count.values()), "observations": sum(flows.values()),
        "relations": relations if relations is not None else len(flows),
        "derived": sum(n for s, n in status_tot.items() if s != "observed"),
        "violations": sev.get("violation", 0), "warnings": sev.get("warning", 0),
    }
    return {
        "meta": {"title": title, "engine": engine, "generated": int(time.time()),
                 "source": (summary or {}).get("sources"), "scope_note": ""},
        "kpi": kpi,
        "families": dict(fam_count),
        "flows": [[sf, t, df, st, b, n] for (sf, t, df, st, b), n in flows.items()],
        "activity": activity,
        "schema": schema,
        "hierarchy": _hierarchy(schema["classes"]),
        "network": network,
        "rules": [[r, s, n] for (r, s), n in sorted(rules.items())],
        "style": {"familySlot": FAMILY_SLOT, "familyShape": FAMILY_SHAPE, "layerSlot": LAYER_SLOT,
                  "groups": {g: {"name": name, "slot": tok} for g, (tok, name, _) in EDGE_GROUPS.items()},
                  "typeGroup": {t: _group(t) for t in {f[1] for f in flows} | {l["type"] for l in network["links"]}},
                  "hiddenGroups": sorted(HIDDEN_GROUPS), "statuses": STATUSES,
                  "classLayer": {c.name: c.layer for c in O.CLASSES.values()},
                  "relDoc": {r.name: r.doc for r in O.RELATIONS.values() if r.doc}},
    }


def build_dashboard(out_dir: str | Path, *, path: str | Path | None = None, max_nodes: int = 500,
                    title: str | None = None) -> Path:
    """Write ``dashboard.html`` for a TGO output folder (memory or DuckDB engine output)."""
    out = Path(out_dir)
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8")) if (out / "summary.json").exists() else {}
    findings = json.loads((out / "validation.json").read_text(encoding="utf-8")) \
        if (out / "validation.json").exists() else []
    name = title or f"TGO · {out.resolve().name}"
    if (out / "tgo.duckdb").exists() and summary.get("engine") == "duckdb":
        from ..scale.store import Store

        case = Graph.load(out / "case_graph.json") if (out / "case_graph.json").exists() else None
        store = Store(out / "tgo.duckdb", allowed_directories=[out])
        try:
            data = collect_store(store, case, findings, summary, max_nodes=max_nodes, title=name)
        finally:
            store.con.close()
    elif (out / "graph.json").exists():
        data = collect(Graph.load(out / "graph.json"), findings, summary, max_nodes=max_nodes, title=name)
    else:
        raise SystemExit(f"no TGO output in {out} (expected graph.json, or tgo.duckdb + summary.json)")
    data["meta"]["hasReport"] = (out / "report.html").exists()
    return write_html(data, Path(path) if path else out / "dashboard.html")


def write_html(data: dict, path: Path) -> Path:
    tpl = (HERE / "template.html").read_text(encoding="utf-8")
    js = (VENDOR / "echarts.min.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str).replace("<", "\\u003c")
    notice = (VENDOR / "NOTICE-echarts.txt").read_text(encoding="utf-8") if (VENDOR / "NOTICE-echarts.txt").exists() else ""
    js = "/*! Apache ECharts NOTICE (Apache-2.0 section 4(d)):\n" + notice.replace("*/", "* /") + "*/\n" + js
    html = (tpl.replace("/*__ECHARTS__*/", js, 1)
               .replace("__TGO_DASH__", payload, 1)
               .replace("__TITLE__", data["meta"]["title"].replace("&", "&amp;").replace("<", "&lt;")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
