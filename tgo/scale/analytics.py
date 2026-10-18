"""Set-based (DuckDB SQL / NetworKit / Faiss) versions of the TGO analyses.

Same semantics and output vocabulary as :mod:`tgo.analysis`, but never
materialise per-message Python objects.  Quadratic blow-ups are prevented
with the standard blocking techniques (research notes in docs):

* coordination: dedupe to the first share per (account, object); drop objects
  shared by more than ``max_object_accounts`` accounts (logged); equi-join on
  (object, time bucket) + neighbouring bucket, residual |dt| <= window; pair
  counts aggregated in SQL; Poisson null model per surviving pair.
* SimHash near-duplicates: 4 x 16-bit block equi-joins (pigeonhole for k <= 3)
  verified with ``bit_count(xor)``.
* identity: per-evidence-key degree cap (``max_evidence_accounts``) before the
  pair join, IDF ``min(1, 2/n)``, noisy-OR per pair in SQL.
* communities: NetworKit PLM (parallel Louvain, MIT) on dense integer ids;
  falls back to NetworkX when NetworKit is not installed.
* perceptual near-duplicates: Faiss binary HNSW k-NN + radius filter; Python
  band index fallback below 200k fingerprints.
Derived rows go to the store's staging tables with a DetectionRun in ``runs``,
exactly like the in-memory engine writes them.
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict

from .. import keys as K
from ..graph import Graph

AGENT = {"codeLicense": "MIT", "weightsLicense": "n/a"}


def _run(store, kind: str, key: str, tool: str, version: str, **params) -> str:
    """Write a DetectionRun + ModelAgent through a tiny Graph and return the run key."""
    g = Graph()
    with g.activity(kind, key, agent={"tool": tool, "version": version, **AGENT}, **params) as rk:
        pass
    store.flush(g)
    return rk


def _insert_edges(store, select_sql: str, params=None) -> int:
    """INSERT derived edges; ``select_sql`` yields the 15 staging_edges columns (minus ``seq``) in order."""
    seq = store._seq()
    # positional: id, type, src, dst, status, conf, method, count, t_first, t_last, scope, observed_by,
    # evidence, runs, props
    store.con.execute(f"""INSERT INTO staging_edges SELECT q.*, {seq}, true, md5_number(q.id), md5_number(q.src),
        md5_number(q.dst) FROM ({select_sql})
        q(id, type, src, dst, status, conf, method, count, t_first, t_last, scope, observed_by, evidence, runs, props)""",
                      params or [])
    return store.con.execute(f"SELECT count(*) FROM staging_edges WHERE seq = {seq}").fetchone()[0]


# ============================================================== aggregate
def aggregate(store) -> dict:
    """SQL projections: POSTED_IN, FORWARDS_FROM, MENTIONS, NAME_HISTORY_LINK, SAME_DOCUMENT_LINEAGE, SAME_FILE_AS."""
    rk = _run(store, "DetectionRun", "aggregate", "tgo-aggregate-sql", "0.2")
    out = {}
    out["POSTED_IN"] = _insert_edges(store, f"""
        SELECT s.src || '-[POSTED_IN]->' || c.dst AS id, 'POSTED_IN' AS type, s.src, c.dst,
               'observed' AS status, 1.0 AS conf, 'aggregate(SENT,IN_CHAT)' AS method, count(*) AS count,
               min(s.t_first) AS t_first, max(s.t_last) AS t_last, NULL AS scope, NULL AS observed_by,
               list_slice(list(s.dst), 1, 50) AS evidence, ['{rk}'] AS runs, '{{}}' AS props
        FROM edges s JOIN edges c ON c.src = s.dst AND c.type = 'IN_CHAT'
        WHERE s.type = 'SENT' AND s.src <> c.dst GROUP BY s.src, c.dst""")
    out["FORWARDS_FROM"] = _insert_edges(store, f"""
        WITH f AS (
            SELECT f.src AS msg, h.dst AS here, coalesce(t.dst, f.dst) AS there, f.t_first
            FROM edges f JOIN edges h ON h.src = f.src AND h.type = 'IN_CHAT'
            LEFT JOIN edges t ON t.src = f.dst AND t.type = 'IN_CHAT'
            WHERE f.type = 'FORWARD_OF')
        SELECT here || '-[FORWARDS_FROM]->' || there, 'FORWARDS_FROM', here, there, 'observed', 1.0,
               'aggregate(FORWARD_OF)', count(*), min(t_first), max(t_first), NULL, NULL,
               list_slice(list(msg), 1, 50), ['{rk}'], '{{}}'
        FROM f WHERE here <> there GROUP BY here, there""")
    out["MENTIONS"] = _insert_edges(store, f"""
        SELECT ci.src || '-[MENTIONS]->' || hu.src, 'MENTIONS', ci.src, hu.src, 'inferred', 0.9,
               'handle->username holder', count(*), min(ci.t_first), max(ci.t_last), NULL, NULL,
               list_slice(list(ci.dst), 1, 50), ['{rk}'], '{{}}'
        FROM edges ci JOIN edges hu ON hu.type = 'HAS_USERNAME' AND hu.dst = 'username:' || substr(ci.dst, 7)
        WHERE ci.type = 'CONTAINS_IDENTIFIER' AND ci.dst LIKE 'id:tg:%' AND ci.src LIKE '%/msg:%'
        GROUP BY ci.src, hu.src""")
    out["NAME_HISTORY_LINK"] = _insert_edges(store, f"""
        WITH h AS (SELECT dst, list(DISTINCT src ORDER BY src) AS holders FROM edges WHERE type = 'HAS_USERNAME'
                   GROUP BY dst HAVING count(DISTINCT src) BETWEEN 2 AND 50),
        p AS (SELECT dst, unnest(holders) AS a, holders FROM h),
        q AS (SELECT dst, a, unnest(holders) AS b FROM p)
        SELECT a || '-[NAME_HISTORY_LINK]->' || b, 'NAME_HISTORY_LINK', a, b, 'observed', 1.0,
               'shared username over time', 1, NULL::BIGINT, NULL::BIGINT, NULL, NULL, [dst], ['{rk}'], '{{}}'
        FROM q WHERE a < b""")
    out["SAME_DOCUMENT_LINEAGE"] = _insert_edges(store, f"""
        WITH m AS (SELECT e.src AS file, json_extract_string(n.props, '$.xmpDocumentID') AS xmp
                   FROM edges e JOIN nodes n ON n.key = e.dst
                   WHERE e.type = 'HAS_METADATA' AND json_extract_string(n.props, '$.xmpDocumentID') IS NOT NULL),
        g AS (SELECT xmp FROM m GROUP BY xmp HAVING count(*) BETWEEN 2 AND 50)
        SELECT a.file || '-[SAME_DOCUMENT_LINEAGE]->' || b.file, 'SAME_DOCUMENT_LINEAGE', a.file, b.file,
               'file_declared', 0.7, 'xmpMM:DocumentID', 1, NULL::BIGINT, NULL::BIGINT, NULL, NULL,
               ['meta:' || a.file, 'meta:' || b.file], ['{rk}'], json_object('xmpDocumentID', a.xmp)::VARCHAR
        FROM m a JOIN m b ON a.xmp = b.xmp AND a.file < b.file JOIN g ON g.xmp = a.xmp""")
    out["SAME_FILE_AS"] = _insert_edges(store, f"""
        WITH f AS (SELECT key, json_extract_string(props, '$.documentId') AS doc FROM nodes
                   WHERE list_contains(labels, 'File') AND json_extract_string(props, '$.documentId') IS NOT NULL),
        r AS (SELECT doc, min(key) AS first FROM f GROUP BY doc HAVING count(*) > 1)
        SELECT r.first || '-[SAME_FILE_AS]->' || f.key, 'SAME_FILE_AS', r.first, f.key, 'observed', 1.0,
               'shared documentId', 1, NULL::BIGINT, NULL::BIGINT, NULL, NULL, ['documentId=' || r.doc], ['{rk}'], '{{}}'
        FROM f JOIN r USING (doc) WHERE f.key <> r.first""")
    return out


# =========================================================== coordination
EDGE_FOR_KIND = {"text": "IDENTICAL_POST", "neardup": "NEAR_DUP_POST", "forward": "CO_FORWARD",
                 "url": "CO_URL", "media": "CO_FINGERPRINT"}


def coordination(store, *, time_window: int = 60, min_participation: int = 2, max_p: float | None = 1e-3,
                 sync_seconds: int = 5, simhash_max_dist: int = 3, min_cluster_size: int = 3,
                 same_day_min: int = 3, same_day_max: int = 50, max_object_accounts: int = 1000,
                 log=print, **_ignored) -> dict:
    """CooRTweet in SQL with blocking, fan-out cap and Poisson null model; writes pair edges and clusters."""
    c = store.con
    W = int(time_window)
    rk = _run(store, "DetectionRun", f"coortweet-sql:w{W}:m{min_participation}", "tgo-coortweet-sql", "0.2",
              algorithm="coortweet", time_window=W, min_participation=min_participation, max_p=max_p,
              null_model="poisson", max_object_accounts=max_object_accounts, blocking="object+time-bucket")
    # 1. first share per (account, object); object fan-out cap
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE s1 AS
        SELECT object_id, any_value(kind) AS kind, account, arg_min(content, ts) AS content, min(ts) AS ts,
               min(ts) // {W} AS bucket
        FROM shares WHERE kind <> 'simhash' GROUP BY object_id, account""")
    dropped = c.execute(f"""SELECT count(*) FROM (SELECT object_id FROM s1 GROUP BY 1
                            HAVING count(*) > {int(max_object_accounts)})""").fetchone()[0]
    c.execute(f"""DELETE FROM s1 WHERE object_id IN (SELECT object_id FROM s1 GROUP BY 1
                                                   HAVING count(*) > {int(max_object_accounts)})""")
    # 2. bucketed band join -> raw co-share events
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE ev AS
        SELECT a.object_id, a.kind, a.account AS a, b.account AS b, abs(a.ts - b.ts) AS dt,
               least(a.ts, b.ts) AS t, a.content AS ca, b.content AS cb
        FROM s1 a JOIN s1 b ON a.object_id = b.object_id AND b.bucket BETWEEN a.bucket - 1 AND a.bucket + 1
        WHERE a.account < b.account AND abs(a.ts - b.ts) <= {W}""")
    # 3. SimHash near-duplicates: 4 x 16-bit blocks (pigeonhole for k <= 3)
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE sh AS
        SELECT ('0x' || substr(object_id, 9))::UBIGINT AS h, account, content, ts, ts // {W} AS bucket
        FROM shares WHERE kind = 'simhash'""")
    near = []
    for i in range(4):
        near.append(f"""
            SELECT a.account AS a, b.account AS b, abs(a.ts - b.ts) AS dt, least(a.ts, b.ts) AS t,
                   a.content AS ca, b.content AS cb
            FROM sh a JOIN sh b ON (a.h >> {16 * i}) & 65535 = (b.h >> {16 * i}) & 65535
                               AND b.bucket BETWEEN a.bucket - 1 AND a.bucket + 1
            WHERE a.account < b.account AND abs(a.ts - b.ts) <= {W}
              AND bit_count(xor(a.h, b.h)) BETWEEN 1 AND {int(simhash_max_dist)}""")
    c.execute(f"""INSERT INTO ev SELECT DISTINCT 'neardup:' || least(ca, cb) || '|' || greatest(ca, cb),
                  'neardup', a, b, dt, t, ca, cb FROM ({' UNION ALL '.join(near)})""")
    # 4. pair aggregation + min participation
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE pk AS
        SELECT a, b, kind, count(DISTINCT object_id) AS objs, min(dt) AS min_dt, min(t) AS t_first, max(t) AS t_last,
               list_slice(list(DISTINCT ca) || list(DISTINCT cb), 1, 40) AS evidence
        FROM ev GROUP BY a, b, kind""")
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE pairs AS
        SELECT a, b, sum(objs) AS observed, min(min_dt) AS min_dt FROM pk GROUP BY a, b
        HAVING sum(objs) >= {int(min_participation)}""")
    # 5. Poisson null model: lambda = sum_o n_a(o) n_b(o) * 2W / T
    span = max(1, c.execute("SELECT max(ts) - min(ts) FROM shares").fetchone()[0] or 1)
    c.execute("""
        CREATE OR REPLACE TEMP TABLE cnt AS
        SELECT account, object_id, count(*) AS n FROM shares WHERE kind <> 'simhash' GROUP BY 1, 2""")
    rows = c.execute(f"""
        SELECT p.a, p.b, p.observed, coalesce(sum(x.n * y.n), 0) * {2 * W} / {span} AS lam
        FROM pairs p
        LEFT JOIN cnt x ON x.account = p.a
        LEFT JOIN cnt y ON y.account = p.b AND y.object_id = x.object_id
        GROUP BY p.a, p.b, p.observed""").fetchall()
    keep = []
    for a, b, obs, lam in rows:
        pv = _poisson_sf(int(obs), float(lam or 0.0))
        if max_p is None or pv <= max_p:
            keep.append((a, b, float(lam or 0.0), pv))
    import pyarrow as pa

    kt = pa.table({"a": [k[0] for k in keep], "b": [k[1] for k in keep], "lam": [k[2] for k in keep],
                   "pv": [k[3] for k in keep]})
    store.con.register("kt_arrow", kt)   # explicit: replacement scans are off
    c.execute("CREATE OR REPLACE TEMP TABLE sig AS SELECT * FROM kt_arrow")
    kind_case = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in EDGE_FOR_KIND.items())
    n_edges = _insert_edges(store, f"""
        WITH tot AS (SELECT a, b, sum(objs) AS total FROM pk GROUP BY a, b)
        SELECT pk.a || '-[' || (CASE pk.kind {kind_case} END) || ']->' || pk.b, CASE pk.kind {kind_case} END,
               pk.a, pk.b, 'inferred', round(least(0.95, 0.45 + 0.1 * pk.objs + 0.05 * tot.total), 3),
               'coortweet', 1, pk.t_first, pk.t_last, NULL, NULL, pk.evidence, ['{rk}'],
               json_object('sharedObjects', pk.objs, 'minDeltaSec', pk.min_dt,
                           'expectedByChance', round(sig.lam, 4), 'pValue', sig.pv)::VARCHAR
        FROM pk JOIN sig ON sig.a = pk.a AND sig.b = pk.b JOIN tot ON tot.a = pk.a AND tot.b = pk.b""")
    n_sync = _insert_edges(store, f"""
        SELECT pk.a || '-[SYNC_POST]->' || pk.b, 'SYNC_POST', pk.a, pk.b, 'inferred', 0.9, 'coortweet+sync', 1,
               pk.t_first, pk.t_last, NULL, NULL, list_slice(pk.evidence, 1, 20), ['{rk}'],
               json_object('minDeltaSec', pk.min_dt)::VARCHAR
        FROM pk JOIN sig ON sig.a = pk.a AND sig.b = pk.b
        WHERE pk.kind = 'text' AND pk.min_dt <= {int(sync_seconds)}""")
    # 6. clusters = connected components of significant pairs (union-find; pairs are few)
    parent: dict[str, str] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    adj = defaultdict(set)
    for a, b, *_ in keep:
        parent[find(a)] = find(b)
        adj[a].add(b)
        adj[b].add(a)
    comps = defaultdict(list)
    for x in list(parent):
        comps[find(x)].append(x)
    g = Graph()
    nclu = 0
    with g.activity("DetectionRun", rk[4:]):
        for members in comps.values():
            if len(members) < min_cluster_size:
                continue
            members.sort()
            ck = f"cluster:coord:{K.shortuuid(*members)}"
            comp = set(members)
            density = sum(len(adj[m] & comp) for m in members) / (len(members) * (len(members) - 1))
            g.node(ck, "CoordinationCluster", label=f"Coordination cluster ({len(members)})", size=len(members),
                   algorithm="coortweet", density=round(density, 3), qualityScore=round(density, 3),
                   groupKind="coordination_cluster", members=members[:1000])
            g.edge("GENERATED_BY", ck, rk)
            for m in members:
                share = len(adj[m] & comp) / (len(members) - 1)
                g.edge("IN_CLUSTER", m, ck, conf=round(0.5 + 0.45 * share, 3), method="coortweet",
                       evidence=[rk], membershipScore=round(share, 3), role="core" if share >= 0.5 else "peripheral")
            nclu += 1
    store.flush(g)
    # 7. SAME_CREATION_DATE
    n_day = _insert_edges(store, f"""
        WITH ch AS (SELECT key, json_extract(props, '$.createdAt')::BIGINT // 86400 AS day FROM nodes
                    WHERE list_contains(labels, 'Chat') AND json_extract(props, '$.createdAt') IS NOT NULL),
        d AS (SELECT day FROM ch GROUP BY day HAVING count(*) BETWEEN {int(same_day_min)} AND {int(same_day_max)})
        SELECT a.key || '-[SAME_CREATION_DATE]->' || b.key, 'SAME_CREATION_DATE', a.key, b.key, 'inferred', 0.4,
               'createdAt day bucket', 1, a.day * 86400, a.day * 86400, NULL, NULL, [a.key, b.key], ['{rk}'],
               json_object('day', a.day * 86400)::VARCHAR
        FROM ch a JOIN ch b ON a.day = b.day AND a.key < b.key JOIN d ON d.day = a.day""")
    return {"shares": c.execute("SELECT count(*) FROM shares").fetchone()[0], "candidate_pairs": len(rows),
            "significant_pairs": len(keep), "edges": n_edges, "sync": n_sync, "clusters": nclu,
            "same_creation_date": n_day, "objects_dropped_fanout": dropped}


