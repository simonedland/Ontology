from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("telethon")
from telethon import types

from tgstore.sources import telethon as tg

WHEN = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
GROUP = types.PeerChannel(1234567890)


def doc(*attrs):
    return types.MessageMediaDocument(document=types.Document(
        id=1, access_hash=0, file_reference=b"", date=WHEN, mime_type="x", size=1, dc_id=1, attributes=list(attrs)))


def test_user_and_chat_records():
    u = types.User(id=42, username="alice", first_name="Alice", bot=False, premium=True)
    assert tg.user_record(u) == {"kind": "user", "id": 42, "username": "alice", "first_name": "Alice",
                                 "is_bot": False, "extra": {"premium": True}}
    ch = types.Channel(id=1234567890, title="Group", photo=types.ChatPhotoEmpty(), date=WHEN, megagroup=True,
                       username="grp", participants_count=10)
    assert tg.chat_record(ch) == {"kind": "chat", "id": -1001234567890, "type": "supergroup", "title": "Group",
                                  "username": "grp", "member_count": 10, "extra": {"megagroup": True}}
    bc = types.Channel(id=5, title="News", photo=types.ChatPhotoEmpty(), date=WHEN, broadcast=True)
    assert tg.chat_record(bc)["type"] == "channel"
    basic = types.Chat(id=5, title="Basic", photo=types.ChatPhotoEmpty(), participants_count=3, date=WHEN, version=1)
    assert tg.chat_record(basic) == {"kind": "chat", "id": -5, "type": "group", "title": "Basic", "member_count": 3}
    assert tg.chat_record(u) == {"kind": "chat", "id": 42, "type": "private", "username": "alice", "title": "Alice"}


def test_message_record():
    m = types.Message(id=9, peer_id=GROUP, date=WHEN, message="hello", from_id=types.PeerUser(42),
                      reply_to=types.MessageReplyHeader(reply_to_msg_id=8), edit_date=WHEN, views=3,
                      fwd_from=types.MessageFwdHeader(date=WHEN, from_id=types.PeerChannel(77), from_name="Src"),
                      media=types.MessageMediaPhoto())
    assert tg.message_record(m) == {
        "kind": "message", "chat_id": -1001234567890, "id": 9, "user_id": 42, "date": "2026-10-18T12:00:00+00:00",
        "text": "hello", "reply_to_id": 8, "edit_date": "2026-10-18T12:00:00+00:00", "media_type": "photo",
        "views": 3, "forward_from_chat_id": -1000000000077, "extra": {"forwarded_from": "Src"}}
    anon = types.Message(id=10, peer_id=GROUP, date=WHEN, message="", from_id=GROUP, post_author="Admin")
    rec = tg.message_record(anon)
    assert "user_id" not in rec and "text" not in rec
    assert rec["extra"] == {"sender_chat_id": -1001234567890, "author": "Admin"}


def test_media_types():
    assert tg.media_type(None) is None and tg.media_type(types.MessageMediaEmpty()) is None
    assert tg.media_type(doc(types.DocumentAttributeAudio(duration=1, voice=True))) == "voice"
    assert tg.media_type(doc(types.DocumentAttributeAudio(duration=1))) == "audio"
    assert tg.media_type(doc(types.DocumentAttributeVideo(duration=1, w=1, h=1, round_message=True))) == "video_note"
    gif = doc(types.DocumentAttributeVideo(duration=1, w=1, h=1), types.DocumentAttributeAnimated())
    assert tg.media_type(gif) == "animation"
    sticker = doc(types.DocumentAttributeSticker(alt="x", stickerset=types.InputStickerSetEmpty()))
    assert tg.media_type(sticker) == "sticker"
    assert tg.media_type(doc(types.DocumentAttributeFilename("a.pdf"))) == "document"
    assert tg.media_type(types.MessageMediaGeo(geo=types.GeoPointEmpty())) == "geo"


def test_service_messages_and_records():
    add = types.MessageService(id=1, peer_id=GROUP, date=WHEN, from_id=types.PeerUser(42),
                               action=types.MessageActionChatAddUser(users=[7, 9]))
    sender = types.User(id=42, first_name="Alice")
    recs = list(tg.records(add, sender))
    assert [r["kind"] for r in recs] == ["user", "message", "membership", "membership"]
    assert recs[1]["extra"] == {"action": "MessageActionChatAddUser"}
    assert recs[2] == {"kind": "membership", "chat_id": -1001234567890, "user_id": 7, "status": "member",
                       "joined_at": "2026-10-18T12:00:00+00:00"}

    def status(action, sender=42):
        m = types.MessageService(id=2, peer_id=GROUP, date=WHEN, from_id=types.PeerUser(sender), action=action)
        return [(r["user_id"], r["status"]) for r in tg.membership_records(m)]

    assert status(types.MessageActionChatDeleteUser(user_id=42)) == [(42, "left")]
    assert status(types.MessageActionChatDeleteUser(user_id=7)) == [(7, "kicked")]
    assert status(types.MessageActionChatJoinedByLink(inviter_id=1)) == [(42, "member")]
    assert status(types.MessageActionChatCreate(title="t", users=[42, 7])) == [(42, "creator"), (7, "member")]
    assert status(types.MessageActionPinMessage()) == []


def test_action_records_without_service_message():
    ev = SimpleNamespace(action_message=None, user_joined=False, user_added=False, user_kicked=True,
                         user_left=False, user_ids=[7], chat_id=-1001234567890)
    assert list(tg.action_records(ev)) == [
        {"kind": "membership", "chat_id": -1001234567890, "user_id": 7, "status": "kicked"}]
    ev.user_kicked = False
    assert list(tg.action_records(ev)) == []


def test_participant_records():
    def member(p, uid=7):
        u = types.User(id=uid, first_name="Bob")
        u.participant = p
        return list(tg.participant_records(-1001234567890, u))

    user, ms = member(types.ChannelParticipantCreator(user_id=7, admin_rights=types.ChatAdminRights(), rank="boss"))
    assert user == {"kind": "user", "id": 7, "first_name": "Bob", "is_bot": False}
    assert ms == {"kind": "membership", "chat_id": -1001234567890, "user_id": 7, "status": "creator",
                  "extra": {"rank": "boss"}}
    _, ms = member(types.ChannelParticipant(user_id=7, date=WHEN))
    assert ms["status"] == "member" and ms["joined_at"] == "2026-10-18T12:00:00+00:00"
    _, ms = member(types.ChannelParticipantAdmin(user_id=7, promoted_by=1, date=WHEN,
                                                 admin_rights=types.ChatAdminRights(), inviter_id=42))
    assert ms["status"] == "admin" and ms["extra"] == {"inviter_id": 42}
    _, ms = member(types.ChannelParticipantBanned(peer=types.PeerUser(7), kicked_by=1, date=WHEN,
                                                  banned_rights=types.ChatBannedRights(until_date=None,
                                                                                       view_messages=True)))
    assert ms["status"] == "kicked"
    _, ms = member(types.ChatParticipantAdmin(user_id=7, inviter_id=1, date=WHEN))
    assert ms["status"] == "admin"
    _, ms = member(None)   # basic group without participant info
    assert ms["status"] == "member"
