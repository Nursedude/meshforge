"""scripts/cron_verdict_freshness.sh — moved into the repo 2026-09-30 from an
untracked ~/cron_verdict_freshness.sh (it wrote the log the repo's digest
reads, yet nothing versioned it). These tests RUN the real script in a
sandbox: every outward path (ntfy page, verdict stamp, ssh) is a stub that
records its arguments, so nothing is ever paged.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = _ROOT / "scripts" / "cron_verdict_freshness.sh"
sys.path.insert(0, str(_ROOT / "src"))

import monitoring.meshforge_digest as dg  # noqa: E402

RECORDER = '#!/bin/bash\nprintf "%s|" "$@" >> "{out}"; echo >> "{out}"\n'
#: The ONE cron the verdict leg judges since 2026-10-09's narrowing.
J = "fleet_offline_check"


@pytest.fixture
def box(tmp_path):
    """A sandboxed box: home, config path, and recording stubs."""
    home = tmp_path / "home"
    home.mkdir()
    stubs = {}
    for name in ("ntfy", "verdict", "ssh"):
        p = tmp_path / f"stub_{name}.sh"
        p.write_text(RECORDER.format(out=tmp_path / f"{name}.txt"))
        p.chmod(0o755)
        stubs[name] = p

    class Box:
        def __init__(self):
            self.home, self.tmp = home, tmp_path
            self.conf = tmp_path / "cron_freshness.conf"
            self.ssh = stubs["ssh"]

        def config(self, **kv):
            self.conf.write_text("".join(f"{k}={v}\n" for k, v in kv.items()))

        def verdicts(self, *lines):
            (home / "cron_verdicts.log").write_text("".join(l + "\n" for l in lines))

        def run(self, now=None):
            env = dict(os.environ,
                       CRON_FRESHNESS_HOME=str(home), CRON_FRESHNESS_CONF=str(self.conf),
                       CRON_FRESHNESS_NTFY=str(stubs["ntfy"]),
                       CRON_FRESHNESS_VERDICT=str(stubs["verdict"]),
                       CRON_FRESHNESS_SSH=str(self.ssh))
            if now is not None:
                env["CRON_FRESHNESS_NOW"] = str(int(now))
            r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                               text=True, timeout=60)
            assert r.returncode == 0, r.stderr
            return r

        def read(self, name):
            p = tmp_path / f"{name}.txt"
            return p.read_text() if p.exists() else ""

        def alerts(self):
            p = home / "fleet_alerts.log"
            return p.read_text() if p.exists() else ""

    return Box()


def _iso(ago_s):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago_s))


# ── a config it cannot use must never stamp OK ────────────────────────────

@pytest.mark.parametrize("body,why", [
    (None, "no config"),
    ("", "configures nothing"),
    ("BOGUS=1\n", "unknown key 'BOGUS'"),
    ("POWER_LOCAL_MAX_MIN=ten\n", "POWER_LOCAL_MAX_MIN 'ten'"),
    # readers, reproduced: these once word-split to nothing / two numbers,
    # passed, and made `-gt` error into the FRESH branch — OK, measured nothing
    ("POWER_REMOTE_BOXES=b\nPOWER_REMOTE_MAX_MIN=\n", "POWER_REMOTE_MAX_MIN ''"),
    ("POWER_REMOTE_BOXES=b\nPOWER_REMOTE_MAX_MIN=5 9\n", "POWER_REMOTE_MAX_MIN '5 9'"),
    ("POWER_LOCAL_MAX_MIN=5 9\n", "POWER_LOCAL_MAX_MIN '5 9'"),
    ("JUST SOME TEXT\n", "line without '='"),
    ("OFFLINE_CHECK_MAX_MIN=ten\n", "OFFLINE_CHECK_MAX_MIN 'ten'"),
    ("OFFLINE_CHECK_MAX_MIN=5 9\n", "OFFLINE_CHECK_MAX_MIN '5 9'"),
    # retired 2026-10-10: refused with the migration, never silently ignored
    # (ignored, the one cron it still judged would stop being judged at all)
    ("VERDICT_MANIFEST=fleet_offline_check:20\n", "set OFFLINE_CHECK_MAX_MIN"),
])
def test_unusable_config_is_concern_and_pages_nobody(box, body, why):
    if body is not None:
        box.conf.write_text(body)
    box.run()
    v = box.read("verdict")
    assert v.startswith("cron_freshness|CONCERN|") and why in v, v
    assert box.read("ntfy") == "" and box.alerts() == ""


def test_config_is_read_never_sourced(box):
    marker = box.tmp / "pwned"
    box.conf.write_text(f"LOCAL_LABEL=$(touch {marker})\nOFFLINE_CHECK_MAX_MIN=5\n")
    box.verdicts(f"{_iso(60)} {J} OK")
    box.run()
    assert not marker.exists(), "config value was EXECUTED — it must be data"


# ── the verdict leg ────────────────────────────────────────────────────────

def test_fresh_verdict_stamps_ok_and_pages_nobody(box):
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    box.verdicts(f"{_iso(300)} {J} OK")
    box.run()
    assert box.read("verdict") == "cron_freshness|OK|0 stale|\n"
    assert box.read("ntfy") == "" and box.alerts() == ""


def test_stale_is_flagged(box):
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    box.verdicts(f"{_iso(3600)} {J} OK")
    box.run()
    a = box.alerts()
    assert "CRON-FRESHNESS STALE:" in a
    assert re.search(rf"box-a/{J}: last verdict 6\dm ago \(max 30m\)", a), a
    assert box.read("verdict") == "cron_freshness|FAIL|1 stale|\n"
    assert box.read("ntfy").startswith("fleet cron gone silent|high|hourglass|")


def test_never_recorded_is_flagged(box):
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    box.verdicts()
    box.run()
    assert f"box-a/{J}: no verdict ever recorded" in box.alerts()


def test_a_fresh_fail_is_flagged_once_as_fail(box):
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    box.verdicts(f"{_iso(60)} {J} FAIL(2)")
    box.run()
    assert f"box-a/{J}/FAIL: latest verdict: FAIL(2)" in box.alerts()
    assert box.read("verdict") == "cron_freshness|FAIL|1 stale|\n"


def test_other_crons_are_not_judged_here(box):
    """Narrowed 2026-10-09: every other wired cron belongs to the watchdog's
    cron_verdict_stale (3x cadence, 2h floor). A stale or failing OTHER cron
    must not page from this script — that was the duplicate page (10-07)."""
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    box.verdicts(f"{_iso(86400)} brain_git_push OK", f"{_iso(60)} brain_backup FAIL(1)",
                 f"{_iso(60)} {J} OK")
    box.run()
    assert box.read("verdict") == "cron_freshness|OK|0 stale|\n"
    assert box.read("ntfy") == "" and box.alerts() == ""


def test_a_verdict_for_a_similarly_named_cron_is_not_ours(box):
    """Exact name match: `fleet_offline_check_v2` must not stand in for it."""
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    box.verdicts(f"{_iso(60)} {J}_v2 OK")
    box.run()
    assert f"box-a/{J}: no verdict ever recorded" in box.alerts()


def test_realert_gate_withholds_the_page_never_the_record(box):
    """2026-09-07 rule, kept: the 6h gate may withhold a NOTIFICATION, never
    the verdict — a persistently silent cron reads FAIL every hour."""
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    box.verdicts(f"{_iso(7200)} {J} OK")
    t = time.time()
    box.run(now=t)
    box.run(now=t + 3600)                      # 1h later: inside the 6h gate
    assert box.read("ntfy").count("fleet cron gone silent") == 1
    assert box.read("verdict").splitlines() == ["cron_freshness|FAIL|1 stale|"] * 2
    box.run(now=t + 6 * 3600)                  # gate expired: paged again
    assert box.read("ntfy").count("fleet cron gone silent") == 2


def test_a_recovered_cron_alerts_immediately_next_time(box):
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    box.verdicts(f"{_iso(7200)} {J} OK")
    box.run()
    box.verdicts(f"{_iso(7200)} {J} OK", f"{_iso(60)} {J} OK")       # recovered
    box.run()
    box.verdicts(f"{_iso(7200)} {J} OK", f"{_iso(3600)} {J} OK")     # silent again
    box.run()
    assert box.read("ntfy").count("fleet cron gone silent") == 2


def test_a_fail_that_clears_pages_again_on_the_next_fail(box):
    """FAIL → OK → FAIL inside 6h must page twice: the /FAIL item clears."""
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    box.verdicts(f"{_iso(60)} {J} FAIL(1)")
    box.run()
    box.verdicts(f"{_iso(60)} {J} FAIL(1)", f"{_iso(30)} {J} OK")
    box.run()
    box.verdicts(f"{_iso(60)} {J} FAIL(1)", f"{_iso(30)} {J} OK", f"{_iso(10)} {J} FAIL(3)")
    box.run()
    assert box.read("ntfy").count("fleet cron gone silent") == 2, box.read("ntfy")


# ── the power_history leg (local + remote) ────────────────────────────────

def test_local_power_history_age(box):
    box.config(LOCAL_LABEL="box-a", POWER_LOCAL_MAX_MIN="10")
    p = box.home / "power_history.log"
    p.write_text("x\n")
    old = time.time() - 30 * 60
    os.utime(p, (old, old))
    box.run()
    assert "box-a/power_history.log: data file 30m stale (max 10m)" in box.alerts()
    (box.home / ".cron_freshness_state").write_text("")   # ungate, so the next alert is written
    p.unlink()
    box.run()
    # assert on THIS run's alert, not the accumulated verdict file (reader T:
    # the old `or "FAIL" in …` passed on the first run's FAIL and pinned nothing)
    last_block = box.alerts().split("CRON-FRESHNESS STALE:")[-1]
    assert "box-a/power_history.log: file missing/unreadable" in last_block, last_block


def test_remote_power_history_stale_flagged_unreachable_skipped(box):
    stale_mt = int(time.time() - 45 * 60)
    box.ssh.write_text(
        "#!/bin/bash\n"
        f'case " $* " in *" box-b "*) echo {stale_mt} ;; *" box-c "*) exit 255 ;; esac\n')
    box.config(POWER_REMOTE_BOXES="box-b box-c", POWER_REMOTE_MAX_MIN="10")
    box.run()
    a = box.alerts()
    assert "box-b/power_history.log: data file 45m stale (max 10m)" in a, a
    assert "box-c" not in a                    # unreachable is fleet_offline_check's job — no page…
    assert box.read("verdict") == "cron_freshness|FAIL|1 stale; remote unanswered: box-c|\n"  # …but named


# ── the contract with the digest that reads this log ──────────────────────

def test_realert_period_matches_the_digest_window():
    """meshforge_digest.py keeps a CRON-FRESHNESS block amber for
    CRON_FRESHNESS_WINDOW_S: this script's re-alert + its hourly cadence. If
    REALERT_S grows, a persistently stale cron goes GREEN in the digest
    between pages (honest_failure_modes #5: two consumers, one constant)."""
    m = re.search(r"^REALERT_S=(\d+)$", SCRIPT.read_text(), re.M)
    assert m, "REALERT_S not found"
    # +1h cadence +1h slack: at exactly +1h a run starting 1s early lands the
    # re-alert at 7h and a 7h window closed on it (reader S).
    assert int(m.group(1)) + 2 * 3600 == dg.CRON_FRESHNESS_WINDOW_S


def test_alert_line_parses_in_the_digest(box):
    """The digest must be able to DATE what this script writes."""
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    box.verdicts()
    box.run()
    first = box.alerts().splitlines()[0]
    assert "CRON-FRESHNESS STALE:" in first
    e = dg._log_line_epoch(first)
    assert e is not None and abs(time.time() - e) < 120, first


# ── reader-pair folds (2026-09-30) ─────────────────────────────────────────

def test_crlf_quotes_and_spaces_are_normalised_not_trusted(box):
    """A CRLF-edited or quoted config once turned `moc` into `moc\r` — an ssh
    miss, skipped quietly as 'unreachable', OK forever."""
    stale_mt = int(time.time() - 45 * 60)
    box.ssh.write_text(f'#!/bin/bash\ncase " $* " in *" box-b "*) echo {stale_mt} ;; esac\n')
    box.conf.write_bytes(b'LOCAL_LABEL = "box-a"\r\nPOWER_REMOTE_BOXES="box-b"\r\n'
                         b"POWER_REMOTE_MAX_MIN='10'\r\n")
    box.run()
    a = box.alerts()
    assert "box-b/power_history.log: data file 45m stale" in a, a
    assert "\r" not in a and "\r" not in (box.home / ".cron_freshness_state").read_text()


def test_no_remote_box_answering_is_concern_one_is_named(box):
    box.ssh.write_text("#!/bin/bash\nexit 255\n")            # nobody answers
    box.config(POWER_REMOTE_BOXES="box-b box-c")
    box.run()
    v = box.read("verdict")
    assert v.startswith("cron_freshness|CONCERN|") and "NO remote box answered (of 2)" in v, v
    fresh = int(time.time() - 60)
    box.ssh.write_text(f'#!/bin/bash\ncase " $* " in *" box-b "*) echo {fresh} ;; *) exit 255 ;; esac\n')
    box.run()
    v = box.read("verdict").splitlines()[-1]
    assert v.startswith("cron_freshness|OK|") and "remote unanswered: box-c" in v, v


def test_state_read_error_never_truncates_the_state(box):
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    st = box.home / ".cron_freshness_state"
    st.write_text(f"box-a/other 1000\nbox-a/{J}/FAIL 1000\n")
    box.verdicts(f"{_iso(60)} {J} OK")
    st.chmod(0o000)
    try:
        box.run()
    finally:
        st.chmod(0o600)
    assert "box-a/other 1000" in st.read_text(), st.read_text()


def test_state_keys_match_exactly_not_as_regex(box):
    """`grep "^$1 "` let the '.' in power_history.log match any character."""
    box.config(LOCAL_LABEL="box-a", POWER_LOCAL_MAX_MIN="10")
    (box.home / "power_history.log").write_text("x\n")       # fresh → clear_state
    st = box.home / ".cron_freshness_state"
    st.write_text("box-a/power_historyXlog 1000\n")
    box.run()
    assert "box-a/power_historyXlog 1000" in st.read_text(), st.read_text()


def test_overlapping_runs_are_refused_not_interleaved(box):
    import fcntl
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    box.verdicts(f"{_iso(7200)} {J} OK")
    with open(box.home / ".cron_freshness_state.lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = box.run()
    assert "previous run still holds" in r.stderr
    assert box.read("verdict") == "" and box.alerts() == ""


def test_a_symlink_to_the_script_still_finds_its_repo(tmp_path):
    """The real consumer path: invoked through a symlink with NO seams for the
    verdict, it must reach the REAL scripts/cron_verdict.sh (sandboxed HOME
    so the verdict lands in a temp log; nothing is stale, so nothing pages)."""
    home = tmp_path / "home"
    home.mkdir()
    link = tmp_path / "cron_verdict_freshness.sh"
    link.symlink_to(SCRIPT)
    conf = tmp_path / "c.conf"
    conf.write_text("LOCAL_LABEL=box-a\nOFFLINE_CHECK_MAX_MIN=30\n")
    (home / "cron_verdicts.log").write_text(f"{_iso(60)} {J} OK\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CRON_FRESHNESS_")}
    env.update(HOME=str(home), CRON_FRESHNESS_HOME=str(home), CRON_FRESHNESS_CONF=str(conf),
               CRON_VERDICT_LOG=str(home / "cron_verdicts.log"),
               CRON_VERDICT_OUT_DIR=str(tmp_path / "out"))
    r = subprocess.run(["bash", str(link)], env=env, capture_output=True, text=True,
                       cwd="/", timeout=60)
    assert r.returncode == 0, r.stderr
    log = (home / "cron_verdicts.log").read_text()
    assert re.search(r" cron_freshness OK ", log), (log, r.stderr)


# ── reader T folds (2026-09-30, third independent reader) ──────────────────

def test_a_box_that_answers_without_the_file_is_flagged_not_unanswered(box):
    """A dead power_capture: the box ANSWERS, the file is gone. It was filed
    as 'unanswered' (not our job) and the run read OK."""
    fresh = int(time.time() - 60)
    box.ssh.write_text(
        "#!/bin/bash\n"
        f'case " $* " in *" good "*) echo {fresh} ;; *" gone "*) echo MISSING ;; *) exit 255 ;; esac\n')
    box.config(POWER_REMOTE_BOXES="good gone down")
    box.run()
    assert "gone/power_history.log: file missing/unreadable" in box.alerts(), box.alerts()
    v = box.read("verdict")
    assert v.startswith("cron_freshness|FAIL|1 stale") and "unanswered: down" in v, v


def test_a_login_banner_cannot_pose_as_the_answer(box):
    stale = int(time.time() - 45 * 60)
    box.ssh.write_text(f'#!/bin/bash\necho "Welcome to box-b"\necho {stale}\n')
    box.config(POWER_REMOTE_BOXES="box-b")
    box.run()
    assert "box-b/power_history.log: data file 45m stale" in box.alerts()


@pytest.mark.parametrize("leg", ["verdict", "file"])
def test_a_future_timestamp_is_flagged_not_fresh(box, leg):
    """honest_failure_modes #6: a negative age was never -gt max → fresh."""
    if leg == "verdict":
        box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
        box.verdicts(f"{_iso(-3 * 86400)} {J} OK")
    else:
        box.config(LOCAL_LABEL="box-a", POWER_LOCAL_MAX_MIN="10")
        p = box.home / "power_history.log"
        p.write_text("x\n")
        t = time.time() + 3 * 86400
        os.utime(p, (t, t))
    box.run()
    assert "in the FUTURE" in box.alerts(), box.alerts()
    assert box.read("verdict").startswith("cron_freshness|FAIL|")


def test_whitespace_in_the_label_is_refused(box):
    """State keys are the first field: 'my box/j' never matched itself, so
    every run re-paged and the state grew a line per run (regression of MINE
    in the awk fold — the old grep handled it)."""
    box.conf.write_text("LOCAL_LABEL=my box\nOFFLINE_CHECK_MAX_MIN=30\n")
    box.run()
    v = box.read("verdict")
    assert v.startswith("cron_freshness|CONCERN|") and "whitespace" in v, v


def test_duplicate_state_keys_still_gate_and_page(box):
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    box.verdicts(f"{_iso(7200)} {J} OK")
    old = int(time.time() - 7 * 3600)
    (box.home / ".cron_freshness_state").write_text(f"box-a/{J} {old - 100}\nbox-a/{J} {old}\n")
    r = box.run()
    assert "syntax error" not in r.stderr, r.stderr
    assert box.read("ntfy").count("fleet cron gone silent") == 1     # gate expired → pages


def test_duplicate_state_keys_use_the_newest_stamp(box):
    """Duplicated key whose NEWEST stamp is inside the 6h gate: no page. (A
    digit-guard alone reads the two-line value as 'unknown' and re-pages.)"""
    box.config(LOCAL_LABEL="box-a", OFFLINE_CHECK_MAX_MIN="30")
    box.verdicts(f"{_iso(7200)} {J} OK")
    now = int(time.time())
    (box.home / ".cron_freshness_state").write_text(f"box-a/{J} {now - 30000}\nbox-a/{J} {now - 600}\n")
    box.run()
    assert box.read("ntfy") == "", box.read("ntfy")
    assert box.read("verdict").startswith("cron_freshness|FAIL|")      # the record is never withheld
