"""scripts/fleet_offline_check.sh — the fleet paging monitor, now gated.

MOVED into the repo 2026-07-29 after THREE defects of one class surfaced in it in
a single day, while it lived untracked in the operator's home with no lint and no
tests:

  1. `bot` was checked at a stale address (.24, now unoccupied) — it is alive
     elsewhere;
  2. worse, the manager has NO ROUTE to bot's subnet, so an ssh failure there is
     BLINDNESS — yet it reported "the offline-monitor confirms DOWN" and paged for
     3.5 h about a healthy box (honest_failure_modes #1, committed by the
     reporter);
  3. `moc1` was a hardcoded NAT-front IP on a segment whose addresses this fleet
     does not control — the next false page, already queued.

These tests drive the REAL script with fake `ssh` and `curl` early on PATH. They
deliberately do NOT re-implement the decision logic: the thresholds, re-alert
cadence and escalation were hardened by actual incidents, and a test that
paraphrases them would pass against a paraphrase rather than the thing that runs
at 3am (the fleet_hosts lesson — 13 green tests over a detector that could not
detect, because they mocked the broken layer).
"""

import json
import os
import stat
import subprocess

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "fleet_offline_check.sh")

SSH_SHIM = """#!/usr/bin/env bash
# Fake ssh. Behaviour driven by files in $SHIMDIR so each test picks an outcome.
host=""
for a in "$@"; do case "$a" in *@*) host="${a#*@}";; esac; done
if [ -f "$SHIMDIR/unreachable_$host" ]; then exit 255; fi
if [ -f "$SHIMDIR/badsvc_$host" ]; then cat "$SHIMDIR/badsvc_$host"; exit 0; fi
exit 0
"""

CURL_SHIM = """#!/usr/bin/env bash
# Fake ntfy. Records each push and returns a 200 + id so delivery is "witnessed".
{ echo "PUSH $*"; } >> "$SHIMDIR/pushes.log"
printf '{"id":"fakeid123"}\\n200'
exit 0
"""


class Harness:
    def __init__(self, tmp):
        self.tmp = tmp
        self.shim = tmp / "shim"
        self.shim.mkdir()
        for name, body in (("ssh", SSH_SHIM), ("curl", CURL_SHIM)):
            p = self.shim / name
            p.write_text(body)
            p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self.state = tmp / "state.tsv"
        self.log = tmp / "alerts.log"
        self.witness = tmp / "witness.log"
        self.hb = tmp / "hb.log"
        self.conf = tmp / "boxes.json"
        self.topicdir = tmp / "cfg"
        self.topicdir.mkdir()

    def write_conf(self, boxes=None, ssh_user="testuser"):
        boxes = boxes if boxes is not None else [
            {"name": "alpha", "host": "alpha",
             "services": ["svc-a"], "tier": "critical"}]
        self.conf.write_text(json.dumps({"ssh_user": ssh_user, "boxes": boxes}))

    def unreachable(self, host):
        (self.shim / ("unreachable_%s" % host)).write_text("")

    def reachable(self, host):
        f = self.shim / ("unreachable_%s" % host)
        if f.exists():
            f.unlink()

    def bad_service(self, host, svc):
        (self.shim / ("badsvc_%s" % host)).write_text(svc + "\n")

    def run(self, env_extra=None):
        env = dict(os.environ)
        env.update({
            "HOME": str(self.tmp),
            "SHIMDIR": str(self.shim),
            "MESHFORGE_OFFLINE_PATH": "%s:/usr/local/bin:/usr/bin:/bin" % self.shim,
            "MESHFORGE_OFFLINE_BOXES": str(self.conf),
            "MESHFORGE_OFFLINE_STATE": str(self.state),
            "MESHFORGE_OFFLINE_LOG": str(self.log),
            "MESHFORGE_OFFLINE_WITNESS": str(self.witness),
            "MESHFORGE_OFFLINE_HB": str(self.hb),
            "MESHFORGE_FLEETKEY": str(self.tmp / "fakekey"),
            "MESHFORGE_NTFY_TOPIC": "throwaway-test-topic",
            "ALERT_THRESHOLD": "3",
        })
        if env_extra:
            env.update(env_extra)
        return subprocess.run(["bash", SCRIPT], capture_output=True, text=True,
                              timeout=120, env=env)

    def row(self, name):
        if not self.state.exists():
            return None
        for line in self.state.read_text().splitlines():
            f = line.split("\t")
            if f and f[0] == name:
                return f
        return None

    def pushes(self):
        p = self.shim / "pushes.log"
        return p.read_text() if p.exists() else ""

    def alerts(self):
        return self.log.read_text() if self.log.exists() else ""


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


class TestRefusesRatherThanSweepNothing:
    """An empty sweep reports 'nothing is down' about a fleet it never checked —
    indistinguishable from a healthy fleet. That is the #1 defect class, so the
    script must refuse loudly instead."""

    def test_missing_config_refuses(self, h):
        r = h.run()
        assert r.returncode == 78, r.stdout + r.stderr
        assert "FATAL" in h.alerts()
        assert not h.pushes(), "must not page anyone when it cannot know"

    def test_empty_box_list_refuses(self, h):
        h.write_conf(boxes=[])
        r = h.run()
        assert r.returncode == 78
        assert "refusing to run" in h.alerts()

    def test_missing_ssh_user_refuses(self, h):
        h.write_conf(ssh_user="")
        r = h.run()
        assert r.returncode == 78


