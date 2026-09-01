"""Tests for the fleet power-posture SSOT (src/utils/fleet_posture.py) and its
CLI — the DORMANT arc's batch 1 (2026-09-01 design pass, Decision 3).

Every rule the design compiled into the module is pinned here: mandatory
capped expiry, expiry-as-honest-default, clock-gated HOLD, absent-file
invariance, loud unreadable/invalid, the mesh-less refusal, and the
closed-consumer gate that fails the moment a named consumer stops reading
posture.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils import fleet_posture as fp  # noqa: E402

REPO = Path(__file__).parent.parent
CLI = REPO / "scripts" / "fleet_posture.py"
NOW = 1_800_000_000.0


def _doc(**boxes):
    return {"posture": "t", "declared_at": fp.fmt_ts(NOW), "declared_by": "op",
            "boxes": boxes}


def _write(tmp_path, doc):
    p = tmp_path / "fleet_posture.json"
    p.write_text(json.dumps(doc))
    return str(p)


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
class TestReadPosture:
    def test_absent_file_is_undeclared_and_every_box_active(self, tmp_path):
        p = fp.read_posture(str(tmp_path / "nope.json"), now=NOW)
        assert p.status == fp.UNDECLARED and p.boxes == {}
        b = p.box("moc4")
        assert b.state == fp.STATE_ACTIVE and not b.silent

    def test_declared_dormant_inside_window_is_silent(self, tmp_path):
        path = _write(tmp_path, _doc(moc4={"state": "dormant", "since": fp.fmt_ts(NOW),
                                          "until": fp.fmt_ts(NOW + 3600), "reason": "storm"}))
        p = fp.read_posture(path, now=NOW + 60)
        assert p.status == fp.DECLARED
        b = p.box("moc4")
        assert b.state == "dormant" and b.silent and not b.expired
        assert "until" in b.note and "storm" in b.note
        assert p.silent_boxes() == ["moc4"]

    def test_expiry_is_the_honest_default(self, tmp_path):
        path = _write(tmp_path, _doc(moc4={"state": "dormant", "until": fp.fmt_ts(NOW + 3600)}))
        p = fp.read_posture(path, now=NOW + 3601)
        b = p.box("moc4")
        assert b.state == fp.STATE_ACTIVE and b.expired and not b.silent
        assert "EXPIRED" in b.note

    def test_unconfirmed_clock_holds_posture_past_expiry(self, tmp_path):
        path = _write(tmp_path, _doc(moc4={"state": "dormant", "until": fp.fmt_ts(NOW + 3600)}))
        p = fp.read_posture(path, now=NOW + 3601, clock_confident=False)
        b = p.box("moc4")
        assert b.state == "dormant" and b.held and b.silent
        assert "unconfirmed" in b.note and "HELD" in b.note

    def test_unreadable_file_is_loud_and_watches_everything(self, tmp_path):
        d = tmp_path / "dir.json"
        d.mkdir()                                     # a directory, not a file
        p = fp.read_posture(str(d), now=NOW)
        assert p.status == fp.UNREADABLE and p.detail
        assert p.box("moc4").state == fp.STATE_ACTIVE

    def test_invalid_json_is_loud_and_watches_everything(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        p = fp.read_posture(str(path), now=NOW)
        assert p.status == fp.INVALID and "not JSON" in p.detail
        assert p.box("moc4").state == fp.STATE_ACTIVE

    def test_open_ended_declaration_is_invalid_not_dormant(self, tmp_path):
        # the furniture failure: no `until` must never silence a box
        path = _write(tmp_path, _doc(moc4={"state": "dormant"}))
        p = fp.read_posture(path, now=NOW)
        assert p.status == fp.INVALID
        assert any("MANDATORY" in e for e in p.errors)
        assert p.box("moc4").state == fp.STATE_ACTIVE

    def test_expired_window_is_still_a_valid_document(self, tmp_path):
        path = _write(tmp_path, _doc(moc4={"state": "dormant", "until": fp.fmt_ts(NOW - 5)}))
        p = fp.read_posture(path, now=NOW)
        assert p.status == fp.DECLARED and p.box("moc4").expired

    def test_env_override_wins_for_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv(fp.POSTURE_ENV, str(tmp_path / "x.json"))
        assert fp.posture_path() == str(tmp_path / "x.json")

    def test_default_path_is_under_the_real_home(self, monkeypatch):
        monkeypatch.delenv(fp.POSTURE_ENV, raising=False)
        assert fp.posture_path(home="/h/u").endswith("/h/u/.config/meshforge/fleet_posture.json")


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
class TestValidate:
    def test_cap_is_enforced(self):
        doc = _doc(moc4={"state": "dormant", "since": fp.fmt_ts(NOW),
                         "until": fp.fmt_ts(NOW + fp.MAX_DORMANCY_S + 1)})
        errs = fp.validate(doc, now=NOW)
        assert any("cap" in e for e in errs)

    def test_unknown_state_refused(self):
        errs = fp.validate(_doc(moc4={"state": "asleep", "until": fp.fmt_ts(NOW + 10)}), now=NOW)
        assert any("state" in e for e in errs)

    def test_mesh_less_posture_refused_when_bridges_known(self):
        doc = _doc(moc={"state": "dormant", "until": fp.fmt_ts(NOW + 10)},
                   moc3={"state": "dormant", "until": fp.fmt_ts(NOW + 10)})
        errs = fp.validate(doc, now=NOW, bridge_boxes={"moc", "moc3"})
        assert any("cannot deliver" in e for e in errs)
        # one bridge left active → allowed
        doc["boxes"]["moc3"] = {"state": "active"}
        assert not [e for e in fp.validate(doc, now=NOW, bridge_boxes={"moc", "moc3"})
                    if "cannot deliver" in e]

    def test_shed_with_empty_services_counts_as_silenced(self):
        doc = _doc(moc={"state": "shed", "until": fp.fmt_ts(NOW + 10), "services": []})
        errs = fp.validate(doc, now=NOW, bridge_boxes={"moc"})
        assert any("cannot deliver" in e for e in errs)

    def test_no_boxes_key_is_not_a_posture(self):
        assert fp.validate({"posture": "x"}, now=NOW)

    def test_past_until_refused_at_declare_time(self):
        errs = fp.validate(_doc(moc4={"state": "dormant", "until": fp.fmt_ts(NOW - 1)}), now=NOW)
        assert any("in the past" in e for e in errs)


class TestParseUntil:
    def test_relative_forms(self):
        assert fp.parse_until("+90m", NOW) == NOW + 5400
        assert fp.parse_until("+36h", NOW) == NOW + 36 * 3600
        assert fp.parse_until("+3d", NOW) == NOW + 3 * 86400

    def test_absolute_and_garbage(self):
        assert fp.parse_until("2026-09-05T00:00:00Z", NOW) == 1788566400.0
        assert fp.parse_until("soon", NOW) is None
        assert fp.parse_ts(True) is None


# --------------------------------------------------------------------------- #
# CLI — drives the real script against a temp file
# --------------------------------------------------------------------------- #
class TestCli:
    def _run(self, tmp_path, *args):
        env = dict(os.environ)
        env["HOME"] = str(tmp_path)                    # no fleet_hosts → bridge leg skipped, SAID
        env[fp.POSTURE_ENV] = str(tmp_path / "fleet_posture.json")
        return subprocess.run([sys.executable, str(CLI), *args], capture_output=True,
                              text=True, timeout=60, env=env)

    def test_declare_show_clear_round_trip(self, tmp_path):
        r = self._run(tmp_path, "declare", "moc4", "dormant", "--until", "+3d", "--reason", "storm")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "declared moc4 dormant until" in r.stdout
        assert "mesh-less refusal skipped" in r.stdout  # said, not silent
        r = self._run(tmp_path, "show")
        assert r.returncode == 0 and "dormant" in r.stdout and "storm" in r.stdout
        r = self._run(tmp_path, "clear", "moc4")
        assert r.returncode == 0 and "ACTIVE" in r.stdout
        r = self._run(tmp_path, "show")
        assert "moc4" not in r.stdout

    def test_declare_without_until_is_refused(self, tmp_path):
        r = self._run(tmp_path, "declare", "moc4", "dormant")
        assert r.returncode == 2 and "mandatory" in r.stdout
        assert not (tmp_path / "fleet_posture.json").exists()

    def test_declare_past_cap_is_refused_and_force_records_it(self, tmp_path):
        r = self._run(tmp_path, "declare", "moc4", "dormant", "--until", "+20d")
        assert r.returncode == 1 and "cap" in r.stdout
        assert not (tmp_path / "fleet_posture.json").exists()
        r = self._run(tmp_path, "declare", "moc4", "dormant", "--until", "+20d", "--force")
        assert r.returncode == 0 and "FORCED" in r.stdout
        doc = json.loads((tmp_path / "fleet_posture.json").read_text())
        assert doc["forced_reasons"] and "cap" in doc["forced_reasons"][0]["refusal"]

    def test_second_declare_keeps_a_backup(self, tmp_path):
        self._run(tmp_path, "declare", "a", "dormant", "--until", "+1d")
        r = self._run(tmp_path, "declare", "b", "detached", "--until", "+1d")
        assert "backup" in r.stdout
        assert list(tmp_path.glob("fleet_posture.json.bak-*"))

    def test_show_on_absent_file_is_undeclared_rc0(self, tmp_path):
        r = self._run(tmp_path, "show")
        assert r.returncode == 0 and "undeclared" in r.stdout and "ACTIVE" in r.stdout


# --------------------------------------------------------------------------- #
# closed consumers — the enum may not grow / a consumer may not stop reading
# --------------------------------------------------------------------------- #
class TestClosedConsumers:
    """Every instrument the design names as a posture consumer must READ the
    posture module or file (hfm #7: closed enums need closed consumers). The
    list grows as batches land; a consumer that silently drops its read
    fails here."""

    CONSUMERS = {
        "scripts/fleet_offline_check.sh": ("fleet_posture", "MESHFORGE_FLEET_POSTURE"),
        "src/utils/fleet_truth_collector.py": ("fleet_posture", "read_posture"),
        "src/utils/fleet_truth.py": ("posture", "dormant"),
        "web/fleet.html": ("declared_posture", "posture_drift"),
        "scripts/lib/fleet_posture.sh": ("fleet_posture", "fleet_posture_is_silent"),
        "scripts/honest_status.sh": ("fleet_posture.sh", "fleet_posture_is_silent"),
        "scripts/fleet_pull.sh": ("fleet_posture.sh", "fleet_posture_is_silent"),
        "scripts/fleet_registry_sync.sh": ("fleet_posture.sh", "fleet_posture_is_silent"),
    }

    @pytest.mark.parametrize("rel,needles", sorted(CONSUMERS.items()))
    def test_consumer_reads_posture(self, rel, needles):
        src = (REPO / rel).read_text()
        for n in needles:
            assert n in src, f"{rel} no longer references {n!r}"

    def test_states_enum_is_closed(self):
        assert set(fp.STATES) == {"active", "shed", "dormant", "detached"}
        assert set(fp.SILENT_STATES) == {"dormant", "detached"}


def test_hand_written_entry_without_since_reads_declared(tmp_path):
    # 2026-09-01: caught by the collector test — read_posture validated with a
    # zero clock, so an entry with neither `since` nor `declared_at` anchored
    # at 1970 and tripped the 14-day cap. A reader must not refuse a file the
    # declare-time validator would have accepted.
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"boxes": {"moc4": {"state": "dormant",
                                                    "until": fp.fmt_ts(NOW + 3600)}}}))
    p = fp.read_posture(str(path), now=NOW)
    assert p.status == fp.DECLARED and p.box("moc4").silent
