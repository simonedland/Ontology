# Record format (ingest contract)

Everything that goes into the store is a **record**: a JSON object with a `kind`. Send records as
NDJSON (one per line), a JSON array, or a single object. Unknown fields are never rejected: anything
not listed below is kept in the record's `extra` (JSONB), so new sources work without schema changes.

Ids are Telegram's numeric ids (64-bit integers). Dates are ISO 8601 strings (`2026-10-18T12:00:00Z`)
or Unix seconds. Every field except the keys is optional.

## `chat`

| field | type | notes |
|---|---|---|
| `id` | int | **key** |
| `type` | str | `group`, `supergroup`, `channel`, `private`, ... |
| `title`, `username`, `about` | str | |
| `member_count` | int | |
| `extra` | object | merged into stored `extra` |

## `user`

| field | type | notes |
|---|---|---|
| `id` | int | **key** |
| `username`, `first_name`, `last_name`, `phone`, `bio`, `lang_code` | str | |
| `is_bot` | bool | |
| `extra` | object | |

When a profile field (`username`, `first_name`, `last_name`, `bio`, `phone`) changes, the old values
are kept in `user_history`, so name and bio changes over time are not lost.

## `message`

| field | type | notes |
|---|---|---|
| `chat_id`, `id` | int | **key** (message ids are per chat) |
| `user_id` | int | sender; null for anonymous admins / channel posts |
| `date` | date | |
| `text` | str | |
| `reply_to_id` | int | message id in the same chat |
| `forward_from_user_id`, `forward_from_chat_id` | int | |
| `edit_date` | date | |
| `media_type` | str | `photo`, `video`, `voice`, `document`, `sticker`, ... |
| `views` | int | |
| `user`, `chat` | object | optional embedded `user` / `chat` record (no `kind` needed) |
| `extra` | object | |

A message also records the sender as a member of the chat (`membership`, `status` unknown unless known).

## `membership`

| field | type | notes |
|---|---|---|
| `chat_id`, `user_id` | int | **key** |
| `status` | str | `member`, `admin`, `creator`, `left`, `kicked`, ... |
| `joined_at` | date | |
| `extra` | object | |

## Merge rules

Records are **upserts** and ingest is **idempotent**: sending the same record twice changes nothing.
A later record overwrites a field only when it carries a non-null value, and `extra` objects are merged
key by key. So partial records (for example a message edit with just `chat_id`, `id`, `text`, `edit_date`)
are fine.

`POST /v1/records` answers with the number of records received per table (after merging duplicates
within the request). Memberships implied by messages are stored but not counted.

## Example

```json
{"kind": "chat", "id": -1001234567890, "type": "supergroup", "title": "Example group"}
{"kind": "user", "id": 42, "username": "alice", "first_name": "Alice"}
{"kind": "message", "chat_id": -1001234567890, "id": 1, "user_id": 42, "date": "2026-10-18T12:00:00Z", "text": "hi"}
```
