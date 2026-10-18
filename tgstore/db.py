"""PostgreSQL access: one batched upsert path for writes, a handful of indexed queries for reads."""

from __future__ import annotations

import json
import os
import time
from importlib.resources import files
from itertools import islice

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .records import FIELDS, KEYS, TABLES, normalize

DEFAULT_URL = "postgresql://tgstore:tgstore@127.0.0.1:5432/tgstore"
PROFILE = ("username", "first_name", "last_name", "bio", "phone")  # changes are kept in user_history


class Store:
    def __init__(self, url: str | None = None, pool_size: int | None = None):
        url = url or os.environ.get("TGSTORE_DATABASE_URL", DEFAULT_URL)
        pool_size = pool_size or int(os.environ.get("TGSTORE_POOL", "8"))
        self.pool = ConnectionPool(url, min_size=1, max_size=pool_size, kwargs={"row_factory": dict_row, "autocommit": True}, open=True)
        self.pool.wait(timeout=60)

    def close(self) -> None:
        self.pool.close()

    def migrate(self) -> None:
        with self.pool.connection() as conn:
            conn.execute("SELECT pg_advisory_lock(7262)")  # several API replicas may start at once
            conn.execute(files("tgstore").joinpath("schema.sql").read_text())
            conn.execute("SELECT pg_advisory_unlock(7262)")

    # ---- writes -------------------------------------------------------------------------------------

    def ingest(self, records, batch: int = 5000) -> dict[str, int]:
        """Store any number of records, `batch` per transaction. Idempotent; safe to call concurrently.

        A RecordError in one batch leaves the batches before it stored.
        """
        total: dict[str, int] = {}
        it = iter(records)
        while chunk := list(islice(it, batch)):
            for k, v in self.ingest_batch(chunk).items():
                total[k] = total.get(k, 0) + v
        return total

    def ingest_batch(self, records) -> dict[str, int]:
        """Write one batch of records.

        Each statement is its own short transaction (the pool is in autocommit mode), so a row lock is
        held for one statement, not a whole batch: concurrent writers that share busy chats and users do
        not queue behind each other. Partial batches are harmless because every statement is idempotent.
        """
        rows = normalize(records)
        params = {kind: json.dumps(r, ensure_ascii=False) for kind, r in rows.items() if r}
        if "chat" in params:
            self._run(_INSERT_NEW["chat"], _UPDATE_CHANGED["chat"], param=params["chat"])
        if "user" in params:
            self._run(_HISTORY_SQL, _INSERT_NEW["user"], _UPDATE_CHANGED["user"], param=params["user"])
        if "message" in params:
            self._run(_STUBS, _UPSERT_MESSAGES, _SEEN_INSERT, _SEEN_UPDATE, param=params["message"])
        if "membership" in params:
            self._run(_INSERT_NEW["membership"], _UPDATE_CHANGED["membership"], param=params["membership"])
        return {TABLES[kind]: len(r) for kind, r in rows.items()}

    def _run(self, *statements: str, param: str) -> None:
        with self.pool.connection() as conn:
            for sql in statements:
                for attempt in range(6):
                    try:
                        conn.execute(sql, (param,))
                        break
                    except (psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure):
                        if attempt == 5:
                            raise
                        time.sleep(0.05 * 2**attempt)

    # ---- reads --------------------------------------------------------------------------------------

    def _all(self, sql: str, params=()) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def stats(self) -> dict[str, int]:
        """Row counts from the statistics collector: current within a second, unlike count(*) at millions."""
        rows = self._all(
            "SELECT relname, n_live_tup AS n FROM pg_stat_user_tables WHERE relname = ANY(%s)",
            (list(TABLES.values()) + ["user_history"],),
        )
        return {r["relname"]: r["n"] for r in rows}

    def users(self, *, username: str | None = None, after_id: int | None = None, limit: int = 1000) -> list[dict]:
        """All users by id (page with `after_id` = the last id of the previous page), or one username."""
        if username is not None:
            return self._all("SELECT * FROM users WHERE lower(username) = lower(%s)", (username.lstrip("@"),))
        if after_id is None:
            return self._all("SELECT * FROM users ORDER BY id LIMIT %s", (limit,))
        return self._all("SELECT * FROM users WHERE id > %s ORDER BY id LIMIT %s", (after_id, limit))

    def user(self, user_id: int) -> dict | None:
        found = self._all("SELECT * FROM users WHERE id = %s", (user_id,))
        if not found:
            return None
        out = found[0]
        out["chats"] = self._all(
            "SELECT m.chat_id, c.title, c.username, m.status, m.joined_at, m.first_seen, m.last_seen "
            "FROM memberships m LEFT JOIN chats c ON c.id = m.chat_id WHERE m.user_id = %s "
            "ORDER BY m.last_seen DESC NULLS LAST",
            (user_id,),
        )
        out["history"] = self._all(
            "SELECT replaced_at, username, first_name, last_name, bio, phone FROM user_history "
            "WHERE user_id = %s ORDER BY replaced_at",
            (user_id,),
        )
        return out

    def chat(self, chat_id: int) -> dict | None:
        found = self._all("SELECT * FROM chats WHERE id = %s", (chat_id,))
        return found[0] if found else None

    def chat_members(self, chat_id: int, limit: int = 1000, offset: int = 0) -> list[dict]:
        return self._all(
            "SELECT m.*, u.username, u.first_name, u.last_name FROM memberships m "
            "LEFT JOIN users u ON u.id = m.user_id WHERE m.chat_id = %s "
            "ORDER BY m.last_seen DESC NULLS LAST, m.user_id LIMIT %s OFFSET %s",
            (chat_id, limit, offset),
        )

    def messages(self, *, user_id: int, chat_id=None, before=None, limit: int = 100) -> list[dict]:
        """A user's messages, newest first (index messages_user_date). Page with `before` = oldest `date`."""
        where, params = ["user_id = %s"], [user_id]
        if chat_id is not None:
            where.append("chat_id = %s")
            params.append(chat_id)
        if before is not None:
            where.append("date < %s")
            params.append(before)
        sql = f"SELECT * FROM messages WHERE {' AND '.join(where)} ORDER BY date DESC LIMIT %s"
        return self._all(sql, (*params, limit))

    def chat_messages(self, chat_id: int, before_id=None, limit: int = 100) -> list[dict]:
        """A chat's messages, newest first by id (primary key). Page with `before_id` = smallest `id`."""
        if before_id is None:
            return self._all("SELECT * FROM messages WHERE chat_id = %s ORDER BY id DESC LIMIT %s", (chat_id, limit))
        return self._all(
            "SELECT * FROM messages WHERE chat_id = %s AND id < %s ORDER BY id DESC LIMIT %s",
            (chat_id, before_id, limit),
        )

    def search(self, q: str, *, chat_id=None, user_id=None, newest: bool = False, limit: int = 100) -> list[dict]:
        """Full-text search. Unordered by default, which stays fast however common the words are;
        `newest=True` sorts by date, which costs time in proportion to the number of matches."""
        where, params = ["to_tsvector('simple', coalesce(text, '')) @@ websearch_to_tsquery('simple', %s)"], [q]
        for col, val in (("user_id", user_id), ("chat_id", chat_id)):
            if val is not None:
                where.append(f"{col} = %s")
                params.append(val)
        order = " ORDER BY date DESC NULLS LAST" if newest else ""
        return self._all(f"SELECT * FROM messages WHERE {' AND '.join(where)}{order} LIMIT %s", (*params, limit))


