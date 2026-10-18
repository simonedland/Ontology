"""Community detection over the actor graph, with stability and network roles.

Louvain (NetworkX, BSD-3) is run with several seeds.  For each cluster of the
reference run we store ``stabilityScore`` (mean best-match Jaccard across the
other runs) and per member ``coAssignmentFrequency`` (share of runs in which
the member landed in the matched cluster).  Only clusters at or above
``min_stability`` are promoted (TGO design proposal).

Roles (Bovet & Grindrod / DFRLab vocabulary) from the FORWARDS_FROM graph:
originator (heavily forwarded), amplifier (forwards a lot), bridge (high
betweenness), hub (high degree).
"""
from __future__ import annotations

from .. import keys as K
from ..graph import Graph

ACTOR_EDGES = {"FORWARDS_FROM": 1.0, "IDENTICAL_POST": 1.0, "SYNC_POST": 1.0, "CO_FORWARD": 0.8,
               "NEAR_DUP_POST": 0.6, "CO_URL": 0.5, "CO_FINGERPRINT": 0.5, "MEMBER_OF_COMMUNITY": 0.7,
               "LINKED_DISCUSSION": 0.7, "POSTED_IN": 0.3, "GAVE_GIFT_TO": 0.4, "REPOSTED": 0.4,
               "FEATURES": 0.6, "INVITED_BY": 0.3}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def run(g: Graph, seeds: int = 10, resolution: float = 1.0, min_size: int = 3, min_stability: float = 0.6,
        edge_weights: dict[str, float] | None = None, deterministic: bool = True) -> dict:
    """Seeded multi-run Louvain over the actor graph; writes CommunityCluster nodes and roles.

    ``edge_weights`` maps relationship type -> weight (default :data:`ACTOR_EDGES`).
    Parameters mirror :class:`tgo.config.CommunityConfig`.  Returns counts.
    """
    weights = ACTOR_EDGES if edge_weights is None else edge_weights
    import networkx as nx

    G = nx.Graph()
    for e in sorted(g.edges.values(), key=lambda e: e.id):   # deterministic input order for seeded louvain
        w = weights.get(e.type)
        if not w or e.src == e.dst:
            continue
        inc = w * e.conf * (1 + (e.props.get("count", 1) - 1) ** 0.5)
        if G.has_edge(e.src, e.dst):
            G[e.src][e.dst]["weight"] += inc
        else:
            G.add_edge(e.src, e.dst, weight=inc)
    H = nx.Graph()
    H.add_nodes_from(sorted(G.nodes))
    H.add_edges_from(sorted(G.edges(data=True)))
    G = H
    out = {"nodes": G.number_of_nodes(), "edges": G.number_of_edges(), "clusters": 0, "rejected_unstable": 0}
    if G.number_of_edges() == 0:
        return out
    params = dict(algorithm="louvain", seeds=seeds, resolution=resolution, min_size=min_size,
                  min_stability=min_stability, edge_weights=weights)
    with g.activity("DetectionRun", f"louvain:r{resolution}:s{seeds}", agent={
            "tool": "networkx.louvain_communities", "version": nx.__version__, "codeLicense": "BSD-3-Clause",
            "weightsLicense": "n/a"}, **params) as run_key:
        runs = [[set(c) for c in nx.community.louvain_communities(G, weight="weight", resolution=resolution, seed=s)]
                for s in range(seeds)]
        ref = runs[0]
        modularity = nx.community.modularity(G, ref, weight="weight")
        g.nodes[run_key].props["qualityScore"] = round(modularity, 4)
        for comm in ref:
            if len(comm) < min_size:
                continue
            matches = [max(other, key=lambda c: _jaccard(comm, c)) for other in runs[1:]]
            stability = sum(_jaccard(comm, m) for m in matches) / max(1, len(matches))
            if stability < min_stability:
                out["rejected_unstable"] += 1
                continue
            members = sorted(comm)
            ck = f"cluster:louvain:{K.shortuuid(*members)}"
            g.node(ck, "CommunityCluster", label=f"Community ({len(members)})", size=len(members),
                   algorithm="louvain", stabilityScore=round(stability, 3), modularity=round(modularity, 4),
                   groupKind="community_cluster", members=members)
            g.edge("GENERATED_BY", ck, run_key)
            for m in members:
                co = (1 + sum(1 for mm in matches if m in mm)) / (1 + len(matches))
                g.edge("IN_CLUSTER", m, ck, conf=round(min(0.95, co * stability), 3), method="louvain",
                       evidence=[run_key], coAssignmentFrequency=round(co, 3))
            out["clusters"] += 1

        # network roles from the directed forward graph
        D = nx.DiGraph()
        for e in g.by_type("FORWARDS_FROM"):
            D.add_edge(e.src, e.dst, weight=e.props.get("count", 1))
        if D.number_of_nodes():
            indeg = dict(D.in_degree(weight="weight"))
            outdeg = dict(D.out_degree(weight="weight"))
            btw = nx.betweenness_centrality(D.to_undirected())
            max_in = max(indeg.values()) or 1
            for n in D.nodes:
                roles = []
                if indeg[n] >= 0.5 * max_in and indeg[n] >= 3:
                    roles.append("originator")
                if outdeg[n] >= 2:
                    roles.append("amplifier")
                if btw.get(n, 0) >= 0.2:
                    roles.append("bridge")
                if roles:
                    g.node(n, networkRoles=roles, networkRoleStatus="inferred", forwardedByCount=indeg[n],
                           forwardsCount=outdeg[n], betweenness=round(btw.get(n, 0), 3))
    return out
