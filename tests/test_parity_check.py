"""Tests for scripts/parity_check.py's pure check_parity() — the (findings, overall)
contract that watchdog probe_parity_drift consumes. The drift-handling branch is
unit-tested in test_watchdog_probes.py via an injected check_fn; here we lock the
overall-status computation and the in-sync happy path against the real repos.
"""

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_HAS_MESHANCHOR = os.path.isdir("/opt/meshanchor")


def _load():
    spec = importlib.util.spec_from_file_location(
        "parity_check", ROOT / "scripts" / "parity_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repo_missing_when_meshanchor_absent(tmp_path):
    pc = _load()
    findings, overall = pc.check_parity(str(ROOT), str(tmp_path / "nope"))
    assert overall == "repo_missing"
    assert findings and findings[0].status == "missing"


def test_main_repo_missing_exits_2(tmp_path):
    pc = _load()
    rc = pc.main(["--meshforge", str(ROOT), "--meshanchor", str(tmp_path / "nope")])
    assert rc == 2


@pytest.mark.skipif(not _HAS_MESHANCHOR, reason="/opt/meshanchor not present")
def test_in_sync_against_real_repos():
    pc = _load()
    # ROOT, not /opt/meshforge: run from a worktree or branch, the literal
    # compared MAIN and read in_sync over the change under test (2026-10-20).
    findings, overall = pc.check_parity(str(ROOT), "/opt/meshanchor")
    assert overall == "in_sync", [f.label for f in findings if f.status != "ok"]
    assert findings and all(f.status == "ok" for f in findings)


@pytest.mark.skipif(not _HAS_MESHANCHOR, reason="/opt/meshanchor not present")
def test_main_in_sync_exits_0(capsys):
    pc = _load()
    rc = pc.main(["--meshforge", str(ROOT), "--meshanchor", "/opt/meshanchor"])
    assert rc == 0
    assert "RESULT: in sync." in capsys.readouterr().out


def test_fleet_truth_module_level_imports_are_twinned_too():
    """2026-10-20 review round 4: with the twin-import tier CUT, a module-level
    first-party import added to the byte-identical fleet_truth.py would port
    byte-for-byte and crash the twin on import with every parity tier green.
    Narrow on purpose: other twinned files import per-app seams by design
    (box_truth_app, paths), so the rule is per-file, not a tier. Lazy and
    try-guarded imports degrade and are not pinned."""
    import ast
    pc = _load()
    rel = "src/utils/fleet_truth.py"
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    deps = set()
    for node in tree.body:                      # module level only
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        else:
            continue
        for name in names:
            if name.split(".")[0] not in ("utils", "gateway", "mini_dudeai"):
                continue
            for cand in ("src/" + name.replace(".", "/") + ".py",
                         "src/" + name.replace(".", "/") + "/__init__.py"):
                if (ROOT / cand).is_file():
                    deps.add(cand)
    deps.discard("src/utils/__init__.py")       # the package itself, in both repos
    assert deps, "pin went vacuous — fleet_truth imports its registry"
    assert deps <= set(pc.BYTE_IDENTICAL), sorted(deps - set(pc.BYTE_IDENTICAL))


# ── Calibration claim-gate shared-core guard (self-audit-qa-arc §1) ──

def test_extract_literal_constant_tuple():
    pc = _load()
    assert pc._extract_literal_constant('X = ("a", "b", "c")\nY = 5\n', "X") == \
        frozenset({"a", "b", "c"})


def test_extract_literal_constant_absent_or_nonliteral():
    pc = _load()
    assert pc._extract_literal_constant("X = 1", "MISSING") is None   # absent
    assert pc._extract_literal_constant("X = foo()", "X") is None      # non-literal
    assert pc._extract_literal_constant("X = 5", "X") is None          # not a collection
    assert pc._extract_literal_constant("def (", "X") is None          # unparseable
    assert pc._extract_literal_constant(None, "X") is None             # no text


def test_extract_detects_vocab_difference():
    """The set-difference the drift finding reports (mf-only / ma-only)."""
    pc = _load()
    a = pc._extract_literal_constant('S = ("x", "y")', "S")
    b = pc._extract_literal_constant('S = ("x", "z")', "S")
    assert a != b
    assert sorted(a - b) == ["y"] and sorted(b - a) == ["z"]


@pytest.mark.skipif(not _HAS_MESHANCHOR, reason="/opt/meshanchor not present")
def test_calibgate_section_wired_and_ok():
    """The claim-gate shared-core check is wired into check_parity and the three
    detection vocabularies agree between the real repos — the guard the operator
    asked for so a future MeshForge claim_gate change can't silently skip MA."""
    pc = _load()
    findings, _ = pc.check_parity("/opt/meshforge", "/opt/meshanchor")
    cg = [f for f in findings if f.section == "calibgate"]
    assert cg, "calibgate section missing — check not wired"
    assert all(f.status == "ok" for f in cg), \
        [f.label for f in cg if f.status != "ok"]
    names = {f.label.split("::")[-1].strip() for f in cg if "::" in f.label}
    assert {"STRONG_CLAIMS", "CALIBRATION_MARKERS", "EVIDENCE_PATTERNS"} <= names


# ── Delivery-failure reason vocabulary (2026-10-24) ─────────────────────
# Both twins define DELIVERY_FAILURE_REASONS as ``DropReason.X.value``
# members, not string literals, so _extract_literal_constant reads None on
# both sides. The extractor resolves the members against the enum class in
# the same file; anything it cannot resolve is None (reported 'missing').

_ENUM_SRC = '''
class DropReason(str, Enum):
    A = "alpha"
    B = "beta"
    C = "gamma"

REASONS = frozenset({
    DropReason.A.value,
    DropReason.B.value,
    "literal",
})
'''


def test_extract_reason_set_resolves_enum_members():
    pc = _load()
    assert pc._extract_reason_set(_ENUM_SRC, "REASONS") == \
        frozenset({"alpha", "beta", "literal"})


def test_extract_reason_set_unresolvable_is_none():
    pc = _load()
    bad_member = _ENUM_SRC.replace("DropReason.B.value", "DropReason.Z.value")
    assert pc._extract_reason_set(bad_member, "REASONS") is None
    assert pc._extract_reason_set(_ENUM_SRC, "MISSING") is None
    assert pc._extract_reason_set("REASONS = frozenset(foo())", "REASONS") is None
    assert pc._extract_reason_set("def (", "REASONS") is None
    assert pc._extract_reason_set(None, "REASONS") is None


def _ma_with(tmp_path, text):
    """A fake MeshAnchor root carrying only delivery_counters.py — every OTHER
    finding reads 'missing', so assert on the reasons finding alone."""
    p = tmp_path / "ma" / "src" / "gateway" / "delivery_counters.py"
    p.parent.mkdir(parents=True)
    if text is not None:
        p.write_text(text)
    return str(tmp_path / "ma")


def _reasons(findings):
    (f,) = [f for f in findings if f.section == "reasons"]
    return f


def test_reasons_planted_drift_fires(tmp_path):
    """A guard that never failed is not evidence: drop one reason from the
    'MeshAnchor' copy and the leg must read drift, naming the reason."""
    pc = _load()
    src = (ROOT / "src" / "gateway" / "delivery_counters.py").read_text()
    planted = src.replace("    DropReason.UNSENDABLE.value,\n", "", 1)
    assert planted != src
    f = _reasons(pc.check_parity(str(ROOT), _ma_with(tmp_path, planted))[0])
    assert f.status == "drift" and "unsendable" in f.detail


def test_reasons_absent_file_is_missing(tmp_path):
    pc = _load()
    f = _reasons(pc.check_parity(str(ROOT), _ma_with(tmp_path, None))[0])
    assert f.status == "missing"


@pytest.mark.skipif(not _HAS_MESHANCHOR, reason="/opt/meshanchor not present")
def test_reasons_section_wired_and_ok():
    pc = _load()
    f = _reasons(pc.check_parity(str(ROOT), "/opt/meshanchor")[0])
    assert f.status == "ok", f.detail
    assert "DELIVERY_FAILURE_REASONS" in f.label


# ── A blind extractor must not mask another leg's drift (review #1, 10-24) ──
# 'missing' out-ranks 'drift' for the whole run, and probe_parity_drift turns
# 'missing' into indeterminate + a streak reset. A reasons leg whose file is
# PRESENT but whose members the AST cannot resolve is a limit of this tool,
# not an absent file, so it reads 'blind' and ranks BELOW drift.

def _f(section, status, label="x"):
    pc = _load()
    return pc.ParityFinding(section, label, status)


def test_reasons_unresolvable_member_is_blind_not_missing(tmp_path):
    pc = _load()
    src = (ROOT / "src" / "gateway" / "delivery_counters.py").read_text()
    planted = src.replace("DropReason.UNSENDABLE.value", "DropReason.NOPE.value", 1)
    assert planted != src
    f = _reasons(pc.check_parity(str(ROOT), _ma_with(tmp_path, planted))[0])
    assert f.status == "blind"
    assert "ma=unresolvable" in f.detail and "mf=ok" in f.detail


def test_overall_drift_outranks_blind():
    pc = _load()
    assert pc._overall([_f("reasons", "blind"), _f("byte", "drift")]) == "drift"


def test_overall_blind_alone_cannot_compare():
    pc = _load()
    assert pc._overall([_f("byte", "ok"), _f("reasons", "blind")]) == "missing"


def test_overall_missing_still_outranks_drift():
    pc = _load()
    assert pc._overall([_f("byte", "missing"), _f("shape", "drift")]) == "missing"
    assert pc._overall([_f("byte", "ok")]) == "in_sync"


def test_cli_names_a_blind_leg(monkeypatch, capsys):
    """The RESULT line must not say 'MISSING files' when nothing is missing."""
    pc = _load()
    fake = [pc.ParityFinding("reasons", "R :: S", "blind", "(mf=ok ma=unresolvable)")]
    monkeypatch.setattr(pc, "check_parity", lambda mf, ma: (fake, "missing"))
    assert pc.main(["--meshforge", "a", "--meshanchor", "b"]) == 2
    out = capsys.readouterr().out
    assert "[BLIND  ] R :: S" in out
    assert "BLIND" in out.split("RESULT:")[1] and "MISSING files" not in out


# ── None is four states, not one (review #2 of 51b02209, 10-24) ──
# 'blind' + "teach it the form" is the cure for an EXTRACTOR limit. A constant
# that is gone from a present file is the repo diverging (the shape tier's
# absent-symbol = drift precedent), and a SyntaxError is the FILE's defect —
# both were mislabelled as parser limits and the first ranked below drift.

def test_read_reason_set_names_each_state():
    pc = _load()
    assert pc._read_reason_set(_ENUM_SRC, "REASONS")[0] == "ok"
    assert pc._read_reason_set(_ENUM_SRC, "MISSING") == ("absent", None)
    assert pc._read_reason_set("def (", "REASONS") == ("unparseable", None)
    assert pc._read_reason_set("REASONS = frozenset(foo())", "REASONS") == ("unresolvable", None)
    assert pc._read_reason_set(None, "REASONS") == ("no_file", None)


def test_reasons_constant_gone_from_present_file_is_drift(tmp_path):
    pc = _load()
    src = (ROOT / "src" / "gateway" / "delivery_counters.py").read_text()
    planted = src.replace("DELIVERY_FAILURE_REASONS", "RENAMED_REASONS")
    assert planted != src
    f = _reasons(pc.check_parity(str(ROOT), _ma_with(tmp_path, planted))[0])
    assert f.status == "drift"
    assert "ma=absent" in f.detail and "teach it the form" not in f.detail


def test_reasons_syntax_error_is_blind_with_file_cure(tmp_path):
    pc = _load()
    src = (ROOT / "src" / "gateway" / "delivery_counters.py").read_text()
    f = _reasons(pc.check_parity(str(ROOT), _ma_with(tmp_path, src + "\ndef (\n"))[0])
    assert f.status == "blind"
    assert "ma=unparseable" in f.detail and "SyntaxError" in f.detail
    assert "teach it the form" not in f.detail


def test_reasons_absent_in_both_is_blind_stale_constant():
    """Neither side has it: nothing diverged, the leg's own name is stale."""
    pc = _load()
    f = pc._reasons_finding("L", ("absent", None), ("absent", None))
    assert f.status == "blind" and "REASONS_CONSTANT" in f.detail


def test_reasons_unresolvable_keeps_extractor_cure():
    pc = _load()
    f = pc._reasons_finding("L", ("ok", frozenset({"a"})), ("unresolvable", None))
    assert f.status == "blind" and "teach it the form" in f.detail


# ── 'absent' must mean NOT ASSIGNED ANYWHERE (non-author review #1 of 31a22563) ──
# The top-level loop reads only a plain module-level Assign. An annotated or
# nested assignment of the SAME constant is present and unread — the tool's
# limit (blind, "teach it the form") — never "gone or renamed; port it".

@pytest.mark.parametrize("form", [
    "R: frozenset = frozenset({'a'})\n",
    "if True:\n    R = frozenset({'a'})\n",
    "try:\n    R = frozenset({'a'})\nexcept Exception:\n    pass\n",
    "R, OTHER = frozenset({'a'}), 1\n",
])
def test_read_reason_set_present_but_unread_form_is_unresolvable(form):
    pc = _load()
    assert pc._read_reason_set(form, "R") == ("unresolvable", None)


def test_read_reason_set_truly_absent_stays_absent():
    pc = _load()
    assert pc._read_reason_set("X = frozenset({'a'})\nprint(R)\n", "R") == ("absent", None)


def test_reasons_annotated_constant_is_blind_not_drift(tmp_path):
    pc = _load()
    src = (ROOT / "src" / "gateway" / "delivery_counters.py").read_text()
    planted = src.replace("DELIVERY_FAILURE_REASONS = ", "DELIVERY_FAILURE_REASONS: frozenset = ", 1)
    assert planted != src
    f = _reasons(pc.check_parity(str(ROOT), _ma_with(tmp_path, planted))[0])
    assert f.status == "blind" and "teach it the form" in f.detail
