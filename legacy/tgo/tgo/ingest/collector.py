"""Live MTProto collector (Telethon) -> the directory layout read by ``mtproto.py``.

    pip install "tgo[collect]"            # telethon>=1.45,<2 (v2 drops to_dict/takeout)
    set TG_API_ID=123 & set TG_API_HASH=abc
    python -m tgo collect --target @somechannel --limit 500 --out data/run1

Writes raw ``to_dict()`` objects (bytes as hex, datetimes as ISO) so ingestion
stays a pure, re-runnable function of the collected files.  Telethon names
namespaced wrappers by bare class (``users.UserFull`` -> ``"UserFull"``, which
collides with the inner ``UserFull``), so top-level records get the full TL
name in ``_``; nested objects are left as Telethon wrote them.  Every run is a
CollectionRun with the collector account recorded, because viewer-relative
facts depend on who looked.

Re-running into the same ``--out`` is incremental: message ids already in
``messages.jsonl`` are never written twice, ``state.json`` keeps a per-peer
checkpoint (advanced only when a target finishes), media files are named
``{peer}_{msg}{ext}`` and reused, and a FloodWait or RPC error on one target is
recorded as a ``tgo.Gap`` instead of aborting the run.

Credentials come from ``TG_API_ID`` / ``TG_API_HASH`` (not the command line,
which ends up in shell history); the session file defaults to
``~/.tgo/collector.session`` and grants full account access - keep it out of
the repo.

Collection must have a documented lawful basis (GDPR Art. 6/9, Telegram ToS);
the collector deliberately does not fetch other users' privacy rules, does not
brute-force anything and never downloads files above ``--max-file-mb``.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

MAX_PARTICIPANTS = 10_000  # Telegram stops listing members of large groups well before this
FLOOD_SLEEP_THRESHOLD = 120  # FloodWaits up to this many seconds are slept through by Telethon


def _default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, (bytes, bytearray)):
        return o.hex()
    return str(o)


def _tl(obj) -> dict:
    """``to_dict()`` with ``_`` set to the namespaced TL name for wrapper types."""
    d = obj.to_dict()
    mod = type(obj).__module__
    ns = mod.removeprefix("telethon.tl.types.") if mod.startswith("telethon.tl.types.") else ""
    if ns:
        d["_"] = f"{ns}.{d['_']}"
    return d


def _peer_id(peer: dict | None) -> int | None:
    peer = peer or {}
    return peer.get("channel_id") or peer.get("chat_id") or peer.get("user_id")


class _Out:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        (root / "media").mkdir(exist_ok=True)
        self.state_path = root / "state.json"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}
        self.seen: set[tuple[int | None, int]] = set()
        msgs = root / "messages.jsonl"
        if msgs.exists():
            with open(msgs, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        m = json.loads(line)
                        self.seen.add((_peer_id(m.get("peer_id")), m.get("id")))
        self.ent = open(root / "entities.jsonl", "a", encoding="utf-8")
        self.msg = open(msgs, "a", encoding="utf-8")

    def entity(self, d: dict):
        d.setdefault("_observed_at", int(time.time()))
        self.ent.write(json.dumps(d, default=_default, ensure_ascii=False) + "\n")

    def gap(self, what: str, peer, reason: str, **extra):
        self.entity({"_": "tgo.Gap", "what": what, "peer": peer, "reason": reason, **extra})

    def message(self, d: dict) -> bool:
        key = (_peer_id(d.get("peer_id")), d.get("id"))
        if key in self.seen:
            return False
        self.seen.add(key)
        self.msg.write(json.dumps(d, default=_default, ensure_ascii=False) + "\n")
        return True

    def checkpoint(self, peer: int, max_id: int):
        """Advance a peer's checkpoint atomically, after its rows are on disk."""
        self.ent.flush()
        self.msg.flush()
        self.state[str(peer)] = max(max_id, self.state.get(str(peer), 0))
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def close(self):
        self.ent.close()
        self.msg.close()


def _credentials(api_id, api_hash, session) -> tuple[int, str, str]:
    api_id = api_id or os.environ.get("TG_API_ID")
    api_hash = api_hash or os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        raise SystemExit("set TG_API_ID and TG_API_HASH (from my.telegram.org) or pass --api-id/--api-hash")
    session = session or os.environ.get("TG_SESSION") or str(Path.home() / ".tgo" / "collector")
    Path(session).parent.mkdir(parents=True, exist_ok=True)
    return int(api_id), str(api_hash), session