# ---- SQL ----------------------------------------------------------------------------------------------
# Merge rule everywhere: a non-null incoming value wins, extra merges key by key, and a row is only
# written when that changes it (re-sent data then costs an index lookup, not a new row version).


def _parts(kind: str):
    table, keys = TABLES[kind], KEYS[kind]
    types = {c: t for c, (t, _) in FIELDS[kind].items()} | {"extra": "jsonb"}
    recordset = f"jsonb_to_recordset(%s::jsonb) AS r({', '.join(f'{c} {t}' for c, t in types.items())})"
    data = [c for c in FIELDS[kind] if c not in keys]
    return table, keys, ", ".join(types), recordset, data


def _insert_new(kind: str) -> str:
    """Insert rows that do not exist yet. DO NOTHING never locks existing rows."""
    table, keys, cols, recordset, _ = _parts(kind)
    return f"INSERT INTO {table} ({cols}) SELECT {cols} FROM {recordset} ON CONFLICT ({', '.join(keys)}) DO NOTHING"


def _update_changed(kind: str) -> str:
    """Apply the merge rule to existing rows, touching (and locking) only rows that actually change."""
    table, keys, _, recordset, data = _parts(kind)
    new = [f"COALESCE(r.{c}, t.{c})" for c in data] + ["t.extra || r.extra"]
    old = [f"t.{c}" for c in data] + ["t.extra"]
    sets = [f"{c} = COALESCE(r.{c}, t.{c})" for c in data] + ["extra = t.extra || r.extra"]
    if table in ("chats", "users"):
        sets.append("updated_at = now()")
    match = " AND ".join(f"t.{k} = r.{k}" for k in keys)
    return (
        f"UPDATE {table} AS t SET {', '.join(sets)} FROM {recordset} "
        f"WHERE {match} AND ({', '.join(new)}) IS DISTINCT FROM ({', '.join(old)})"
    )


