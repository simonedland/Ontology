"""Collector behaviour against a stub Telethon (no network, no real account)."""
from __future__ import annotations

import asyncio
import json
import sys
import types as pytypes
from datetime import UTC, datetime

import pytest

from tgo.ingest import collector


class _TL:
    def __init__(self, *args, **kw):
        self.args = args
        self.__dict__.update(kw)

    def to_dict(self):
        return {"_": type(self).__name__,
                **{k: v for k, v in self.__dict__.items() if k != "args" and not k.startswith("_")}}


class Msg:
    def __init__(self, mid, size=None, photo=False):
        self.id = mid
        self.voice = self.video = self.document = None
        self.photo = object() if photo else None
        self.file = pytypes.SimpleNamespace(size=size, ext=".jpg") if photo else None

    def to_dict(self):
        return {"_": "Message", "id": self.id, "peer_id": {"_": "PeerChannel", "channel_id": 7},
                "date": datetime(2026, 1, 1, tzinfo=UTC)}

    async def download_media(self, file):
        with open(file, "wb") as f:
            f.write(b"jpeg")
        return file


def _stub_telethon(msgs: list[Msg], flood_on=()):
    class User(_TL): ...
    class Channel(_TL): ...
    class ChatFull(_TL): ...
    class ChannelParticipants(_TL): ...
    User.__module__ = Channel.__module__ = "telethon.tl.types"
    ChatFull.__module__ = "telethon.tl.types.messages"  # as in Telethon: namespaced module, bare "_"
    ChannelParticipants.__module__ = "telethon.tl.types.channels"

    class RPCError(Exception): ...

    class FloodWaitError(RPCError):
        def __init__(self, seconds):
            super().__init__(seconds)
            self.seconds = seconds

    class GetParticipantsRequest(_TL): ...

    class TelegramClient:
        calls: list = []

        def __init__(self, session, api_id, api_hash, **kw):
            TelegramClient.calls.append((session, api_id, api_hash, kw))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_me(self):
            return User(id=1, is_self=True)

        async def get_entity(self, target):
            if target in flood_on:
                raise FloodWaitError(3600)
            return Channel(id=7, title="chan")

        async def __call__(self, request):
            if isinstance(request, GetParticipantsRequest):
                return ChannelParticipants(participants=[] if request.args[2] else [{"user_id": 5}], users=[])
            return ChatFull(full_chat={"_": "ChannelFull", "id": 7}, chats=[], users=[])

        async def iter_messages(self, ent, limit, min_id=0, **kw):
            for m in sorted((m for m in msgs if m.id > min_id), key=lambda m: -m.id)[:limit]:
                yield m

    tl = pytypes.ModuleType("telethon")
    tl.__version__ = "1.45.0-stub"
    tl.tl = pytypes.SimpleNamespace(alltlobjects=pytypes.SimpleNamespace(LAYER=229))
    tl.TelegramClient = TelegramClient
    tl.types = pytypes.SimpleNamespace(User=User, Channel=Channel, ChannelParticipantsRecent=_TL)
    tl.functions = pytypes.SimpleNamespace(
        users=pytypes.SimpleNamespace(GetFullUserRequest=_TL),
        channels=pytypes.SimpleNamespace(GetFullChannelRequest=_TL, GetParticipantsRequest=GetParticipantsRequest),
        messages=pytypes.SimpleNamespace(GetFullChatRequest=_TL))
    errors = pytypes.ModuleType("telethon.errors")
    errors.RPCError, errors.FloodWaitError = RPCError, FloodWaitError
    return tl, errors


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setenv("TG_API_ID", "123")
    monkeypatch.setenv("TG_API_HASH", "abc")

    def install(msgs, flood_on=()):
        tl, errors = _stub_telethon(msgs, flood_on)
        monkeypatch.setitem(sys.modules, "telethon", tl)
        monkeypatch.setitem(sys.modules, "telethon.errors", errors)
        return tl
    return install


def _rows(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _run(out, **kw):
    asyncio.run(collector.collect(None, None, str(out / "s"), ["@chan"], out, **kw))


def test_rerun_is_incremental_and_names_are_namespaced(stub, tmp_path):
    msgs = [Msg(1), Msg(2, size=100, photo=True), Msg(3)]
    tl = stub(msgs)
    _run(tmp_path)
    assert tl.TelegramClient.calls[0][1:3] == (123, "abc")  # credentials from env
    rows = _rows(tmp_path / "messages.jsonl")
    assert [r["id"] for r in rows] == [3, 2, 1]
    assert rows[1]["_tgo"] == {"media_path": "media/7_2.jpg"} and (tmp_path / "media" / "7_2.jpg").exists()
    assert json.loads((tmp_path / "state.json").read_text())["7"] == 3

    ents = {e["_"] for e in _rows(tmp_path / "entities.jsonl")}
    assert {"Channel", "messages.ChatFull", "channels.ChannelParticipants"} <= ents

    msgs.append(Msg(4))
    _run(tmp_path)
    assert [r["id"] for r in _rows(tmp_path / "messages.jsonl")] == [3, 2, 1, 4]  # no duplicates
    assert json.loads((tmp_path / "state.json").read_text())["7"] == 4


def test_crash_rerun_does_not_duplicate(stub, tmp_path):
    stub([Msg(1), Msg(2)])
    _run(tmp_path)
    (tmp_path / "state.json").unlink()  # simulate a crash before the checkpoint was written
    _run(tmp_path)
    assert [r["id"] for r in _rows(tmp_path / "messages.jsonl")] == [2, 1]


def test_size_checks(stub, tmp_path):
    stub([Msg(1, size=None, photo=True), Msg(2, size=50 * 1024 * 1024, photo=True)])
    _run(tmp_path, max_file_mb=20)
    status = {r["id"]: r["_tgo"]["file_status"] for r in _rows(tmp_path / "messages.jsonl")}
    assert status == {1: "size_unknown", 2: "excluded_by_size"}
    assert not any((tmp_path / "media").iterdir())


def test_floodwait_becomes_gap(stub, tmp_path):
    stub([Msg(1)], flood_on={"@chan"})
    _run(tmp_path)
    gaps = [e for e in _rows(tmp_path / "entities.jsonl") if e["_"] == "tgo.Gap"]
    assert gaps and gaps[0]["reason"] == "FloodWait" and gaps[0]["seconds"] == 3600


def test_missing_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    with pytest.raises(SystemExit, match="TG_API_ID"):
        collector._credentials(None, None, str(tmp_path / "s"))
