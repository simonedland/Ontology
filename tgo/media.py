"""Telegram media model: classification, renditions, waveforms, media areas.

Two storage regimes (core.telegram.org/api/files):

* ``photo``: server-side JPEG renditions only, **no original bytes** -> key on
  ``photo:{id}``, fingerprint perceptually, never trust EXIF/C2PA.
* ``document``: everything else; **original bytes preserved** -> sha256
  identity and embedded-metadata extraction are meaningful.

Document attributes are *sender-declared*, so values such as ``duration`` or
``file_name`` are tagged ``declared_by_sender`` rather than measured.
"""
from __future__ import annotations

PHOTO_MODE = "PhotoMode"
DOCUMENT_MODE = "DocumentMode"
TTL_VIEW_ONCE = 0x7FFFFFFF

# PhotoSize type letters (verbatim from core: files)
PHOTO_SIZE_TYPES = {
    "s": "box 100x100", "m": "box 320x320", "x": "box 800x800", "y": "box 1280x1280",
    "w": "box 2560x2560", "a": "crop 160x160", "b": "crop 320x320", "c": "crop 640x640",
    "d": "crop 1280x1280", "i": "stripped inline thumbnail (inline literal)",
    "j": "vector SVG outline (inline literal)",
}
INLINE_SIZE_TYPES = {"i", "j"}
VIDEO_SIZE_TYPES = {"p": "animated profile preview", "u": "animated profile full",
                    "v": "video preview", "f": "premium sticker effect (TGS)"}

AUX_MIME = {
    "application/x-mpegurl": "HlsPlaylist",
    "application/x-tgstoryboard": "Storyboard",
    "application/x-tgstoryboardmap": "StoryboardMap",
}


def _attrs(attributes: list[dict]) -> dict[str, dict]:
    return {a.get("_", ""): a for a in attributes or []}


def classify_document(mime_type: str | None, attributes: list[dict]) -> str:
    """Return the TGO class for an MTProto document.

    Order matters (TDLib MessageContent.cpp): Sticker and CustomEmoji first,
    then round/voice/animated flags, then Video/Audio, then Document, because a
    GIF and a video sticker both also carry ``documentAttributeVideo``.
    """
    a = _attrs(attributes)
    mime = (mime_type or "").lower()
    if mime in AUX_MIME:
        return AUX_MIME[mime]
    if "DocumentAttributeSticker" in a:
        return "Sticker"
    if "DocumentAttributeCustomEmoji" in a:
        return "CustomEmoji"
    vid = a.get("DocumentAttributeVideo")
    aud = a.get("DocumentAttributeAudio")
    if vid and vid.get("round_message"):
        return "RoundVideo"
    if aud and aud.get("voice"):
        return "VoiceNote"
    if "DocumentAttributeAnimated" in a:
        return "Animation"
    if vid:
        return "Video"
    if aud:
        return "Audio"
    return "Document"


def document_props(mime_type: str | None, attributes: list[dict]) -> dict:
    """Flatten DocumentAttributes into sender-declared properties."""
    a = _attrs(attributes)
    p: dict = {"mime": mime_type}
    if fn := a.get("DocumentAttributeFilename"):
        p["fileName"] = fn.get("file_name")
    if im := a.get("DocumentAttributeImageSize"):
        p["width"], p["height"] = im.get("w"), im.get("h")
    if v := a.get("DocumentAttributeVideo"):
        p.update(duration=v.get("duration"), width=v.get("w"), height=v.get("h"),
                 supportsStreaming=v.get("supports_streaming"), nosound=v.get("nosound"),
                 videoCodec=v.get("video_codec"))
    if au := a.get("DocumentAttributeAudio"):
        p.update(duration=au.get("duration"), title=au.get("title"), performer=au.get("performer"))
        if au.get("waveform"):
            wf = au["waveform"]
            raw = bytes(wf) if isinstance(wf, (list, bytes, bytearray)) else bytes.fromhex(wf)
            p["waveform"] = [round(x, 3) for x in decode_waveform(raw)]
    if st := a.get("DocumentAttributeSticker"):
        p["stickerAlt"] = st.get("alt")
    p = {k: v for k, v in p.items() if v is not None}
    p["declaredBySender"] = sorted(k for k in p if k not in ("mime", "waveform"))
    return p