_INSERT_NEW = {k: _insert_new(k) for k in ("chat", "user", "membership")}
_UPDATE_CHANGED = {k: _update_changed(k) for k in ("chat", "user", "membership")}


def _upsert_messages() -> str:
    """Messages in one statement: almost all are new, and two batches rarely carry the same message."""
    table, keys, cols, recordset, data = _parts("message")
    new = [f"COALESCE(EXCLUDED.{c}, t.{c})" for c in data] + ["t.extra || EXCLUDED.extra"]
    old = [f"t.{c}" for c in data] + ["t.extra"]
    sets = [f"{c} = COALESCE(EXCLUDED.{c}, t.{c})" for c in data] + ["extra = t.extra || EXCLUDED.extra"]
    return (
        f"INSERT INTO {table} AS t ({cols}) SELECT {cols} FROM {recordset} "
        f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {', '.join(sets)} "
        f"WHERE ({', '.join(new)}) IS DISTINCT FROM ({', '.join(old)})"
    )


_UPSERT_MESSAGES = _upsert_messages()

# Before users are written: keep the current profile of every user whose profile is about to change.
_HISTORY_SQL = (
    f"INSERT INTO user_history (user_id, {', '.join(PROFILE)}) "
    f"SELECT u.id, {', '.join('u.' + c for c in PROFILE)} "
    f"FROM jsonb_to_recordset(%s::jsonb) AS r(id bigint, {', '.join(c + ' text' for c in PROFILE)}) "
    f"JOIN users u ON u.id = r.id "
    f"WHERE num_nonnulls({', '.join('u.' + c for c in PROFILE)}) > 0 AND ({', '.join(f'COALESCE(r.{c}, u.{c})' for c in PROFILE)}) "
    f"IS DISTINCT FROM ({', '.join('u.' + c for c in PROFILE)})"
)

# Every chat and sender a message mentions gets a row (id only until a chat/user record arrives), so
# walking users or chats finds everyone. DO NOTHING: never locks or rewrites existing rows.
_STUBS = (
    "WITH r AS (SELECT * FROM jsonb_to_recordset(%s::jsonb) AS r(chat_id bigint, user_id bigint)), "
    "c AS (INSERT INTO chats (id) SELECT DISTINCT chat_id FROM r ORDER BY 1 ON CONFLICT DO NOTHING) "
    "INSERT INTO users (id) SELECT DISTINCT user_id FROM r WHERE user_id IS NOT NULL ORDER BY 1 ON CONFLICT DO NOTHING"
)

# Every message proves its sender was in the chat: keep first/last message seen per (chat, user).
_SEEN = (
    "(SELECT chat_id, user_id, min(date) AS first_seen, max(date) AS last_seen "
    "FROM jsonb_to_recordset(%s::jsonb) AS r(chat_id bigint, user_id bigint, date timestamptz) "
    "WHERE user_id IS NOT NULL GROUP BY chat_id, user_id ORDER BY chat_id, user_id) AS s"
)
_SEEN_INSERT = (
    f"INSERT INTO memberships (chat_id, user_id, first_seen, last_seen) SELECT * FROM {_SEEN} "
    "ON CONFLICT (chat_id, user_id) DO NOTHING"
)
_SEEN_UPDATE = (
    f"UPDATE memberships AS t SET first_seen = LEAST(t.first_seen, s.first_seen), "
    f"last_seen = GREATEST(t.last_seen, s.last_seen) FROM {_SEEN} "
    "WHERE t.chat_id = s.chat_id AND t.user_id = s.user_id "
    "AND (LEAST(t.first_seen, s.first_seen), GREATEST(t.last_seen, s.last_seen)) IS DISTINCT FROM (t.first_seen, t.last_seen)"
)
