import json

from tgstore.sources import desktop

GROUP = {
    "name": "Example group", "type": "private_supergroup", "id": 1234567890,
    "messages": [
        {"id": 1, "type": "service", "date": "2026-10-18T12:00:00", "date_unixtime": "1792324800",
         "actor": "Alice", "actor_id": "user42", "action": "create_group", "title": "Example group",
         "members": ["Alice", "Bob"], "text": ""},
        {"id": 2, "type": "message", "date_unixtime": "1792324860", "from": "Bob", "from_id": "user7",
         "text": ["see ", {"type": "link", "text": "https://example.org"}, "!"]},
        {"id": 3, "type": "message", "date_unixtime": "1792324920", "edited_unixtime": "1792325000",
         "from": "Alice", "from_id": "user42", "reply_to_message_id": 2, "text": "",
         "photo": "photos/photo_1.jpg", "forwarded_from": "Some Name"},
        {"id": 4, "type": "message", "date_unixtime": "1792324980", "from": "Example group",
         "from_id": "channel1234567890", "author": "Admin", "text": "anon",
         "media_type": "voice_message", "file": "voice/1.ogg"},
        {"id": 5, "type": "service", "date_unixtime": "1792325040", "actor": "Carol", "actor_id": "user9",
         "action": "join_group_by_link", "inviter": "Alice", "text": ""},
        {"id": 6, "type": "service", "date_unixtime": "1792325100", "actor": "Alice", "actor_id": "user42",
         "action": "remove_members", "members": ["Bob", "Unknown person"], "text": ""},
    ],
}


def by_kind(recs):
    out = {}
    for r in recs:
        out.setdefault(r["kind"], []).append(r)
    return out


def test_peer_and_chat_ids():
    assert desktop.peer_id("user42") == 42
    assert desktop.peer_id("chat5") == -5
    assert desktop.peer_id("channel1234567890") == -1001234567890
    assert desktop.peer_id(None) is None and desktop.peer_id("weird") is None
    assert desktop.chat_id({"id": 1234567890, "type": "public_channel"}) == -1001234567890
    assert desktop.chat_id({"id": 5, "type": "private_group"}) == -5
    assert desktop.chat_id({"id": 42, "type": "personal_chat"}) == 42


def test_single_chat_export():
    recs = by_kind(desktop.records_from_export(GROUP))
    cid = -1001234567890
    assert recs["chat"] == [{"kind": "chat", "id": cid, "type": "supergroup", "title": "Example group"}]
    msgs = {m["id"]: m for m in recs["message"]}
    assert len(msgs) == 6 and all(m["chat_id"] == cid for m in msgs.values())

    assert msgs[2]["text"] == "see https://example.org!"
    assert msgs[2]["user_id"] == 7 and msgs[2]["user"] == {"id": 7, "extra": {"name": "Bob"}}
    assert msgs[2]["date"] == 1792324860

    assert msgs[3]["reply_to_id"] == 2 and msgs[3]["edit_date"] == 1792325000
    assert msgs[3]["media_type"] == "photo" and msgs[3]["extra"]["forwarded_from"] == "Some Name"
    assert "text" not in msgs[3]

    assert "user_id" not in msgs[4] and msgs[4]["extra"]["sender_chat_id"] == cid
    assert msgs[4]["extra"]["author"] == "Admin" and msgs[4]["media_type"] == "voice"
    assert msgs[1]["extra"]["action"] == "create_group"

    members = {(m["user_id"], m["status"]) for m in recs["membership"]}
    assert members == {(42, "member"), (7, "member"), (42, "creator"), (9, "member"), (7, "kicked")}
    assert all(m["chat_id"] == cid for m in recs["membership"])


def test_full_account_export(tmp_path):
    doc = {"personal_information": {"user_id": 42, "first_name": "Alice", "username": "@alice",
                                    "phone_number": "+1 555"},
           "chats": {"list": [GROUP, {"name": "Bob", "type": "personal_chat", "id": 7, "messages": [
               {"id": 1, "type": "message", "date_unixtime": "1792324800", "from": "Bob", "from_id": "user7",
                "text": "hi"}]}]},
           "left_chats": {"list": [{"name": "Old", "type": "private_group", "id": 3, "messages": []}]}}
    path = tmp_path / "result.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    recs = list(desktop.records(path))
    assert recs[0]["kind"] == "user" and recs[0]["username"] == "alice" and recs[0]["phone"] == "+1 555"
    chats = [r for r in recs if r["kind"] == "chat"]
    assert [(c["id"], c["type"]) for c in chats] == [(-1001234567890, "supergroup"), (7, "private"), (-3, "group")]
    mine = [(r["chat_id"], r["status"]) for r in recs if r["kind"] == "membership" and r["user_id"] == 42
            and "joined_at" not in r]
    assert mine == [(-1001234567890, "member"), (-3, "left")]   # the exporter, not the private chat
    json.dumps(recs)   # everything is plain JSON
