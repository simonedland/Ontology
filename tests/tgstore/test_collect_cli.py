import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("telethon")
from tgstore import collect


def test_cli_needs_work_and_parses_backfill(monkeypatch):
    with pytest.raises(SystemExit):
        collect.main(["--chat", "@group"])
    seen = {}
    monkeypatch.setattr(collect, "run", lambda args: asyncio.sleep(0, seen.update(vars(args))))
    collect.main(["--chat", "@a", "--chat", "-100123", "--backfill", "all", "--follow", "--url", "http://x"])
    assert seen["chat"] == ["@a", "-100123"] and seen["backfill"] is None and seen["follow"]
    assert seen["url"] == "http://x" and not seen["members"]
    collect.main(["--chat", "@a", "--members"])   # members alone is enough work
    assert seen["members"] and seen["backfill"] == 0


def test_outbox_batches_and_flushes(monkeypatch):
    sent = []
    monkeypatch.setattr(collect, "push", lambda recs, url, token: sent.append((list(recs), url, token)) or {})

    async def go():
        out = collect.Outbox("http://x", "t", batch=2)
        await out.add({"kind": "user", "id": 1})
        assert sent == []
        await out.add({"kind": "user", "id": 2}, {"kind": "user", "id": 3})   # reaches batch size
        assert len(sent) == 1 and len(sent[0][0]) == 3
        await out.add({"kind": "user", "id": 4})
        await out.flush()
        await out.flush()   # nothing buffered: no request
        return out

    asyncio.run(go())
    assert [len(b) for b, *_ in sent] == [3, 1] and sent[0][1:] == ("http://x", "t")


def test_session_choice():
    assert collect._session("tgstore") == "tgstore"
    from telethon.sessions import StringSession

    real = StringSession()
    real.set_dc(2, "149.154.167.51", 443)
    real.auth_key = SimpleNamespace(key=b"k" * 256)
    assert isinstance(collect._session(real.save()), StringSession)
