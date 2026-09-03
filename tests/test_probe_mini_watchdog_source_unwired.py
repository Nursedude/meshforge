"""probe_mini_watchdog_source_unwired — the silent half of a reader/writer pair.

WHY THIS EXISTS (2026-09-03): ``MINI_DUDEAI_ENABLE_WATCHDOG=0`` switches off
mini's only signal_class feed. On a box with no watchdog that flag is CORRECT
and required — without it the source emits ``source_error_watchdog`` every tick
and pins ``src_errors=1`` forever (declared-absent != unobservable != error).
So the flag cannot simply be removed, and the defect is the COMBINATION:
a watchdog gets installed on such a box later and nobody flips the flag back.

lehua's env file documents exactly that transition in prose — "FLIP TO 1 the
day a watchdog is installed here, or the dead-watchdog rule stays blind" — and
nothing enforced it. Meanwhile mini keeps ticking at ``src_errors=0`` and reads
GREEN, indistinguishable from a genuinely healthy box.

The LOUD polarity (flag on, no watchdog) was already covered by src_errors.
This closes the polarity that never complains — both directions, per the
frozen-green lesson.
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.watchdog_probe_core import (  # noqa: E402
    collect_dispositions,
    reset_dispositions,
)
from utils.watchdog_probes_mini import (  # noqa: E402
    probe_mini_watchdog_source_unwired,
)

CLS = "mini_watchdog_source_unwired"
FLEET_CMD = "/usr/bin/python3\0-m\0mini_dudeai\0--preset\0meshforge_fleet\0--interval\x0030"
CLAW_CMD = "/usr/bin/python3\0-m\0mini_dudeai\0--preset\0standalone\0--interval\x0030"


@pytest.fixture
def dispositions():
    reset_dispositions()
    return collect_dispositions


def _proc(tmp_path, pid="1234", cmdline=FLEET_CMD, env=None, environ=True):
    root = tmp_path / "proc"
    d = root / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "cmdline").write_bytes(cmdline.encode())
    if environ:
        blob = "\0".join(f"{k}={v}" for k, v in (env or {}).items())
        (d / "environ").write_bytes(blob.encode())
    (root / "not-a-pid").mkdir(exist_ok=True)   # non-numeric entries ignored
    return str(root)


# ── the contradiction ─────────────────────────────────────────────────
def test_watchdog_running_while_mini_feed_is_off_is_degraded(tmp_path, dispositions):
    proc = _proc(tmp_path, env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    sig = probe_mini_watchdog_source_unwired(proc_root=proc, unit_status="ok")
    assert sig is not None
    assert sig.cls == CLS and sig.severity == "degraded"
    assert "MINI_DUDEAI_ENABLE_WATCHDOG" in sig.detail
    assert "RESTART" in sig.detail       # editing the file alone is not enough


def test_flag_on_is_clean(tmp_path, dispositions):
    proc = _proc(tmp_path, env={"MINI_DUDEAI_ENABLE_WATCHDOG": "1"})
    assert probe_mini_watchdog_source_unwired(proc_root=proc, unit_status="ok") is None
    assert dispositions()[CLS]["disp"] == "clean"


def test_flag_unset_is_clean_because_the_preset_default_is_on(tmp_path, dispositions):
    proc = _proc(tmp_path, env={"PYTHONPATH": "/opt/meshforge/src"})
    assert probe_mini_watchdog_source_unwired(proc_root=proc, unit_status="ok") is None
    assert dispositions()[CLS]["disp"] == "clean"


def test_reads_the_live_environ_not_the_config_file(tmp_path, dispositions):
    """THE consumer-of-record property (calibrated_claims #7). An env file
    edited to 1 without restarting the unit leaves the RUNNING process on 0 —
    the state this probe must still catch. Config files are the wiring; the
    live environ is what actually decides."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".config").mkdir()
    # A config file that says the feed is ON ...
    cfg = home / "mini_dudeai.env"
    cfg.write_text("MINI_DUDEAI_ENABLE_WATCHDOG=1\n")
    # ... while the process actually running was started with it OFF.
    proc = _proc(tmp_path, env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    sig = probe_mini_watchdog_source_unwired(proc_root=proc, unit_status="ok")
    assert sig is not None, "read the config file instead of the live process"


def test_claw_instance_is_not_a_consumer(tmp_path, dispositions):
    """A --preset standalone claw mini reads no watchdog document, so it is not
    party to this contract and must not be matched."""
    proc = _proc(tmp_path, cmdline=CLAW_CMD,
                 env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    sig = probe_mini_watchdog_source_unwired(
        proc_root=proc, unit_status="ok", mini_home=str(tmp_path / "nohome"))
    assert sig is None
    assert dispositions()[CLS]["disp"] == "inert"


# ── no producer -> nothing to contradict ──────────────────────────────
def test_no_watchdog_unit_here_is_inert(tmp_path, dispositions):
    """lehua's real shape: the flag is correct there, and this must stay quiet."""
    proc = _proc(tmp_path, env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    assert probe_mini_watchdog_source_unwired(
        proc_root=proc, unit_status="absent") is None
    assert dispositions()[CLS]["disp"] == "inert"


def test_watchdog_installed_but_stopped_is_inert(tmp_path, dispositions):
    proc = _proc(tmp_path, env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    assert probe_mini_watchdog_source_unwired(
        proc_root=proc, unit_status="down") is None
    assert dispositions()[CLS]["disp"] == "inert"


def test_unresolvable_unit_state_is_indeterminate_not_inert(tmp_path, dispositions):
    """systemctl unreadable is unobservable, never 'absent by design'."""
    proc = _proc(tmp_path, env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    assert probe_mini_watchdog_source_unwired(
        proc_root=proc, unit_status="unknown") is None
    assert dispositions()[CLS]["disp"] == "indeterminate"


# ── reader missing -> honest tri-state ────────────────────────────────
def test_mini_seeded_but_not_running_is_indeterminate(tmp_path, dispositions):
    home = tmp_path / "home"
    home.mkdir()
    (home / "mini_dudeai_rules.json").write_text(json.dumps({"rules": []}))
    empty = tmp_path / "emptyproc"
    empty.mkdir()
    assert probe_mini_watchdog_source_unwired(
        proc_root=str(empty), unit_status="ok", mini_home=str(home)) is None
    assert dispositions()[CLS]["disp"] == "indeterminate"


def test_no_mini_at_all_is_inert(tmp_path, dispositions):
    empty = tmp_path / "emptyproc"
    empty.mkdir()
    assert probe_mini_watchdog_source_unwired(
        proc_root=str(empty), unit_status="ok",
        mini_home=str(tmp_path / "nothing")) is None
    assert dispositions()[CLS]["disp"] == "inert"


def test_unreadable_environ_is_indeterminate_not_clean(tmp_path, dispositions):
    """A process we cannot read is unobservable — it must never fold into the
    affirmative 'flag is on' branch (honest_failure_modes #1)."""
    proc = _proc(tmp_path, environ=False)
    assert probe_mini_watchdog_source_unwired(
        proc_root=proc, unit_status="ok") is None
    assert dispositions()[CLS]["disp"] == "indeterminate"


def test_unlistable_proc_is_indeterminate(tmp_path, dispositions):
    assert probe_mini_watchdog_source_unwired(
        proc_root=str(tmp_path / "does-not-exist"), unit_status="ok") is None
    assert dispositions()[CLS]["disp"] == "indeterminate"


# ── 2026-09-03 frontier pass: what a /proc scraper gets wrong on real data ──
AUTO_CMD = "/usr/bin/python3\0-m\0mini_dudeai\0--preset\0auto\0--interval\x0030"
MA_CMD = "/usr/bin/python3\0-m\0mini_dudeai\0--preset\0meshanchor_fleet\0--interval\x0030"
# ONE argv token that merely mentions both words — a shell, a grep, an
# editor. Substring marks matched this and read ITS environ as mini's.
SHELL_CMD = "/bin/bash\0-c\0grep mini_dudeai --preset meshforge_fleet ~/notes"


def test_two_fleet_minis_disagreeing_is_indeterminate_not_a_verdict(tmp_path, dispositions):
    """A restart in flight: old process on 0, new one on 1. The first cut
    returned whichever /proc listed first — a verdict decided by directory
    order, under exactly the condition the probe's own fix text advises."""
    proc = _proc(tmp_path, pid="99", env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    _proc(tmp_path, pid="100", env={"MINI_DUDEAI_ENABLE_WATCHDOG": "1"})
    sig = probe_mini_watchdog_source_unwired(proc_root=proc, unit_status="ok")
    assert sig is None
    d = dispositions()[CLS]
    assert d["disp"] == "indeterminate" and "mixed" in (d.get("reason") or "")


def test_two_fleet_minis_both_off_is_still_degraded(tmp_path, dispositions):
    proc = _proc(tmp_path, pid="99", env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    _proc(tmp_path, pid="100", env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    assert probe_mini_watchdog_source_unwired(proc_root=proc, unit_status="ok") is not None


def test_a_shell_mentioning_both_marks_in_one_argv_is_not_a_mini(tmp_path, dispositions):
    proc = _proc(tmp_path, cmdline=SHELL_CMD,
                 env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    sig = probe_mini_watchdog_source_unwired(
        proc_root=proc, unit_status="ok", mini_home=str(tmp_path / "nohome"))
    assert sig is None, "a shell's argv was read as the mini process"
    assert dispositions()[CLS]["disp"] == "inert"


def test_preset_auto_resolving_to_fleet_is_a_consumer(tmp_path, dispositions):
    """The shipped unit template launches `--preset auto` (templates/systemd);
    the first cut only recognised a literal `meshforge_fleet` argv and would
    have read every template-deployed box as 'mini not running'. auto is
    resolved the way the daemon resolves it — through the fleet_hosts SSOT,
    with the PROCESS's own environment (its HOME, its override), never the
    watchdog's root view."""
    hosts = tmp_path / "fleet_hosts"
    hosts.write_text("moc1 moc2\n")
    proc = _proc(tmp_path, cmdline=AUTO_CMD,
                 env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0",
                      "MESHFORGE_FLEET_HOSTS": str(hosts)})
    sig = probe_mini_watchdog_source_unwired(proc_root=proc, unit_status="ok")
    assert sig is not None, "--preset auto (fleet) was not recognised as a consumer"


def test_preset_auto_resolving_to_standalone_is_not_a_consumer(tmp_path, dispositions):
    # A SET but missing override is authoritative in the resolver: no list ->
    # standalone. Pins the test to injected state, not this machine's config.
    proc = _proc(tmp_path, cmdline=AUTO_CMD,
                 env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0",
                      "MESHFORGE_FLEET_HOSTS": str(tmp_path / "absent")})
    sig = probe_mini_watchdog_source_unwired(
        proc_root=proc, unit_status="ok", mini_home=str(tmp_path / "nohome"))
    assert sig is None
    assert dispositions()[CLS]["disp"] == "inert"


def test_meshanchor_fleet_preset_is_not_a_consumer(tmp_path, dispositions):
    """meshanchor-server's real shape: a meshanchor_fleet mini reads MeshAnchor's
    watchdog document, not this one."""
    proc = _proc(tmp_path, cmdline=MA_CMD,
                 env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    sig = probe_mini_watchdog_source_unwired(
        proc_root=proc, unit_status="ok", mini_home=str(tmp_path / "nohome"))
    assert sig is None
    assert dispositions()[CLS]["disp"] == "inert"


def test_flag_semantics_are_the_preset_s_not_a_private_copy(tmp_path, dispositions):
    """hfm #5: the preset decides with `!= "0"` exactly; the first cut decided
    with `.strip() == "0"`, so a value of "0 " read OFF here and ON in mini.
    One helper, imported by both."""
    import inspect
    from mini_dudeai import _util
    from mini_dudeai.presets import meshforge_fleet
    assert _util.WATCHDOG_ENV_FLAG == "MINI_DUDEAI_ENABLE_WATCHDOG"
    assert _util.watchdog_feed_enabled({}) is True
    assert _util.watchdog_feed_enabled({"MINI_DUDEAI_ENABLE_WATCHDOG": "0"}) is False
    assert _util.watchdog_feed_enabled({"MINI_DUDEAI_ENABLE_WATCHDOG": "0 "}) is True
    src = inspect.getsource(meshforge_fleet)
    assert "watchdog_feed_enabled(" in src
    assert 'environ.get("MINI_DUDEAI_ENABLE_WATCHDOG"' not in src
    proc = _proc(tmp_path, env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0 "})
    assert probe_mini_watchdog_source_unwired(proc_root=proc, unit_status="ok") is None
    assert dispositions()[CLS]["disp"] == "clean"
