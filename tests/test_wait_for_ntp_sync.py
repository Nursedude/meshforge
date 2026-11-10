"""Tests for scripts/wait_for_ntp_sync.sh — the meshtasticd clock-quality gate.

Portduino meshtasticd asks `timedatectl status` ONCE at startup. Not yet
NTP-synced, it rates its clock RTCQualityDevice; Router.cpp then stamps every
received packet rx_time 0 and NodeDB never moves last_heard, so every node
reads offline for the life of the process. Measured 2026-10-02: moc4 0 of 374
online for 5.8 days (started 15 s after boot), moc2 0 of 6 (7 s); a restart
after sync fixed moc4 within 4 minutes.

Every test stubs `timedatectl` on PATH, so nothing here depends on the host's
real clock state.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "wait_for_ntp_sync.sh"
CONF = REPO / "templates" / "systemd" / "meshtasticd.service.d" / "20-wait-for-ntp-sync.conf"

# Real `timedatectl status` shapes (systemd 252/257).
SYNCED = """               Local time: Fri 2026-10-02 23:24:43 HST
           Universal time: Sat 2026-10-03 09:24:43 UTC
System clock synchronized: yes
              NTP service: active
          RTC in local TZ: no"""
UNSYNCED = SYNCED.replace("synchronized: yes", "synchronized: no")


def _stub(tmp_path: Path, body: str) -> dict:
    stub = tmp_path / "timedatectl"
    stub.write_text("#!/bin/sh\ncat <<'OUT'\n" + body + "\nOUT\n", encoding="utf-8")
    stub.chmod(0o755)
    return {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)}


def run(tmp_path, body, budget=2):
    env = _stub(tmp_path, body)
    t0 = time.monotonic()
    p = subprocess.run(["sh", str(SCRIPT), str(budget)], capture_output=True,
                       text=True, timeout=60, env=env)
    return p.returncode, p.stderr, time.monotonic() - t0


class TestGate:
    def test_a_synced_clock_returns_immediately(self, tmp_path):
        rc, err, elapsed = run(tmp_path, SYNCED, budget=10)
        assert rc == 0
        assert elapsed < 3          # 1 s LINGER after speaking (r2 F1), no wait
        assert "TIMEOUT" not in err
        # 2026-11-09: the gate speaks on EVERY run, so a start with no gate
        # line means the gate did not run (meshtasticd_clock_unrated reads
        # that absence as unknown, never as clean).
        assert "wait_for_ntp_sync: clock synchronized after 0s" in err

    def test_an_unsynced_clock_waits_the_budget_then_fails_open(self, tmp_path):
        """THE case this exists for."""
        rc, err, elapsed = run(tmp_path, UNSYNCED, budget=2)
        assert rc == 0
        assert elapsed >= 2
        assert "TIMEOUT" in err

    def test_a_clock_that_syncs_mid_wait_releases_the_gate(self, tmp_path):
        flag = tmp_path / "synced"
        stub = tmp_path / "timedatectl"
        stub.write_text(
            "#!/bin/sh\n"
            f"if [ -f '{flag}' ]; then echo 'System clock synchronized: yes';"
            " else echo 'System clock synchronized: no'; fi\n")
        stub.chmod(0o755)
        env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)}
        p = subprocess.Popen(["sh", str(SCRIPT), "20"], stderr=subprocess.PIPE,
                             text=True, env=env)
        time.sleep(1.5)
        flag.touch()
        t0 = time.monotonic()
        _, err = p.communicate(timeout=10)
        assert p.returncode == 0
        assert time.monotonic() - t0 < 5
        assert "synchronized after" in err and "TIMEOUT" not in err

    def test_timedatectl_missing_fails_open_with_a_witness(self, tmp_path):
        env = {"PATH": f"{tmp_path}", "HOME": str(tmp_path)}  # no timedatectl, no grep
        p = subprocess.run(["/bin/sh", str(SCRIPT), "1"], capture_output=True,
                           text=True, timeout=30, env=env)
        assert p.returncode == 0
        assert "WITNESS" in p.stderr

    def test_it_never_exits_nonzero(self, tmp_path):
        for body in (SYNCED, UNSYNCED, ""):
            rc, _, _ = run(tmp_path, body, budget=1)
            assert rc == 0, f"non-zero exit for {body[:30]!r}"

    def test_the_witness_names_the_consequence_and_the_fix(self, tmp_path):
        _, err, _ = run(tmp_path, UNSYNCED, budget=1)
        assert "sudo systemctl restart meshtasticd" in err
        # 2026-11-09: the consequence is a RISK, not a certainty — lehua's gate
        # timed out and its radio read 33 nodes online. The witness says MAY
        # and names the check that tells the two apart (the one the
        # meshtasticd_clock_unrated probe reads).
        assert "may" in err
        assert "will advance" not in err
        assert "num_online_nodes" in err
        # The probe keys on this exact mark (hfm #5; test_watchdog_clock_unrated).
        assert "wait_for_ntp_sync: TIMEOUT" in err

    def test_predicate_is_meshtasticds_own_command(self):
        """Gate and consumer must agree on 'synchronized' (hfm #5). The firmware
        runs: timedatectl status | grep synchronized | grep yes -c."""
        text = SCRIPT.read_text()
        assert "timedatectl status" in text
        assert "grep synchronized" in text


class TestDropIn:
    def test_the_drop_in_ships_and_points_at_the_script(self):
        assert CONF.exists()
        assert "wait_for_ntp_sync.sh" in CONF.read_text()

    def test_no_percent_signs_in_execstartpre(self):
        for line in CONF.read_text().splitlines():
            if line.startswith("ExecStartPre="):
                assert "%" not in line, line

    def test_start_timeout_exceeds_the_gate_budget(self):
        budget = timeout_s = None
        for line in CONF.read_text().splitlines():
            if line.startswith("ExecStartPre="):
                budget = int(line.strip().split()[-1])
            if line.startswith("TimeoutStartSec="):
                timeout_s = int(line.split("=", 1)[1].strip())
        assert budget is not None and timeout_s is not None
        assert timeout_s > budget


class TestDropInIsDeployed:
    """Runs the REAL update.sh block with its unit dirs pointed at a sandbox."""

    START = "# Deploy the meshtasticd NTP-sync wait drop-in."

    def _block(self) -> str:
        text = (REPO / "scripts" / "update.sh").read_text()
        i = text.index(self.START)
        j = text.index("\nfi\n", i) + len("\nfi\n")
        return text[i:j]

    def _run(self, tmp_path: Path, unit_in: str = "etc"):
        dirs = {k: tmp_path / k for k in ("etc", "libsd", "usrlibsd")}
        for d in dirs.values():
            d.mkdir(exist_ok=True)
        if unit_in:
            (dirs[unit_in] / "meshtasticd.service").write_text("[Service]\n")
        block = (self._block()
                 .replace("/usr/lib/systemd/system", str(dirs["usrlibsd"]))
                 .replace("/etc/systemd/system", str(dirs["etc"]))
                 .replace("/lib/systemd/system", str(dirs["libsd"])))
        script = (f'GREEN=""; NC=""; SVC_UPDATED=false; INSTALL_DIR="{REPO}"\n'
                  f'{block}\necho "SVC_UPDATED=$SVC_UPDATED"\n')
        p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        return p, dirs["etc"] / "meshtasticd.service.d" / CONF.name

    def test_installs_byte_for_byte_then_is_idempotent(self, tmp_path):
        first, dst = self._run(tmp_path)
        assert first.returncode == 0, first.stderr
        assert dst.read_text() == CONF.read_text().replace("/opt/meshforge/", f"{REPO}/")
        assert "SVC_UPDATED=true" in first.stdout
        second, _ = self._run(tmp_path)
        assert "already current" in second.stdout
        assert "SVC_UPDATED=false" in second.stdout

    def test_the_apt_packages_unit_location_counts(self, tmp_path):
        """meshtasticd from the apt package lives in /usr/lib/systemd/system."""
        p, dst = self._run(tmp_path, unit_in="usrlibsd")
        assert p.returncode == 0, p.stderr
        assert dst.exists()

    def test_a_box_without_meshtasticd_gets_nothing(self, tmp_path):
        p, dst = self._run(tmp_path, unit_in="")
        assert p.returncode == 0, p.stderr
        assert not dst.exists()
        assert "SVC_UPDATED=false" in p.stdout


class TestLinger:
    def test_the_gate_outlives_its_own_line(self, tmp_path):
        """Fable 5.2 review r2 F1 (11-09): journald reads ``_COMM`` from
        /proc/<pid> lazily — a gate that exits right after ``echo`` lost it on
        11/30 and 22/30 lines (0 s path), 0/30 with a 0.3 s linger. The probe
        authenticates the gate by ``_COMM``, so the gate must still be alive
        after it speaks, on BOTH paths."""
        for body, budget in ((SYNCED, 10), (UNSYNCED, 1)):
            env = _stub(tmp_path, body)
            p = subprocess.Popen(["sh", str(SCRIPT), str(budget)],
                                 stderr=subprocess.PIPE, text=True, env=env)
            line = p.stderr.readline()
            assert line.startswith("wait_for_ntp_sync:"), line
            time.sleep(0.5)
            assert p.poll() is None, "the gate exited right after speaking"
            p.communicate(timeout=10)
            assert p.returncode == 0


class TestReviewR5:
    def test_the_gate_is_directly_executable(self, tmp_path):
        """r5 #5: systemd EXECUTES it (ExecStartPre=-/opt/.../wait_for_ntp_sync.sh);
        every other test runs `sh SCRIPT`, so a lost exec bit passed 132/132 —
        and `-` swallows the 203/EXEC, so the gate would silently never run."""
        assert os.access(SCRIPT, os.X_OK), "wait_for_ntp_sync.sh lost its exec bit"
        env = _stub(tmp_path, SYNCED)
        p = subprocess.run([str(SCRIPT), "5"], capture_output=True, text=True,
                           timeout=30, env=env)
        assert p.returncode == 0 and "clock synchronized after" in p.stderr

    def test_the_probe_comm_is_this_scripts_kernel_comm(self):
        """The probe authenticates gate lines by journald _COMM — the kernel's
        15-char comm of THIS file's name. Renaming the script breaks it."""
        import utils.watchdog_probes_clock as wc
        assert wc.GATE_COMM == os.path.basename(str(SCRIPT))[:15]

    def test_a_slow_timedatectl_cannot_outrun_the_budget(self, tmp_path):
        """r5 #4: the budget is WALL time on the monotonic clock, not loop
        iterations: a 1.5 s timedatectl, budget 4 -> done well under 4 x 2.5 s."""
        stub = tmp_path / "timedatectl"
        stub.write_text("#!/bin/sh\nsleep 1.5\necho 'System clock synchronized: no'\n")
        stub.chmod(0o755)
        env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)}
        t0 = time.monotonic()
        p = subprocess.run(["sh", str(SCRIPT), "4"], capture_output=True, text=True,
                           timeout=60, env=env)
        elapsed = time.monotonic() - t0
        assert p.returncode == 0 and "TIMEOUT after" in p.stderr
        assert elapsed < 4 + 2.5 + 1 + 1.5, elapsed     # budget + one call + linger + slack

    def test_a_hung_timedatectl_is_cut_off_per_call(self, tmp_path):
        """r5 #4: a timedatectl that never answers (D-Bus wedged at boot) is
        cut at 5 s per call, so the gate still speaks before TimeoutStartSec."""
        stub = tmp_path / "timedatectl"
        # `exec`: the stub IS the hung process (real timedatectl is one
        # process) — a forked child would outlive the kill and hold the
        # $(...) pipe open, a fixture artefact (CI c8e503d0 hung 30 s).
        stub.write_text("#!/bin/sh\nexec sleep 30\n")
        stub.chmod(0o755)
        env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)}
        t0 = time.monotonic()
        p = subprocess.run(["sh", str(SCRIPT), "2"], capture_output=True, text=True,
                           timeout=60, env=env)
        elapsed = time.monotonic() - t0
        assert p.returncode == 0 and "TIMEOUT after" in p.stderr
        assert elapsed < 2 + 5 + 1 + 2, elapsed     # budget + one cut call + linger + slack


