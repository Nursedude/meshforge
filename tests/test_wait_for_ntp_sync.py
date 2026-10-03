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
        assert elapsed < 2
        assert "TIMEOUT" not in err

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
        assert "no node's lastHeard will advance" in err
        assert "sudo systemctl restart meshtasticd" in err

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
