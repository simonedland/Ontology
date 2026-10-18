"""Identity resolution: scored candidate edges + nomenklatura-style judgements.

An account is never a person.  Every account-to-account identity claim is an
``IdentityJudgement`` (nomenklatura Resolver columns: source, target,
judgement, score, user/decidedBy, created_at, deleted_at) that serialises to
FtM ``Similar``; never ``owl:sameAs``.

* the algorithm only writes ``no_judgement`` rows with a score;
* only analysts (or an explicit, logged auto-accept policy) write ``positive``;
* a positive decision mints / extends a ``PersonCluster`` (``PC-{shortuuid}``);
* ``negative`` is a cannot-link constraint that blocks transitive merges;
* ``explode`` un-merges (GDPR rectification) without deleting observations.

Evidence is strictly non-biometric: shared device serial, shared validated
identifier, same personal channel, same bio text, username history, phone,
whole-image avatar hash (identifies the *picture*, not the face) with IDF
down-weighting of high-degree hashes.
"""
from __future__ import annotations

import time
from collections import defaultdict
from itertools import combinations

from .. import keys as K
from ..graph import Graph

WEIGHTS = {
    "same_phone": 0.9,
    "same_personal_channel": 0.7,
    "shared_device_serial": 0.6,
    "shared_identifier": 0.45,
    "username_history": 0.4,
    "same_bio_text": 0.35,
    "shared_avatar_hash": 0.35,
}
STRONG_IDENTIFIERS = {"CryptoAddress", "IBAN", "PhoneNumber", "Email"}


def _content(g: Graph, acct: str) -> set[str]:
    out = set()
    for e in g.out_edges(acct, "SENT"):
        out.add(e.dst)
        for hm in g.out_edges(e.dst, "HAS_MEDIA"):
            out.add(hm.dst)
    return out


def _features(g: Graph, strong=None, min_bio_len: int = 15) -> dict[str, dict[str, set[str]]]:
    """feature -> evidence key -> set(accounts)"""
    strong = STRONG_IDENTIFIERS if strong is None else set(strong)
    feats: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    accounts = [n.key for n in g.by_label("Account") if n.key.startswith("user:")]
    for a in accounts:
        node = g.nodes[a]
        content = _content(g, a)
        for c in content:
            for e in g.out_edges(c, "CAPTURED_WITH"):
                feats["shared_device_serial"][e.dst].add(a)
            for e in g.out_edges(c, "CONTAINS_IDENTIFIER"):
                if strong & g.nodes[e.dst].labels:
                    feats["shared_identifier"][e.dst].add(a)
        for e in g.out_edges(a, "FEATURES"):
            feats["same_personal_channel"][e.dst].add(a)
        if node.props.get("phone"):
            feats["same_phone"]["phone:" + str(node.props["phone"])].add(a)
        snaps = sorted(g.out_edges(a, "HAS_PROFILE"), key=lambda e: e.props.get("t_last", 0))
        if snaps:
            about = (g.nodes[snaps[-1].dst].props.get("about") or "").strip().lower()
            if len(about) >= min_bio_len:
                feats["same_bio_text"]["bio:" + about].add(a)
        for av in g.out_edges(a, "HAS_AVATAR"):
            for fp in g.in_edges(av.dst, "FINGERPRINT_OF"):
                feats["shared_avatar_hash"][fp.src].add(a)
            for nd in list(g.out_edges(av.dst, "NEAR_DUPLICATE_OF")) + list(g.in_edges(av.dst, "NEAR_DUPLICATE_OF")):
                other = nd.dst if nd.src == av.dst else nd.src
                for owner in g.in_edges(other, "HAS_AVATAR"):
                    feats["shared_avatar_hash"][nd.id].update({a, owner.src})
        for e in g.out_edges(a, "NAME_HISTORY_LINK"):
            feats["username_history"][e.id].update({a, e.dst})
    return feats


