"""Tests for scripts/fleet_power.py — the ordered fleet shutdown executor.

Every test pins its own graph in a tmp file and injects its own reachability
probe. Nothing here reads the live fleet, the operator's real box config, or
the real posture file: a test whose verdict depends on un-pinned machine state
pins nothing (the 2026-07-28 lesson, where two probe tests read the running
crontab and gave three different verdicts on three boxes).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import fleet_power as fpw  # noqa: E402


def write_graph(tmp_path, boxes):
    p = tmp_path / "boxes.json"
    p.write_text(json.dumps({"ssh_user": "u", "boxes": boxes}), encoding="utf-8")
    return str(p)


ALL_DARK = lambda name: False          # noqa: E731 — nothing is reachable
ALL_UP = lambda name: True             # noqa: E731 — everything is reachable


class TestOrdering:
    def test_leaf_precedes_its_hop(self):
        via = {"leaf": "hop"}
        assert fpw.order_shutdown(["hop", "leaf"], via) == ["leaf", "hop"]

    def test_ordering_is_topology_not_alphabetical_ascending(self):
        """Paired with the descending case below so NO sort-only implementation
        can pass both. Found by drilling: with names 'leaf'/'hop' a reversed
        sort produces the right answer BY ALPHABETICAL COINCIDENCE, so that
        test alone passed against a planted defect that removed the ordering
        constraint entirely."""
        # correct order runs A->Z, so a reversed sort gets it wrong
        assert fpw.order_shutdown(["zulu", "alpha"], {"alpha": "zulu"}) == ["alpha", "zulu"]

    def test_ordering_is_topology_not_alphabetical_descending(self):
        # correct order runs Z->A, so a plain sort gets it wrong
        assert fpw.order_shutdown(["alpha", "zulu"], {"zulu": "alpha"}) == ["zulu", "alpha"]

    def test_order_is_independent_of_argument_order(self):
        via = {"leaf": "hop"}
        assert (fpw.order_shutdown(["leaf", "hop"], via)
                == fpw.order_shutdown(["hop", "leaf"], via) == ["leaf", "hop"])

    def test_independent_boxes_are_sorted_deterministically(self):
        assert fpw.order_shutdown(["c", "a", "b"], {}) == ["a", "b", "c"]

    def test_chain_of_three_unwinds_leaf_first(self):
        # a -> b -> c : a must go first, c last.
        via = {"a": "b", "b": "c"}
        assert fpw.order_shutdown(["c", "b", "a"], via) == ["a", "b", "c"]

    def test_two_leaves_behind_one_hop_both_precede_it(self):
        via = {"x": "hop", "y": "hop"}
        order = fpw.order_shutdown(["hop", "x", "y"], via)
        assert order.index("x") < order.index("hop")
        assert order.index("y") < order.index("hop")

    def test_hop_outside_the_target_set_does_not_constrain(self):
        # Powering down only the leaf is always safe; the hop stays up.
        assert fpw.order_shutdown(["leaf"], {"leaf": "hop"}) == ["leaf"]

    def test_duplicate_targets_collapse(self):
        assert fpw.order_shutdown(["a", "a"], {}) == ["a"]

    def test_cycle_is_a_loud_refusal(self):
        via = {"a": "b", "b": "a"}
        with pytest.raises(fpw.Refusal) as exc:
            fpw.order_shutdown(["a", "b"], via)
        assert "cycle" in str(exc.value)


class TestStrandingGuard:
    """The guard that would have prevented today's real hazard: powering a hop
    down while a live box still routes through it."""

    def test_refuses_when_a_live_box_routes_through_the_target(self):
        with pytest.raises(fpw.Refusal) as exc:
            fpw.check_no_stranding(["hop"], {"leaf": "hop"}, probe=ALL_UP)
        msg = str(exc.value)
        assert "leaf" in msg and "hop" in msg
        assert "UNOBSERVABLE" in msg          # names the consequence, not just "no"

    def test_allows_when_the_dependant_is_already_dark(self):
        notes = fpw.check_no_stranding(["hop"], {"leaf": "hop"}, probe=ALL_DARK)
        assert any("not stranded" in n for n in notes)

    def test_allows_when_the_dependant_is_also_a_target(self):
        # Both in the operation -> ordering handles it, no refusal.
        assert fpw.check_no_stranding(["leaf", "hop"], {"leaf": "hop"}, probe=ALL_UP) == []

    def test_probe_is_not_called_for_a_targeted_dependant(self):
        calls = []

        def probe(name):
            calls.append(name)
            return True

        fpw.check_no_stranding(["leaf", "hop"], {"leaf": "hop"}, probe=probe)
        assert calls == []


class TestSelfGuard:
    """This guard was structurally unable to fire until 2026-09-10: the manager
    is deliberately absent from the boxes config, so the unknown-host check
    consumed the name first. Order of checks is part of the contract."""

    def test_exact_hostname_is_refused(self, monkeypatch):
        monkeypatch.setenv("MESHFORGE_POWER_SELF", "boxzero")
        with pytest.raises(fpw.Refusal) as exc:
            fpw.check_not_self(["boxzero"])
        assert "THIS box" in str(exc.value)

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("MESHFORGE_POWER_SELF", "BoxZero")
        with pytest.raises(fpw.Refusal):
            fpw.check_not_self(["boxzero"])

    def test_fleet_alias_of_a_longer_real_hostname_is_refused(self, monkeypatch):
        # A box answering as 'prefix-name' is the fleet's 'name'.
        monkeypatch.setenv("MESHFORGE_POWER_SELF", "prefix-name")
        with pytest.raises(fpw.Refusal):
            fpw.check_not_self(["name"])

    def test_suffix_match_requires_a_dash_boundary(self, monkeypatch):
        # Regression: a bare endswith let a hostname swallow any box whose name
        # merely happened to be its final letters. The '-' keeps them apart.
        monkeypatch.setenv("MESHFORGE_POWER_SELF", "alphabox")
        fpw.check_not_self(["box"])         # must NOT raise

    def test_self_check_runs_before_the_membership_check(self, monkeypatch, tmp_path):
        """Naming the manager must give the SPECIFIC refusal even though the
        manager is not in the graph — otherwise the guard is unreachable."""
        monkeypatch.setenv("MESHFORGE_POWER_SELF", "themanager")
        boxes, via = fpw.load_graph(write_graph(tmp_path, [{"name": "a", "host": "a"}]))
        with pytest.raises(fpw.Refusal) as exc:
            fpw.build_plan(["themanager"], boxes, via, probe=ALL_DARK)
        assert "THIS box" in str(exc.value)
        assert "unknown host" not in str(exc.value)


class TestGraph:
    def test_hops_are_known_targets_even_though_they_are_not_boxes(self, tmp_path):
        g = write_graph(tmp_path, [{"name": "leaf", "host": "leaf", "via": "hop"}])
        boxes, via = fpw.load_graph(g)
        assert "hop" not in boxes
        assert "hop" in fpw.known_hosts(boxes, via)

    def test_unknown_host_is_refused_and_lists_what_is_known(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MESHFORGE_POWER_SELF", "themanager")
        boxes, via = fpw.load_graph(write_graph(tmp_path, [{"name": "a", "host": "a"}]))
        with pytest.raises(fpw.Refusal) as exc:
            fpw.build_plan(["nope"], boxes, via, probe=ALL_DARK)
        assert "nope" in str(exc.value) and "a" in str(exc.value)

    def test_missing_config_refuses_rather_than_assuming_no_dependencies(self, tmp_path):
        with pytest.raises(fpw.Refusal) as exc:
            fpw.load_graph(str(tmp_path / "absent.json"))
        assert "Refusing" in str(exc.value)

    def test_invalid_config_refuses_loudly(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(fpw.Refusal) as exc:
            fpw.load_graph(str(p))
        # An empty graph would LOOK like "no dependencies" — the exact class
        # this ordering exists to prevent (hfm #1).
        assert "no dependencies" in str(exc.value)

    def test_a_box_with_no_via_has_no_edge(self, tmp_path):
        boxes, via = fpw.load_graph(write_graph(tmp_path, [{"name": "a", "host": "a"}]))
        assert via == {}

    def test_blank_via_is_not_an_edge(self, tmp_path):
        boxes, via = fpw.load_graph(
            write_graph(tmp_path, [{"name": "a", "host": "a", "via": "  "}]))
        assert via == {}


class TestPlan:
    def test_declarable_excludes_hops_that_are_not_fleet_boxes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MESHFORGE_POWER_SELF", "themanager")
        boxes, via = fpw.load_graph(
            write_graph(tmp_path, [{"name": "leaf", "host": "leaf", "via": "hop"}]))
        plan = fpw.build_plan(["leaf", "hop"], boxes, via, probe=ALL_DARK)
        assert plan["order"] == ["leaf", "hop"]
        assert plan["declarable"] == ["leaf"]
        assert plan["hops_only"] == ["hop"]

    def test_a_hop_gets_no_posture_entry(self, tmp_path, monkeypatch):
        """Declaring a box nothing watches would be a claim with no consumer."""
        monkeypatch.setenv("MESHFORGE_POWER_SELF", "themanager")
        boxes, via = fpw.load_graph(
            write_graph(tmp_path, [{"name": "leaf", "host": "leaf", "via": "hop"}]))
        plan = fpw.build_plan(["hop", "leaf"], boxes, via, probe=ALL_DARK)
        assert "hop" not in plan["declarable"]


class TestPowerMethod:
    """`--method reboot` is the DRILL path. A box not on a UPS that is
    `poweroff`d stays halted until someone physically power-cycles it — mains
    returning does not help, because it never lost power. So an unattended
    drill must be able to exercise everything except the final verb."""

    def test_poweroff_is_the_default_verb(self):
        assert fpw.power_command("poweroff") == "sudo -n systemctl poweroff --no-block"

    def test_reboot_builds_the_drill_verb(self):
        assert fpw.power_command("reboot") == "sudo -n systemctl reboot --no-block"

    def test_the_two_methods_are_different_commands(self):
        assert fpw.power_command("reboot") != fpw.power_command("poweroff")

    def test_no_block_is_always_present(self):
        # Without --no-block the ssh call hangs until the box dies, and the
        # caller cannot tell "issued" from "never returned".
        for m in fpw.METHODS:
            assert "--no-block" in fpw.power_command(m)

    def test_halt_is_refused(self):
        # `halt` stops the OS but leaves the board drawing power — the worst of
        # both: unreachable AND still consuming the battery it was meant to save.
        with pytest.raises(fpw.Refusal):
            fpw.power_command("halt")

    def test_an_empty_method_is_refused(self):
        with pytest.raises(fpw.Refusal):
            fpw.power_command("")


class TestWatchAndClear:
    """`resume`'s core, shared with reboot-mode chaining so both paths clear
    posture the same way."""

    def _posture(self, tmp_path, names):
        import time as _t
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from utils import fleet_posture as fp
        now = _t.time()
        doc = {"boxes": {n: {"state": "dormant", "since": fp.fmt_ts(now),
                             "until": fp.fmt_ts(now + 3600)} for n in names},
               "declared_at": fp.fmt_ts(now), "declared_by": "operator", "posture": "t"}
        f = tmp_path / "posture.json"
        f.write_text(json.dumps(doc), encoding="utf-8")
        return str(f)

    def test_a_returning_box_is_cleared_immediately(self, tmp_path, monkeypatch):
        path = self._posture(tmp_path, ["b1"])
        monkeypatch.setattr(fpw, "reachable", lambda n: True)
        assert fpw.watch_and_clear(path, ["b1"], wait=5) == 0
        assert json.loads(Path(path).read_text())["boxes"] == {}

    def test_a_box_that_never_returns_keeps_its_declaration(self, tmp_path, monkeypatch):
        """Clearing a box that has not come back would turn a real outage into a
        DOWN page with no declaration behind it."""
        path = self._posture(tmp_path, ["b1"])
        monkeypatch.setattr(fpw, "reachable", lambda n: False)
        assert fpw.watch_and_clear(path, ["b1"], wait=1, poll_s=0) == 1
        assert "b1" in json.loads(Path(path).read_text())["boxes"]

    def test_partial_return_clears_only_what_came_back(self, tmp_path, monkeypatch):
        path = self._posture(tmp_path, ["b1", "b2"])
        monkeypatch.setattr(fpw, "reachable", lambda n: n == "b1")
        assert fpw.watch_and_clear(path, ["b1", "b2"], wait=1, poll_s=0) == 1
        boxes = json.loads(Path(path).read_text())["boxes"]
        assert "b1" not in boxes and "b2" in boxes


class TestDownArgSurface:
    """Parsed through the REAL parser (fpw.build_parser). A second copy of the
    arg surface in a test drifts from the shipped one silently — the same
    two-consumers-one-artifact rule the executor's own docstring cites."""

    def _parse(self, argv):
        return fpw.build_parser().parse_args(argv)

    def test_reboot_chains_into_resume_by_default(self):
        ns = self._parse(["down", "b", "--method", "reboot"])
        assert ns.method == "reboot" and ns.no_resume is False

    def test_no_resume_opts_out(self):
        assert self._parse(["down", "b", "--method", "reboot", "--no-resume"]).no_resume is True

    def test_poweroff_is_the_default_method(self):
        assert self._parse(["down", "b"]).method == "poweroff"

    def test_dry_run_is_the_default(self):
        assert self._parse(["down", "b"]).apply is False

    def test_halt_is_rejected_at_the_arg_layer_too(self):
        with pytest.raises(SystemExit):
            self._parse(["down", "b", "--method", "halt"])
