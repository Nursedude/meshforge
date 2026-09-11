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


@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch):
    """Pin this reader's clock verdict for every test in the module.

    2026-09-10: ``read_posture`` stopped ASSUMING its clock was good and
    started asking the machine. Without this fixture every expiry assertion
    below would silently depend on the NTP state of whatever box runs the
    suite — green on a synced dev box, opposite on an RTC-less Pi mid-boot,
    and a third answer in CI. A test whose verdict depends on un-pinned
    machine state pins nothing (2026-07-28). Tests that are ABOUT the probe
    clear this themselves.
    """
    monkeypatch.setenv(fp.CLOCK_ENV, "1")
    fp._clock_cache = (0.0, False, "")


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
        # The mirror, and the first two consumers that could only exist once
        # the declaration reached the boxes (2026-09-10).
        "scripts/fleet_posture_sync.sh": ("fleet_posture.sh", "fleet_posture_is_silent"),
        "scripts/fleet_power.py": ("fleet_posture_sync.sh", "mirror_posture"),
        "src/utils/watchdog_probes_tracer.py": ("fleet_posture", "silenced_peer"),
        "src/mini_dudeai/presets/meshforge_fleet.py": ("fleet_posture", "silenced_peer"),
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


class TestBridgeBoxesCaller:
    """The mesh-less refusal's SOURCE of truth — `_bridge_boxes()` in the CLI.

    2026-09-04: this leg had NEVER run. `gather_fleet_roles(hosts, self_role,
    ssh_cmd=...)` was called with `hosts` only, the TypeError was swallowed by
    the advisory `except`, and every invocation printed "mesh-less refusal
    skipped". The CALLEE's two-arg signature was already pinned by
    test_provision_role.py; the CALLER was not. These tests pin the caller.
    """

    def _load_cli(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("fleet_posture_cli", str(CLI))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _fake_pr(self, monkeypatch, role_map, calls):
        """Install a stub provision_role that RECORDS how it was called."""
        import types
        pr = types.ModuleType("provision_role")
        pr.DEFAULT_ROLES_FILE = "roles.yaml"
        pr.load_roles = lambda path: {"catalog": True}
        pr.parse_fleet_hosts = lambda p: ["moc", "moc3"]
        pr.read_role = lambda: "primary"

        def gather(hosts, self_role, ssh_cmd=None):
            calls.append({"hosts": hosts, "self_role": self_role})
            return dict(role_map)
        pr.gather_fleet_roles = gather
        pr.resolve_role = lambda cat, role: {
            "services": {"meshforge-gateway": "enabled" if role in
                         ("gateway-only", "primary") else "disabled"}}
        monkeypatch.setitem(sys.modules, "provision_role", pr)
        return pr

    def test_passes_self_role_the_callee_requires(self, tmp_path, monkeypatch):
        """A gather() that REQUIRES self_role must actually receive it."""
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".config" / "meshforge").mkdir(parents=True)
        (tmp_path / ".config" / "meshforge" / "fleet_hosts").write_text("moc\nmoc3\n")
        calls = []
        self._fake_pr(monkeypatch, {"(self)": "primary", "moc": "gateway-only"}, calls)
        mod = self._load_cli()
        bridges, note = mod._bridge_boxes()
        assert calls and calls[0]["self_role"] == "primary", \
            "caller must pass self_role — omitting it is the 2026-09-04 TypeError"
        assert "skipped" not in note, f"refusal leg silently disabled: {note}"

    def test_never_returns_the_literal_self_key(self, tmp_path, monkeypatch):
        """gather() keys the local box "(self)", but validate() compares bridge
        names against the posture doc's REAL box names. Leaking "(self)" makes
        the guard structurally unable to refuse a posture silencing THIS box —
        it would run, look healthy, and never fire."""
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".config" / "meshforge").mkdir(parents=True)
        (tmp_path / ".config" / "meshforge" / "fleet_hosts").write_text("moc\n")
        calls = []
        self._fake_pr(monkeypatch, {"(self)": "gateway-only", "moc": "disabled-role"}, calls)
        mod = self._load_cli()
        bridges, _ = mod._bridge_boxes()
        assert bridges is not None
        assert "(self)" not in bridges, "the literal (self) can never match a declaration"
        import socket
        assert socket.gethostname() in bridges, \
            "the local box must appear under the name the operator declares"

    def test_refusal_actually_fires_when_all_bridges_silenced(self):
        """End of the domain, not the wiring: given a bridge set, validate()
        must REFUSE a doc that silences all of them."""
        doc = _doc(moc={"state": "dormant", "until": fp.fmt_ts(NOW + 3600)},
                   moc3={"state": "dormant", "until": fp.fmt_ts(NOW + 3600)})
        errs = fp.validate(doc, now=NOW, bridge_boxes={"moc", "moc3"})
        assert any("bridge-capable" in e for e in errs), errs
        errs_ok = fp.validate(doc, now=NOW, bridge_boxes={"moc", "moc3", "moc9"})
        assert not any("bridge-capable" in e for e in errs_ok), \
            "one surviving bridge must NOT refuse"


