"""Regression guards for the NOC read grant (security pass 2026-10-14).

S11 / acceptance 6 — a fresh install carries ZERO grants: nothing but the
       grant CLI writes authorized_keys.
B7a  — the center's private key never rides a backup sweep.
S9   — the /fleet page escapes every peer value it interpolates into HTML,
       before map-less boxes join the set of peers that can inject.
Each is a grep-shaped guard in the MF007 style: a new offender fails here
and names the rule; an allowlist entry needs a reason.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── S11: one writer of authorized_keys ───────────────────────────────────

#: Files allowed to MENTION authorized_keys, each with why.
_AK_ALLOWED = {
    "src/utils/box_truth.py": "the grant-line builder/parser (no writes)",
    "src/utils/box_truth_grants.py": "THE writer (plan + typed confirm + read-back)",
    "src/utils/box_truth_center.py": "docstring: push runs the target's CLI, never edits remotely",
    "scripts/box_truth_grant.py": "the CLI over box_truth_grants",
    "src/utils/box_truth_reader.py": "docstring: what a pinned read proves",
    "scripts/box_truth_export.py": "docstring: the forced-command line it runs under",
    "scripts/box_truth_grant_drill.py": "a SCRATCH authorized_keys in its own temp dir, "
                                        "written through box_truth_grants",
    "scripts/cloud/setup_vps.sh": "cloud VPS push user: `touch` an EMPTY file "
                                  "(zero lines, not a fleet box)",
    "scripts/cloud/README.md": "documents the VPS step above",
}


def test_s11_only_the_grant_cli_touches_authorized_keys():
    offenders = []
    for base in ("src", "scripts", "templates", ".githooks"):
        for p in (ROOT / base).rglob("*"):
            if not p.is_file() or p.suffix in (".pyc",) or "__pycache__" in p.parts:
                continue
            rel = p.relative_to(ROOT).as_posix()
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if "authorized_keys" in text and rel not in _AK_ALLOWED:
                offenders.append(rel)
    for top in ROOT.glob("install*.sh"):
        if "authorized_keys" in top.read_text(encoding="utf-8"):
            offenders.append(top.name)
    assert not offenders, (
        f"{offenders} mention authorized_keys — grants are written ONLY by "
        "scripts/box_truth_grant.py (S11: a fresh install carries zero grants)")


def test_s11_vps_script_only_creates_an_empty_file():
    text = (ROOT / "scripts/cloud/setup_vps.sh").read_text(encoding="utf-8")
    writes = [ln for ln in text.splitlines()
              if "authorized_keys" in ln and not ln.lstrip().startswith(("#", "echo"))]
    assert all(re.match(r'\s*(touch|chmod|chown)\b', ln) for ln in writes), writes


# ── B7a: the center key never rides a backup ────────────────────────────

#: The ONLY .ssh lines a capture/backup script may carry (measured
#: 2026-10-14): box_config_capture takes ~/.ssh/config (aliases, no keys)
#: and filters private-key names out of its list. Anything else that names
#: .ssh is a new sweep that could archive ~/.ssh/meshforge_noc_ed25519.
_SSH_LINES_ALLOWED = {
    '[ -f "$HOME/.ssh/config" ] && echo "$HOME/.ssh/config" >>"$L"',
    r"""grep -v -E '/\.ssh/(id_|.*_np$|.*\.pem$)' "$L" >"$L.ok" """.strip(),
}


def test_b7a_backup_scripts_never_sweep_dot_ssh():
    for name in ("fleet_backup.sh", "box_config_capture.sh"):
        p = ROOT / "scripts" / name
        if not p.exists():
            continue
        code = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
                if ".ssh" in ln and not ln.lstrip().startswith("#")]
        new = [ln for ln in code if ln not in _SSH_LINES_ALLOWED]
        assert not new, (f"{name} gained a .ssh reference — the NOC key "
                         f"(~/.ssh/meshforge_noc_ed25519) must never be archived: {new}")


# ── S9: the /fleet page escapes peer values ─────────────────────────────

#: Bare member expressions concatenated in fleet.html that are NOT peer
#: strings — numbers, lengths, joins of already-escaped parts, or text that
#: is escaped by every caller. Measured 2026-10-14; grow with a reason.
_S9_SAFE = {
    "resp.status": "HTTP status number",
    "c.reason": "inside cellTitle(), whose result every caller esc()s (pinned below)",
    "c.source": "inside cellTitle() (as above)",
    "c.age_s": "number (typeof-guarded)",
    "c.claw_count": "number",
    "dpBoxes.map": "map of esc()d parts",
    "fanout.ttl_s": "number",
    "boxes.length": "count",
    "folded.length": "count (Known Blind Spots fold)",
    "folded.map(line)": "map of renderStructuralDark's line(), which esc()s every row field "
                        "(pinned by test_fleet_page's blind-spot tests)",
    "cols.map": "map of esc()d parts",
    "alerts.map(esc)": "escaped",
    "doc.bearings.map": "map of esc()d parts",
    "b.azimuth_deg.toFixed(1)": "number",
    "b.elevation_deg.toFixed(1)": "number",
    "b.snr.toFixed(2)": "number",
    "jobs.map": "map of esc()d parts",
    "units.map": "map of esc()d parts",
    "errs.map": "map of esc()d parts",
    "chips.join('')": "join of esc()d parts",
    "r.rung": "map key only (line builds `key`), rendered via esc()",
    "r.label": "map key only (as above)",
    "r.loss_pct.toFixed(0)": "number",
    "worst.toFixed(0)": "number",
    "seen.length": "count",
    "hist.length": "count",
    "lines.length": "count",
    "doc.error": "written to textContent, not innerHTML",
}

_CONCAT = re.compile(
    r"(?:\+\s*)([A-Za-z_$][\w$]*(?:\.[\w$]+|\[[^\]]+\])+(?:\([^()]*\))?)"
    r"|([A-Za-z_$][\w$]*(?:\.[\w$]+|\[[^\]]+\])+)(?=\s*\+)")


def _script():
    text = (ROOT / "web" / "fleet.html").read_text(encoding="utf-8")
    return text[text.index("<script"):]


def test_s9_every_concatenated_peer_value_is_escaped():
    found = {m.group(1) or m.group(2) for m in _CONCAT.finditer(_script())}
    new = sorted(found - set(_S9_SAFE))
    assert not new, (
        f"fleet.html concatenates {new} without esc() — wrap peer values in "
        "esc(...) (S9: a map-less box's spooled text reaches this page), or add "
        "the expression to _S9_SAFE with the reason it is not a peer string")


def test_s9_template_literals_stay_absent_or_escaped():
    bad = [i for i in re.findall(r"\$\{([^}]*)\}", _script())
           if not i.strip().startswith("esc(")]
    assert not bad, f"template interpolation without esc(): {bad}"


def test_s9_celltitle_is_always_escaped_by_its_caller():
    s = _script()
    calls = [m.start() for m in re.finditer(r"cellTitle\(", s)]
    defs = [m.start() for m in re.finditer(r"function cellTitle\(", s)]
    for pos in calls:
        if pos - len("function ") in defs:
            continue
        assert s[max(0, pos - 4):pos] == "esc(", s[pos - 40:pos + 40]


def test_s9_safe_list_has_no_stale_entries():
    found = {m.group(1) or m.group(2) for m in _CONCAT.finditer(_script())}
    stale = sorted(set(_S9_SAFE) - found)
    assert not stale, f"_S9_SAFE entries no longer in fleet.html: {stale} — remove them"
