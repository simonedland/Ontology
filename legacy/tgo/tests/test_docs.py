"""The documentation stays true to the code."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / "docs").glob("*.md")) + [p for p in (ROOT / "README.md", ROOT / "CONTRIBUTING.md") if p.exists()]


def test_vocabulary_reference_is_current():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_docs.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_relative_links_resolve():
    broken = []
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if re.match(r"[a-z]+:", target) or target.startswith("#"):
                continue
            path = target.split("#", 1)[0].replace("%20", " ")
            if path and not (doc.parent / path).exists():
                broken.append(f"{doc.relative_to(ROOT)} -> {target}")
    assert not broken, broken


def test_referenced_source_files_exist():
    missing = []
    for doc in DOCS:
        for ref in set(re.findall(r"`((?:tgo|tests|scripts|examples)/[\w/.-]+\.(?:py|html|json|md))`",
                                  doc.read_text(encoding="utf-8"))):
            if not (ROOT / ref).exists():
                missing.append(f"{doc.relative_to(ROOT)} -> {ref}")
    assert not missing, missing


def test_documented_cli_commands_exist():
    from tgo import cli

    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()) as out:
        cli.main([])
    known = set(re.search(r"\{([\w,-]+)\}", out.getvalue()).group(1).split(","))
    used = set()
    for doc in DOCS:
        used |= set(re.findall(r"python -m tgo ([a-z][\w-]*)", doc.read_text(encoding="utf-8")))
    assert used <= known, f"docs mention unknown subcommands: {sorted(used - known)}"
