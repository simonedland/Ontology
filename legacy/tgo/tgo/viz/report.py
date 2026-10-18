"""Self-contained, offline HTML investigation viewer.

One file, no network: Cytoscape.js is inlined from ``vendor/``, data is
embedded as a JSON script tag (``<`` escaped), all rendering uses
``textContent`` (names are attacker-controlled text), system fonts only, no
map tiles.

Encodings (TGO report, Guo/Huang/Laidlaw 2015):
  edge hue        = relation group          solid / dashed / dotted = observed / inferred / file-declared
  edge opacity    = confidence (4 bins)     edge width = event count (log)
  node shape+hue  = ontology family         avatars blurred until clicked
"""
from __future__ import annotations

import base64
import io
import json
import math
from collections import defaultdict
from pathlib import Path

from .. import ontology as O
from ..graph import Graph

HERE = Path(__file__).parent

FAMILY_STYLE = {  # family -> (shape, colour token, base size, display name)
    "Account": ("ellipse", "s1", 20, "Account"),
    "Chat": ("round-rectangle", "s2", 20, "Chat / channel"),
    "Community": ("hexagon", "s2", 40, "Community"),
    "InferredGroup": ("octagon", "s7", 36, "Inferred group"),
    "Group": ("octagon", "s7", 30, "Group"),
    "Person": ("diamond", "s5", 34, "Person cluster"),
    "Message": ("ellipse", "muted", 7, "Message"),
    "Media": ("rectangle", "s3", 16, "Media / file"),
    "Rendition": ("rectangle", "muted", 7, "Photo rendition"),
    "Identifier": ("tag", "s4", 18, "Identifier"),
    "Device": ("triangle", "s6", 20, "Device / geo"),
    "Extracted": ("barrel", "s3", 11, "Extracted content"),
    "Snapshot": ("ellipse", "muted", 9, "Profile / annotation"),
    "Judgement": ("diamond", "muted", 10, "Identity judgement"),
    "Membership": ("ellipse", "muted", 8, "Membership"),
    "Run": ("vee", "muted", 10, "Run / agent"),
    "Message_": ("ellipse", "muted", 7, "Message"),
    "other": ("ellipse", "muted", 8, "Other"),
}

EDGE_GROUPS = {
    "social": ("s1", "Person-to-person", ["KNOWS", "CLOSE_FRIEND_OF", "GAVE_GIFT_TO", "REPOSTED", "FEATURES",
                                          "DELEGATES_TO", "VERIFIES", "INVITED_BY", "APPROVED_BY", "REACTED", "MENTIONS"]),
    "coordination": ("s2", "Propagation & coordination", [
        "FORWARDS_FROM", "IDENTICAL_POST", "NEAR_DUP_POST", "SYNC_POST", "CO_FORWARD", "CO_URL", "CO_FINGERPRINT",
        "SAME_CREATION_DATE", "NAME_HISTORY_LINK", "SHARED_DOMAIN_PROMOTION", "FORWARD_OF"]),
    "extraction": ("s3", "Media & extraction", [
        "FINGERPRINT_OF", "NEAR_DUPLICATE_OF", "SAME_FILE_AS", "HAS_TRANSCRIPT", "HAS_SEGMENT", "HAS_TEXT",
        "HAS_METADATA", "CONTAINS_IDENTIFIER", "MENTIONS_ENTITY", "CAPTURED_WITH", "HAS_EXIF_GPS",
        "SAME_DOCUMENT_LINEAGE"]),
    "membership": ("s4", "Membership & groups", ["MEMBER_OF", "ADMIN_OF", "BOOSTED", "MEMBER_OF_COMMUNITY",
                                                   "LINKED_DISCUSSION", "IN_CLUSTER", "CREATED"]),
    "identity": ("s5", "Identity judgements", ["CANDIDATE_SAME_PERSON", "OPERATED_BY"]),
    "content": ("muted", "Content structure", []),
    "provenance": ("muted", "Provenance", ["GENERATED_BY", "USED", "ASSOCIATED_WITH"]),
}
_TYPE_GROUP = {t: g for g, (_, _, ts) in EDGE_GROUPS.items() for t in ts}

