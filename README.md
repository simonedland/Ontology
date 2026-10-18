# tgstore

Simple, scalable storage for Telegram group-chat data: **chats, users, messages and memberships**.
PostgreSQL holds the data; a small HTTP API and a drop folder take data in from anywhere outside the
containers. Downstream tools read it over HTTP or plain SQL.

```text
 Telegram collector (Telethon) ──┐
 Telegram Desktop export ────────┤                                  ┌─► HTTP reads  (/v1/users/{id}, ...)
 your own scripts (curl, Python) ┼─► POST /v1/records ─┐            │
 files dropped into ./inbox ─────┘   (api)             ├─► PostgreSQL ─► SQL on :5432 (pandas, DuckDB, BI ...)
 bulk SQL (COPY, INSERT) ───────────────────────────────┘
```

## Quick start

```bash
docker compose up -d                 # db + api + inbox; API docs at http://127.0.0.1:8080/docs
curl -X POST http://127.0.0.1:8080/v1/records -H 'Content-Type: application/x-ndjson' --data-binary @- <<'EOF'
{"kind": "chat", "id": -1001234567890, "type": "supergroup", "title": "Example group"}
{"kind": "message", "chat_id": -1001234567890, "id": 1, "user_id": 42, "date": "2026-10-18T12:00:00Z", "text": "hi", "user": {"username": "alice"}}
EOF
curl http://127.0.0.1:8080/v1/users/42             # profile + chats + profile history
curl http://127.0.0.1:8080/v1/users/42/messages    # everything the user wrote, newest first
```

## Getting data in

All paths take the same [record format](docs/records.md): JSON objects with a `kind` of `chat`, `user`,
`message` or `membership`. Unknown fields are kept (in `extra`), and ingest is idempotent, so you can
re-send anything at any time.

| From | How |
|---|---|
| any program | `POST /v1/records` with NDJSON, a JSON array or one object |
| Python, without installing anything | copy [`tgstore/client.py`](tgstore/client.py) (standard library only): `push(records, url=...)` |
| a shell | `python tgstore/client.py records.ndjson` or `tgstore push file.ndjson` |
| files | drop `*.ndjson`, `*.json` or a Telegram Desktop `result.json` into `./inbox`; it moves to `inbox/done` (or `inbox/failed` with the error). Write as `*.tmp` and rename when complete. |
| Telegram Desktop export | `POST /v1/import/telegram-desktop` with `result.json`, or `tgstore import-desktop result.json` |
| Telegram, live | `docker compose --profile collect run --rm collect --chat @group --backfill 1000 --follow` (see below) |
| SQL | connect to `postgresql://tgstore:tgstore@127.0.0.1:5432/tgstore` and write to the tables directly |

### Live collection from Telegram

Get an API id and hash at https://my.telegram.org and put them in `.env` (`TG_API_ID`, `TG_API_HASH`).
The first run asks for your phone number and login code; the session is then kept in `./session`.

```bash
docker compose --profile collect run --rm collect --chat @group1 --chat @group2 --backfill all --follow
```

The collector can also run on the host: `pip install ".[collect]"`, then
`TGSTORE_URL=http://127.0.0.1:8080 python -m tgstore collect --chat @group --follow`.

## Reading data

| Endpoint | Returns |
|---|---|
| `GET /v1/users?username=&after=&limit=` | find a user by @username, or walk all users by id |
| `GET /v1/users/{id}` | profile, every chat the user is in (with first/last message seen), profile history |
| `GET /v1/users/{id}/messages?chat_id=&before=&limit=` | the user's messages, newest first; page with `before` = oldest `date` |
| `GET /v1/chats/{id}` · `/members` · `/messages?before=&limit=` | chat info, members, timeline (page with `before` = smallest `id`) |
| `GET /v1/search?q=&chat_id=&user_id=&newest=` | full-text search over message text (`newest=true` sorts by date) |
| `GET /v1/stats` | approximate row counts |

For analysis, SQL is usually simplest:

```python
import pandas as pd
df = pd.read_sql("SELECT * FROM messages WHERE user_id = 42", "postgresql://tgstore:tgstore@127.0.0.1:5432/tgstore")
```

Tables: `chats`, `users`, `user_history` (old profile values), `messages`, `memberships`.
See [`tgstore/schema.sql`](tgstore/schema.sql).

## Scale

Writes are batched (5,000 records per transaction, as one `INSERT ... SELECT FROM jsonb_to_recordset`
per table) and skip rows that would not change, so re-sent data is nearly free. Reads use indexes
only: a chat's timeline is the primary key, a user's messages are `(user_id, date)`, search is a GIN
index. Neither depends on table size.

Measured on one desktop (6 cores, 16 GB, Docker Desktop) with `scripts/bench.py`: **10 million
messages** ingested over HTTP at ~55,000 messages/s, as fast at the 10th million as at the first, with
reads at 2–7 ms p95 throughout. Details and tuning: [docs/scaling.md](docs/scaling.md).

## Adapting it

- **New field?** Just send it: it lands in `extra` (JSONB, queryable: `extra->>'field'`). To promote
  it to a real column, add it to `tgstore/schema.sql` (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) and to
  `FIELDS` in `tgstore/records.py`.
- **New source?** Write a function that yields records and pass it to `client.push`. See
  `tgstore/sources/` for the Telegram Desktop and Telethon converters.
- **Expose it on a network?** Set `TGSTORE_TOKEN`, `POSTGRES_PASSWORD` and `TGSTORE_BIND` in `.env`.

## Development

```bash
pip install -e ".[collect]" pytest httpx
docker compose up -d db
pytest                                     # database tests use TGSTORE_DATABASE_URL (default: the compose db)
```

The earlier ontology and analysis toolkit (TGO) lives unchanged in [`legacy/tgo/`](legacy/tgo).
