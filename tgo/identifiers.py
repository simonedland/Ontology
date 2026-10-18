"""Checksum-validated identifier extraction (wallets, IBANs, phones, URLs, handles).

Only identifiers that pass a checksum get high confidence; format-only matches
are kept with low confidence and ``validation='format_only'`` so the analyst
can tell them apart.  Pure Python, no dependencies (``phonenumbers`` is used
when installed).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class Found:
    """An identifier found in text: kind, TGO label, normalised value, span, validation and confidence."""
    kind: str        # key namespace: btc, eth, tron, iban, phone, url, domain, email, tg
    label: str       # TGO class: CryptoAddress, IBAN, PhoneNumber, Url, Domain, Email, TelegramHandle
    value: str       # normalized value
    raw: str
    start: int
    end: int
    validation: str  # e.g. base58check, eip55, iban-mod97, format_only
    conf: float
    extra: tuple = ()


# ------------------------------------------------------------------ keccak
_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
        [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M64 = (1 << 64) - 1


def _rol(v: int, n: int) -> int:
    return ((v << n) | (v >> (64 - n))) & _M64 if n else v


def _keccak_f(s):
    for rc in _RC:
        c = [s[x][0] ^ s[x][1] ^ s[x][2] ^ s[x][3] ^ s[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        s = [[s[x][y] ^ d[x] for y in range(5)] for x in range(5)]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(s[x][y], _ROT[x][y])
        s = [[b[x][y] ^ (~b[(x + 1) % 5][y] & b[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        s[0][0] ^= rc
    return s


def keccak256(data: bytes) -> bytes:
    """Original Keccak-256 (Ethereum), not NIST SHA3-256."""
    rate = 136
    msg = bytearray(data) + b"\x01"
    while len(msg) % rate:
        msg.append(0)
    msg[-1] |= 0x80
    s = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        block = msg[off: off + rate]
        for i in range(rate // 8):
            x, y = i % 5, i // 5
            s[x][y] ^= int.from_bytes(block[8 * i: 8 * i + 8], "little")
        s = _keccak_f(s)
    out = b"".join(s[i % 5][i // 5].to_bytes(8, "little") for i in range(4))
    return out


def eip55(addr_hex40: str) -> str:
    """EIP-55 mixed-case checksum form of a 40-hex-digit Ethereum address."""
    lower = addr_hex40.lower()
    h = keccak256(lower.encode()).hex()
    return "0x" + "".join(ch.upper() if ch.isalpha() and int(h[i], 16) >= 8 else ch
                          for i, ch in enumerate(lower))


# ----------------------------------------------------------------- base58
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    """Base58 (Bitcoin alphabet) decode."""
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body


def b58encode(b: bytes) -> str:
    """Base58 (Bitcoin alphabet) encode."""
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(b) - len(b.lstrip(b"\x00"))
    return "1" * pad + out


def _dsha(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def b58check_encode(payload: bytes) -> str:
    """Base58Check-encode ``payload`` (appends the double-sha256 checksum)."""
    return b58encode(payload + _dsha(payload)[:4])


def b58check_version(s: str) -> int | None:
    """Return the version byte if ``s`` is a valid 25-byte Base58Check string."""
    try:
        raw = b58decode(s)
    except ValueError:
        return None
    if len(raw) != 25 or _dsha(raw[:-4])[:4] != raw[-4:]:
        return None
    return raw[0]


# ----------------------------------------------------------------- bech32
_BECH = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def bech32_valid(addr: str) -> str | None:
    """Return 'bech32'/'bech32m' if the checksum verifies."""
    a = addr.lower()
    if "1" not in a:
        return None
    hrp, data = a.rsplit("1", 1)
    if not data or any(c not in _BECH for c in data):
        return None
    vals = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + [_BECH.index(c) for c in data]
    pm = _polymod(vals)
    return {1: "bech32", 0x2BC830A3: "bech32m"}.get(pm)


# ------------------------------------------------------------------- IBAN
_IBAN_LEN = {
    "AD": 24, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24, "DE": 22, "DK": 18,
    "EE": 20, "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GE": 22, "GR": 27, "HR": 21, "HU": 28,
    "IE": 22, "IS": 26, "IT": 27, "LT": 20, "LU": 20, "LV": 21, "MD": 24, "MT": 31, "NL": 18,
    "NO": 15, "PL": 28, "PT": 25, "RO": 24, "RS": 22, "SE": 24, "SI": 19, "SK": 24, "TR": 26,
    "UA": 29, "AE": 23,
}


def iban_valid(iban: str) -> bool:
    """IBAN length (per country) + ISO 13616 mod-97 check."""
    s = iban.replace(" ", "").upper()
    exp = _IBAN_LEN.get(s[:2])
    if exp is not None and len(s) != exp:
        return False
    if not (15 <= len(s) <= 34):
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


# ------------------------------------------------------------------ regexes
_RE_ETH = re.compile(r"(?<![0-9A-Za-z])0x[0-9a-fA-F]{40}(?![0-9A-Za-z])")
_RE_B58 = re.compile(r"(?<![0-9A-Za-z])[13T][1-9A-HJ-NP-Za-km-z]{25,34}(?![0-9A-Za-z])")
_RE_BECH = re.compile(r"(?<![0-9A-Za-z])(?:bc1|tb1)[02-9ac-hj-np-z]{11,71}(?![0-9A-Za-z])", re.I)
_RE_IBAN = re.compile(r"(?<![0-9A-Z])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?(?![0-9A-Z])")
_RE_PHONE = re.compile(r"(?<![\w+])\+\d[\d \-()]{6,18}\d(?!\d)")
_RE_URL = re.compile(r"(?:https?://|(?<![\w.@])(?:www\.|t\.me/))[^\s<>\"'\]\)]+", re.I)
_RE_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_RE_HANDLE = re.compile(r"(?<![\w@/.])@([A-Za-z][A-Za-z0-9_]{4,31})(?![\w])")

_TRACKING = {"fbclid", "gclid", "yclid", "igshid", "si"}
_CC = {"1", "7", "20", "27", "30", "31", "32", "33", "34", "36", "39", "40", "41", "43", "44",
       "45", "46", "47", "48", "49", "351", "353", "354", "358", "359", "370", "371", "372",
       "373", "374", "375", "380", "381", "385", "386", "90", "91", "971", "972", "98", "992",
       "993", "994", "995", "996", "998", "86", "81", "82", "61", "55", "52"}


def canonical_url(url: str) -> str:
    """Canonical URL: https, lower-case host without www, tracking params and trailing slash removed."""
    u = url.rstrip(".,;:!?")
    if not re.match(r"https?://", u, re.I):
        u = "https://" + u
    p = urlsplit(u)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    q = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k.lower() not in _TRACKING]
    path = p.path.rstrip("/") or ""
    return urlunsplit(("https", host, path, urlencode(q), ""))


def _phone(raw: str) -> tuple[str, str, float] | None:
    digits = re.sub(r"\D", "", raw)
    if not 8 <= len(digits) <= 15:
        return None
    try:
        import phonenumbers  # type: ignore

        num = phonenumbers.parse("+" + digits, None)
        if phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164), "libphonenumber", 0.95
        return None
    except ImportError:
        if any(digits.startswith(cc) for cc in _CC):
            return "+" + digits, "format_only+country_code", 0.6
        return None


def extract(text: str) -> list[Found]:
    """Find identifiers in free text (message, OCR output, transcript, PDF text)."""
    if not text:
        return []
    out: list[Found] = []
    taken: list[tuple[int, int]] = []

    def add(f: Found):
        if any(not (f.end <= s or f.start >= e) for s, e in taken):
            return
        taken.append((f.start, f.end))
        out.append(f)

    for m in _RE_ETH.finditer(text):
        raw = m.group(0)
        body = raw[2:]
        if body.islower() or body.isupper():
            add(Found("eth", "CryptoAddress", "0x" + body.lower(), raw, m.start(), m.end(), "format_only", 0.7, ("ETH",)))
        elif eip55(body) == raw:
            add(Found("eth", "CryptoAddress", "0x" + body.lower(), raw, m.start(), m.end(), "eip55", 0.98, ("ETH",)))
    for m in _RE_BECH.finditer(text):
        kind = bech32_valid(m.group(0))
        if kind:
            add(Found("btc", "CryptoAddress", m.group(0).lower(), m.group(0), m.start(), m.end(), kind, 0.98, ("BTC",)))
    for m in _RE_B58.finditer(text):
        ver = b58check_version(m.group(0))
        if ver in (0x00, 0x05):
            add(Found("btc", "CryptoAddress", m.group(0), m.group(0), m.start(), m.end(), "base58check", 0.97, ("BTC",)))
        elif ver == 0x41:
            add(Found("tron", "CryptoAddress", m.group(0), m.group(0), m.start(), m.end(), "base58check", 0.97, ("TRON",)))
    for m in _RE_IBAN.finditer(text):
        if iban_valid(m.group(0)):
            v = m.group(0).replace(" ", "")
            add(Found("iban", "IBAN", v, m.group(0), m.start(), m.end(), "iban-mod97", 0.96, (v[:2],)))
    for m in _RE_EMAIL.finditer(text):
        add(Found("email", "Email", m.group(0).lower(), m.group(0), m.start(), m.end(), "format_only", 0.85))
    for m in _RE_URL.finditer(text):
        raw = m.group(0).rstrip(".,;:!?")
        cu = canonical_url(raw)
        host = urlsplit(cu).hostname or ""
        if host in ("t.me", "telegram.me"):
            parts = [p for p in urlsplit(cu).path.split("/") if p]
            if parts and parts[0] not in ("joinchat", "addstickers", "c", "+") and not parts[0].startswith("+"):
                add(Found("tg", "TelegramHandle", parts[0].lower(), raw, m.start(), m.start() + len(raw),
                          "format_only", 0.9, ("t.me link",) + tuple(parts[1:2])))
                continue
        add(Found("url", "Url", cu, raw, m.start(), m.start() + len(raw), "canonicalized", 0.9, (host,)))
    for m in _RE_PHONE.finditer(text):
        r = _phone(m.group(0))
        if r:
            add(Found("phone", "PhoneNumber", r[0], m.group(0), m.start(), m.end(), r[1], r[2]))
    for m in _RE_HANDLE.finditer(text):
        add(Found("tg", "TelegramHandle", m.group(1).lower(), m.group(0), m.start(), m.end(), "format_only", 0.8))
    return sorted(out, key=lambda f: f.start)
