"""Tests for the manager-side scout collector
(scripts/router_scout_pull.sh, 2026-07-11 OpenWrt-router arc).

Run the real bash script hermetically via the SCOUT_PULL_CMD seam (the
raven_soak_watch pattern): a fake fetch executable whose stdout/exit the
test controls. No ssh, no router.

Contract under test (honest_failure_modes: absence ≠ recovery):
  * a VALID tick lands atomically as <device>_tick.json — even on a
    threshold FAIL (fresh real data must reach kilo + the probe);
  * unreachable / garbage NEVER touches the previous mirrored tick —
    it ages honestly and the watchdog sees a stale mirror for what it is;
  * no SCOUT_SSH and no seam → fail loud (MF014: no target in repo);
  * the mirror dirname is pinned to kilo's SCOUT_MIRROR_SUBDIR (two
    consumers, one constant).
"""

import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent
          / "scripts" / "router_scout_pull.sh")


def make_tick(device="owrt-test", age_s=60, maps=110, ok=True,
              persistence_ok=True, errors=None):
    return {
        "schema": 1, "device": device,
        "captured_at": time.time() - age_s,
        "ok": ok, "errors": errors or [], "notes": [],
        "service": {"name": "meshtasticd", "running": True, "pid": 100},
        "meshtasticd": {"vsz_kb": 18300, "rss_kb": 9000, "maps": maps,
                        "age_s": 4000},
        "radio_tcp": "ok",
        "host": {"uptime_s": 5000, "load_1m": 0.15,
                 "mem_available_kb": 800000, "mem_total_kb": 1000000},
        "persistence": {"ok": persistence_ok,
                        "data_dir": "/etc/meshtasticd/data",
                        "fstype": "ext4"},
        "opkg_hold": True,
    }


