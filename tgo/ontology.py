"""TGO (Telegram Graph Ontology) expressed as code: the TBox.

Everything the pipeline writes (node labels, relationship types, SKOS
concepts) must be declared here.  The validator, the Turtle exporter and the
viewer legend are all driven from these tables, so this module is the single
source of truth for the vocabulary.

Six layers (from the TGO report): Core, Content, Network, Economy, Derived,
Provenance.  Epistemic split (L6): observed / file_declared / inferred /
analyst.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

DOMAIN = os.environ.get("TGO_DOMAIN", "tgo.example.org")
TG = f"https://{DOMAIN}/ontology/tg#"
TGR = f"https://{DOMAIN}/id/"

PREFIXES = {
    "tg": TG,
    "tgr": TGR,
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "prov": "http://www.w3.org/ns/prov#",
    "oa": "http://www.w3.org/ns/oa#",
    "org": "http://www.w3.org/ns/org#",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "as": "https://www.w3.org/ns/activitystreams#",
    "schema": "https://schema.org/",
    "nfo": "http://www.semanticdesktop.org/ontologies/2007/03/22/nfo#",
    "ma": "http://www.w3.org/ns/ma-ont#",
    "time": "http://www.w3.org/2006/time#",
    "dcterms": "http://purl.org/dc/terms/",
    "mls": "http://www.w3.org/ns/mls#",
    "sioc": "http://rdfs.org/sioc/ns#",
    "ftm": "https://followthemoney.tech/ns#",
}


class Status(str, Enum):
    """Epistemic status of every fact (TGO L6)."""

    OBSERVED = "observed"            # platform metadata, EXIF as read, OCR text as read
    FILE_DECLARED = "file_declared"  # XMP, docProps, ID3: forgeable by the file author
    INFERRED = "inferred"            # every ML output and every person link
    ANALYST = "analyst"              # human judgement / case grouping


class Visibility(str, Enum):
    """tg:FieldVisibility: an absent field is not absence."""

    VISIBLE = "visible"
    HIDDEN_BY_PRIVACY = "hidden_by_privacy"
    NOT_SET = "not_set"
    NOT_REQUESTED = "not_requested"
    MIN_OBJECT = "min_object"
    NOT_EXPOSED_BY_SOURCE = "not_exposed_by_source"


class Scope(str, Enum):
    """tg:visibilityScope: who could have observed the fact."""

    PUBLIC = "public"
    MEMBERS = "members"
    ADMINS = "admins"
    OWNER_ONLY = "owner_only"
    LINK_HOLDERS = "link_holders"
    COLLECTOR_ONLY = "collector_only"  # viewer-relative ego-network facts


ELEVATED_SCOPES = {Scope.ADMINS.value, Scope.OWNER_ONLY.value}


@dataclass(frozen=True)
class ClassDef:
    """A TGO class: parents, external alignments, layer, default epistemic status, viewer family."""
    name: str
    parents: tuple[str, ...] = ()
    align: tuple[str, ...] = ()
    layer: str = "Core"
    epistemic: str = "observed"
    doc: str = ""
    family: str = "other"  # used by the viewer for shape/colour


def _c(name, parents=(), align=(), layer="Core", epistemic="observed", doc="", family=None):
    return ClassDef(name, tuple(parents), tuple(align), layer, epistemic, doc,
                    family or (parents[0] if parents else name))


_CLASS_LIST = [
    # ---- Core: accounts and people --------------------------------------
    _c("Account", (), ("foaf:OnlineAccount", "ftm:UserAccount"), doc="A Telegram account. Never a person.", family="Account"),
    _c("HumanAccount", ("Account",), family="Account"),
    _c("BotAccount", ("Account",), family="Account"),
    _c("DeletedAccount", ("Account",), ("as:Tombstone",), family="Account"),
    _c("Username", (), ("skos:Concept",), doc="A username identifies an account at a time (collectible usernames transfer).", family="Identifier"),
    _c("ProfileSnapshot", (), ("as:Profile",), doc="One per userFull fetch.", family="Snapshot"),
    _c("ContactAnnotation", (), ("oa:Annotation",), doc="Collector-authored contact name/note; scoped to the collector.", family="Snapshot"),
    _c("Person", (), ("foaf:Person", "ftm:Person"), epistemic="inferred", doc="Minted only on a positive identity judgement.", family="Person"),
    _c("PersonCluster", (), ("prov:Entity",), layer="Derived", epistemic="inferred", family="Person"),
    _c("IdentityJudgement", (), ("prov:Entity", "ftm:Similar"), layer="Derived", epistemic="inferred", family="Judgement"),
    # ---- Core: chats and groupings ---------------------------------------
    _c("Group", (), ("foaf:Group", "as:Group"), doc="Abstract grouping of people.", family="Group"),
    _c("PlatformGroup", ("Group",), family="Chat"),
    _c("Chat", ("PlatformGroup",), ("sioc:Container",), family="Chat"),
    _c("PrivateChat", ("Chat",), family="Chat"),
    _c("BasicGroup", ("Chat",), family="Chat"),
    _c("Supergroup", ("Chat",), family="Chat"),
    _c("Gigagroup", ("Supergroup",), family="Chat"),
    _c("Forum", ("Supergroup",), family="Chat"),
    _c("Channel", ("Chat",), doc="Broadcast channel.", family="Chat"),
    _c("Monoforum", ("Chat",), doc="Channel direct messages.", family="Chat"),
    _c("ForumTopic", ("PlatformGroup",), family="Chat"),
    _c("Community", ("PlatformGroup",), ("org:FormalOrganization",), doc="Layer 230 group-of-groups container.", family="Community"),
    _c("Chatlist", ("PlatformGroup",), doc="Shareable folder.", family="Chat"),
    _c("GroupCall", ("PlatformGroup",), family="Chat"),
    _c("RealWorldGroup", ("Group",), ("org:Organization",), epistemic="analyst", family="Group"),
    _c("FormalOrganization", ("RealWorldGroup",), ("org:FormalOrganization",), epistemic="analyst", family="Group"),
    _c("InferredGroup", ("Group",), ("prov:Entity",), layer="Derived", epistemic="inferred", family="InferredGroup"),
    _c("CommunityCluster", ("InferredGroup",), layer="Derived", epistemic="inferred", family="InferredGroup"),
    _c("CoordinationCluster", ("InferredGroup",), layer="Derived", epistemic="inferred", family="InferredGroup"),
    _c("OperatorCluster", ("InferredGroup",), layer="Derived", epistemic="inferred", family="InferredGroup"),
    _c("CaseGroup", ("Group",), epistemic="analyst", family="InferredGroup"),
    _c("GroupMembership", (), ("org:Membership",), layer="Network", epistemic="inferred", family="Membership"),
    # ---- Content ---------------------------------------------------------
    _c("Message", (), ("sioc:Post", "schema:Message"), layer="Content", family="Message"),
    _c("ServiceMessage", ("Message",), layer="Content", family="Message"),
    _c("Story", (), ("schema:CreativeWork",), layer="Content", family="Message"),
    _c("MediaAttachment", (), (), layer="Content", doc="Occurrence of media in a message: spoiler/TTL live here.", family="Message"),
    _c("Media", (), ("schema:MediaObject",), layer="Content", family="Media"),
    _c("Photo", ("Media",), ("schema:ImageObject", "ma:Image", "nfo:RasterImage"), layer="Content", doc="No original bytes: server renditions only.", family="Media"),
    _c("PhotoRendition", (), ("schema:ImageObject",), layer="Content", family="Rendition"),
    _c("File", ("Media",), ("nfo:FileDataObject",), layer="Content", doc="Byte-preserved document; keyed by sha256.", family="Media"),
    _c("Document", ("File",), ("schema:DigitalDocument",), layer="Content", family="Media"),
    _c("Video", ("File",), ("schema:VideoObject",), layer="Content", family="Media"),
    _c("RoundVideo", ("Video",), layer="Content", family="Media"),
    _c("Animation", ("Video",), layer="Content", family="Media"),
    _c("Audio", ("File",), ("schema:AudioObject",), layer="Content", family="Media"),
    _c("VoiceNote", ("Audio",), layer="Content", family="Media"),
    _c("Sticker", ("File",), ("schema:ImageObject",), layer="Content", family="Media"),
    _c("CustomEmoji", ("File",), layer="Content", family="Media"),
    _c("StickerSet", (), (), layer="Content", family="Media"),
    # ---- Content extraction ----------------------------------------------
    _c("Transcript", (), (), layer="Derived", epistemic="inferred", family="Extracted"),
    _c("TranscriptSegment", (), (), layer="Derived", epistemic="inferred", family="Extracted"),
    _c("ExtractedText", (), (), layer="Derived", epistemic="observed", family="Extracted"),
    _c("DocumentMetadata", (), (), layer="Derived", epistemic="file_declared", family="Extracted"),
    _c("MediaFingerprint", (), ("nfo:FileHash",), layer="Derived", epistemic="observed", family="Extracted"),
    _c("ExtractedEntity", (), (), layer="Derived", epistemic="inferred", family="Extracted"),
    _c("Identifier", (), (), layer="Economy", doc="Checksum-validated identifier.", family="Identifier"),
    _c("CryptoAddress", ("Identifier",), layer="Economy", family="Identifier"),
    _c("IBAN", ("Identifier",), layer="Economy", family="Identifier"),
    _c("PhoneNumber", ("Identifier",), layer="Economy", family="Identifier"),
    _c("Url", ("Identifier",), layer="Economy", family="Identifier"),
    _c("Domain", ("Identifier",), layer="Economy", family="Identifier"),
    _c("Email", ("Identifier",), layer="Economy", family="Identifier"),
    _c("TelegramHandle", ("Identifier",), layer="Economy", family="Identifier"),
    _c("Device", (), (), layer="Derived", epistemic="observed", doc="Camera body serial etc. from EXIF (document mode only).", family="Device"),
    _c("GeoPoint", (), ("schema:GeoCoordinates",), layer="Derived", family="Device"),
    _c("Gift", (), (), layer="Economy", family="Identifier"),
    # ---- Provenance ------------------------------------------------------
    _c("CollectionRun", (), ("prov:Activity",), layer="Provenance", family="Run"),
    _c("DetectionRun", (), ("prov:Activity", "mls:Run"), layer="Provenance", family="Run"),
    _c("ModelAgent", (), ("prov:SoftwareAgent",), layer="Provenance", family="Run"),
    _c("Collector", ("Account",), ("prov:Agent",), layer="Provenance", family="Account"),
]
CLASSES: dict[str, ClassDef] = {c.name: c for c in _CLASS_LIST}


def ancestors(label: str) -> list[str]:
    """All (transitive) superclasses of ``label``."""
    out, stack = [], [label]
    while stack:
        cur = stack.pop()
        cd = CLASSES.get(cur)
        if not cd:
            continue
        for p in cd.parents:
            if p not in out:
                out.append(p)
                stack.append(p)
    return out


def family(labels) -> str:
    """Most specific viewer family for a label set."""
    for lab in sorted(labels):
        cd = CLASSES.get(lab)
        if cd and not any(lab in ancestors(o) for o in labels if o != lab):
            return cd.family
    return "other"


# --------------------------------------------------------------------------
# Relationship types.  tier = who can observe (decides evidentiary weight).
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RelDef:
    """A TGO relationship type: observability tier, default status, colour, FtM / RDF mapping."""
    name: str
    tier: str            # structural | viewer_only | pairwise | admin_only | derived | provenance
    status: str          # default epistemic status
    color: str
    doc: str = ""
    ftm: str | None = None
    rdf: str | None = None


_REL_LIST = [
    # structural / content
    RelDef("SENT", "pairwise", "observed", "#8a8f98", "account sent message", rdf="sioc:has_creator"),
    RelDef("IN_CHAT", "structural", "observed", "#b0b4ba", "message posted in chat", rdf="sioc:has_container"),
    RelDef("POSTED_IN", "pairwise", "observed", "#6c7a89", "aggregated: account posted in chat (count = messages)"),
    RelDef("REPLIES_TO", "pairwise", "observed", "#9b59b6", rdf="sioc:reply_of"),
    RelDef("FORWARD_OF", "pairwise", "observed", "#e67e22", "message is a forward of original message"),
    RelDef("FORWARDS_FROM", "pairwise", "observed", "#e67e22", "aggregated: chat forwards content from chat (DFRLab edge)"),
    RelDef("MENTIONS", "pairwise", "observed", "#16a085"),
    RelDef("REACTED", "pairwise", "observed", "#f1c40f"),
    RelDef("VOTED", "pairwise", "observed", "#f39c12"),
    RelDef("HAS_MEDIA", "structural", "observed", "#95a5a6"),
    RelDef("ATTACHMENT_OF", "structural", "observed", "#95a5a6"),
    RelDef("HAS_RENDITION", "structural", "observed", "#bdc3c7"),
    RelDef("IN_STICKER_SET", "structural", "observed", "#bdc3c7"),
    RelDef("OWNED_BY", "structural", "observed", "#7f8c8d", "sticker set ownership: creator flag or _by_<bot> only"),
    RelDef("HAS_USERNAME", "structural", "observed", "#3498db"),
    RelDef("HAS_PROFILE", "structural", "observed", "#bdc3c7"),
    RelDef("HAS_AVATAR", "structural", "observed", "#bdc3c7"),
    RelDef("CREATED", "pairwise", "observed", "#2c3e50"),
    RelDef("ANNOTATES", "viewer_only", "observed", "#bdc3c7"),
    # people <-> people
    RelDef("KNOWS", "viewer_only", "observed", "#2980b9", "contact of collector", ftm="Associate"),
    RelDef("CLOSE_FRIEND_OF", "viewer_only", "observed", "#2980b9", ftm="Associate"),
    RelDef("GAVE_GIFT_TO", "pairwise", "observed", "#e84393", "dated; may be retroactively hidden", ftm="Associate"),
    RelDef("REPOSTED", "pairwise", "observed", "#d35400", "story repost"),
    RelDef("FEATURES", "pairwise", "observed", "#27ae60", "personal channel on profile"),
    RelDef("DELEGATES_TO", "pairwise", "observed", "#7f8c8d", "business bot delegation"),
    RelDef("VERIFIES", "pairwise", "observed", "#7f8c8d"),
    # membership
    RelDef("MEMBER_OF", "pairwise", "observed", "#34495e", ftm="Membership", rdf="org:memberOf"),
    RelDef("ADMIN_OF", "admin_only", "observed", "#c0392b", ftm="Membership"),
    RelDef("INVITED_BY", "admin_only", "observed", "#c0392b"),
    RelDef("APPROVED_BY", "admin_only", "observed", "#c0392b"),
    RelDef("BOOSTED", "admin_only", "observed", "#c0392b"),
    RelDef("MEMBER_OF_COMMUNITY", "pairwise", "observed", "#8e44ad", "chat linked into a Community", rdf="org:subOrganizationOf"),
    RelDef("LINKED_DISCUSSION", "pairwise", "observed", "#8e44ad"),
    RelDef("IN_TOPIC", "structural", "observed", "#b0b4ba"),
    # extraction
    RelDef("FINGERPRINT_OF", "derived", "observed", "#7f8c8d"),
    RelDef("NEAR_DUPLICATE_OF", "derived", "inferred", "#e74c3c", "perceptual hash within threshold"),
    RelDef("SAME_FILE_AS", "derived", "observed", "#c0392b", "same sha256 / file_unique_id / document id"),
    RelDef("HAS_TRANSCRIPT", "derived", "inferred", "#16a085"),
    RelDef("HAS_SEGMENT", "derived", "inferred", "#16a085"),
    RelDef("HAS_TEXT", "derived", "observed", "#16a085"),
    RelDef("HAS_METADATA", "derived", "file_declared", "#a0522d"),
    RelDef("CONTAINS_IDENTIFIER", "derived", "inferred", "#d4ac0d", "checksum-validated identifier found in content"),
    RelDef("MENTIONS_ENTITY", "derived", "inferred", "#1abc9c"),
    RelDef("CAPTURED_WITH", "derived", "observed", "#a0522d", "EXIF body serial (document mode only)"),
    RelDef("HAS_EXIF_GPS", "derived", "observed", "#a0522d", "EXIF GPS (spoofable)"),
    RelDef("SAME_DOCUMENT_LINEAGE", "derived", "file_declared", "#a0522d", "shared xmpMM:DocumentID"),
    # coordination (DFRLab edge types)
    RelDef("IDENTICAL_POST", "derived", "inferred", "#ff4757", "same normalized text within window", ftm=None),
    RelDef("NEAR_DUP_POST", "derived", "inferred", "#ff6b81"),
    RelDef("SYNC_POST", "derived", "inferred", "#ff4757", "posts synchronized to the seconds"),
    RelDef("CO_FORWARD", "derived", "inferred", "#ffa502", "forward the same original within window"),
    RelDef("CO_URL", "derived", "inferred", "#ffa502"),
    RelDef("CO_FINGERPRINT", "derived", "inferred", "#ffa502"),
    RelDef("SAME_CREATION_DATE", "derived", "inferred", "#a4b0be"),
    RelDef("NAME_HISTORY_LINK", "derived", "observed", "#a4b0be"),
    RelDef("SHARED_DOMAIN_PROMOTION", "derived", "inferred", "#ffa502"),
    # identity & groups
    RelDef("CANDIDATE_SAME_PERSON", "derived", "inferred", "#ff00aa", "scored candidate; never owl:sameAs", ftm="Similar"),
    RelDef("OPERATED_BY", "derived", "inferred", "#ff00aa", "account operated by person cluster"),
    RelDef("IN_CLUSTER", "derived", "inferred", "#5f27cd", "membership in an inferred group"),
    # provenance
    RelDef("GENERATED_BY", "provenance", "observed", "#dfe4ea", rdf="prov:wasGeneratedBy"),
    RelDef("USED", "provenance", "observed", "#dfe4ea", rdf="prov:used"),
    RelDef("ASSOCIATED_WITH", "provenance", "observed", "#dfe4ea", rdf="prov:wasAssociatedWith"),
]
RELATIONS: dict[str, RelDef] = {r.name: r for r in _REL_LIST}

# Relationship types that must never exist in the graph.
FORBIDDEN_RELATIONS = {"SAME_AS", "OWL_SAMEAS", "FACE_MATCH", "SPEAKER_MATCH", "STICKER_CREATOR_INFERRED"}
FORBIDDEN_LABELS = {"FaceEmbedding", "FaceCluster", "SpeakerEmbedding", "VoicePrint", "BiometricTemplate"}

# --------------------------------------------------------------------------
# SKOS concept schemes
# --------------------------------------------------------------------------
GROUP_ROLES = [
    "creator", "owner", "admin", "member", "restricted", "banned", "left", "subscriber",
    "booster", "giveaway_winner", "call_participant", "speaker", "inviter", "approver",
    "originator", "amplifier", "bridge", "hub", "core", "peripheral",
]
GROUP_KINDS = [
    "private_chat", "basic_group", "supergroup", "gigagroup", "forum", "broadcast_channel",
    "monoforum", "forum_topic", "community", "chatlist", "group_call", "giveaway", "boosters",
]
ALGORITHMS = [
    "leiden_cpm", "leiden_modularity", "louvain", "infomap", "kcore", "coortweet",
    "nomenklatura_xref", "splink", "noisy_or_evidence",
]
JUDGEMENTS = ["positive", "negative", "unsure", "no_judgement"]

# Chat topics whose membership edges are GDPR Art. 9 special-category data.
SPECIAL_CATEGORY_TOPICS = {"religion", "politics", "health", "trade_union", "sexuality", "ethnicity"}

# Facts the platform simply does not expose (rendered as typed gaps).
NOT_OBTAINABLE = [
    ("Original uploaded bytes of a compressed photo", "API exposes only server-resized renditions"),
    ("Reliable EXIF/GPS/C2PA from photo-mode images", "re-encoded JPEGs; survives only on documents"),
    ("Transcripts/waveforms via Bot API or Desktop export", "only MTProto messages.transcribeAudio"),
    ("Channel subscriber lists, boosters, join requesters, story viewers", "admin/owner only"),
    ("Member lists via the Bot API", "bots cannot enumerate members"),
    ("Other users' privacy rules", "only the collector's own account.getPrivacy"),
    ("A verified sticker-set creator from the set ID", "unofficial, untested, excluded by design"),
    ("Message-media IDs in the Desktop export", "join by (chat, msg, date) or sha256"),
    ("People Nearby geo-groups", "feature retired September 2024"),
]

# Where each person attribute can come from (MTProto layer 230 / Bot API 10.3 / Desktop export).
PERSON_FIELD_SOURCES = {
    "id": ("user.id", "User.id", "personal_information.user_id / from_id"),
    "first_name": ("user.first_name", "User.first_name", "personal_information / from"),
    "username": ("user.username, user.usernames", "User.username", "from handle"),
    "phone": ("user.phone (privacy-gated)", None, "contacts.list[].phone_number"),
    "about": ("userFull.about", "ChatFullInfo.bio", "personal_information.bio"),
    "birthday": ("userFull.birthday", "ChatFullInfo.birthdate", None),
    "status": ("user.status", None, None),
    "premium": ("user.premium", "User.is_premium", None),
    "personal_channel": ("userFull.personal_channel_id", "ChatFullInfo.personal_chat", None),
    "registration_month": ("peerSettings.registration_month", None, None),
    "phone_country": ("peerSettings.phone_country", None, None),
}
