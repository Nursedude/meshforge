"""Tests for scripts/claw_metrics_push.py — the capture-and-paint orchestration.

The parser/record honesty is covered in test_claw_telemetry.py; here we pin the
WRITER contract: a total NATS failure still persists an honest *unreachable*
tick (so /api/status shows claw_unreachable instead of the last good numbers
silently aging into "stale"), and a paint failure still persists the captured
tick before paging. IP test vectors are synthetic (MF014/MF015).
"""
from __future__ import annotations

import importlib.util
import json
import os

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                       "claw_metrics_push.py")
_spec = importlib.util.spec_from_file_location("claw_metrics_push", _SCRIPT)
cmp_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmp_mod)

DI = ("Free heap: 17764 bytes, Total heap: 210492 bytes, Uptime: 109368 "
      "seconds, WiFi: connected (rssi -37 dBm), IP: 10.0.0.5, Chip: "
      "ESP32-S3 rev 2, 2 cores, 240 MHz")
BS = "ble_adv_age_s: 0 (advs 767422, uniq 32+, last rssi -59 dBm, restarts 0/0)"


class _FakeNC:
    """Context-manager stand-in for NatsConnection."""

    def __init__(self, responses, connect_error=None):
        self._responses = responses
        self._connect_error = connect_error
        self.requests = []

    def __enter__(self):
        if self._connect_error:
            raise self._connect_error
        return self

    def __exit__(self, *a):
        return False

    def request(self, subject, payload):
        d = json.loads(payload)
        self.requests.append(d)
        r = self._responses.get(d.get("tool"))
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Patch out env load, the localhost /api/status read, and the tick path."""
    monkeypatch.setattr(cmp_mod, "_load_claw_env", lambda _p=None: {
        "MINI_DUDEAI_NATS_SERVER": "nats://x", "MINI_DUDEAI_CLAW_DEVICE": "dudeclaw-01"})
    monkeypatch.setattr(cmp_mod, "build_rows", lambda: ["row0", "row1"])
    tick_file = tmp_path / "claw_last_tick.json"
    monkeypatch.setattr(cmp_mod, "_tick_path", lambda *_a: str(tick_file))
    return tick_file


def _install_conn(monkeypatch, fake):
    monkeypatch.setattr(cmp_mod, "NatsConnection", lambda *a, **k: fake)
    return fake


def test_happy_path_writes_ok_tick(wired, monkeypatch):
    _install_conn(monkeypatch, _FakeNC({
        "device_info": {"ok": True, "result": DI},
        "ble_stats": {"ok": True, "result": BS},
        "display_print": {"ok": True},
    }))
    rc = cmp_mod.main([])
    assert rc == 0
    tick = json.loads(wired.read_text())
    assert tick["ok"] is True
    assert tick["device_info"]["uptime_s"] == 109368
    assert tick["ble"]["advs"] == 767422
    assert tick["host"] and tick["device"] == "dudeclaw-01"


def test_connect_failure_writes_unreachable_tick_and_pages(wired, monkeypatch):
    # Total NATS failure: an honest unreachable tick is still persisted, AND
    # the cron pages (SystemExit) so claw death is never silent.
    _install_conn(monkeypatch, _FakeNC({}, connect_error=cmp_mod.NatsError("refused")))
    with pytest.raises(SystemExit):
        cmp_mod.main([])
    tick = json.loads(wired.read_text())
    assert tick["ok"] is False
    assert tick["device_info"] is None and tick["ble"] is None
    assert set(tick["errors"]) == {"device_info", "ble_stats"}


def test_paint_failure_still_persists_captured_tick_and_pages(wired, monkeypatch):
    # Capture succeeded but the OLED paint was refused: persist the good tick
    # (display stays current) yet still page on the paint failure.
    _install_conn(monkeypatch, _FakeNC({
        "device_info": {"ok": True, "result": DI},
        "ble_stats": {"ok": True, "result": BS},
        "display_print": {"ok": False, "error": "oled busy"},
    }))
    with pytest.raises(SystemExit):
        cmp_mod.main([])
    tick = json.loads(wired.read_text())
    assert tick["ok"] is True
    assert tick["device_info"]["uptime_s"] == 109368


def test_accessory_half_failing_keeps_the_device_reachable(wired, monkeypatch):
    """device_info answers, ble_stats times out.

    REVISED 2026-07-19 (structural-dark row 7). This used to assert
    ``ok is False`` — an AND over both halves — which pinned BLE-less
    dudeclaw-02 at not-ok in every tick forever and made the /fleet rollup
    render a perfectly healthy claw as "unreachable". The device DID answer, so
    reachable/ok stay True; the accessory miss is reported in errors +
    degraded_optional rather than collapsed into the liveness flag
    (honest_failure_modes #1/#3 — declared-absent is not an error, and a
    permanently-false flag is one nobody reads)."""
    _install_conn(monkeypatch, _FakeNC({
        "device_info": {"ok": True, "result": DI},
        "ble_stats": cmp_mod.NatsError("timeout"),
        "display_print": {"ok": True},
    }))
    rc = cmp_mod.main([])
    assert rc == 0
    tick = json.loads(wired.read_text())
    assert tick["reachable"] is True and tick["ok"] is True
    assert tick["device_info"]["uptime_s"] == 109368
    assert tick["ble"] is None and "ble_stats" in tick["errors"]
    assert "ble_stats" in tick["degraded_optional"]   # surfaced, not swallowed


class TestBrainTierPush:
    """display_tier wiring (firmware 0.4.0+dudeclaw.15): the glyph is pushed
    only when _probe_tier proved a tier; ladder decisions themselves are
    covered in test_claw_telemetry.py::TestComputeBrainTier."""

    def _nc_ok(self):
        return _FakeNC({
            "device_info": {"ok": True, "result": DI},
            "ble_stats": {"ok": True, "result": BS},
            "display_print": {"ok": True},
            "display_tier": {"ok": True, "result": "Brain tier set: F"},
        })

    def test_tier_pushed_and_recorded_in_tick(self, wired, monkeypatch):
        monkeypatch.setattr(cmp_mod, "_probe_tier", lambda env: ("F", "test"))
        fake = _install_conn(monkeypatch, self._nc_ok())
        rc = cmp_mod.main([])
        assert rc == 0
        tier_reqs = [r for r in fake.requests if r.get("tool") == "display_tier"]
        assert tier_reqs == [{"tool": "display_tier", "tier": "F"}]
        tick = json.loads(wired.read_text())
        assert tick["brain_tier"] == "F"

    def test_unprovable_tier_pushes_nothing(self, wired, monkeypatch):
        # None = push NOTHING; the firmware decays the glyph to SOLO — the
        # pusher never fabricates a tier just to have something to send.
        monkeypatch.setattr(cmp_mod, "_probe_tier",
                            lambda env: (None, "no tier provable"))
        fake = _install_conn(monkeypatch, self._nc_ok())
        rc = cmp_mod.main([])
        assert rc == 0
        assert not [r for r in fake.requests if r.get("tool") == "display_tier"]
        tick = json.loads(wired.read_text())
        assert tick["brain_tier"] is None

    def test_pre_dudeclaw15_firmware_warns_but_does_not_page(self, wired,
                                                             monkeypatch):
        # MF pulls can land before a claw reflash: a firmware without the
        # tool must not page the operator; the glyph simply stays absent.
        monkeypatch.setattr(cmp_mod, "_probe_tier", lambda env: ("F", "test"))
        nc = self._nc_ok()
        nc._responses["display_tier"] = {
            "ok": False, "error": "Error: unknown tool 'display_tier'"}
        _install_conn(monkeypatch, nc)
        assert cmp_mod.main([]) == 0

    def test_real_tier_refusal_pages(self, wired, monkeypatch):
        monkeypatch.setattr(cmp_mod, "_probe_tier", lambda env: ("F", "test"))
        nc = self._nc_ok()
        nc._responses["display_tier"] = {"ok": False, "error": "oled busy"}
        _install_conn(monkeypatch, nc)
        with pytest.raises(SystemExit):
            cmp_mod.main([])

    def test_row_paint_failure_skips_tier_push(self, wired, monkeypatch):
        # Rows refused -> the claw's paint path is already broken; page once,
        # don't stack a second failing call onto it.
        monkeypatch.setattr(cmp_mod, "_probe_tier", lambda env: ("F", "test"))
        nc = self._nc_ok()
        nc._responses["display_print"] = {"ok": False, "error": "oled busy"}
        fake = _install_conn(monkeypatch, nc)
        with pytest.raises(SystemExit):
            cmp_mod.main([])
        assert not [r for r in fake.requests if r.get("tool") == "display_tier"]


class TestProbeTier:
    """_probe_tier I/O honesty: unset URL disables the feed (never claims
    L/R without having LOOKED at the frontier); an unreachable SLO IS
    evidence and falls down the ladder. Endpoints here are synthetic."""

    def test_unset_slo_url_disables_feed(self):
        tier, note = cmp_mod._probe_tier({})
        assert tier is None
        assert "disabled" in note

    def test_frontier_proven_from_slo_verdict(self, monkeypatch):
        env = {"MINI_DUDEAI_TIER_SLO_URL": "http://brain.invalid:5000/fleet/slo",
               "MINI_DUDEAI_OLLAMA_URL": "http://llm.invalid:11434"}

        def fake_fetch(url, timeout=0):
            return {"schedules": {"verdicts": {"available": True, "jobs": [
                {"name": "mini_cadence", "status": "OK", "stale": False,
                 "age_s": 60.0}]}}}, None

        monkeypatch.setattr(cmp_mod, "fetch_json", fake_fetch)
        # ollama reachability now rides THE shared probe (chat_compiler)
        monkeypatch.setattr(cmp_mod, "probe_ollama",
                            lambda url, timeout_s=6: (True, "ollama 0.30"))
        tier, note = cmp_mod._probe_tier(env)
        assert tier == "F"

    def test_unreachable_slo_falls_to_local(self, monkeypatch):
        env = {"MINI_DUDEAI_TIER_SLO_URL": "http://brain.invalid:5000/fleet/slo",
               "MINI_DUDEAI_OLLAMA_URL": "http://llm.invalid:11434"}

        monkeypatch.setattr(cmp_mod, "fetch_json",
                            lambda url, timeout=0: (None, "connection refused"))
        monkeypatch.setattr(cmp_mod, "probe_ollama",
                            lambda url, timeout_s=6: (True, "ollama 0.30"))
        tier, note = cmp_mod._probe_tier(env)
        assert tier == "L"

    def test_list_shaped_slo_fields_fall_through_not_crash(self, tmp_path,
                                                           monkeypatch):
        # 2026-07-04 hardening: `or {}` only guards None — a truthy wrong
        # type (schedules as a list) used to AttributeError the whole push
        # into a FALSE claw-liveness page.
        env = {"MINI_DUDEAI_TIER_SLO_URL": "http://brain.invalid/fleet/slo",
               "MINI_DUDEAI_HOME": str(tmp_path)}  # no state file -> no R
        monkeypatch.setattr(cmp_mod, "fetch_json",
                            lambda url, timeout=0: ({"schedules": ["oops"]},
                                                    None))
        tier, note = cmp_mod._probe_tier(env)  # must not raise
        assert tier is None

    def test_rules_tier_reads_state_from_mini_dudeai_home(self, tmp_path,
                                                          monkeypatch):
        # 2026-07-04 hardening: the WRITER (claw daemon via resolve_home)
        # honors MINI_DUDEAI_HOME; the reader must follow the same
        # precedence or a homed-elsewhere daemon makes tier R unprovable.
        env = {"MINI_DUDEAI_TIER_SLO_URL": "http://brain.invalid/fleet/slo",
               "MINI_DUDEAI_HOME": str(tmp_path)}
        monkeypatch.setattr(cmp_mod, "fetch_json",
                            lambda url, timeout=0: (None, "refused"))
        (tmp_path / cmp_mod.CLAW_STATE_BASENAME).write_text("{}")
        tier, note = cmp_mod._probe_tier(env)
        assert tier == "R"


class TestUnknownToolCaseFolding:
    """2026-07-04 hardening: the pre-reflash detection must survive OTHER
    firmware vintages' casing — 'Unknown Tool: ...' must warn, not page."""

    def test_recased_unknown_tool_error_still_warns_not_pages(
            self, wired, monkeypatch):
        monkeypatch.setattr(cmp_mod, "_probe_tier", lambda env: ("F", "test"))
        nc = _FakeNC({
            "device_info": {"ok": True, "result": DI},
            "ble_stats": {"ok": True, "result": BS},
            "display_print": {"ok": True},
            "display_tier": {"ok": False,
                             "error": "Unknown Tool: display_tier"},
        })
        _install_conn(monkeypatch, nc)
        assert cmp_mod.main([]) == 0


