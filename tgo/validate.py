"""Guardrail validation: the TGO rules as executable checks (SHACL-like).

Each rule returns findings with a severity:
  violation - the graph breaks a hard TGO rule (must be fixed before export)
  warning   - legal/epistemic risk an analyst must see
  info      - typed gaps (absence-as-data) worth surfacing
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from . import ontology as O
from .graph import Graph


@dataclass
class Finding:
    rule: str
    severity: str
    subject: str
    message: str


def validate(g: Graph) -> list[Finding]:
    F: list[Finding] = []

    def add(rule, sev, subj, msg):
        F.append(Finding(rule, sev, subj, msg))

    for w in g.warnings:
        add("vocabulary", "warning", "-", w)

    # R1 derived edges carry conf / method / evidence / run
    for e in g.edges.values():
        p = e.props
        if e.status in ("inferred", "file_declared"):
            missing = [k for k in ("method", "evidence", "runs") if not p.get(k)]
            if p.get("method") == "unspecified":
                missing.append("method")
            if missing:
                add("R1-derived-provenance", "violation", e.id, f"{e.status} edge lacks {', '.join(missing)}")
            if e.status == "inferred" and float(p.get("conf", 1)) >= 1.0:
                add("R1-derived-provenance", "violation", e.id, "inferred edge with confidence 1.0")
    # R2 never sameAs / biometric edges or labels
    for e in g.edges.values():
        if e.type in O.FORBIDDEN_RELATIONS:
            add("R2-no-sameas-no-biometrics", "violation", e.id, f"forbidden relationship {e.type}")
    for n in g.nodes.values():
        bad = n.labels & O.FORBIDDEN_LABELS
        if bad:
            add("R2-no-sameas-no-biometrics", "violation", n.key, f"forbidden label {sorted(bad)}")
    # R3 Person / PersonCluster only with a positive judgement
    for n in g.nodes.values():
        if n.labels & {"Person", "PersonCluster"}:
            jk = n.props.get("mintedBy")
            ok = jk and g.nodes.get(jk) and g.nodes[jk].props.get("judgement") == "positive"
            if not ok and not n.props.get("exploded"):
                add("R3-person-needs-judgement", "violation", n.key, "person node without positive identity judgement")
    # R4 keys never on file_id / access_hash / file_reference
    for n in g.nodes.values():
        if re.search(r"(file_id|access_hash|file_reference)", n.key):
            add("R4-stable-keys", "violation", n.key, "node keyed on a volatile Telegram handle")
    # R5 no EXIF / C2PA claims on photo-mode media
    for e in g.edges.values():
        if e.type in ("CAPTURED_WITH", "HAS_EXIF_GPS"):
            src = g.nodes[e.src]
            if "Photo" in src.labels or src.props.get("sendMode") == "PhotoMode":
                add("R5-photo-mode-metadata", "violation", e.id, "EXIF fact on a server re-encoded photo")
            elif e.type == "HAS_EXIF_GPS":
                add("R5-photo-mode-metadata", "warning", e.id, "EXIF GPS is spoofable; corroborate before use")
    # R6 Art. 9: membership in special-category chats
    for e in g.edges.values():
        if e.type in ("MEMBER_OF", "ADMIN_OF", "POSTED_IN"):
            topic = g.nodes[e.dst].props.get("topic")
            if topic in O.SPECIAL_CATEGORY_TOPICS:
                e.props["art9"] = True
                add("R6-art9-membership", "warning", e.id,
                    f"membership in a '{topic}' chat is special-category data: needs Art. 9(2)(j) basis, DPIA, RBAC")
    # R7 admin/owner-only observations are elevated sensitivity
    elevated = [e for e in g.edges.values() if e.props.get("scope") in O.ELEVATED_SCOPES]
    if elevated:
        add("R7-admin-only-signals", "info", f"{len(elevated)} edges",
            "admin/owner-only observations (inviters, subscriber lists) reveal non-posting audience members")
    # R8 positive judgements need a decider; analyst judgements a legal basis
    for n in g.by_label("IdentityJudgement"):
        if n.props.get("judgement") == "positive":
            if not n.props.get("decidedBy"):
                add("R8-judgement-accountability", "violation", n.key, "positive judgement without decidedBy")
            if str(n.props.get("decidedBy", "")).startswith("analyst:") and not n.props.get("legalBasis"):
                add("R8-judgement-accountability", "warning", n.key, "analyst judgement without legal basis")
        if n.props.get("conflict"):
            add("R8-judgement-accountability", "warning", n.key, f"merge blocked: {n.props['conflict']}")
    # R9 sticker ownership only from observed facts
    for e in g.by_type("OWNED_BY"):
        if e.props.get("method") not in ("stickerSet.creator", "short_name _by_<bot> suffix"):
            add("R9-sticker-ownership", "violation", e.id, "sticker-set ownership must not be inferred from the set ID")
    # R10 viewer-relative facts must name the observer
    for e in g.edges.values():
        if e.props.get("scope") == "collector_only" and not e.props.get("observed_by"):
            add("R10-viewer-relative", "violation", e.id, "viewer-relative fact without observed_by")
    # Info: typed gaps
    gaps: dict[str, int] = {}
    for n in g.nodes.values():
        for f, rec in n.fields.items():
            if rec["visibility"] != "visible":
                gaps[f"{f}:{rec['visibility']}"] = gaps.get(f"{f}:{rec['visibility']}", 0) + 1
        if n.props.get("fileStatus"):
            k = f"file:{n.props['fileStatus']}"
            gaps[k] = gaps.get(k, 0) + 1
    for k, v in sorted(gaps.items()):
        add("typed-gap", "info", k, f"{v} occurrences (absence is data)")
    return F


def summary(findings: list[Finding]) -> dict:
    s = {"violation": 0, "warning": 0, "info": 0}
    for f in findings:
        s[f.severity] += 1
    return s


def to_dicts(findings: list[Finding]) -> list[dict]:
    return [asdict(f) for f in findings]
