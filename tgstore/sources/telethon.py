"""Telethon objects -> tgstore records. Needs the ``collect`` extra (``pip install telethon``).

Chat ids are Telethon's marked peer ids (``utils.get_peer_id``): ``-100…`` for supergroups/channels,
``-…`` for basic groups, the user id for private chats.
"""

from __future__ import annotations

from collections.abc import Iterator

from telethon import types, utils

# MessageAction class -> membership status of the users it names
ACTIONS = {
    types.MessageActionChatAddUser: "member",
    types.MessageActionChatJoinedByLink: "member",
    types.MessageActionChatJoinedByRequest: "member",
    types.MessageActionChatCreate: "member",
    types.MessageActionChatDeleteUser: "left",
}

# participant class (from iter_participants) -> membership status
PARTICIPANTS = {
    types.ChannelParticipantCreator: "creator", types.ChatParticipantCreator: "creator",
    types.ChannelParticipantAdmin: "admin", types.ChatParticipantAdmin: "admin",
    types.ChannelParticipantLeft: "left",
}


def _clean(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if v is not None and v != {}}


def _flags(obj, *names: str) -> dict:
    """True-valued boolean flags, for ``extra``."""
    return {n: True for n in names if getattr(obj, n, None)}


def user_record(u: types.User) -> dict:
    return _clean({"kind": "user", "id": u.id, "username": u.username, "first_name": u.first_name,
                   "last_name": u.last_name, "phone": u.phone, "is_bot": bool(u.bot), "lang_code": u.lang_code,
                   "extra": _flags(u, "premium", "verified", "scam", "fake", "deleted")})


def chat_record(c: types.Chat | types.Channel | types.User) -> dict:
    """A group, supergroup, channel, or (for a ``User``) the private chat with that user."""
    if isinstance(c, types.User):
        return _clean({"kind": "chat", "id": c.id, "type": "private", "username": c.username,
                       "title": " ".join(filter(None, (c.first_name, c.last_name))) or None})
    if isinstance(c, (types.Channel, types.ChannelForbidden)):
        kind = "channel" if c.broadcast else "supergroup"
    else:
        kind = "group"
    return _clean({"kind": "chat", "id": utils.get_peer_id(c), "type": kind, "title": c.title,
                   "username": getattr(c, "username", None),
                   "member_count": getattr(c, "participants_count", None),
                   "extra": _flags(c, "forum", "megagroup", "gigagroup", "verified", "scam", "fake")})


def participant_records(chat_id: int, u: types.User) -> Iterator[dict]:
    """The user and their membership, for a user from ``client.iter_participants`` (has ``.participant``)."""
    yield user_record(u)
    p = getattr(u, "participant", None)
    status = PARTICIPANTS.get(type(p), "member")
    if isinstance(p, types.ChannelParticipantBanned):
        status = "kicked" if p.banned_rights.view_messages else "restricted"
    rec = {"kind": "membership", "chat_id": chat_id, "user_id": u.id, "status": status,
           "joined_at": getattr(p, "date", None), "extra": {}}
    if rec["joined_at"] is not None:
        rec["joined_at"] = rec["joined_at"].isoformat()
    if inviter := getattr(p, "inviter_id", None):
        rec["extra"]["inviter_id"] = inviter
    if getattr(p, "rank", None):
        rec["extra"]["rank"] = p.rank
    yield _clean(rec)


def media_type(media) -> str | None:
    if media is None or isinstance(media, types.MessageMediaEmpty):
        return None
    if isinstance(media, types.MessageMediaPhoto):
        return "photo"
    if isinstance(media, types.MessageMediaDocument):
        attrs = {type(a): a for a in getattr(media.document, "attributes", None) or []}
        if types.DocumentAttributeSticker in attrs:
            return "sticker"
        if audio := attrs.get(types.DocumentAttributeAudio):
            return "voice" if audio.voice else "audio"
        if video := attrs.get(types.DocumentAttributeVideo):
            if video.round_message:
                return "video_note"
            return "animation" if types.DocumentAttributeAnimated in attrs else "video"
        return "document"
    return type(media).__name__.removeprefix("MessageMedia").lower()   # geo, contact, poll, webpage, ...


def message_record(m: types.Message | types.MessageService) -> dict:
    sender = m.sender_id
    rec = {"kind": "message", "chat_id": m.chat_id, "id": m.id, "date": m.date, "text": m.message or None,
           "edit_date": getattr(m, "edit_date", None), "media_type": media_type(getattr(m, "media", None)),
           "views": getattr(m, "views", None), "extra": {}}
    if sender is not None and sender > 0:
        rec["user_id"] = sender
    elif sender is not None:
        rec["extra"]["sender_chat_id"] = sender   # channel post or anonymous admin
    if isinstance(m.reply_to, types.MessageReplyHeader) and m.reply_to.reply_to_msg_id:
        if m.reply_to.reply_to_peer_id:
            rec["extra"]["reply_to_chat_id"] = utils.get_peer_id(m.reply_to.reply_to_peer_id)
        rec["reply_to_id"] = m.reply_to.reply_to_msg_id
    if fwd := getattr(m, "fwd_from", None):
        if fwd.from_id is not None:
            pid = utils.get_peer_id(fwd.from_id)
            rec["forward_from_user_id" if pid > 0 else "forward_from_chat_id"] = pid
        if fwd.from_name:
            rec["extra"]["forwarded_from"] = fwd.from_name
    if getattr(m, "post_author", None):
        rec["extra"]["author"] = m.post_author
    if getattr(m, "grouped_id", None):
        rec["extra"]["grouped_id"] = m.grouped_id
    if action := getattr(m, "action", None):
        rec["extra"]["action"] = type(action).__name__
    for key in ("date", "edit_date"):
        if rec[key] is not None:
            rec[key] = rec[key].isoformat()
    return _clean(rec)


def membership_records(m: types.MessageService) -> Iterator[dict]:
    """Memberships implied by a service message (joins, adds, removals)."""
    status = ACTIONS.get(type(m.action))
    if status is None:
        return
    a = m.action
    ids = getattr(a, "users", None) or ([a.user_id] if hasattr(a, "user_id") else [m.sender_id])
    for uid in ids:
        if uid is None or uid <= 0:
            continue
        rec = {"kind": "membership", "chat_id": m.chat_id, "user_id": uid, "status": status}
        if status == "left" and uid != m.sender_id:
            rec["status"] = "kicked"
        elif status == "member":
            rec["joined_at"] = m.date.isoformat()
        if isinstance(a, types.MessageActionChatCreate) and uid == m.sender_id:
            rec["status"] = "creator"
        yield rec


def records(m, sender=None) -> Iterator[dict]:
    """Everything one message yields: its sender's ``user`` record, the message, implied memberships."""
    if isinstance(sender, types.User):
        yield user_record(sender)
    yield message_record(m)
    if isinstance(m, types.MessageService):
        yield from membership_records(m)


def action_records(event) -> Iterator[dict]:
    """Records for a live ``events.ChatAction`` (some joins/leaves arrive without a service message)."""
    if event.action_message:
        yield from records(event.action_message)
        return
    status = ("member" if event.user_joined or event.user_added else "kicked" if event.user_kicked
              else "left" if event.user_left else None)
    for uid in event.user_ids if status else []:
        yield {"kind": "membership", "chat_id": event.chat_id, "user_id": uid, "status": status}