def score_candidates(g: Graph, min_score: float = 0.3, weights: dict[str, float] | None = None,
                     strong_identifiers=None, min_bio_len: int = 15) -> list[dict]:
    """Score account pairs by noisy-OR over shared non-biometric evidence (one vote per feature).

    Returns ``[{source, target, score, features, evidence}]`` sorted by score, >= ``min_score``.
    """
    weights = WEIGHTS if weights is None else weights
    feats = _features(g, strong_identifiers, min_bio_len)
    pair_ev: dict[tuple[str, str], list[tuple[str, str, float]]] = defaultdict(list)
    for feat, groups in feats.items():
        for ev, accts in groups.items():
            if len(accts) < 2:
                continue
            # IDF down-weighting: an identifier / avatar shared by many accounts (stock image,
            # default avatar, exchange deposit address) is weak evidence of a shared operator
            idf = min(1.0, 2.0 / len(accts))
            if feat == "shared_avatar_hash" and len(accts) > 3 and ev in g.nodes:
                g.nodes[ev].props["hashClass"] = "high_degree"
            for a, b in combinations(sorted(accts), 2):
                pair_ev[(a, b)].append((feat, ev, weights.get(feat, 0.0) * idf))
    cands = []
    for (a, b), evs in pair_ev.items():
        best: dict[str, float] = {}
        for feat, _, w in evs:
            best[feat] = max(best.get(feat, 0), w)  # one vote per feature type
        p_not = 1.0
        for w in best.values():
            p_not *= 1 - w
        score = round(1 - p_not, 3)
        if score >= min_score:
            cands.append({"source": a, "target": b, "score": score,
                          "features": sorted(best), "evidence": sorted({ev for _, ev, _ in evs})})
    return sorted(cands, key=lambda c: -c["score"])


class Resolver:
    """nomenklatura-style judgement store on top of the graph (suggest / decide / merge / explode)."""
    def __init__(self, g: Graph):
        self.g = g

    @staticmethod
    def jkey(a: str, b: str) -> str:
        """Deterministic IdentityJudgement key for an unordered account pair."""
        a, b = sorted((a, b))
        return f"judg:{a}|{b}"

    def cluster_of(self, acct: str) -> str | None:
        """Active PersonCluster of ``acct`` (via OPERATED_BY), or None."""
        for e in self.g.out_edges(acct, "OPERATED_BY"):
            if not e.props.get("deletedAt"):
                return e.dst
        return None

    def members(self, pc: str) -> set[str]:
        """Accounts currently operated by person cluster ``pc``."""
        return {e.src for e in self.g.in_edges(pc, "OPERATED_BY") if not e.props.get("deletedAt")}

    def _negative(self, a: str, b: str) -> bool:
        n = self.g.nodes.get(self.jkey(a, b))
        return bool(n and n.props.get("judgement") == "negative" and not n.props.get("deletedAt"))

    def suggest(self, a: str, b: str, score: float, features, evidence, decided_by: str) -> str:
        """Record an algorithmic candidate (``no_judgement`` + score); never overrides a human decision."""
        a, b = sorted((a, b))
        jk = self.jkey(a, b)
        existing = self.g.nodes.get(jk)
        if existing and existing.props.get("judgement") in ("positive", "negative", "unsure"):
            existing.props["score"] = score   # keep the human decision, refresh the score
            return jk
        self.g.node(jk, "IdentityJudgement", source=a, target=b, judgement="no_judgement", score=score,
                    decidedBy=decided_by, createdAt=int(time.time()), features=list(features),
                    label=f"{a} ? {b}")
        self.g.edge("CANDIDATE_SAME_PERSON", a, b, conf=score, method=decided_by, evidence=list(evidence),
                    judgement="no_judgement", decidedBy=decided_by, judgementKey=jk, features=list(features))
        return jk

    def decide(self, a: str, b: str, judgement: str, decided_by: str, legal_basis: str | None = None,
               rationale: str | None = None) -> dict:
        """Record an analyst/policy judgement; ``positive`` merges into a PersonCluster unless a cannot-link blocks it."""
        assert judgement in ("positive", "negative", "unsure", "no_judgement")
        a, b = sorted((a, b))
        jk = self.jkey(a, b)
        self.g.node(jk, "IdentityJudgement", source=a, target=b, judgement=judgement, decidedBy=decided_by,
                    decidedAt=int(time.time()), legalBasis=legal_basis, rationale=rationale, label=f"{a} ? {b}")
        e = self.g.edges.get(f"{a}-[CANDIDATE_SAME_PERSON]->{b}")
        if e is None:
            e = self.g.edge("CANDIDATE_SAME_PERSON", a, b, status="analyst", conf=1.0, method=decided_by,
                            evidence=[jk], judgementKey=jk)
        e.props.update(judgement=judgement, decidedBy=decided_by)
        if self.g.current_run and self.g.current_run not in e.props.setdefault("runs", []):
            e.props["runs"].append(self.g.current_run)
        result = {"judgement": jk, "decision": judgement}
        if judgement == "positive":
            result.update(self._merge(a, b, jk, decided_by))
        return result

    def _merge(self, a: str, b: str, jk: str, decided_by: str) -> dict:
        ca, cb = self.cluster_of(a), self.cluster_of(b)
        ma = self.members(ca) if ca else {a}
        mb = self.members(cb) if cb else {b}
        blocked = [(x, y) for x in ma for y in mb if self._negative(x, y)]
        if blocked:
            self.g.nodes[jk].props["conflict"] = f"cannot-link {blocked[0][0]} / {blocked[0][1]}"
            return {"merged": False, "blockedBy": blocked}
        if ca and cb and ca != cb:
            pc = min(ca, cb)
            for m in self.members(max(ca, cb)):
                self._operated(m, pc, jk, decided_by)
                self.g.edges[f"{m}-[OPERATED_BY]->{max(ca, cb)}"].props["deletedAt"] = int(time.time())
            self.g.nodes[max(ca, cb)].props["mergedInto"] = pc
        else:
            pc = ca or cb or "pc:" + K.person_cluster(a, b)
            self.g.node(pc, "PersonCluster", label=pc.split(":", 1)[1], mintedBy=jk, mintedAt=int(time.time()))
        for m in (a, b):
            self._operated(m, pc, jk, decided_by)
        return {"merged": True, "cluster": pc}

    def _operated(self, acct: str, pc: str, jk: str, decided_by: str) -> None:
        self.g.edge("OPERATED_BY", acct, pc, status="analyst", conf=0.9, method="identity-judgement",
                    evidence=[jk], decidedBy=decided_by)

    def explode(self, pc: str, reason: str = "rectification") -> int:
        """Un-merge a cluster (soft delete, observations untouched)."""
        now, n = int(time.time()), 0
        for e in list(self.g.in_edges(pc, "OPERATED_BY")):
            if not e.props.get("deletedAt"):
                e.props["deletedAt"] = now
                n += 1
                for ev in e.props.get("evidence", []):
                    if ev in self.g.nodes:
                        self.g.nodes[ev].props.update(deletedAt=now, judgement="no_judgement")
        self.g.nodes[pc].props.update(exploded=now, explodeReason=reason)
        return n


