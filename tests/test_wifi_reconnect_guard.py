"""Tests for scripts/wifi_reconnect_guard.sh — the headless Wi-Fi self-heal.

lehua, 2026-10-04: a 3-minute weak-signal blip (14:30-14:33, ASSOC-REJECT
status 16) ended with NetworkManager `association took too long` ->
`failed (reason 'no-secrets')`. A no-secrets failure blocks autoconnect until
an agent supplies secrets — on a headless box, forever. Zero NetworkManager
lines from 14:35 until the operator rebooted it at 16:21: a 3-minute RF fault
became a 2-hour outage. The guard re-activates the profile NM gave up on.

Every test stubs `nmcli` and `logger` on PATH and points the guard's state,
clock and witness at tmp_path, so no verdict depends on the host's network.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "wifi_reconnect_guard.sh"

PROFILES = "netplan-wlan0-DudeNET:802-11-wireless:yes\nWired:802-3-ethernet:yes\n"


def run(tmp_path, dev_state, uptime, profiles=PROFILES, iface="", default_route=""):
    """Run one guard tick. Returns (rc, nmcli call log, logger log)."""
    calls = tmp_path / "nmcli.calls"
    logs = tmp_path / "logger.calls"
    nm = tmp_path / "nmcli"
    nm.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        'case "$*" in\n'
        f'  *"-f DEVICE,TYPE,STATE device"*) printf "wlan0:wifi:{dev_state}\\neth0:ethernet:unavailable\\nlo:loopback:unmanaged\\n" ;;\n'
        f'  *"-f NAME,TYPE,AUTOCONNECT connection show"*) printf "{profiles}" ;;\n'
        f'  *"connection.interface-name"*) echo "{iface}" ;;\n'
        '  *"connection up"*) exit 0 ;;\n'
        "esac\n", encoding="utf-8")
    nm.chmod(0o755)
    ipstub = tmp_path / "ip"
    ipstub.write_text("#!/bin/sh\nprintf '%s'\n" % default_route, encoding="utf-8")
    ipstub.chmod(0o755)
    lg = tmp_path / "logger"
    lg.write_text(f'#!/bin/sh\necho "$*" >> "{logs}"\n', encoding="utf-8")
    lg.chmod(0o755)
    (tmp_path / "uptime").write_text(f"{uptime}.42 1234.5\n", encoding="utf-8")
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path),
           "WIFI_GUARD_STATE": str(tmp_path / "state"),
           "WIFI_GUARD_UPTIME_FILE": str(tmp_path / "uptime"),
           "WIFI_GUARD_WITNESS": str(tmp_path / "witness")}
    p = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True,
                       timeout=30, env=env)
    read = lambda f: f.read_text(encoding="utf-8") if f.exists() else ""
    return p.returncode, read(calls), read(logs)


def heals(nm_calls):
    return [l for l in nm_calls.splitlines() if "connection up" in l]


class TestGuard:
    def test_connected_never_acts(self, tmp_path):
        rc, nm, _ = run(tmp_path, "connected", 1000)
        assert rc == 0 and not heals(nm)

    def test_first_disconnected_tick_only_starts_the_clock(self, tmp_path):
        rc, nm, _ = run(tmp_path, "disconnected", 1000)
        assert rc == 0 and not heals(nm)
        assert (tmp_path / "state" / "wlan0.down").exists()

    def test_heals_after_grace(self, tmp_path):
        """THE case: NM sits in `disconnected` past the grace -> bring the
        autoconnect profile back up on that device, and leave a witness."""
        run(tmp_path, "disconnected", 1000)
        rc, nm, log = run(tmp_path, "disconnected", 1000 + 301)
        assert rc == 0
        assert heals(nm) == ["connection up netplan-wlan0-DudeNET ifname wlan0"]
        assert "wlan0" in log and "netplan-wlan0-DudeNET" in log
        assert (tmp_path / "witness").read_text().strip() != ""

    def test_within_grace_does_not_act(self, tmp_path):
        run(tmp_path, "disconnected", 1000)
        _, nm, _ = run(tmp_path, "disconnected", 1000 + 120)
        assert not heals(nm)

    def test_cooldown_blocks_a_second_heal(self, tmp_path):
        run(tmp_path, "disconnected", 1000)
        run(tmp_path, "disconnected", 1301)
        _, nm, _ = run(tmp_path, "disconnected", 1301 + 60)
        assert len(heals(nm)) == 1

    def test_reconnect_resets_the_clock(self, tmp_path):
        run(tmp_path, "disconnected", 1000)
        run(tmp_path, "connected", 1100)
        _, nm, _ = run(tmp_path, "disconnected", 1350)
        assert not heals(nm)  # a fresh outage, not 350 s of one

    def test_radio_off_is_a_deliberate_absence(self, tmp_path):
        """`unavailable` = radio disabled / rfkill. Fighting it would undo an
        operator's decision."""
        run(tmp_path, "unavailable", 1000)
        _, nm, _ = run(tmp_path, "unavailable", 5000)
        assert not heals(nm)

    def test_no_autoconnect_wifi_profile_never_acts(self, tmp_path):
        prof = "home:802-11-wireless:no\nWired:802-3-ethernet:yes\n"
        run(tmp_path, "disconnected", 1000, profiles=prof)
        _, nm, _ = run(tmp_path, "disconnected", 2000, profiles=prof)
        assert not heals(nm)

    def test_profile_pinned_to_another_device_is_skipped(self, tmp_path):
        run(tmp_path, "disconnected", 1000, iface="wlan1")
        _, nm, _ = run(tmp_path, "disconnected", 2000, iface="wlan1")
        assert not heals(nm)

    def test_wired_box_with_idle_wlan_never_acts(self, tmp_path):
        """Measured 2026-10-04: EIGHT fleet boxes sit at wlan0 `disconnected`
        while eth0 carries the default route. The guard must not bring Wi-Fi
        up beside a working uplink — it only acts when the box is OFFLINE."""
        dr = "default via 10.0.0.1 dev eth0 proto dhcp metric 100"
        run(tmp_path, "disconnected", 1000, default_route=dr)
        _, nm, _ = run(tmp_path, "disconnected", 5000, default_route=dr)
        assert not heals(nm)

    def test_clock_going_backward_restarts_the_window(self, tmp_path):
        """honest_failure_modes #6: a stale marker from a later 'time' must not
        read as a negative (or huge) outage."""
        run(tmp_path, "disconnected", 5000)
        _, nm, _ = run(tmp_path, "disconnected", 100)
        assert not heals(nm)
