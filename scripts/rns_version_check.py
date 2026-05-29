"""Fleet RNS/LXMF version drift check (RNS T2-isolate arc, sub-arc A).

Reads the pinned rns/lxmf versions from requirements/rns.txt and compares them to
what is INSTALLED on this box. Exits non-zero on drift. Runs from anywhere
(incl. over SSH): python3 /opt/meshforge/scripts/rns_version_check.py

Why: RNS upstream withdrew public support (see the
project_rns_upstream_withdrawal memory), so MeshForge pins a known-good RNS
version DELIBERATELY — a bump is a reviewed decision, never an automatic
`pip install` grabbing latest. This catches a box that has drifted off the pin
before it bites. See .claude/plans/rns_t2_isolate_arc.md.
"""
import os
import re
import sys

try:
    from importlib.metadata import version, PackageNotFoundError
except ImportError:  # pragma: no cover  (py<3.8 fallback)
    from importlib_metadata import version, PackageNotFoundError  # type: ignore

_HERE = os.path.dirname(os.path.abspath(__file__))
REQ = os.path.join(_HERE, "..", "requirements", "rns.txt")


def pinned_versions():
    """Parse exact pins (`rns==X`, `lxmf==Y`) out of requirements/rns.txt."""
    pins = {}
    try:
        with open(REQ) as f:
            for line in f:
                m = re.match(r"^\s*(rns|lxmf)==([0-9][0-9A-Za-z.\-]*)", line)
                if m:
                    pins[m.group(1)] = m.group(2)
    except OSError as e:
        print(f"cannot read {REQ}: {e}")
    return pins


def installed_version(pkg):
    try:
        return version(pkg)
    except PackageNotFoundError:
        return None


def main():
    pins = pinned_versions()
    host = os.uname().nodename
    print(f"RNS version check @ {host}")
    if not pins:
        print("  NO exact pin in requirements/rns.txt (sub-arc A not applied?)")
        return 2

    drift = False
    for pkg in ("rns", "lxmf"):
        want = pins.get(pkg)
        if want is None:
            continue
        have = installed_version(pkg)
        ok = have == want
        drift = drift or not ok
        print(f"  [{'OK   ' if ok else 'DRIFT'}] {pkg:<5} installed={str(have):<10} pinned={want}")

    if drift:
        conv = " ".join(f"{p}=={v}" for p, v in pins.items())
        print(f"  -> CONVERGE (watched): pip install {conv}")
        print("     (verify rnsd + shared instance after; downgrades carry config-format risk)")
        return 1
    print("  -> compliant with the pin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
