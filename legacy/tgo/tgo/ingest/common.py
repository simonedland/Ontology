"""Helpers shared by the ingest adapters: timestamps, run ids, media nodes and attachments."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .. import keys as K
from .. import media as M
from ..extractors import sha256_file
from ..graph import Graph


def ts(v) -> int | None:
    """Epoch seconds from int / numeric string / ISO string (naive = UTC)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v)
    if s.isdigit():
        return int(s)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def run_id(*parts) -> str:
    """Short deterministic id (sha256 prefix) for a collection run from its identifying parts."""
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12]


def attach_file(g: Graph, root: Path, rel_path: str | None, cls: str, send_mode: str, *,
                fallback_key: str | None, props: dict) -> str | None:
    """Create the media node, keyed by content when the bytes are available.

    Key priority (TGO): ``file:{sha256}`` > ``media:{file_unique_id}`` > ``doc:{id}``.
    Photos never get a sha256 *identity* (server renditions only): they key on
    ``photo:{id}`` and keep the rendition hash as a property.
    """
    path = (root / rel_path) if rel_path else None
    have = path is not None and path.is_file()
    sha = sha256_file(path) if have else None
    if cls == "Photo":
        key = fallback_key or (f"photo:sha256:{sha}" if sha else None)
        if key is None:
            return None
        g.node(key, "Photo", sendMode=send_mode, renditionSha256=sha,
               localPath=str(path.resolve()) if have else None, **props)
        return key
    if sha:
        key = K.file_sha(sha)
    elif props.get("fileUniqueId"):
        key = K.media_uid(props["fileUniqueId"])
    else:
        key = fallback_key
    if key is None:
        return None
    labels = [cls] if cls in ("Document", "Video", "RoundVideo", "Animation", "Audio", "VoiceNote",
                              "Sticker", "CustomEmoji") else ["Document"]
    g.node(key, *labels, sendMode=send_mode, sha256=sha,
           localPath=str(path.resolve()) if have else None, **props)
    if not have and rel_path:
        g.nodes[key].props["fileStatus"] = "not_downloaded"
    return key


def attachment(g: Graph, msg_key: str, media_key: str, idx: int, t: int | None, **props) -> None:
    """Create the MediaAttachment occurrence (spoiler/TTL live here) and link message -> media."""
    ak = K.attachment(msg_key, idx)
    g.node(ak, "MediaAttachment", **props)
    g.edge("ATTACHMENT_OF", ak, msg_key, t=t)
    g.edge("HAS_MEDIA", msg_key, media_key, t=t, **{k: v for k, v in props.items() if k == "groupedId"})


__all__ = ["ts", "run_id", "attach_file", "attachment", "M"]
