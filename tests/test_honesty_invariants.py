"""Honest-by-construction invariants — step 3 of the honest dev-env arc.

> Born 2026-06-15 from the operator's concern: *"AI is convincing me things
> are good and that's not true… I need a more honest reliable dev env."*

Each invariant here converts a 1000-hour blind-spot lesson from passive docs
(``.claude/rules/honest_failure_modes.md``, ``persistent_issues.md``) into a
BUILD FAILURE. Passive knowledge does not prevent misalignment; only
enforcement at the moment of action does (the arc's ordering principle).

DISCIPLINE — load-bearing, do NOT relax: every invariant ships with BOTH

  * a GREEN test — the repo holds the invariant right now, AND
  * a RED test — a deliberately-seeded violation is *actually caught*.

A guard that cannot be shown to fail is a vacuous false guard — worse than
none, and the exact defect this arc exists to kill. So each invariant is a
pure checker function exercised against synthetic violations (red) and the
real tree (green). Green tests also guard against the "absence of evidence"
false-pass (honest_failure_modes #2): if the checker's input set comes back
empty because a path moved, the test FAILS rather than vacuously passing.

Three families (arc spec §3a/§3b/§3c):
  3A  false-green displays  — an operator-facing bounded metric never exceeds
      its logical max under adversarial input (the #74 confirmation_rate=1.64
      cross-population lie).
  3B  wiring               — every watchdog SIGNAL_CLASSES member is reachable
      from a probe actually CALLED in run_all_probes (the synth-soak gap: a
      probe existed but its call was never wired in).
  3C  user-access (MF018)  — a TUI handler that exists on disk stays
      REGISTERED and reachable (the NomadNet-inaccessible class: a handler file
      forgotten in the hand-maintained get_all_handlers() list is dead UI).

Why no blanket name-based metric scan (a candidate §3a encoding): many
``*rate*``/``*ratio*``/``*pct*`` fields are legitimately unbounded —
``bitrate``, ``heart_rate``, ``size_compression_ratio``, ``rate_per_min``. A
guard that bounded all of them to 1.0/100 would itself false-fail on healthy
values — a false guard, the very defect this arc kills. "Cross-population vs
within-population" is a *semantic* property (bridge_health's
``confirmed/total_sent`` is honest because it only tracks confirmable LXMF
sends; DeliveryCounters' old ``confirmed/sent`` was a lie because ``sent`` was
mesh-dominated) — not decidable from a field name. So §3a pins the actual
computation under adversarial input instead of grepping names.
"""
from __future__ import annotations

import ast
import glob
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"


# ═════════════════════════════════════════════════════════════════════
# 3A — false-green / bounded operator-facing displays
# ═════════════════════════════════════════════════════════════════════

def _is_bounded(value, lo, hi):
    """A displayed metric is honest iff it is the no-data sentinel (None)
    or falls within its logical range. 0.0 is NOT a safe default for a rate
    — "no data" must never read as "0% = total failure" (#74)."""
    if value is None:
        return True
    return lo <= value <= hi