class TestRTierReaderEnvFileOnly:
    def test_process_env_mini_dudeai_home_is_ignored(self, tmp_path,
                                                     monkeypatch):
        """Re-review pin (2026-07-04): the claw daemon sees ONLY the claw
        env FILE (EnvironmentFile=), so the R-tier reader must not honor a
        crontab/profile MINI_DUDEAI_HOME the daemon never sees."""
        writer_home = tmp_path / "writer"
        cron_home = tmp_path / "cron_env"
        writer_home.mkdir()
        cron_home.mkdir()
        # fresh state where the CRON env points; NOTHING where the env-file
        # points — an os.environ leg would wrongly prove tier R
        (cron_home / cmp_mod.CLAW_STATE_BASENAME).write_text("{}")
        monkeypatch.setenv("MINI_DUDEAI_HOME", str(cron_home))
        env = {"MINI_DUDEAI_TIER_SLO_URL": "http://brain.invalid/fleet/slo",
               "MINI_DUDEAI_HOME": str(writer_home)}
        monkeypatch.setattr(cmp_mod, "fetch_json",
                            lambda url, timeout=0: (None, "refused"))
        tier, note = cmp_mod._probe_tier(env)
        assert tier is None  # env-file home has no state; cron env ignored

    def test_secondary_instance_reads_suffixed_state(self, tmp_path,
                                                     monkeypatch):
        """W5.1: a second claw (MINI_DUDEAI_CLAW_INSTANCE=dudeclaw-02) runs its
        mini under a device-suffixed state file; the pusher's R-tier glyph must
        read THAT file, via the SAME instance_basename formula the daemon writes
        with (honest_failure_modes #5)."""
        from mini_dudeai.claw_telemetry import instance_basename
        home = tmp_path / "h"
        home.mkdir()
        suffixed = instance_basename(cmp_mod.CLAW_STATE_BASENAME, "dudeclaw-02")
        (home / suffixed).write_text("{}")  # fresh state for the secondary only
        env = {"MINI_DUDEAI_HOME": str(home),
               "MINI_DUDEAI_CLAW_INSTANCE": "dudeclaw-02",
               "MINI_DUDEAI_TIER_SLO_URL": "http://brain.invalid/fleet/slo"}
        monkeypatch.setattr(cmp_mod, "fetch_json",
                            lambda url, timeout=0: (None, "refused"))
        tier, note = cmp_mod._probe_tier(env)
        assert tier == "R"  # the secondary's fresh state proved the rule tier

    def test_secondary_instance_ignores_primary_state(self, tmp_path,
                                                      monkeypatch):
        """Symmetric guard: a fresh PRIMARY state file must NOT prove tier R
        for a secondary instance whose own suffixed state is absent (else
        claw-02's glyph would silently reflect claw-01's rule brain)."""
        home = tmp_path / "h"
        home.mkdir()
        (home / cmp_mod.CLAW_STATE_BASENAME).write_text("{}")  # primary fresh
        env = {"MINI_DUDEAI_HOME": str(home),
               "MINI_DUDEAI_CLAW_INSTANCE": "dudeclaw-02",
               "MINI_DUDEAI_TIER_SLO_URL": "http://brain.invalid/fleet/slo"}
        monkeypatch.setattr(cmp_mod, "fetch_json",
                            lambda url, timeout=0: (None, "refused"))
        tier, note = cmp_mod._probe_tier(env)
        assert tier is None  # primary's state doesn't count for the secondary


