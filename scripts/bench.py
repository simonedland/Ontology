"""Ingest + read benchmark for the tgstore HTTP API (synthetic, fictional data).

    docker compose up -d db api
    python scripts/bench.py --messages 5000000 --workers 8 --url http://127.0.0.1:8080

Pushes N messages (plus ~5% edits/exact duplicates and embedded user/chat records) through
``POST /v1/records`` with K concurrent connections, in segments of ``--segment`` messages. After each
segment it drains the in-flight batches, then samples the read endpoints, so you can see whether ingest
throughput or read latency degrades as the tables grow.

Every record is a pure function of (seed, message index): runs are reproducible, generation is spread
over ``--gen-procs`` processes, and duplicates/edits of old messages are regenerated rather than kept
in memory. Memory stays bounded (at most ~``2 * workers + gen-procs`` batches in flight).

Shape: Zipf chat activity and Zipf posters per chat (each chat has its own heavy posters), a Zipf
vocabulary (so search terms range from very common to rare), replies, forwards, media, occasional
username changes (exercises ``user_history``), membership records with admin/left statuses, and exact
duplicates that land both in the same batch and in later batches (exercises idempotent upserts).
Standard library only.
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import multiprocessing as mp
import queue
import random
import statistics
import sys
import threading
import time
from collections import deque
from urllib.parse import quote, urlsplit

BASE_TS = 1_735_689_600            # 2025-01-01T00:00:00Z
SPAN_S = 365 * 86400               # messages spread over a year, increasing with the index
SYL = [
    "ka", "lo", "mi", "ne", "ru", "ta", "vo", "si", "de", "pa", "zu", "ri", "go", "me", "la", "to",
    "ni", "be", "ku", "sa", "fe", "ho", "ja", "ly",
]
COMMON = [
    "price", "border", "protest", "bridge", "farm", "truth", "energy", "bank", "court", "election",
    "army", "port", "freight", "rail", "school", "hospital", "tax", "salary", "pension", "market",
    "city", "village", "river", "storm", "vote", "rally", "police", "mayor",
]
MEDIA = ("photo", "video", "voice", "document", "sticker")
STATUSES = ("member", "member", "member", "admin", "left", "kicked", "creator")

CFG: dict = {}                     # per-process generator config (set by _init)
VOCAB: list[str] = []


def _init(cfg: dict) -> None:
    CFG.clear()
    CFG.update(cfg)
    rnd = random.Random(cfg["seed"])
    words = set(COMMON)
    while len(words) < cfg["vocab"]:
        words.add("".join(rnd.choice(SYL) for _ in range(rnd.randint(2, 4))))
    VOCAB[:] = sorted(words)
    rnd.shuffle(VOCAB)             # rank order = Zipf frequency order


def zipf(rnd: random.Random, n: int, s: float = 1.1) -> int:
    """Rank in [0, n): heavy head, long tail (inverse-CDF approximation)."""
    return min(n - 1, int(n ** (rnd.random() ** s)) - 1) if n > 1 else 0


def chat_id(c: int) -> int:
    return -1_000_000_000_000 - c * 7919


def user_id(u: int) -> int:
    return 100_000_000 + u * 104_729


def _rng(i: int, salt: int = 0) -> random.Random:
    return random.Random((CFG["seed"] << 42) ^ (i << 2) ^ salt)


def message(i: int) -> dict:
    """Message number i, deterministic. Its id is i (unique per chat, increasing with time)."""
    r = _rng(i)
    c = zipf(r, CFG["chats"], 1.15)
    u = (zipf(r, CFG["users"], 1.3) + c * 7_777) % CFG["users"]     # per-chat heavy posters
    n = r.randint(3, 25)
    text = " ".join(VOCAB[zipf(r, len(VOCAB), 1.05)] for _ in range(n))
    m = {"kind": "message", "chat_id": chat_id(c), "id": i + 1, "user_id": user_id(u),
         "date": BASE_TS + i * SPAN_S // CFG["messages"], "text": text}
    x = r.random()
    if x < 0.20 and i > 0:
        m["reply_to_id"] = r.randint(max(1, i - 5000), i)
    elif x < 0.25:
        m["forward_from_chat_id"] = chat_id(zipf(r, CFG["chats"]))
    elif x < 0.27:
        m["forward_from_user_id"] = user_id(zipf(r, CFG["users"]))
    if r.random() < 0.15:
        m["media_type"] = r.choice(MEDIA)
        if r.random() < 0.5:
            m["text"] = None
    if r.random() < 0.10:
        m["views"] = r.randint(1, 100_000)
    if r.random() < 0.03:          # embedded sender profile; ~1 in 8 of them is a renamed version
        ver = r.randint(1, 3) if r.random() < 0.125 else 0
        m["user"] = {"id": user_id(u), "username": f"user{u}" + (f"_v{ver}" if ver else ""),
                     "first_name": VOCAB[u % len(VOCAB)].title(), "lang_code": r.choice(("en", "ru", "uk", "de")),
                     "is_bot": u % 997 == 0}
    if r.random() < 0.002:
        m["chat"] = {"id": chat_id(c), "member_count": 10 + r.randint(0, 50_000)}
    if r.random() < 0.01:
        m["extra"] = {"source": "bench", "grouped_id": r.randint(1, 10**9)}
    return m


def chat_record(c: int) -> dict:
    r = _rng(c, 1)
    return {"kind": "chat", "id": chat_id(c), "type": "supergroup" if r.random() < 0.8 else "channel",
            "title": f"Chat {c} " + VOCAB[c % len(VOCAB)], "username": f"chat{c}"}


def batch_lines(b: int) -> tuple[bytes, dict]:
    """Batch b covers messages [b*bs, (b+1)*bs) plus extra records; returns (NDJSON, counts)."""
    bs, n = CFG["batch"], CFG["messages"]
    lo, hi = b * bs, min(n, (b + 1) * bs)
    r = _rng(b, 2)
    recs = []
    if b == 0:
        recs.extend(chat_record(c) for c in range(CFG["chats"]))
    cnt = {"message": 0, "edit": 0, "dup": 0, "membership": 0}
    for i in range(lo, hi):
        recs.append(message(i))
        cnt["message"] += 1
        x = r.random()
        if x < CFG["dup_rate"] * 0.6 and i > 0:                  # edit of an older message (partial record)
            j = max(0, i - 1 - zipf(r, min(i, 200_000)))
            old = message(j)
            recs.append({"kind": "message", "chat_id": old["chat_id"], "id": old["id"],
                         "text": (old["text"] or "") + " edited " + VOCAB[r.randrange(len(VOCAB))],
                         "edit_date": BASE_TS + i * SPAN_S // n})
            cnt["edit"] += 1
        elif x < CFG["dup_rate"] and i > 0:                      # exact resend; half land in this batch
            j = r.randrange(lo, i) if (i > lo and r.random() < 0.5) else max(0, i - 1 - zipf(r, min(i, 500_000)))
            recs.append(message(j))
            cnt["dup"] += 1
        if r.random() < 0.005:
            m = recs[-1]
            if m.get("user_id"):
                recs.append({"kind": "membership", "chat_id": m["chat_id"], "user_id": m["user_id"],
                             "status": r.choice(STATUSES), "joined_at": m.get("date", BASE_TS)})
                cnt["membership"] += 1
    body = "\n".join(json.dumps(x, separators=(",", ":"), ensure_ascii=False) for x in recs).encode() + b"\n"
    cnt["records"] = len(recs)
    return body, cnt


# ---------------------------------------------------------------- HTTP


class Api:
    def __init__(self, url: str, token: str | None, timeout: float = 300):
        p = urlsplit(url)
        self.host, self.port, self.prefix = p.hostname, p.port or 80, p.path.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.timeout = timeout
        self.conn: http.client.HTTPConnection | None = None

    def request(self, method: str, path: str, body: bytes | None = None, ctype: str | None = None):
        headers = dict(self.headers)
        if ctype:
            headers["Content-Type"] = ctype
        for attempt in (0, 1):     # one reconnect on a dropped keep-alive connection
            if self.conn is None:
                self.conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
            try:
                self.conn.request(method, self.prefix + path, body=body, headers=headers)
                resp = self.conn.getresponse()
                data = resp.read()
                return resp.status, data
            except (http.client.HTTPException, ConnectionError, OSError):
                self.conn.close()
                self.conn = None
                if attempt:
                    raise

    def get_json(self, path: str):
        st, data = self.request("GET", path)
        if st != 200:
            raise RuntimeError(f"GET {path} -> {st}: {data[:300]!r}")
        return json.loads(data)


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, math.ceil(p / 100 * len(xs)) - 1))]


def sample_reads(api: Api, done_msgs: int, n: int, seed: int) -> dict:
    """p50/p95 (ms) of each read endpoint over ids that exist (derived from ingested message indices)."""
    r = random.Random(seed)
    out: dict[str, list[float]] = {k: [] for k in ("user", "user_messages", "user_messages_page2", "chat_messages",
                                                   "chat_messages_page2", "search_common", "search_rare",
                                                   "search_common_newest", "search_rare_newest", "stats")}
    errors: list[str] = []

    def timed(key: str, path: str):
        t = time.perf_counter()
        try:
            res = api.get_json(path)
        except Exception as e:  # noqa: BLE001 - reported, the benchmark keeps going
            errors.append(f"{key}: {e}")
            return None
        out[key].append((time.perf_counter() - t) * 1000)
        return res

    for _ in range(n):
        m = message(r.randrange(done_msgs))
        for _ in range(200):       # /v1/users/{id} needs a profile record: find a message that embedded one
            pm = message(r.randrange(done_msgs))
            if "user" in pm:
                timed("user", f"/v1/users/{pm['user_id']}")
                break
        um = timed("user_messages", f"/v1/users/{m['user_id']}/messages?limit=50")
        if isinstance(um, list) and len(um) == 50 and um[-1].get("date"):
            timed("user_messages_page2", f"/v1/users/{m['user_id']}/messages?limit=50&before=" + quote(str(um[-1]["date"])))
        page = timed("chat_messages", f"/v1/chats/{m['chat_id']}/messages?limit=50")
        rows = page if isinstance(page, list) else (page or {}).get("messages") or (page or {}).get("items") or []
        if rows and isinstance(rows[-1], dict) and rows[-1].get("id") is not None:
            timed("chat_messages_page2", f"/v1/chats/{m['chat_id']}/messages?limit=50&before={rows[-1]['id']}")
        common, rare = quote(VOCAB[r.randrange(0, 20)]), quote(VOCAB[r.randrange(len(VOCAB) // 2, len(VOCAB))])
        timed("search_common", f"/v1/search?q={common}&limit=50")
        timed("search_rare", f"/v1/search?q={rare}&limit=50")
        timed("search_common_newest", f"/v1/search?q={common}&limit=50&newest=true")
        timed("search_rare_newest", f"/v1/search?q={rare}&limit=50&newest=true")
    for _ in range(max(3, n // 10)):
        timed("stats", "/v1/stats")
    res = {k: {"p50": round(pct(v, 50), 2), "p95": round(pct(v, 95), 2), "n": len(v)} for k, v in out.items() if v}
    if errors:
        res["errors"] = errors[:5] + ([f"... {len(errors) - 5} more"] if len(errors) > 5 else [])
    return res


# ---------------------------------------------------------------- driver


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--messages", type=int, default=1_000_000)
    ap.add_argument("--workers", type=int, default=8, help="concurrent HTTP connections")
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--token", default=None)
    ap.add_argument("--batch", type=int, default=5000, help="messages per POST")
    ap.add_argument("--segment", type=int, default=1_000_000, help="report every N messages")
    ap.add_argument("--chats", type=int, default=2_000)
    ap.add_argument("--users", type=int, default=None, help="default: messages / 20, min 1000")
    ap.add_argument("--vocab", type=int, default=20_000)
    ap.add_argument("--dup-rate", type=float, default=0.05, help="edits + exact duplicates per message")
    ap.add_argument("--samples", type=int, default=50, help="read samples per endpoint per segment")
    ap.add_argument("--start", type=int, default=0, help="first message index (resume / grow an existing store)")
    ap.add_argument("--gen-procs", type=int, default=max(1, min(4, (mp.cpu_count() or 2) - 1)))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", default=None, help="write per-segment results here")
    a = ap.parse_args()
    if a.segment % a.batch or a.start % a.batch:
        ap.error("--segment and --start must be multiples of --batch")
    cfg = {"seed": a.seed, "messages": a.messages, "chats": a.chats, "batch": a.batch, "vocab": a.vocab,
           "users": a.users or max(1000, a.messages // 20), "dup_rate": a.dup_rate}
    _init(cfg)
    api = Api(a.url, a.token)
    try:
        before = api.get_json("/v1/stats")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"cannot reach {a.url}/v1/stats: {e}")
    print(f"tgstore bench: {a.messages:,} messages, {cfg['chats']:,} chats, {cfg['users']:,} users, "
          f"batch {a.batch}, {a.workers} workers, {a.gen_procs} generator procs -> {a.url}")
    print(f"stats before: {json.dumps(before)}", flush=True)

    work: queue.Queue = queue.Queue(maxsize=a.workers)
    lock = threading.Lock()
    tot = {"records": 0, "bytes": 0, "errors": 0, "post_s": 0.0}
    first_errors: list[str] = []
    post_ms: list[float] = []

    def worker():
        wapi = Api(a.url, a.token)
        while True:
            item = work.get()
            if item is None:
                work.task_done()
                return
            body, cnt = item
            t = time.perf_counter()
            try:
                st, data = wapi.request("POST", "/v1/records", body, "application/x-ndjson")
                err = None if st == 200 else f"HTTP {st}: {data[:300]!r}"
            except Exception as e:  # noqa: BLE001
                err = repr(e)
            dt = time.perf_counter() - t
            with lock:
                post_ms.append(dt * 1000)
                tot["post_s"] += dt
                if err:
                    tot["errors"] += 1
                    if len(first_errors) < 5:
                        first_errors.append(err)
                else:
                    tot["records"] += cnt["records"]
                    tot["bytes"] += len(body)
            work.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(a.workers)]
    for t in threads:
        t.start()

    nb_start, nb_end = a.start // a.batch, math.ceil(a.messages / a.batch)
    seg_batches = a.segment // a.batch
    results = []
    pool = mp.Pool(a.gen_procs, initializer=_init, initargs=(cfg,))
    pending: deque = deque()
    nxt = nb_start
    window = a.gen_procs * 2
    seg_t0, seg_rec0, seg_bytes0 = time.perf_counter(), 0, 0
    gen_wait = 0.0
    cnt_all = {"message": 0, "edit": 0, "dup": 0, "membership": 0}
    t_all = time.perf_counter()
    try:
        for b in range(nb_start, nb_end):
            while nxt < nb_end and len(pending) < window:
                pending.append(pool.apply_async(batch_lines, (nxt,)))
                nxt += 1
            tw = time.perf_counter()
            body, cnt = pending.popleft().get()
            gen_wait += time.perf_counter() - tw
            for k in cnt_all:
                cnt_all[k] += cnt[k]
            work.put((body, cnt))
            seg_end = (b + 1 - nb_start) % seg_batches == 0 or b + 1 == nb_end
            if not seg_end:
                continue
            work.join()        # drain: the segment's time includes its last commit
            dt = time.perf_counter() - seg_t0
            done = min(a.messages, (b + 1) * a.batch)
            with lock:
                recs, byts = tot["records"] - seg_rec0, tot["bytes"] - seg_bytes0
                seg_rec0, seg_bytes0 = tot["records"], tot["bytes"]
                lat = {"p50": round(pct(post_ms, 50)), "p95": round(pct(post_ms, 95)), "max": round(max(post_ms or [0]))}
                post_ms.clear()
                errs = tot["errors"]
            seg_msgs = done - (results[-1]["messages_total"] if results else a.start)
            t_r = time.perf_counter()
            reads = sample_reads(api, done, a.samples, a.seed + done)
            stats = api.get_json("/v1/stats")
            row = {"segment": len(results) + 1, "messages_total": done, "seconds": round(dt, 1),
                   "msgs_per_s": round(seg_msgs / dt), "records_per_s": round(recs / dt),
                   "mb_per_s": round(byts / dt / 1e6, 2), "post_ms": lat, "gen_wait_s": round(gen_wait, 1),
                   "errors_total": errs, "reads_ms": reads, "read_sample_s": round(time.perf_counter() - t_r, 1),
                   "stats": stats}
            results.append(row)
            gen_wait = 0.0
            print(f"\n[seg {row['segment']}] {done:>11,} msgs  {row['msgs_per_s']:>7,} msg/s  "
                  f"{row['records_per_s']:>7,} rec/s  {row['mb_per_s']:>6} MB/s  POST p50/p95/max "
                  f"{lat['p50']}/{lat['p95']}/{lat['max']} ms  gen-wait {row['gen_wait_s']}s  errors {errs}")
            for k, v in reads.items():
                if k != "errors":
                    print(f"    {k:<20} p50 {v['p50']:>8} ms   p95 {v['p95']:>8} ms   (n={v['n']})")
            if "errors" in reads:
                print("    read errors:", *reads["errors"], sep="\n      ")
            if first_errors:
                print("    ingest errors:", *first_errors, sep="\n      ")
            print(f"    stats: {json.dumps(stats)}", flush=True)
            if a.json:
                with open(a.json, "w", encoding="utf-8") as f:
                    json.dump({"args": vars(a), "config": cfg, "generated": cnt_all, "segments": results}, f, indent=2)
            seg_t0 = time.perf_counter()
    except KeyboardInterrupt:
        print("\ninterrupted; draining in-flight batches", file=sys.stderr)
    finally:
        pool.terminate()
        for _ in threads:
            work.put(None)
        for t in threads:
            t.join(timeout=600)

    wall = time.perf_counter() - t_all
    rates = [r["msgs_per_s"] for r in results]
    print(f"\ngenerated: {json.dumps(cnt_all)}  wall {wall:.0f}s  ingest errors {tot['errors']}")
    if len(rates) > 1:
        print(f"msg/s per segment: {rates}  first->last {rates[-1] / rates[0]:.2f}x  "
              f"median {statistics.median(rates):,.0f}")
    sys.exit(1 if tot["errors"] else 0)


if __name__ == "__main__":
    main()
