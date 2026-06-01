"""MeshForge <-> MeshAnchor RNS-reliability parity check.

The two sister NOC apps (MeshForge at /opt/meshforge, MeshAnchor at
/opt/meshanchor) share the fleet's RNS substrate (one rnsd per box, shared
instance). RNS-reliability changes land in MeshForge FIRST (the lead repo),
then this script flags MeshAnchor as drifted, then the change is ported.

Two tiers of parity:

* **Byte-identical tier** — files that MUST match exactly between the repos.
  A SHA-256 mismatch is drift. Today: the guarded RNS-init chokepoint, the
  shared bridge contract, the version-check tool, and the fork-pin block of
  requirements/rns.txt (compared as a normalized sub-block, since the
  surrounding prose legitimately differs per app).

* **Shape tier** — files that carry the same INTENT but are allowed
  app-specific text. We assert the presence of key symbols, not byte
  equality. Today: the rnstatus parser's ``timed_out`` keystone, the lint
  rules MF009 + MF019, and the two RNS-wedge probes.

Pure stdlib. Runs from anywhere:

    python3 /opt/meshforge/scripts/parity_check.py
    python3 /opt/meshforge/scripts/parity_check.py --meshforge /opt/meshforge \\
        --meshanchor /opt/meshanchor

Exit codes:
    0  in sync (both tiers)
    1  byte-tier drift (a must-match file differs) OR a shape-tier symbol
       is missing
    2  a repo or expected file is missing (can't compare)
"""
import argparse
import hashlib
import os
import re
import sys

DEFAULT_MESHFORGE = "/opt/meshforge"
DEFAULT_MESHANCHOR = "/opt/meshanchor"

# ── Byte-identical tier ────────────────────────────────────────────────
# repo-relative path -> must be byte-for-byte identical between the repos.
# rns_version_check.py is intentionally NOT here: its LOGIC is identical but
# its docstring legitimately names its own repo path, so it lives in the
# shape tier (symbol presence) instead.
BYTE_IDENTICAL = (
    "src/utils/rns_init.py",
    "src/gateway/canonical_message.py",
    # The RNS-tree permission foundation SSOT (configdir/logfile/storage layout
    # for a non-root rnsd — the mf.4/#73 perms class). App-agnostic, stdlib-only,
    # delegated to by both rns_alignment (MeshForge) and fleet_foundation (both
    # repos), so it MUST stay byte-identical.
    "src/utils/rns_tree_perms.py",
)

# The fork-pin block is compared as a normalized sub-block: the lines that
# matter (MF-FORK-PIN markers + the git requirement lines) must match, but
# the surrounding header prose is allowed to differ per app.
FORK_PIN_FILE = "requirements/rns.txt"

# ── Shape tier ─────────────────────────────────────────────────────────
# repo-relative path -> tuple of substrings that MUST all be present in
# BOTH repos' copy of the file. Intent parity, not byte parity.
SHAPE_SYMBOLS = {
    "src/utils/rns_status_parser.py": (
        "timed_out",
        "def run_rnstatus(",
        "timeout_s",
    ),
    "scripts/lint.py": (
        "MF009",
        "MF019",
    ),
    # Same version-drift tool in both repos (repo-specific docstring only).
    "scripts/rns_version_check.py": (
        "MF-FORK-PIN",
        "def pinned_versions(",
        "def installed_version(",
    ),
    # fleet_foundation: the audit/plan ENGINE is parity-shared (pure functions
    # over an injected owner-lookup), but each app declares its own data-roots
    # (meshforge_data_roots vs meshanchor_data_roots), so it's shape- not
    # byte-parity. Assert the shared engine + RNS-tree delegation are present.
    "src/utils/fleet_foundation.py": (
        "def audit_data_roots(",
        "def plan_data_root_fixes(",
        "def apply_foundation(",
        "from utils.rns_tree_perms import",
    ),
}

# The two RNS-wedge probes live in different modules in the two repos
# (MeshForge: watchdog_probes.py pure-function Signal idiom; MeshAnchor:
# active_health_probe.py HealthResult idiom). So we assert each repo
# carries the two probe concepts in ITS OWN home, not a shared path.
PROBE_SYMBOLS = {
    DEFAULT_MESHFORGE: (
        "src/utils/watchdog_probes.py",
        ("probe_rns_rpc_responsive", "probe_rns_interface_down_peer_reachable"),
    ),
    DEFAULT_MESHANCHOR: (
        "src/utils/active_health_probe.py",
        ("check_rns_rpc_responsive", "check_rns_interface_down_peer_reachable"),
    ),
}


