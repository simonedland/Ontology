"""HTTP API. Run with `python -m tgstore serve` (or `uvicorn tgstore.api:app`). Interactive docs at /docs."""

from __future__ import annotations

import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import Store
from .records import RecordError

TOKEN = os.environ.get("TGSTORE_TOKEN") or None
store: Store


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    store = Store()
    store.migrate()
    yield
    store.close()


app = FastAPI(
    title="tgstore",
    version="0.1.0",
    lifespan=lifespan,
    description="Storage for Telegram chats, users, messages and memberships. Record format: docs/records.md",
)


def auth(cred: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False))) -> None:
    if TOKEN and not (cred and secrets.compare_digest(cred.credentials, TOKEN)):
        raise HTTPException(401, "missing or wrong bearer token")


def parse_body(raw: bytes) -> list:
    """NDJSON, a JSON array, a single record, or {"records": [...]}."""
    text = raw.decode("utf-8-sig").strip()
    if not text:
        return []
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        try:
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"body is neither JSON nor NDJSON: line {e.lineno}: {e.msg}") from None
    if isinstance(doc, dict) and "kind" not in doc and isinstance(doc.get("records"), list):
        return doc["records"]
    return doc if isinstance(doc, list) else [doc]


async def _ingest(records) -> dict[str, int]:
    try:
        return await run_in_threadpool(store.ingest, records)
    except RecordError as e:
        raise HTTPException(422, f"{e} (earlier batches of this request were stored)") from None


# ---- write ---------------------------------------------------------------------------------------------


@app.post("/v1/records", dependencies=[Depends(auth)], summary="Ingest records (NDJSON, JSON array or object)")
async def post_records(request: Request) -> dict[str, int]:
    return await _ingest(parse_body(await request.body()))


@app.post("/v1/import/telegram-desktop", dependencies=[Depends(auth)], summary="Ingest a Telegram Desktop result.json")
async def post_desktop_export(request: Request) -> dict[str, int]:
    from .sources.desktop import records_from_export

    try:
        doc = json.loads(await request.body())
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"not JSON: {e}") from None
    return await _ingest(records_from_export(doc))


# ---- read ----------------------------------------------------------------------------------------------


@app.get("/v1/health")
def health() -> dict:
    store.stats()
    return {"ok": True}


@app.get("/v1/stats", dependencies=[Depends(auth)], summary="Approximate row counts per table")
def stats() -> dict[str, int]:
    return store.stats()


@app.get("/v1/users", dependencies=[Depends(auth)], summary="List users by id, or find one by username")
def list_users(
    username: str | None = None,
    after: int | None = Query(None, description="page: the last `id` of the previous page"),
    limit: int = Query(1000, ge=1, le=100000),
) -> list[dict]:
    return store.users(username=username, after_id=after, limit=limit)


@app.get("/v1/users/{user_id}", dependencies=[Depends(auth)], summary="Profile, chats and profile history")
def get_user(user_id: int) -> dict:
    if (u := store.user(user_id)) is None:
        raise HTTPException(404, "unknown user")
    return u


@app.get("/v1/users/{user_id}/messages", dependencies=[Depends(auth)], summary="A user's messages, newest first")
def get_user_messages(
    user_id: int,
    chat_id: int | None = None,
    before: datetime | None = Query(None, description="page: the oldest `date` of the previous page"),
    limit: int = Query(100, ge=1, le=10000),
) -> list[dict]:
    return store.messages(user_id=user_id, chat_id=chat_id, before=before, limit=limit)


@app.get("/v1/chats/{chat_id}", dependencies=[Depends(auth)])
def get_chat(chat_id: int) -> dict:
    if (c := store.chat(chat_id)) is None:
        raise HTTPException(404, "unknown chat")
    return c


@app.get("/v1/chats/{chat_id}/members", dependencies=[Depends(auth)])
def get_chat_members(chat_id: int, limit: int = Query(1000, ge=1, le=100000), offset: int = 0) -> list[dict]:
    return store.chat_members(chat_id, limit, offset)


@app.get("/v1/chats/{chat_id}/messages", dependencies=[Depends(auth)], summary="A chat's messages, newest first")
def get_chat_messages(
    chat_id: int,
    before: int | None = Query(None, description="page: the smallest message `id` of the previous page"),
    limit: int = Query(100, ge=1, le=10000),
) -> list[dict]:
    return store.chat_messages(chat_id, before_id=before, limit=limit)


@app.get("/v1/search", dependencies=[Depends(auth)], summary="Full-text search over message text")
def search(
    q: str,
    chat_id: int | None = None,
    user_id: int | None = None,
    newest: bool = Query(False, description="sort by date (slower for very common words)"),
    limit: int = Query(100, ge=1, le=10000),
) -> list[dict]:
    return store.search(q, chat_id=chat_id, user_id=user_id, newest=newest, limit=limit)
