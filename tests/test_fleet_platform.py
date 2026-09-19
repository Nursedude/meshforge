"""Tests for src/utils/fleet_platform.py — the update surface's SSOT.

The surface is read-only, so its whole value is the QUALITY of its
distinctions. These tests exist mostly to pin the three-way split that this
fleet learned the hard way between 08-05 and 09-02:

    inert   = deviates BY DECISION      -> not a fault, do not nag
    drift   = deviates, nobody decided  -> a finding
    unknown = could not tell            -> never a pass

Collapsing any pair produces either a surface people learn to ignore, or one
that reports a decided box as broken.
"""

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.fleet_platform import (  # noqa: E402
    DRIFT, INERT, OK, UNKNOWN, Catalog, Declaration, DistroBase,
    canonical_box, judge_box, kernel_flavour, kernel_version_key,
    load_catalog, load_declarations, match_declaration, newer_kernel_installed,
    reboot_owed, same_box, summarize,
)

CATALOG_YAML = """
version: 0
platform:
  distro_bases:
    trixie:   {tier: target,     python: "3.13"}
    bookworm: {tier: supported,  python: "3.11"}
    noble:    {tier: deprecated, python: "3.12"}
  support_floor_python: "3.9"
pins:
  meshtasticd:
    mechanism: apt_hold
    current: "2.7.26"
    why: "upstream regression"
    recheck:
      - {kind: github_pr_merged, repo: x/y, pr: 2, means: "fix landed"}
  rns:
    mechanism: fork_pin
    current: "1.3.8+mf.0"
    why: "six issues collapsed into one fork"
    recheck:
      - {kind: manual, means: "merge + parity + canary"}
"""


def _catalog(tmp_path, text=CATALOG_YAML):
    p = tmp_path / "fleet_platform.yaml"
    p.write_text(text)
    return str(p)


def _cat():
    """A hand-built catalog, so judge tests do not depend on YAML."""
    return Catalog(bases={
        "trixie": DistroBase("trixie", "target", "3.13"),
        "bookworm": DistroBase("bookworm", "supported", "3.11"),
        "noble": DistroBase("noble", "deprecated", "3.12"),
    })


class TestCatalog:
    def test_valid_catalog_loads(self, tmp_path):
        cat, errs = load_catalog(_catalog(tmp_path))
        assert errs == []
        assert cat.target_bases() == ["trixie"]
        assert cat.support_floor_python == "3.9"
        assert set(cat.pins) == {"meshtasticd", "rns"}

    def test_missing_file_is_none_plus_errors(self, tmp_path):
        cat, errs = load_catalog(str(tmp_path / "absent.yaml"))
        assert cat is None and errs

    def test_zero_bases_is_an_error_not_an_empty_baseline(self, tmp_path):
        """An empty baseline would read 'every base is fine' fleet-wide."""
        cat, errs = load_catalog(_catalog(tmp_path, """
platform:
  distro_bases: {}
"""))
        assert cat is None and errs

    def test_no_target_tier_is_an_error(self, tmp_path):
        cat, errs = load_catalog(_catalog(tmp_path, """
platform:
  distro_bases:
    bookworm: {tier: supported}
"""))
        assert cat is None
        assert any("target" in e for e in errs)

    def test_pin_without_why_is_refused(self, tmp_path):
        """A hold with no reason is how a hold outlives its reason."""
        cat, errs = load_catalog(_catalog(tmp_path, """
platform:
  distro_bases:
    trixie: {tier: target}
pins:
  thing: {mechanism: apt_hold, current: "1.0",
          recheck: [{kind: manual, means: m}]}
"""))
        assert cat is None
        assert any("why" in e for e in errs)

    def test_pin_without_recheck_is_refused(self, tmp_path):
        """Silence would imply the pin is watched when nothing watches it —
        'manual' must be stated, not omitted."""
        cat, errs = load_catalog(_catalog(tmp_path, """
platform:
  distro_bases:
    trixie: {tier: target}
pins:
  thing: {mechanism: apt_hold, current: "1.0", why: w}
"""))
        assert cat is None
        assert any("recheck" in e for e in errs)

    def test_unknown_recheck_kind_is_refused(self, tmp_path):
        cat, errs = load_catalog(_catalog(tmp_path, """
platform:
  distro_bases:
    trixie: {tier: target}
pins:
  thing: {mechanism: apt_hold, current: "1.0", why: w,
          recheck: [{kind: vibes}]}
"""))
        assert cat is None

    def test_self_monitoring_distinguishes_manual_only_pins(self, tmp_path):
        cat, _ = load_catalog(_catalog(tmp_path))
        assert cat.pins["meshtasticd"].self_monitoring is True
        assert cat.pins["rns"].self_monitoring is False


