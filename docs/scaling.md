# Scaling

The goal is that ingest speed and read latency stay the same as the tables grow. This page shows
how we measure that, the results, and what keeps it that way.

## Setup

- **Machine:** AMD Ryzen 5 7600 (6 cores / 12 threads), 16 GB RAM, Windows 11 + Docker Desktop (WSL2).
  The database, the API and the benchmark client all run on this one box.
- **Stack:** `docker compose up -d` with the defaults: Postgres 17 (`shared_buffers=1GB`,
  `synchronous_commit=off`) and the API with 4 uvicorn workers (`TGSTORE_WORKERS`).
- **Load:** `python scripts/bench.py --workers 8` with `--messages 6000000` (before) and
  `--messages 10000000` (after) drives 8 concurrent HTTP
  connections with 5,000 messages per `POST /v1/records`. On top of the messages it sends ~3% partial
  edits, ~2% exact re-sends (half of them inside the same batch), ~0.5% membership records and ~3%
  embedded user/chat profiles. Activity is skewed: the busiest chat has ~12% of all messages,
  each chat has a few heavy posters, and words follow a Zipf distribution. Data is synthetic and
  deterministic (same `--seed`, same records).
- **Measurement:** after every 1M messages the client waits for in-flight batches to finish, then
  times 40 samples of each read endpoint (ids that exist; 50 rows per page). Read latency is measured
  with ingest paused. The ingest rate includes the HTTP layer and the JSON parsing.

## Results

Ingest is msg/s per 1M segment. Every other column is the 95th percentile in ms.

**Before (6M messages).** One transaction per batch, and `/v1/search` always sorted by date:

| messages | ingest msg/s | POST p95 | chat timeline | chat page 2 | user messages | user profile | search rare | search common |
|---|---|---|---|---|---|---|---|---|
| 1M | 29,025 | 3,274 | 3.1 | 2.7 | 2.8 | 4.4 | 8.0 | 56 |
| 2M | 24,913 | 4,374 | 2.8 | 2.6 | 2.9 | 4.7 | 22.1 | 186 |
| 3M | 24,345 | 4,038 | 3.5 | 3.1 | 3.1 | 4.5 | 24.3 | 233 |
| 4M | 23,800 | 4,270 | 2.7 | 2.8 | 2.7 | 4.4 | 22.5 | 247 |
| 5M | 21,248 | 4,942 | 2.8 | 2.6 | 2.9 | 5.0 | 24.5 | 400 |
| 6M | 30,868 | 3,109 | 3.0 | 2.5 | 3.1 | 5.0 | 22.8 | 537 |

No ingest errors. Ingest showed no steady decline from 1M to 6M (it varied between 21k and 31k msg/s),
and the indexed reads stayed at 2.5–5 ms (p95). The one thing that grew with the table was search for a
common word sorted by date: Postgres must fetch and sort every match (3.5M rows for the most common
word at 6M).

