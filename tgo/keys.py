"""Deterministic node keys.

Rules from the TGO report:
* key on constructor/field names and stable numeric IDs, never CRC32 IDs;
* files key on ``file:{sha256}`` (primary) or ``media:{file_unique_id}``;
* never key on ``file_id``, ``access_hash`` or ``file_reference`` (account
  scoped / volatile);
* a photo has no original bytes, so it keys on ``photo:{id}``.
"""
from __future__ import annotations

import hashlib
import re
import uuid

_NS = uuid.UUID("6f1c3c2e-5b0a-4f7e-9a51-7465676f6e74")
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def user(uid) -> str:
    """``user:{id}``"""
    return f"user:{int(uid)}"


def channel(cid) -> str:
    """``channel:{id}`` (broadcast channels and supergroups)"""
    return f"channel:{int(cid)}"


def chat(cid) -> str:
    """Basic (legacy) group."""
    return f"chat:{int(cid)}"


def community(cid) -> str:
    """``community:{id}``"""
    return f"community:{int(cid)}"


def message(peer_key: str, msg_id) -> str:
    """``{peer}/msg:{id}``"""
    return f"{peer_key}/msg:{int(msg_id)}"


def story(peer_key: str, story_id) -> str:
    """``{peer}/story:{id}``"""
    return f"{peer_key}/story:{int(story_id)}"


def topic(peer_key: str, topic_id) -> str:
    """``{peer}/topic:{id}``"""
    return f"{peer_key}/topic:{int(topic_id)}"


def attachment(msg_key: str, idx: int) -> str:
    """``{msg}/att:{idx}``"""
    return f"{msg_key}/att:{idx}"


def file_sha(sha256: str) -> str:
    """``file:{sha256}`` - primary key for byte-preserved files"""
    return f"file:{sha256.lower()}"


def media_uid(file_unique_id: str) -> str:
    """``media:{file_unique_id}`` - secondary key before download"""
    return f"media:{file_unique_id}"


def document(doc_id) -> str:
    """Pre-download key: document.id survives forwards (same CDN object)."""
    return f"doc:{int(doc_id)}"


def photo(photo_id) -> str:
    """``photo:{id}`` - photos have no original bytes"""
    return f"photo:{int(photo_id)}"


def rendition(photo_id, size_type: str) -> str:
    """``photo:{id}:{sizeType}``"""
    return f"photo:{int(photo_id)}:{size_type}"


def username(name: str) -> str:
    """``username:{lower-case name}``"""
    return f"username:{name.lstrip('@').lower()}"


def sticker_set(set_id=None, short_name: str | None = None) -> str:
    """``stickerset:{id|short_name}``"""
    return f"stickerset:{int(set_id)}" if set_id is not None else f"stickerset:{short_name.lower()}"


def identifier(kind: str, normalized: str) -> str:
    """``id:{kind}:{normalised value}``"""
    return f"id:{kind}:{normalized}"


def device(serial: str, make: str = "") -> str:
    """``device:{make}:{serial}``"""
    make_slug = re.sub(r"\W+", "_", make.lower())
    return f"device:{make_slug}:{serial}"


def geo(lat: float, lon: float) -> str:
    """``geo:{lat},{lon}`` (5 decimals)"""
    return f"geo:{lat:.5f},{lon:.5f}"


def fingerprint(algo: str, hex_: str) -> str:
    """``fp:{algo}:{hex}``"""
    return f"fp:{algo}:{hex_}"


def peer_from_mtproto(peer: dict | None) -> str | None:
    """Telethon ``to_dict()`` Peer -> key."""
    if not peer:
        return None
    t = peer.get("_")
    if t == "PeerUser":
        return user(peer["user_id"])
    if t == "PeerChannel":
        return channel(peer["channel_id"])
    if t == "PeerChat":
        return chat(peer["chat_id"])
    return None


def peer_from_export(from_id: str | None) -> str | None:
    """Desktop export ``from_id`` (``user123`` / ``channel123`` / ``chat123``)."""
    if not from_id:
        return None
    m = re.fullmatch(r"(user|channel|chat)(\d+)", str(from_id))
    if not m:
        return None
    return f"{m.group(1)}:{int(m.group(2))}"


def stable_uuid(*parts) -> str:
    """UUIDv5 over the given parts (deterministic)."""
    return str(uuid.uuid5(_NS, "|".join(map(str, parts))))


def shortuuid(*parts, length: int = 10) -> str:
    """Deterministic short id (nomenklatura NK- / TGO PC- pattern)."""
    n = int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest(), 16)
    out = []
    for _ in range(length):
        n, r = divmod(n, len(_ALPHABET))
        out.append(_ALPHABET[r])
    return "".join(out)


def person_cluster(*seed_parts) -> str:
    """``PC-{shortuuid}`` minted from the first positive judgement's accounts."""
    return f"PC-{shortuuid(*seed_parts)}"
