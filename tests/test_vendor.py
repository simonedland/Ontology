"""Vendored browser assets are pinned, licensed and byte-identical to the manifest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "tgo" / "viz" / "vendor"


def test_manifest_matches_vendored_bytes():
    manifest = json.loads((VENDOR / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["files"]
    for f in manifest["files"]:
        data = (VENDOR / f["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == f["sha256"], f"{f['file']} changed; re-pin via scripts/vendor_fetch.py"
        assert (VENDOR / f["license_file"]).read_text(encoding="utf-8").strip(), f"missing licence for {f['name']}"
        assert f["integrity"].startswith("sha512-")


def test_every_vendored_script_is_in_manifest():
    listed = {f["file"] for f in json.loads((VENDOR / "MANIFEST.json").read_text(encoding="utf-8"))["files"]}
    on_disk = {p.name for p in VENDOR.iterdir() if p.suffix in {".js", ".mjs", ".css"}}
    assert on_disk <= listed, f"unpinned vendored files: {sorted(on_disk - listed)}"
