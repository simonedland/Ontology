"""Drop-folder ingest: any file put into the inbox is stored, then moved to inbox/done (or inbox/failed).

Accepted: *.ndjson / *.jsonl (records), *.json (records, or a Telegram Desktop result.json).
Files ending in .tmp or .part are ignored, so write under such a name and rename when complete.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from pathlib import Path

log = logging.getLogger("tgstore.inbox")
SUFFIXES = {".ndjson", ".jsonl", ".json"}


def read_file(path: Path):
    """Yield records from one inbox file."""
    if path.suffix in (".ndjson", ".jsonl"):
        with path.open(encoding="utf-8-sig") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
        return
    doc = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(doc, dict) and "kind" not in doc and ("messages" in doc or "chats" in doc):
        from .sources.desktop import records_from_export

        yield from records_from_export(doc)
    elif isinstance(doc, dict) and isinstance(doc.get("records"), list):
        yield from doc["records"]
    else:
        yield from doc if isinstance(doc, list) else [doc]


class Inbox:
    def __init__(self, ingest, folder: str | Path, interval: float = 2.0):
        self.ingest, self.dir, self.interval = ingest, Path(folder), interval
        self.done, self.failed = self.dir / "done", self.dir / "failed"
        self._seen: dict[Path, tuple] = {}
        self._stop = threading.Event()

    def start(self) -> Inbox:
        for d in (self.dir, self.done, self.failed):
            d.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._run, name="inbox", daemon=True).start()
        log.info("watching %s", self.dir)
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.scan()
            except Exception:  # keep watching whatever happens
                log.exception("inbox scan failed")

    def ready(self) -> list[Path]:
        """Files whose size and mtime did not change since the previous scan (fully written)."""
        out, now = [], {}
        for p in sorted(self.dir.rglob("*")):
            if not p.is_file() or p.suffix not in SUFFIXES or self.done in p.parents or self.failed in p.parents:
                continue
            st = p.stat()
            now[p] = (st.st_size, st.st_mtime_ns)
            if self._seen.get(p) == now[p]:
                out.append(p)
        self._seen = now
        return out

    def scan(self) -> None:
        for path in self.ready():
            stamp = time.strftime("%Y%m%dT%H%M%S")
            rel = path.relative_to(self.dir).as_posix().replace("/", "__")
            try:
                counts = self.ingest(read_file(path))
                path.replace(self.done / f"{stamp}-{rel}")
                log.info("ingested %s: %s", rel, counts)
            except Exception:
                path.replace(self.failed / f"{stamp}-{rel}")
                (self.failed / f"{stamp}-{rel}.error.txt").write_text(traceback.format_exc())
                log.exception("failed to ingest %s", rel)