class TestPostureSummaryNote:
    """`fleet_posture_summary` — the one-clause note shell consumers append.

    Born 2026-09-10: a posture FILE that parses reads `declared` even with ZERO
    boxes in it, which is the normal resting state once the last box is cleared
    (fleet_power.py resume). honest_status mapped `declared*` straight to
    "declared posture in effect", so it made an affirmative claim from an EMPTY
    state — nothing silenced, no denominator moved, and a reader chasing the
    note finds nothing. Empty != absent != in effect.
    """

    LIB = Path(__file__).parent.parent / "scripts" / "lib" / "fleet_posture.sh"

    def _summary(self, posture_file):
        script = (f'. "{self.LIB}"; fleet_posture_read "{self.LIB.parent.parent.parent}"; '
                  'printf "%s|%s" "$FLEET_POSTURE_STATUS" "$(fleet_posture_summary)"')
        env = dict(os.environ, MESHFORGE_FLEET_POSTURE=str(posture_file))
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                             timeout=60, env=env).stdout
        status, _, note = out.partition("|")
        return status, note

    def _write(self, tmp_path, boxes):
        now = time.time()
        doc = {"boxes": boxes, "declared_at": fp.fmt_ts(now),
               "declared_by": "operator", "posture": "t"}
        p = tmp_path / "posture.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def _box(self, hours=3):
        now = time.time()
        return {"state": "dormant", "since": fp.fmt_ts(now),
                "until": fp.fmt_ts(now + hours * 3600)}

    def test_absent_file_says_nothing(self, tmp_path):
        status, note = self._summary(tmp_path / "nope.json")
        assert status == "undeclared"
        assert note == ""

    def test_declared_but_empty_does_not_claim_it_is_in_effect(self, tmp_path):
        """THE regression this exists for."""
        status, note = self._summary(self._write(tmp_path, {}))
        assert status.startswith("declared")
        assert "in effect" not in note
        assert "no box declared" in note

    def test_declared_with_boxes_says_in_effect_and_counts_them(self, tmp_path):
        p = self._write(tmp_path, {"b1": self._box(), "b2": self._box()})
        status, note = self._summary(p)
        assert status.startswith("declared")
        assert "in effect" in note
        assert "2 box(es) silenced" in note

    def test_count_reflects_only_silent_boxes(self, tmp_path):
        # `shed` is a box that is UP with reduced services -- it is watched, not
        # silenced, so it must not inflate the silenced count.
        boxes = {"b1": self._box()}
        boxes["b2"] = dict(self._box(), state="shed")
        status, note = self._summary(self._write(tmp_path, boxes))
        assert "1 box(es) silenced" in note

    def test_unusable_file_is_loud_and_says_every_box_is_checked(self, tmp_path):
        p = tmp_path / "posture.json"
        p.write_text("{not json", encoding="utf-8")
        status, note = self._summary(p)
        assert "NOT USABLE" in note
        assert "every box checked" in note