class TestHealthyPath:

    def test_healthy_box_records_zeroes_and_pages_nobody(self, h):
        h.write_conf()
        r = h.run()
        assert r.returncode == 0, r.stdout + r.stderr
        assert h.row("alpha") == ["alpha", "0", "0", "0", "0", "0"]
        assert not h.pushes()

    def test_inactive_service_counts_as_a_fault(self, h):
        h.write_conf()
        h.bad_service("alpha", "svc-a")
        h.run()
        assert h.row("alpha")[1] == "1", "a dead service must accrue a failure"


class TestThresholdAndPaging:

    def test_below_threshold_does_not_page(self, h):
        h.write_conf()
        h.unreachable("alpha")
        for expected in ("1", "2"):
            h.run()
            assert h.row("alpha")[1] == expected
            assert not h.pushes(), "paged before the threshold"

    def test_pages_at_threshold_with_witnessed_delivery(self, h):
        h.write_conf()
        h.unreachable("alpha")
        for _ in range(3):
            h.run()
        assert "PUSH" in h.pushes(), "no page at threshold"
        assert "ALERT [alpha]" in h.alerts()
        row = h.row("alpha")
        assert row[2] == "1", "alerted flag not set"
        assert row[5] == "1", "alert_count not started"
        # delivery must be witnessed, not assumed
        assert "PUSH-OK id=" in h.witness.read_text()

    def test_does_not_repage_before_realert_interval(self, h):
        h.write_conf()
        h.unreachable("alpha")
        for _ in range(3):
            h.run()
        n = h.pushes().count("PUSH")
        h.run()
        assert h.pushes().count("PUSH") == n, "re-paged before the interval elapsed"

    def test_repages_once_interval_elapsed(self, h):
        h.write_conf()
        h.unreachable("alpha")
        for _ in range(3):
            h.run()
        n = h.pushes().count("PUSH")
        h.run(env_extra={"REALERT_INTERVAL": "0"})
        assert h.pushes().count("PUSH") > n, "an ongoing outage went silent"
        assert "STILL-DOWN" in h.alerts()
        assert h.row("alpha")[5] == "2", "alert_count did not advance"

    def test_recovery_pages_and_clears_state(self, h):
        h.write_conf()
        h.unreachable("alpha")
        for _ in range(3):
            h.run()
        h.reachable("alpha")
        h.run()
        assert "RECOVERED [alpha]" in h.alerts()
        assert h.row("alpha") == ["alpha", "0", "0", "0", "0", "0"]


class TestTierComesFromConfigNotAName:
    """The tier used to be `if [ "$name" = bot ]` — a box NAME baked into logic,
    so the tier could not follow a rename and no other box could ever be quiet.
    Same defect class as the hardcoded addresses fixed the same day."""

    def test_quiet_tier_uses_default_priority(self, h):
        h.write_conf(boxes=[{"name": "hobby", "host": "hobby",
                             "services": ["svc-a"], "tier": "quiet"}])
        h.unreachable("hobby")
        for _ in range(3):
            h.run()
        assert "Priority: default" in h.pushes(), h.pushes()

    def test_critical_tier_uses_high_priority(self, h):
        h.write_conf(boxes=[{"name": "prod", "host": "prod",
                             "services": ["svc-a"], "tier": "critical"}])
        h.unreachable("prod")
        for _ in range(3):
            h.run()
        assert "Priority: high" in h.pushes(), h.pushes()

    def test_quiet_tier_never_escalates_to_urgent(self, h):
        h.write_conf(boxes=[{"name": "hobby", "host": "hobby",
                             "services": ["svc-a"], "tier": "quiet"}])
        h.unreachable("hobby")
        for _ in range(3):
            h.run()
        for _ in range(6):
            h.run(env_extra={"QUIET_REALERT_INTERVAL": "0", "ESCALATE_AFTER": "2"})
        assert "Priority: urgent" not in h.pushes(), \
            "a quiet box escalated — the whole point of the tier is that it does not"

    def test_unspecified_tier_defaults_to_critical(self, h):
        h.write_conf(boxes=[{"name": "plain", "host": "plain",
                             "services": ["svc-a"]}])
        h.unreachable("plain")
        for _ in range(3):
            h.run()
        assert "Priority: high" in h.pushes()


class TestNoOperatorValuesInSource:
    """MF014 in spirit: the reason this file could not simply be copied into the
    repo. Membership and identity belong in operator config, not in code."""

    def test_source_carries_no_hostnames_or_user(self):
        src = open(SCRIPT).read()
        body = "\n".join(l for l in src.splitlines()
                         if not l.lstrip().startswith("#"))
        for forbidden in ("wh6gxz", '"moc"', "moc1;", "= bot ]"):
            assert forbidden not in body, \
                "operator value or baked box name leaked back into the script: %s" % forbidden

    def test_reads_membership_from_config(self):
        src = open(SCRIPT).read()
        assert "MESHFORGE_OFFLINE_BOXES" in src
        assert "fleet_offline_boxes.json" in src