class TestDeclarations:
    def test_absent_file_is_undeclared_not_an_error(self, tmp_path):
        decls, status = load_declarations(str(tmp_path / "none.json"))
        assert decls == {} and status == "undeclared"

    def test_corrupt_file_is_invalid_never_empty(self, tmp_path):
        """'invalid' must not read as 'no deviations declared' — that would
        turn every deliberate box into drift in one bad write."""
        p = tmp_path / "d.json"
        p.write_text("{not json")
        decls, status = load_declarations(str(p))
        assert decls == {} and status == "invalid"

    def test_declaration_without_reason_is_dropped(self, tmp_path):
        p = tmp_path / "d.json"
        p.write_text(json.dumps({"boxes": {"b": {"base": "bookworm"}}}))
        decls, status = load_declarations(str(p))
        assert status == "declared" and decls == {}

    def test_valid_declaration_loads(self, tmp_path):
        p = tmp_path / "d.json"
        p.write_text(json.dumps({"boxes": {"moc4": {
            "base": "bookworm", "reason": "standalone-compat canary",
            "reviewed": "2026-09-11"}}}))
        decls, status = load_declarations(str(p))
        assert status == "declared"
        assert decls["moc4"].reason == "standalone-compat canary"


class TestStaleness:
    def test_recent_review_is_fresh(self):
        today = time.strftime("%Y-%m-%d")
        assert Declaration("b", "bookworm", "r", today).stale() is False

    def test_missing_review_date_is_stale_not_fresh(self):
        """A missing review date is not evidence of a recent review."""
        assert Declaration("b", "bookworm", "r", None).stale() is True

    def test_old_review_goes_stale(self):
        assert Declaration("b", "bookworm", "r", "2020-01-01").stale() is True


