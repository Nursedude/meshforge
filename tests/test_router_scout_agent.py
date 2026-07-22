"""Tests for the router-resident telemetry agent
(templates/openwrt/meshforge-scout, 2026-07-11 OpenWrt-router arc).

These run the REAL busybox-ash script hermetically: a fake /proc tree
(SCOUT_PROC_ROOT), PATH stubs for ubus/uci, env seams for nc
(SCOUT_NC) / restart (SCOUT_RESTART_CMD) / opkg status
(SCOUT_OPKG_STATUS). Prefers ``busybox sh`` when present (the target
interpreter) and falls back to ``sh``.

Honest-failure contract under test (honest_failure_modes.md): every
degraded read lands as null + a witness in errors[] (never a
healthy-looking value); nc-absent reads "unavailable" and ok=false,
never "ok"; a tmpfs data_dir is a loud persistence failure (the
config-vanishes-on-reboot gotcha); the tick write is atomic.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent
          / "templates" / "openwrt" / "meshforge-scout")


def _interp():
    if shutil.which("busybox"):
        return ["busybox", "sh"]
    return ["sh"]


UBUS_RUNNING = ('{ "meshtasticd": { "instances": { "instance1": { '
                '"running": true, "pid": 100 } } } }')
UBUS_NOT_RUNNING = "{ }"

STAT_LINE = ("100 (meshtasticd) S 1 100 100 0 -1 4194560 1000 0 0 0 "
             "10 20 0 0 20 0 8 0 100000 18739200 2250 4294967295")
# field 22 (starttime) = 100000 ticks -> 1000 s; uptime 5000 -> age 4000 s


@pytest.fixture
def sandbox(tmp_path):
    proc = tmp_path / "proc"
    pid = proc / "100"
    pid.mkdir(parents=True)
    (proc / "uptime").write_text("5000.00 4000.00\n")
    (proc / "loadavg").write_text("0.15 0.10 0.05 1/100 1234\n")
    (proc / "meminfo").write_text(
        "MemTotal:        1000000 kB\n"
        "MemFree:          500000 kB\n"
        "MemAvailable:     800000 kB\n")
    (proc / "mounts").write_text(
        "/dev/root / ext4 rw 0 0\n"
        "tmpfs /var tmpfs rw 0 0\n"
        "tmpfs /tmp tmpfs rw 0 0\n")
    # 4403 = 0x1133; st 0A = LISTEN (header line + one listener)
    (proc / "net").mkdir()
    (proc / "net" / "tcp").write_text(
        "  sl  local_address rem_address   st\n"
        "   0: 00000000:1133 00000000:0000 0A\n")
    (pid / "status").write_text(
        "Name:\tmeshtasticd\nVmSize:\t   18300 kB\nVmRSS:\t    9000 kB\n")
    (pid / "maps").write_text("map-line\n" * 110)
    (pid / "stat").write_text(STAT_LINE + "\n")

    binp = tmp_path / "bin"
    binp.mkdir()
    ubus = binp / "ubus"
    ubus.write_text('#!/bin/sh\ncat "$SCOUT_TEST_UBUS_OUT"\n'
                    'exit "${SCOUT_TEST_UBUS_RC:-0}"\n')
    ubus.chmod(0o755)
    uci = binp / "uci"
    uci.write_text('#!/bin/sh\n[ -n "${SCOUT_TEST_UCI_OUT:-}" ] || exit 1\n'
                   'printf "%s\\n" "$SCOUT_TEST_UCI_OUT"\n')
    uci.chmod(0o755)
    restart = binp / "fake_restart"
    restart.write_text('#!/bin/sh\nprintf x >> "$SCOUT_TEST_RESTART_CALLS"\n'
                       'exit "${SCOUT_TEST_RESTART_EXIT:-0}"\n')
    restart.chmod(0o755)

    ubus_out = tmp_path / "ubus_out"
    ubus_out.write_text(UBUS_RUNNING)
    opkg_status = tmp_path / "opkg_status"
    opkg_status.write_text(
        "Package: meshtasticd-full\nVersion: 2.7.26\n"
        "Status: install hold installed\n\n"
        "Package: other\nStatus: install ok installed\n")

    return SimpleNamespace(
        tmp=tmp_path, proc=proc, binp=binp,
        ubus_out=ubus_out, opkg_status=opkg_status,
        tick=tmp_path / "scout_tick.json",
        log=tmp_path / "scout.log",
        restart=restart,
        restart_calls=tmp_path / "restart_calls",
    )


def run(sb, *args, env_extra=None, conf_lines=None):
    conf = sb.tmp / "scout.conf"
    conf.write_text("".join((conf_lines or [])))
    env = {
        **os.environ,
        "PATH": f"{sb.binp}:{os.environ.get('PATH', '')}",
        "SCOUT_CONF": str(conf),
        "SCOUT_DEVICE": "owrt-test",
        "SCOUT_TICK": str(sb.tick),
        "SCOUT_LOG": str(sb.log),
        "SCOUT_PROC_ROOT": str(sb.proc),
        "SCOUT_OPKG_STATUS": str(sb.opkg_status),
        "SCOUT_RESTART_CMD": str(sb.restart),
        "SCOUT_TEST_UBUS_OUT": str(sb.ubus_out),
        "SCOUT_TEST_RESTART_CALLS": str(sb.restart_calls),
    }
    env.update(env_extra or {})
    proc = subprocess.run(_interp() + [str(SCRIPT)] + list(args),
                          env=env, capture_output=True, text=True,
                          timeout=60)
    tick = None
    if sb.tick.exists():
        tick = json.loads(sb.tick.read_text())
    log = sb.log.read_text() if sb.log.exists() else ""
    restarts = (sb.restart_calls.read_text().count("x")
                if sb.restart_calls.exists() else 0)
    return proc, tick, log, restarts


class TestCollectHealthy:
    def test_healthy_tick_shape(self, sandbox):
        proc, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert proc.returncode == 0
        assert tick["ok"] is True
        assert tick["errors"] == []
        assert tick["device"] == "owrt-test"
        assert isinstance(tick["captured_at"], int)
        assert tick["service"] == {"name": "meshtasticd", "running": True,
                                   "pid": 100}
        assert tick["meshtasticd"] == {"vsz_kb": 18300, "rss_kb": 9000,
                                       "maps": 110, "age_s": 4000}
        assert tick["radio_tcp"] == "ok"
        assert tick["host"]["uptime_s"] == 5000
        assert tick["host"]["mem_available_kb"] == 800000
        assert tick["host"]["load_1m"] == 0.15
        assert tick["persistence"]["ok"] is True

    def test_default_data_dir_on_tmpfs_var(self, sandbox):
        """With no uci override the package default /var/lib/meshtasticd
        rides the tmpfs /var mount — the reboot-blank gotcha, loud."""
        _, tick, _, _ = run(sandbox, "collect")
        assert tick["persistence"]["ok"] is False
        assert tick["persistence"]["data_dir"] == "/var/lib/meshtasticd"
        assert tick["ok"] is False

    def test_persistent_data_dir_reads_ok(self, sandbox):
        proc, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert tick["persistence"] == {"ok": True,
                                       "data_dir": "/etc/meshtasticd/data",
                                       "fstype": "ext4"}
        assert tick["ok"] is True

    def test_opkg_hold_detected(self, sandbox):
        _, tick, _, _ = run(sandbox, "collect")
        assert tick["opkg_hold"] is True

    def test_opkg_status_absent_is_benign_note(self, sandbox):
        """apk-based OpenWrt has no opkg status — unobservable, NOT an
        error (a note, so ok stays true)."""
        _, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_OPKG_STATUS": str(sandbox.tmp / "nope"),
                       "SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert tick["opkg_hold"] is None
        assert tick["ok"] is True
        assert any("opkg" in n for n in tick["notes"])

    def test_show_prints_tick(self, sandbox):
        run(sandbox, "collect")
        proc, _, _, _ = run(sandbox, "show")
        assert proc.returncode == 0
        assert '"device": "owrt-test"' in proc.stdout


class TestCollectDegraded:
    def test_tmpfs_data_dir_is_loud_persistence_failure(self, sandbox):
        _, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_TEST_UCI_OUT": "/var/lib/meshtasticd"})
        assert tick["persistence"]["ok"] is False
        assert tick["persistence"]["fstype"] == "tmpfs"
        assert tick["ok"] is False
        assert any("VANISHES" in e or "tmpfs" in e for e in tick["errors"])

    def test_proc_net_unreadable_is_null_plus_witness(self, sandbox):
        """No /proc/net/tcp[6] → unobservable, never 'down' (the minimal-
        busybox-nc lesson: tool failure must not read as an observation)."""
        import shutil
        shutil.rmtree(sandbox.proc / "net")
        _, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert tick["radio_tcp"] is None
        assert tick["ok"] is False
        assert any("unobservable" in e and "tcp" in e
                   for e in tick["errors"])

    def test_no_listener_is_down_a_real_observation(self, sandbox):
        (sandbox.proc / "net" / "tcp").write_text(
            "  sl  local_address rem_address   st\n"
            "   0: 00000000:0016 00000000:0000 0A\n")   # only :22 listening
        _, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert tick["radio_tcp"] == "down"
        # a missing listener is an observation, not a blind spot
        assert not any("radio" in e.lower() for e in tick["errors"])

    def test_listener_in_tcp6_only_reads_ok(self, sandbox):
        (sandbox.proc / "net" / "tcp").write_text(
            "  sl  local_address rem_address   st\n")
        (sandbox.proc / "net" / "tcp6").write_text(
            "  sl  local_address rem_address   st\n"
            "   0: 00000000000000000000000000000000:1133 "
            "00000000000000000000000000000000:0000 0A\n")
        _, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert tick["radio_tcp"] == "ok"

    def test_established_connection_on_port_is_not_listen(self, sandbox):
        """Only st 0A (LISTEN) counts — an ESTABLISHED (01) row on the
        port must not read as a live listener."""
        (sandbox.proc / "net" / "tcp").write_text(
            "  sl  local_address rem_address   st\n"
            "   0: 0100007F:1133 0100007F:E1C4 01\n")
        _, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert tick["radio_tcp"] == "down"

    def test_service_not_running_is_false_not_null(self, sandbox):
        sandbox.ubus_out.write_text(UBUS_NOT_RUNNING)
        _, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert tick["service"]["running"] is False
        assert tick["service"]["pid"] is None
        # ubus answered — a stopped service is truth, not degraded reading
        assert not any("ubus" in e for e in tick["errors"])
        assert tick["meshtasticd"]["vsz_kb"] is None

    def test_ubus_failure_is_null_plus_witness_not_down(self, sandbox):
        """ubus erroring must NOT read as service-down (degraded state
        mapped to a valid-looking value — the #80 class)."""
        _, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_TEST_UBUS_RC": "1",
                       "SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert tick["service"]["running"] is None
        assert tick["ok"] is False
        assert any("ubus" in e for e in tick["errors"])

    def test_pid_without_proc_entry_is_witnessed(self, sandbox):
        sandbox.ubus_out.write_text(UBUS_RUNNING.replace('"pid": 100',
                                                         '"pid": 999'))
        _, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert tick["meshtasticd"]["vsz_kb"] is None
        assert tick["ok"] is False
        assert any("disagree" in e for e in tick["errors"])

    def test_missing_starttime_is_null_age_with_witness(self, sandbox):
        """A stat file whose field 22 is absent must NOT fabricate
        age == host uptime (ash arithmetic reads a missing operand as 0 —
        verified live). Null + witness, never a plausible wrong number."""
        (sandbox.proc / "100" / "stat").write_text("100 (meshtasticd) S 1\n")
        _, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert tick["meshtasticd"]["age_s"] is None
        assert tick["ok"] is False
        assert any("starttime" in e for e in tick["errors"])

    def test_missing_meminfo_field_is_witnessed(self, sandbox):
        """A host field failing to read must not hide behind a silent null
        with ok=true — every degraded read leaves a witness."""
        (sandbox.proc / "meminfo").write_text("MemTotal: 1000000 kB\n")
        _, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert tick["host"]["mem_available_kb"] is None
        assert tick["ok"] is False
        assert any("MemAvailable" in e for e in tick["errors"])

    def test_malformed_loadavg_emits_null_not_invalid_json(self, sandbox):
        """A torn multi-dot loadavg token must become null, never an
        unquoted 1.2.3 that breaks json.loads for the whole tick."""
        (sandbox.proc / "loadavg").write_text("1.2.3 0.10 0.05 1/100 1\n")
        proc, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert tick is not None          # json.loads in run() succeeded
        assert tick["host"]["load_1m"] is None

    def test_hostile_device_id_sanitized_to_pull_safe_charset(self, sandbox):
        """device flows into the pull's filename regex [A-Za-z0-9._-]{1,64}
        — a hostname with spaces/colons must be emitted pull-legal, or the
        manager rejects every tick forever."""
        import re
        _, tick, _, _ = run(
            sandbox, "collect",
            env_extra={"SCOUT_DEVICE": "bad host:name/../x@y",
                       "SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"})
        assert re.fullmatch(r"[A-Za-z0-9._-]{1,64}", tick["device"])

    def test_guessed_data_dir_leaves_a_note(self, sandbox):
        """No uci value → the persistence verdict is about the GUESSED
        package default; the tick must say so."""
        _, tick, _, _ = run(sandbox, "collect")
        assert any("guessed" in n for n in tick["notes"])

    def test_atomic_write_no_tmp_left_behind(self, sandbox):
        run(sandbox, "collect")
        leftovers = [p for p in sandbox.tmp.iterdir()
                     if p.name.startswith("scout_tick.json.tmp")]
        assert leftovers == []
        # and the tick parses (json.loads in run() already proved it)


class TestRtunWatchdog:
    """rtun self-heal watchdog liveness surfaced in the tick (2026-07-21
    tunnel-wedge arc). Honest-failure contract: absence/staleness never read
    as fresh (age null + a carrying state), and watchdog liveness is kept
    SEPARATE from the meshtasticd verdict (never flips ok)."""

    ENV_OK = {"SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"}

    def test_unconfigured_is_not_configured_null_age(self, sandbox):
        """No RTUN_HEARTBEAT → this box runs no watchdog: benign, ok stays
        true, no note, age null."""
        _, tick, _, _ = run(sandbox, "collect", env_extra=self.ENV_OK)
        assert tick["rtun_watchdog"] == {"state": "not_configured",
                                         "heartbeat_age_s": None}
        assert tick["ok"] is True
        assert not any("rtun" in n for n in tick["notes"])

    def test_fresh_heartbeat_is_ok_with_age(self, sandbox):
        hb = sandbox.tmp / "rtun_hb"
        hb.write_text("2026-07-22 07:15:00 TUN_OK\n")   # mtime = now
        _, tick, _, _ = run(sandbox, "collect", env_extra=self.ENV_OK,
                            conf_lines=[f'RTUN_HEARTBEAT={hb}\n'])
        assert tick["rtun_watchdog"]["state"] == "ok"
        assert isinstance(tick["rtun_watchdog"]["heartbeat_age_s"], int)
        assert 0 <= tick["rtun_watchdog"]["heartbeat_age_s"] < 120
        assert tick["ok"] is True

    def test_stale_heartbeat_is_stale_noted_not_ok_false(self, sandbox):
        """An old heartbeat is surfaced (state=stale + note) but does NOT
        flip the meshtasticd verdict — watchdog liveness is its own concern."""
        hb = sandbox.tmp / "rtun_hb"
        hb.write_text("old\n")
        old = int(hb.stat().st_mtime) - 9999
        os.utime(hb, (old, old))
        _, tick, _, _ = run(sandbox, "collect", env_extra=self.ENV_OK,
                            conf_lines=[f'RTUN_HEARTBEAT={hb}\n',
                                        "RTUN_STALE_S=1200\n"])
        assert tick["rtun_watchdog"]["state"] == "stale"
        assert tick["rtun_watchdog"]["heartbeat_age_s"] >= 9999
        assert any("stale" in n and "rtun" in n for n in tick["notes"])
        assert tick["ok"] is True

    def test_absent_heartbeat_when_configured_is_noted(self, sandbox):
        """Configured but the file is missing → the watchdog is expected and
        not running: age null, state absent, a note — never a fresh-looking 0."""
        missing = sandbox.tmp / "does_not_exist"
        _, tick, _, _ = run(sandbox, "collect", env_extra=self.ENV_OK,
                            conf_lines=[f'RTUN_HEARTBEAT={missing}\n'])
        assert tick["rtun_watchdog"] == {"state": "absent",
                                         "heartbeat_age_s": None}
        assert any("absent" in n and "rtun" in n for n in tick["notes"])
        assert tick["ok"] is True

    def test_future_mtime_clamps_to_zero_never_negative(self, sandbox):
        """Clock skew / future mtime must clamp to 0, not emit a negative age
        (honest_failure_modes #6: forgeable wall-clock deltas)."""
        hb = sandbox.tmp / "rtun_hb"
        hb.write_text("future\n")
        future = int(hb.stat().st_mtime) + 5000
        os.utime(hb, (future, future))
        _, tick, _, _ = run(sandbox, "collect", env_extra=self.ENV_OK,
                            conf_lines=[f'RTUN_HEARTBEAT={hb}\n'])
        assert tick["rtun_watchdog"]["heartbeat_age_s"] == 0
        assert tick["rtun_watchdog"]["state"] == "ok"


class TestCheck:
    ENV_OK = {"SCOUT_TEST_UCI_OUT": "/etc/meshtasticd/data"}

    def test_healthy_check_logs_ok_no_restart(self, sandbox):
        proc, _, log, restarts = run(sandbox, "check", env_extra=self.ENV_OK)
        assert proc.returncode == 0
        assert "OK uptime=5000 maps=110 vsz_kb=18300" in log
        assert restarts == 0

    def test_maps_over_threshold_restarts_exactly_once(self, sandbox):
        proc, _, log, restarts = run(
            sandbox, "check", env_extra=self.ENV_OK,
            conf_lines=["MAPS_RESTART=100\n"])
        assert restarts == 1
        assert proc.returncode == 0     # the designed maintenance action
        assert "RESTART" in log and "maps>100" in log

    def test_maps_restart_failure_is_loud(self, sandbox):
        proc, _, log, restarts = run(
            sandbox, "check",
            env_extra={**self.ENV_OK, "SCOUT_TEST_RESTART_EXIT": "1"},
            conf_lines=["MAPS_RESTART=100\n"])
        assert restarts == 1
        assert proc.returncode == 1
        assert "restart FAILED" in log

    def test_vsz_warn_threshold(self, sandbox):
        proc, _, log, restarts = run(
            sandbox, "check", env_extra=self.ENV_OK,
            conf_lines=["VSZ_WARN_KB=10000\n"])
        assert restarts == 0
        assert proc.returncode == 1
        assert "WARN" in log and "vsz>10000" in log

    def test_collect_errors_fail_the_check(self, sandbox):
        proc, _, log, _ = run(
            sandbox, "check",
            env_extra={**self.ENV_OK, "SCOUT_TEST_UBUS_RC": "1"})
        assert proc.returncode == 1
        assert "FAIL" in log

    def test_log_self_truncates(self, sandbox):
        sandbox.log.write_text("old line\n" * 50)
        run(sandbox, "check", env_extra=self.ENV_OK,
            conf_lines=["SCOUT_LOG_MAX_LINES=10\n"])
        lines = sandbox.log.read_text().splitlines()
        assert len(lines) <= 10
        assert "OK uptime" in lines[-1]


class TestUsage:
    def test_unknown_subcommand_exits_2(self, sandbox):
        proc, _, _, _ = run(sandbox, "bogus")
        assert proc.returncode == 2

    def test_show_without_tick_fails(self, sandbox):
        proc, _, _, _ = run(sandbox, "show")
        assert proc.returncode == 1
