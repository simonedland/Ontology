import json

import pytest
from fastapi.testclient import TestClient

from tgstore.inbox import Inbox


@pytest.fixture
def api(store, database_url, monkeypatch):
    monkeypatch.setenv("TGSTORE_DATABASE_URL", database_url)
    from tgstore import api

    monkeypatch.setattr(api, "TOKEN", "secret")
    with TestClient(api.app, headers={"Authorization": "Bearer secret"}) as client:
        yield client


RECORDS = [
    {"kind": "chat", "id": -1001, "type": "supergroup", "title": "G"},
    {"kind": "message", "chat_id": -1001, "id": 1, "user_id": 42, "date": "2026-10-18T12:00:00Z",
     "text": "hello world", "user": {"username": "alice"}},
]


def test_ndjson_json_array_and_single_object(api):
    ndjson = "\n".join(json.dumps(r) for r in RECORDS)
    assert api.post("/v1/records", content=ndjson).json() == {"chats": 1, "users": 1, "messages": 1, "memberships": 0}
    assert api.post("/v1/records", json=RECORDS).status_code == 200
    assert api.post("/v1/records", json={"records": RECORDS}).status_code == 200
    assert api.post("/v1/records", json=RECORDS[0]).json()["chats"] == 1
    u = api.get("/v1/users/42").json()
    assert u["username"] == "alice" and u["chats"][0]["title"] == "G"
    assert [m["text"] for m in api.get("/v1/users/42/messages").json()] == ["hello world"]
    assert api.get("/v1/users", params={"username": "@Alice"}).json()[0]["id"] == 42
    assert api.get("/v1/users", params={"after": 42}).json() == []
    assert api.get("/v1/chats/-1001/members").json()[0]["user_id"] == 42
    assert api.get("/v1/search", params={"q": "hello"}).json()[0]["id"] == 1


def test_errors(api):
    assert api.post("/v1/records", content="{nope").status_code == 400
    r = api.post("/v1/records", json=[{"kind": "message", "id": 1}])
    assert r.status_code == 422 and "chat_id" in r.json()["detail"]
    assert api.get("/v1/users/999").status_code == 404
    assert api.post("/v1/records", json=RECORDS, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_inbox_ingests_and_moves_files(store, tmp_path):
    inbox = Inbox(store.ingest, tmp_path)
    for d in (inbox.done, inbox.failed):
        d.mkdir()
    (tmp_path / "a.ndjson").write_text("\n".join(json.dumps(r) for r in RECORDS))
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.json").write_text(json.dumps([{"kind": "user", "id": 7}]))
    (tmp_path / "bad.json").write_text('[{"kind": "?"}]')
    (tmp_path / "c.ndjson.tmp").write_text("ignored")

    inbox.scan()  # first sight: files may still be growing
    assert store.user(42) is None
    inbox.scan()
    assert store.user(42)["username"] == "alice" and store.user(7)
    assert sorted(p.name.split("-", 1)[1] for p in inbox.done.iterdir()) == ["a.ndjson", "sub__b.json"]
    assert {p.suffix for p in inbox.failed.iterdir()} == {".json", ".txt"}
    assert (tmp_path / "c.ndjson.tmp").exists()