DIRECTED = {"SENT", "IN_CHAT", "POSTED_IN", "REPLIES_TO", "FORWARD_OF", "FORWARDS_FROM", "MENTIONS", "REACTED",
            "HAS_MEDIA", "ATTACHMENT_OF", "GAVE_GIFT_TO", "REPOSTED", "FEATURES", "MEMBER_OF", "ADMIN_OF",
            "INVITED_BY", "MEMBER_OF_COMMUNITY", "CONTAINS_IDENTIFIER", "CAPTURED_WITH", "HAS_EXIF_GPS",
            "OPERATED_BY", "IN_CLUSTER", "CREATED", "KNOWS", "HAS_AVATAR", "HAS_TRANSCRIPT", "HAS_METADATA",
            "HAS_TEXT", "FINGERPRINT_OF", "OWNED_BY", "HAS_USERNAME", "DELEGATES_TO"}


VIEWS = {
    "Actors": {"fams": ["Account", "Chat", "Community", "InferredGroup", "Group", "Person"],
               "groups": ["social", "coordination", "membership", "identity", "content"]},
    "Coordination": {"fams": ["Chat", "InferredGroup", "Identifier"], "groups": ["coordination", "membership"]},
    "Identity": {"fams": ["Account", "Person", "Identifier", "Device", "Media", "Judgement"],
                 "groups": ["identity", "extraction", "social", "content"]},
    "Content": {"fams": ["Account", "Chat", "Message", "Media", "Identifier", "Device", "Extracted"],
                "groups": ["content", "extraction", "coordination", "social"]},
    "Everything": {"fams": [k for k in FAMILY_STYLE if not k.endswith("_") and k != "Run"],
                   "groups": [k for k in EDGE_GROUPS if k != "provenance"]},
}


def view_layouts(g: Graph, size: float = 1000.0, keep: set | None = None) -> dict:
    """Per-view positions: Kamada-Kawai on the collapsed simple graph of what the view shows."""
    import networkx as nx

    out = {}
    fam = {k: _family(n.labels) for k, n in g.nodes.items()}
    for name, v in VIEWS.items():
        fams, groups = set(v["fams"]), set(v["groups"])
        G = nx.Graph()
        for e in sorted(g.edges.values(), key=lambda e: e.id):
            if e.src == e.dst or _TYPE_GROUP.get(e.type, "content") not in groups:
                continue
            if fam[e.src] in fams and fam[e.dst] in fams:
                G.add_edge(e.src, e.dst)
        if keep is not None:
            G = G.subgraph([k for k in G if k in keep]).copy()
        if not G.number_of_nodes():
            continue
        n = G.number_of_nodes()
        try:
            if n <= 600:
                pos = nx.kamada_kawai_layout(G)
            elif n <= 3000:
                pos = nx.spring_layout(G, seed=7, k=2.0 / n ** 0.5, iterations=60)
            else:
                continue   # browser falls back to cose on the (filtered) visible set
        except Exception:
            pos = nx.spring_layout(G, seed=7, k=2.0 / n ** 0.5, iterations=60)
        out[name] = {k: [round(float(x) * size, 1), round(float(y) * size, 1)] for k, (x, y) in pos.items()}
    return out


def _family(labels: set[str]) -> str:
    fam = O.family(labels) if labels else "other"
    return fam if fam in FAMILY_STYLE else "other"


def _label(n) -> str:
    p = n.props
    for k in ("label", "title", "usernameValue", "value", "name", "fileName", "collectorDisplayName"):
        if p.get(k):
            v = str(p[k])
            return ("@" + v) if k == "usernameValue" else v
    return n.key