class TestMultiClawTickRouting:
    """W5.1: a --env instance must never clobber the primary's tick file."""

    def test_no_env_flag_keeps_legacy_basename(self):
        from mini_dudeai.claw_telemetry import CLAW_TICK_BASENAME
        assert cmp_mod._tick_basename_for(None, "dudeclaw-01") == \
            CLAW_TICK_BASENAME

    def test_explicit_default_env_still_primary(self):
        # `--env <the default path>` must not fork the primary's tick
        # into a secondary file (realpath comparison, not flag presence)
        from mini_dudeai.claw_telemetry import CLAW_TICK_BASENAME
        assert cmp_mod._tick_basename_for(cmp_mod.DEFAULT_ENV_PATH,
                                          "dudeclaw-01") == \
            CLAW_TICK_BASENAME

    def test_other_env_gets_per_device_basename(self, tmp_path):
        env = tmp_path / "mini_dudeai_claw02.env"
        env.write_text("MINI_DUDEAI_CLAW_DEVICE=dudeclaw-02\n")
        assert cmp_mod._tick_basename_for(str(env), "dudeclaw-02") == \
            "claw_last_tick.dudeclaw-02.json"

    def test_load_claw_env_custom_path(self, tmp_path):
        env = tmp_path / "claw02.env"
        env.write_text("# comment\nMINI_DUDEAI_NATS_SERVER=localhost:4222\n"
                       "MINI_DUDEAI_CLAW_DEVICE=dudeclaw-02\n")
        got = cmp_mod._load_claw_env(str(env))
        assert got["MINI_DUDEAI_CLAW_DEVICE"] == "dudeclaw-02"

    def test_load_claw_env_missing_custom_path_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            cmp_mod._load_claw_env(str(tmp_path / "absent.env"))


