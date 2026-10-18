# Live dashboard and continuous ingestion

`tgo serve` turns the dashboard into a live, local web app. It watches an input folder, ingests new data
as it lands, and pushes every update to open browsers. Charts, the relation network and the findings
refresh in place, keeping your filters, selection, zoom and time window. Like everything else in TGO it
runs offline: the server uses only the Python standard library, makes no outbound connections, and serves
a page that is not allowed to contact any other origin.

```bash
python -m tgo serve --input data/inbox --out out/live --open
# TGO live dashboard: http://127.0.0.1:8765/  (watching data/inbox, every 5s; Ctrl+C to stop)
```

The input folder may start empty. Anything TGO can read can be dropped into it at any time.

## Feeding it

| Source | What happens |
|---|---|
| `tgo collect --out data/inbox/mtproto …` (run it on a schedule, e.g. every 15 min) | the collector **appends** to `entities.jsonl` / `messages.jsonl`. TGO tails those files and ingests only the new lines. A half-written last line waits for the next scan. |
| a new Desktop export folder (`…/result.json` + media) | ingested whole as soon as it appears |
| a new file for a plugin source (e.g. `leads.csv` for [`examples/csv_leads.py`](../examples/csv_leads.py)) | ingested whole |
| `analyst/judgements.json` edited | applied at the next update; analysts can confirm or reject identity candidates live |
| a file **rewritten** rather than appended to (a re-export, a truncated JSONL) or removed | a full rebuild, so observation counts never double; shown as "rebuilt: …" in the ingestion log |

Copy large exports into the folder in one go: write to a temporary name and rename, or copy the folder
next to it and move it in. Otherwise a scan can see a partly copied `result.json`. It will be re-read when
it changes again, but at the cost of a rebuild.

## What updates

The **Live ingestion** section shows new messages, nodes and relations per update, with the time each
update took. The badge in the header says how long ago the last update was, and turns red while the
browser is reconnecting. Nodes that just arrived glow in the relation network for a few seconds.

Each update also refreshes, in `--out`:

- `graph.json`, `summary.json` (with `"live": true`) and `validation.json`, so other tools always see the
  current state; turn this off with `--no-snapshots`;
- `/report.html` on the server, the investigation view of the current graph, built on demand. The
  dashboard's *Open investigation view* button links to it.

## How it stays correct

Observed facts are merged into a **base graph** incrementally. Derived layers (extraction, coordination,
communities, identity, plugin steps) are not idempotent, so every update recomputes them on a copy of the
base, exactly as a batch run would. `tests/test_live.py` checks that ingesting a collection in pieces,
plus an export dropped in mid-session, gives **the same nodes, relations, counts and confidences** as
`tgo run` over the final folder.

The expensive per-file extractors (perceptual hashes, EXIF, document text, speech-to-text, OCR) are
cached by file signature, so an update re-reads no media it has already seen. Text fingerprints and identifier matches are cached the same way. Update cost grows with the size of
the case, not with how much arrived:

| Case size | First build | Each update |
|---|---|---|
| demo (~140 messages) | < 1 s | < 1 s |
| 50,000 messages (64k nodes, 138k relations) | 12 s | ~5 s |
| 200,000 messages (254k nodes, 561k relations), `--engine duckdb` | 24 s | ~5 s |

Measured on a laptop (12 cores). Past roughly 10⁵ messages, use `--engine duckdb`.

**Engines:**

- **memory** (the default): incremental ingestion as described above. Suits a case or a monitoring
  watch-list up to roughly 10⁵–10⁶ relations.
- **duckdb** (`--engine duckdb`): for corpora of millions of messages, and incremental too. New lines and
  files are streamed into the corpus store (`--out/tgo.duckdb`) as batches. Every corpus analysis then
  re-runs in place: each one retracts its previous output, and merging is incremental. The dashboard reads
  the store, and the relation network shows the current case slice. A rewritten or removed file rebuilds
  the store. While the server runs, it owns `tgo.duckdb`, since DuckDB allows one writer. To query it
  alongside, use the snapshots or stop the server.

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--input DIR` | (required) | folder to watch, searched recursively |
| `--out DIR` | (required) | where snapshots are kept current (and the corpus store, with `--engine duckdb`) |
| `--interval S` | `5` | seconds between scans |
| `--host` / `--port` | `127.0.0.1` / `8765` | bind address. Anything other than this machine requires a token. |
| `--token T` | generated when needed | access token for non-local binds |
| `--engine` | by input size | `memory` or `duckdb` (see above); defaults to the `scale.engine` choice for the input |
| `--max-nodes N` | `500` | actors in the relation network |
| `--no-snapshots` | off | don't rewrite `graph.json` / `summary.json` / `validation.json` |
| `--config`, `--plugin` | | as for `tgo run` ([configuration.md](configuration.md), [extending.md](extending.md)) |
| `--open` | off | open the dashboard in the default browser |

## Security

- **This machine only by default.** With `--host 0.0.0.0` (e.g. inside a container, or for colleagues on
  the LAN), the server requires a token. It generates one if you don't pass `--token` and prints the URL to
  share. The token works once as `?token=…` (then an HttpOnly, SameSite cookie) or as an `X-TGO-Token`
  header. Anyone with the token can read the case, so treat it like the data.
- **Read-only.** There are no endpoints that change data. Analysts change judgements by editing
  `analyst/judgements.json`, which keeps an audit trail in your normal file history.
- **Nothing leaves the machine through the browser.** Every response carries a Content-Security-Policy with
  `default-src 'none'` and `connect-src 'self'`, so even a malicious name inside the data cannot make the
  page contact another host. Names are rendered as text, never as HTML.
- **Server-side:** the server makes no outbound connections. The offline checks
  (`python -m tgo offline-check`, `tests/test_offline.py`) cover `tgo/live/` like everything else.

## Running it in Docker

The compose stack has a `live` profile that runs `tgo serve` for you:

```bash
cp .env.example .env                       # set TGO_TOKEN (and TGO_INPUT / TGO_CASE) there
docker compose --profile live up -d        # http://127.0.0.1:8765/?token=<TGO_TOKEN>
docker compose logs -f live                # the URL and one line per update
```

- **Mounts:** inside the container the input is `/data/$TGO_INPUT` (read-only for the server) and the
  output is `/out/$TGO_CASE`.
- **Feeding it:** the `collect` profile, or anything you copy into `workspace/` on the host.
- **Network:** the server sits on an internal network with no outbound access. A small loopback-only
  proxy publishes it on 127.0.0.1, and `/healthz` is the container health check.
- **Details:** see [docker.md](docker.md).

## Endpoints

| Path | Returns |
|---|---|
| `/` | the dashboard (live mode) |
| `/api/dashboard` | the current dashboard payload (JSON) |
| `/api/events` | server-sent events: `update` with `{"version", "updated", "error"}`, heartbeats every 15 s |
| `/api/status` | engine state, last error, recent ingestion events, cache size |
| `/report.html` | investigation view of the current graph (built on demand) |
| `/healthz` | `{"ok": true, "version": n}` without a token (for container health checks) |