# --------------------------------------------------------------------------- #
# Clock confidence — the predicate that existed for nine days with no way in.
# --------------------------------------------------------------------------- #
class TestClockConfidence:
    def test_env_override_forces_true_and_says_so(self, monkeypatch):
        monkeypatch.setenv(fp.CLOCK_ENV, "1")
        conf, note = fp.clock_confidence()
        assert conf is True and fp.CLOCK_ENV in note

    def test_env_override_forces_false(self, monkeypatch):
        monkeypatch.setenv(fp.CLOCK_ENV, "0")
        conf, note = fp.clock_confidence()
        assert conf is False and fp.CLOCK_ENV in note

    def test_probe_failure_is_not_confidence(self, monkeypatch):
        """Unobservable is never healthy: a clock we could not check is not a
        clock we may trust (hfm #2)."""
        monkeypatch.delenv(fp.CLOCK_ENV, raising=False)
        conf, note = fp.clock_confidence(
            _probe=lambda: (False, "clock confidence UNOBSERVABLE (boom)"))
        assert conf is False and "UNOBSERVABLE" in note


    def test_probe_says_synced(self, monkeypatch):
        monkeypatch.delenv(fp.CLOCK_ENV, raising=False)
        conf, _ = fp.clock_confidence(_probe=lambda: (True, "synced"))
        assert conf is True

    def test_probe_result_is_cached_not_re_run_every_tick(self, monkeypatch):
        """mini ticks every 30 s on ten boxes; this answer moves in hours."""
        monkeypatch.delenv(fp.CLOCK_ENV, raising=False)
        fp._clock_cache = (0.0, False, "")
        calls = []

        def fake():
            calls.append(1)
            return True, "synced"

        monkeypatch.setattr(fp, "_probe_clock", fake)
        fp.clock_confidence(now=NOW)
        fp.clock_confidence(now=NOW + 10)
        assert len(calls) == 1
        fp.clock_confidence(now=NOW + fp._CLOCK_TTL_S + 1)
        assert len(calls) == 2

    def test_read_posture_asks_when_not_told(self, tmp_path, monkeypatch):
        """The regression that motivated the whole leg: before 2026-09-10 the
        default was True and no caller ever overrode it, so the HOLD branch
        below was unreachable in production."""
        monkeypatch.delenv(fp.CLOCK_ENV, raising=False)
        fp._clock_cache = (0.0, False, "")
        monkeypatch.setattr(fp, "_probe_clock", lambda: (False, "clock NOT synced"))
        path = _write(tmp_path, _doc(boxa={"state": "dormant",
                                           "since": fp.fmt_ts(NOW),
                                           "until": fp.fmt_ts(NOW + 3600)}))
        p = fp.read_posture(path, now=NOW + 7200)
        assert p.box("boxa").held is True, "expired window must be HELD, not lifted"
        assert p.clock_confident is False
        assert "NOT synced" in p.clock_note

    def test_explicit_argument_still_wins_over_the_probe(self, tmp_path, monkeypatch):
        monkeypatch.delenv(fp.CLOCK_ENV, raising=False)
        monkeypatch.setattr(fp, "_probe_clock", lambda: (False, "unsynced"))
        path = _write(tmp_path, _doc(boxa={"state": "dormant",
                                           "since": fp.fmt_ts(NOW),
                                           "until": fp.fmt_ts(NOW + 3600)}))
        p = fp.read_posture(path, now=NOW + 7200, clock_confident=True)
        assert p.box("boxa").expired is True and p.box("boxa").held is False


# --------------------------------------------------------------------------- #
# Peer name -> box name

