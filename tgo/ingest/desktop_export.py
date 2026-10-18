"""Ingest a Telegram Desktop export (``result.json`` + media folders) into TGO.

Quirks handled (tdesktop export_output_json.cpp / export_data_types.cpp):

* ``from_id`` is a string (``user123`` / ``channel123``); chat ``id`` is bare;
* message media carries **no IDs**: files key on sha256 of the exported bytes
  (photos on ``photo:sha256:{hash}`` because they are renditions, not originals);
* classify from ``media_type`` + ``mime_type``, never the folder name;
* ``(File not included...)`` placeholders become a typed gap
  (``fileStatus = excluded_by_export_settings``), not a missing node;
* service actions list invited members by *name*; names are resolved to ids
  via other messages in the same chat when possible.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import keys as K
from .. import media as M
from ..graph import Graph
from ..ontology import Scope, Visibility
from .common import attach_file, attachment, run_id, ts

SRC = "desktop_export"

CHAT_TYPES = {
    "personal_chat": ("user", "PrivateChat", "private_chat"),
    "bot_chat": ("user", "PrivateChat", "private_chat"),
    "saved_messages": ("user", "PrivateChat", "private_chat"),
    "private_group": ("chat", "BasicGroup", "basic_group"),
    "private_supergroup": ("channel", "Supergroup", "supergroup"),
    "public_supergroup": ("channel", "Supergroup", "supergroup"),
    "private_channel": ("channel", "Channel", "broadcast_channel"),
    "public_channel": ("channel", "Channel", "broadcast_channel"),
}


def _text(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "".join(p if isinstance(p, str) else p.get("text", "") for p in v)
    return ""


def _fallback_label(g: Graph, key: str, label: str | None, *labels: str) -> None:
    """Export display names for *other* users are the collector's contact names
    (viewer-relative), so they never overwrite an account's self-presented name."""
    g.node(key, *labels)
    if label and not g.nodes[key].props.get("label"):
        g.nodes[key].props["label"] = label
        g.nodes[key].props["labelSource"] = "desktop_export (collector-scoped display name)"


def _excluded(path) -> bool:
    """tdesktop writes parenthesised placeholders instead of paths for skipped media."""
    return isinstance(path, str) and path.startswith("(") and path.endswith(")")


def _excluded_status(path: str) -> str:
    p = path.lower()
    if "exceeds" in p:
        return "excluded_by_size"
    if "unavailable" in p:
        return "unavailable"
    return "excluded_by_export_settings"