class TestBoxIdentity:
    """A box NAMES itself one way and the fleet ADDRESSES it another.

    `uname -n` answers `<prefix>-<alias>` on the fleet boxes while fleet_hosts,
    the registry and every `declare` call use the bare alias; one box's own
    uname differs from its registry key by CASE alone. Comparing the two
    namespaces with `==` judged remote rows under one and the local row under
    the other, so a box deliberately declared on an older base read INERT in
    the fleet pane and DRIFT on its own TUI screen.

    Names here are deliberately GENERIC: this pins the naming RULE, not this
    fleet, and real box names in a portability test are an MF014 violation.
    """

    def test_prefixed_hostname_is_the_same_box_as_its_alias(self):
        assert same_box("appname-boxa", "boxa")
        assert same_box("boxa", "appname-boxa")

    def test_case_and_domain_do_not_make_a_different_box(self):
        assert same_box("BoxA", "boxa")
        assert same_box("boxa.example.internal", "boxa")
        assert canonical_box("BOXA.example.internal") == "boxa"

    def test_the_dash_boundary_is_required(self):
        """A bare endswith would match `boxa` against anything ending in those
        letters and silently attach one box's declaration to another."""
        assert not same_box("notboxa", "boxa")
        assert not same_box("boxa1", "boxa")
        assert not same_box("superboxa", "boxa")

    def test_empty_names_never_match(self):
        assert not same_box("", "boxa")
        assert not same_box("boxa", "")

    def test_a_declaration_under_the_alias_reaches_the_prefixed_box(self):
        """THE finding. `declare boxa bookworm` is typed with the alias; the
        box answers as the prefixed hostname and read DRIFT anyway."""
        decls = {"boxa": Declaration("boxa", "bookworm", "standalone canary",
                                     time.strftime("%Y-%m-%d"))}
        v = judge_box("appname-boxa", "bookworm", _cat(), decls)
        assert v.verdict == INERT, v.detail
        assert "standalone canary" in v.detail

    def test_the_matched_key_is_named_when_it_differs(self):
        """The reader sees a pane saying `appname-boxa` and a file saying
        `boxa`; an unexplained match is as confusing as a missed one."""
        decls = {"boxa": Declaration("boxa", "bookworm", "canary",
                                     time.strftime("%Y-%m-%d"))}
        assert "declared as 'boxa'" in judge_box(
            "appname-boxa", "bookworm", _cat(), decls).detail
        # ...and NOT named when it is the same name, which would be noise.
        assert "declared as" not in judge_box(
            "boxa", "bookworm", _cat(), decls).detail

    def test_an_exact_key_wins_over_a_prefix_match(self):
        """Determinism first: a key that IS this box's name is never in doubt,
        so a looser candidate must not make it ambiguous."""
        decls = {
            "boxa": Declaration("boxa", "bookworm", "the alias one",
                                time.strftime("%Y-%m-%d")),
            "appname-boxa": Declaration("appname-boxa", "bookworm",
                                        "the prefixed one",
                                        time.strftime("%Y-%m-%d")),
        }
        v = judge_box("appname-boxa", "bookworm", _cat(), decls)
        assert v.verdict == INERT
        assert "the prefixed one" in v.detail

    def test_two_declarations_that_could_both_be_this_box_are_unknown(self):
        """Never silently resolved: that is a finding about the file, and
        picking one would make a real ambiguity look like a settled decision.

        Two apps' prefixes over one alias, with nothing naming the box
        exactly — there is no honest way to choose.
        """
        decls = {
            "appname-boxa": Declaration("appname-boxa", "bookworm", "a",
                                        "2026-09-11"),
            "otherapp-boxa": Declaration("otherapp-boxa", "bookworm", "b",
                                         "2026-09-11"),
        }
        v = judge_box("boxa", "bookworm", _cat(), decls)
        assert v.verdict == UNKNOWN, v.detail
        assert "cannot tell which" in v.detail

    def test_match_declaration_reports_the_key_it_used(self):
        decls = {"boxa": Declaration("boxa", "bookworm", "x", "2026-09-11")}
        key, decl, amb = match_declaration("appname-boxa", decls)
        assert (key, amb) == ("boxa", [])
        assert decl.reason == "x"
        assert match_declaration("boxz", decls) == (None, None, [])