def _poisson_sf(k: int, lam: float) -> float:
    if k <= 0:
        return 1.0
    try:
        from scipy.stats import poisson

        return float(poisson.sf(k - 1, lam))
    except ImportError:
        from ..analysis.coordination import poisson_sf

        return poisson_sf(k, lam)


# ======================================================= perceptual near-dups
def near_duplicates(store, *, threshold: int | None = None, k: int = 16, exact_limit: int = 2_000_000,
                    log=print) -> dict:
    """All perceptual near-duplicate pairs (Hamming <= threshold) across every fingerprint in the store.

    Exact (Faiss IndexBinaryFlat range search) up to ``exact_limit`` distinct fingerprints, near-exact
    multi-index hashing beyond; every candidate is confirmed with an exact Hamming distance.  Without
    Faiss a Python band index is used (exact, slower).  ``k`` is kept for config compatibility.
    """
    """Perceptual near-duplicates across all fingerprints (Faiss HNSW, band-index fallback)."""
    rows = store.con.execute("""SELECT key, json_extract_string(props, '$.algorithm'),
                                       json_extract_string(props, '$.hashHex') FROM nodes
                                WHERE list_contains(labels, 'MediaFingerprint')""").fetchall()
    by_algo = defaultdict(list)
    for key, algo, hx in rows:
        if hx:
            by_algo[algo].append((key, hx))
    pairs = []
    method = "none"
    for algo, items in by_algo.items():
        bits = len(items[0][1]) * 4
        thr = threshold if threshold is not None else (31 if algo == "pdq" else 10)
        if len(items) < 2:
            continue
        try:
            import faiss
            import numpy as np

            codes = np.frombuffer(b"".join(bytes.fromhex(h) for _, h in items), dtype=np.uint8).reshape(len(items), bits // 8)
            if len(items) <= exact_limit:
                # exact: brute-force popcount range search (multi-core); radius is exclusive in faiss
                index, method = faiss.IndexBinaryFlat(bits), "faiss-range-flat"
            else:
                # near-exact at very large n: multi-index hashing + exact Hamming confirmation below
                index, method = faiss.IndexBinaryMultiHash(bits, 8, bits // 8), "faiss-range-multihash"
                index.nflip = 2
            index.add(codes)
            for lo in range(0, len(items), 50_000):
                lims, D, I = index.range_search(codes[lo:lo + 50_000], thr + 1)
                for qi in range(len(lims) - 1):
                    i_ = lo + qi
                    for d, j_ in zip(D[lims[qi]:lims[qi + 1]], I[lims[qi]:lims[qi + 1]]):
                        if j_ > i_ and 0 < d <= thr:
                            pairs.append((items[i_][0], items[j_][0], int(d), thr, algo))
        except ImportError:
            if len(items) > 200_000:
                log(f"  near-duplicates: {len(items)} {algo} hashes and no faiss; skipped (pip install faiss-cpu)")
                continue
            from ..extractors import PHash, near_pairs

            hs = {key: PHash(algo, hx, bits, None, thr) for key, hx in items}
            for a, b in near_pairs(hs):
                d = bin(int(hs[a].hex, 16) ^ int(hs[b].hex, 16)).count("1")
                if 0 < d <= thr:
                    pairs.append((a, b, d, thr, algo))
            method = "band-index"
    if not pairs:
        return {"fingerprints": len(rows), "pairs": 0, "method": method}
    rk = _run(store, "DetectionRun", f"neardup:{method}", f"tgo-neardup-{method}", "0.3",
              exact_limit=exact_limit)
    import pyarrow as pa

    t = pa.table({"fa": [p[0] for p in pairs], "fb": [p[1] for p in pairs], "d": [p[2] for p in pairs],
                  "thr": [p[3] for p in pairs], "algo": [p[4] for p in pairs]})
    store.con.register("t_arrow", t)   # explicit: replacement scans are off
    store.con.execute("CREATE OR REPLACE TEMP TABLE ndp AS SELECT * FROM t_arrow")
    n = _insert_edges(store, f"""
        WITH m AS (
            SELECT ndp.*, a.dst AS ma, b.dst AS mb FROM ndp
            JOIN edges a ON a.src = ndp.fa AND a.type = 'FINGERPRINT_OF'
            JOIN edges b ON b.src = ndp.fb AND b.type = 'FINGERPRINT_OF')
        SELECT least(ma, mb) || '-[NEAR_DUPLICATE_OF]->' || greatest(ma, mb), 'NEAR_DUPLICATE_OF',
               least(ma, mb), greatest(ma, mb), 'inferred', round(1 - 0.5 * d / thr, 3), algo, 1,
               NULL::BIGINT, NULL::BIGINT, NULL, NULL, [fa, fb], ['{rk}'],
               json_object('metric', 'hamming', 'distance', d, 'threshold', thr)::VARCHAR
        FROM m WHERE ma <> mb""")
    return {"fingerprints": len(rows), "pairs": len(pairs), "edges": n, "method": method}


# ============================================================ communities
def communities(store, *, seeds: int = 10, resolution: float = 1.0, min_size: int = 3, min_stability: float = 0.6,
                edge_weights: dict[str, float] | None = None, deterministic: bool = True, log=print) -> dict:
    """NetworKit PLM (or NetworkX) multi-seed Louvain on the actor graph, with stability and roles."""
    import numpy as np
    import pyarrow as pa

    from ..analysis.communities import ACTOR_EDGES

    weights = edge_weights or ACTOR_EDGES
    wt = pa.table({"type": list(weights), "w": [float(v) for v in weights.values()]})
    store.con.register("wt_arrow", wt)   # explicit: replacement scans are off
    c = store.con
    c.execute("CREATE OR REPLACE TEMP TABLE wt AS SELECT * FROM wt_arrow")
    arr = c.execute("""
        SELECT least(src_id, dst_id) AS u, greatest(src_id, dst_id) AS v,
               sum(wt.w * conf * (1 + sqrt(greatest(count, 1) - 1))) AS w
        FROM edges JOIN wt USING (type) WHERE src_id <> dst_id GROUP BY 1, 2""")
    arr = arr.to_arrow_table() if hasattr(arr, "to_arrow_table") else arr.fetch_arrow_table()
    if arr.num_rows == 0:
        return {"nodes": 0, "edges": 0, "clusters": 0}
    u = arr.column("u").to_numpy()
    v = arr.column("v").to_numpy()
    w = arr.column("w").to_numpy().astype(np.float64)
    ids, inv = np.unique(np.concatenate([u, v]), return_inverse=True)
    uu, vv = inv[: len(u)], inv[len(u):]
    n = len(ids)
    t0 = time.perf_counter()
    try:
        import networkit as nk

        G = nk.graph.GraphFromCoo((w, (uu, vv)), n=n, weighted=True, directed=False)
        if deterministic:      # parallel PLM is not reproducible even with a fixed seed
            nk.setNumberOfThreads(1)
        runs = []
        for s in range(seeds):
            nk.setSeed(s, False)
            plm = nk.community.PLM(G, refine=True, gamma=resolution)
            plm.run()
            runs.append(np.asarray(plm.getPartition().getVector()))
        modularity = nk.community.Modularity().getQuality(nk.structures.Partition(n, list(map(int, runs[0]))), G) \
            if hasattr(nk.structures, "Partition") else None
        tool, version = "networkit.PLM", nk.__version__
    except ImportError:
        import networkx as nx

        H = nx.Graph()
        H.add_weighted_edges_from(zip(uu.tolist(), vv.tolist(), w.tolist()))
        runs = []
        for s in range(seeds):
            lab = np.zeros(n, dtype=np.int64)
            for ci, comm in enumerate(nx.community.louvain_communities(H, weight="weight", resolution=resolution, seed=s)):
                lab[list(comm)] = ci
            runs.append(lab)
        modularity = None
        tool, version = "networkx.louvain_communities", nx.__version__
    elapsed = time.perf_counter() - t0
    rk = _run(store, "DetectionRun", f"louvain-scale:r{resolution}:s{seeds}", tool, version, algorithm="louvain",
              seeds=seeds, resolution=resolution, min_size=min_size, min_stability=min_stability,
              edge_weights=weights, seconds=round(elapsed, 2),
              reproducible=bool(deterministic) or tool.startswith("networkx"))
    if modularity is not None:
        c.execute("UPDATE staging_nodes SET props = json_merge_patch(props, ?::JSON) WHERE key = ?",
                  [json.dumps({"qualityScore": round(float(modularity), 4)}), rk])
    ref = runs[0]
    ref_sizes = Counter(ref.tolist())
    other_sizes = [Counter(r.tolist()) for r in runs[1:]]
    members_by = defaultdict(list)
    for idx, lab in enumerate(ref.tolist()):
        if ref_sizes[lab] >= min_size:
            members_by[lab].append(idx)
    keymap = dict(c.execute("SELECT id, key FROM nodes WHERE id IN (SELECT unnest(?))", [ids.tolist()]).fetchall())
    mem_rows, clusters, rejected = [], 0, 0
    g = Graph()
    for lab, members in members_by.items():
        A = set(members)
        jacs, co = [], np.zeros(len(members))
        for r, sizes in zip(runs[1:], other_sizes):
            labs = r[members]
            best, cnt = Counter(labs.tolist()).most_common(1)[0]
            jacs.append(cnt / (len(A) + sizes[best] - cnt))
            co += (labs == best)
        stability = float(np.mean(jacs)) if jacs else 1.0
        if stability < min_stability:
            rejected += 1
            continue
        mkeys = [keymap[int(ids[m])] for m in members]
        ck = f"cluster:louvain:{K.shortuuid(*sorted(mkeys))}"
        g.node(ck, "CommunityCluster", label=f"Community ({len(members)})", size=len(members), algorithm="louvain",
               stabilityScore=round(stability, 3), groupKind="community_cluster", members=sorted(mkeys)[:1000])
        g.edge("GENERATED_BY", ck, rk, run=rk)
        cof = (1 + co) / (1 + len(runs) - 1)
        for mk, f in zip(mkeys, cof.tolist()):
            mem_rows.append((mk, ck, round(min(0.95, f * stability), 3), round(f, 3)))
        clusters += 1
    store.flush(g)
    if mem_rows:
        mt = pa.table({"m": [r[0] for r in mem_rows], "ck": [r[1] for r in mem_rows],
                       "conf": [r[2] for r in mem_rows], "co": [r[3] for r in mem_rows]})
        store.con.register("mt_arrow", mt)   # explicit: replacement scans are off
        c.execute("CREATE OR REPLACE TEMP TABLE mt AS SELECT * FROM mt_arrow")
        _insert_edges(store, f"""
            SELECT m || '-[IN_CLUSTER]->' || ck, 'IN_CLUSTER', m, ck, 'inferred', conf, 'louvain', 1,
                   NULL::BIGINT, NULL::BIGINT, NULL, NULL, ['{rk}'], ['{rk}'],
                   json_object('coAssignmentFrequency', co)::VARCHAR FROM mt""")
    # roles from the forward graph (SQL; betweenness omitted at scale)
    c.execute("""CREATE OR REPLACE TEMP TABLE roles AS
        WITH i AS (SELECT dst AS k, sum(count) AS fin FROM edges WHERE type = 'FORWARDS_FROM' GROUP BY 1),
             o AS (SELECT src AS k, sum(count) AS fout FROM edges WHERE type = 'FORWARDS_FROM' GROUP BY 1),
             j AS (SELECT coalesce(i.k, o.k) AS k, coalesce(fin, 0) AS fin, coalesce(fout, 0) AS fout
                   FROM i FULL JOIN o ON i.k = o.k)
        SELECT k, fin, fout, (SELECT max(fin) FROM j) AS mx FROM j""")
    roles = c.execute("""SELECT k, fin, fout, mx FROM roles""").fetchall()
    g = Graph()
    for k, fin, fout, mx in roles:
        r = []
        if fin >= 0.5 * (mx or 1) and fin >= 3:
            r.append("originator")
        if fout >= 2:
            r.append("amplifier")
        if r:
            g.node(k, networkRoles=r, networkRoleStatus="inferred", forwardedByCount=int(fin), forwardsCount=int(fout))
    store.flush(g)
    return {"nodes": n, "edges": len(u), "clusters": clusters, "rejected_unstable": rejected,
            "engine": tool, "seconds": round(elapsed, 2)}


# =============================================================== identity
def identity(store, decisions=None, *, min_score: float = 0.3, auto_accept: float | None = None,
             weights: dict[str, float] | None = None, strong_identifiers=None, min_bio_len: int = 15,
             max_evidence_accounts: int = 50, log=print) -> dict:
    """SQL candidate generation (degree-capped evidence keys, IDF, noisy-OR) + judgements."""
    import pyarrow as pa

    from ..analysis.identity import STRONG_IDENTIFIERS, WEIGHTS, Resolver

    weights = weights or WEIGHTS
    strong = list(strong_identifiers or STRONG_IDENTIFIERS)
    c = store.con
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE content AS
        SELECT s.src AS acct, s.dst AS item FROM edges s WHERE s.type = 'SENT' AND s.src LIKE 'user:%'
        UNION ALL
        SELECT s.src, m.dst FROM edges s JOIN edges m ON m.src = s.dst AND m.type = 'HAS_MEDIA'
        WHERE s.type = 'SENT' AND s.src LIKE 'user:%'""")
    st = pa.table({"lab": strong})
    store.con.register("st_arrow", st)   # explicit: replacement scans are off
    c.execute("CREATE OR REPLACE TEMP TABLE strong AS SELECT * FROM st_arrow")
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE feat AS
        SELECT 'shared_device_serial' AS feature, e.dst AS evidence, ct.acct FROM content ct
            JOIN edges e ON e.src = ct.item AND e.type = 'CAPTURED_WITH'
        UNION SELECT 'shared_identifier', e.dst, ct.acct FROM content ct
            JOIN edges e ON e.src = ct.item AND e.type = 'CONTAINS_IDENTIFIER'
            JOIN nodes n ON n.key = e.dst WHERE len(list_intersect(n.labels, (SELECT list(lab) FROM strong))) > 0
        UNION SELECT 'same_personal_channel', dst, src FROM edges WHERE type = 'FEATURES' AND src LIKE 'user:%'
        UNION SELECT 'same_phone', 'phone:' || json_extract_string(props, '$.phone'), key FROM nodes
            WHERE key LIKE 'user:%' AND json_extract_string(props, '$.phone') IS NOT NULL
        UNION SELECT 'same_bio_text', 'bio:' || lower(trim(json_extract_string(n.props, '$.about'))), e.src
            FROM edges e JOIN nodes n ON n.key = e.dst
            WHERE e.type = 'HAS_PROFILE' AND length(trim(json_extract_string(n.props, '$.about'))) >= {int(min_bio_len)}
        UNION SELECT 'shared_avatar_hash', fp.src, av.src FROM edges av
            JOIN edges fp ON fp.dst = av.dst AND fp.type = 'FINGERPRINT_OF'
            WHERE av.type = 'HAS_AVATAR' AND av.src LIKE 'user:%'
        UNION SELECT 'shared_avatar_hash', nd.id, av.src FROM edges nd
            JOIN edges av ON av.type = 'HAS_AVATAR' AND (av.dst = nd.src OR av.dst = nd.dst)
            WHERE nd.type = 'NEAR_DUPLICATE_OF' AND av.src LIKE 'user:%'
        UNION SELECT 'username_history', id, src FROM edges WHERE type = 'NAME_HISTORY_LINK' AND src LIKE 'user:%'
        UNION SELECT 'username_history', id, dst FROM edges WHERE type = 'NAME_HISTORY_LINK' AND dst LIKE 'user:%'
    """)
    wtab = pa.table({"feature": list(weights), "w": [float(x) for x in weights.values()]})
    store.con.register("wtab_arrow", wtab)   # explicit: replacement scans are off
    c.execute("CREATE OR REPLACE TEMP TABLE fw AS SELECT * FROM wtab_arrow")
    capped = c.execute(f"""SELECT count(*) FROM (SELECT feature, evidence FROM feat GROUP BY 1, 2
                           HAVING count(DISTINCT acct) > {int(max_evidence_accounts)})""").fetchone()[0]
    cands = c.execute(f"""
        WITH deg AS (SELECT feature, evidence, count(DISTINCT acct) AS n FROM feat GROUP BY 1, 2
                     HAVING count(DISTINCT acct) BETWEEN 2 AND {int(max_evidence_accounts)}),
        pe AS (SELECT a.acct AS a, b.acct AS b, a.feature, a.evidence, fw.w * least(1.0, 2.0 / deg.n) AS w
               FROM feat a JOIN feat b ON a.feature = b.feature AND a.evidence = b.evidence AND a.acct < b.acct
               JOIN deg ON deg.feature = a.feature AND deg.evidence = a.evidence
               JOIN fw ON fw.feature = a.feature),
        pf AS (SELECT a, b, feature, max(w) AS w, list(DISTINCT evidence) AS ev FROM pe GROUP BY 1, 2, 3)
        SELECT a, b, round(1 - exp(sum(ln(greatest(1e-12, 1 - w)))), 3) AS score,
               list(feature ORDER BY feature) AS features, flatten(list(ev)) AS evidence
        FROM pf GROUP BY a, b HAVING 1 - exp(sum(ln(greatest(1e-12, 1 - w)))) >= {float(min_score)}
        ORDER BY score DESC""").fetchall()
    # judgements run in memory on the (small) identity subgraph, then flow back to the store
    existing = [k for (k,) in c.execute("""SELECT key FROM nodes WHERE list_contains(labels, 'IdentityJudgement')
                                           OR list_contains(labels, 'PersonCluster')""").fetchall()]
    accts = {x for a, b, *_ in cands for x in (a, b)} | {d[k] for d in (decisions or []) for k in ("source", "target")}
    sub = store.subgraph(set(existing) | accts) if (existing or accts) else Graph()
    res = Resolver(sub)
    out = {"candidates": len(cands), "decisions": [], "auto_accepted": 0, "evidence_keys_capped": capped}
    with sub.activity("DetectionRun", f"xref:noisy-or-sql:min{min_score}", agent={
            "tool": "tgo-xref-sql", "version": "0.2 (nomenklatura judgement model)", **AGENT},
            algorithm="noisy_or_evidence", weights=weights, min_score=min_score, auto_accept=auto_accept,
            max_evidence_accounts=max_evidence_accounts):
        for a, b, score, feats, ev in cands:
            res.suggest(a, b, float(score), feats, ev, "tgo-xref:noisy-or")
            if auto_accept is not None and score >= auto_accept:
                out["decisions"].append(res.decide(a, b, "positive", f"policy:auto>={auto_accept}"))
                out["auto_accepted"] += 1
    if decisions:
        with sub.activity("CollectionRun", "analyst-judgements", source="analyst"):
            for d in decisions:
                out["decisions"].append(res.decide(d["source"], d["target"], d["judgement"], d["decidedBy"],
                                                   d.get("legalBasis"), d.get("rationale")))
    store.replace(sub)   # sub was loaded from the store: replace, don't re-merge (counts would double)
    return out
