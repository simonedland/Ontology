"""Interoperability exports: FollowTheMoney (OpenAleph), STIX 2.1 (OpenCTI), Neo4j Cypher, GEXF."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..graph import Graph

# ---------------------------------------------------------------------- FtM


def _fid(key: str) -> str:
    return "tg-" + hashlib.sha1(key.encode()).hexdigest()[:20]


def _date(t) -> str | None:
    return datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") if t else None


def ftm_entities(g: Graph) -> list[dict]:
    """Map the graph to FollowTheMoney entities (UserAccount, Organization, Message, Membership, Similar, ...)."""
    ents: list[dict] = []

    def ent(key, schema, **props):
        ents.append({"id": _fid(key), "schema": schema,
                     "properties": {k: (v if isinstance(v, list) else [v]) for k, v in props.items()
                                    if v not in (None, "", [])}})

    owners: dict[str, str] = {}
    for n in g.by_label("PersonCluster"):
        if n.props.get("exploded"):
            continue
        ent(n.key, "Person", name=n.props.get("label"), notes="Telegram person cluster (positive identity judgement)")
        for e in g.in_edges(n.key, "OPERATED_BY"):
            if not e.props.get("deletedAt"):
                owners[e.src] = _fid(n.key)
    usernames = {e.src: g.nodes[e.dst].props.get("usernameValue") for e in g.by_type("HAS_USERNAME")}
    for n in g.by_label("Account"):
        # FtM UserAccount: username is required; phone (not number)
        ent(n.key, "UserAccount", service="telegram", username=usernames.get(n.key) or n.key,
            name=n.props.get("label") or n.props.get("collectorDisplayName"), phone=n.props.get("phone"),
            owner=owners.get(n.key))
    for n in g.by_label("PlatformGroup"):
        if n.labels & {"Chat", "Community"} and "PrivateChat" not in n.labels:
            ent(n.key, "Organization", name=n.props.get("title") or n.props.get("label"),
                website=f"https://t.me/{usernames[n.key]}" if usernames.get(n.key) else None,
                incorporationDate=_date(n.props.get("createdAt")), summary=n.props.get("about"),
                classification=n.props.get("groupKind"))
    for n in g.by_label("Message"):
        if not n.props.get("text"):
            continue
        sender = next((e.src for e in g.in_edges(n.key, "SENT")), None)
        ent(n.key, "Message", bodyText=n.props["text"], date=_date(n.props.get("date")),
            sender=_fid(sender) if sender else None, sourceUrl=n.key)
    for n in g.by_label("CryptoAddress"):
        ent(n.key, "CryptoWallet", publicKey=n.props["value"], currency=(n.props.get("detail") or [None])[0])
    for n in g.by_label("IBAN"):
        ent(n.key, "BankAccount", iban=n.props["value"], accountNumber=n.props["value"])
    for e in g.edges.values():
        if e.type in ("MEMBER_OF", "ADMIN_OF") and e.dst in g.nodes and "Chat" in g.nodes[e.dst].labels:
            ent(e.id, "Membership", member=_fid(e.src), organization=_fid(e.dst), role=e.props.get("role"),
                startDate=_date(e.props.get("since")), endDate=_date(e.props.get("until")),
                summary=f"visibility={e.props.get('scope')}")
        elif e.type == "CANDIDATE_SAME_PERSON":
            ent(e.id, "Similar", candidate=_fid(e.src), match=_fid(e.dst), confidenceScore=str(e.conf),
                matcher=e.props.get("decidedBy"),
                criteria=f"features={','.join(e.props.get('features') or [])}; judgement={e.props.get('judgement')}")
        elif e.type in ("GAVE_GIFT_TO", "KNOWS", "CLOSE_FRIEND_OF", "REPOSTED"):
            ent(e.id, "UnknownLink", subject=_fid(e.src), object=_fid(e.dst),
                role=f"telegram_{e.type.lower()}", date=_date(e.props.get("t_first")))
        elif e.type == "CONTAINS_IDENTIFIER" and g.nodes[e.dst].labels & {"CryptoAddress", "IBAN"}:
            ent(e.id, "UnknownLink", subject=_fid(e.src), object=_fid(e.dst), role="mentions_identifier")
    return ents


def write_ftm(g: Graph, path: str | Path) -> int:
    """Write FtM entities as JSON lines; returns the entity count."""
    ents = ftm_entities(g)
    with open(path, "w", encoding="utf-8") as f:
        for e in ents:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return len(ents)


# --------------------------------------------------------------------- STIX
_STIX_NS = uuid.UUID("00abedb4-aa42-466c-9c01-fed23315a9b7")
_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _sid(t: str, key: str) -> str:
    return f"{t}--{uuid.uuid5(_STIX_NS, key)}"


def stix_bundle(g: Graph) -> dict:
    """Map the graph to a STIX 2.1 bundle (user-account, OpenCTI channel/wallet/phone, relationships)."""
    objs: list[dict] = []
    ids: dict[str, str] = {}
    usernames = {e.src: g.nodes[e.dst].props.get("usernameValue") for e in g.by_type("HAS_USERNAME")}
    for n in g.by_label("Account"):
        i = ids[n.key] = _sid("user-account", n.key)
        objs.append({"type": "user-account", "spec_version": "2.1", "id": i, "user_id": str(n.props.get("userId", n.key)),
                     "account_login": usernames.get(n.key), "display_name": n.props.get("label") or n.props.get("collectorDisplayName"),
                     "account_type": "telegram", "is_service_account": bool(n.props.get("isBot"))})
    for n in g.by_label("Chat"):
        if "PrivateChat" in n.labels or not n.key.startswith(("channel:", "chat:")):
            continue
        i = ids[n.key] = _sid("channel", n.key)  # OpenCTI Channel SDO
        objs.append({"type": "channel", "spec_version": "2.1", "id": i, "created": _NOW, "modified": _NOW,
                     "name": n.props.get("title") or n.props.get("label") or n.key,
                     "channel_types": ["Telegram"], "description": n.props.get("about")})
    for n in g.by_label("Identifier"):
        t = ("cryptocurrency-wallet" if "CryptoAddress" in n.labels else "url" if "Url" in n.labels
             else "domain-name" if "Domain" in n.labels else "phone-number" if "PhoneNumber" in n.labels
             else "email-addr" if "Email" in n.labels else None)
        if t:
            i = ids[n.key] = _sid(t, n.key)
            objs.append({"type": t, "spec_version": "2.1", "id": i, "value": n.props.get("value")})
    rel_types = {"MEMBER_OF": "belongs-to", "ADMIN_OF": "belongs-to", "CONTAINS_IDENTIFIER": "related-to",
                 "POSTED_IN": "related-to", "FORWARDS_FROM": "related-to", "CANDIDATE_SAME_PERSON": "related-to",
                 "IDENTICAL_POST": "related-to", "SYNC_POST": "related-to", "CO_FORWARD": "related-to"}
    for e in g.edges.values():
        rt = rel_types.get(e.type)
        src = ids.get(e.src)
        if not rt and e.type == "CONTAINS_IDENTIFIER":
            continue
        if e.type == "CONTAINS_IDENTIFIER":
            sender = next((s.src for s in g.in_edges(e.src, "SENT")), None)
            src = ids.get(sender) if sender else None
        if rt and src and ids.get(e.dst):
            objs.append({"type": "relationship", "spec_version": "2.1", "id": _sid("relationship", e.id),
                         "created": _NOW, "modified": _NOW, "relationship_type": rt, "source_ref": src,
                         "target_ref": ids[e.dst], "description": f"TGO {e.type} ({e.status})",
                         "confidence": int(round(e.conf * 100))})
    objs = [{k: v for k, v in o.items() if v is not None} for o in objs]
    return {"type": "bundle", "id": _sid("bundle", "tgo"), "objects": objs}


def write_stix(g: Graph, path: str | Path) -> int:
    """Write the STIX 2.1 bundle; returns the object count."""
    b = stix_bundle(g)
    Path(path).write_text(json.dumps(b, indent=1, ensure_ascii=False), encoding="utf-8")
    return len(b["objects"])


# -------------------------------------------------------------------- Neo4j


def _cy(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    if isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v) and \
            len({type(x) for x in v}) <= 1:
        return "[" + ", ".join(_cy(x) for x in v) + "]"
    if not isinstance(v, str):
        v = json.dumps(v, ensure_ascii=False, default=str)
    return json.dumps(v, ensure_ascii=False)


def _map(d: dict) -> str:
    return "{" + ", ".join(f"`{k}`: {_cy(v)}" for k, v in d.items() if v is not None) + "}"


def write_cypher(g: Graph, path: str | Path) -> None:
    """MERGE script; observed memberships stay plain MEMBER_OF relationships (ORG shortcut pattern)."""
    L = ["CREATE CONSTRAINT tgo_key IF NOT EXISTS FOR (n:TGO) REQUIRE n.key IS UNIQUE;"]
    for n in g.nodes.values():
        labels = ":".join(["TGO"] + sorted(n.labels))
        props = {k: v for k, v in n.props.items() if k != "localPath"}
        L.append(f"MERGE (n:{labels} {{key: {_cy(n.key)}}}) SET n += {_map(props)};")
    for e in g.edges.values():
        props = dict(e.props)
        props["id"] = e.id
        L.append(f"MATCH (a:TGO {{key: {_cy(e.src)}}}), (b:TGO {{key: {_cy(e.dst)}}}) "
                 f"MERGE (a)-[r:{e.type} {{id: {_cy(e.id)}}}]->(b) SET r += {_map(props)};")
    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")


def write_gexf(g: Graph, path: str | Path) -> None:
    """Legacy NetworkX GEXF writer (the pipeline uses :mod:`tgo.export.gexf`)."""
    import networkx as nx

    G = g.to_networkx()
    for _, d in G.nodes(data=True):
        for k in list(d):
            if d[k] is None:
                del d[k]
    nx.write_gexf(G, path)
