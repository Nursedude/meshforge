"""Tests for the seed-promotion CLI (scripts/promote_seed_rules.py) — the
merge_seed_rules CLI that closes the 'sessions today, TUI later' gap.

Covers the in-memory plan (added / preserved-tuned-local / in-sync), apply
(write + backup), idempotency, the --seed override, and the never-guess error
paths. Run from /opt/meshforge/src so utils.* + mini_dudeai import.
"""
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "scripts", _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import promote_seed_rules as psr  # noqa: E402


def _rule(rid):
    return {"id": rid,
            "match": {"kind": "signal_class", "class": rid, "subject_glob": "*"},
            "action": {"kind": "propose_escalation"}, "cooldown_s": 1800}


def _setup(tmp_path, live_rules, seed_rules, seed_name="fleet_gateway"):
    root = tmp_path / "root"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / f"mini_dudeai_rules.{seed_name}.json").write_text(
        json.dumps({"rules": seed_rules}))
    home = tmp_path / "home"
    home.mkdir()
    (home / "mini_dudeai_rules.json").write_text(json.dumps({"rules": live_rules}))
    return str(root), str(home)


def _live_ids(home):
    doc = json.load(open(os.path.join(home, "mini_dudeai_rules.json")))
    return {r["id"] for r in doc["rules"]}


# ── plan ────────────────────────────────────────────────────────────

def test_plan_reports_added_rule_without_writing(tmp_path):
    root, home = _setup(tmp_path, [_rule("a")], [_rule("a"), _rule("b")])
    p = psr.plan(meshforge_root=root, mini_home=home, seed_name="fleet_gateway")
    assert p["changed"] is True
    assert "b" in p["report"]["added"]
    assert p["before"] == 1 and p["after"] == 2
    # plan() is in-memory only — the live file is untouched.
    assert _live_ids(home) == {"a"}


def test_plan_preserves_box_local_rule(tmp_path):
    root, home = _setup(tmp_path, [_rule("a"), _rule("zlocal")],
                        [_rule("a"), _rule("b")])
    p = psr.plan(meshforge_root=root, mini_home=home, seed_name="fleet_gateway")
    merged_ids = {r["id"] for r in p["merged"]}
    assert "zlocal" in merged_ids            # box-local kept
    assert "zlocal" in p["report"]["local"]
    assert "b" in p["report"]["added"]


# ── apply ───────────────────────────────────────────────────────────

def test_apply_writes_and_backs_up(tmp_path):
    root, home = _setup(tmp_path, [_rule("a")], [_rule("a"), _rule("b")])
    p = psr.plan(meshforge_root=root, mini_home=home, seed_name="fleet_gateway")
    bak = psr.apply(p)
    assert _live_ids(home) == {"a", "b"}     # b promoted into live
    assert os.path.exists(bak)
    assert {r["id"] for r in json.load(open(bak))["rules"]} == {"a"}  # backup = old set


def test_apply_then_replan_is_in_sync(tmp_path):
    # Idempotency: after a promote, a fresh plan sees nothing to do (the
    # provenance stamps now match the seed).
    root, home = _setup(tmp_path, [_rule("a")], [_rule("a"), _rule("b")])
    psr.apply(psr.plan(meshforge_root=root, mini_home=home, seed_name="fleet_gateway"))
    p2 = psr.plan(meshforge_root=root, mini_home=home, seed_name="fleet_gateway")
    assert p2["changed"] is False


# ── errors (never guess) ────────────────────────────────────────────

def test_resolve_target_errors_on_unknown_role():
    with pytest.raises(psr.PromoteError):
        psr.resolve_target(meshforge_root="/x", mini_home="/y", role="bogus_role_xyz")


def test_plan_errors_on_missing_seed_file(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "mini_dudeai_rules.json").write_text(json.dumps({"rules": []}))
    with pytest.raises(psr.PromoteError):
        psr.plan(meshforge_root=str(tmp_path / "noconfigs"),
                 mini_home=str(home), seed_name="fleet_gateway")


def test_plan_errors_on_missing_live_file(tmp_path):
    root, _ = _setup(tmp_path, [], [_rule("a")])
    with pytest.raises(psr.PromoteError):
        psr.plan(meshforge_root=root, mini_home=str(tmp_path / "nohome"),
                 seed_name="fleet_gateway")


# ── main (CLI surface) ──────────────────────────────────────────────

def test_main_dry_run_does_not_write_returns_0(tmp_path, capsys):
    root, home = _setup(tmp_path, [_rule("a")], [_rule("a"), _rule("b")])
    rc = psr.main(["--seed", "fleet_gateway", "--meshforge-root", root,
                   "--mini-home", home])
    assert rc == 0
    assert "WOULD promote" in capsys.readouterr().out
    assert _live_ids(home) == {"a"}          # dry-run wrote nothing


def test_main_apply_writes_returns_0(tmp_path):
    root, home = _setup(tmp_path, [_rule("a")], [_rule("a"), _rule("b")])
    rc = psr.main(["--seed", "fleet_gateway", "--meshforge-root", root,
                   "--mini-home", home, "--apply"])
    assert rc == 0
    assert _live_ids(home) == {"a", "b"}


def test_main_unknown_role_returns_2(tmp_path, capsys):
    rc = psr.main(["--role", "bogus_role_xyz", "--meshforge-root", str(tmp_path),
                   "--mini-home", str(tmp_path)])
    assert rc == 2