class TestTheScriptRoutesTheLocalRow:
    """The other half of the namespace split: the fleet PANE.

    Remote rows are addressed by fleet alias and the local row by kernel
    hostname. Comparing them with `==` meant a hosts file that already named
    this box (under the alias) got a SECOND row appended under the other
    spelling, and `observe` then sent the alias row over ssh — to ourselves.
    """

    CLI = Path(__file__).resolve().parents[1] / "scripts" / "fleet_platform.py"

    def _mod(self):
        spec = importlib.util.spec_from_file_location(
            "fleet_platform_cli", str(self.CLI))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _stub_probes(self, mod, monkeypatch, ssh_calls):
        def _fake_ssh(host, probe):
            ssh_calls.append(host)
            return 0, ""
        monkeypatch.setattr(mod, "_ssh", _fake_ssh)
        monkeypatch.setattr(mod, "_parse", lambda text: {
            "base": "trixie", "python": None, "pending": None,
            "holds": [], "why": ""})

        class _Done:
            stdout = ""
            returncode = 0
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Done())

    def test_a_hosts_list_already_naming_this_box_gains_no_duplicate(self):
        mod = self._mod()
        hosts = ["boxc", "boxa", "boxd"]
        assert "BoxA" not in hosts            # why an `in` test was not enough
        assert mod.resolve_hosts(hosts, "BoxA") == hosts
        assert mod.resolve_hosts(["appname-boxa"], "boxa") == ["appname-boxa"]

    def test_a_hosts_list_without_this_box_still_gains_it(self):
        mod = self._mod()
        assert mod.resolve_hosts(["boxc", "boxd"], "BoxA") == [
            "boxc", "boxd", "BoxA"]

    def test_the_alias_row_for_this_box_is_observed_locally_not_over_ssh(
            self, monkeypatch):
        mod = self._mod()
        ssh_calls = []
        self._stub_probes(mod, monkeypatch, ssh_calls)
        out = mod.observe(["boxa"], "appname-boxa")
        assert ssh_calls == [], f"ssh'd to this very box: {ssh_calls}"
        assert list(out) == ["boxa"]
        assert out["boxa"]["base"] == "trixie"

    def test_a_genuinely_remote_host_still_goes_over_ssh(self, monkeypatch):
        """The mirror: loosening the compare must not swallow real peers."""
        mod = self._mod()
        ssh_calls = []
        self._stub_probes(mod, monkeypatch, ssh_calls)
        mod.observe(["boxc"], "appname-boxa")
        assert ssh_calls == ["boxc"]

    def test_a_stalled_local_probe_degrades_its_row_not_the_whole_pane(
            self, monkeypatch):
        """An expensive OPTIONAL field must never destroy the cheap ESSENTIAL
        one — this module's own rule, stated above PROBE.

        The local branch called subprocess.run bare, so a TimeoutExpired (the
        probe shells out to `apt-get -s upgrade`; an apt lock is enough) raised
        straight out of ThreadPoolExecutor.map and threw away every remote row
        already collected. The ssh branch had always degraded to base=None plus
        a reason.
        """
        mod = self._mod()
        ssh_calls = []
        self._stub_probes(mod, monkeypatch, ssh_calls)

        def _stall(*a, **k):
            raise mod.subprocess.TimeoutExpired(cmd="bash",
                                                timeout=mod.SSH_TIMEOUT)
        monkeypatch.setattr(mod.subprocess, "run", _stall)

        out = mod.observe(["boxa", "boxc"], "boxa")
        assert out["boxc"]["base"] == "trixie", \
            "one stalled local probe took the whole pane down with it"
        assert out["boxa"]["base"] is None
        assert "timed out" in out["boxa"]["why"]
        assert "locally" in out["boxa"]["why"], \
            "the reason must say WHERE — a local stall is not an ssh problem"

    def test_a_local_probe_that_cannot_run_at_all_says_why(self, monkeypatch):
        """An UNKNOWN that cannot say WHY costs its reader a debugging session
        to tell 'box is down' from 'our probe broke' — judge_box says as much."""
        mod = self._mod()
        ssh_calls = []
        self._stub_probes(mod, monkeypatch, ssh_calls)

        def _boom(*a, **k):
            raise OSError("Exec format error")
        monkeypatch.setattr(mod.subprocess, "run", _boom)

        out = mod.observe(["boxa"], "boxa")
        assert out["boxa"]["base"] is None
        assert "Exec format error" in out["boxa"]["why"]

    def test_a_local_probe_exiting_nonzero_is_not_parsed_as_an_observation(
            self, monkeypatch):
        """The local branch ignored the return code entirely and parsed
        whatever came out, so a failed probe became an empty-but-'observed'
        row with no reason attached."""
        mod = self._mod()
        ssh_calls = []
        self._stub_probes(mod, monkeypatch, ssh_calls)

        class _Failed:
            stdout = ""
            returncode = 3
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Failed())

        out = mod.observe(["boxa"], "boxa")
        assert out["boxa"]["base"] is None
        assert "rc=3" in out["boxa"]["why"]


