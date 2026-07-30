"""Per-box override for the host_memory_pressure LEVEL legs (2026-07-30).

WHY it exists: the 20% availability gate is fleet-wide and correct as a ratio,
but it assumes every box's NORMAL sits above it. moc3 (905 MB) lives at
19.8-20.3% available and has held that for five weeks of unbroken uptime with no
reset — so there the gate fires on the box's own steady state, flaps across the
line every few minutes, carries no information, and trains the operator to
ignore the one line that means "this box is about to be hardware-reset".

WHY it is safe: the level legs are the tombstone, not the warning (this module's
own rate-leg comment measures it — level fires ~4 s before the 07-24 reset, rate
fires ~94 s before). The RATE leg is deliberately NOT overridable, and on a box
tuned quiet it is permanently armed anyway because its floor is 35% availability.

The load-bearing property pinned here: **the override can only ever make the
warning quieter, so anything malformed must fall back to the strict fleet
defaults and leave a witness.** A typo must not be able to disarm the gate.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.watchdog_probe_core import reset_dispositions  # noqa: E402
from utils.watchdog_probes_host import (  # noqa: E402
    DEFAULT_DEGRADED_AVAIL_RATIO,
    DEFAULT_WEDGE_AVAIL_RATIO,
    _AVAIL_OVERRIDE_WARNED,
    _read_avail_overrides,
    probe_host_memory_pressure,
)

# moc3's real numbers: 926,828 kB total, ~184,000 kB available = 19.9%.
MOC3_TOTAL_KB = 926828
MOC3_AVAIL_KB = 184172


def _meminfo(tmp_path, total_kb, avail_kb, name="meminfo"):
    p = tmp_path / name
    p.write_text("MemTotal:       %d kB\nMemFree:        1000 kB\n"
                 "MemAvailable:   %d kB\nShmem: 0 kB\nSlab: 0 kB\n"
                 "SwapTotal: 0 kB\nSwapFree: 0 kB\n" % (total_kb, avail_kb))
    return str(p)


def _cfg(tmp_path, doc, name="host_memory_thresholds.json"):
    p = tmp_path / name
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return str(p)


def _run(tmp_path, *, avail_kb, total_kb=MOC3_TOTAL_KB, cfg=None, ticks=2,
         streak="streak.json"):
    """Drive the probe past its 2-tick debounce and return the last Signal."""
    reset_dispositions()
    sig = None
    for _ in range(ticks):
        sig = probe_host_memory_pressure(
            meminfo_path=_meminfo(tmp_path, total_kb, avail_kb),
            psi_path=str(tmp_path / "no-psi"),          # PSI absent, as on moc3
            proc_root=str(tmp_path / "no-proc"),
            debounce_path=str(tmp_path / streak),
            cgroup_root=str(tmp_path / "no-cgroup"),
            boot_id_path=str(tmp_path / "no-bootid"),
            avail_config_path=cfg,
        )
    return sig


@pytest.fixture(autouse=True)
def _clear_warn_cache():
    _AVAIL_OVERRIDE_WARNED.clear()
    yield
    _AVAIL_OVERRIDE_WARNED.clear()


class TestHermeticByDefault:
    """The probe must NOT reach for the operator home on its own — otherwise
    every existing test that omits the ratios silently depends on whether the
    box running the suite carries an override file
    (feedback_tests_must_pin_ambient_state)."""

    def test_no_config_path_means_no_override_lookup(self, tmp_path):
        assert _read_avail_overrides(None) == (None, None)

    def test_moc3_numbers_still_fire_on_fleet_defaults(self, tmp_path):
        sig = _run(tmp_path, avail_kb=MOC3_AVAIL_KB)
        assert sig is not None and sig.severity == "degraded"
        assert sig.extra["avail_thresholds_source"] == "fleet-default"
        assert sig.extra["degraded_avail_ratio"] == DEFAULT_DEGRADED_AVAIL_RATIO

    def test_runner_helper_resolves_a_path_without_reading_it(self):
        """The helper names the file; it must not require it to exist."""
        from utils.watchdog_probes_host import (
            default_host_memory_thresholds_path)
        p = default_host_memory_thresholds_path()
        assert p is None or p.endswith("host_memory_thresholds.json")


class TestOverrideApplies:
    def test_moc3_at_15_percent_gate_is_quiet_at_its_normal_level(self, tmp_path):
        """The whole point: moc3's steady state stops being a finding."""
        sig = _run(tmp_path, avail_kb=MOC3_AVAIL_KB,
                   cfg=_cfg(tmp_path, {"degraded_avail_ratio": 0.15}))
        assert sig is None

    def test_the_same_box_still_fires_below_the_tuned_gate(self, tmp_path):
        """Tuned quieter, NOT disarmed — 13% must still page."""
        sig = _run(tmp_path, avail_kb=int(MOC3_TOTAL_KB * 0.13),
                   cfg=_cfg(tmp_path, {"degraded_avail_ratio": 0.15}))
        assert sig is not None and sig.severity == "degraded"
        assert sig.extra["avail_thresholds_source"] == "per-box override"
        assert sig.extra["degraded_avail_ratio"] == 0.15

    def test_wedge_rung_survives_a_degraded_override(self, tmp_path):
        """Lowering the warning must not swallow the emergency."""
        sig = _run(tmp_path, avail_kb=int(MOC3_TOTAL_KB * 0.05),
                   cfg=_cfg(tmp_path, {"degraded_avail_ratio": 0.15}), ticks=1)
        assert sig is not None and sig.severity == "wedge"
        assert sig.extra["wedge_avail_ratio"] == DEFAULT_WEDGE_AVAIL_RATIO

    def test_explicit_argument_beats_the_config_file(self, tmp_path):
        """Existing tests pin ratios explicitly; a config must never perturb them."""
        sig = _run(tmp_path, avail_kb=int(MOC3_TOTAL_KB * 0.18), ticks=2,
                   cfg=_cfg(tmp_path, {"degraded_avail_ratio": 0.15}))
        assert sig is None                       # 18% > 15% override
        reset_dispositions()
        sig2 = None
        for _ in range(2):
            sig2 = probe_host_memory_pressure(
                meminfo_path=_meminfo(tmp_path, MOC3_TOTAL_KB,
                                      int(MOC3_TOTAL_KB * 0.18)),
                psi_path=str(tmp_path / "no-psi"),
                proc_root=str(tmp_path / "no-proc"),
                debounce_path=str(tmp_path / "s2.json"),
                cgroup_root=str(tmp_path / "no-cgroup"),
                boot_id_path=str(tmp_path / "no-bootid"),
                avail_config_path=_cfg(tmp_path, {"degraded_avail_ratio": 0.15}),
                degraded_avail_ratio=0.20,       # explicit wins
            )
        assert sig2 is not None and sig2.extra["degraded_avail_ratio"] == 0.20

    def test_the_signal_carries_the_gate_that_judged_it(self, tmp_path):
        """A tuned gate must be visible in the page — an operator asking 'why
        didn't this fire sooner?' should not have to find a config file."""
        sig = _run(tmp_path, avail_kb=int(MOC3_TOTAL_KB * 0.10),
                   cfg=_cfg(tmp_path, {"degraded_avail_ratio": 0.15}))
        assert sig.extra["avail_thresholds_source"] == "per-box override"
        assert "degraded_avail_ratio" in sig.extra