class TestConfirmationDisplayBounded:
    """§3a — the gateway's operator-facing confirmation displays must stay
    within their logical range even on the exact mesh-heavy traffic shape
    that produced the #74 ">164% confirmed" lie. Pins the computation, not
    the field name (see module docstring for why a name scan is rejected)."""

    def _adversarial_inputs(self):
        """A grid of raw-counter shapes, each a (label, totals, by_proto,
        drops) tuple. Includes the literal #74 shape: confirmed exists only
        for RNS, but sends are dominated by fire-and-forget mesh that can
        NEVER confirm — the cross-population denominator trap."""
        from gateway.delivery_counters import DeliveryState, DELIVERY_FAILURE_REASONS
        C = DeliveryState.CONFIRMED.value
        S = DeliveryState.SENT.value
        a_fail = sorted(DELIVERY_FAILURE_REASONS)[0]
        return [
            # The #74 shape: 164 RNS confirms, 1000 unconfirmable mesh sends.
            # Old confirmed/sent = 164/1164... or, the observed inversion,
            # confirmed/rns_sent with mesh excluded gave 1.64. Either way the
            # honest view must stay in [0,1].
            ("issue74_mesh_heavy",
             {C: 164},
             {C: {"rns": 164}, S: {"meshtastic": 1000, "rns": 100}},
             {}),
            # All confirmed, zero failures → exactly 1.0, never above.
            ("all_confirmed", {C: 50}, {C: {"rns": 50}, S: {"rns": 50}}, {}),
            # Heavy failures → rate biases DOWN (the safe direction).
            ("heavy_failures", {C: 5}, {C: {"rns": 5}, S: {"rns": 5}},
             {a_fail: 95}),
            # Zero traffic → None (no-data sentinel, NOT 0.0).
            ("zero_traffic", {}, {}, {}),
            # Confirmed with no sent record (counter skew) — still bounded.
            ("confirmed_no_sent", {C: 10}, {C: {"rns": 10}}, {}),
            # Absurd over-count of confirmed vs a tiny failure set.
            ("confirmed_dominates", {C: 100000}, {C: {"rns": 100000}},
             {a_fail: 1}),
        ]

    def test_confirmation_rate_bounded_or_none(self):
        from gateway.delivery_counters import compute_confirmation_view
        for label, totals, by_proto, drops in self._adversarial_inputs():
            view = compute_confirmation_view(totals, by_proto, drops)
            rate = view["confirmation_rate"]
            assert _is_bounded(rate, 0.0, 1.0), (
                f"confirmation_rate={rate!r} out of [0,1] on input {label!r} — "
                f"a false-green operator display (the #74 class)")

    def test_issue74_shape_surfaces_the_blind_spot(self):
        """The honest view must not just be bounded — it must SURFACE the
        unconfirmable mesh population as its own field, never average it into
        a healthy-looking scalar (honest_failure_modes #2)."""
        from gateway.delivery_counters import (
            compute_confirmation_view, DeliveryState)
        C, S = DeliveryState.CONFIRMED.value, DeliveryState.SENT.value
        view = compute_confirmation_view(
            {C: 164}, {C: {"rns": 164}, S: {"meshtastic": 1000, "rns": 100}}, {})
        assert view["unconfirmable_sent"] == 1000, (
            "mesh sends with no confirmation mechanism must be visible, "
            "not folded into confirmation_rate")
        assert view["confirmable_protocols"] == ["rns"]

    def test_red_old_cross_population_formula_is_caught(self):
        """RED proof — prove the bound check is NOT vacuous.

        Reconstruct the pre-2026-06-15 formula (confirmed / total_sent over the
        whole population) on the #74 input and show (a) it really exceeded 1.0
        and (b) the SAME _is_bounded gate that passes the honest view flags it.
        If this assertion ever fails, the guard above is asleep."""
        confirmed = 164
        total_sent = 100  # rns-only sent slice the old code divided by → 1.64
        old_rate = confirmed / total_sent
        assert old_rate > 1.0, "fixture no longer reproduces the #74 trap"
        assert not _is_bounded(old_rate, 0.0, 1.0), (
            "the bound check failed to flag a >100% rate — it is vacuous")
        # And the live function, given the same reality, stays honest:
        from gateway.delivery_counters import (
            compute_confirmation_view, DeliveryState)
        C, S = DeliveryState.CONFIRMED.value, DeliveryState.SENT.value
        view = compute_confirmation_view(
            {C: confirmed}, {C: {"rns": confirmed}, S: {"rns": total_sent}}, {})
        assert _is_bounded(view["confirmation_rate"], 0.0, 1.0)

    def test_bridge_health_rate_pct_bounded(self):
        """The other operator-facing confirmation display
        (DeliveryTracker.get_stats → confirmation_rate_pct) must stay in
        [0,100] or be None at zero traffic — it tracks only confirmable LXMF
        sends, so confirmed can never exceed total_sent."""
        from gateway.bridge_health import DeliveryTracker
        t = DeliveryTracker()
        # Zero traffic → None, not 0.0.
        assert t.get_stats()["confirmation_rate_pct"] is None
        # Track 3 confirmable sends, confirm 2 → 66.7%, bounded.
        for i in range(3):
            t.track_message(f"m{i}", b"\xaa" * 16)
        t.confirm_delivery("m0")
        t.confirm_delivery("m1")
        pct = t.get_stats()["confirmation_rate_pct"]
        assert _is_bounded(pct, 0.0, 100.0) and pct == pytest.approx(66.7, abs=0.1)


# ═════════════════════════════════════════════════════════════════════
# 3B — wiring: every signal class is reachable from run_all_probes
# ═════════════════════════════════════════════════════════════════════

def _parse_func_table(src: str):
    """{func_name: (set_of_cls_literals, set_of_called_names)} for every
    function in ``src``.

    A function "emits" a class C if its body (including any nested helper
    defined inside it — ast.walk descends) contains ``Signal(cls="C", ...)``.
    A function "calls" name N if its body contains a bare ``N(...)`` call.
    Names repeated across modules merge (union)."""
    tree = ast.parse(src)
    table: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        cls_lits, calls = set(), set()
        for n in ast.walk(node):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                if n.func.id == "Signal":
                    for kw in n.keywords:
                        if (kw.arg == "cls" and isinstance(kw.value, ast.Constant)
                                and isinstance(kw.value.value, str)):
                            cls_lits.add(kw.value.value)
                else:
                    calls.add(n.func.id)
        pc, pk = table.get(node.name, (set(), set()))
        table[node.name] = (pc | cls_lits, pk | calls)
    return table