def test_main_json_output(tmp_path, capsys):
    root, home = _setup(tmp_path, [_rule("a")], [_rule("a"), _rule("b")])
    rc = psr.main(["--seed", "fleet_gateway", "--meshforge-root", root,
                   "--mini-home", home, "--apply", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["applied"] and out["seed"] == "fleet_gateway"
    assert "b" in out["report"]["added"]


# ── malformed-document validation (honest_failure_modes #3) ──────────

def test_plan_rejects_non_object_live_doc(tmp_path):
    # A top-level JSON list/scalar used to crash with an uncaught AttributeError
    # ('list' object has no attribute 'get'); now it's a clean PromoteError.
    root, home = _setup(tmp_path, [], [_rule("a")])
    (Path(home) / "mini_dudeai_rules.json").write_text("[1, 2, 3]")
    with pytest.raises(psr.PromoteError):
        psr.plan(meshforge_root=root, mini_home=home, seed_name="fleet_gateway")


def test_plan_rejects_dict_valued_rules(tmp_path):
    # A dict (not list) `rules` used to be iterated as keys and silently write
    # bare strings into the live ruleset; now it's rejected.
    root, home = _setup(tmp_path, [], [_rule("a")])
    (Path(home) / "mini_dudeai_rules.json").write_text(
        json.dumps({"rules": {"a": {"id": "a"}}}))
    with pytest.raises(psr.PromoteError):
        psr.plan(meshforge_root=root, mini_home=home, seed_name="fleet_gateway")


def test_plan_rejects_non_object_seed_doc(tmp_path):
    root, home = _setup(tmp_path, [_rule("a")], [_rule("a")])
    (Path(root) / "configs" / "mini_dudeai_rules.fleet_gateway.json").write_text("42")
    with pytest.raises(psr.PromoteError):
        psr.plan(meshforge_root=root, mini_home=home, seed_name="fleet_gateway")


def test_resolve_target_rejects_unknown_seed():
    # --seed is validated against the known seeds, so a garbage/path-traversal
    # value can't build an arbitrary file path.
    with pytest.raises(psr.PromoteError):
        psr.resolve_target(meshforge_root="/x", mini_home="/y",
                           seed_name="../../etc/passwd")


# ── apply: ownership/mode preservation + atomic backup ──────────────

def test_apply_preserves_file_mode(tmp_path):
    # atomic_write_json's mkstemp+os.replace would otherwise leave the live
    # file at 0600; apply() re-stamps the original mode so the daemon can read.
    import stat as _stat
    root, home = _setup(tmp_path, [_rule("a")], [_rule("a"), _rule("b")])
    live = Path(home) / "mini_dudeai_rules.json"
    os.chmod(live, 0o644)
    psr.apply(psr.plan(meshforge_root=root, mini_home=home, seed_name="fleet_gateway"))
    assert _stat.S_IMODE(live.stat().st_mode) == 0o644
    assert _live_ids(home) == {"a", "b"}


def test_apply_backup_is_cli_private_not_daemon_slot(tmp_path):
    # The backup must NOT be <live>.bak (the daemon's single-slot recovery file)
    # — it uses <live>.promote.bak so a promote never clobbers the daemon's
    # restore point.
    root, home = _setup(tmp_path, [_rule("a")], [_rule("a"), _rule("b")])
    daemon_bak = Path(home) / "mini_dudeai_rules.json.bak"
    daemon_bak.write_text(json.dumps({"rules": [_rule("daemon_recovery")]}))
    bak = psr.apply(psr.plan(meshforge_root=root, mini_home=home,
                             seed_name="fleet_gateway"))
    assert bak.endswith(".promote.bak")
    # the daemon's recovery slot is untouched
    assert {r["id"] for r in json.load(open(daemon_bak))["rules"]} == {"daemon_recovery"}
    # the CLI backup holds the pre-merge set
    assert {r["id"] for r in json.load(open(bak))["rules"]} == {"a"}


# ── apply: optimistic-concurrency guard ─────────────────────────────

def test_apply_refuses_when_live_changed_since_plan(tmp_path):
    # A concurrent daemon promotion (the live file moved between plan and apply)
    # must be refused, not silently overwritten with the stale merge.
    root, home = _setup(tmp_path, [_rule("a")], [_rule("a"), _rule("b")])
    p = psr.plan(meshforge_root=root, mini_home=home, seed_name="fleet_gateway")
    live = Path(home) / "mini_dudeai_rules.json"
    # Simulate the daemon rewriting the live file after plan() snapshotted it.
    p["live_mtime_ns"] = live.stat().st_mtime_ns - 1
    with pytest.raises(psr.PromoteError):
        psr.apply(p)


def test_apply_refuses_when_live_vanished(tmp_path):
    root, home = _setup(tmp_path, [_rule("a")], [_rule("a"), _rule("b")])
    p = psr.plan(meshforge_root=root, mini_home=home, seed_name="fleet_gateway")
    os.remove(Path(home) / "mini_dudeai_rules.json")
    with pytest.raises(psr.PromoteError):
        psr.apply(p)


# ── honest role in --json (no synthetic pseudo-role) ────────────────

def test_json_role_is_null_when_seed_targeted(tmp_path, capsys):
    root, home = _setup(tmp_path, [_rule("a")], [_rule("a"), _rule("b")])
    rc = psr.main(["--seed", "fleet_gateway", "--meshforge-root", root,
                   "--mini-home", home, "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["role"] is None and out["seed"] == "fleet_gateway"
