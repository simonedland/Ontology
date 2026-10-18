"""Offline guard: TGO must operate with no internet access.

``python -m tgo.offline [paths...]`` statically scans the package (and any
generated HTML) for things that would reach the network at runtime: remote
``<script src>``/``<link href>``/CSS ``@import``/``url()``, Python HTTP clients,
and model loaders that download weights on first use. Namespace IRIs and
citation links in docstrings/comments are fine — they are identifiers, never
dereferenced.

:func:`block_network` patches ``socket`` so any connection attempt raises;
the smoke test runs the whole pipeline under it.
"""
from __future__ import annotations

import re
import socket
import sys
from contextlib import contextmanager
from pathlib import Path

PKG = Path(__file__).resolve().parent

# HTML/CSS/JS that the browser would fetch.
_WEB = [
    (re.compile(r"<script[^>]+src\s*=\s*['\"]?(?:https?:)?//", re.I), "remote <script src>"),
    (re.compile(r"<link[^>]+href\s*=\s*['\"]?(?:https?:)?//", re.I), "remote <link href>"),
    (re.compile(r"<img[^>]+src\s*=\s*['\"]?(?:https?:)?//", re.I), "remote <img src>"),
    (re.compile(r"@import\s+(?:url\()?['\"]?(?:https?:)?//", re.I), "remote CSS @import"),
    (re.compile(r"url\(\s*['\"]?(?:https?:)?//", re.I), "remote CSS url()"),
    (re.compile(r"\b(?:fetch|import)\s*\(\s*['\"`](?:https?:)?//", re.I), "remote fetch()/import()"),
]
# Python code that talks to the network.
_PY = [
    (re.compile(r"^\s*(?:import|from)\s+(requests|httpx|urllib3|aiohttp|httplib2)\b", re.M), "HTTP client import"),
    # Heavy local AI is welcome; hosted AI / cloud APIs are not.
    (re.compile(r"^\s*(?:import|from)\s+(google|googleapiclient|vertexai|openai|anthropic|cohere|mistralai|"
                r"groq|replicate|elevenlabs|assemblyai|deepgram|boto3|botocore|azure|ibm_watson|clarifai)\b", re.M),
     "cloud AI/API SDK import"),
    (re.compile(r"^\s*(?:import|from)\s+(telethon|pyrogram)\b", re.M), "live Telegram client import"),
    (re.compile(r"\burllib\.request\b|\burlopen\s*\("), "urllib.request"),
    (re.compile(r"\bhf_hub_download\b|\bsnapshot_download\b"), "HuggingFace download"),
    (re.compile(r"spacy\.cli\.download|\bnltk\.download\b"), "model download"),
    (re.compile(r"WhisperModel\((?![^)]*local_files_only\s*=\s*True)", re.S), "WhisperModel without local_files_only=True"),
    (re.compile(r"\b(?:FORCE\s+)?INSTALL\s+['\"]?[a-z_]+['\"]?\s*(?:;|FROM|$)", re.M), "DuckDB INSTALL (downloads an extension)"),
    (re.compile(r"\bRapidOCR\(\s*\)"), "RapidOCR() without explicit local model paths"),
    (re.compile(r"from_pretrained\((?![^)]*local_files_only\s*=\s*True)", re.S), "from_pretrained without local_files_only=True"),
]
# The live MTProto collector is an explicitly online, separately invoked step.
_ALLOW_ONLINE = {"ingest/collector.py"}
_SKIP_DIRS = {"__pycache__", ".git", "node_modules"}


def scan(paths: list[Path] | None = None) -> list[tuple[str, int, str]]:
    hits = []
    for root in paths or [PKG]:
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for p in files:
            if _SKIP_DIRS & set(p.parts) or p.suffix not in {".py", ".html", ".htm", ".js", ".css", ".mjs"}:
                continue
            rel = p.relative_to(PKG).as_posix() if p.is_relative_to(PKG) else str(p)
            if rel in _ALLOW_ONLINE or rel == "offline.py":
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            # Vendored minified libraries mention URLs in licence banners / dead code paths;
            # only the HTML that loads them matters.
            rules = (_PY if p.suffix == ".py" else []) + ([] if "/vendor/" in "/" + rel else _WEB)
            for rx, why in rules:
                for m in rx.finditer(text):
                    hits.append((rel, text.count("\n", 0, m.start()) + 1, why))
            # DuckDB downloads extensions from extensions.duckdb.org (auto-install, or an explicit INSTALL)
            # in native code that the Python socket guard cannot see. A file that opens a connection must
            # also carry the lockdown, however the config dict is assembled.
            if p.suffix == ".py" and "duckdb.connect(" in text:
                missing = [g for g in _DUCKDB_GUARDS if g not in text]
                if missing:
                    m = text.index("duckdb.connect(")
                    hits.append((rel, text.count("\n", 0, m) + 1,
                                 f"duckdb.connect without the offline lockdown (missing: {', '.join(missing)})"))
    return hits


_DUCKDB_GUARDS = ("autoinstall_known_extensions", "http_proxy", "lock_configuration")


from ._env import OFFLINE_ENV, harden_env  # noqa: E402,F401  (re-exported)


class NetworkBlocked(RuntimeError):
    pass


@contextmanager
def block_network():
    """Make every outbound socket connection fail (DNS included)."""
    def deny(*a, **k):
        raise NetworkBlocked(f"network access attempted: {a[:2]!r}")

    saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)
    socket.socket.connect = deny  # type: ignore[assignment]
    socket.socket.connect_ex = deny  # type: ignore[assignment]
    socket.create_connection = deny  # type: ignore[assignment]
    socket.getaddrinfo = deny  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = saved


def main(argv: list[str]) -> int:
    hits = scan([Path(a).resolve() for a in argv] or None)
    for rel, line, why in hits:
        print(f"{rel}:{line}: {why}")
    print(f"offline check: {'FAIL' if hits else 'OK'} ({len(hits)} issue(s))")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