async def _participants(client, functions, types, o: _Out, ent) -> None:
    """Page through GetParticipants, one ``channels.ChannelParticipants`` record per page."""
    offset = 0
    while offset < MAX_PARTICIPANTS:
        res = await client(functions.channels.GetParticipantsRequest(
            ent, types.ChannelParticipantsRecent(), offset, 200, hash=0))
        if not res.participants:
            break
        d = _tl(res)
        d["_tgo"] = {"channel_id": ent.id, "offset": offset}
        o.entity(d)
        offset += len(res.participants)


async def _messages(client, o: _Out, ent, *, limit: int, download: set[str], max_file_mb: int) -> None:
    since = o.state.get(str(ent.id), 0)
    newest = oldest = None
    fetched = 0
    async for msg in client.iter_messages(ent, limit=limit, min_id=since, wait_time=1 if limit > 3000 else 0):
        fetched += 1
        newest = max(newest or msg.id, msg.id)
        oldest = min(oldest or msg.id, msg.id)
        d = msg.to_dict()
        if (_peer_id(d.get("peer_id")), msg.id) in o.seen:
            continue
        ext = {}
        kind = ("voice" if msg.voice else "photo" if msg.photo else "video" if msg.video
                else "document" if msg.document else None)
        if kind and kind in download:
            size = msg.file.size if msg.file else None
            if not size:
                ext["file_status"] = "size_unknown"
            elif size > max_file_mb * 1024 * 1024:
                ext["file_status"] = "excluded_by_size"
            else:
                dest = o.root / "media" / f"{ent.id}_{msg.id}{msg.file.ext or ''}"
                if not dest.exists():
                    tmp = dest.with_name(dest.name + ".part")
                    if await msg.download_media(file=str(tmp)):
                        os.replace(tmp, dest)
                if dest.exists():
                    ext["media_path"] = dest.relative_to(o.root).as_posix()
        if ext:
            d["_tgo"] = ext
        o.message(d)
    if since and fetched >= limit and oldest and oldest > since + 1:
        # More new messages than --limit since the last run: the middle was not fetched.
        o.gap("messages", ent.id, "limit_reached", after_id=since, before_id=oldest)
    if newest:
        o.checkpoint(ent.id, newest)


async def collect(api_id: int | None, api_hash: str | None, session: str | None, targets: list[str], out: Path, *,
                  limit: int = 200, download: set[str] = frozenset({"voice", "document", "photo"}),
                  max_file_mb: int = 20, participants: bool = True) -> None:
    try:
        import telethon
        from telethon import TelegramClient, functions, types
        from telethon.errors import FloodWaitError, RPCError
    except ImportError as e:  # pragma: no cover - optional dependency
        raise SystemExit('pip install "tgo[collect]"  (the live collector is optional)') from e

    api_id, api_hash, session = _credentials(api_id, api_hash, session)
    o = _Out(out)
    try:
        async with TelegramClient(session, api_id, api_hash, flood_sleep_threshold=FLOOD_SLEEP_THRESHOLD) as client:
            me = await client.get_me()
            (out / "collector.json").write_text(json.dumps({
                "collector": me.to_dict(),
                "run": {"id": f"{me.id}-{int(time.time())}", "client": "telethon",
                        "clientVersion": telethon.__version__,
                        "layer": getattr(telethon.tl.alltlobjects, "LAYER", None), "started": int(time.time()),
                        "targets": targets, "limit": limit},
            }, default=_default, ensure_ascii=False, indent=1), encoding="utf-8")

            for target in targets:
                try:
                    ent = await client.get_entity(target)
                    o.entity(_tl(ent))
                    if isinstance(ent, types.User):
                        o.entity(_tl(await client(functions.users.GetFullUserRequest(ent))))
                        continue
                    if isinstance(ent, types.Channel):
                        full = await client(functions.channels.GetFullChannelRequest(ent))
                    else:
                        full = await client(functions.messages.GetFullChatRequest(ent.id))
                    o.entity(_tl(full))

                    if participants and isinstance(ent, types.Channel):
                        try:
                            await _participants(client, functions, types, o, ent)
                        except RPCError as exc:  # admin-only for broadcast channels
                            o.gap("participants", ent.id, type(exc).__name__)

                    await _messages(client, o, ent, limit=limit, download=set(download), max_file_mb=max_file_mb)
                except FloodWaitError as exc:
                    o.gap("target", target, "FloodWait", seconds=exc.seconds)
                except (RPCError, ValueError) as exc:  # ValueError: unresolvable username
                    o.gap("target", target, type(exc).__name__, detail=str(exc)[:200])
    finally:
        o.close()


def main(args) -> None:
    """CLI adapter: run :func:`collect` with parsed ``args``."""
    asyncio.run(collect(getattr(args, "api_id", None), getattr(args, "api_hash", None),
                        getattr(args, "session", None), args.target, Path(args.out),
                        limit=args.limit, max_file_mb=args.max_file_mb, participants=not args.no_participants))