def _thumb(path: str, size: int = 112) -> str | None:
    try:
        from PIL import Image

        im = Image.open(path)
        im.seek(0)
        im = im.convert("RGB")
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=72)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _burst_matrix(g: Graph) -> dict | None:
    """Delay (seconds) between each originator post and each actor's first matching share."""
    from ..analysis.coordination import build_shares

    origins = [n.key for n in g.by_label("Chat") if "originator" in (n.props.get("networkRoles") or [])]
    if not origins:
        return None
    shares = build_shares(g)
    by_obj = defaultdict(list)
    by_content = defaultdict(list)
    for s in shares:
        by_obj[s.object_id].append(s)
        by_content[s.content_id].append(s)
    out = []
    for o in origins:
        posts = sorted((e.src for e in g.in_edges(o, "IN_CHAT") if g.nodes[e.src].props.get("date")),
                       key=lambda k: g.nodes[k].props["date"])
        cols, rows = [], defaultdict(dict)
        for i, mk in enumerate(posts):
            t0 = g.nodes[mk].props["date"]
            objs = {s.object_id for s in by_content[mk] if s.kind != "simhash"} | {"fwd:" + mk}
            hits = {}
            for ob in objs:
                for s in by_obj.get(ob, []):
                    if s.account_id == o or not (-60 <= s.ts - t0 <= 6 * 3600):
                        continue
                    d = s.ts - t0
                    if s.account_id not in hits or d < hits[s.account_id][0]:
                        hits[s.account_id] = (d, s.content_id, s.kind)
            if not hits:
                continue
            cols.append({"key": mk, "t": t0, "label": (g.nodes[mk].props.get("text") or mk)[:80]})
            for acct, (d, ck, kind) in hits.items():
                rows[acct][len(cols) - 1] = {"d": d, "c": ck, "k": kind}
        if cols:
            out.append({"origin": o, "originLabel": _label(g.nodes[o]), "cols": cols,
                        "rows": [{"key": a, "label": _label(g.nodes[a]), "cells": cells}
                                 for a, cells in sorted(rows.items(), key=lambda kv: -len(kv[1]))]})
    return {"matrices": out} if out else None


CONTENT_FAMILIES = {"Message", "Rendition", "Extracted", "Snapshot", "Membership"}


def select_nodes(g: Graph, max_nodes: int) -> set[str]:
    """Everything for small graphs.  For large ones: all actor/derived nodes, then content nodes
    that are evidence for derived edges, then the highest-degree rest, up to ``max_nodes``."""
    if len(g.nodes) <= max_nodes:
        return set(g.nodes)
    keep = {k for k, n in g.nodes.items() if _family(n.labels) not in CONTENT_FAMILIES}
    ev = []
    for e in g.edges.values():
        if e.status != "observed":
            ev.extend(x for x in e.props.get("evidence", []) if x in g.nodes)
    for k in ev:
        if len(keep) >= max_nodes:
            break
        keep.add(k)
    if len(keep) < max_nodes:
        deg = defaultdict(int)
        for e in g.edges.values():
            deg[e.src] += 1
            deg[e.dst] += 1
        for k in sorted((k for k in g.nodes if k not in keep), key=lambda k: -deg[k])[: max_nodes - len(keep)]:
            keep.add(k)
    return keep


