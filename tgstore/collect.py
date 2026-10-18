"""Collect Telegram group chats with a user account (Telethon) and push them to the tgstore API.

    python -m tgstore collect --chat @group --chat -1001234567890 --members --backfill 1000 --follow

``--members`` stores each chat's member list (users + creator/admin/member/... status; channels and some
big groups need admin rights). ``--backfill N`` stores the newest N messages per chat (``all`` for full
history), then ``--follow`` keeps listening for new messages, edits and joins/leaves, flushing to the API
about once a second.

Env: TG_API_ID, TG_API_HASH (my.telegram.org), TG_SESSION (session file path, or a StringSession
string; default ``tgstore``), TGSTORE_URL, TGSTORE_TOKEN. The first run asks for a login code, so in
compose run it once interactively: ``docker compose run --rm collect --chat @group``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from telethon import TelegramClient, errors, events
from telethon.sessions import StringSession

from tgstore.client import DEFAULT_URL, push
from tgstore.sources import telethon as tg

log = logging.getLogger("tgstore.collect")


class Outbox:
    """Buffers records and sends them in a worker thread so the event loop never blocks on HTTP."""

    def __init__(self, url: str, token: str | None, batch: int = 500):
        self.url, self.token, self.batch, self.records = url, token, batch, []

    async def add(self, *records: dict) -> None:
        self.records.extend(records)
        if len(self.records) >= self.batch:
            await self.flush()

    async def flush(self) -> None:
        if self.records:
            batch, self.records = self.records, []
            counts = await asyncio.to_thread(push, batch, self.url, self.token)
            log.info("pushed %s", counts)

    async def flush_forever(self, every: float = 1.0) -> None:
        while True:
            await asyncio.sleep(every)
            try:
                await self.flush()
            except Exception:   # API down: client.post already retried; keep collecting
                log.exception("push failed, records dropped")


def _session(value: str):
    return StringSession(value) if len(value) > 100 else value   # string sessions are ~350 chars


def _limit(value: str) -> int | None:
    return None if value == "all" else int(value)


async def run(args: argparse.Namespace) -> None:
    client = TelegramClient(_session(os.environ.get("TG_SESSION", "tgstore")),
                            int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    out = Outbox(args.url, args.token)
    await client.start()
    chats = [await client.get_entity(int(c) if c.lstrip("-").isdigit() else c) for c in args.chat]

    for chat in chats:
        rec = tg.chat_record(chat)
        cid = rec["id"]
        await out.add(rec)
        if args.members:
            try:
                async for u in client.iter_participants(chat):
                    await out.add(*tg.participant_records(cid, u))
            except errors.ChatAdminRequiredError:
                log.warning("member list of %s needs admin rights; skipped", getattr(chat, "title", cid))
        if args.backfill != 0:
            async for m in client.iter_messages(chat, limit=args.backfill):
                await out.add(*tg.records(m, m.sender))
        await out.flush()
        log.info("backfilled %s", getattr(chat, "title", chat.id))

    if not args.follow:
        await client.disconnect()
        return

    async def on_message(event):
        await out.add(*tg.records(event.message, await event.get_sender()))

    async def on_action(event):
        await out.add(*tg.action_records(event))

    client.add_event_handler(on_message, events.NewMessage(chats=chats))
    client.add_event_handler(on_message, events.MessageEdited(chats=chats))
    client.add_event_handler(on_action, events.ChatAction(chats=chats))
    flusher = asyncio.create_task(out.flush_forever())
    log.info("following %d chat(s)", len(chats))
    try:
        await client.run_until_disconnected()
    finally:
        flusher.cancel()
        await out.flush()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="tgstore collect", description=__doc__.split("\n")[0])
    p.add_argument("--chat", action="append", required=True, help="@username, t.me link or numeric id (repeatable)")
    p.add_argument("--members", action="store_true", help="store each chat's member list with admin/creator status")
    p.add_argument("--backfill", type=_limit, default=0, metavar="N", help="newest N messages per chat, or 'all'")
    p.add_argument("--follow", action="store_true", help="keep listening for new messages, edits and joins")
    p.add_argument("--url", default=DEFAULT_URL, help="tgstore API (default: $TGSTORE_URL)")
    p.add_argument("--token", default=os.environ.get("TGSTORE_TOKEN"), help="API token (default: $TGSTORE_TOKEN)")
    args = p.parse_args(argv)
    if args.backfill == 0 and not (args.follow or args.members):
        p.error("nothing to do: give --members, --backfill N and/or --follow")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