def run(g: Graph, decisions: list[dict] | None = None, min_score: float = 0.3,
        auto_accept: float | None = None, weights: dict[str, float] | None = None,
        strong_identifiers=None, min_bio_len: int = 15) -> dict:
    """Write scored candidates, apply the optional auto-accept policy, then analyst ``decisions``.

    ``decisions`` = ``[{source, target, judgement, decidedBy, legalBasis?, rationale?}]``.
    Parameters mirror :class:`tgo.config.IdentityConfig`.
    """
    weights = WEIGHTS if weights is None else weights
    res = Resolver(g)
    out = {"candidates": 0, "decisions": [], "auto_accepted": 0}
    with g.activity("DetectionRun", f"xref:noisy-or:min{min_score}", agent={
            "tool": "tgo-xref", "version": "0.1 (nomenklatura judgement model)", "codeLicense": "MIT",
            "weightsLicense": "n/a"}, algorithm="noisy_or_evidence", weights=weights, min_score=min_score,
            auto_accept=auto_accept):
        for c in score_candidates(g, min_score, weights, strong_identifiers, min_bio_len):
            res.suggest(c["source"], c["target"], c["score"], c["features"], c["evidence"], "tgo-xref:noisy-or")
            out["candidates"] += 1
            if auto_accept is not None and c["score"] >= auto_accept:
                out["decisions"].append(res.decide(c["source"], c["target"], "positive",
                                                   f"policy:auto>={auto_accept}"))
                out["auto_accepted"] += 1
    if decisions:
        with g.activity("CollectionRun", "analyst-judgements", source="analyst"):
            for d in decisions:
                out["decisions"].append(res.decide(d["source"], d["target"], d["judgement"], d["decidedBy"],
                                                   d.get("legalBasis"), d.get("rationale")))
    return out
