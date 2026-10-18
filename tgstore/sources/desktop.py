"""Telegram Desktop export (``result.json``) -> tgstore records.

Works for a single-chat export and for a full-account export (``chats.list`` + ``left_chats.list``):

    python -m tgstore.sources.desktop result.json | python tgstore/client.py

Export quirks: chat ``id`` is the bare id (marked here as ``-100…`` for supergroups/channels, ``-…`` for
basic groups); ``from_id`` is a string like ``user123`` / ``channel123``; ``text`` may be a list of
entities; service messages name invited members by display name only (resolved within the chat).
Display names are the exporter's view of a user (contact names win), so they go to ``extra.name``.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

CHAT_TYPES = {
    "personal_chat": "private", "bot_chat": "private", "saved_messages": "private",
    "private_group": "group",
    "private_supergroup": "supergroup", "public_supergroup": "supergroup",
    "private_channel": "channel", "public_channel": "channel",
}
MEDIA_TYPES = {"voice_message": "voice", "video_file": "video", "video_message": "video_note", "audio_file": "audio"}
FIELD_MEDIA = {"photo": "photo", "file": "document", "poll": "poll", "location_information": "geo",
               "contact_information": "contact"}
JOINS = {"join_group_by_link", "join_group_by_request", "join_group_via_community"}


def peer_id(from_id: str | None) -> int | None:
    """``user123`` -> 123, ``chat123`` -> -123, ``channel123`` -> -100123 (Bot API style marked ids)."""
    m = re.fullmatch(r"(user|chat|channel)(\d+)", str(from_id or ""))
    if not m:
        return None
    n = int(m.group(2))
    return {"user": n, "chat": -n, "channel": -1_000_000_000_000 - n}[m.group(1)]


def chat_id(chat: dict) -> int:
    n, kind = int(chat["id"]), CHAT_TYPES.get(chat.get("type"))
    if n < 0 or kind == "private":
        return n
    return -n if kind == "group" else -1_000_000_000_000 - n


def _text(v) -> str:
    if isinstance(v, list):
        return "".join(p if isinstance(p, str) else p.get("text", "") for p in v)
    return v or ""


def _date(m: dict, key: str):
    """Prefer ``<key>_unixtime`` (UTC); the plain field is local time without a zone."""
    unix = m.get(f"{key}_unixtime")
    return int(unix) if unix else m.get(key)


def _media_type(m: dict) -> str | None:
    if m.get("media_type"):
        return MEDIA_TYPES.get(m["media_type"], m["media_type"])
    return next((t for key, t in FIELD_MEDIA.items() if key in m), None)


def message(cid: int, m: dict) -> dict:
    """One export message as a ``message`` record (service messages keep their ``action`` in ``extra``)."""
    sender = peer_id(m.get("from_id") or m.get("actor_id"))
    name = m.get("from") or m.get("actor")
    rec = {"kind": "message", "chat_id": cid, "id": m["id"], "date": _date(m, "date"),
           "text": _text(m.get("text")) or None, "reply_to_id": m.get("reply_to_message_id"),
           "edit_date": _date(m, "edited"), "media_type": _media_type(m), "extra": {}}
    if sender is not None and sender > 0:
        rec["user_id"] = sender
        if name:
            rec["user"] = {"id": sender, "extra": {"name": name}}
    elif sender is not None:
        rec["extra"]["sender_chat_id"] = sender   # channel post or anonymous admin
    fwd = peer_id(m.get("forwarded_from_id"))
    if fwd is not None:
        rec["forward_from_user_id" if fwd > 0 else "forward_from_chat_id"] = fwd
    for key, out in (("forwarded_from", "forwarded_from"), ("author", "author"), ("action", "action"),
                     ("via_bot", "via_bot"), ("file_name", "file_name")):
        if m.get(key):
            rec["extra"][out] = m[key]
    return {k: v for k, v in rec.items() if v is not None and v != {}}


def memberships(cid: int, m: dict, names: dict[str, int]) -> Iterator[dict]:
    """Membership records implied by a service message (joins, invites, removals)."""
    action, actor, when = m.get("action"), peer_id(m.get("actor_id")), _date(m, "date")
    if action in JOINS and actor and actor > 0:
        yield {"kind": "membership", "chat_id": cid, "user_id": actor, "status": "member", "joined_at": when}
    elif action in ("invite_members", "create_group", "remove_members"):
        for who in m.get("members") or []:
            uid = names.get(who)
            if uid is None:
                continue
            if action == "remove_members":
                yield {"kind": "membership", "chat_id": cid, "user_id": uid,
                       "status": "left" if uid == actor else "kicked"}
            else:
                yield {"kind": "membership", "chat_id": cid, "user_id": uid, "status": "member", "joined_at": when}
    if action in ("create_group", "create_channel") and actor and actor > 0:
        yield {"kind": "membership", "chat_id": cid, "user_id": actor, "status": "creator", "joined_at": when}


def chat_records(chat: dict) -> Iterator[dict]:
    """The chat record, then every message (and the memberships service messages imply)."""
    cid = chat_id(chat)
    yield {"kind": "chat", "id": cid, "type": CHAT_TYPES.get(chat.get("type"), chat.get("type")),
           "title": chat.get("name")}
    names = {}   # display name -> user id, to resolve service-message member lists
    for m in chat.get("messages", []):
        for name, pid in ((m.get("from"), m.get("from_id")), (m.get("actor"), m.get("actor_id"))):
            if name and (uid := peer_id(pid)) and uid > 0:
                names[name] = uid
    for m in chat.get("messages", []):
        yield message(cid, m)
        if m.get("type") == "service":
            yield from memberships(cid, m, names)


def records_from_export(data: dict) -> Iterator[dict]:
    """All records in a parsed ``result.json`` (single-chat or full-account export)."""
    me = data.get("personal_information")
    if me and me.get("user_id"):
        yield {"kind": "user", "id": me["user_id"], "first_name": me.get("first_name"),
               "last_name": me.get("last_name"), "username": (me.get("username") or "").lstrip("@") or None,
               "phone": me.get("phone_number"), "bio": me.get("bio")}
    if "messages" in data:
        yield from chat_records(data)
    for section, status in (("chats", "member"), ("left_chats", "left")):
        for chat in (data.get(section) or {}).get("list", []):
            yield from chat_records(chat)
            if me and me.get("user_id") and CHAT_TYPES.get(chat.get("type")) != "private":
                yield {"kind": "membership", "chat_id": chat_id(chat), "user_id": me["user_id"], "status": status}


def records(path: str | Path) -> Iterator[dict]:
    """All records in a ``result.json`` file."""
    return records_from_export(json.loads(Path(path).read_text(encoding="utf-8")))


if __name__ == "__main__":
    for p in sys.argv[1:]:
        for r in records(p):
            sys.stdout.write(json.dumps(r) + "\n")
