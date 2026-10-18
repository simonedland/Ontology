"""Re-fetch and verify the pinned browser assets in ``tgo/viz/vendor``.

This is the *only* online step for the viz, run by a maintainer when upgrading,
never by the pipeline (it lives outside the ``tgo`` package on purpose)::

    python scripts/vendor_fetch.py            # verify vendored files offline (sha256)
    python scripts/vendor_fetch.py --fetch    # download tarballs, check npm integrity, extract

To upgrade a library, edit ``version``/``tarball``/``integrity`` in MANIFEST.json
(``npm view <pkg>@<ver> dist --json``), run ``--fetch``, then commit the new file,
the updated ``sha256`` it prints and the LICENSE.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "tgo" / "viz" / "vendor"


def _sri_ok(data: bytes, integrity: str) -> bool:
    algo, _, b64 = integrity.partition("-")
    return base64.b64encode(hashlib.new(algo, data).digest()).decode() == b64


def verify(manifest: dict) -> list[str]:
    bad = []
    for f in manifest["files"]:
        p = VENDOR / f["file"]
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != f["sha256"]:
            bad.append(f["file"])
        for name in [f["license_file"], *(n["file"] for n in f.get("notices", []))]:
            if not (VENDOR / name).exists():
                bad.append(name)
    return bad


def fetch(manifest: dict) -> None:
    for f in manifest["files"]:
        with urllib.request.urlopen(f["tarball"], timeout=60) as r:  # noqa: S310 - pinned https URL
            tgz = r.read()
        if not _sri_ok(tgz, f["integrity"]):
            raise SystemExit(f"{f['name']}: tarball does not match npm integrity {f['integrity']}")
        with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tar:
            data = tar.extractfile(f["member"]).read()
            lic = next((m for m in tar.getmembers() if m.name.lower() in {"package/license", "package/license.md",
                                                                          "package/license.txt"}), None)
            if lic:
                (VENDOR / f["license_file"]).write_bytes(tar.extractfile(lic).read())
            # Apache-2.0 4(d): NOTICE files and bundled third-party licences ship alongside.
            for n in f.get("notices", []):
                (VENDOR / n["file"]).write_bytes(tar.extractfile(n["member"]).read())
        (VENDOR / f["file"]).write_bytes(data)
        f["sha256"] = hashlib.sha256(data).hexdigest()
        print(f"{f['name']}@{f['version']} -> {f['file']} sha256={f['sha256']}")
    (VENDOR / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fetch", action="store_true", help="download from the npm registry (needs network)")
    args = ap.parse_args(argv)
    manifest = json.loads((VENDOR / "MANIFEST.json").read_text(encoding="utf-8"))
    if args.fetch:
        fetch(manifest)
    bad = verify(manifest)
    for b in bad:
        print(f"MISMATCH or missing: {b}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
