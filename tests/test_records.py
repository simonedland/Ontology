import pytest

from tgstore.records import RecordError, normalize


def test_unknown_fields_go_to_extra_and_types_are_coerced():
    rows = normalize([{"kind": "message", "chat_id": "-100", "id": 5, "date": 0, "reactions": 3, "extra": {"a": 1}}])
    (m,) = rows["message"]
    assert m["chat_id"] == -100 and m["date"] == "1970-01-01T00:00:00+00:00"
    assert m["extra"] == {"a": 1, "reactions": 3}


def test_naive_dates_are_utc():
    (m,) = normalize([{"kind": "message", "chat_id": 1, "id": 1, "date": "2026-10-18T12:00:00"}])["message"]
    assert m["date"] == "2026-10-18T12:00:00+00:00"


def test_embedded_user_and_chat_become_records():
    rows = normalize([{"kind": "message", "chat_id": -1, "id": 1, "user_id": 7, "user": {"username": "a"},
                       "chat": {"title": "G"}}])
    assert rows["user"] == [{"id": 7, "username": "a", "extra": {}}]
    assert rows["chat"] == [{"id": -1, "title": "G", "extra": {}}]
    assert "user" not in rows["message"][0]["extra"]


def test_duplicates_in_a_batch_merge_later_non_null_wins():
    rows = normalize([
        {"kind": "message", "chat_id": 1, "id": 1, "user_id": 9, "text": "a", "extra": {"x": 1}},
        {"kind": "message", "chat_id": 1, "id": 1, "text": "b", "user_id": None, "extra": {"y": 2}},
    ])
    (m,) = rows["message"]
    assert (m["user_id"], m["text"], m["extra"]) == (9, "b", {"x": 1, "y": 2})


def test_rows_are_sorted_by_key():
    rows = normalize([{"kind": "user", "id": i} for i in (3, 1, 2)])
    assert [r["id"] for r in rows["user"]] == [1, 2, 3]


@pytest.mark.parametrize("rec, msg", [
    ({"kind": "message", "id": 1}, "chat_id"),
    ({"kind": "nope"}, "unknown kind"),
    ({"kind": "user", "id": "abc"}, "record 0"),
    ([1], "JSON object"),
])
def test_bad_records_are_rejected_with_position(rec, msg):
    with pytest.raises(RecordError, match=msg):
        normalize([rec])
