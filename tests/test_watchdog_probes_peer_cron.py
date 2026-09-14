"""probe_peer_cron_verdict_stale — a watchdog-less box's crons reach the spine.

The gap these pin (2026-09-13): lehua is `role: field-node`, declared to run no
watchdog, so `probe_cron_verdict_stale` never ran for it. Its verdicts reached
the manager over the ssh truth spool and became a /fleet CELL — a surface a
human must open — and nothing else. It failed its hourly cron 26 consecutive
times and no signal ever existed.
"""
import base64
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.watchdog_probe_core import (  # noqa: E402
    SIGNAL_CLASSES, collect_dispositions, reset_dispositions,
)
from utils.watchdog_probes_peer_cron import (  # noqa: E402
    probe_peer_cron_verdict_stale,
)

_CRONTAB = (
    "41 * * * * /opt/meshforge/scripts/fleet_hosts_selfheal.sh >/dev/null "
    "2>&1 || /opt/meshforge/scripts/cron_verdict.sh fleet_hosts_drift FAIL "
    "wrapper_crashed\n"
)


def _verdict(status, ago_s, now, name="fleet_hosts_drift"):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - ago_s))
    return f"{ts} {name} {status} some message\n"


def _spool(tmp_path, alias, verdicts, *, now, age_s=10.0, crontab=_CRONTAB):
    doc = {
        "schema": "truth_spool/v1",
        "alias": alias,
        "fetched_at": now - age_s,
        "schedules": {
            "crontab_b64": base64.b64encode(crontab.encode()).decode(),
            "verdicts_b64": base64.b64encode(verdicts.encode()).decode(),
        },
    }
    (tmp_path / f"{alias}.json").write_text(json.dumps(doc))
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_dispositions():
    reset_dispositions()
    yield
    reset_dispositions()


class TestItRoutesIntoTheExistingClass:

    def test_emits_no_new_signal_class(self):
        """THE design constraint. This probe must not grow the closed enum —
        it routes a peer's failure into the class that already carries the
        fleet's chosen severity for this subject."""
        assert "cron_verdict_stale" in SIGNAL_CLASSES
        assert "peer_cron_verdict_stale" not in SIGNAL_CLASSES

    def test_failing_peer_becomes_a_signal_subject(self, tmp_path):
        """The whole point: lehua's failure exists as a SIGNAL, not only as a
        web-page cell, and carries the peer's name as its subject."""
        now = time.time()
        _spool(tmp_path, "lehua",
               _verdict("FAIL", 3600, now) + _verdict("FAIL", 60, now), now=now)
        sigs = []
        for _ in range(4):          # 2-tick debounce, as the local probe has
            sigs = probe_peer_cron_verdict_stale(now=now, spool_dir=tmp_path)
        assert len(sigs) == 1, sigs
        assert sigs[0].cls == "cron_verdict_stale"
        assert sigs[0].subject == "lehua"
        assert "lehua" in sigs[0].detail
        assert sigs[0].extra["via"] == "truth_spool"

    def test_healthy_peer_emits_nothing_but_leaves_a_witness(self, tmp_path):
        """`clean` must still SAY it judged somebody. An all-healthy fleet and
        a dead peer leg both emit zero signals; only the coverage tells them
        apart."""
        now = time.time()
        _spool(tmp_path, "lehua", _verdict("OK", 60, now), now=now)
        assert probe_peer_cron_verdict_stale(now=now, spool_dir=tmp_path) == []
        d = collect_dispositions()["cron_verdict_stale"]
        assert d["disp"] == "clean"
        assert d["coverage"] == {"judged": 1, "enrolled": 1}


