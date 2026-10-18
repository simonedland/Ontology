"""Projections from the message layer to the actor layer, plus cheap joins.

* POSTED_IN       account -> chat     (count = messages)
* FORWARDS_FROM   chat -> source chat (the DFRLab forward graph)
* MENTIONS        message -> account  (via @handle / t.me link resolved to a username holder)
* NAME_HISTORY_LINK  accounts that held the same (collectible / transferred) username
* SAME_FILE_AS    nodes for the same file seen with and without bytes (doc id / file_unique_id)
"""
from __future__ import annotations

from ..graph import Graph


def chat_of(g: Graph, key: str) -> str | None:
    if key.split(":", 1)[0] in ("channel", "chat", "user", "community") and "/" not in key:
        return key
    for e in g.out_edges(key, "IN_CHAT"):
        return e.dst
    return None


def sender_of(g: Graph, msg_key: str) -> str | None:
    for e in g.in_edges(msg_key, "SENT"):
        return e.src
    return None


def run(g: Graph) -> dict:
    n = {"POSTED_IN": 0, "FORWARDS_FROM": 0, "MENTIONS": 0, "NAME_HISTORY_LINK": 0, "SAME_FILE_AS": 0}
    with g.activity("DetectionRun", "aggregate", agent={"tool": "tgo-aggregate", "version": "0.1",
                                                          "codeLicense": "MIT", "weightsLicense": "n/a"}):
        for e in list(g.by_type("SENT")):
            chat = chat_of(g, e.dst)
            if chat and chat != e.src:
                g.edge("POSTED_IN", e.src, chat, method="aggregate(SENT,IN_CHAT)", evidence=[e.dst],
                       t=g.nodes[e.dst].props.get("date"))
                n["POSTED_IN"] += 1
        for e in list(g.by_type("FORWARD_OF")):
            here = chat_of(g, e.src)
            there = chat_of(g, e.dst)
            if here and there and here != there:
                g.edge("FORWARDS_FROM", here, there, method="aggregate(FORWARD_OF)", evidence=[e.src],
                       t=g.nodes[e.src].props.get("date"))
                n["FORWARDS_FROM"] += 1

        holders: dict[str, list[str]] = {}
        for e in g.by_type("HAS_USERNAME"):
            holders.setdefault(e.dst, []).append(e.src)
        for e in list(g.by_type("CONTAINS_IDENTIFIER")):
            idn = g.nodes[e.dst]
            if "TelegramHandle" not in idn.labels or "Message" not in g.nodes[e.src].labels:
                continue
            for acct in holders.get(f"username:{idn.props['value']}", []):
                # a username identifies an account *at a time*: inferred, not observed
                g.edge("MENTIONS", e.src, acct, status="inferred", conf=0.9, method="handle->username holder",
                       evidence=[e.dst], t=g.nodes[e.src].props.get("date"))
                n["MENTIONS"] += 1
        for uname, accts in holders.items():
            accts = sorted(set(accts))
            for i, a in enumerate(accts):
                for b in accts[i + 1:]:
                    g.edge("NAME_HISTORY_LINK", a, b, method="shared username over time", evidence=[uname])
                    n["NAME_HISTORY_LINK"] += 1

        by_alt: dict[tuple, set[str]] = {}
        for node in g.by_label("File"):
            for prop in ("documentId", "fileUniqueId"):
                if node.props.get(prop) is not None:
                    by_alt.setdefault((prop, node.props[prop]), set()).add(node.key)
        for (prop, val), ks in by_alt.items():
            ks = sorted(ks)
            for a in ks[1:]:
                g.edge("SAME_FILE_AS", ks[0], a, method=f"shared {prop}", evidence=[f"{prop}={val}"])
                n["SAME_FILE_AS"] += 1
    return n