# Desktop export ``media_type`` -> TGO class.  Classify from media_type +
# mime_type, never from the folder name (GIFs and video stickers both land in
# ``video_files/``).
EXPORT_MEDIA_TYPES = {
    "animation": "Animation",
    "video_file": "Video",
    "video_message": "RoundVideo",
    "voice_message": "VoiceNote",
    "audio_file": "Audio",
    "sticker": "Sticker",
}


def classify_export(msg: dict) -> tuple[str, str] | None:
    """Return (TGO class, send mode) for a Desktop export message, or None."""
    if msg.get("photo"):
        return "Photo", PHOTO_MODE
    if msg.get("file") or msg.get("media_type"):
        cls = EXPORT_MEDIA_TYPES.get(msg.get("media_type", ""), "Document")
        return cls, DOCUMENT_MODE
    return None


# ------------------------------------------------------------------ waveform
def decode_waveform(data: bytes) -> list[float]:
    """Decode Telegram's 5-bit packed voice waveform (tdesktop data_document.cpp).

    ``valuesCount = bytes*8/5``; each value is ``(uint16_le >> shift) & 0x1F``.
    Returns samples normalised to 0..1.
    """
    count = len(data) * 8 // 5
    out = []
    for i in range(count):
        bit = i * 5
        byte, shift = divmod(bit, 8)
        lo = data[byte]
        hi = data[byte + 1] if byte + 1 < len(data) else 0
        out.append((((hi << 8) | lo) >> shift & 0x1F) / 31.0)
    return out


def encode_waveform(samples: list[float]) -> bytes:
    """Inverse of :func:`decode_waveform` (used by the synthetic dataset)."""
    nbits = len(samples) * 5
    buf = bytearray((nbits + 7) // 8 + 1)
    for i, s in enumerate(samples):
        v = max(0, min(31, round(s * 31)))
        bit = i * 5
        byte, shift = divmod(bit, 8)
        word = v << shift
        buf[byte] |= word & 0xFF
        buf[byte + 1] |= (word >> 8) & 0xFF
    return bytes(buf[: (nbits + 7) // 8])


# --------------------------------------------------------------- media areas
def media_area_selector(x: float, y: float, w: float, h: float, rotation: float = 0.0) -> dict:
    """mediaAreaCoordinates (percent, x/y = centre) -> W3C selectors.

    Unrotated areas map to ``xywh=percent:(x-w/2),(y-h/2),w,h``; rotation needs
    an SvgSelector alongside the FragmentSelector.  Percent coordinates remain
    valid across Telegram's resized photo variants.
    """
    left, top = x - w / 2, y - h / 2
    sel = {"FragmentSelector": f"xywh=percent:{left:.2f},{top:.2f},{w:.2f},{h:.2f}"}
    if rotation:
        sel["SvgSelector"] = (
            f'<svg viewBox="0 0 100 100"><rect x="{left:.2f}" y="{top:.2f}" width="{w:.2f}" '
            f'height="{h:.2f}" transform="rotate({rotation:.1f} {x:.2f} {y:.2f})"/></svg>')
    return sel


def ttl_props(ttl_seconds: int | None, spoiler: bool | None) -> dict:
    """Spoiler/TTL belong to the attachment (occurrence), not to the file."""
    p = {}
    if spoiler is not None:
        p["spoiler"] = bool(spoiler)
    if ttl_seconds:
        p["ttlSeconds"] = int(ttl_seconds)
        p["viewOnce"] = ttl_seconds == TTL_VIEW_ONCE
    return p