class DesktopExportIngester:
    def __init__(self, g: Graph, result_json: Path):
        self.g = g
        self.path = Path(result_json)
        self.root = self.path.parent
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        pi = self.data.get("personal_information") or {}
        self.collector_key = K.user(pi["user_id"]) if pi.get("user_id") else None

    def run(self) -> str:
        rid = run_id(self.path.resolve(), self.path.stat().st_size)
        with self.g.activity("CollectionRun", f"export:{rid}", agent={
                "tool": "telegram-desktop-export", "version": "json", "codeLicense": "GPL-3.0 (tdesktop)",
                "weightsLicense": "n/a"}, source="DesktopExport", collector=self.collector_key,
                file=str(self.path.name)) as run:
            self._personal()
            self._contacts()
            for chat in (self.data.get("chats") or {}).get("list", []):
                self._chat(chat)
            for chat in (self.data.get("left_chats") or {}).get("list", []):
                self._chat(chat, left=True)
        return run

    # ----------------------------------------------------------- self/ego
    def _personal(self) -> None:
        pi = self.data.get("personal_information")
        if not pi:
            return
        g, key = self.g, self.collector_key
        g.node(key, "HumanAccount", "Collector", userId=pi["user_id"],
               label=" ".join(x for x in (pi.get("first_name"), pi.get("last_name")) if x))
        for f, src in (("first_name", "first_name"), ("last_name", "last_name"), ("phone", "phone_number"),
                       ("about", "bio")):
            g.field(key, f, pi.get(src), Visibility.VISIBLE if pi.get(src) else Visibility.NOT_SET,
                    source=f"{SRC}:personal_information.{src}")
        if pi.get("username"):
            uk = K.username(pi["username"])
            g.node(uk, "Username", usernameValue=pi["username"].lstrip("@"))
            g.edge("HAS_USERNAME", key, uk, scope=Scope.PUBLIC.value)
        for p in self.data.get("profile_pictures") or []:
            if p.get("photo") and not _excluded(p["photo"]):
                pk = attach_file(g, self.root, p["photo"], "Photo", M.PHOTO_MODE, fallback_key=None,
                                 props={"label": "own profile photo", "uploadDate": ts(p.get("date_unixtime") or p.get("date"))})
                if pk:
                    g.edge("HAS_AVATAR", key, pk, t=ts(p.get("date_unixtime") or p.get("date")))

    def _contacts(self) -> None:
        g = self.g
        if not self.collector_key:
            return
        for c in (self.data.get("contacts") or {}).get("list", []):
            name = " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x)
            phone = c.get("phone_number")
            if c.get("user_id"):
                target = K.user(c["user_id"])
                g.node(target, "HumanAccount", userId=c["user_id"])
            elif phone:
                # contact without account id: only the phone identifier is known
                target = K.identifier("phone", "+" + "".join(ch for ch in phone if ch.isdigit()))
                g.node(target, "PhoneNumber", value=target.split(":", 2)[2], kind="phone")
            else:
                continue
            ann = f"contact-annotation:{self.collector_key}:{target}"
            # contact-assigned names are the collector's annotation, not the target's self-presentation
            g.node(ann, "ContactAnnotation", firstName=c.get("first_name"), lastName=c.get("last_name"),
                   label=name, attributedTo=self.collector_key, addedAt=ts(c.get("date_unixtime") or c.get("date")))
            g.edge("ANNOTATES", ann, target, scope=Scope.COLLECTOR_ONLY.value, observed_by=self.collector_key)
            g.edge("KNOWS", self.collector_key, target, scope=Scope.COLLECTOR_ONLY.value,
                   observed_by=self.collector_key, t=ts(c.get("date_unixtime") or c.get("date")))
            if phone and target.startswith("user:"):
                ik = K.identifier("phone", "+" + "".join(ch for ch in phone if ch.isdigit()))
                g.node(ik, "PhoneNumber", value=ik.split(":", 2)[2], kind="phone")
                g.edge("CONTAINS_IDENTIFIER", ann, ik, status="observed", method="contacts.phone_number")
        for fc in (self.data.get("frequent_contacts") or {}).get("list", []):
            if fc.get("type") == "user" and fc.get("id"):
                g.edge("KNOWS", self.collector_key, K.user(fc["id"]), scope=Scope.COLLECTOR_ONLY.value,
                       observed_by=self.collector_key, frequentRating=fc.get("rating"), method="frequent_contacts")

    # ----------------------------------------------------------------- chats
    def _chat(self, chat: dict, left: bool = False) -> None:
        g = self.g
        ns, cls, kind = CHAT_TYPES.get(chat.get("type"), ("chat", "Chat", "unknown"))
        cid = chat.get("id")
        if cid is None:
            return
        ckey = f"{ns}:{int(cid)}"
        if ns == "user":
            # a private chat is the conversation with that user
            g.node(ckey, "HumanAccount" if chat.get("type") != "bot_chat" else "BotAccount", userId=int(cid))
            _fallback_label(g, ckey, chat.get("name"))
        else:
            g.node(ckey, cls, chatId=int(cid), title=chat.get("name"), label=chat.get("name"), groupKind=kind)
            if self.collector_key:
                g.edge("MEMBER_OF", self.collector_key, ckey, scope=Scope.MEMBERS.value,
                       role="left" if left else ("subscriber" if cls == "Channel" else "member"))
        names: dict[str, str] = {}
        for m in chat.get("messages", []):
            if m.get("from") and m.get("from_id"):
                names[m["from"]] = m["from_id"]
            if m.get("actor") and m.get("actor_id"):
                names[m["actor"]] = m["actor_id"]
        for m in chat.get("messages", []):
            self._message(ckey, m, names)

    def _message(self, ckey: str, m: dict, names: dict[str, str]) -> None:
        g = self.g
        mk = K.message(ckey, m["id"])
        t = ts(m.get("date_unixtime") or m.get("date"))
        text = _text(m.get("text"))
        is_service = m.get("type") == "service"
        g.node(mk, "ServiceMessage" if is_service else "Message", msgId=m["id"], date=t, text=text or None,
               label=text[:60] if text else f"msg {m['id']}", editDate=ts(m.get("edited_unixtime") or m.get("edited")),
               postAuthor=m.get("author"))
        g.edge("IN_CHAT", mk, ckey, t=t)
        sender = K.peer_from_export(m.get("from_id") or m.get("actor_id"))
        if sender:
            _fallback_label(g, sender, m.get("from") or m.get("actor"),
                            "HumanAccount" if sender.startswith("user:") else "Channel")
            g.edge("SENT", sender, mk, t=t, scope=Scope.MEMBERS.value)
        if m.get("reply_to_message_id"):
            g.edge("REPLIES_TO", mk, K.message(ckey, m["reply_to_message_id"]), t=t)
        if m.get("forwarded_from"):
            src = K.peer_from_export(m.get("forwarded_from_id"))
            if src:
                _fallback_label(g, src, m["forwarded_from"])
                g.edge("FORWARD_OF", mk, src, t=t, originalName=m["forwarded_from"])
            else:
                g.node(mk, forwardedFromName=m["forwarded_from"])
                g.field(mk, "forwardOrigin", m["forwarded_from"], Visibility.MIN_OBJECT,
                        source=f"{SRC}:forwarded_from (name only)")
        for ent in m.get("text_entities") or []:
            if ent.get("type") == "mention_name" and ent.get("user_id"):
                g.edge("MENTIONS", mk, K.user(ent["user_id"]), t=t)
            elif ent.get("type") == "text_link" and ent.get("href"):
                g.node(mk, hiddenUrls=list({*g.nodes[mk].props.get("hiddenUrls", []), ent["href"]}))
        for r in m.get("reactions") or []:
            for rec in r.get("recent") or []:
                who = K.peer_from_export(rec.get("from_id"))
                if who:
                    emo = r.get("emoji") or r.get("document_id") or r.get("type")
                    g.edge("REACTED", who, mk, t=ts(rec.get("date")), reaction=emo, discriminator=str(emo))
        self._media(mk, m, t)
        if is_service:
            self._action(mk, ckey, sender, m, names, t)

    def _media(self, mk: str, m: dict, t: int | None) -> None:
        g = self.g
        c = M.classify_export(m)
        if not c:
            return
        cls, mode = c
        rel = m.get("photo") if cls == "Photo" else m.get("file")
        occ = M.ttl_props(m.get("self_destruct_period_seconds"), m.get("media_spoiler"))
        props = {k: v for k, v in {
            "mime": m.get("mime_type") or ("image/jpeg" if cls == "Photo" else None),
            "width": m.get("width"), "height": m.get("height"), "duration": m.get("duration_seconds"),
            "fileName": m.get("file_name"), "size": m.get("file_size") or m.get("photo_file_size"),
            "title": m.get("title"), "performer": m.get("performer"), "stickerAlt": m.get("sticker_emoji"),
            "label": m.get("file_name") or f"{cls} in {mk}",
        }.items() if v is not None}
        if _excluded(rel):
            fk = f"{mk}/unexported:{cls}"
            g.node(fk, cls if cls != "Photo" else "Photo", fileStatus=_excluded_status(rel),
                   sendMode=mode, **props)
            attachment(g, mk, fk, 0, t, **occ)
            return
        fk = attach_file(g, self.root, rel, cls, mode, fallback_key=f"{mk}/media", props=props)
        if fk:
            attachment(g, mk, fk, 0, t, **occ)

    def _action(self, mk: str, ckey: str, actor: str | None, m: dict, names: dict[str, str], t) -> None:
        g = self.g
        action = m.get("action", "")
        g.node(mk, action=action)
        if action in ("invite_members", "create_group"):
            for name in m.get("members") or []:
                uid = names.get(name)
                if not uid:
                    hist = g.nodes[mk].props.setdefault("unresolvedMembers", [])
                    hist.append(name)
                    continue
                uk = K.peer_from_export(uid)
                g.edge("MEMBER_OF", uk, ckey, t=t, role="member", since=t, scope=Scope.MEMBERS.value)
                if actor and actor != uk:
                    g.edge("INVITED_BY", uk, actor, t=t, chat=ckey, scope=Scope.MEMBERS.value, evidence=[mk],
                           method="service:invite_members (name-resolved)")
        if action in ("create_group", "create_channel", "migrate_from_group") and actor:
            g.edge("CREATED", actor, ckey, t=t, evidence=[mk])
            g.node(ckey, createdAt=t)
        elif action == "join_group_by_link" and actor:
            g.edge("MEMBER_OF", actor, ckey, t=t, role="member", since=t, scope=Scope.MEMBERS.value)
            inv = K.peer_from_export(names.get(m.get("inviter", "")))
            if inv:
                g.edge("INVITED_BY", actor, inv, t=t, chat=ckey, scope=Scope.MEMBERS.value, evidence=[mk])
        elif action == "join_group_via_community" and actor:
            g.edge("MEMBER_OF", actor, ckey, t=t, role="member", since=t, scope=Scope.MEMBERS.value,
                   method="via community")
        elif action == "remove_members":
            for name in m.get("members") or []:
                uk = K.peer_from_export(names.get(name))
                if uk:
                    g.edge("MEMBER_OF", uk, ckey, t=t, role="left", until=t, scope=Scope.MEMBERS.value)
        elif action == "edit_group_title":
            hist = g.nodes[ckey].props.setdefault("titleHistory", [])
            if m.get("title") not in hist:
                hist.append(m.get("title"))


def ingest(g: Graph, result_json: str | Path) -> str:
    return DesktopExportIngester(g, Path(result_json)).run()
