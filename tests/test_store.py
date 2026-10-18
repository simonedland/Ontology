from concurrent.futures import ThreadPoolExecutor


def msg(i, **kw):
    return {"kind": "message", "chat_id": -100, "id": i, "user_id": 1, "date": 1_700_000_000 + i, "text": f"m{i}", **kw}


def count(store, table):
    with store.pool.connection() as conn:
        return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


def versions(store, table):
    with store.pool.connection() as conn:
        return conn.execute(f"SELECT xmin::text AS v FROM {table} ORDER BY 1").fetchall()


def test_ingest_is_idempotent_and_skips_noop_updates(store):
    batch = [msg(i, user={"username": "a"}) for i in range(100)]
    store.ingest(batch)
    before = {t: versions(store, t) for t in ("messages", "users", "memberships")}
    store.ingest(batch)
    assert count(store, "messages") == 100
    # re-sent data wrote no new row versions (xmin is the id of the transaction that wrote the row)
    assert {t: versions(store, t) for t in before} == before


def test_partial_edit_keeps_existing_fields(store):
    store.ingest([msg(1, extra={"a": 1})])
    store.ingest([{"kind": "message", "chat_id": -100, "id": 1, "text": "edited", "edit_date": 1_800_000_000,
                   "extra": {"b": 2}}])
    (m,) = store.chat_messages(-100)
    assert (m["user_id"], m["text"], m["extra"]) == (1, "edited", {"a": 1, "b": 2})
    assert m["date"] is not None and m["edit_date"] is not None


def test_profile_changes_are_kept_in_history(store):
    store.ingest([{"kind": "user", "id": 1, "username": "a", "first_name": "A"}])
    store.ingest([{"kind": "user", "id": 1, "first_name": "A"}])  # no change
    store.ingest([{"kind": "user", "id": 1, "username": "b"}])
    u = store.user(1)
    assert (u["username"], u["first_name"]) == ("b", "A")
    assert [h["username"] for h in u["history"]] == ["a"]


def test_messages_record_membership_seen_range(store):
    store.ingest([msg(1), msg(5), {"kind": "membership", "chat_id": -100, "user_id": 1, "status": "admin"}])
    store.ingest([msg(3)])
    (m,) = store.chat_members(-100)
    assert m["status"] == "admin"
    assert m["first_seen"].timestamp() == 1_700_000_001 and m["last_seen"].timestamp() == 1_700_000_005


def test_messages_create_stub_users_and_chats(store):
    store.ingest([msg(1, user_id=77)])
    assert store.user(77)["chats"][0]["chat_id"] == -100
    assert store.chat(-100) is not None
    store.ingest([{"kind": "user", "id": 77, "username": "late"}])
    assert store.user(77)["username"] == "late" and store.user(77)["history"] == []


def test_reads(store):
    store.ingest([msg(i, text=f"hello number {i}") for i in range(1, 11)] + [msg(11, user_id=2, text="other")])
    assert [m["id"] for m in store.chat_messages(-100, limit=3)] == [11, 10, 9]
    assert [m["id"] for m in store.chat_messages(-100, before_id=9, limit=2)] == [8, 7]
    page = store.messages(user_id=1, limit=4)
    assert [m["id"] for m in page] == [10, 9, 8, 7]
    assert [m["id"] for m in store.messages(user_id=1, before=page[-1]["date"], limit=2)] == [6, 5]
    assert {m["id"] for m in store.search("number", user_id=1, limit=100)} == set(range(1, 11))
    assert store.search("other")[0]["user_id"] == 2
    assert [m["id"] for m in store.search("number", newest=True, limit=2)] == [10, 9]


def test_concurrent_overlapping_batches(store):
    """Workers sending overlapping users and memberships at once must not deadlock or lose rows."""
    def batch(w):
        return [msg(w * 1000 + i, user_id=i % 20, user={"username": f"u{i % 20}"}) for i in range(500)]

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda w: store.ingest(batch(w)), range(16)))
    assert count(store, "messages") == 16 * 500
    assert count(store, "users") == 20
    assert count(store, "memberships") == 20