def reachable_signal_classes(runner_src: str, probe_srcs, *,
                             entry: str = "run_all_probes") -> set:
    """Signal classes emitted by any probe transitively reachable from the
    probe calls inside ``entry``.

    Models the real wiring: ``run_all_probes`` calls ``probe_*`` by name; a
    probe may delegate Signal construction to a top-level helper it calls.
    Closure over the call graph so future delegation can't fool the gate.

    Raises KeyError if ``entry`` isn't found — a rename must update this test
    rather than silently reduce the reachable set to nothing (honest_failure
    #4: a detector that can't find its anchor must fail loud, not pass empty).
    """
    probe_table: dict = {}
    for s in probe_srcs:
        for name, (c, k) in _parse_func_table(s).items():
            pc, pk = probe_table.get(name, (set(), set()))
            probe_table[name] = (pc | c, pk | k)

    runner_table = _parse_func_table(runner_src)
    if entry not in runner_table:
        raise KeyError(f"entry function {entry!r} not found in runner source")
    _, entry_calls = runner_table[entry]

    reachable, seen = set(), set()
    frontier = [n for n in entry_calls if n in probe_table]
    while frontier:
        fn = frontier.pop()
        if fn in seen:
            continue
        seen.add(fn)
        emits, calls = probe_table[fn]
        reachable |= emits
        for nxt in calls:
            if nxt in probe_table and nxt not in seen:
                frontier.append(nxt)
    return reachable


class TestSignalClassWiring:
    """§3b — the synth-soak gap: a probe can exist (and be in SIGNAL_CLASSES,
    and routed in the role seeds) yet never be CALLED in run_all_probes, so it
    fires into a void. TestSeedCoversSignalClasses closes the seed half; this
    closes the runner half."""

    def _probe_sources(self):
        paths = sorted(glob.glob(str(SRC / "utils" / "watchdog_probes*.py")))
        assert len(paths) >= 4, (
            f"expected the split watchdog_probes* modules, found {paths} — "
            "path moved; the wiring gate would vacuously pass (no probes read)")
        return [Path(p).read_text() for p in paths]

    def test_every_signal_class_reachable_in_run_all_probes(self):
        from utils.watchdog_probe_core import SIGNAL_CLASSES
        runner_src = (SRC / "utils" / "watchdog_runner.py").read_text()
        reachable = reachable_signal_classes(runner_src, self._probe_sources())
        # Non-vacuity: the closure must actually have found classes.
        assert len(reachable) >= 20, (
            f"only {len(reachable)} classes reachable — checker likely broke")
        missing = set(SIGNAL_CLASSES) - reachable
        assert not missing, (
            f"SIGNAL_CLASSES member(s) {sorted(missing)} are emitted by NO "
            f"probe called in run_all_probes — the probe exists but its call "
            f"was never wired in (the synth_soak gap). Add the probe call to "
            f"watchdog_runner.run_all_probes.")

    def test_red_unwired_class_is_detected(self):
        """RED proof — a probe whose call is omitted from the runner is NOT
        reachable. If this passed, the gate would miss the synth-soak gap."""
        runner = (
            "def run_all_probes():\n"
            "    signals = []\n"
            "    sig = probe_alpha()\n"
            "    # probe_beta() deliberately NOT called here\n"
            "    return signals\n")
        probes = (
            'def probe_alpha():\n'
            '    return Signal(cls="alpha_class", subject="x",'
            ' severity="info", detail="")\n'
            'def probe_beta():\n'
            '    return Signal(cls="beta_class", subject="x",'
            ' severity="info", detail="")\n')
        reachable = reachable_signal_classes(runner, [probes])
        assert reachable == {"alpha_class"}
        assert "beta_class" not in reachable  # the seeded wiring gap is caught

    def test_red_closure_follows_helper_delegation(self):
        """A probe that delegates Signal construction to a top-level helper it
        calls is still covered — so the gate won't FALSE-fail on a legit
        refactor (honest_failure: a guard that fires on healthy code is also a
        false guard)."""
        runner = "def run_all_probes():\n    sig = probe_gamma()\n"
        probes = (
            'def _emit_gamma():\n'
            '    return Signal(cls="gamma_class", subject="x",'
            ' severity="info", detail="")\n'
            'def probe_gamma():\n'
            '    return _emit_gamma()\n')
        assert "gamma_class" in reachable_signal_classes(runner, [probes])

    def test_red_missing_entry_fails_loud(self):
        """A renamed/removed entry function raises rather than passing empty."""
        with pytest.raises(KeyError):
            reachable_signal_classes("def something_else():\n    pass\n", [])