class TestQaFixes20260705:
    """QA review pins: MF001-consistent env anchor (V3.1), device-name
    fold refuse (sweep S4), and the end-to-end --env routing the wired
    fixture's `lambda *_a` had silently stopped covering (V8.2)."""

    def test_default_env_path_uses_real_user_home(self):
        from utils.paths import get_real_user_home
        assert cmp_mod.DEFAULT_ENV_PATH == str(
            get_real_user_home() / ".config" / "meshforge"
            / "mini_dudeai_claw.env")

    def test_folding_device_name_refused_loud(self, tmp_path):
        env = tmp_path / "claw.env"
        env.write_text("MINI_DUDEAI_CLAW_DEVICE=dude.claw-03\n")
        with pytest.raises(SystemExit, match="fold"):
            cmp_mod._tick_basename_for(str(env), "dude.claw-03")

    def test_main_env_flag_routes_tick_to_per_device_file(
            self, monkeypatch, tmp_path):
        # END-TO-END: main(["--env", ...]) must thread the basename into
        # the write — a refactor reverting to bare _tick_path() must fail
        # HERE, not silently clobber the primary tick in production.
        env = tmp_path / "claw02.env"
        env.write_text("MINI_DUDEAI_NATS_SERVER=nats://x\n"
                       "MINI_DUDEAI_CLAW_DEVICE=dudeclaw-02\n"
                       # required since the 07-23 identity gate: a secondary
                       # env must name its instance suffix
                       "MINI_DUDEAI_CLAW_INSTANCE=dudeclaw-02\n")
        monkeypatch.setattr(cmp_mod, "build_rows", lambda: ["row0"])
        monkeypatch.setattr(cmp_mod, "_probe_tier", lambda _e: (None, ""))
        written = []
        monkeypatch.setattr(
            cmp_mod, "_tick_path",
            lambda basename=None: written.append(basename) or str(
                tmp_path / (basename or "claw_last_tick.json")))
        monkeypatch.setattr(cmp_mod, "NatsConnection", lambda *a, **k: _FakeNC({
            "device_info": {"ok": True, "result": DI},
            "ble_stats": {"ok": True, "result": BS},
            "display_print": {"ok": True},
        }))
        rc = cmp_mod.main(["--env", str(env)])
        assert rc == 0
        assert written == ["claw_last_tick.dudeclaw-02.json"]
        assert (tmp_path / "claw_last_tick.dudeclaw-02.json").exists()
        assert not (tmp_path / "claw_last_tick.json").exists()