class TestProbeClockItself:
    """The drill (2026-09-10) caught this hole in the class above: every test
    there injects ``_probe``, so they exercise the LAMBDA and never the branch
    that actually decides. Planting "an unobservable clock reads as confident"
    inside ``_probe_clock`` left all of them green — the 2026-07-25 lesson
    (13 tests passed against a mocked resolver while the thing it stood in for
    was broken) reproduced in the tests written to prevent it.

    These drive the real function and fake only the OS underneath it.
    """

    def _no_stamp(self, monkeypatch):
        monkeypatch.setattr(fp.os.path, "exists",
                            lambda p: False if p == fp._TIMESYNC_STAMP else True)

    def _timedatectl(self, monkeypatch, *, rc=0, out="yes", exc=None):
        class R:
            returncode = rc
            stdout = out
            stderr = ""

        def fake(cmd, **kw):
            assert "timeout" in kw, "MF004: every subprocess call needs a timeout"
            if exc is not None:
                raise exc
            return R()

        monkeypatch.setattr(fp.subprocess, "run", fake)

    def test_timesyncd_stamp_present_is_affirmative(self, monkeypatch):
        monkeypatch.setattr(fp.os.path, "exists", lambda p: True)
        conf, note = fp._probe_clock()
        assert conf is True and "timesyncd" in note

    def test_timedatectl_yes(self, monkeypatch):
        self._no_stamp(monkeypatch)
        self._timedatectl(monkeypatch, out="yes\n")
        assert fp._probe_clock()[0] is True

    def test_timedatectl_no_is_not_confident(self, monkeypatch):
        self._no_stamp(monkeypatch)
        self._timedatectl(monkeypatch, out="no\n")
        conf, note = fp._probe_clock()
        assert conf is False and "NOT NTP-synchronized" in note

    def test_nonzero_rc_is_UNOBSERVABLE_not_confident(self, monkeypatch):
        self._no_stamp(monkeypatch)
        self._timedatectl(monkeypatch, rc=1, out="")
        conf, note = fp._probe_clock()
        assert conf is False and "UNOBSERVABLE" in note

    def test_missing_binary_is_UNOBSERVABLE_not_confident(self, monkeypatch):
        """A box with no timedatectl at all. Absence of the instrument is not
        evidence about the clock."""
        self._no_stamp(monkeypatch)
        self._timedatectl(monkeypatch, exc=FileNotFoundError("timedatectl"))
        conf, note = fp._probe_clock()
        assert conf is False and "UNOBSERVABLE" in note

    def test_timeout_is_UNOBSERVABLE_not_confident(self, monkeypatch):
        self._no_stamp(monkeypatch)
        self._timedatectl(
            monkeypatch, exc=subprocess.TimeoutExpired("timedatectl", 5))
        conf, note = fp._probe_clock()
        assert conf is False and "UNOBSERVABLE" in note

    def test_unparseable_answer_is_UNOBSERVABLE_not_confident(self, monkeypatch):
        """The degraded value must not overlap the healthy domain (hfm #1)."""
        self._no_stamp(monkeypatch)
        self._timedatectl(monkeypatch, out="n/a")
        conf, note = fp._probe_clock()
        assert conf is False and "UNOBSERVABLE" in note

    def test_stamp_stat_error_falls_through_rather_than_deciding(self, monkeypatch):
        def boom(p):
            raise OSError("permission denied")

        monkeypatch.setattr(fp.os.path, "exists", boom)
        self._timedatectl(monkeypatch, out="yes")
        assert fp._probe_clock()[0] is True