class TestMalformedOverrideFailsStrict:
    """THE load-bearing property. This switch's only power is to make a warning
    quieter, so every unusable form must fall back to the fleet defaults."""

    @pytest.mark.parametrize("doc,why", [
        ("{ not json", "unparseable"),
        ("[1, 2]", "not an object"),
        ({"degraded_avail_ratio": "0.15"}, "string not number"),
        ({"degraded_avail_ratio": True}, "bool is not 1.0"),
        ({"degraded_avail_ratio": 0.0}, "zero"),
        ({"degraded_avail_ratio": 1.5}, "above 1"),
        ({"degraded_avail_ratio": -0.2}, "negative"),
        ({"degraded_avail_ratio": 0.05}, "below the wedge rung (inverted)"),
        ({"wedge_avail_ratio": 0.30}, "wedge above degraded (inverted)"),
        ({"unrelated": 1}, "no threshold keys"),
    ])
    def test_unusable_override_falls_back_to_fleet_defaults(self, tmp_path,
                                                            doc, why):
        sig = _run(tmp_path, avail_kb=MOC3_AVAIL_KB, cfg=_cfg(tmp_path, doc))
        assert sig is not None, (
            "a %s override silenced the gate — a malformed silence-manufacturing "
            "switch must fail toward paging" % why)
        assert sig.extra["degraded_avail_ratio"] == DEFAULT_DEGRADED_AVAIL_RATIO
        assert sig.extra["avail_thresholds_source"] == "fleet-default"

    def test_absent_file_is_silent_not_warned(self, tmp_path):
        """Most boxes have no file; that is normal, not a problem."""
        _read_avail_overrides(str(tmp_path / "nope.json"))
        assert not _AVAIL_OVERRIDE_WARNED

    def test_malformed_file_leaves_a_witness(self, tmp_path, caplog):
        """Every swallow leaves a witness (#9) — a typo must be findable."""
        import logging
        p = _cfg(tmp_path, "{ not json")
        with caplog.at_level(logging.WARNING, logger="watchdog"):
            _read_avail_overrides(p)
        assert any("IGNORED" in r.message or "IGNORED" in r.getMessage()
                   for r in caplog.records), caplog.text
        assert p in _AVAIL_OVERRIDE_WARNED

    def test_witness_is_logged_once_per_path(self, tmp_path, caplog):
        """A 30 s tick must not reprint forever."""
        import logging
        p = _cfg(tmp_path, "{ not json")
        with caplog.at_level(logging.WARNING, logger="watchdog"):
            for _ in range(5):
                _read_avail_overrides(p)
        hits = [r for r in caplog.records if "IGNORED" in r.getMessage()]
        assert len(hits) == 1, "expected one witness, got %d" % len(hits)


class TestRateLegIsNotOverridable:
    def test_no_rate_keys_are_honoured_from_the_config(self, tmp_path):
        """A box tuned quiet on LEVEL must keep its early leg at full
        sensitivity, or the tuning becomes a blindfold."""
        deg, wed = _read_avail_overrides(
            _cfg(tmp_path, {"degraded_avail_ratio": 0.15,
                            "rate_drop_ratio": 0.99,
                            "rate_floor_ratio": 0.01}))
        assert (deg, wed) == (0.15, None)     # rate keys simply ignored

    def test_rate_leg_signature_has_no_config_path(self):
        import inspect
        from utils.watchdog_probes_host import (
            _probe_host_memory_pressure_impl as impl)
        params = inspect.signature(impl).parameters
        assert "avail_config_path" in params
        for p in ("rate_drop_ratio", "rate_floor_ratio"):
            assert params[p].default is not None, (
                "%s must keep a concrete fleet default — it is deliberately "
                "not per-box tunable" % p)