# ═════════════════════════════════════════════════════════════════════
# 3C — user-access (MF018): an existing handler stays registered/reachable
# ═════════════════════════════════════════════════════════════════════

def _class_str_attr(cls_node: ast.ClassDef, name: str):
    """Return the string value of a class-level ``name = "..."`` (or
    annotated) assignment, else None. Handlers declare handler_id/menu_section
    as class-level string literals."""
    for stmt in cls_node.body:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if (isinstance(t, ast.Name) and t.id == name
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str)):
                    return stmt.value.value
        elif (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
              and stmt.target.id == name and isinstance(stmt.value, ast.Constant)
              and isinstance(stmt.value.value, str)):
            return stmt.value.value
    return None


def discover_handler_classes(handlers_dir: Path) -> dict:
    """{class_name: module_basename} for every CONCRETE TUI handler defined on
    disk — a class with non-empty ``handler_id`` AND ``menu_section`` class
    literals (the structural handler signature; base/abstract classes and
    pure-helper modules lack both).

    Skips ``__init__.py`` and ``_``-prefixed modules — by convention those are
    private split/helper modules (e.g. ``_nomadnet_service_ops.py``), never
    standalone registrable handlers."""
    discovered: dict = {}
    for path in sorted(glob.glob(str(handlers_dir / "*.py"))):
        base = os.path.basename(path)
        if base == "__init__.py" or base.startswith("_"):
            continue
        tree = ast.parse(Path(path).read_text(), path)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if (_class_str_attr(node, "handler_id")
                    and _class_str_attr(node, "menu_section")):
                discovered[node.name] = base
    return discovered


def unregistered_handlers(discovered: dict, registered: set, allowed: set) -> dict:
    """Handlers that exist on disk but are NOT returned by get_all_handlers()
    (and aren't on the explicit allowlist) — dead, unreachable UI."""
    return {n: m for n, m in discovered.items()
            if n not in registered and n not in allowed}


class TestHandlerReachability:
    """§3c (MF018) — "an entry point that exists stays reachable." A handler
    file with a registrable class that someone forgot to add to the
    hand-maintained get_all_handlers() import list is silently dead UI — the
    NomadNet-inaccessible class. test_all_handlers_protocol.py tests everything
    get_all_handlers RETURNS; only this test catches what it OMITS."""

    # Deliberately-unregistered handlers go here WITH a reason, never silently.
    ALLOWED_UNREGISTERED: set = set()

    def _registered_names(self):
        from handlers import get_all_handlers
        return {cls.__name__ for cls in get_all_handlers()}

    def test_every_handler_on_disk_is_registered(self):
        handlers_dir = SRC / "launcher_tui" / "handlers"
        discovered = discover_handler_classes(handlers_dir)
        # Non-vacuity guard (honest_failure #2): a moved path / broken filter
        # that discovered nothing must FAIL, not pass empty.
        assert len(discovered) >= 50, (
            f"only {len(discovered)} handler classes discovered under "
            f"{handlers_dir} — discovery likely broke; refusing to pass vacuously")
        missing = unregistered_handlers(
            discovered, self._registered_names(), self.ALLOWED_UNREGISTERED)
        assert not missing, (
            f"handler class(es) exist on disk but are NOT in get_all_handlers() "
            f"→ silently unreachable UI (MF018): {missing}. Add the import+append "
            f"in handlers/__init__.py, or add to ALLOWED_UNREGISTERED with a reason.")

    def test_red_forgotten_handler_is_detected(self):
        """RED proof — a discovered handler absent from the registered set is
        flagged. If this passed, the gate would miss a forgotten handler."""
        discovered = {"RealHandler": "real.py", "GhostHandler": "ghost.py"}
        registered = {"RealHandler"}
        missing = unregistered_handlers(discovered, registered, set())
        assert missing == {"GhostHandler": "ghost.py"}

    def test_red_allowlist_suppresses_with_reason(self):
        """An explicit allowlist entry suppresses — so an intentional
        exclusion is documented, never a silent gap."""
        discovered = {"DeliberatelyHidden": "hidden.py"}
        missing = unregistered_handlers(
            discovered, set(), {"DeliberatelyHidden"})
        assert missing == {}