# --------------------------------------------------------------------------- #
class TestResolvePeerBox:
    BOXES = ["boxc", "boxb", "boxa", "meshanchor-server", "mgrbox"]

    def test_exact_match(self):
        assert fp.resolve_peer_box("boxb", self.BOXES) == "boxb"

    def test_case_insensitive(self):
        assert fp.resolve_peer_box("MgrBox", self.BOXES) == "mgrbox"

    def test_app_prefix_is_stripped(self):
        assert fp.resolve_peer_box("meshforge-boxa", self.BOXES) == "boxa"

    def test_exact_match_beats_prefix_strip(self):
        """THE trap. `meshanchor-server` is a real box name in fleet_hosts.
        Strip-first would resolve it to `server` — a box that does not exist —
        and the detector would be keyed to a name nothing serves, which reads
        healthy rather than broken (the 2026-08-05 class)."""
        assert fp.resolve_peer_box("meshanchor-server", self.BOXES) == "meshanchor-server"

    def test_unknown_peer_is_none_not_a_guess(self):
        assert fp.resolve_peer_box("some-stranger", self.BOXES) is None
        assert fp.resolve_peer_box("", self.BOXES) is None

    def test_prefix_list_cannot_eat_a_real_box_name(self):
        """A guard on the constant itself: adding a prefix that is itself the
        head of a real box name silently re-creates the trap above."""
        assert "meshanchor-" not in fp.PEER_PREFIXES