class TestJudge:
    def test_target_base_is_ok(self):
        v = judge_box("moc", "trixie", _cat(), {})
        assert v.verdict == OK

    def test_undeclared_deviation_is_drift(self):
        v = judge_box("moc4", "bookworm", _cat(), {})
        assert v.verdict == DRIFT

    def test_declared_deviation_is_inert_not_drift(self):
        """THE distinction. A decided box must not nag."""
        decls = {"moc4": Declaration("moc4", "bookworm", "standalone canary",
                                     time.strftime("%Y-%m-%d"))}
        v = judge_box("moc4", "bookworm", _cat(), decls)
        assert v.verdict == INERT
        assert "standalone canary" in v.detail
        assert v.stale_declaration is False

    def test_declared_but_unreviewed_is_still_inert_yet_flagged(self):
        """The deviation stays inert; the DECISION ages visibly. An indefinite
        declaration is how a call becomes its own warrant."""
        decls = {"moc4": Declaration("moc4", "bookworm", "canary", "2019-01-01")}
        v = judge_box("moc4", "bookworm", _cat(), decls)
        assert v.verdict == INERT and v.stale_declaration is True

    def test_declaration_naming_a_different_base_is_drift(self):
        """One of the two moved and nobody noticed — that IS the finding."""
        decls = {"moc4": Declaration("moc4", "bookworm", "canary", "2026-09-11")}
        v = judge_box("moc4", "trixie2", _cat(), decls)
        assert v.verdict == UNKNOWN  # uncatalogued base short-circuits first
        decls2 = {"moc5": Declaration("moc5", "bookworm", "canary", "2026-09-11")}
        v2 = judge_box("moc5", "noble", _cat(), decls2)
        assert v2.verdict == DRIFT
        assert "disagree" in v2.detail

    def test_declaring_a_deprecated_base_does_not_settle_it(self):
        """A declaration cannot un-deprecate a base — noble is retirement
        work, and calling it inert would park it forever."""
        decls = {"moc5": Declaration("moc5", "noble", "was experimenting",
                                     time.strftime("%Y-%m-%d"))}
        v = judge_box("moc5", "noble", _cat(), decls)
        assert v.verdict == DRIFT
        assert "DEPRECATED" in v.detail

    def test_unobserved_is_unknown_never_ok(self):
        v = judge_box("kiai", None, _cat(), {})
        assert v.verdict == UNKNOWN
        assert "not healthy" in v.detail

    def test_unobserved_names_the_failing_leg(self):
        """An UNKNOWN that cannot say WHY costs its reader a debugging
        session. 2026-09-11: moc3 read 'not observed' while up and answering,
        because a 24s apt simulation blew a 25s timeout — that is a bug in the
        instrument, not news about the box."""
        v = judge_box("moc3", None, _cat(), {},
                      unobserved_why="probe timed out after 25s — OUR limit")
        assert v.verdict == UNKNOWN
        assert "OUR limit" in v.detail

    def test_unobserved_without_a_reason_says_so(self):
        """Silence about the reason must itself be visible, not blank."""
        v = judge_box("x", None, _cat(), {})
        assert "no reason recorded" in v.detail

    def test_uncatalogued_base_is_unknown_never_ok(self):
        v = judge_box("x", "plan9", _cat(), {})
        assert v.verdict == UNKNOWN
        assert "not in the catalog" in v.detail

    def test_unreadable_declarations_make_deviations_unknown_not_drift(self):
        """Cannot read the decisions => cannot tell deliberate from drift.
        Refusing to guess beats reporting a decided box as broken."""
        v = judge_box("moc4", "bookworm", _cat(), {}, decl_status="unreadable")
        assert v.verdict == UNKNOWN
        assert "cannot tell" in v.detail

    def test_unreadable_declarations_do_not_taint_target_boxes(self):
        """A box on the target base needs no declaration, so a broken
        declarations file must not make the whole fleet unknown."""
        v = judge_box("moc", "trixie", _cat(), {}, decl_status="unreadable")
        assert v.verdict == OK


class TestSummarize:
    def test_counts_every_verdict_class(self):
        vs = [judge_box("a", "trixie", _cat(), {}),
              judge_box("b", "noble", _cat(), {}),
              judge_box("c", None, _cat(), {})]
        s = summarize(vs)
        assert s[OK] == 1 and s[DRIFT] == 1 and s[UNKNOWN] == 1
        assert s[INERT] == 0



# ── reboot-owed reduction (2026-09-19) ───────────────────────────────────
# Regression anchor: on 2026-09-19 a session told the operator a reboot was
# owed on a box because mini's RECENT-FIRES list still showed
# kernel_reboot_pending. The box had rebooted 13 min after that fire and was
# already on the new kernel -- the fire was history, the standing fact was
# the opposite, and nothing rendered the standing fact. These pin it.

