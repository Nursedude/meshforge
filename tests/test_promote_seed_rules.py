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
