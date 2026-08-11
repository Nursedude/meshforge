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
# Destination is parsed the way real ssh parses it — first non-flag token, after
# skipping flags that take a value — so a bare config ALIAS (`ssh alaula true`,
# how a `via` dependency hop is probed) resolves as well as `user@host`.
host=""
skip=0
for a in "$@"; do
  if [ "$skip" = 1 ]; then skip=0; continue; fi
  case "$a" in
    -o|-i|-p|-l|-F|-E|-b|-c) skip=1; continue;;
    -*) continue;;
  esac
  host="$a"; break
done
host="${host#*@}"
echo "SSH $host" >> "$SHIMDIR/ssh.log"
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

    def ssh_log(self):
        p = self.shim / "ssh.log"
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
        assert h.row("alpha") == ["alpha", "0", "0", "0", "0", "0", "down"]
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
        assert h.row("alpha") == ["alpha", "0", "0", "0", "0", "0", "down"]


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


class TestPathAwareVerdict:
    """A box whose only route is a tunnel through another box cannot be judged by
    that route alone. On 2026-08-10 the T1 restore drill factory-reset the tunnel
    host and this monitor paged "Fleet box DOWN: kiai" for 54 min about a box that
    was up 17 days and never rebooted — the same blindness-as-DOWN lie already
    caught once on `bot` (see the module docstring), in a second skin.

    The distinction is only drawn for a box that DECLARES its dependency, so these
    tests pin both sides: the declaring box gets the honest verdict, and every
    other box keeps the historical behaviour untouched."""

    BOXES = [{"name": "leaf", "host": "leaf", "services": ["svc-a"],
              "tier": "critical", "via": "jump"}]

    def _page(self, h):
        for _ in range(3):
            h.run()

    def test_path_down_pages_unobservable_not_down(self, h):
        h.write_conf(boxes=self.BOXES)
        h.unreachable("leaf")
        h.unreachable("jump")
        self._page(h)
        pushes = h.pushes()
        assert "Fleet box UNOBSERVABLE" in pushes, pushes
        # The subject of the claim is what matters: "the PATH is down" is true and
        # must survive; "the BOX is down" is the 08-10 lie and must not appear.
        assert "Fleet box DOWN" not in pushes, \
            "claimed the BOX was down when only its path was observed"
        assert h.row("leaf")[6] == "unobservable"

    def test_path_up_still_says_down(self, h):
        """The dependency answering is what makes DOWN honest — the box is then
        the only thing left implicated. Without this leg the fix would be a
        blanket excuse that silences real outages."""
        h.write_conf(boxes=self.BOXES)
        h.unreachable("leaf")
        self._page(h)
        pushes = h.pushes()
        assert "Fleet box DOWN" in pushes, pushes
        assert "UNOBSERVABLE" not in pushes
        assert h.row("leaf")[6] == "down"

    def test_dependency_is_actually_probed(self, h):
        """Guard-drill doctrine: assert the probe HAPPENED, not just that the
        wording changed. A verdict derived without asking the dependency would
        be a guess wearing the right label."""
        h.write_conf(boxes=self.BOXES)
        h.unreachable("leaf")
        h.run()
        assert "SSH jump" in h.ssh_log(), h.ssh_log()

    def test_box_without_via_is_unchanged(self, h):
        h.write_conf(boxes=[{"name": "plain", "host": "plain",
                             "services": ["svc-a"], "tier": "critical"}])
        h.unreachable("plain")
        self._page(h)
        assert "Fleet box DOWN" in h.pushes()
        assert h.row("plain")[6] == "down"
        assert "SSH jump" not in h.ssh_log(), \
            "probed a dependency for a box that declares none"

    def test_service_failure_is_down_even_with_via(self, h):
        """We REACHED the box — its path is proven good, so a dead service is a
        real fault, never an observation problem."""
        h.write_conf(boxes=self.BOXES)
        h.bad_service("leaf", "svc-a")
        self._page(h)
        assert "Fleet box DOWN" in h.pushes()
        assert h.row("leaf")[6] == "down"

    def test_verdict_promotes_when_path_returns_mid_outage(self, h):
        """The path coming back while the box stays dark turns an UNKNOWN into a
        real DOWN. A verdict frozen at first observation would keep excusing an
        outage that has since become the box's fault."""
        h.write_conf(boxes=self.BOXES)
        h.unreachable("leaf")
        h.unreachable("jump")
        self._page(h)
        assert h.row("leaf")[6] == "unobservable"
        h.reachable("jump")
        h.run(env_extra={"REALERT_INTERVAL": "0"})
        assert h.row("leaf")[6] == "down"
        assert "STILL DOWN" in h.pushes()

    def test_recovery_from_unobservable_does_not_claim_it_was_down(self, h):
        h.write_conf(boxes=self.BOXES)
        h.unreachable("leaf")
        h.unreachable("jump")
        self._page(h)
        h.reachable("leaf")
        h.reachable("jump")
        h.run()
        alerts = h.alerts()
        assert "RECOVERED [leaf]" in alerts
        assert "was unobservable" in alerts, alerts
        assert "was down" not in alerts, \
            "recovery retroactively asserted the outage the page refused to claim"

    def test_legacy_six_field_row_is_read_as_down(self, h):
        """State rows written before this change have no verdict field and MEANT
        down. Defaulting them to down preserves their claim; defaulting them the
        other way would silently downgrade a real outage in flight."""
        h.write_conf(boxes=self.BOXES)
        h.state.write_text("leaf\t3\t1\t100\t100\t1\n")
        h.unreachable("leaf")
        h.reachable("jump")
        h.run()
        assert "RECOVERED" not in h.alerts()
        assert h.row("leaf")[6] == "down"


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
