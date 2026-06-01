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
from dataclasses import dataclass

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


@dataclass
class ParityFinding:
    """One parity comparison result. ``section`` groups the render; ``status`` is
    one of 'ok'|'drift'|'missing'; ``detail`` is the formatted line suffix."""
    section: str   # 'byte' | 'forkpin' | 'shape' | 'probe'
    label: str
    status: str
    detail: str = ""


def check_parity(mf=DEFAULT_MESHFORGE, ma=DEFAULT_MESHANCHOR):
    """Pure parity comparison — no printing. Returns ``(findings, overall)`` where
    ``overall`` is one of:
      'repo_missing' — a repo root isn't a directory (can't compare at all)
      'missing'      — both roots present but a tracked file/symbol is absent
      'drift'        — a must-match file/symbol content-diverged (the real signal)
      'in_sync'      — everything matches
    'missing' takes precedence over 'drift' (same as the CLI's exit-2-over-1).
    ``main()`` renders this; the watchdog ``probe_parity_drift`` consumes it.
    """
    for root in (mf, ma):
        if not os.path.isdir(root):
            return [ParityFinding("repo", root, "missing", "repo root not found")], "repo_missing"

    findings = []

    # ── Byte-identical tier ──
    for rel in BYTE_IDENTICAL:
        a, b = _sha256(os.path.join(mf, rel)), _sha256(os.path.join(ma, rel))
        if a is None or b is None:
            findings.append(ParityFinding("byte", rel, "missing",
                f"(mf={'ok' if a else 'absent'} ma={'ok' if b else 'absent'})"))
        elif a == b:
            findings.append(ParityFinding("byte", rel, "ok"))
        else:
            findings.append(ParityFinding("byte", rel, "drift", f"({a[:12]} != {b[:12]})"))

    # fork-pin sub-block
    fa = _fork_pin_block(_read(os.path.join(mf, FORK_PIN_FILE)))
    fb = _fork_pin_block(_read(os.path.join(ma, FORK_PIN_FILE)))
    label = f"{FORK_PIN_FILE} (fork-pin block)"
    if fa is None or fb is None:
        findings.append(ParityFinding("forkpin", label, "missing"))
    elif fa == fb and fa:
        findings.append(ParityFinding("forkpin", label, "ok"))
    else:
        findings.append(ParityFinding("forkpin", label, "drift", f"\n            mf={fa}\n            ma={fb}"))

    # ── Shape tier ──
    for rel, symbols in SHAPE_SYMBOLS.items():
        ta, tb = _read(os.path.join(mf, rel)), _read(os.path.join(ma, rel))
        if ta is None or tb is None:
            findings.append(ParityFinding("shape", rel, "missing"))
            continue
        for sym in symbols:
            in_a, in_b = sym in ta, sym in tb
            if in_a and in_b:
                findings.append(ParityFinding("shape", f"{rel} :: {sym}", "ok"))
            else:
                # A symbol present in one repo but not the other is real drift, not
                # an absent file. Tag it 'drift' (the CLI counted it that way too).
                findings.append(ParityFinding("shape", f"{rel} :: {sym}", "drift",
                    f"(mf={'y' if in_a else 'n'} ma={'y' if in_b else 'n'})"))

    # probes (different module per repo)
    for root, (rel, syms) in PROBE_SYMBOLS.items():
        actual = mf if root == DEFAULT_MESHFORGE else ma
        rlabel = "meshforge" if root == DEFAULT_MESHFORGE else "meshanchor"
        txt = _read(os.path.join(actual, rel))
        if txt is None:
            findings.append(ParityFinding("probe", f"{rlabel}:{rel}", "missing"))
            continue
        for sym in syms:
            status = "ok" if sym in txt else "drift"
            findings.append(ParityFinding("probe", f"{rlabel}:{rel} :: {sym}", status))

    if any(f.status == "missing" for f in findings):
        overall = "missing"
    elif any(f.status == "drift" for f in findings):
        overall = "drift"
    else:
        overall = "in_sync"
    return findings, overall


_TAG = {"ok": "OK     ", "drift": "DRIFT  ", "missing": "MISSING"}
_SECTION_HEADER = {
    "byte": "Byte-identical tier:",
    "shape": "\nShape tier (symbol presence in both repos):",
    "probe": "\nRNS-wedge probes (each repo, own idiom):",
}


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="parity_check",
        description="Check RNS-reliability parity between MeshForge and MeshAnchor.",
    )
    p.add_argument("--meshforge", default=DEFAULT_MESHFORGE)
    p.add_argument("--meshanchor", default=DEFAULT_MESHANCHOR)
    args = p.parse_args(argv)

    mf, ma = args.meshforge, args.meshanchor
    findings, overall = check_parity(mf, ma)

    if overall == "repo_missing":
        for f in findings:
            print(f"[MISSING] {f.detail}: {f.label}")
        return 2

    print(f"RNS parity: {mf}  <->  {ma}\n")
    seen_sections = set()
    for f in findings:
        # byte + forkpin render under the byte header; print headers once.
        header_key = "byte" if f.section == "forkpin" else f.section
        if header_key not in seen_sections:
            print(_SECTION_HEADER[header_key])
            seen_sections.add(header_key)
        suffix = f"  {f.detail}" if f.detail else ""
        print(f"  [{_TAG[f.status]}] {f.label}{suffix}")

    print()
    if overall == "missing":
        print("RESULT: MISSING files/symbols — cannot fully compare.")
        return 2
    if overall == "drift":
        print("RESULT: DRIFT — port the flagged changes (MeshForge is the lead repo).")
        return 1
    print("RESULT: in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
