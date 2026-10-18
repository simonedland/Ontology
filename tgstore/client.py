"""Push records to a tgstore API. Standard library only: copy this file anywhere and use it.

    from client import push
    push([{"kind": "user", "id": 42, "username": "alice"}], url="http://127.0.0.1:8080")

or from a shell:  python client.py records.ndjson [more.ndjson ...]   (reads stdin if no files)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from itertools import islice

DEFAULT_URL = os.environ.get("TGSTORE_URL", "http://127.0.0.1:8080")


def _batches(records: Iterable[dict], size: int) -> Iterator[list[dict]]:
    it = iter(records)
    while batch := list(islice(it, size)):
        yield batch


def post(batch: list[dict], url: str = DEFAULT_URL, token: str | None = None, retries: int = 5) -> dict:
    """Send one batch; retries connection errors and 5xx with backoff. Returns the per-kind counts."""
    body = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in batch).encode()
    headers = {"Content-Type": "application/x-ndjson"}
    if token := token or os.environ.get("TGSTORE_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{url.rstrip('/')}/v1/records", data=body, headers=headers, method="POST")
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == retries:
                raise RuntimeError(f"tgstore rejected batch: {e.code} {e.read().decode(errors='replace')}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if attempt == retries:
                raise
        time.sleep(min(2**attempt, 30))
    raise AssertionError("unreachable")


def push(records: Iterable[dict], url: str = DEFAULT_URL, token: str | None = None, batch: int = 5000) -> dict:
    """Send any number of records in batches. Returns summed counts per kind."""
    total: dict[str, int] = {}
    for chunk in _batches(records, batch):
        for k, v in post(chunk, url, token).items():
            total[k] = total.get(k, 0) + v
    return total


def read_ndjson(lines: Iterable[str]) -> Iterator[dict]:
    for line in lines:
        if line.strip():
            yield json.loads(line)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print(json.dumps(push(read_ndjson(sys.stdin))))
    for name in sys.argv[1:]:
        with open(name, encoding="utf-8-sig") as f:
            print(name, json.dumps(push(read_ndjson(f))))
