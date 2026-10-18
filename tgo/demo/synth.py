"""Synthetic, fully fictional demo dataset exercising every TGO path.

Nothing here describes real people, channels or accounts.  IDs, names, places
("Veloria"), wallets and IBANs are invented or well-known checksum test
vectors.  The data is shaped exactly like the two real sources the pipeline
reads:

    <dir>/mtproto/   collector.json, entities.jsonl, messages.jsonl, media/   (Telethon to_dict shape)
    <dir>/export/    result.json + files/, photos/, voice_messages/          (Telegram Desktop export)
    <dir>/analyst/   judgements.json                                         (analyst decisions)

Scenarios planted (and what the pipeline should find):
  1. "Veloria" regional channel franchise: 8 channels created the same day, posting
     identical / near-identical text within seconds of an originator, co-forwarding it,
     re-uploading its images and promoting the same domain   -> coordination cluster.
  2. An organic channel that forwards the originator once     -> must NOT be clustered.
  3. Two accounts sharing camera body serial (EXIF, document mode), personal channel,
     bio text and a re-compressed avatar                       -> candidate, analyst: positive.
  4. A third account sharing only a wallet address             -> candidate, analyst: negative.
  5. Four throwaway accounts with the same stock avatar        -> IDF-downweighted, no candidate.
  6. A voice note whose transcript names a place, a wallet and a phone number.
  7. A PDF re-saved with the same xmpMM:DocumentID              -> document lineage.
  8. Community (layer 230) linking a group, a channel and a bot; gifts (one hidden sender);
     story repost with geo media area; collectible username moved between channels;
     sticker set owned by a bot; Art. 9 special-category chat; privacy-hidden fields;
     a file excluded from the export.
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import media as M
from ..identifiers import b58check_encode

UTC = timezone.utc
COLLECTOR = 900000001
MARK, MD2, OLA, BOT, DELETED, SILENT = 705502011, 705502099, 705502050, 705502077, 705502033, 705502060
THROWAWAYS = [710000001, 710000002, 710000003, 710000004]
HARBOR_GROUP, HARBOR_CHANNEL, FAITH = 1600300400, 1600300500, 1600300600
COMMUNITY = 4200000001
CENTRAL = 1700000001
REGIONS = ["Nordvale", "Eastmarch", "Southbay", "Westhold", "Lakemere", "Highmoor", "Rivermouth", "Pinecrest"]
REGIONAL = [1700000101 + i for i in range(len(REGIONS))]
GARDEN = 1700000900

TRON_WALLET = b58check_encode(bytes([0x41]) + hashlib.sha256(b"tgo-demo-wallet").digest()[:20])
ETH_WALLET = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"   # EIP-55 spec test vector
IBAN = "GB82 WEST 1234 5698 7654 32"                        # standard IBAN test vector
PHONE_MARK = "+47 912 34 567"


def PU(i):
    return {"_": "PeerUser", "user_id": i}


def PC(i):
    return {"_": "PeerChannel", "channel_id": i}


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


# ------------------------------------------------------------------ media
def _img_pattern(seed: int, size=(256, 256), text: str | None = None):
    from PIL import Image, ImageDraw

    rnd = random.Random(seed)
    img = Image.new("RGB", size, tuple(rnd.randrange(40, 200) for _ in range(3)))
    d = ImageDraw.Draw(img)
    w, h = size
    for _ in range(14):
        x0, y0 = rnd.randrange(w), rnd.randrange(h)
        x1, y1 = x0 + rnd.randrange(20, w // 2), y0 + rnd.randrange(20, h // 2)
        col = tuple(rnd.randrange(256) for _ in range(3))
        (d.ellipse if rnd.random() < 0.5 else d.rectangle)([x0, y0, x1, y1], fill=col)
    if text:
        d.rectangle([0, h - 40, w, h], fill=(20, 20, 20))
        d.text((10, h - 32), text, fill=(240, 240, 240))
    return img


def _save_jpeg(img, path: Path, quality=90, exif=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    kw = {"quality": quality}
    if exif is not None:
        kw["exif"] = exif
    img.save(path, "JPEG", **kw)


def _camera_jpeg(path: Path, seed: int, serial: str, gps: tuple | None, when: str):
    from PIL import Image

    img = _img_pattern(seed, (960, 720))
    exif = Image.Exif()
    exif[0x010F] = "Canon"
    exif[0x0110] = "Canon EOS R6"
    exif[0x0131] = "Firmware Version 1.8.1"
    sub = exif.get_ifd(0x8769)
    sub[0xA431] = serial
    sub[0x9003] = when
    if gps:
        g = exif.get_ifd(0x8825)
        (lat, lon) = gps

        def dms(v):
            d = int(v)
            m = int((v - d) * 60)
            s = round(((v - d) * 60 - m) * 60, 2)
            return (float(d), float(m), float(s))
        g[1], g[2], g[3], g[4] = "N", dms(lat), "E", dms(lon)
    _save_jpeg(img, path, 92, exif)


def _pdf(path: Path, lines: list[str], author: str, producer: str, xmp_id: str, title: str):
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content = "BT /F1 11 Tf 56 780 Td 16 TL\n" + "".join(f"({esc(l)}) Tj T*\n" for l in lines) + "ET"
    xmp = (f'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
           f'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
           f'xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" xmpMM:DocumentID="{xmp_id}"/></rdf:RDF>'
           f'</x:xmpmeta><?xpacket end="w"?>')
    objs = [
        "<< /Type /Catalog /Pages 2 0 R /Metadata 6 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Type /Metadata /Subtype /XML /Length {len(xmp)} >>\nstream\n{xmp}\nendstream",
        f"<< /Author ({esc(author)}) /Producer ({esc(producer)}) /Title ({esc(title)}) /CreationDate (D:20261001091500Z) >>",
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 7 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


def _media(root: Path) -> dict:
    """Create all binary media under ``root/mtproto/media`` and return relative paths."""
    m = root / "mtproto" / "media"
    p = {}
    # avatars: MARK and MD2 near-duplicate (resize + recompress); throwaways share a stock image
    av = _img_pattern(11, (320, 320))
    _save_jpeg(av, m / "avatars" / "mark.jpg", 90)
    _save_jpeg(av.resize((300, 300)).point(lambda v: min(255, int(v * 1.03))), m / "avatars" / "md2.jpg", 55)
    _save_jpeg(_img_pattern(12, (320, 320)), m / "avatars" / "ola.jpg", 88)
    stock = _img_pattern(99, (320, 320))
    for i, uid in enumerate(THROWAWAYS):
        _save_jpeg(stock, m / "avatars" / f"stock_{uid}.jpg", 80 + i)
    # photo-as-file JPEGs (document mode): same camera body serial
    _camera_jpeg(m / "IMG_8841.JPG", 21, "SN-48213377", (69.7271, 30.0450), "2026:10:01 09:12:44")
    _camera_jpeg(m / "IMG_2207.JPG", 22, "SN-48213377", None, "2026:10:03 17:40:02")
    # photo-mode screenshot with OCR sidecar (wallet)
    _save_jpeg(_img_pattern(31, (720, 1280), text="USDT TRC20 payout"), m / "screenshot_ola.jpg", 85)
    (m / "screenshot_ola.jpg.ocr.txt").write_text(
        f"Payout confirmed\nNetwork: TRC20\nTo: {TRON_WALLET}\nAmount: 4,800 USDT\n", encoding="utf-8")
    # voice note (opaque bytes) + ASR sidecar standing in for faster-whisper
    rnd = random.Random(42)
    (m / "voice_0042.ogg").write_bytes(b"OggS\x00\x02" + bytes(rnd.randrange(256) for _ in range(6000)))
    (m / "voice_0042.ogg.transcript.json").write_text(json.dumps({
        "language": "en", "language_probability": 0.93,
        "segments": [
            {"start": 0.0, "end": 5.4, "text": "Okay, the shipment leaves Kirkenes on Friday morning."},
            {"start": 5.4, "end": 12.3, "text": "Do not use the old account, the bank flagged it."},
            {"start": 12.3, "end": 17.92, "text": f"Payment goes to the TRON wallet {TRON_WALLET}."},
            {"start": 17.92, "end": 26.0, "text": f"If anything changes call me on {PHONE_MARK}."},
            {"start": 26.0, "end": 42.0, "text": "Second truck goes via Riga next week."},
        ]}, indent=1), encoding="utf-8")
    # PDFs: same xmpMM:DocumentID, different bytes (re-saved)
    lines = ["INVOICE 2026-117", "Harbor freight, Kirkenes - Riga", "Amount due: EUR 18,400",
             f"Pay to IBAN {IBAN}", f"or ETH {ETH_WALLET}", "Reference: HFK-117"]
    _pdf(m / "invoice_2026.pdf", lines, "M. Dunmore", "LibreOffice 24.2", "uuid:7f3c2a10-demo-4b1e-9e55-117", "Invoice 117")
    _pdf(m / "invoice_2026_v2.pdf", lines + ["Revised: payment terms 14 days"], "M. Dunmore",
         "Microsoft Word 2021", "uuid:7f3c2a10-demo-4b1e-9e55-117", "Invoice 117 rev")
    # sticker
    from PIL import Image

    st = _img_pattern(51, (512, 512)).convert("RGBA")
    st.save(m / "sticker_anchor.webp", "WEBP")
    # campaign images: originator + re-uploads by regional channels (recompressed)
    camp = _img_pattern(61, (800, 600), text="VELORIA: THEY ARE LYING TO YOU")
    _save_jpeg(camp, m / "campaign" / "central.jpg", 90)
    for i, cid in enumerate(REGIONAL):
        _save_jpeg(camp.resize((780, 585)), m / "campaign" / f"reupload_{cid}.jpg", 60 + 4 * i)
    for k, v in {"mark_avatar": "media/avatars/mark.jpg", "md2_avatar": "media/avatars/md2.jpg",
                 "ola_avatar": "media/avatars/ola.jpg", "img8841": "media/IMG_8841.JPG",
                 "img2207": "media/IMG_2207.JPG", "screenshot": "media/screenshot_ola.jpg",
                 "voice": "media/voice_0042.ogg", "invoice": "media/invoice_2026.pdf",
                 "invoice2": "media/invoice_2026_v2.pdf", "sticker": "media/sticker_anchor.webp",
                 "camp_central": "media/campaign/central.jpg"}.items():
        p[k] = v
    _ = Image
    return p


# --------------------------------------------------------------- MTProto
def _user(uid, first, last=None, username=None, **kw):
    d = {"_": "User", "id": uid, "first_name": first, "last_name": last, "username": username,
         "bot": False, "deleted": False, "premium": False, "verified": False, "scam": False, "fake": False,
         "min": False, "status": {"_": "UserStatusRecently"}}
    d.update(kw)
    return d


def _channel(cid, title, username=None, broadcast=True, date=None, **kw):
    d = {"_": "Channel", "id": cid, "title": title, "username": username, "broadcast": broadcast,
         "megagroup": not broadcast, "date": date, "left": True, "creator": False, "verified": False}
    d.update(kw)
    return d


def _mtproto(root: Path, media: dict) -> None:
    out = root / "mtproto"
    out.mkdir(parents=True, exist_ok=True)
    t_obs = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    (out / "collector.json").write_text(json.dumps({
        "collector": _user(COLLECTOR, "Field", "Analyst", "tgo_collector_demo", is_self=True),
        "run": {"id": "demo-2026-10-15", "client": "telethon", "clientVersion": "1.40 (synthetic)", "layer": 230,
                "started": int(t_obs.timestamp())}}, indent=1), encoding="utf-8")

    E = []
    obs = int(t_obs.timestamp())
    E.append(_user(MARK, "Mark", "Dunmore", "dunmore_m", contact=True, mutual_contact=True, premium=True,
                   phone="4791234567", photo={"_": "UserProfilePhoto", "photo_id": 5001},
                   _tgo={"avatar_path": media["mark_avatar"]},
                   usernames=[{"_": "Username", "username": "dunmore_m", "editable": True, "active": True}]))
    E.append(_user(MD2, "M.", "D.", "harbor_md2", status={"_": "UserStatusOffline",
                   "was_online": iso(datetime(2026, 10, 14, 22, 5, tzinfo=UTC))},
                   photo={"_": "UserProfilePhoto", "photo_id": 5002}, _tgo={"avatar_path": media["md2_avatar"]}))
    E.append(_user(OLA, "Ola", "Fisk", "olafisk", photo={"_": "UserProfilePhoto", "photo_id": 5003},
                   _tgo={"avatar_path": media["ola_avatar"]}, close_friend=True))
    E.append(_user(BOT, "Harbor Helper", None, "harbor_helper_bot", bot=True, bot_active_users=212))
    E.append(_user(DELETED, None, None, None, deleted=True, status=None))
    E.append(_user(SILENT, "Silent", "Reader", None))
    for i, uid in enumerate(THROWAWAYS):
        E.append(_user(uid, ["Anna", "Viktor", "Elena", "Pavel"][i], None, None,
                       photo={"_": "UserProfilePhoto", "photo_id": 5100 + i},
                       _tgo={"avatar_path": f"media/avatars/stock_{uid}.jpg"}))
    E.append({"_": "UserFull", "_observed_at": obs, "users": [], "full_user": {
        "_": "UserFull", "id": MARK, "about": "Harbor logistics & freight forwarding. DM for rates.",
        "common_chats_count": 3, "personal_channel_id": HARBOR_CHANNEL, "business_bot_id": BOT,
        "settings": {"_": "PeerSettings", "registration_month": "2019-03", "phone_country": "NO",
                     "name_change_date": None, "photo_change_date": int(datetime(2026, 9, 2, tzinfo=UTC).timestamp())}}})
    E.append({"_": "UserFull", "_observed_at": obs, "users": [], "full_user": {
        "_": "UserFull", "id": MD2, "about": "Harbor logistics & freight forwarding. DM for rates.",
        "common_chats_count": 1, "personal_channel_id": HARBOR_CHANNEL, "fallback_photo": {"_": "Photo", "id": 1},
        "settings": {"_": "PeerSettings", "registration_month": "2026-04", "phone_country": "NO",
                     "name_change_date": int(datetime(2026, 9, 30, tzinfo=UTC).timestamp())}}})
    E.append({"_": "UserFull", "_observed_at": obs, "users": [], "full_user": {
        "_": "UserFull", "id": OLA, "about": "Fisheries & cold storage, Bergen.", "common_chats_count": 2,
        "birthday": {"_": "Birthday", "day": 3, "month": 7}, "note": {"_": "TextWithEntities",
                                                                       "text": "met at the Bergen fish expo"}}})
    # channels / groups / community
    created = datetime(2026, 5, 14, 3, 12, tzinfo=UTC)
    E.append(_channel(CENTRAL, "Veloria Central Wire", "velwire", date=iso(datetime(2026, 3, 1, 9, tzinfo=UTC)),
                      participants_count=48210,
                      usernames=[{"_": "Username", "username": "velwire", "active": True},
                                 {"_": "Username", "username": "veloria_news", "active": False}],
                      _tgo={"title_history": ["Veloria Truth", "Veloria Central Wire"]}))
    for i, (cid, region) in enumerate(zip(REGIONAL, REGIONS)):
        E.append(_channel(cid, f"{region} News 24", f"{region.lower()}_news24",
                          date=iso(created + timedelta(minutes=3 * i, seconds=7 * i)), participants_count=900 + 137 * i))
    E[-len(REGIONS)]["usernames"] = [{"_": "Username", "username": "nordvale_news24", "active": True},
                                     {"_": "Username", "username": "veloria_news", "active": False}]
    E.append(_channel(GARDEN, "Lakemere Gardening Club", "lakemere_garden",
                      date=iso(datetime(2025, 2, 11, tzinfo=UTC)), participants_count=3100))
    E.append(_channel(HARBOR_GROUP, "Harbor Logistics Chat", None, broadcast=False, megagroup=True,
                      date=iso(datetime(2026, 9, 1, 10, tzinfo=UTC)), linked_community_id=COMMUNITY, left=False))
    E.append(_channel(HARBOR_CHANNEL, "Harbor Announcements", "harbor_announce",
                      date=iso(datetime(2026, 8, 20, tzinfo=UTC)), linked_community_id=COMMUNITY))
    E.append(_channel(FAITH, "Veloria Faith Circle", None, broadcast=False, megagroup=True,
                      date=iso(datetime(2024, 1, 5, tzinfo=UTC)), _tgo={"topic": "religion"}, left=False))
    E.append({"_": "Community", "id": COMMUNITY, "title": "Harbor Community", "date": iso(datetime(2026, 9, 2, tzinfo=UTC)),
              "creator": False})
    E.append({"_": "communities.CommunityFull", "full_community": {
        "_": "CommunityFull", "id": COMMUNITY, "about": "Freight, rates and the harbor helper bot.",
        "linked_peers": [
            {"_": "CommunityPeer", "peer": PC(HARBOR_GROUP), "visible": True, "can_view_history": True,
             "requested_by": MARK, "date": iso(datetime(2026, 9, 2, 8, tzinfo=UTC))},
            {"_": "CommunityPeer", "peer": PC(HARBOR_CHANNEL), "visible": True, "can_view_history": True,
             "requested_by": MARK, "date": iso(datetime(2026, 9, 2, 8, 1, tzinfo=UTC))},
            {"_": "CommunityPeer", "peer": PU(BOT), "visible": True, "requested_by": MARK,
             "date": iso(datetime(2026, 9, 2, 8, 2, tzinfo=UTC))}]}})
    E.append({"_": "messages.ChatFull", "chats": [], "users": [], "full_chat": {
        "_": "ChannelFull", "id": HARBOR_CHANNEL, "about": "Rates and sailing schedule. Discussion: Harbor Logistics Chat",
        "participants_count": 640, "linked_chat_id": HARBOR_GROUP}})
    E.append({"_": "messages.ChatFull", "chats": [], "users": [], "full_chat": {
        "_": "ChannelFull", "id": CENTRAL, "about": "The news they don't want you to read. veloria-truth.example",
        "participants_count": 48210}})
    E.append({"_": "ChannelParticipants", "_tgo": {"channel_id": HARBOR_GROUP}, "users": [], "participants": [
        {"_": "ChannelParticipantCreator", "user_id": MARK, "rank": "boss"},
        {"_": "ChannelParticipantAdmin", "user_id": MD2, "promoted_by": MARK, "date": iso(datetime(2026, 9, 28, tzinfo=UTC)),
         "inviter_id": MARK},
        {"_": "ChannelParticipantAdmin", "user_id": BOT, "promoted_by": MARK, "date": iso(datetime(2026, 9, 2, tzinfo=UTC))},
        {"_": "ChannelParticipant", "user_id": OLA, "date": iso(datetime(2026, 9, 3, tzinfo=UTC)), "inviter_id": MARK},
        {"_": "ChannelParticipant", "user_id": SILENT, "date": iso(datetime(2026, 9, 9, tzinfo=UTC)), "inviter_id": MARK},
        {"_": "ChannelParticipantSelf", "user_id": COLLECTOR, "date": iso(datetime(2026, 9, 12, tzinfo=UTC)),
         "inviter_id": OLA},
        {"_": "ChannelParticipantLeft", "peer": PU(DELETED)}]})
    E.append({"_": "ChannelParticipants", "_tgo": {"channel_id": FAITH}, "users": [], "participants": [
        {"_": "ChannelParticipant", "user_id": OLA, "date": iso(datetime(2025, 6, 1, tzinfo=UTC))}] + [
        {"_": "ChannelParticipant", "user_id": u, "date": iso(datetime(2026, 5, 15, tzinfo=UTC))} for u in THROWAWAYS]})
    E.append({"_": "messages.StickerSet", "set": {"_": "StickerSet", "id": 880001, "title": "Harbor Pack",
                                                  "short_name": "harbor_pack_by_stickerbot", "count": 1, "creator": False},
              "documents": [{"_": "Document", "id": 990001, "mime_type": "image/webp",
                             "attributes": [{"_": "DocumentAttributeSticker", "alt": "⚓"},
                                            {"_": "DocumentAttributeImageSize", "w": 512, "h": 512}]}]})
    E.append({"_": "StoryItem", "id": 7, "_tgo": {"peer": PU(MARK)}, "date": iso(datetime(2026, 10, 2, 7, tzinfo=UTC)),
              "expire_date": iso(datetime(2026, 10, 3, 7, tzinfo=UTC)), "caption": "Loading day",
              "views": {"_": "StoryViews", "views_count": 311},
              "media_areas": [
                  {"_": "MediaAreaGeoPoint", "coordinates": {"x": 50, "y": 70, "w": 30, "h": 8, "rotation": 12},
                   "geo": {"_": "GeoPoint", "lat": 69.7269, "long": 30.0453},
                   "address": {"country_iso2": "NO", "city": "Kirkenes"}},
                  {"_": "MediaAreaUrl", "coordinates": {"x": 50, "y": 20, "w": 40, "h": 6},
                   "url": "https://harbor-freight.example/rates?utm_source=story"}]})
    E.append({"_": "StoryItem", "id": 3, "_tgo": {"peer": PU(OLA)}, "date": iso(datetime(2026, 10, 2, 9, tzinfo=UTC)),
              "fwd_from": {"_": "StoryFwdHeader", "from_": PU(MARK), "story_id": 7}})
    E.append({"_": "SavedStarGifts", "_tgo": {"peer": PU(MARK)}, "gifts": [
        {"_": "SavedStarGift", "from_id": PU(OLA), "date": iso(datetime(2026, 9, 20, tzinfo=UTC)),
         "gift": {"_": "StarGift", "id": 5170145012310081615, "stars": 50}, "message": {"text": "for the Riga run"}},
        {"_": "SavedStarGift", "name_hidden": True, "date": iso(datetime(2026, 9, 25, tzinfo=UTC)),
         "gift": {"_": "StarGift", "id": 5170145012310081616, "stars": 500}}]})
    E.append({"_": "Contacts", "users": [], "contacts": [{"_": "Contact", "user_id": MARK, "mutual": True}]})

    with open(out / "entities.jsonl", "w", encoding="utf-8") as f:
        for e in E:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # ----------------------------------------------------------- messages
    Ms = []
    campaign = [
        "BREAKING: officials in the capital hid the real numbers on the grain deal. Share before it is deleted!",
        "They are lying to you about the border closures. Our sources confirm the opposite. veloria-truth.example",
        "Prices for heating will triple this winter, and the government knew it in spring. Read more: https://veloria-truth.example/heating?utm_source=tg",
        "Exclusive: the minister's family moved money abroad last month. Documents inside the link https://veloria-truth.example/docs",
        "Why are western advisers in our ministries? Ask your deputy today and demand answers in public.",
        "Mass protests are planned for Sunday in every regional centre. Bring your neighbours and be loud.",
        "The election commission already printed extra ballots, insiders say. Watch every polling station.",
        "Farmers are abandoned while foreign companies buy our land for nothing. Spread the truth now.",
        "Hospitals will close in three districts next month according to a leaked memo. Share widely.",
        "Remember who brought gas prices down before. Remember who raised them. Make the right choice.",
        "The truth about the bridge contract: kickbacks, shell firms and silence from the press.",
        "Final call: Sunday, 12:00, central squares. Our future is decided in the street, not in offices.",
    ]
    base = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)
    msg_id = {cid: 100 for cid in REGIONAL}
    photo_id = 7_000_000
    for k, text in enumerate(campaign):
        t = base + timedelta(hours=11 * k, minutes=random.Random(k).randrange(0, 50))
        cpost = 2000 + k
        cm = {"_": "Message", "id": cpost, "peer_id": PC(CENTRAL), "date": iso(t), "message": text, "post": True,
              "views": 30000 + 517 * k, "forwards": 40 + k}
        if k % 3 == 0:
            photo_id += 1
            cm["media"] = {"_": "MessageMediaPhoto", "photo": {"_": "Photo", "id": photo_id, "date": iso(t), "sizes": [
                {"_": "PhotoStrippedSize", "type": "i", "bytes": "01282e"},
                {"_": "PhotoSize", "type": "m", "w": 320, "h": 240, "size": 18011},
                {"_": "PhotoSize", "type": "x", "w": 800, "h": 600, "size": 71234}]}}
            cm["_tgo"] = {"media_path": "media/campaign/central.jpg"}
        Ms.append(cm)
        for j, cid in enumerate(REGIONAL):
            msg_id[cid] += 1
            rt = t + timedelta(seconds=1 + j * random.Random(k * 10 + j).choice([1, 2, 3]))
            rm = {"_": "Message", "id": msg_id[cid], "peer_id": PC(cid), "date": iso(rt), "post": True,
                  "views": 400 + 13 * j}
            if j % 3 == 0:
                rm["fwd_from"] = {"_": "MessageFwdHeader", "from_id": PC(CENTRAL), "date": iso(t), "channel_post": cpost}
                rm["message"] = text
            else:
                rm["message"] = text if not (j in (4, 7) and k % 2 == 0) else text.replace("Share", "Please share").replace(
                    "truth", "real truth")
            if k % 3 == 0 and j % 3 != 0:
                photo_id += 1
                rm["media"] = {"_": "MessageMediaPhoto", "photo": {"_": "Photo", "id": photo_id, "date": iso(rt),
                                                                    "sizes": [{"_": "PhotoSize", "type": "x", "w": 780, "h": 585, "size": 52000}]}}
                rm["_tgo"] = {"media_path": f"media/campaign/reupload_{cid}.jpg"}
            Ms.append(rm)
    # the organic channel: own posts + a single late forward (outside any window)
    for k in range(6):
        t = base + timedelta(days=2 * k, hours=15)
        Ms.append({"_": "Message", "id": 300 + k, "peer_id": PC(GARDEN), "date": iso(t), "post": True,
                   "message": ["Tomato seedlings: when to transplant outdoors.", "Club meeting Thursday at the library.",
                               "Photos from the autumn fair, thanks to all volunteers!", "Composting 101 for beginners.",
                               "Frost warning this weekend - cover your beds.", "Seed swap list is open."][k]})
    orig_2005 = next(m_ for m_ in Ms if m_.get("id") == 2005 and m_["peer_id"] == PC(CENTRAL))
    Ms.append({"_": "Message", "id": 310, "peer_id": PC(GARDEN), "post": True,
               "date": iso(datetime.fromisoformat(orig_2005["date"]) + timedelta(hours=6)), "message": campaign[5],
               "fwd_from": {"_": "MessageFwdHeader", "from_id": PC(CENTRAL), "date": orig_2005["date"],
                            "channel_post": 2005}})

    # harbor group: album (grouped_id) of photo-as-file + voice + pdf by MARK
    hg = PC(HARBOR_GROUP)
    t0 = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
    waveform = M.encode_waveform([abs(__import__("math").sin(i / 6.0)) * (0.3 + 0.7 * ((i * 37) % 11) / 10)
                                  for i in range(100)])
    Ms += [
        {"_": "Message", "id": 88210, "peer_id": hg, "from_id": PU(MARK), "date": iso(t0 - timedelta(minutes=40)),
         "message": "Morning all. Kirkenes run is confirmed for Friday, rates in @harbor_announce"},
        {"_": "Message", "id": 88211, "peer_id": hg, "from_id": PU(OLA), "date": iso(t0 - timedelta(minutes=31)),
         "message": "Cold storage is ready. Same terms as last time?",
         "reply_to": {"_": "MessageReplyHeader", "reply_to_msg_id": 88210},
         "reactions": {"_": "MessageReactions", "results": [{"_": "ReactionCount", "reaction": {"_": "ReactionEmoji", "emoticon": "👍"}, "count": 2}],
                       "recent_reactions": [{"_": "MessagePeerReaction", "peer_id": PU(MARK), "date": iso(t0 - timedelta(minutes=30)),
                                             "reaction": {"_": "ReactionEmoji", "emoticon": "👍"}},
                                            {"_": "MessagePeerReaction", "peer_id": PU(MD2), "date": iso(t0 - timedelta(minutes=29)),
                                             "reaction": {"_": "ReactionEmoji", "emoticon": "👍"}}]}},
        {"_": "Message", "id": 88213, "peer_id": hg, "from_id": PU(MARK), "date": iso(t0), "grouped_id": 1099511627776,
         "message": "Docs for the Kirkenes run, see voice + invoice",
         "media": {"_": "MessageMediaDocument", "document": {"_": "Document", "id": 5120000000000000001, "mime_type": "image/jpeg",
                                                              "size": 0, "attributes": [
                                                                  {"_": "DocumentAttributeFilename", "file_name": "IMG_8841.JPG"},
                                                                  {"_": "DocumentAttributeImageSize", "w": 4032, "h": 3024}]}},
         "_tgo": {"media_path": media["img8841"]}},
        {"_": "Message", "id": 88214, "peer_id": hg, "from_id": PU(MARK), "date": iso(t0), "grouped_id": 1099511627776,
         "media": {"_": "MessageMediaDocument", "document": {"_": "Document", "id": 5120000000000000002, "mime_type": "audio/ogg",
                                                              "size": 6006, "attributes": [
                                                                  {"_": "DocumentAttributeAudio", "voice": True, "duration": 42,
                                                                   "waveform": waveform.hex()}]}},
         "_tgo": {"media_path": media["voice"]}},
        {"_": "Message", "id": 88215, "peer_id": hg, "from_id": PU(MARK), "date": iso(t0), "grouped_id": 1099511627776,
         "media": {"_": "MessageMediaDocument", "document": {"_": "Document", "id": 5120000000000000003, "mime_type": "application/pdf",
                                                              "size": 0, "attributes": [
                                                                  {"_": "DocumentAttributeFilename", "file_name": "invoice_2026.pdf"}]}},
         "_tgo": {"media_path": media["invoice"]}},
        {"_": "Message", "id": 88220, "peer_id": hg, "from_id": PU(OLA), "date": iso(t0 + timedelta(hours=2)),
         "message": "Payout went through, screenshot attached",
         "media": {"_": "MessageMediaPhoto", "spoiler": True, "photo": {"_": "Photo", "id": 6200001, "date": iso(t0 + timedelta(hours=2)),
                                                                         "sizes": [{"_": "PhotoSize", "type": "y", "w": 720, "h": 1280, "size": 98000}]}},
         "_tgo": {"media_path": media["screenshot"]}},
        {"_": "Message", "id": 88221, "peer_id": hg, "from_id": PU(MARK), "date": iso(t0 + timedelta(hours=2, minutes=3)),
         "media": {"_": "MessageMediaDocument", "document": {"_": "Document", "id": 990001, "mime_type": "image/webp",
                                                              "size": 0, "attributes": [
                                                                  {"_": "DocumentAttributeSticker", "alt": "⚓",
                                                                   "stickerset": {"_": "InputStickerSetID", "id": 880001}},
                                                                  {"_": "DocumentAttributeImageSize", "w": 512, "h": 512}]}},
         "_tgo": {"media_path": media["sticker"]}},
        {"_": "Message", "id": 88230, "peer_id": hg, "from_id": PU(MD2), "date": iso(datetime(2026, 10, 3, 18, 2, tzinfo=UTC)),
         "message": "Photos from the second truck. Updated invoice attached.",
         "media": {"_": "MessageMediaDocument", "document": {"_": "Document", "id": 5120000000000000010, "mime_type": "image/jpeg",
                                                              "size": 0, "attributes": [
                                                                  {"_": "DocumentAttributeFilename", "file_name": "IMG_2207.JPG"}]}},
         "_tgo": {"media_path": media["img2207"]}},
        {"_": "Message", "id": 88231, "peer_id": hg, "from_id": PU(MD2), "date": iso(datetime(2026, 10, 3, 18, 3, tzinfo=UTC)),
         "media": {"_": "MessageMediaDocument", "document": {"_": "Document", "id": 5120000000000000011, "mime_type": "application/pdf",
                                                              "size": 0, "attributes": [
                                                                  {"_": "DocumentAttributeFilename", "file_name": "invoice_2026_v2.pdf"}]}},
         "_tgo": {"media_path": media["invoice2"]}},
        {"_": "Message", "id": 88232, "peer_id": hg, "from_id": PU(OLA), "date": iso(datetime(2026, 10, 4, 8, 0, tzinfo=UTC)),
         "message": "Someone sent me this, is it real?", "fwd_from": {"_": "MessageFwdHeader", "from_name": "Hidden Broker",
                                                                    "date": iso(datetime(2026, 10, 3, tzinfo=UTC))}},
        {"_": "Message", "id": 88233, "peer_id": hg, "from_id": PU(OLA), "date": iso(datetime(2026, 10, 4, 8, 5, tzinfo=UTC)),
         "message": "Rates page", "media": {"_": "MessageMediaWebPage", "webpage": {
             "_": "WebPage", "url": "https://www.harbor-freight.example/rates/?utm_campaign=x", "site_name": "Harbor Freight",
             "title": "Rates"}}},
        {"_": "MessageService", "id": 88200, "peer_id": hg, "from_id": PU(MARK), "date": iso(datetime(2026, 9, 9, tzinfo=UTC)),
         "action": {"_": "MessageActionChatAddUser", "users": [SILENT]}},
        {"_": "Message", "id": 501, "peer_id": PC(HARBOR_CHANNEL), "post": True, "date": iso(t0 - timedelta(hours=2)),
         "message": "Kirkenes - Riga, Friday. Book via @harbor_helper_bot or https://harbor-freight.example/rates"},
        {"_": "Message", "id": 41, "peer_id": PC(FAITH), "from_id": PU(OLA), "date": iso(datetime(2026, 9, 14, 18, tzinfo=UTC)),
         "message": "Evening prayer moved to 19:00 this week."},
    ]
    with open(out / "messages.jsonl", "w", encoding="utf-8") as f:
        for m_ in Ms:
            f.write(json.dumps(m_, ensure_ascii=False) + "\n")


# --------------------------------------------------------------- export
def _export(root: Path) -> None:
    ex = root / "export"
    (ex / "files").mkdir(parents=True, exist_ok=True)
    (ex / "voice_messages").mkdir(exist_ok=True)
    (ex / "photos").mkdir(exist_ok=True)
    src = root / "mtproto" / "media"
    shutil.copy(src / "IMG_8841.JPG", ex / "files" / "IMG_8841.JPG")
    shutil.copy(src / "voice_0042.ogg", ex / "voice_messages" / "audio_1@01-10-2026_09-30-00.ogg")
    shutil.copy(src / "voice_0042.ogg.transcript.json", ex / "voice_messages" / "audio_1@01-10-2026_09-30-00.ogg.transcript.json")
    shutil.copy(src / "invoice_2026.pdf", ex / "files" / "invoice_2026.pdf")
    shutil.copy(src / "avatars" / "ola.jpg", ex / "photos" / "photo_1@14-09-2026_10-00-00.jpg")

    def dt(d: datetime):
        return {"date": d.strftime("%Y-%m-%dT%H:%M:%S"), "date_unixtime": str(int(d.timestamp()))}

    t0 = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
    harbor = [
        {"id": 88199, "type": "service", **dt(datetime(2026, 9, 1, 10, tzinfo=UTC)), "actor": "Mark Dunmore",
         "actor_id": f"user{MARK}", "action": "create_group", "title": "Harbor Logistics Chat",
         "members": ["Mark Dunmore", "Ola Fisk"]},
        {"id": 88201, "type": "service", **dt(datetime(2026, 9, 9, 12, tzinfo=UTC)), "actor": "Mark Dunmore",
         "actor_id": f"user{MARK}", "action": "invite_members", "members": ["Silent Reader", "Field Analyst"]},
        {"id": 88202, "type": "service", **dt(datetime(2026, 9, 28, 7, tzinfo=UTC)), "actor": "M. D.",
         "actor_id": f"user{MD2}", "action": "join_group_by_link", "inviter": "Mark Dunmore"},
        {"id": 88210, "type": "message", **dt(t0 - timedelta(minutes=40)), "from": "Mark Dunmore", "from_id": f"user{MARK}",
         "text": ["Morning all. Kirkenes run is confirmed for Friday, rates in ",
                  {"type": "mention", "text": "@harbor_announce"}],
         "text_entities": [{"type": "plain", "text": "Morning all. Kirkenes run is confirmed for Friday, rates in "},
                           {"type": "mention", "text": "@harbor_announce"}]},
        {"id": 88211, "type": "message", **dt(t0 - timedelta(minutes=31)), "from": "Ola Fisk", "from_id": f"user{OLA}",
         "reply_to_message_id": 88210, "text": "Cold storage is ready. Same terms as last time?",
         "reactions": [{"type": "emoji", "count": 2, "emoji": "👍", "recent": [
             {"from": "Mark Dunmore", "from_id": f"user{MARK}", "date": (t0 - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")},
             {"from": "M. D.", "from_id": f"user{MD2}", "date": (t0 - timedelta(minutes=29)).strftime("%Y-%m-%dT%H:%M:%S")}]}]},
        {"id": 88212, "type": "message", **dt(t0 - timedelta(minutes=10)), "from": "Field Analyst",
         "from_id": f"user{COLLECTOR}", "text": "Thanks, following."},
        {"id": 88213, "type": "message", **dt(t0), "from": "Mark Dunmore", "from_id": f"user{MARK}",
         "file": "files/IMG_8841.JPG", "file_name": "IMG_8841.JPG", "file_size": (ex / "files" / "IMG_8841.JPG").stat().st_size,
         "mime_type": "image/jpeg", "text": "Docs for the Kirkenes run, see voice + invoice"},
        {"id": 88214, "type": "message", **dt(t0), "from": "Mark Dunmore", "from_id": f"user{MARK}",
         "file": "voice_messages/audio_1@01-10-2026_09-30-00.ogg", "media_type": "voice_message",
         "mime_type": "audio/ogg", "duration_seconds": 42, "text": ""},
        {"id": 88215, "type": "message", **dt(t0), "from": "Mark Dunmore", "from_id": f"user{MARK}",
         "file": "files/invoice_2026.pdf", "file_name": "invoice_2026.pdf", "mime_type": "application/pdf", "text": ""},
        {"id": 88216, "type": "message", **dt(t0 + timedelta(minutes=5)), "from": "Mark Dunmore", "from_id": f"user{MARK}",
         "file": "(File not included. Change data exporting settings to download.)", "media_type": "video_file",
         "mime_type": "video/mp4", "duration_seconds": 95, "width": 1920, "height": 1080, "text": "Loading video"},
        {"id": 88217, "type": "message", **dt(t0 + timedelta(minutes=7)), "from": "Ola Fisk", "from_id": f"user{OLA}",
         "photo": "photos/photo_1@14-09-2026_10-00-00.jpg", "width": 320, "height": 320, "text": "old pic from the expo",
         "media_spoiler": True},
        {"id": 88218, "type": "message", **dt(t0 + timedelta(minutes=9)), "from": "Ola Fisk", "from_id": f"user{OLA}",
         "text": "Did you see what @velwire posted? https://veloria-truth.example/docs?utm_source=fwd",
         "forwarded_from": "Veloria Central Wire", "forwarded_from_id": f"channel{CENTRAL}"},
    ]
    result = {
        "about": "Here is the data you requested. (synthetic TGO demo export)",
        "personal_information": {"user_id": COLLECTOR, "first_name": "Field", "last_name": "Analyst",
                                 "phone_number": "+47 400 00 000", "username": "@tgo_collector_demo",
                                 "bio": "OSINT demo collector account"},
        "profile_pictures": [],
        "contacts": {"about": "contacts", "list": [
            {"user_id": MARK, "first_name": "Mark (harbor)", "last_name": "", "phone_number": PHONE_MARK,
             **dt(datetime(2026, 8, 30, tzinfo=UTC))},
            {"first_name": "Unknown", "last_name": "Supplier", "phone_number": "+371 2000 0000",
             **dt(datetime(2026, 9, 5, tzinfo=UTC))}]},
        "frequent_contacts": {"about": "", "list": [{"id": MARK, "category": "people", "type": "user",
                                                    "name": "Mark (harbor)", "rating": 0.82}]},
        "chats": {"about": "chats", "list": [
            {"name": "Harbor Logistics Chat", "type": "private_supergroup", "id": HARBOR_GROUP, "messages": harbor},
            {"name": "Mark (harbor)", "type": "personal_chat", "id": MARK, "messages": [
                {"id": 1, "type": "message", **dt(datetime(2026, 8, 30, 12, tzinfo=UTC)), "from": "Mark (harbor)",
                 "from_id": f"user{MARK}", "text": f"My number if Telegram is down: {PHONE_MARK}"},
                {"id": 2, "type": "message", **dt(datetime(2026, 8, 30, 12, 5, tzinfo=UTC)), "from": "Field Analyst",
                 "from_id": f"user{COLLECTOR}", "text": "Noted."}]},
        ]},
    }
    (ex / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


def _analyst(root: Path) -> None:
    a = root / "analyst"
    a.mkdir(parents=True, exist_ok=True)
    (a / "judgements.json").write_text(json.dumps([
        {"source": f"user:{MARK}", "target": f"user:{MD2}", "judgement": "positive", "decidedBy": "analyst:A-17",
         "legalBasis": "Art. 9(2)(j) research + national safeguard ref DEMO-001",
         "rationale": "Same camera body serial, same personal channel, re-compressed avatar, identical bio; second account registered 2026-04."},
        {"source": f"user:{MARK}", "target": f"user:{OLA}", "judgement": "negative", "decidedBy": "analyst:A-17",
         "legalBasis": "Art. 9(2)(j) research + national safeguard ref DEMO-001",
         "rationale": "Shared TRON wallet is a payment counterpart, not a shared operator."},
    ], indent=1), encoding="utf-8")


def generate(dir_: str | Path) -> Path:
    """Write the full synthetic dataset to ``dir_`` and return it."""
    root = Path(dir_)
    if root.exists():
        for sub in ("mtproto", "export", "analyst"):
            shutil.rmtree(root / sub, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    media = _media(root)
    _mtproto(root, media)
    _export(root)
    _analyst(root)
    (root / "README.txt").write_text("Synthetic TGO demo data. Entirely fictional.\n", encoding="utf-8")
    return root