class TestSilencedPeer:
    def _p(self, tmp_path, state="dormant"):
        path = _write(tmp_path, _doc(boxa={"state": state,
                                           "since": fp.fmt_ts(NOW),
                                           "until": fp.fmt_ts(NOW + 3600)}))
        return fp.read_posture(path, now=NOW)

    def test_declared_dormant_peer_is_silenced(self, tmp_path):
        p = self._p(tmp_path)
        assert fp.silenced_peer("meshforge-boxa", p) is not None

    def test_active_peer_is_not_silenced(self, tmp_path):
        p = self._p(tmp_path, state="shed")   # up, services reduced — still watched
        assert fp.silenced_peer("meshforge-boxa", p) is None

    def test_undeclared_peer_is_not_silenced(self, tmp_path):
        p = self._p(tmp_path)
        assert fp.silenced_peer("meshforge-boxb", p) is None

    def test_broken_declaration_silences_nothing(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text("{not json")
        p = fp.read_posture(str(path), now=NOW)
        assert p.status == fp.INVALID or p.status == fp.UNREADABLE
        assert fp.silenced_peer("meshforge-boxa", p) is None


# --------------------------------------------------------------------------- #
# A mirror is not the original.
# --------------------------------------------------------------------------- #
class TestMirrorIsStricterThanTheOriginal:
    def _mirror_doc(self):
        d = _doc(boxa={"state": "dormant", "since": fp.fmt_ts(NOW),
                       "until": fp.fmt_ts(NOW + 3600)})
        d["mirror"] = {"from": "mgrbox", "at": fp.fmt_ts(NOW)}
        return d

    def test_mirror_inside_its_window_silences_normally(self, tmp_path):
        path = _write(tmp_path, self._mirror_doc())
        p = fp.read_posture(path, now=NOW + 60, clock_confident=True)
        assert p.is_mirror and p.mirror_from == "mgrbox"
        assert p.box("boxa").silent

    def test_mirror_with_an_unconfirmed_clock_silences_NOTHING(self, tmp_path):
        """HOLD is right on the manager, where the document is authoritative
        and an operator is present. On a mirrored copy read by an RTC-less Pi
        it has no upper bound — it is 'the box that died in the storm is still
        dormant in November', rebuilt by the mirror."""
        path = _write(tmp_path, self._mirror_doc())
        p = fp.read_posture(path, now=NOW + 60, clock_confident=False)
        assert p.box("boxa").silent is False
        assert p.box("boxa").declared_state == "dormant"
        assert "NOT APPLIED" in p.box("boxa").note
        assert fp.silenced_peer("meshforge-boxa", p) is None

    def test_the_ORIGINAL_still_holds_on_an_unconfirmed_clock(self, tmp_path):
        """The manager's own file carries no mirror stamp, and its behaviour
        must not change: past `until` with an unconfirmed clock it HOLDS."""
        d = _doc(boxa={"state": "dormant", "since": fp.fmt_ts(NOW),
                       "until": fp.fmt_ts(NOW + 3600)})
        path = _write(tmp_path, d)
        p = fp.read_posture(path, now=NOW + 7200, clock_confident=False)
        assert p.is_mirror is False
        assert p.box("boxa").held is True and p.box("boxa").silent is True

    def test_mirror_refusal_is_stated_not_silent(self, tmp_path):
        path = _write(tmp_path, self._mirror_doc())
        p = fp.read_posture(path, now=NOW + 60, clock_confident=False)
        assert "NOT APPLIED" in p.detail


# --------------------------------------------------------------------------- #
# The stamp helper refuses to fan out what it would not accept.
# --------------------------------------------------------------------------- #
class TestMirrorStamp:
    STAMP = REPO / "scripts" / "fleet_posture_stamp.py"

    def _run(self, src, dest):
        return subprocess.run(
            [sys.executable, str(self.STAMP), str(src), str(dest), "testmgr"],
            capture_output=True, text=True, timeout=60)

    def test_valid_document_is_stamped_as_a_mirror(self, tmp_path):
        src = tmp_path / "src.json"
        src.write_text(json.dumps(_doc(boxa={"state": "dormant",
                                             "since": fp.fmt_ts(NOW),
                                             "until": fp.fmt_ts(time.time() + 3600)})))
        dest = tmp_path / "dest.json"
        r = self._run(src, dest)
        assert r.returncode == 0, r.stderr
        got = json.loads(dest.read_text())
        assert got["mirror"]["from"] == "testmgr" and got["mirror"]["at"]

    def test_invalid_document_is_REFUSED_not_distributed(self, tmp_path):
        """One broken file on the manager must not become nine broken files on
        the boxes that are about to lose their operator."""
        src = tmp_path / "src.json"
        src.write_text(json.dumps({"boxes": {"boxa": {"state": "dormant"}}}))  # no until
        dest = tmp_path / "dest.json"
        r = self._run(src, dest)
        assert r.returncode == 2
        assert "REFUSING" in r.stderr
        assert not dest.exists()

    def test_expired_window_is_still_distributable(self, tmp_path):
        """An expired declaration is a valid document whose effect has ended —
        and it must travel, or a box never learns the storm is over."""
        src = tmp_path / "src.json"
        src.write_text(json.dumps(_doc(boxa={"state": "dormant",
                                             "since": fp.fmt_ts(NOW),
                                             "until": fp.fmt_ts(NOW + 60)})))
        dest = tmp_path / "dest.json"
        assert self._run(src, dest).returncode == 0

    def test_empty_declaration_travels(self, tmp_path):
        """The resting state after `resume`. A mirror that only ever learns
        about declarations, never their end, keeps a fleet silent about a peer
        that already came back."""
        src = tmp_path / "src.json"
        src.write_text(json.dumps({"posture": "", "boxes": {}}))
        dest = tmp_path / "dest.json"
        assert self._run(src, dest).returncode == 0
        assert json.loads(dest.read_text())["boxes"] == {}


class TestCliShowEmptyDeclaration:
    """43e0ad37 fixed this in scripts/lib/fleet_posture.sh. There were two
    readers (hfm #5: when a mechanism is cured, grep for its copies)."""

    def _show(self, path):
        return subprocess.run([sys.executable, str(CLI), "--file", str(path), "show"],
                              capture_output=True, text=True, timeout=60)

    def test_declared_but_empty_says_nothing_is_silenced(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps({"posture": "", "declared_by": "operator",
                                    "boxes": {}}))
        r = self._show(path)
        assert r.returncode == 0
        assert "NOTHING is silenced" in r.stdout

    def test_declared_with_boxes_counts_what_is_silenced(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps(_doc(
            boxa={"state": "dormant", "since": fp.fmt_ts(time.time()),
                  "until": fp.fmt_ts(time.time() + 3600)})))
        r = self._show(path)
        assert r.returncode == 0
        assert "1 box(es) silenced" in r.stdout and "boxa" in r.stdout
