"""scripts/fleet_os_upgrade.sh `upgrade` — the wait leg, driven for real.

The script runs with `ssh` and `sleep` stubbed on PATH, so nothing leaves
this process. Found by adversarial review 2026-09-22 (Opus 5.5): the wait
loop polled EVERY box named — including one it had just refused — with no
deadline. A refused box carrying a PRIOR run's /var/log/mf_osupgrade.log
answered `MF_UPGRADE_RC=0` and was reported as this run's success (the
stale-marker class launch_upgrade's own comment says it prevents); a
launched box that never wrote its marker hung the script forever.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "fleet_os_upgrade.sh"

# A stub ssh that plays three boxes. The host is the first argument that is
# neither an option nor an option's value; the remote command follows it.
SSH_STUB = r"""#!/bin/bash
args=("$@"); i=0
while [ $i -lt ${#args[@]} ]; do
  case "${args[$i]}" in -o) i=$((i+2)) ;; -*) i=$((i+1)) ;; *) break ;; esac
done
host="${args[$i]}"; cmd="${args[*]:$((i+1))}"
state="$STUB_STATE"
case "$cmd" in
  *"apt-get -s full-upgrade"*)
    # 4th field = the simulation's own exit code
    case "$host" in
      refusedbox) echo "1|pcmanfm|meshtasticd|0" ;;
      simfailbox) echo "0||meshtasticd|100" ;;
      *) echo "0||meshtasticd|0" ;;
    esac
    exit 0 ;;
  *"systemctl reboot"*)
    touch "$state/rebooted_$host"; exit 0 ;;
  *"random/boot_id"*)
    [ -e "$state/rebooted_$host" ] && echo new-boot || echo old-boot; exit 0 ;;
  *"list-units --type=service"*)
    [ "$host" = blindbox ] && exit 0     # snapshot failed: no lines, no error
    echo "sys:rnsd.service"; echo "sys:meshtasticd.service"
    if [ ! -e "$state/rebooted_$host" ]; then
      echo "sys:colord.service"    # on-demand desktop daemon: must not count
      [ "$host" = lostsvcbox ] && echo "sys:wayvnc.service"
    fi
    exit 0 ;;
  *"ss -xnpl"*)
    # run the script's REAL owner pipeline over a fake ss table, so the
    # parsing is tested, not a token the stub chose
    rows='u_str LISTEN 0 0 @rns/default/rpc 1 * 0 users:(("rnsd",pid=10,fd=5))'
    if [ -e "$state/rebooted_$host" ] && [ "$host" = squatbox ]; then
      rows='u_str LISTEN 0 0 @rns/default 2 * 0 users:(("rnsd",pid=10,fd=4))
u_str LISTEN 0 0 @rns/default/rpc 3 * 0 users:(("python3",pid=11,fd=6))'
    fi
    pipeline="${cmd#*grep \"@rns/\"}"
    printf '%s\n' "$rows" | grep "@rns/" | bash -c "cat ${pipeline}"
    exit 0 ;;
  *"rm -f /var/log/mf_osupgrade.log"*)
    touch "$state/launched_$host"; exit 0 ;;
  *"grep -q MF_UPGRADE_RC"*)
    # refusedbox: a PRIOR run's log is still on disk — never cleared,
    # because this run never launched there.
    [ "$host" = refusedbox ] && exit 0
    [ "$host" = goodbox ] && [ -e "$state/launched_$host" ] && exit 0
    exit 1 ;;
  *"grep -E"*)
    printf 'MF_UPGRADE_RC=0\nMF_HOLD_AFTER=meshtasticd \n'; exit 0 ;;
esac
exit 0
"""


@pytest.fixture
def stubbed(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("ssh", SSH_STUB), ("sleep", "#!/bin/bash\nexit 0\n")):
        p = bindir / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "state"
    state.mkdir()

    def run(*boxes, deadline="2", mode="upgrade"):
        env = dict(os.environ,
                   PATH=f"{bindir}:{os.environ['PATH']}",
                   STUB_STATE=str(state),
                   MF_UPGRADE_DEADLINE_S=deadline,
                   MF_REBOOT_POLLS="4")
        try:
            return subprocess.run(["bash", str(SCRIPT), mode, *boxes],
                                  capture_output=True, text=True, env=env,
                                  timeout=30)
        except subprocess.TimeoutExpired:
            pytest.fail("the upgrade wait never returned — no deadline")
    return run


def test_a_refused_box_is_never_waited_on_or_reported(stubbed):
    r = stubbed("refusedbox", "goodbox")
    assert "refusedbox: REFUSING" in r.stdout
    # A stale MF_UPGRADE_RC from an earlier run must not be read as ours.
    assert "refusedbox: MF_UPGRADE_RC" not in r.stdout, r.stdout
    assert "goodbox: MF_UPGRADE_RC=0" in r.stdout, r.stdout
    assert r.returncode == 1, "a refusal is not a clean run"


def test_a_box_that_never_finishes_ends_as_unknown_not_a_hang(stubbed):
    r = stubbed("hangbox", deadline="1")
    assert "hangbox" in r.stdout and "UNKNOWN" in r.stdout, r.stdout
    assert "hangbox: MF_UPGRADE_RC" not in r.stdout
    assert r.returncode == 1


def test_the_clean_path_still_exits_zero(stubbed):
    r = stubbed("goodbox")
    assert "goodbox: MF_UPGRADE_RC=0" in r.stdout, r.stdout
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)


def test_a_failed_simulation_is_refused_not_read_as_zero_removals(stubbed):
    # review 2026-09-22 residual (5): an empty sim file grep-counts 0 Remv,
    # and the detached run would then remove whatever it liked.
    r = stubbed("simfailbox")
    assert "simfailbox: REFUSING" in r.stdout and "simulation" in r.stdout, r.stdout
    assert "simfailbox: launching" not in r.stdout
    assert r.returncode == 1


# reboot — 2026-10-25 fleet OS roll. /var/run/reboot-required was absent on
# all 10 boxes after 71-121 upgrades incl. firmware (on Pi OS it is written by
# unattended-upgrades, kernel-only), so the old gate skipped every one.
def test_reboot_does_not_need_the_reboot_required_marker(stubbed):
    r = stubbed("nomarkerbox", mode="reboot")
    assert "SKIP" not in r.stdout, r.stdout
    assert "RECOVERED" in r.stdout, r.stdout
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)


def test_a_service_that_did_not_come_back_is_named(stubbed):
    # lehua 10-25: wayvnc was running (runtime-only start), disabled at boot.
    r = stubbed("lostsvcbox", mode="reboot")
    assert "NOT RECOVERED" in r.stdout and "wayvnc.service" in r.stdout, r.stdout
    assert "colord" not in r.stdout.split("NOT RECOVERED", 1)[1]
    assert r.returncode == 1


def test_rns_owned_by_a_squatter_is_not_recovered(stubbed):
    # #69: the @rns socket present is not the socket owned by rnsd.
    r = stubbed("squatbox", mode="reboot")
    assert "NOT RECOVERED" in r.stdout and "python3" in r.stdout, r.stdout
    assert r.returncode == 1


def test_a_failed_service_snapshot_never_reads_recovered(stubbed):
    # review 2026-10-25 (Fable), CONFIRMED: empty pre == empty post read as
    # RECOVERED, and the empty set also switched the rnsd check off.
    r = stubbed("blindbox", mode="reboot")
    assert "RECOVERED" not in r.stdout.replace("NOT RECOVERED", ""), r.stdout
    assert "UNKNOWN" in r.stdout, r.stdout
    assert r.returncode == 1