While this ran, most writers were waiting on `Lock:transactionid` on the `chats` / `memberships`
upserts. Every batch touches the same hot rows (busy chats, their heavy posters' `last_seen`), and
`ON CONFLICT DO UPDATE` locks each conflicting row even when its `WHERE` skips the update. Those locks
were held until the whole batch committed, so concurrent batches mostly ran one at a time.

**After (10M messages).** What changed:
- statement-level transactions, with lock-light upserts for chats, users and memberships;
- search is unordered by default (`newest=true` to sort);
- every message also inserts a stub `users` / `chats` row (`DO NOTHING`), so `/v1/users/{id}` works
  for every sender;
- `user_history` skips stubs whose profile is empty;
- `/v1/stats` reads `n_live_tup` (live, no waiting for `ANALYZE`);
- `jit=off`.

Same command, but with `--messages 10000000`, so the run has 500k users (the default is messages / 20)
instead of 300k. "Common, newest" is the same query as the "search common" column in the table
above.

| messages | ingest msg/s | POST p95 | chat timeline | chat page 2 | user messages | user profile | search rare | search common | common, newest |
|---|---|---|---|---|---|---|---|---|---|
| 1M | 59,926 | 1,092 | 2.4 | 2.3 | 2.5 | 3.0 | 8.9 | 6.9 | 69 |
| 2M | 59,858 | 1,176 | 3.1 | 2.9 | 3.6 | 6.5 | 3.4 | 7.1 | 162 |
| 3M | 51,873 | 1,363 | 2.5 | 2.3 | 2.5 | 3.4 | 4.8 | 5.9 | 250 |
| 4M | 51,555 | 1,221 | 2.6 | 2.6 | 2.7 | 3.8 | 3.0 | 5.8 | 269 |
| 5M | 47,546 | 1,756 | 2.5 | 2.5 | 2.6 | 3.3 | 2.6 | 5.4 | 406 |
| 6M | 54,142 | 1,246 | 2.6 | 2.5 | 2.6 | 4.2 | 4.5 | 6.2 | 531 |
| 7M | 51,294 | 1,423 | 2.4 | 2.3 | 2.4 | 3.8 | 4.0 | 6.3 | 513 |
| 8M | 55,664 | 1,234 | 2.5 | 2.6 | 2.5 | 4.3 | 3.3 | 5.5 | 655 |
| 9M | 58,541 | 1,127 | 2.4 | 2.3 | 2.5 | 3.6 | 3.5 | 6.0 | 667 |
| 10M | 57,275 | 1,121 | 2.5 | 2.8 | 2.4 | 5.1 | 3.7 | 6.2 | 859 |

No ingest errors (10M messages plus 300k edits, 201k re-sends and 49k memberships). Ingest is about
twice the baseline (55k msg/s median) and does not decline: the last segment ran at 0.96x the first.
POST p95 dropped from 3–5 s to 1.1–1.8 s. Every indexed read, and the default (unordered) search,
stays at 2.3–9 ms p95 up to 10M. Only `newest=true` on a very common word still grows with the
table, as expected: it sorts every match.

Size: 2.2 GB at 6M messages (baseline; the full-text GIN index is 266 MB of it), 3.7 GB at 10M
(after).

## What keeps it flat

- **Keyset paging on indexes.** A chat timeline is the primary key `(chat_id, id)` read backwards and
  paged with `before=<smallest id>`. A user's messages use `(user_id, date DESC)`, paged with
  `before=<oldest date>`. Each page costs a few index pages, however big the table is. There is no
  `OFFSET`.
- **Search is unordered by default.** The GIN index returns the first `limit` matches without
  touching the rest. `newest=true` has to sort every match, so its cost grows with how common the
  word is. Add `chat_id` or `user_id` to keep it cheap.
- **No-op updates are skipped.** Every upsert has `WHERE (new values) IS DISTINCT FROM (old values)`.
  A re-sent record costs one index lookup, not a new row version, so duplicates create no table or
  index bloat and no extra vacuum work.
- **Short transactions.** Each table's statement commits on its own. Chats, users and memberships
  use `INSERT ... ON CONFLICT DO NOTHING` followed by `UPDATE ... WHERE changed`, so rows that don't
  change are never locked and hot rows are not held for the whole batch. Ingest is idempotent, so a
  batch that fails half way is simply re-sent.
- **Batches are deduplicated and sorted** before they reach the database. This avoids "cannot affect
  row a second time" errors and deadlocks between concurrent batches.

Do not add a plain index on `messages(date)` to speed up `newest=true`. We tested it at 6M: common
words got faster (690 → 0.5 ms), but rare and absent words went from 3 ms to 3.5 s. The planner bets
on walking the date index and loses whenever the word is rarer than its estimate.

## Tuning knobs

| knob | default | effect |
|---|---|---|
| `TGSTORE_WORKERS` | 4 | API processes. The JSON parsing and normalising is CPU-bound in Python: raise this toward the number of cores if `docker stats` shows the api container near 100%. |
| `TGSTORE_POOL` | 8 | database connections per API process. |
| `PG_SHARED_BUFFERS` | 1GB | Postgres cache. ~25% of the RAM given to the db. Keep the hot indexes (`messages_pkey`, `messages_user_date`, `messages_fts`) in memory. |
| `PG_EFFECTIVE_CACHE` | 3GB | planner hint: roughly the RAM left for the OS page cache. |
| `PG_SYNCHRONOUS_COMMIT` | off | `off` acknowledges before the WAL flush: a crash may lose the last ~0.5 s, but re-sending is harmless. Set `on` if every acknowledged write must survive a crash, at a lower commit rate. |
| client `batch` | 5,000 | records per POST (`client.push(..., batch=)`, `bench.py --batch`). Larger batches mean fewer round trips but longer transactions. 1k–10k is the useful range. |
| client concurrency | — | Run several pushers in parallel (`bench.py --workers`). Throughput rises until the API or Postgres CPU saturates. |

## Reproduce

```bash
docker compose down -v && docker compose up -d --build
python scripts/bench.py --messages 10000000 --workers 8 --url http://127.0.0.1:8080 --json run.json
```

`--start N` continues an existing store from message N (to grow it in steps). `--segment` sets how
often it reports, and `--samples` how many read samples it takes per endpoint.
