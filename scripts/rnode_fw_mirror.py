#!/usr/bin/env python3
"""Mirror one RNode firmware release into our fork, verified end to end.

Why (operator, 2026-09-25): "the firmware is public — we need to 'own' it;
upstream may not work for us or may include issues that debilitate our
operational status". Our forked rnodeconf downloaded
markqvist/rnode_firmware/releases/LATEST — any reflash installed whatever
upstream shipped that day. GitHub forks carry code and tags but NOT release
assets, and the assets are what rnodeconf flashes.

Steps for one TAG:
  1. download every asset of upstream TAG;
  2. verify each firmware zip's SHA-256 against upstream's release.json —
     a mismatch or a zip missing from the manifest ABORTS (nothing published);
  3. (--publish) create the same release on the fork with identical assets;
  4. re-download from the fork and verify again — the copy is proven, not
     assumed.

Usage:
    python3 scripts/rnode_fw_mirror.py 1.86              # dry run: 1-2 only
    python3 scripts/rnode_fw_mirror.py 1.86 --publish    # 1-4

Exit 0 = verified (and published if asked); 1 = verification failed;
2 = tool/network error (UNKNOWN — nothing was proven).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

UPSTREAM = "markqvist/RNode_Firmware"
FORK = "Nursedude/RNode_Firmware"


def _gh(args, timeout=600):
    return subprocess.run(["gh", *args], capture_output=True, text=True,
                          timeout=timeout, check=False)


def download(repo: str, tag: str, dest: Path) -> None:
    r = _gh(["release", "download", tag, "-R", repo, "-D", str(dest), "--clobber"])
    if r.returncode != 0:
        raise RuntimeError(f"download {repo}@{tag} failed: {r.stderr.strip()[:300]}")


def verify(folder: Path) -> list:
    """Problems found (empty = verified). Every zip must be in release.json with
    a matching SHA-256; every manifest entry must be present."""
    manifest_path = folder / "release.json"
    if not manifest_path.is_file():
        return ["release.json missing — nothing to verify against"]
    manifest = json.loads(manifest_path.read_text())
    problems = []
    zips = {p.name for p in folder.glob("*.zip")}
    for name in sorted(zips - set(manifest)):
        problems.append(f"{name}: not in release.json")
    for name, meta in sorted(manifest.items()):
        f = folder / name
        if not f.is_file():
            problems.append(f"{name}: in release.json but not downloaded")
            continue
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        if digest != meta.get("hash"):
            problems.append(f"{name}: sha256 {digest[:12]} != manifest {str(meta.get('hash'))[:12]}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tag")
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args()
    if not shutil.which("gh"):
        print("UNKNOWN: gh CLI not installed")
        return 2
    work = Path(tempfile.mkdtemp(prefix=f"rnode_fw_{args.tag}_"))
    up = work / "upstream"
    try:
        download(UPSTREAM, args.tag, up)
        problems = verify(up)
        n = len(list(up.iterdir()))
        if problems:
            print(f"FAIL: upstream {args.tag} did not verify ({len(problems)} problem(s)); nothing published")
            for p in problems:
                print(f"  {p}")
            return 1
        print(f"OK: upstream {args.tag} — {n} assets, every zip matches release.json")
        if not args.publish:
            print("dry run: not published (--publish to mirror into the fork)")
            return 0
        assets = [str(p) for p in sorted(up.iterdir())]
        r = _gh(["release", "create", args.tag, "-R", FORK, "--verify-tag",
                 "--title", f"{args.tag} (mirror of {UPSTREAM}@{args.tag}, sha256-verified)",
                 "--notes", f"Byte-identical mirror of {UPSTREAM} release {args.tag}; every "
                            "firmware zip verified against upstream release.json before "
                            "publishing and again after re-download.", *assets], timeout=1800)
        if r.returncode != 0:
            print(f"UNKNOWN: release create failed: {r.stderr.strip()[:300]}")
            return 2
        back = work / "fork"
        download(FORK, args.tag, back)
        problems = verify(back)
        if problems:
            print(f"FAIL: fork copy of {args.tag} did not verify after publishing")
            for p in problems:
                print(f"  {p}")
            return 1
        print(f"OK: published and re-verified {FORK}@{args.tag} ({len(list(back.iterdir()))} assets)")
        return 0
    except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError) as e:
        print(f"UNKNOWN: {e}")
        return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