class TestSecondaryEnvRequiresInstance:
    """07-23 audit: one identity discriminator, not two. A secondary env
    without MINI_DUDEAI_CLAW_INSTANCE would write the secondary's tick file
    with a brain glyph proven from the PRIMARY's state — refuse loud."""

    def _base_env(self, tmp_path, extra=""):
        env = tmp_path / "mini_dudeai_claw02.env"
        env.write_text("MINI_DUDEAI_NATS_SERVER=localhost:4222\n"
                       "MINI_DUDEAI_CLAW_DEVICE=dudeclaw-02\n" + extra)
        return env

    def test_secondary_env_without_instance_exits_loud(self, tmp_path):
        env = self._base_env(tmp_path)
        with pytest.raises(SystemExit) as ei:
            cmp_mod.main(["--env", str(env)])
        assert "MINI_DUDEAI_CLAW_INSTANCE" in str(ei.value)

    def test_secondary_env_with_instance_passes_the_gate(self, tmp_path, monkeypatch):
        env = self._base_env(tmp_path, "MINI_DUDEAI_CLAW_INSTANCE=dudeclaw-02\n")
        # Gate passes -> execution proceeds to the NATS phase; stub it to
        # prove we got past the identity check without a real claw.
        sentinel = RuntimeError("reached-nats")

        def _boom(*a, **k):
            raise sentinel
        monkeypatch.setattr(cmp_mod, "build_rows", _boom)
        with pytest.raises(RuntimeError) as ei:
            cmp_mod.main(["--env", str(env)])
        assert ei.value is sentinel

    def test_default_env_needs_no_instance(self, tmp_path, monkeypatch):
        # The primary path (no --env) must NOT hit the new gate.
        env = tmp_path / "mini_dudeai_claw.env"
        env.write_text("MINI_DUDEAI_NATS_SERVER=localhost:4222\n"
                       "MINI_DUDEAI_CLAW_DEVICE=dudeclaw-01\n")
        monkeypatch.setattr(cmp_mod, "DEFAULT_ENV_PATH", str(env))
        sentinel = RuntimeError("reached-nats")

        def _boom(*a, **k):
            raise sentinel
        monkeypatch.setattr(cmp_mod, "build_rows", _boom)
        with pytest.raises(RuntimeError):
            cmp_mod.main([])
