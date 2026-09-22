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
    [ "$host" = refusedbox ] && echo "1|pcmanfm|meshtasticd" || echo "0||meshtasticd"
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

    def run(*boxes, deadline="2"):
        env = dict(os.environ,
                   PATH=f"{bindir}:{os.environ['PATH']}",
                   STUB_STATE=str(state),
                   MF_UPGRADE_DEADLINE_S=deadline)
        try:
            return subprocess.run(["bash", str(SCRIPT), "upgrade", *boxes],
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