class TestUnobservableIsNeverHealthy:

    def test_stale_spool_is_indeterminate_not_silent(self, tmp_path):
        now = time.time()
        _spool(tmp_path, "lehua", _verdict("OK", 60, now), now=now, age_s=9999)
        assert probe_peer_cron_verdict_stale(now=now, spool_dir=tmp_path) == []
        d = collect_dispositions()["cron_verdict_stale"]
        assert d["disp"] == "indeterminate"
        assert "lehua" in d["reason"]
        assert d["coverage"]["judged"] == 0

    def test_future_stamped_spool_is_also_unobservable(self, tmp_path):
        """A clock that ran backwards must not read fresh (#74's class)."""
        now = time.time()
        _spool(tmp_path, "lehua", _verdict("OK", 60, now), now=now, age_s=-5000)
        assert probe_peer_cron_verdict_stale(now=now, spool_dir=tmp_path) == []
        assert collect_dispositions()["cron_verdict_stale"]["disp"] == "indeterminate"

    def test_corrupt_spool_is_indeterminate(self, tmp_path):
        (tmp_path / "lehua.json").write_text("{not json")
        assert probe_peer_cron_verdict_stale(now=time.time(),
                                             spool_dir=tmp_path) == []
        d = collect_dispositions()["cron_verdict_stale"]
        assert d["disp"] == "indeterminate"
        assert "unreadable" in d["reason"]

    def test_unconfirmed_peer_failure_is_not_yet_a_signal(self, tmp_path):
        """One verdict line cannot confirm a fast cron. That is `dark` — held,
        not healthy, and not yet a finding."""
        now = time.time()
        _spool(tmp_path, "lehua", _verdict("FAIL", 60, now), now=now)
        assert probe_peer_cron_verdict_stale(now=now, spool_dir=tmp_path) == []
        assert collect_dispositions()["cron_verdict_stale"]["disp"] == "indeterminate"


class TestAbsenceIsReadCorrectly:

    def test_no_spool_and_no_targets_says_nothing(self, tmp_path, monkeypatch):
        """Not the manager. Absence of a spool is not a claim about crons —
        the LOCAL probe owns this class on such a box."""
        import utils.watchdog_probes_peer_cron as m
        monkeypatch.setattr(m, "_declared_targets", lambda: [])
        assert probe_peer_cron_verdict_stale(now=time.time(),
                                             spool_dir=tmp_path) == []
        assert collect_dispositions() == {}

    def test_declared_targets_with_no_spool_is_a_dead_cron_finding(
            self, tmp_path, monkeypatch):
        """THE one that would have caught a dead spool cron. Targets declared
        but nothing written = every peer just went invisible, which must not
        render identically to 'all peers healthy'."""
        import utils.watchdog_probes_peer_cron as m
        monkeypatch.setattr(m, "_declared_targets", lambda: ["lehua", "kiai"])
        assert probe_peer_cron_verdict_stale(now=time.time(),
                                             spool_dir=tmp_path) == []
        d = collect_dispositions()["cron_verdict_stale"]
        assert d["disp"] == "indeterminate"
        assert d["coverage"] == {"judged": 0, "enrolled": 2}

    def test_debounce_state_files_are_not_mistaken_for_peers(self, tmp_path):
        """The spool dir holds `cron_debounce.<alias>.json` beside the spool
        files. Reading one as a peer would invent a box."""
        now = time.time()
        (tmp_path / "cron_debounce.lehua.json").write_text('{"streak":0}')
        _spool(tmp_path, "lehua", _verdict("OK", 60, now), now=now)
        assert probe_peer_cron_verdict_stale(now=now, spool_dir=tmp_path) == []
        assert (collect_dispositions()["cron_verdict_stale"]["coverage"]
                == {"judged": 1, "enrolled": 1})


class TestRootServiceSafety:
    """meshforge-watchdog runs as ROOT with no SUDO_USER, where
    `get_real_user_home()` is /root — but the spool is written by an OPERATOR
    cron into the operator's ~/.local/state. Measured before this guard
    existed: the probe judged 4 peers as the operator and ZERO as root, and
    said nothing either way. A silently-inert peer leg is the exact failure
    this module exists to end (calibrated_claims #7)."""

    def test_spool_dir_follows_the_operator_not_the_euid(self, monkeypatch):
        import utils.watchdog_probes_peer_cron as m
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setattr(m, "_operator_home", lambda: Path("/home/someone"))
        assert (m._operator_spool_dir()
                == Path("/home/someone/.local/state/meshforge/truth_spool"))

    def test_unresolvable_operator_raises_rather_than_reading_empty(
            self, monkeypatch):
        """It must NOT fall back to a directory that happens to be empty —
        that is 'no peers' rendered as health. Raising makes the caller say
        `indeterminate`."""
        import utils.watchdog_probes_peer_cron as m
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setattr(m, "_operator_home", lambda: None)
        with pytest.raises(RuntimeError):
            m._operator_spool_dir()

    def test_unresolvable_operator_is_reported_indeterminate(self, monkeypatch):
        import utils.watchdog_probes_peer_cron as m
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setattr(m, "_operator_home", lambda: None)
        assert probe_peer_cron_verdict_stale(now=time.time()) == []
        d = collect_dispositions()["cron_verdict_stale"]
        assert d["disp"] == "indeterminate"
        assert "operator" in d["reason"]

