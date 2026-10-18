"""Ingest MTProto objects (Telethon ``to_dict()`` shape, layer 230) into TGO.

Input directory layout (written by :mod:`tgo.ingest.collector`)::

    collector.json      {"collector": {...User...}, "run": {"id", "client", "layer", "started"}}
    entities.jsonl      User / Channel / Chat / Community / users.UserFull / messages.ChatFull /
                        communities.CommunityFull / channels.ChannelParticipants /
                        messages.StickerSet / StoryItem / payments.SavedStarGifts / contacts.Contacts
    messages.jsonl      Message / MessageService (optional ``_tgo.media_path``)
    media/...           downloaded files

Dispatch is on constructor *names* (``_``), never on CRC32 IDs: ``user#31774388``
(layer 225) is ``user#b1b8cc83`` in layer 230.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import keys as K
from .. import media as M
from ..graph import Graph
from ..ontology import Scope, Visibility
from .common import attach_file, attachment, run_id, ts

SRC = "mtproto"


def _jsonl(path: Path):
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


class MTProtoIngester:
    """Adapter for MTProto/Telethon ``to_dict()`` dumps (dispatch on constructor names)."""
    def __init__(self, g: Graph, root: Path):
        self.g = g
        self.root = Path(root)
        meta = json.loads((self.root / "collector.json").read_text(encoding="utf-8"))
        self.run_meta = meta.get("run", {})
        self.collector = meta.get("collector") or {}
        self.collector_key = K.user(self.collector["id"]) if self.collector.get("id") else None
        self.broadcast: set[str] = set()

    # ------------------------------------------------------------------ main
    def activity(self):
        """The CollectionRun context for this collection (same run key for every batch)."""
        rid = self.run_meta.get("id") or run_id(self.root.resolve())
        return self.g.activity("CollectionRun", f"mtproto:{rid}", agent={
            "tool": self.run_meta.get("client", "telethon"), "version": str(self.run_meta.get("clientVersion", "")),
            "codeLicense": "MIT", "weightsLicense": "n/a"},
            source="MTProto", layer=self.run_meta.get("layer", 230),
            collector=self.collector_key, method="user-account")

    def run(self) -> str:
        """Ingest the whole directory into ``self.g`` (in-memory engine)."""
        with self.activity() as run:
            if self.collector:
                self.user(self.collector, is_collector=True)
            for obj in _jsonl(self.root / "entities.jsonl"):
                self.entity(obj)
            for obj in _jsonl(self.root / "messages.jsonl"):
                self.message(obj)
        return run

    @staticmethod
    def _wrapper_name(o: dict) -> str:
        """Telethon ``to_dict()`` writes the *bare* class name for namespaced TL types
        (users.UserFull -> 'UserFull'), colliding with inner types; resolve by shape."""
        t = o.get("_", "")
        if "." in t:
            return t
        if t == "UserFull" and "full_user" in o:
            return "users.UserFull"
        if t == "ChatFull" and "full_chat" in o:
            inner = (o.get("full_chat") or {}).get("_")
            return "communities.CommunityFull" if inner == "CommunityFull" else "messages.ChatFull"
        if t == "CommunityFull" and "full_community" in o:
            return "communities.CommunityFull"
        if t in ("ChannelParticipants", "ChannelParticipantsNotModified") and "participants" in o:
            return "channels.ChannelParticipants"
        if t == "SavedStarGifts" and "gifts" in o:
            return "payments.SavedStarGifts"
        if t == "Contacts" and "contacts" in o:
            return "contacts.Contacts"
        if t == "StickerSet" and "set" in o:
            return "messages.StickerSet"
        return t

    def entity(self, o: dict) -> None:
        """Dispatch one entities.jsonl record to its handler (unknown constructors are warned, not fatal)."""
        t = self._wrapper_name(o)
        handler = {
            "User": self.user, "Channel": self.channel, "Chat": self.basic_chat,
            "Community": self.community, "users.UserFull": self.user_full,
            "messages.ChatFull": self.chat_full, "communities.CommunityFull": self.community_full,
            "channels.ChannelParticipants": self.participants, "messages.StickerSet": self.sticker_set,
            "StoryItem": self.story, "payments.SavedStarGifts": self.gifts, "contacts.Contacts": self.contacts,
        }.get(t)
        if handler:
            handler(o)
        else:
            self.g._warn(f"mtproto: unhandled constructor {t}")

    # --------------------------------------------------------------- people
    def user(self, u: dict, is_collector: bool = False) -> str:
        """User -> Account (+ usernames, privacy-aware fields, viewer-relative contact edges, avatar)."""
        g, key = self.g, K.user(u["id"])
        cls = "DeletedAccount" if u.get("deleted") else "BotAccount" if u.get("bot") else "HumanAccount"
        labels = [cls] + (["Collector"] if is_collector or u.get("is_self") else [])
        g.node(key, *labels, userId=u["id"], isBot=bool(u.get("bot")), isDeleted=bool(u.get("deleted")),
               isPremium=u.get("premium"), isVerified=u.get("verified"), isScam=u.get("scam"),
               isFake=u.get("fake"), langCode=u.get("lang_code"), botActiveUsers=u.get("bot_active_users"),
               linkedCommunityId=u.get("linked_community_id"),
               label=(" ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or u.get("username")
                      or f"user {u['id']}"))
        vis_missing = Visibility.MIN_OBJECT if u.get("min") else Visibility.HIDDEN_BY_PRIVACY
        for f in ("first_name", "last_name"):
            g.field(key, f, u.get(f), Visibility.VISIBLE if u.get(f) else Visibility.NOT_SET, source=f"{SRC}:user.{f}")
        g.field(key, "phone", u.get("phone"), Visibility.VISIBLE if u.get("phone") else vis_missing,
                source=f"{SRC}:user.phone", observed_by=self.collector_key)
        st = (u.get("status") or {}).get("_")
        if st == "UserStatusOffline" or st == "UserStatusOnline":
            g.field(key, "lastSeen", ts((u["status"]).get("was_online") or (u["status"]).get("expires")),
                    Visibility.VISIBLE, source=f"{SRC}:user.status")
        elif st in ("UserStatusRecently", "UserStatusLastWeek", "UserStatusLastMonth"):
            g.field(key, "lastSeen", None, Visibility.HIDDEN_BY_PRIVACY, source=f"{SRC}:user.status={st}")
        names = [u.get("username")] if u.get("username") else []
        names += [x["username"] for x in u.get("usernames") or [] if x.get("username")]
        for i, name in enumerate(dict.fromkeys(names)):
            uk = K.username(name)
            act = next((x for x in u.get("usernames") or [] if x.get("username") == name), {})
            g.node(uk, "Username", usernameValue=name, editable=act.get("editable"), active=act.get("active", True),
                   collectible=act.get("editable") is False)
            g.edge("HAS_USERNAME", key, uk, scope=Scope.PUBLIC.value, t=ts(u.get("_observed_at")))
        # viewer-relative facts: pair (collector, target)
        if self.collector_key and not is_collector:
            if u.get("contact"):
                g.edge("KNOWS", self.collector_key, key, scope=Scope.COLLECTOR_ONLY.value,
                       observed_by=self.collector_key, mutual=bool(u.get("mutual_contact")))
            if u.get("close_friend"):
                g.edge("CLOSE_FRIEND_OF", key, self.collector_key, scope=Scope.COLLECTOR_ONLY.value,
                       observed_by=self.collector_key)
        if (u.get("photo") or {}).get("photo_id"):
            pk = K.photo(u["photo"]["photo_id"])
            av = (u.get("_tgo") or {}).get("avatar_path")
            attach_file(self.g, self.root, av, "Photo", M.PHOTO_MODE, fallback_key=pk,
                        props={"photoId": u["photo"]["photo_id"], "hasVideo": u["photo"].get("has_video")})
            g.edge("HAS_AVATAR", key, pk, scope=Scope.PUBLIC.value)
        return key

    def user_full(self, o: dict) -> None:
        """users.UserFull -> ProfileSnapshot, personal channel, business bot, peer-settings provenance."""
        g = self.g
        for u in o.get("users", []):
            self.user(u)
        fu = o["full_user"]
        key = K.user(fu["id"])
        observed = ts(o.get("_observed_at")) or 0
        snap = f"profile:{fu['id']}:{observed}"
        bday = fu.get("birthday")
        g.node(snap, "ProfileSnapshot", about=fu.get("about"), observedAt=observed,
               birthday=f"{bday.get('year') or '????'}-{bday['month']:02d}-{bday['day']:02d}" if bday else None,
               commonChatsCount=fu.get("common_chats_count"), observedBy=self.collector_key)
        g.edge("HAS_PROFILE", key, snap, t=observed)
        g.field(key, "about", fu.get("about"), Visibility.VISIBLE if fu.get("about") else Visibility.NOT_SET,
                source=f"{SRC}:userFull.about")
        g.field(key, "birthday", None, Visibility.VISIBLE if bday else Visibility.HIDDEN_BY_PRIVACY,
                source=f"{SRC}:userFull.birthday")
        if fu.get("fallback_photo"):
            # positive evidence of a privacy rule, not missing data
            g.field(key, "profilePhoto", None, Visibility.HIDDEN_BY_PRIVACY, source=f"{SRC}:userFull.fallback_photo")
        if fu.get("personal_channel_id"):
            g.edge("FEATURES", key, K.channel(fu["personal_channel_id"]), scope=Scope.PUBLIC.value, t=observed)
        if fu.get("business_bot_id") or (fu.get("business_connected_bot") or {}).get("bot_id"):
            bot = fu.get("business_bot_id") or fu["business_connected_bot"]["bot_id"]
            g.edge("DELEGATES_TO", key, K.user(bot), t=observed)
        st = fu.get("settings") or {}
        for f, prop in (("registration_month", "registrationMonth"), ("phone_country", "phoneCountry"),
                        ("name_change_date", "nameChangeDate"), ("photo_change_date", "photoChangeDate")):
            if st.get(f) is not None:
                g.field(key, prop, st[f], Visibility.VISIBLE, source=f"{SRC}:peerSettings.{f}",
                        observed_by=self.collector_key)
        if self.collector_key and (fu.get("note") or fu.get("blocked") is not None):
            ann = f"contact-annotation:{self.collector_key}:{fu['id']}"
            g.node(ann, "ContactAnnotation", note=(fu.get("note") or {}).get("text") if isinstance(fu.get("note"), dict)
                   else fu.get("note"), blocked=fu.get("blocked"), attributedTo=self.collector_key)
            g.edge("ANNOTATES", ann, key, scope=Scope.COLLECTOR_ONLY.value, observed_by=self.collector_key)

    def contacts(self, o: dict) -> None:
        """contacts.Contacts -> collector-scoped KNOWS edges."""
        for u in o.get("users", []):
            self.user(u)
        for c in o.get("contacts", []):
            self.g.edge("KNOWS", self.collector_key, K.user(c["user_id"]), scope=Scope.COLLECTOR_ONLY.value,
                        observed_by=self.collector_key, mutual=bool(c.get("mutual")))

    def gifts(self, o: dict) -> None:
        """SavedStarGifts -> dated GAVE_GIFT_TO edges; hidden senders become a typed gap."""
        g = self.g
        recipient = K.peer_from_mtproto((o.get("_tgo") or {}).get("peer"))
        hidden = 0
        for sg in o.get("gifts", []):
            sender = K.peer_from_mtproto(sg.get("from_id"))
            if not sender or sg.get("name_hidden"):
                hidden += 1
                continue
            gift = sg.get("gift") or {}
            g.edge("GAVE_GIFT_TO", sender, recipient, scope=Scope.PUBLIC.value, t=ts(sg.get("date")),
                   discriminator=f"{gift.get('id')}:{ts(sg.get('date'))}", giftId=gift.get("id"),
                   stars=gift.get("stars"), message=(sg.get("message") or {}).get("text"))
        if recipient and hidden:
            # absence as data: gifts exist but their senders are hidden
            g.field(recipient, "hiddenGiftSenders", hidden, Visibility.HIDDEN_BY_PRIVACY,
                    source=f"{SRC}:savedStarGift.name_hidden")
            g.nodes[recipient].props["hiddenGiftSenders"] = hidden

    # --------------------------------------------------------------- groups
    def channel(self, c: dict) -> str:
        """Channel -> Channel/Supergroup/Forum/Gigagroup/Monoforum (+ usernames, title history, community link)."""
        g, key = self.g, K.channel(c["id"])
        if c.get("broadcast"):
            cls = "Monoforum" if c.get("monoforum") else "Channel"
            self.broadcast.add(key)
        elif c.get("gigagroup"):
            cls = "Gigagroup"
        elif c.get("forum"):
            cls = "Forum"
        else:
            cls = "Supergroup"
        g.node(key, cls, chatId=c["id"], title=c.get("title"), label=c.get("title"),
               groupKind={"Channel": "broadcast_channel", "Monoforum": "monoforum", "Gigagroup": "gigagroup",
                          "Forum": "forum", "Supergroup": "supergroup"}[cls],
               username=c.get("username"), isVerified=c.get("verified"), isScam=c.get("scam"),
               isFake=c.get("fake"), participantsCount=c.get("participants_count"),
               createdAt=ts(c.get("date")) if not c.get("creator") and c.get("left", True) else None,
               dateField=ts(c.get("date")), linkedCommunityId=c.get("linked_community_id"),
               topic=(c.get("_tgo") or {}).get("topic"))
        names = {c["username"]: True} if c.get("username") else {}
        for un in c.get("usernames") or []:
            if un.get("username"):
                names.setdefault(un["username"], bool(un.get("active")))
        for name, active in names.items():
            uk = K.username(name)
            g.node(uk, "Username", usernameValue=name)
            g.edge("HAS_USERNAME", key, uk, scope=Scope.PUBLIC.value, active=active)
        for th in (c.get("_tgo") or {}).get("title_history", []):
            prev = g.nodes[key].props.setdefault("titleHistory", [])
            if th not in prev:
                prev.append(th)
        if c.get("linked_community_id"):
            g.edge("MEMBER_OF_COMMUNITY", key, K.community(c["linked_community_id"]), scope=Scope.PUBLIC.value)
        return key

    def basic_chat(self, c: dict) -> str:
        """Chat -> BasicGroup."""
        key = K.chat(c["id"])
        self.g.node(key, "BasicGroup", chatId=c["id"], title=c.get("title"), label=c.get("title"),
                    groupKind="basic_group", participantsCount=c.get("participants_count"),
                    createdAt=ts(c.get("date")))
        return key

    def community(self, c: dict) -> str:
        """Community (layer 230) -> Community node."""
        key = K.community(c["id"])
        self.g.node(key, "Community", communityId=c["id"], title=c.get("title"), label=c.get("title"),
                    groupKind="community", createdAt=ts(c.get("date")), hidden=c.get("collapsed_in_dialogs"))
        if c.get("creator") and self.collector_key:
            self.g.edge("CREATED", self.collector_key, key)
        return key

    def community_full(self, o: dict) -> None:
        """CommunityFull -> MEMBER_OF_COMMUNITY links and admin-visible joiner/creator roles."""
        fc = o.get("full_community") or o["full_chat"]
        ck = K.community(fc["id"])
        self.g.node(ck, "Community", about=fc.get("about"))
        for lp in fc.get("linked_peers", []):
            pk = K.peer_from_mtproto(lp.get("peer"))
            if pk:
                self.g.edge("MEMBER_OF_COMMUNITY", pk, ck, t=ts(lp.get("date")), scope=Scope.MEMBERS.value,
                            visible=lp.get("visible"), canViewHistory=lp.get("can_view_history"),
                            requestedBy=K.user(lp["requested_by"]) if lp.get("requested_by") else None)
        # admins see per-participant joined vs created chats (joiner/creator role split)
        for part in fc.get("_tgo_participant_chats", []):
            uk = K.user(part["user_id"])
            for cid in part.get("creator_chat_ids", []):
                self.g.edge("CREATED", uk, K.channel(cid), scope=Scope.ADMINS.value)
            for cid in part.get("joined_chat_ids", []):
                self.g.edge("MEMBER_OF", uk, K.channel(cid), scope=Scope.ADMINS.value, role="member",
                            via=ck)

    def chat_full(self, o: dict) -> None:
        """messages.ChatFull -> chat details, linked discussion, basic-group participants."""
        for c in o.get("chats", []):
            (self.channel if c.get("_") == "Channel" else self.basic_chat)(c)
        for u in o.get("users", []):
            self.user(u)
        fc = o["full_chat"]
        if fc.get("_") == "ChannelFull":
            key = K.channel(fc["id"])
            self.g.node(key, about=fc.get("about"), participantsCount=fc.get("participants_count"),
                        adminsCount=fc.get("admins_count"), participantsHidden=fc.get("participants_hidden"))
            if fc.get("linked_chat_id"):
                self.g.edge("LINKED_DISCUSSION", key, K.channel(fc["linked_chat_id"]), scope=Scope.PUBLIC.value)
        else:
            key = K.chat(fc["id"])
            self.g.node(key, about=fc.get("about"))
            parts = (fc.get("participants") or {}).get("participants", [])
            for p in parts:
                self._participant(key, p, Scope.MEMBERS.value)

    def participants(self, o: dict) -> None:
        """channels.ChannelParticipants -> MEMBER_OF/ADMIN_OF/INVITED_BY with visibility scope."""
        for u in o.get("users", []):
            self.user(u)
        meta = o.get("_tgo") or {}
        key = K.channel(meta["channel_id"])
        scope = meta.get("scope") or (Scope.ADMINS.value if key in self.broadcast else Scope.MEMBERS.value)
        for p in o.get("participants", []):
            self._participant(key, p, scope)

    def _participant(self, chat_key: str, p: dict, scope: str) -> None:
        g = self.g
        t = p.get("_", "")
        uid = p.get("user_id") or (p.get("peer") or {}).get("user_id")
        if uid is None:
            return
        uk = K.user(uid)
        role = ("creator" if "Creator" in t else "admin" if "Admin" in t else "banned" if "Banned" in t
                else "left" if "Left" in t else "subscriber" if chat_key in self.broadcast else "member")
        g.edge("MEMBER_OF", uk, chat_key, scope=scope, t=ts(p.get("date")), role=role,
               since=ts(p.get("date")), rank=p.get("rank"))
        if role in ("creator", "admin"):
            g.edge("ADMIN_OF", uk, chat_key, scope=scope, t=ts(p.get("date")), role=role,
                   promotedBy=K.user(p["promoted_by"]) if p.get("promoted_by") else None)
        if p.get("inviter_id"):
            # admin/owner-only signal: reveals audience members who never posted
            g.edge("INVITED_BY", uk, K.user(p["inviter_id"]), scope=Scope.ADMINS.value, t=ts(p.get("date")),
                   chat=chat_key)

    def sticker_set(self, o: dict) -> None:
        """messages.StickerSet -> StickerSet, documents and *observed* ownership only."""
        s = o["set"]
        sk = K.sticker_set(s.get("id"), s.get("short_name"))
        self.g.node(sk, "StickerSet", title=s.get("title"), shortName=s.get("short_name"), count=s.get("count"),
                    label=s.get("title"))
        # Ownership only from observed facts: creator flag or _by_<bot> suffix.
        if s.get("creator") and self.collector_key:
            self.g.edge("OWNED_BY", sk, self.collector_key, method="stickerSet.creator")
        sn = s.get("short_name") or ""
        if "_by_" in sn:
            bot = sn.rsplit("_by_", 1)[1]
            self.g.node(K.username(bot), "Username", usernameValue=bot)
            self.g.edge("OWNED_BY", sk, K.username(bot), method="short_name _by_<bot> suffix")
        for d in o.get("documents", []):
            dk = K.document(d["id"])
            cls = M.classify_document(d.get("mime_type"), d.get("attributes", []))
            self.g.node(dk, cls, documentId=d["id"], **M.document_props(d.get("mime_type"), d.get("attributes", [])))
            self.g.edge("IN_STICKER_SET", dk, sk)

    def story(self, s: dict) -> None:
        """StoryItem -> Story, reposts and media areas (geo/venue, channel post, URL) with W3C selectors."""
        g = self.g
        owner = K.peer_from_mtproto((s.get("_tgo") or {}).get("peer"))
        sk = K.story(owner, s["id"])
        t = ts(s.get("date"))
        g.node(sk, "Story", storyId=s["id"], date=t, expireDate=ts(s.get("expire_date")),
               caption=s.get("caption"), views=(s.get("views") or {}).get("views_count"), label=f"story {s['id']}")
        g.edge("CREATED", owner, sk, t=t, scope=Scope.PUBLIC.value)
        fwd = s.get("fwd_from") or {}
        # Telethon renames the TL field `from` to `from_` (Python keyword)
        src = K.peer_from_mtproto(fwd.get("from_") or fwd.get("from"))
        if src:
            g.edge("REPOSTED", owner, src, t=t, scope=Scope.PUBLIC.value, evidence=[sk],
                   originalStory=K.story(src, fwd["story_id"]) if fwd.get("story_id") else None)
        for i, area in enumerate(s.get("media_areas", [])):
            c = area.get("coordinates") or {}
            sel = M.media_area_selector(c.get("x", 0), c.get("y", 0), c.get("w", 0), c.get("h", 0), c.get("rotation", 0))
            at = area.get("_")
            if at in ("MediaAreaGeoPoint", "MediaAreaVenue"):
                geo = area.get("geo") or {}
                gk = K.geo(geo["lat"], geo["long"])
                g.node(gk, "GeoPoint", lat=geo["lat"], lon=geo["long"],
                       address=json.dumps(area.get("address")) if area.get("address") else area.get("title"))
                g.edge("MENTIONS", sk, gk, t=t, selector=sel["FragmentSelector"], svgSelector=sel.get("SvgSelector"),
                       method="mediaArea", discriminator=str(i))
            elif at == "MediaAreaChannelPost":
                ck = K.channel(area["channel_id"])
                g.edge("MENTIONS", sk, ck, t=t, selector=sel["FragmentSelector"], method="mediaArea",
                       evidence=[K.message(ck, area["msg_id"])], discriminator=str(i))
            elif at == "MediaAreaUrl":
                from ..identifiers import canonical_url

                u = canonical_url(area["url"])
                ik = K.identifier("url", u)
                g.node(ik, "Url", value=u, kind="url")
                g.edge("MENTIONS", sk, ik, t=t, selector=sel["FragmentSelector"], method="mediaArea",
                       discriminator=str(i))

    # ------------------------------------------------------------- messages
    def message(self, m: dict) -> None:
        """Message/MessageService -> message node, sender, forwards, replies, topics, mentions, reactions, media, actions."""
        g = self.g
        peer = K.peer_from_mtproto(m.get("peer_id"))
        if not peer:
            return
        mk = K.message(peer, m["id"])
        t = ts(m.get("date"))
        sender = K.peer_from_mtproto(m.get("from_id")) or (peer if peer.startswith("channel:") and m.get("post") else None)
        is_service = m.get("_") == "MessageService"
        g.node(mk, "ServiceMessage" if is_service else "Message", msgId=m["id"], date=t,
               text=m.get("message") or None, views=m.get("views"), forwards=m.get("forwards"),
               postAuthor=m.get("post_author"), groupedId=m.get("grouped_id"), editDate=ts(m.get("edit_date")),
               label=(m.get("message") or "")[:60] or f"msg {m['id']}")
        g.edge("IN_CHAT", mk, peer, t=t)
        if sender:
            g.edge("SENT", sender, mk, t=t, scope=Scope.MEMBERS.value)
        fwd = m.get("fwd_from") or {}
        if fwd:
            src = K.peer_from_mtproto(fwd.get("from_id"))
            if src and fwd.get("channel_post"):
                orig = K.message(src, fwd["channel_post"])
                # a forward header is second-hand: fill a stub, never overwrite the original's own fields
                stub = g.node(orig, "Message", msgId=fwd["channel_post"])
                for k, v in (("date", ts(fwd.get("date"))), ("postAuthor", fwd.get("post_author"))):
                    if v is not None and k not in stub.props:
                        stub.props[k] = v
                g.edge("IN_CHAT", orig, src, t=ts(fwd.get("date")))
                g.edge("FORWARD_OF", mk, orig, t=t, originalDate=ts(fwd.get("date")))
            elif src:
                g.edge("FORWARD_OF", mk, src, t=t, originalDate=ts(fwd.get("date")))
            elif fwd.get("from_name"):
                # MessageOriginHiddenUser: positive evidence of a Forwards privacy rule
                g.node(mk, forwardedFromHiddenName=fwd["from_name"])
                g.field(mk, "forwardOrigin", None, Visibility.HIDDEN_BY_PRIVACY, source=f"{SRC}:fwd_from.from_name")
        rt = m.get("reply_to") or {}
        if rt.get("reply_to_msg_id"):
            rpeer = K.peer_from_mtproto(rt.get("reply_to_peer_id")) or peer
            g.edge("REPLIES_TO", mk, K.message(rpeer, rt["reply_to_msg_id"]), t=t)
        if rt.get("forum_topic"):
            tid = rt.get("reply_to_top_id") or rt.get("reply_to_msg_id")
            tk = K.topic(peer, tid)
            g.node(tk, "ForumTopic", topicId=tid)
            g.edge("IN_TOPIC", mk, tk, t=t)
        for ent in m.get("entities") or []:
            if ent.get("_") == "MessageEntityMentionName":
                g.edge("MENTIONS", mk, K.user(ent["user_id"]), t=t)
            elif ent.get("_") == "MessageEntityCustomEmoji":
                g.edge("MENTIONS", mk, K.document(ent["document_id"]), t=t, method="custom_emoji")
            elif ent.get("_") == "MessageEntityTextUrl":
                g.node(mk, hiddenUrls=list({*g.nodes[mk].props.get("hiddenUrls", []), ent["url"]}))
        reactions = m.get("reactions") or {}
        for rr in reactions.get("recent_reactions") or []:
            who = K.peer_from_mtproto(rr.get("peer_id"))
            emo = (rr.get("reaction") or {}).get("emoticon")
            if who:
                g.edge("REACTED", who, mk, t=ts(rr.get("date")), reaction=emo, discriminator=emo)
        if reactions.get("results"):
            g.node(mk, reactionCounts={(r.get("reaction") or {}).get("emoticon", "?"): r.get("count")
                                       for r in reactions["results"]})
        if m.get("media"):
            self._media(mk, m["media"], m.get("_tgo") or {}, t, m.get("grouped_id"))
        if is_service:
            self._action(mk, peer, sender, m.get("action") or {}, t)

    def _media(self, mk: str, med: dict, ext: dict, t: int | None, grouped_id) -> None:
        g = self.g
        mt = med.get("_")
        occ = M.ttl_props(med.get("ttl_seconds"), med.get("spoiler"))
        if grouped_id:
            occ["groupedId"] = grouped_id
        if mt == "MessageMediaPhoto" and med.get("photo"):
            ph = med["photo"]
            pk = attach_file(g, self.root, ext.get("media_path"), "Photo", M.PHOTO_MODE,
                             fallback_key=K.photo(ph["id"]), props={"photoId": ph["id"], "uploadDate": ts(ph.get("date")),
                                                                    "label": f"photo {ph['id']}"})
            for s in ph.get("sizes", []):
                rk = K.rendition(ph["id"], s.get("type"))
                g.node(rk, "PhotoRendition", sizeType=s.get("type"), width=s.get("w"), height=s.get("h"),
                       contentSize=s.get("size") or (max(s["sizes"]) if s.get("sizes") else None),
                       inline=s.get("type") in M.INLINE_SIZE_TYPES, sizeMeaning=M.PHOTO_SIZE_TYPES.get(s.get("type")))
                g.edge("HAS_RENDITION", pk, rk, sizeType=s.get("type"))
            if ph.get("video_sizes"):
                g.node(pk, "Photo", animated=True)
            attachment(g, mk, pk, 0, t, **occ)
        elif mt == "MessageMediaDocument" and med.get("document"):
            d = med["document"]
            cls = M.classify_document(d.get("mime_type"), d.get("attributes", []))
            props = M.document_props(d.get("mime_type"), d.get("attributes", []))
            props.update(documentId=d["id"], size=d.get("size"), fileUniqueId=ext.get("file_unique_id"),
                         label=props.get("fileName") or f"{cls} {d['id']}")
            fk = attach_file(g, self.root, ext.get("media_path"), cls, M.DOCUMENT_MODE,
                             fallback_key=K.document(d["id"]), props=props)
            for alt in d.get("alt_documents") or []:
                ak = K.document(alt["id"])
                g.node(ak, "Video", documentId=alt["id"], mime=alt.get("mime_type"), variantOf=fk)
            if med.get("video_timestamp"):
                occ["videoTimestamp"] = med["video_timestamp"]
            attachment(g, mk, fk, 0, t, **occ)
        elif mt == "MessageMediaWebPage" and (med.get("webpage") or {}).get("url"):
            from ..identifiers import canonical_url

            u = canonical_url(med["webpage"]["url"])
            ik = K.identifier("url", u)
            g.node(ik, "Url", value=u, kind="url", siteName=med["webpage"].get("site_name"),
                   title=med["webpage"].get("title"))
            g.edge("CONTAINS_IDENTIFIER", mk, ik, status="observed", method="messageMediaWebPage", t=t)
        elif mt in ("MessageMediaGeo", "MessageMediaVenue", "MessageMediaGeoLive") and med.get("geo"):
            geo = med["geo"]
            gk = K.geo(geo["lat"], geo["long"])
            g.node(gk, "GeoPoint", lat=geo["lat"], lon=geo["long"], title=med.get("title"), live=mt.endswith("Live"))
            g.edge("MENTIONS", mk, gk, t=t, method=mt)
        elif mt == "MessageMediaContact":
            if med.get("phone_number"):
                ik = K.identifier("phone", "+" + "".join(ch for ch in med["phone_number"] if ch.isdigit()))
                g.node(ik, "PhoneNumber", value=ik.split(":", 2)[2], kind="phone")
                g.edge("CONTAINS_IDENTIFIER", mk, ik, status="observed", method="messageMediaContact", t=t)
            if med.get("user_id"):
                g.edge("MENTIONS", mk, K.user(med["user_id"]), t=t, method="messageMediaContact")

    def _action(self, mk: str, peer: str, actor: str | None, a: dict, t: int | None) -> None:
        g = self.g
        at = a.get("_", "")
        g.node(mk, action=at)
        if at == "MessageActionChatAddUser":
            for uid in a.get("users", []):
                uk = K.user(uid)
                g.edge("MEMBER_OF", uk, peer, t=t, role="member", since=t, scope=Scope.MEMBERS.value)
                if actor and actor != uk:
                    g.edge("INVITED_BY", uk, actor, t=t, chat=peer, scope=Scope.MEMBERS.value, evidence=[mk])
        elif at == "MessageActionChatJoinedByLink" and actor:
            g.edge("MEMBER_OF", actor, peer, t=t, role="member", since=t, scope=Scope.MEMBERS.value)
            if a.get("inviter_id"):
                g.edge("INVITED_BY", actor, K.user(a["inviter_id"]), t=t, chat=peer, scope=Scope.MEMBERS.value,
                       evidence=[mk])
        elif at == "MessageActionChatDeleteUser":
            g.edge("MEMBER_OF", K.user(a["user_id"]), peer, t=t, role="left", until=t, scope=Scope.MEMBERS.value)
        elif at in ("MessageActionChannelCreate", "MessageActionChatCreate") and actor:
            g.edge("CREATED", actor, peer, t=t, evidence=[mk])
            g.node(peer, createdAt=t)
        elif at == "MessageActionChatEditTitle":
            hist = g.nodes[peer].props.setdefault("titleHistory", [])
            if a.get("title") not in hist:
                hist.append(a.get("title"))


def ingest(g: Graph, root: str | Path) -> str:
    """Ingest one collection directory into ``g``; returns the CollectionRun key."""
    return MTProtoIngester(g, Path(root)).run()