def build_data(g: Graph, findings, report: dict, positions: dict, max_nodes: int = 6000) -> dict:
    """Assemble the JSON payload the viewer embeds (nodes, edges, runs, findings, views, ...)."""
    keep = select_nodes(g, max_nodes)
    deg = defaultdict(int)
    for e in g.edges.values():
        deg[e.src] += 1
        deg[e.dst] += 1
    nodes = []
    for n in g.nodes.values():
        if n.key not in keep:
            continue
        fam = _family(n.labels)
        shape, color, base, _ = FAMILY_STYLE[fam]
        size = base * (1 + 0.08 * math.log1p(deg[n.key])) if fam in ("Account", "Chat") else base
        props = {}
        for k, v in n.props.items():
            if k in ("localPath",) or v is None:
                continue
            if isinstance(v, str) and len(v) > 4000:
                v = v[:4000] + " …"
            props[k] = v
        d = {"id": n.key, "label": _label(n)[:60], "fam": fam, "labels": sorted(n.labels), "props": props,
             "fields": n.fields, "sources": sorted(n.sources), "shape": shape, "color": color,
             "size": round(size, 1), "deg": deg[n.key]}
        if n.key in positions:
            d["x"], d["y"] = positions[n.key]
        t = n.props.get("date") or n.props.get("createdAt") or n.props.get("observedAt")
        if isinstance(t, int):
            d["t"] = t
        lp = n.props.get("localPath")
        if lp and (str(n.props.get("mime", "")).startswith("image/") or "Photo" in n.labels or "Sticker" in n.labels):
            d["thumb"] = _thumb(lp)
        nodes.append(d)
    edges = []
    for e in g.edges.values():
        if e.src not in keep or e.dst not in keep:
            continue
        grp = _TYPE_GROUP.get(e.type, "content")
        p = dict(e.props)
        conf = float(p.get("conf", 1.0))
        edges.append({"id": e.id, "source": e.src, "target": e.dst, "type": e.type, "group": grp,
                      "color": EDGE_GROUPS[grp][0], "status": e.status, "conf": conf,
                      "opacity": 1.0 if conf >= 0.9 else 0.75 if conf >= 0.7 else 0.5 if conf >= 0.5 else 0.3,
                      "width": round(1.2 + 1.1 * math.log1p(p.get("count", 1)), 2),
                      "style": {"inferred": "dashed", "file_declared": "dotted"}.get(e.status, "solid"),
                      "arrow": "triangle" if e.type in DIRECTED else "none",
                      "t": p.get("t_first"), "t2": p.get("t_last"), "props": p})
    runs = []
    for n in g.nodes.values():
        if n.labels & {"CollectionRun", "DetectionRun"}:
            agent = next((g.nodes[e.dst].props for e in g.out_edges(n.key, "ASSOCIATED_WITH")), {})
            runs.append({"key": n.key, "kind": "CollectionRun" if "CollectionRun" in n.labels else "DetectionRun",
                         "props": n.props, "agent": agent,
                         "edges": sum(1 for e in g.edges.values() if n.key in e.props.get("runs", []))})
    return {
        "nodes": nodes, "edges": edges, "runs": runs,
        "findings": [f.__dict__ for f in findings], "report": report,
        "families": {k: {"shape": v[0], "color": v[1], "name": v[3]} for k, v in FAMILY_STYLE.items()
                     if not k.endswith("_")},
        "edgeGroups": {k: {"color": v[0], "name": v[1], "types": v[2]} for k, v in EDGE_GROUPS.items()},
        "classes": [{"name": c.name, "parents": list(c.parents), "align": list(c.align), "layer": c.layer,
                     "epistemic": c.epistemic, "doc": c.doc, "family": c.family} for c in O.CLASSES.values()],
        "relations": [{"name": r.name, "tier": r.tier, "status": r.status, "doc": r.doc,
                       "group": _TYPE_GROUP.get(r.name, "content"), "ftm": r.ftm} for r in O.RELATIONS.values()],
        "notObtainable": O.NOT_OBTAINABLE,
        "burst": _burst_matrix(g),
        "views": VIEWS,
        "viewPos": view_layouts(g, keep=keep),
        "omitted": {"nodes": len(g.nodes) - len(keep), "note": "content nodes beyond the viewer cap; full graph in graph.json / neo4j.cypher"},
    }


def build_report(g: Graph, findings, report: dict, positions: dict, out_path: str | Path,
                 title: str = "TGO investigation view", max_nodes: int = 6000) -> Path:
    """Write the single-file offline viewer to ``out_path`` and return it."""
    data = build_data(g, findings, report, positions, max_nodes=max_nodes)
    tpl = (HERE / "template.html").read_text(encoding="utf-8")
    js = (HERE / "vendor" / "cytoscape.min.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str).replace("<", "\\u003c")
    html = (tpl.replace("/*__CYTOSCAPE__*/", js, 1)
               .replace("__TGO_DATA__", payload, 1)
               .replace("__TITLE__", title.replace("<", "&lt;"), 2))
    out_path = Path(out_path)
    out_path.write_text(html, encoding="utf-8")
    return out_path