class TestReviewR6:
    def test_the_timeout_line_reports_wall_seconds(self, tmp_path):
        """r6 C1: the elapsed number is pinned (a `${BUDGET}s` mutant survived)."""
        stub = tmp_path / "timedatectl"
        stub.write_text("#!/bin/sh\nsleep 2.5\necho 'System clock synchronized: no'\n")
        stub.chmod(0o755)
        env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)}
        p = subprocess.run(["sh", str(SCRIPT), "2"], capture_output=True, text=True,
                           timeout=60, env=env)
        import re
        n = int(re.search(r"TIMEOUT after (\d+)s", p.stderr).group(1))
        # one 2.5 s call + 1 s sleep overshoots a budget of 2: the WALL number
        # (>= 3) differs from the budget (a `${BUDGET}s` mutant survived r6)
        assert 3 <= n <= 6, p.stderr

    def test_a_timedatectl_ignoring_term_is_killed(self, tmp_path):
        """r6 F4: `timeout -k`: a child that ignores SIGTERM is still cut."""
        stub = tmp_path / "timedatectl"
        # `exec` keeps the ignored TERM on the ONE process timeout watches
        # (see the hung test above: a forked child is a fixture artefact).
        stub.write_text("#!/bin/sh\ntrap '' TERM\nexec sleep 30\n")
        stub.chmod(0o755)
        env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)}
        t0 = time.monotonic()
        p = subprocess.run(["sh", str(SCRIPT), "2"], capture_output=True, text=True,
                           timeout=60, env=env)
        elapsed = time.monotonic() - t0
        assert p.returncode == 0 and "TIMEOUT after" in p.stderr
        assert elapsed < 2 + 7 + 1 + 1 + 2, elapsed

    def test_no_timeout_binary_still_asks_timedatectl(self, tmp_path):
        """r6 F5: without `timeout` on PATH, "cannot ask" must not read as
        "not synchronized" — the gate asks timedatectl directly."""
        import shutil
        bindir = tmp_path / "bin"
        bindir.mkdir()
        for tool in ("grep", "sleep"):
            (bindir / tool).symlink_to(shutil.which(tool))
        stub = bindir / "timedatectl"
        stub.write_text("#!/bin/sh\necho 'System clock synchronized: yes'\n")
        stub.chmod(0o755)
        sh = shutil.which("sh")
        p = subprocess.run([sh, str(SCRIPT), "4"], capture_output=True, text=True,
                           timeout=30, env={"PATH": str(bindir), "HOME": str(tmp_path)})
        assert p.returncode == 0
        assert "clock synchronized after 0s" in p.stderr, p.stderr
