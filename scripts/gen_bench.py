"""Generate a large synthetic MTProto corpus for scale benchmarks (fictional data).

    python scripts/gen_bench.py out/bench1m --messages 1000000
    python -m tgo run --input out/bench1m --out out/bench1m-run --engine duckdb

Shape: broadcast channels with Zipf-distributed activity, organic forwarding between
channels, popular URLs (Zipf - the fan-out stress case for coordination), supergroups
with user chatter, replies, reactions, phone/wallet identifiers - plus planted
coordination rings (channels reposting the same text within seconds) whose members the
benchmark checks are recovered exactly (written to ``planted.json``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tgo.identifiers import b58check_encode  # noqa: E402

WORDS = ("grain border price minister protest bridge farm truth share now today energy gas bank court "
         "election army port harbor freight rail road school hospital tax salary pension market city "
         "village river weather storm vote rally police mayor fund aid loan debt export import").split()


def zipf_pick(rnd: random.Random, n: int, s: float = 1.1) -> int:
    # inverse-CDF approximation good enough for benchmark skew
    u = rnd.random()
    return min(n - 1, int(n ** (u ** s)) - 1) if n > 1 else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--messages", type=int, default=1_000_000)
    ap.add_argument("--channels", type=int, default=None)
    ap.add_argument("--users", type=int, default=None)
    ap.add_argument("--rings", type=int, default=5, help="planted coordination rings")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    n_msg = a.messages
    n_ch = a.channels or max(50, n_msg // 2000)
    n_users = a.users or max(200, n_msg // 50)
    n_groups = max(5, n_ch // 10)
    out = Path(a.out) / "mtproto"
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    span = 90 * 86400
    base = 1_780_000_000
    ch_ids = [3_000_000_000 + i for i in range(n_ch)]
    grp_ids = [4_000_000_000 + i for i in range(n_groups)]
    user_ids = [600_000_000 + i for i in range(n_users)]
    domains = [f"news{i}.example" for i in range(500)]
    wallets = [b58check_encode(bytes([0x41]) + hashlib.sha256(f"w{i}".encode()).digest()[:20]) for i in range(300)]

    (out / "collector.json").write_text(json.dumps({
        "collector": {"_": "User", "id": 1, "first_name": "Bench", "is_self": True},
        "run": {"id": f"bench-{n_msg}", "client": "gen_bench", "layer": 230}}), encoding="utf-8")
    with open(out / "entities.jsonl", "w", encoding="utf-8") as f:
        for i, cid in enumerate(ch_ids):
            f.write(json.dumps({"_": "Channel", "id": cid, "title": f"Channel {i}", "username": f"bench_ch{i}",
                                "broadcast": True, "date": base - rnd.randrange(800) * 86400,
                                "participants_count": rnd.randrange(100, 200_000)}) + "\n")
        for i, gid in enumerate(grp_ids):
            f.write(json.dumps({"_": "Channel", "id": gid, "title": f"Group {i}", "broadcast": False,
                                "megagroup": True, "date": base - rnd.randrange(800) * 86400}) + "\n")
        for i, uid in enumerate(user_ids):
            f.write(json.dumps({"_": "User", "id": uid, "first_name": f"U{i}",
                                "username": f"bench_u{i}" if i % 3 == 0 else None,
                                "status": {"_": "UserStatusRecently"}}) + "\n")

    # planted rings: k channels re-post the same text within seconds, P times each
    rings, planted = [], []
    pool = ch_ids[:]
    rnd.shuffle(pool)
    for r in range(a.rings):
        members = [pool.pop() for _ in range(rnd.randrange(4, 12))]
        rings.append(members)
        planted.append(sorted(f"channel:{m}" for m in members))
    ring_msgs = sum(len(m) * 60 for m in rings)
    organic = max(0, n_msg - ring_msgs)
    next_id = {k: 1 for k in ch_ids + grp_ids}
    written = 0
    with open(out / "messages.jsonl", "w", encoding="utf-8") as f:
        for ri, members in enumerate(rings):
            for k in range(60):
                t = base + rnd.randrange(span)
                txt = f"Ring {ri} message {k}: " + " ".join(rnd.choice(WORDS) for _ in range(16))
                for j, cid in enumerate(members):
                    mid = next_id[cid]
                    next_id[cid] += 1
                    f.write(json.dumps({"_": "Message", "id": mid, "peer_id": {"_": "PeerChannel", "channel_id": cid},
                                        "post": True, "date": t + j * rnd.randrange(1, 4), "message": txt}) + "\n")
                    written += 1
        for _ in range(organic):
            t = base + rnd.randrange(span)
            if rnd.random() < 0.7:     # channel post
                cid = ch_ids[zipf_pick(rnd, n_ch)]
                mid = next_id[cid]
                next_id[cid] += 1
                m = {"_": "Message", "id": mid, "peer_id": {"_": "PeerChannel", "channel_id": cid}, "post": True,
                     "date": t, "views": rnd.randrange(10, 100_000)}
                txt = " ".join(rnd.choice(WORDS) for _ in range(rnd.randrange(8, 30)))
                if rnd.random() < 0.35:
                    txt += f" https://{domains[zipf_pick(rnd, len(domains))]}/a/{rnd.randrange(50_000)}"
                if rnd.random() < 0.12:
                    src = ch_ids[zipf_pick(rnd, n_ch)]
                    if src != cid and next_id[src] > 1:
                        m["fwd_from"] = {"_": "MessageFwdHeader", "from_id": {"_": "PeerChannel", "channel_id": src},
                                         "channel_post": rnd.randrange(1, next_id[src]), "date": t - rnd.randrange(86400)}
                m["message"] = txt
            else:                      # group chatter
                gid = grp_ids[zipf_pick(rnd, n_groups)]
                uid = user_ids[zipf_pick(rnd, n_users, 1.05)]
                mid = next_id[gid]
                next_id[gid] += 1
                txt = " ".join(rnd.choice(WORDS) for _ in range(rnd.randrange(3, 20)))
                if rnd.random() < 0.01:
                    txt += f" send to {wallets[zipf_pick(rnd, len(wallets))]}"
                if rnd.random() < 0.005:
                    txt += f" call +47 9{rnd.randrange(10_000_000):07d}"
                m = {"_": "Message", "id": mid, "peer_id": {"_": "PeerChannel", "channel_id": gid},
                     "from_id": {"_": "PeerUser", "user_id": uid}, "date": t, "message": txt}
                if mid > 1 and rnd.random() < 0.3:
                    m["reply_to"] = {"_": "MessageReplyHeader", "reply_to_msg_id": rnd.randrange(1, mid)}
                if rnd.random() < 0.1:
                    m["reactions"] = {"_": "MessageReactions", "recent_reactions": [
                        {"_": "MessagePeerReaction", "peer_id": {"_": "PeerUser", "user_id": rnd.choice(user_ids)},
                         "date": t + 60, "reaction": {"_": "ReactionEmoji", "emoticon": "👍"}}]}
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
            written += 1
    (Path(a.out) / "planted.json").write_text(json.dumps({"rings": planted}, indent=1), encoding="utf-8")
    size = sum(p.stat().st_size for p in out.iterdir())
    print(f"{written:,} messages, {n_ch:,} channels, {n_groups:,} groups, {n_users:,} users, "
          f"{size / 1e6:.0f} MB in {time.time() - t0:.0f}s -> {a.out}")


if __name__ == "__main__":
    main()
