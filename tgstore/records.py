"""Turn incoming records (docs/records.md) into clean rows, one list per table.

To add a column: add it to schema.sql and to FIELDS below. Everything else follows.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime


def _int(v):
    return None if v is None or v == "" else int(v)


def _str(v):
    return None if v is None else str(v)


def _bool(v):
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "t")
    return bool(v)


def _date(v):
    """ISO 8601 string or Unix seconds -> ISO 8601 string in UTC (naive input is taken as UTC)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, UTC).isoformat()
    d = datetime.fromisoformat(str(v))
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).isoformat()


# kind -> {field: (sql type, coercer)}; the key fields come first.
FIELDS: dict[str, dict[str, tuple[str, callable]]] = {
    "chat": {
        "id": ("bigint", _int),
        "type": ("text", _str),
        "title": ("text", _str),
        "username": ("text", _str),
        "about": ("text", _str),
        "member_count": ("integer", _int),
    },
    "user": {
        "id": ("bigint", _int),
        "username": ("text", _str),
        "first_name": ("text", _str),
        "last_name": ("text", _str),
        "phone": ("text", _str),
        "bio": ("text", _str),
        "lang_code": ("text", _str),
        "is_bot": ("boolean", _bool),
    },
    "message": {
        "chat_id": ("bigint", _int),
        "id": ("bigint", _int),
        "user_id": ("bigint", _int),
        "date": ("timestamptz", _date),
        "text": ("text", _str),
        "reply_to_id": ("bigint", _int),
        "forward_from_user_id": ("bigint", _int),
        "forward_from_chat_id": ("bigint", _int),
        "edit_date": ("timestamptz", _date),
        "media_type": ("text", _str),
        "views": ("integer", _int),
    },
    "membership": {
        "chat_id": ("bigint", _int),
        "user_id": ("bigint", _int),
        "status": ("text", _str),
        "joined_at": ("timestamptz", _date),
    },
}
KEYS = {"chat": ("id",), "user": ("id",), "message": ("chat_id", "id"), "membership": ("chat_id", "user_id")}
TABLES = {"chat": "chats", "user": "users", "message": "messages", "membership": "memberships"}


class RecordError(ValueError):
    pass


def _row(kind: str, rec: dict) -> dict:
    fields = FIELDS[kind]
    row, extra = {}, dict(rec.get("extra") or {})
    for k, v in rec.items():
        if k in fields:
            row[k] = fields[k][1](v)
        elif k not in ("kind", "extra", "user", "chat"):
            extra[k] = v
    for k in KEYS[kind]:
        if row.get(k) is None:
            raise RecordError(f"{kind} record needs {k!r}")
    row["extra"] = extra
    return row


def _merge(old: dict, new: dict) -> dict:
    """Later non-null values win; extra merges key by key (same rule the database applies)."""
    out = dict(old)
    for k, v in new.items():
        if k == "extra":
            out["extra"] = {**old.get("extra", {}), **v}
        elif v is not None:
            out[k] = v
    return out


def normalize(records: Iterable[dict]) -> dict[str, list[dict]]:
    """Validate, split by kind, unpack embedded user/chat, merge duplicates, sort by key.

    Rows are deduplicated per batch because one INSERT ... ON CONFLICT cannot touch a row twice, and
    sorted so concurrent batches lock rows in the same order (no deadlocks).
    """
    rows: dict[str, dict[tuple, dict]] = {k: {} for k in FIELDS}

    def add(kind: str, rec: dict) -> None:
        row = _row(kind, rec)
        key = tuple(row[k] for k in KEYS[kind])
        bucket = rows[kind]
        bucket[key] = _merge(bucket[key], row) if key in bucket else row

    for i, rec in enumerate(records):
        try:
            if not isinstance(rec, dict):
                raise RecordError("record must be a JSON object")
            kind = rec.get("kind")
            if kind not in FIELDS:
                raise RecordError(f"unknown kind {kind!r} (expected one of {', '.join(FIELDS)})")
            if kind == "message":
                if isinstance(rec.get("user"), dict):
                    add("user", {"id": rec.get("user_id"), **rec["user"]})
                if isinstance(rec.get("chat"), dict):
                    add("chat", {"id": rec.get("chat_id"), **rec["chat"]})
            add(kind, rec)
        except (RecordError, ValueError, TypeError) as e:
            raise RecordError(f"record {i}: {e}") from None
    return {kind: [bucket[k] for k in sorted(bucket)] for kind, bucket in rows.items()}