class TestKernelFlavour:
    def test_splits_at_plus(self):
        assert kernel_flavour("6.18.50+rpt-rpi-2712") == "+rpt-rpi-2712"
        assert kernel_flavour("6.18.50+rpt-rpi-v8") == "+rpt-rpi-v8"

    def test_no_flavour_is_empty_not_error(self):
        assert kernel_flavour("6.18.50") == ""
        assert kernel_flavour("") == ""


class TestKernelVersionKey:
    def test_numeric_head(self):
        assert kernel_version_key("6.18.50+rpt-rpi-v8") == (6, 18, 50)

    def test_orders_within_a_series(self):
        assert (kernel_version_key("6.18.50+rpt-rpi-v8")
                > kernel_version_key("6.18.39+rpt-rpi-v8"))

    def test_minor_is_numeric_not_lexical(self):
        """String sort puts 6.12.109 above 6.18.33. It must not."""
        assert (kernel_version_key("6.18.33+rpt-rpi-v8")
                > kernel_version_key("6.12.109+rpt-rpi-v8"))

    def test_garbage_sorts_low_rather_than_raising(self):
        assert kernel_version_key("not-a-version") == ()


class TestNewerKernelInstalled:
    def test_finds_newer_same_flavour(self):
        assert newer_kernel_installed(
            "6.18.39+rpt-rpi-v8",
            ["6.18.39+rpt-rpi-v8", "6.18.50+rpt-rpi-v8"]) == "6.18.50+rpt-rpi-v8"

    def test_ignores_other_flavour(self):
        """A -2712 package a -v8 box can never boot must not count as newer."""
        assert newer_kernel_installed(
            "6.18.50+rpt-rpi-v8",
            ["6.18.50+rpt-rpi-v8", "6.19.0+rpt-rpi-2712"]) is None

    def test_none_when_running_is_newest(self):
        assert newer_kernel_installed(
            "6.18.50+rpt-rpi-v8",
            ["6.18.33+rpt-rpi-v8", "6.18.50+rpt-rpi-v8"]) is None

    def test_picks_highest_not_first(self):
        assert newer_kernel_installed(
            "6.18.29+rpt-rpi-v8",
            ["6.18.33+rpt-rpi-v8", "6.18.50+rpt-rpi-v8",
             "6.18.39+rpt-rpi-v8"]) == "6.18.50+rpt-rpi-v8"


class TestRebootOwed:
    def test_already_rebooted_reads_false(self):
        """THE miss: running the newest installed kernel, flag absent."""
        owed, why = reboot_owed(reboot_required_flag=False,
                                running_kernel="6.18.50+rpt-rpi-2712",
                                installed_kernels=["6.18.39+rpt-rpi-2712",
                                                   "6.18.50+rpt-rpi-2712"])
        assert owed is False
        assert "newest installed" in why

    def test_kernel_installed_but_not_booted_reads_true(self):
        owed, why = reboot_owed(reboot_required_flag=False,
                                running_kernel="6.18.39+rpt-rpi-v8",
                                installed_kernels=["6.18.39+rpt-rpi-v8",
                                                   "6.18.50+rpt-rpi-v8"])
        assert owed is True
        assert "6.18.50+rpt-rpi-v8" in why

    def test_distro_flag_believed_with_no_kernel_change(self):
        """libc/systemd upgrades set the flag without a new kernel."""
        owed, why = reboot_owed(reboot_required_flag=True,
                                running_kernel="6.18.50+rpt-rpi-v8",
                                installed_kernels=["6.18.50+rpt-rpi-v8"])
        assert owed is True
        assert "reboot-required" in why

    def test_unobservable_is_unknown_never_false(self):
        owed, _ = reboot_owed(reboot_required_flag=None,
                              running_kernel=None, installed_kernels=None)
        assert owed is None

    def test_unreadable_flag_still_catches_newer_kernel(self):
        owed, _ = reboot_owed(reboot_required_flag=None,
                              running_kernel="6.18.39+rpt-rpi-v8",
                              installed_kernels=["6.18.50+rpt-rpi-v8"])
        assert owed is True

    def test_absent_flag_with_no_kernel_data_is_unknown_not_false(self):
        owed, _ = reboot_owed(reboot_required_flag=False,
                              running_kernel=None, installed_kernels=None)
        assert owed is None
