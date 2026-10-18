"""A provenance-aware property graph with MERGE semantics.

This is a deliberately small, dependency-free store that mirrors what the
pipeline would write to Neo4j / Fabric Graph:

* nodes are keyed deterministically (see :mod:`tgo.keys`) and merged, so
  multiple sources (Desktop export, MTProto, Bot API) describing the same
  entity collapse into one node;
* every edge carries the mandatory TGO fields ``status, conf, method,
  evidence, run`` plus ``scope`` (who could see it) and ``observed_by``
  (whose view it is, for viewer-relative facts);
* repeated observations of the same edge increment ``count`` and widen the
  ``[t_first, t_last]`` interval instead of creating duplicates;
* per-field visibility (``tg:FieldVisibility``) is kept on the node, so
  "hidden by privacy" is distinguishable from "not set".
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from . import ontology as O

MAX_EVIDENCE = 50


@dataclass
class Node:
    key: str
    labels: set[str] = field(default_factory=set)
    props: dict = field(default_factory=dict)
    sources: set[str] = field(default_factory=set)
    fields: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"key": self.key, "labels": sorted(self.labels), "props": self.props,
                "sources": sorted(self.sources), "fields": self.fields}


@dataclass
class Edge:
    id: str
    type: str
    src: str
    dst: str
    props: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        return self.props.get("status", "observed")

    @property
    def conf(self) -> float:
        return float(self.props.get("conf", 1.0))

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "src": self.src, "dst": self.dst, "props": self.props}


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, Edge] = {}
        self._out: dict[str, set[str]] = {}
        self._in: dict[str, set[str]] = {}
        self._run_stack: list[str] = []
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ nodes
    def node(self, key: str, *labels: str, run: str | None = None, **props) -> Node:
        n = self.nodes.get(key)
        if n is None:
            n = self.nodes[key] = Node(key)
        for lab in labels:
            if lab not in O.CLASSES:
                self._warn(f"label '{lab}' is not declared in the ontology")
            n.labels.add(lab)
            n.labels.update(O.ancestors(lab))
        for k, v in props.items():
            if v is None:
                continue
            n.props[k] = v
        r = run or self.current_run
        if r:
            n.sources.add(r)
        return n

    def has(self, key: str) -> bool:
        return key in self.nodes

    def field(self, key: str, name: str, value, visibility: O.Visibility | str, *,
              source: str, observed_by: str | None = None) -> None:
        """Record a field observation, including *why* it may be absent."""
        n = self.node(key)
        vis = visibility.value if isinstance(visibility, O.Visibility) else visibility
        rec = {"visibility": vis, "source": source}
        if observed_by:
            rec["observed_by"] = observed_by
        prev = n.fields.get(name)
        # A visible observation beats a hidden one; otherwise keep the first.
        if prev is None or (vis == "visible" and prev["visibility"] != "visible"):
            n.fields[name] = rec
        if vis == "visible" and value not in (None, ""):
            n.props[name] = value

    # ------------------------------------------------------------------ edges
    def edge(self, type_: str, src: str, dst: str, *, status: str | None = None,
             conf: float | None = None, method: str | None = None,
             evidence: Iterable[str] = (), run: str | None = None,
             scope: str | None = None, observed_by: str | None = None,
             t: int | None = None, discriminator: str | None = None, **props) -> Edge:
        rel = O.RELATIONS.get(type_)
        if rel is None:
            self._warn(f"relationship '{type_}' is not declared in the ontology")
        if src not in self.nodes:
            self.node(src)
        if dst not in self.nodes:
            self.node(dst)
        eid = f"{src}-[{type_}]->{dst}" + (f"#{discriminator}" if discriminator else "")
        status = status or (rel.status if rel else "observed")
        r = run or self.current_run
        e = self.edges.get(eid)
        if e is None:
            e = self.edges[eid] = Edge(eid, type_, src, dst, {
                "status": status,
                "conf": 1.0 if conf is None and status == "observed" else (conf if conf is not None else 0.5),
                "method": method or ("platform" if status == "observed" else "unspecified"),
                "evidence": [],
                "count": 0,
            })
            self._out.setdefault(src, set()).add(eid)
            self._in.setdefault(dst, set()).add(eid)
        p = e.props
        p["count"] += 1
        if conf is not None:
            p["conf"] = max(float(p["conf"]), float(conf)) if p["count"] > 1 else float(conf)
        if method:
            p["method"] = method
        if r:
            runs = p.setdefault("runs", [])
            if r not in runs:
                runs.append(r)
        if scope:
            p["scope"] = scope
            if scope in O.ELEVATED_SCOPES:
                p["sensitivity"] = "elevated"
        if observed_by:
            p["observed_by"] = observed_by
        if t is not None:
            t = int(t)
            p["t_first"] = min(p.get("t_first", t), t)
            p["t_last"] = max(p.get("t_last", t), t)
        for ev in evidence:
            if ev not in p["evidence"] and len(p["evidence"]) < MAX_EVIDENCE:
                p["evidence"].append(ev)
        for k, v in props.items():
            if v is not None:
                p[k] = v
        return e

    def out_edges(self, key: str, type_: str | None = None) -> Iterator[Edge]:
        for eid in sorted(self._out.get(key, ())):
            e = self.edges[eid]
            if type_ is None or e.type == type_:
                yield e

    def in_edges(self, key: str, type_: str | None = None) -> Iterator[Edge]:
        for eid in sorted(self._in.get(key, ())):
            e = self.edges[eid]
            if type_ is None or e.type == type_:
                yield e

    def by_label(self, label: str) -> list[Node]:
        return [n for n in self.nodes.values() if label in n.labels]

    def by_type(self, type_: str) -> list[Edge]:
        return [e for e in self.edges.values() if e.type == type_]

    def remove_edge(self, eid: str) -> None:
        e = self.edges.pop(eid, None)
        if e:
            self._out.get(e.src, set()).discard(eid)
            self._in.get(e.dst, set()).discard(eid)

    # ------------------------------------------------------------ provenance
    @property
    def current_run(self) -> str | None:
        return self._run_stack[-1] if self._run_stack else None

    @contextmanager
    def activity(self, kind: str, key: str, *, agent: dict | None = None, **params):
        """A prov:Activity (CollectionRun / DetectionRun) wrapping all writes inside it.

        ``agent`` describes the tool (tool, version, model, code/weights licence)
        and becomes a ModelAgent node linked with ASSOCIATED_WITH.
        """
        run_key = f"run:{key}"
        self.node(run_key, kind, run=None, startedAt=int(time.time()),
                  parameters=json.dumps(params, sort_keys=True, default=str) if params else None)
        if agent:
            akey = "agent:" + agent.get("tool", "unknown") + ":" + str(agent.get("version", ""))
            self.node(akey, "ModelAgent", **agent)
            self._run_stack.append(run_key)
            try:
                self.edge("ASSOCIATED_WITH", run_key, akey)
            finally:
                self._run_stack.pop()
        self._run_stack.append(run_key)
        try:
            yield run_key
        finally:
            self._run_stack.pop()
            self.nodes[run_key].props["endedAt"] = int(time.time())

    # ------------------------------------------------------------- utilities
    def _warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def stats(self) -> dict:
        labels: dict[str, int] = {}
        for n in self.nodes.values():
            fam = O.family(n.labels) if n.labels else "unlabelled"
            labels[fam] = labels.get(fam, 0) + 1
        types: dict[str, int] = {}
        status: dict[str, int] = {}
        for e in self.edges.values():
            types[e.type] = types.get(e.type, 0) + 1
            status[e.status] = status.get(e.status, 0) + 1
        return {"nodes": len(self.nodes), "edges": len(self.edges),
                "node_families": dict(sorted(labels.items())),
                "edge_types": dict(sorted(types.items())), "edge_status": status}

    def to_dict(self) -> dict:
        return {"nodes": [n.to_dict() for n in self.nodes.values()],
                "edges": [e.to_dict() for e in self.edges.values()]}

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1, default=str), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Graph":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        g = cls()
        for nd in data["nodes"]:
            g.nodes[nd["key"]] = Node(nd["key"], set(nd["labels"]), nd["props"],
                                      set(nd.get("sources", [])), nd.get("fields", {}))
        for ed in data["edges"]:
            e = g.edges[ed["id"]] = Edge(ed["id"], ed["type"], ed["src"], ed["dst"], ed["props"])
            g._out.setdefault(e.src, set()).add(e.id)
            g._in.setdefault(e.dst, set()).add(e.id)
        return g

    def to_networkx(self, edge_types: set[str] | None = None):
        import networkx as nx

        G = nx.MultiDiGraph()
        for n in self.nodes.values():
            G.add_node(n.key, labels=",".join(sorted(n.labels)), **{
                k: v for k, v in n.props.items() if isinstance(v, (str, int, float, bool))})
        for e in self.edges.values():
            if edge_types and e.type not in edge_types:
                continue
            G.add_edge(e.src, e.dst, key=e.id, type=e.type, status=e.status, conf=e.conf,
                       count=e.props.get("count", 1))
        return G