@pytest.fixture
def sandbox(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    binp = tmp_path / "bin"
    binp.mkdir()
    fetch = binp / "fake_fetch"
    fetch.write_text('#!/bin/bash\ncat "$SCOUT_TEST_FETCH_OUT"\n'
                     'exit "${SCOUT_TEST_FETCH_EXIT:-0}"\n')
    fetch.chmod(0o755)
    return SimpleNamespace(
        home=home,
        fetch=fetch,
        fetch_out=tmp_path / "fetch_out",
        mirror=home / "mirror",
        log=home / "router_scout_pull.log",
    )


def run(sb, payload, *, fetch_exit=0, env_extra=None):
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload)
    sb.fetch_out.write_text(payload)
    env = {
        **os.environ,
        "HOME": str(sb.home),
        "SCOUT_PULL_CMD": str(sb.fetch),
        "SCOUT_MIRROR_DIR": str(sb.mirror),
        "SCOUT_PULL_LOG": str(sb.log),
        "SCOUT_TEST_FETCH_OUT": str(sb.fetch_out),
        "SCOUT_TEST_FETCH_EXIT": str(fetch_exit),
    }
    env.update(env_extra or {})
    proc = subprocess.run(["bash", str(SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=60)
    log = sb.log.read_text() if sb.log.exists() else ""
    return proc.returncode, log


def mirrored(sb, device="owrt-test"):
    p = sb.mirror / f"{device}_tick.json"
    return json.loads(p.read_text()) if p.exists() else None


class TestHappyPath:
    def test_valid_tick_lands_atomically_ok(self, sandbox):
        rc, log = run(sandbox, make_tick())
        assert rc == 0
        assert "OK owrt-test" in log
        tick = mirrored(sandbox)
        assert tick["device"] == "owrt-test"
        # no temp files left behind
        strays = [p.name for p in sandbox.mirror.iterdir()
                  if not p.name.endswith("_tick.json")]
        assert strays == []

    def test_per_device_filenames(self, sandbox):
        run(sandbox, make_tick(device="owrt-a"))
        run(sandbox, make_tick(device="owrt-b"))
        assert mirrored(sandbox, "owrt-a") is not None
        assert mirrored(sandbox, "owrt-b") is not None


class TestThresholds:
    def test_stale_tick_fails_but_still_lands(self, sandbox):
        rc, log = run(sandbox, make_tick(age_s=10000))
        assert rc == 1
        assert "stale" in log
        assert mirrored(sandbox) is not None   # fresh real data still lands

    def test_maps_over_line_fails(self, sandbox):
        rc, log = run(sandbox, make_tick(maps=60000))
        assert rc == 1
        assert "10468" in log or "maps=60000" in log
        assert mirrored(sandbox) is not None

    def test_tmpfs_persistence_fails(self, sandbox):
        rc, log = run(sandbox, make_tick(persistence_ok=False))
        assert rc == 1
        assert "tmpfs" in log

    def test_agent_self_report_false_fails_with_witness(self, sandbox):
        rc, log = run(sandbox, make_tick(ok=False,
                                         errors=["ubus unavailable"]))
        assert rc == 1
        assert "ok=false" in log and "ubus unavailable" in log


class TestDegradedNeverClobbers:
    def test_unreachable_fails_and_previous_mirror_untouched(self, sandbox):
        run(sandbox, make_tick(maps=111))
        before = mirrored(sandbox)
        rc, log = run(sandbox, "", fetch_exit=255)
        assert rc == 1
        assert "unreachable" in log
        assert mirrored(sandbox) == before

    def test_garbage_json_fails_and_previous_mirror_untouched(self, sandbox):
        run(sandbox, make_tick(maps=112))
        before = mirrored(sandbox)
        rc, log = run(sandbox, "not json {")
        assert rc == 1
        assert "invalid tick" in log
        assert mirrored(sandbox) == before

    def test_missing_device_is_invalid(self, sandbox):
        tick = make_tick()
        del tick["device"]
        rc, log = run(sandbox, tick)
        assert rc == 1
        assert "shape drift" in log
        assert mirrored(sandbox) is None

    def test_path_shaped_device_is_refused(self, sandbox):
        """device becomes a filename — traversal shapes must die loud."""
        rc, log = run(sandbox, make_tick(device="../evil"))
        assert rc == 1
        assert "not filename-safe" in log
        assert not (sandbox.home / "evil_tick.json").exists()

    def test_non_object_tick_is_invalid(self, sandbox):
        rc, log = run(sandbox, [1, 2, 3])
        assert rc == 1
        assert "not an object" in log


class TestConfig:
    def test_no_target_and_no_seam_fails_loud(self, sandbox):
        env = {k: v for k, v in os.environ.items()
               if k not in ("SCOUT_SSH", "SCOUT_PULL_CMD")}
        env.update({"HOME": str(sandbox.home),
                    "SCOUT_PULL_LOG": str(sandbox.log)})
        proc = subprocess.run(["bash", str(SCRIPT)], env=env,
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 1
        assert "SCOUT_SSH unset" in sandbox.log.read_text()

    def test_mirror_dirname_pinned_to_kilo_constant(self):
        """THREE consumers, ONE constant (honest_failure_modes #5): the
        pull script's default mirror dir AND the watchdog probe's module
        constant must both equal kilo's SCOUT_MIRROR_SUBDIR — an unpinned
        copy that drifts sends the probe permanently INERT (it reads the
        old path, sees no ticks, never fires) with no test failure."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        from kilo.ingest import SCOUT_MIRROR_SUBDIR
        from utils.watchdog_probes_drift import ROUTER_SCOUT_MIRROR_SUBDIR
        assert f"$HOME/{SCOUT_MIRROR_SUBDIR}" in SCRIPT.read_text()
        assert ROUTER_SCOUT_MIRROR_SUBDIR == SCOUT_MIRROR_SUBDIR

    def test_thresholds_pinned_across_agent_pull_and_probe(self):
        """The same operational thresholds exist in three places by design
        (agent restart-line, pull alert-line, probe stale-line) — their
        DEFAULTS must not drift (honest_failure_modes #5). An operator
        override is deliberate; divergent defaults are not."""
        import re
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        from utils.watchdog_probes_drift import ROUTER_SCOUT_TICK_STALE_S
        agent = (Path(__file__).resolve().parent.parent
                 / "templates" / "openwrt" / "meshforge-scout").read_text()
        pull = SCRIPT.read_text()
        agent_maps = re.search(r"MAPS_RESTART:-(\d+)", agent).group(1)
        pull_maps = re.search(r"SCOUT_MAPS_FAIL:-(\d+)", pull).group(1)
        assert agent_maps == pull_maps
        pull_stale = re.search(r"SCOUT_AGENT_STALE_S:-(\d+)", pull).group(1)
        assert float(pull_stale) == ROUTER_SCOUT_TICK_STALE_S

    def test_device_charsets_pinned_writer_within_reader(self):
        """The agent's sanitize_device charset must stay a subset of the
        pull's filename-safety regex — a writer-legal, reader-illegal
        device would emit ticks the manager rejects forever."""
        agent = (Path(__file__).resolve().parent.parent
                 / "templates" / "openwrt" / "meshforge-scout").read_text()
        assert "tr -cd 'A-Za-z0-9._-' | cut -c1-64" in agent
        assert r"[A-Za-z0-9._-]{1,64}" in SCRIPT.read_text()