def _sha256(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _fork_pin_block(text):
    """Extract the normalized parity-relevant lines from requirements/rns.txt:
    the ``# MF-FORK-PIN`` markers and the ``rns @ git+`` / ``lxmf @ git+``
    requirement lines. Returns a sorted tuple so prose ordering/spacing in
    the surrounding comments doesn't cause false drift."""
    if text is None:
        return None
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if re.match(r"^#\s*MF-FORK-PIN\s+(rns|lxmf)\s", s):
            lines.append(re.sub(r"\s+", " ", s))
        elif re.match(r"^(rns|lxmf)\s*@\s*git\+", s):
            lines.append(re.sub(r"\s+", " ", s))
    return tuple(sorted(lines))


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="parity_check",
        description="Check RNS-reliability parity between MeshForge and MeshAnchor.",
    )
    p.add_argument("--meshforge", default=DEFAULT_MESHFORGE)
    p.add_argument("--meshanchor", default=DEFAULT_MESHANCHOR)
    args = p.parse_args(argv)

    mf, ma = args.meshforge, args.meshanchor

    for root in (mf, ma):
        if not os.path.isdir(root):
            print(f"[MISSING] repo root not found: {root}")
            return 2

    drift = False
    missing = False

    print(f"RNS parity: {mf}  <->  {ma}\n")

    # ── Byte-identical tier ──
    print("Byte-identical tier:")
    for rel in BYTE_IDENTICAL:
        a, b = _sha256(os.path.join(mf, rel)), _sha256(os.path.join(ma, rel))
        if a is None or b is None:
            print(f"  [MISSING] {rel}  (mf={'ok' if a else 'absent'} "
                  f"ma={'ok' if b else 'absent'})")
            missing = True
            continue
        if a == b:
            print(f"  [OK     ] {rel}")
        else:
            print(f"  [DRIFT  ] {rel}  ({a[:12]} != {b[:12]})")
            drift = True

    # fork-pin sub-block
    fa = _fork_pin_block(_read(os.path.join(mf, FORK_PIN_FILE)))
    fb = _fork_pin_block(_read(os.path.join(ma, FORK_PIN_FILE)))
    if fa is None or fb is None:
        print(f"  [MISSING] {FORK_PIN_FILE} (fork-pin block)")
        missing = True
    elif fa == fb and fa:
        print(f"  [OK     ] {FORK_PIN_FILE} (fork-pin block)")
    else:
        print(f"  [DRIFT  ] {FORK_PIN_FILE} (fork-pin block)\n"
              f"            mf={fa}\n            ma={fb}")
        drift = True

    # ── Shape tier ──
    print("\nShape tier (symbol presence in both repos):")
    for rel, symbols in SHAPE_SYMBOLS.items():
        ta, tb = _read(os.path.join(mf, rel)), _read(os.path.join(ma, rel))
        if ta is None or tb is None:
            print(f"  [MISSING] {rel}")
            missing = True
            continue
        for sym in symbols:
            in_a, in_b = sym in ta, sym in tb
            if in_a and in_b:
                print(f"  [OK     ] {rel} :: {sym}")
            else:
                print(f"  [MISSING] {rel} :: {sym}  "
                      f"(mf={'y' if in_a else 'n'} ma={'y' if in_b else 'n'})")
                drift = True

    # probes (different module per repo)
    print("\nRNS-wedge probes (each repo, own idiom):")
    for root, (rel, syms) in PROBE_SYMBOLS.items():
        # Map the configured default root to the actual passed-in root.
        actual = mf if root == DEFAULT_MESHFORGE else ma
        label = "meshforge" if root == DEFAULT_MESHFORGE else "meshanchor"
        txt = _read(os.path.join(actual, rel))
        if txt is None:
            print(f"  [MISSING] {label}:{rel}")
            missing = True
            continue
        for sym in syms:
            if sym in txt:
                print(f"  [OK     ] {label}:{rel} :: {sym}")
            else:
                print(f"  [MISSING] {label}:{rel} :: {sym}")
                drift = True

    print()
    if missing:
        print("RESULT: MISSING files/symbols — cannot fully compare.")
        return 2
    if drift:
        print("RESULT: DRIFT — port the flagged changes (MeshForge is the lead repo).")
        return 1
    print("RESULT: in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
