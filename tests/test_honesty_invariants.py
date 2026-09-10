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


# ═════════════════════════════════════════════════════════════════════
# Step 4 — no false operator-facing instructions (dead AREDN sysinfo URL)
# ═════════════════════════════════════════════════════════════════════

# AREDN 4.x retired the old node API path: a GET to
# ``:8080/cgi-bin/sysinfo.json`` now answers HTTP 307 → ``:8080/a/sysinfo``
# (verified live against WH6GXZ-6-VOLCANO-QTH-HAP, 2026-06-15). The map
# collector + the AREDNClient were moved to ``/a/sysinfo`` back in 243d8a9
# (2026-04-24), but stale operator-FACING references survived — the in-app
# knowledge base and a TUI troubleshooting hint told the operator to curl the
# dead path. A scripted check of that path gets a 307, not JSON: the app was
# instructing the user toward a dead end (MF018 — "never make the user leave
# the app to discover the truth"; the false-instruction skin of a false-green).
_DEAD_AREDN_PATH = "cgi-bin/sysinfo.json"


def _src_files_referencing(needle: str):
    hits = {}
    for path in glob.glob(str(SRC / "**" / "*.py"), recursive=True):
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        n = text.count(needle)
        if n:
            hits[os.path.relpath(path, SRC)] = n
    return hits


class TestNoDeadArednSysinfoUrl:
    """§step4 — no source string points the operator at AREDN's retired
    ``/cgi-bin/sysinfo.json`` (307 → ``/a/sysinfo``). Keeps the stale URL
    from creeping back into KB text / TUI hints / collectors."""

    # Intentional references (e.g. a migration note) go here WITH a reason.
    ALLOWED: set = set()

    def test_no_dead_aredn_url_in_src(self):
        hits = {f: n for f, n in _src_files_referencing(_DEAD_AREDN_PATH).items()
                if f not in self.ALLOWED}
        assert not hits, (
            f"deprecated AREDN path {_DEAD_AREDN_PATH!r} (HTTP 307 → /a/sysinfo) "
            f"still referenced in: {hits}. Point the operator at "
            f"'/a/sysinfo' (add '?hosts=1' for the node/host list) — instructing "
            f"them toward a redirecting/dead endpoint is a false instruction (MF018).")

    def test_red_checker_detects_the_dead_path(self):
        """RED proof — the substring scanner really flags the dead path. If
        this passed, the green test could vacuously pass on a broken scanner."""
        sample = "see http://node:8080/cgi-bin/sysinfo.json for details"
        assert sample.count(_DEAD_AREDN_PATH) == 1
        assert "ok /a/sysinfo only".count(_DEAD_AREDN_PATH) == 0


# ═════════════════════════════════════════════════════════════════════
# Step 4b — the synth soak must publish its result file ATOMICALLY
# ═════════════════════════════════════════════════════════════════════

# 2026-06-15: the step-1 synth_soak_degraded probe false-fired on a HEALTHY
# moc run (pass_envelope=true, 600/600). Root cause: lab_synth_soak_fire.sh
# truncated the PUBLISHED result file at run START (`>"$out"`), but the ~67 s
# synth process only writes its JSON at the end — so for ~67 s the newest
# synth-*.json was empty/partial → unparseable, and the run (~67 s) outlasts
# the probe's ~60 s torn-write debounce. A reader must NEVER see a partial
# newest envelope: publish via temp-file + atomic rename (honest_failure #8).
# This is the inverse of a false-green — a false-RED that pages the operator
# hourly and erodes trust in the very honesty layer this arc builds.


def synth_publishes_atomically(script_text: str) -> bool:
    """True iff the script publishes its result file via an atomic rename
    (mv/os.replace into "$out") and does NOT truncate the published "$out" in
    place with a direct redirect. Pure predicate so the red proof can feed
    synthetic script text.

    Comment-only lines are stripped before the redirect check — a real
    redirect is never on a comment line, and documentation that *mentions*
    the dead `>"$out"` pattern (e.g. explaining why we avoid it) must not
    trip the guard. (A guard that false-fires on a comment is itself the
    false-positive this arc kills — found live writing this fix.)"""
    import re
    code = "\n".join(ln for ln in script_text.splitlines()
                     if not ln.lstrip().startswith("#"))
    bare_redirect = '>"$out"' in code or '> "$out"' in code
    atomic_publish = bool(re.search(r'\bmv\b[^\n]*"\$out"', code)) \
        or 'os.replace' in code
    return atomic_publish and not bare_redirect


class TestSynthSoakAtomicWrite:
    """§step4b — lab_synth_soak_fire.sh must publish atomically so the
    synth_soak_degraded probe can't false-fire on a healthy in-progress run."""

    _SCRIPT = REPO / "scripts" / "lab_synth_soak_fire.sh"

    def test_fire_script_publishes_atomically(self):
        assert self._SCRIPT.exists(), f"{self._SCRIPT} moved — guard would vacuously pass"
        text = self._SCRIPT.read_text()
        assert synth_publishes_atomically(text), (
            "lab_synth_soak_fire.sh must write its envelope to a temp file and "
            "atomically rename it into \"$out\" — a direct `>\"$out\"` redirect "
            "truncates the published file at run start, so a reader sees a "
            "partial newest envelope for the whole run and synth_soak_degraded "
            "false-fires (the 2026-06-15 moc incident).")

    def test_red_bare_redirect_is_rejected(self):
        """RED proof — a script that redirects the synth run straight into the
        published file is flagged as non-atomic."""
        bad = 'python3 -m lab.synth ... >"$out" 2>>"$log"\nrc=$?\n'
        assert not synth_publishes_atomically(bad)

    def test_red_atomic_pattern_is_accepted(self):
        """A temp-then-mv script passes — so the guard won't false-fail a
        correct fix."""
        good = ('python3 -m lab.synth ... >"$tmp" 2>>"$log"\nrc=$?\n'
                'mv -f "$tmp" "$out"\n')
        assert synth_publishes_atomically(good)


class TestSynthSoakEmitsVerdict:
    """The synth soak must emit a first-class cron_verdict so its RESULT
    surfaces on /fleet/slo. The always-0 service exit keeps systemd green
    (observability-not-red); the VERDICT line — not the exit — carries the
    soak outcome. A systemd-timer organ's verdict is unwired, so the fleet
    snapshot's stale-gate (2026-06-27) keeps it while fresh."""

    _SCRIPT = REPO / "scripts" / "lab_synth_soak_fire.sh"

    def test_fire_script_emits_synth_soak_verdict(self):
        assert self._SCRIPT.exists(), f"{self._SCRIPT} moved — guard would vacuously pass"
        text = self._SCRIPT.read_text()
        # Writer half of the reader/writer pair (honest_failure_modes #4): the
        # soak must call cron_verdict.sh with the synth_soak name, else the
        # result never reaches /fleet/slo and the wiring is silently dead.
        assert "cron_verdict.sh" in text and "synth_soak" in text, (
            "lab_synth_soak_fire.sh must emit a cron_verdict for synth_soak so "
            "the soak result is greppable and shows on /fleet/slo.")

    def test_verdict_reflects_envelope_pass_fail(self):
        """The verdict must derive from the envelope and distinguish pass (OK) /
        degraded (CONCERN) / no-envelope (FAIL) — a single hard-coded status
        would hide a real delivery regression (honest_failure_modes #1)."""
        text = self._SCRIPT.read_text()
        assert "pass_envelope" in text, "verdict must derive from pass_envelope"
        for status in ("OK", "CONCERN", "FAIL"):
            assert status in text, f"verdict missing the {status} branch"


# ═════════════════════════════════════════════════════════════════════
# §3b-ii — every long-lived MeshForge-code daemon has a deploy-restart hook
# ═════════════════════════════════════════════════════════════════════

# Issue #79: nothing restarted the mini USER daemon after a `git pull`
# (fleet_sync/update.sh restarted only the SYSTEM units), so the daemon sat on
# OLD code until a hand-restart — a silent deploy gap. The invariant: every
# long-lived daemon whose code lives in THIS repo (so a `git pull` of
# /opt/meshforge changes it) must be restarted by a deploy script (update.sh
# and/or fleet_sync.sh) after a pull.
#
# Why a CURATED set, not a glob over templates/systemd/ (the spec's sanctioned
# escape hatch): the daemon set spans THREE definition sites — templates/systemd/
# (mini, echo, silence-watch, watchdog), contrib/systemd/ (gateway, *.service.in),
# and inline heredocs in install_noc.sh (map). A glob over one dir would miss
# gateway+map entirely (false-pass) AND would sweep in external-code wrappers
# (nomadnet/meshchatx/rnsd/meshtasticd/nats run binaries a /opt/meshforge pull
# does NOT change → restarting them on a MeshForge pull is wrong, and rnsd
# restart is explicitly dangerous). "Runs MeshForge code from this repo" is a
# semantic property of the ExecStart, not decidable from a filename — so it is
# curated, each entry carrying its verified ExecStart as provenance.
#
# Each entry was verified 2026-06-15 against the unit's ExecStart (see comment).
MESHFORGE_CODE_DAEMONS: dict = {
    # unit base name → (ExecStart provenance, where its restart is wired)
    "meshforge-gateway":   "src/gateway/bridge_cli.py (contrib/systemd/*.in)",
    "meshforge-map":       "utils.map_data_service (inline heredoc install_noc.sh)",
    "meshforge-watchdog":  "utils.watchdog_runner (templates/systemd/)",
    "meshforge-mini-dudeai":      "mini_dudeai engine (the #79 unit) (templates/systemd/)",
    "meshforge-mini-dudeai-claw": "dude-claw sibling, claw-brain box only (templates/systemd/)",
    "meshforge-echo":      "lab.lxmf_echo responder (templates/systemd/ meshforge-echo-user.service)",
    "nomadnet-silence-watch": "scripts/nomadnet_silence_watch.py (templates/systemd/)",
}

# Type=simple/forking daemons MeshForge installs that a /opt/meshforge pull does
# NOT change (they exec an EXTERNAL binary) — restarting them on a MeshForge code
# pull is wrong, so they are correctly absent from the deploy-restart path.
RESTART_EXEMPT_DAEMONS: dict = {
    "nomadnet-user":   "tmux-wraps the external `nomadnet` app; pull doesn't change it",
    "meshchatx-user":  "wraps the external meshchatx app; pull doesn't change it",
    "rnsd-user":       "runs pip-installed (forked) rnsd, not repo code; restart is "
                       "explicitly dangerous (RNS rapid-cycle @rns race, #69)",
    "meshtasticd-native": "external meshtasticd binary (upstream)",
    "meshtasticd-alt":    "external meshtasticd binary (TUI-deployed secondary radio)",
    "nats-server":     "external nats-server binary (dude-claw infra)",
}

_RESTART_KEYWORDS = ("try-restart", "restart", "sync_repo", "sync_local_unit",
                     "sync_user_unit", "sync_local_user_unit")


def deploy_restarted_units(*script_texts: str) -> set:
    """Set of systemd unit base names that a deploy script restarts after a
    pull. A unit is "restart-wired" if its base name appears (as a whole token,
    not a hyphen-prefix of a longer name) on a non-comment line that also
    contains a restart verb — covering both direct `systemctl try-restart
    <unit>.service` (update.sh) and the unit-name-as-arg wrappers
    `sync_repo/sync_local_unit/sync_user_unit <unit>` (fleet_sync.sh).

    Pure over the passed text so the red proof can feed synthetic scripts.
    The token guard (negative-lookahead on `[\\w-]`) stops `meshforge-mini-dudeai`
    from spuriously matching inside `meshforge-mini-dudeai-claw`."""
    import re
    found = set()
    candidates = set(MESHFORGE_CODE_DAEMONS) | set(RESTART_EXEMPT_DAEMONS)
    for text in script_texts:
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("#"):
                continue
            if not any(kw in line for kw in _RESTART_KEYWORDS):
                continue
            for unit in candidates:
                if re.search(re.escape(unit) + r"(?![\w-])", line):
                    found.add(unit)
    return found


class TestDeployRestartHook:
    """§3b-ii — the #79 deploy gap: a long-lived daemon running THIS repo's code
    must be restarted by a deploy script after a pull, or it silently serves
    stale code. update.sh + fleet_sync.sh are the two pull-deploy paths."""

    def _deploy_sources(self):
        scripts = REPO / "scripts"
        update = scripts / "update.sh"
        fleet = scripts / "fleet_sync.sh"
        for p in (update, fleet):
            assert p.exists(), f"{p} moved — the deploy-restart guard would vacuously pass"
        return update.read_text(), fleet.read_text()

    def test_every_code_daemon_is_deploy_restarted(self):
        restarted = deploy_restarted_units(*self._deploy_sources())
        # Non-vacuity: the parser must actually have found the wired units.
        assert len(restarted) >= 5, (
            f"only {len(restarted)} restart-wired units found — parser likely "
            f"broke against the real scripts; refusing to pass vacuously")
        missing = set(MESHFORGE_CODE_DAEMONS) - restarted
        assert not missing, (
            f"long-lived MeshForge-code daemon(s) {sorted(missing)} are NOT "
            f"restarted by update.sh or fleet_sync.sh after a pull → they serve "
            f"OLD code until a hand-restart (the #79 deploy gap). Wire a "
            f"try-restart into update.sh's user-unit block and/or a "
            f"sync_repo/sync_*_unit call in fleet_sync.sh. "
            f"Provenance: {[MESHFORGE_CODE_DAEMONS[m] for m in sorted(missing)]}")

    def test_exempt_daemons_are_genuinely_external(self):
        """The exempt set must stay disjoint from the must-restart set — a unit
        can't be both 'runs repo code' and 'external'. Guards a careless edit
        that double-lists a daemon (which would let a real gap hide as exempt)."""
        overlap = set(MESHFORGE_CODE_DAEMONS) & set(RESTART_EXEMPT_DAEMONS)
        assert not overlap, f"daemon(s) {overlap} listed as BOTH code-daemon and exempt"

    def test_red_unrestarted_daemon_is_detected(self):
        """RED proof — a code-daemon whose name appears on NO restart line is
        flagged. If this passed, the gate would miss the #79 gap."""
        update = "run_user_systemctl try-restart meshforge-mini-dudeai.service\n"
        fleet = ("sync_repo meshforge-gateway /opt/meshforge meshforge-gateway\n"
                 "sync_local_unit meshforge-map /opt/meshforge\n")
        restarted = deploy_restarted_units(update, fleet)
        assert "meshforge-mini-dudeai" in restarted
        assert "meshforge-gateway" in restarted
        assert "meshforge-map" in restarted
        # echo runs repo code but is on no restart line here → the seeded gap:
        assert "meshforge-echo" not in restarted

    def test_red_hyphen_prefix_does_not_false_match(self):
        """`meshforge-mini-dudeai` must NOT be considered restarted just because
        `meshforge-mini-dudeai-claw` is on a restart line — else a real gap on
        the bare unit would hide behind its longer sibling."""
        only_claw = "run_user_systemctl try-restart meshforge-mini-dudeai-claw.service\n"
        restarted = deploy_restarted_units(only_claw)
        assert "meshforge-mini-dudeai-claw" in restarted
        assert "meshforge-mini-dudeai" not in restarted

    def test_red_comment_line_is_not_a_wiring(self):
        """A restart verb inside a comment must NOT count as wiring (the same
        comment-stripping discipline as the synth-soak checker)."""
        commented = "# TODO: restart meshforge-echo.service someday\n"
        assert "meshforge-echo" not in deploy_restarted_units(commented)


# ═════════════════════════════════════════════════════════════════════
# §3b-i — every templates/systemd/ unit has documented, verified provenance
# ═════════════════════════════════════════════════════════════════════

# The arc spec asks: "every MeshForge-OWNED systemd unit has a templates/systemd/
# entry" + a REVERSE check that "every template maps to an install site (catch
# dead templates)." Recon (2026-06-15) found the literal bijection is FALSE on a
# healthy repo — MeshForge's systemd units are materialized SEVEN different ways:
#   * shell installer copy/sed (install_noc/nomadnet/meshchatx/lab_traffic)
#   * update.sh's `*-user.service` glob
#   * a TUI handler at runtime (meshtasticd-alt ← dual_radio_failover.py)
#   * manager-box organs hand-enabled on the federator box (backup/ci-status/fleet-health)
#   * a hand-deployed fleet daemon with no committed installer (watchdog)
#   * ops band-aids + claw infra hand-deployed (meshtasticd-restart, nats-server)
#   * inline heredocs (meshforge.service, rnsd.service, meshforge-map.service —
#     these are owned-but-INLINE, deliberately NOT templated)
# A guard asserting template↔shell-install bijection would be RED on ~half the
# templates (all the hand/runtime-deployed ones), forcing a giant allowlist that
# turns it vacuous — the exact false-guard this arc kills. The arc spec sanctioned
# the honest alternative: "a curated OWNED_UNITS tuple WITH a provenance comment."
#
# So the invariant is REFRAMED to what's true and valuable: every template FILE
# carries a curated, verified provenance entry, and every entry's file exists
# (the bijection — disk↔manifest, not disk↔one-installer). This catches the real
# mistakes: a NEW template added without anyone deciding/documenting how it gets
# deployed (a template nobody installs is as dead as a daemon nobody restarts),
# and a deleted/renamed template the manifest still claims. For the machine-
# checkable kinds (installer/glob/tui) the cited deploy reference is VERIFIED too,
# so the provenance can't silently rot. Each entry verified 2026-06-15 against the
# real install/enable call-site (kinds: installer→script names it; glob→update.sh
# *-user.service loop; tui→handler writes it; hand→hand-deployed, documented-only).
TEMPLATE_PROVENANCE: dict = {
    # ── shell-installer-consumed (verified: the named script references it) ──
    "meshchatx-user.service":            ("installer", "install_meshchatx.sh"),
    "nomadnet-user.service":             ("installer", "install_nomadnet.sh"),
    "rnsd-user.service":                 ("installer", "install_noc.sh"),
    "meshtasticd-native.service":        ("installer", "install_noc.sh"),
    "meshforge-echo-user.service":       ("installer", "install_lab_traffic.sh"),
    "meshforge-tracer-user.service":     ("installer", "install_lab_traffic.sh"),
    "meshforge-tracer-user.timer":       ("installer", "install_lab_traffic.sh"),
    "meshforge-mini-dudeai.service":       ("installer", "update.sh"),
    "meshforge-mini-dudeai-dream.service": ("installer", "update.sh"),
    "meshforge-mini-dudeai-dream.timer":   ("installer", "update.sh"),
    "meshforge-mini-dudeai-claw.service":  ("installer", "update.sh"),
    # templated 2nd claw (dudeclaw-02): update.sh lands the @.service template
    # on every box; the operator enables @claw02 per the claw runbook, and
    # fleet_sync.sh + update.sh try-restart the instance on code change.
    "meshforge-mini-dudeai-claw@.service": ("installer", "update.sh"),
    "meshtasticd-mudp-guard.service":      ("installer", "update.sh"),
    "meshtasticd-mudp-guard.timer":        ("installer", "update.sh"),
    # ── update.sh `*-user.service` glob (verified: filename ends -user.service) ──
    "meshforge-synth-soak-user.service":   ("glob", "update.sh *-user.service loop → synth-soak.service"),
    "meshforge-propagation-soak-user.service": ("glob", "update.sh *-user.service loop → propagation-soak.service (oneshot; inert until the timer is hand-deployed on a gateway box with rns.propagation_node set)"),
    "meshforge-gateway-resource-canary-user.service": ("glob", "update.sh *-user.service loop → gateway-resource-canary.service (inert everywhere until the timer is hand-deployed on a gateway box; oneshot, never enabled/started by the glob)"),
    "meshforge-lab-rollup-user.service":   ("glob", "update.sh *-user.service loop → lab-rollup.service"),
    "moc-drain-snapshot-user.service":     ("glob", "update.sh *-user.service loop → moc-drain-snapshot.service"),
    "nomadnet-silence-watch-user.service": ("glob", "update.sh *-user.service loop → nomadnet-silence-watch.service"),
    # ── TUI-handler-deployed at runtime (verified: handler references it) ──
    "meshtasticd-alt.service":           ("tui", "launcher_tui/handlers/dual_radio_failover.py"),
    # ── hand-deployed; documented-only (no committed installer by design) ──
    "meshforge-watchdog.service":  ("hand", "fleet daemon, hand-enabled; no committed "
                                            "installer; fleet_sync.sh restarts it (#3b-ii)"),
    "meshforge-backup.service":    ("hand", "manager-box organ (the federator box), hand-enabled; fleet backup (c111f7a)"),
    "meshforge-backup.timer":      ("hand", "manager-box organ (the federator box), hand-enabled; fleet backup (c111f7a)"),
    "meshforge-ci-status.service": ("hand", "manager-box organ (the federator box); ecosystem CI cron (6e0f21f)"),
    "meshforge-ci-status.timer":   ("hand", "manager-box organ (the federator box); ecosystem CI cron (6e0f21f)"),
    "meshforge-dep-advisory.service": ("hand", "manager-box organ (the federator box); "
                                        "installed + enabled by hand on the manager box 2026-09-04. "
                                        "ssh's the fleet and asks GitHub's advisory DB about the "
                                        "versions the SERVICE python actually has. Manager-only "
                                        "by design: it needs the fleet host list and gh auth, and "
                                        "one asker is enough for a shared external API."),
    "meshforge-dep-advisory.timer":   ("hand", "manager-box organ (the federator box); "
                                        "installed + enabled by hand on the manager box 2026-09-04, "
                                        "daily 09:30 + Persistent=true. SuccessExitStatus=1 so the "
                                        "unit's health tracks OBSERVATION (exit 2 = fleet not "
                                        "assessable -> fails) not the news (exit 1 = advisories "
                                        "found -> success; the finding is the ADVISORY file)."),
    "meshforge-dep-range.service":    ("hand", "manager-box organ (the federator box); "
                                        "installed + enabled by hand on the manager box 2026-09-05. "
                                        "Repo-side twin of dep-advisory: that one asks what the fleet "
                                        "has INSTALLED, this asks whether the manifests still DECLARE "
                                        "a range permitting anything advisory-free. Audits BOTH repos "
                                        "(--root /opt/meshforge --root /opt/meshanchor), so there is "
                                        "no MeshAnchor copy to drift. Deliberately NOT a second "
                                        "ExecStart on dep-advisory: Type=oneshot aborts the chain at "
                                        "the first failure, so an unreachable FLEET (that job's exit "
                                        "2) would silently stop this repo-side check from running."),
    "meshforge-dep-range.timer":      ("hand", "manager-box organ (the federator box); "
                                        "installed + enabled by hand on the manager box 2026-09-05, "
                                        "daily 10:15 + Persistent=true, offset from dep-advisory's "
                                        "09:30 so the two do not contend for the advisory API. "
                                        "SuccessExitStatus=1 for the same reason as its sibling: the "
                                        "unit's health tracks the OBSERVATION (exit 2 = the check "
                                        "could not be made -> fails) not the news (exit 1 = "
                                        "unpatchable ranges found -> success; the finding is the "
                                        "RANGE-FINDING file). Both polarities drilled through "
                                        "systemd-run 2026-09-05."),
    "meshforge-fleet-health.service": ("hand", "manager-box organ (the federator box); fleet runtime health sweep (28c15a3)"),
    "meshforge-fleet-health.timer":   ("hand", "manager-box organ (the federator box); fleet runtime health sweep (28c15a3)"),
    "lxmd.service":               ("hand", "single-box organ (moc1), hand-deployed 2026-07-20; LXMF "
                                            "propagation node / store-and-forward — deliberately ONE "
                                            "box, not a fleet glob, so a second node can't split the "
                                            "message store; install steps in templates/lxmd/config.example "
                                            "(.claude/plans/propagation_leg.md)"),
    "meshtasticd-restart.service": ("hand", "ops band-aid, hand-deployed; weekly meshtasticd restart, upstream VSZ leak (15ea6e4)"),
    "meshtasticd-restart.timer":   ("hand", "ops band-aid, hand-deployed; weekly meshtasticd restart, upstream VSZ leak (15ea6e4)"),
    "nats-server.service":         ("hand", "dude-claw NATS infra, claw-brain box only, hand-deployed (a171fec)"),
    "meshforge-synth-soak-user.timer":   ("hand", "hand-deployed (live on moc); NOT copied by update.sh's "
                                                  "*-user.service glob — known timer-deploy gap"),
    "meshforge-propagation-soak-user.timer": ("hand", "hand-deployed on a GATEWAY box that has adopted a "
                                                  "propagation node (moc/moc3 — the LXMF store-and-forward drill; needs "
                                                  "loginctl enable-linger); NOT copied by update.sh's *-user.service "
                                                  "glob — known timer-deploy gap"),
    "meshforge-gateway-resource-canary-user.timer": ("hand", "hand-deployed on a GATEWAY box only (moc/moc3 — "
                                                  "gateway-reliability arc A1 resource canary; RF-bearing, so opt-in per "
                                                  "box); NOT copied by update.sh's *-user.service glob — timer-deploy gap"),
    "meshforge-lab-rollup-user.timer":   ("hand", "hand-deployed; NOT copied by update.sh glob — known timer-deploy gap"),
    "moc-drain-snapshot-user.timer":     ("hand", "hand-deployed; NOT copied by update.sh glob — known timer-deploy gap"),
}

# Drop-in directories (override fragments, not standalone units) — hand-deployed
# on the fleet per their issue history; documented so a NEW drop-in dir must also
# be accounted for rather than appearing silently.
DROPIN_PROVENANCE: dict = {
    "meshforge-map.service.d": "hand-deployed map start-pre wait-for-rnsd drop-in (cb61d3b)",
    "rnsd.service.d":          "hand-deployed RNS-fork drop-ins: 10-stop-timeout, 20-exit-on-host-loss (#68/#69)",
}


def _template_files(template_dir: Path) -> set:
    return {p.name for p in template_dir.iterdir()
            if p.is_file() and p.suffix in (".service", ".timer")}


def _template_dirs(template_dir: Path) -> set:
    return {p.name for p in template_dir.iterdir() if p.is_dir()}


def undocumented_templates(disk_files: set, manifest_keys: set) -> set:
    """Template files on disk with NO provenance entry — a unit someone added
    without deciding/documenting how it gets deployed (a dead-template risk)."""
    return disk_files - manifest_keys


def dangling_manifest_entries(manifest_keys: set, disk_files: set) -> set:
    """Provenance entries whose template file no longer exists — a rename/delete
    that left the manifest claiming a unit that's gone."""
    return manifest_keys - disk_files


def broken_provenance(manifest: dict, repo: Path) -> dict:
    """{template: reason} for machine-checkable entries whose cited deploy
    reference can't be confirmed — so a provenance string can't silently rot.
      * installer → the named script must exist AND contain the template basename
      * glob      → the filename must actually end in -user.service (else the
                    *-user.service loop never matches it)
      * tui       → the named handler must exist AND reference the basename
      * hand      → documented-only; just requires a specific (non-stub) detail
    """
    broken: dict = {}
    for name, (kind, detail) in manifest.items():
        if kind == "installer":
            script = repo / "scripts" / detail
            if not script.exists():
                broken[name] = f"cited installer {detail} not found"
            elif name not in script.read_text():
                broken[name] = f"{detail} does not reference {name}"
        elif kind == "glob":
            if not name.endswith("-user.service"):
                broken[name] = "tagged glob but name is not *-user.service"
        elif kind == "tui":
            handler = repo / "src" / detail
            if not handler.exists():
                broken[name] = f"cited handler {detail} not found"
            elif name.replace(".service", "") not in handler.read_text():
                broken[name] = f"{detail} does not reference {name}"
        elif kind == "hand":
            if len(detail) < 20:
                broken[name] = "hand-deployed entry lacks a specific provenance note"
        else:
            broken[name] = f"unknown provenance kind {kind!r}"
    return broken


class TestTemplateProvenance:
    """§3b-i — every templates/systemd/ unit has curated, verified provenance.
    Reframed from a (false-on-healthy-repo) template↔installer bijection to a
    disk↔manifest bijection; see the module comment block above for why."""

    _TDIR = SRC.parent / "templates" / "systemd"

    def test_no_undocumented_template(self):
        assert self._TDIR.is_dir(), f"{self._TDIR} moved — guard would vacuously pass"
        disk = _template_files(self._TDIR)
        # Non-vacuity (honest_failure #2): a moved dir / broken filter that found
        # nothing must FAIL, not pass empty.
        assert len(disk) >= 25, (
            f"only {len(disk)} template files found under {self._TDIR} — "
            f"discovery likely broke; refusing to pass vacuously")
        missing = undocumented_templates(disk, set(TEMPLATE_PROVENANCE))
        assert not missing, (
            f"template(s) {sorted(missing)} have NO provenance entry in "
            f"TEMPLATE_PROVENANCE → an undocumented unit nobody decided how to "
            f"deploy (dead-template risk). Add an entry recording how it is "
            f"installed/enabled (installer/glob/tui/hand), or delete the template.")

    def test_no_dangling_manifest_entry(self):
        disk = _template_files(self._TDIR)
        dangling = dangling_manifest_entries(set(TEMPLATE_PROVENANCE), disk)
        assert not dangling, (
            f"TEMPLATE_PROVENANCE claims template(s) {sorted(dangling)} that no "
            f"longer exist on disk — a rename/delete left the manifest stale.")

    def test_provenance_references_resolve(self):
        broken = broken_provenance(TEMPLATE_PROVENANCE, SRC.parent)
        assert not broken, (
            f"provenance entr(ies) cite a deploy reference that can't be "
            f"confirmed: {broken}. Fix the citation (the install site moved/"
            f"renamed) or re-classify the entry.")

    def test_dropin_dirs_documented(self):
        disk_dirs = _template_dirs(self._TDIR)
        missing = disk_dirs - set(DROPIN_PROVENANCE)
        assert not missing, (
            f"drop-in dir(s) {sorted(missing)} under templates/systemd/ have no "
            f"DROPIN_PROVENANCE entry — document how/where they are deployed.")
        # And no stale dropin entry:
        assert not (set(DROPIN_PROVENANCE) - disk_dirs), (
            "DROPIN_PROVENANCE references a drop-in dir that no longer exists")

    def test_red_undocumented_template_caught(self):
        """RED proof — a disk file absent from the manifest is flagged."""
        disk = {"known.service", "ghost.service"}
        manifest = {"known.service"}
        assert undocumented_templates(disk, manifest) == {"ghost.service"}

    def test_red_dangling_entry_caught(self):
        """RED proof — a manifest key with no disk file is flagged."""
        assert dangling_manifest_entries({"gone.service"}, set()) == {"gone.service"}

    def test_red_installer_not_naming_template_caught(self, tmp_path):
        """RED proof — an installer entry whose script does NOT contain the
        template basename is flagged (catches a renamed template the installer
        no longer copies — the sharpest deploy-rot bug)."""
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "fake_install.sh").write_text("echo nothing here\n")
        m = {"renamed.service": ("installer", "fake_install.sh")}
        broken = broken_provenance(m, tmp_path)
        assert "renamed.service" in broken

    def test_red_glob_entry_must_be_user_service(self, tmp_path):
        """RED proof — a `glob` entry not ending in -user.service is flagged
        (update.sh's loop would never copy it)."""
        m = {"wrong.service": ("glob", "claims the *-user.service loop")}
        assert "wrong.service" in broken_provenance(m, tmp_path)


# ═════════════════════════════════════════════════════════════════════
# §3c — "service active ≠ doing the job": each long-lived daemon's OUTPUT
#        is asserted by a probe (or external verifier), never just is-active
# ═════════════════════════════════════════════════════════════════════

# The arc spec reframes §3c (the hardest, "expect the first cut wrong") from one
# clever guard into a COVERAGE test: every long-lived MeshForge-code daemon must
# have something that asserts its USER-FACING OUTPUT (a fresh timestamp, a
# /healthz response, a delivery counter advancing, a written envelope) — NOT
# merely `systemctl is-active`. The "verify the work-holder" lesson: a process
# can be active while producing nothing.
#
# Verified 2026-06-15 against the probe modules + run_all_probes + honest_status.sh.
# CORE = fleet-wide MeshForge-code daemons, each mapped to its OUTPUT mechanism:
#   * probe    → a probe_* function, asserted below to be CALLED in run_all_probes
#                AND to read output (not a process-state check).
#   * external → an out-of-process verifier (used for the watchdog, whose own
#                liveness a self-probe CANNOT catch: a wedged loop never runs the
#                probe — circular. honest_status.sh checks its ts freshness).
DAEMON_OUTPUT_COVERAGE: dict = {
    "meshforge-gateway":     ("probe", ("probe_delivery_write_canary",
                                        "probe_queue_backlog",
                                        "probe_delivery_confirmation_stall")),
    "meshforge-map":         ("probe", ("probe_http_local",)),
    "meshforge-mini-dudeai": ("probe", ("probe_history_write_failure",)),
    "rnsd":                  ("probe", ("probe_rns_rpc_responsive",
                                        "probe_rns_shared_instance_responsive")),
    "meshforge-watchdog":    ("external", "honest_status.sh watchdog ts-freshness (WD_STALE_S)"),
}

# Probes that check ONLY process-state (is-active / pgrep) — these must NEVER be
# offered as a §3c "output" mechanism. Listing one here is the trap the spec
# warns against; the guard rejects it.
PROCESS_STATE_PROBES: set = {"probe_service_inactive"}

# AUX = single-box / transitively-covered daemons. Documented (not hard-gated by
# a probe-wiring assertion) because forcing a dedicated probe for a niche
# single-box daemon would be low-value churn — but each MUST carry an honest
# coverage note so the gap is visible, never silent.
AUX_DAEMON_COVERAGE: dict = {
    "meshforge-echo": "transitive — probe_tracer_peer_unreachable: a dead echo "
                      "responder surfaces as tracer no-route to that box",
    "meshforge-mini-dudeai-claw": "claw-brain box only; runs the same mini engine; "
                      "no claw-specific output probe yet — a documented single-box gap",
    "nomadnet-silence-watch": "is itself an observability organ (watches nomadnet "
                      "MQTT silence); its output is alerts, not metered by a meta-probe",
}


def probe_calls_reachable_from(runner_src: str, entry: str = "run_all_probes") -> set:
    """All function names CALLED transitively from ``entry`` in the runner — a
    coverage probe is 'wired' iff its name is in this set. BFS over the runner's
    own call graph (run_all_probes may delegate to helpers it defines), reusing
    _parse_func_table. Raises KeyError if ``entry`` is gone (fail loud, not empty
    — honest_failure #4)."""
    table = _parse_func_table(runner_src)
    if entry not in table:
        raise KeyError(f"entry {entry!r} not found in runner source")
    reachable_calls, seen, frontier = set(), set(), [entry]
    while frontier:
        fn = frontier.pop()
        if fn in seen:
            continue
        seen.add(fn)
        _, calls = table.get(fn, (set(), set()))
        reachable_calls |= calls
        for c in calls:
            if c in table and c not in seen:
                frontier.append(c)
    return reachable_calls


class TestDaemonOutputCoverage:
    """§3c — every long-lived MeshForge-code daemon has an OUTPUT-asserting
    coverage mechanism, not just an is-active check. A coverage test (per the
    spec's reframe), not one clever guard."""

    def _runner_src(self):
        p = SRC / "utils" / "watchdog_runner.py"
        assert p.exists(), f"{p} moved — coverage guard would vacuously pass"
        return p.read_text()

    def _probe_defined_names(self):
        """All probe_* function names DEFINED across the split probe modules."""
        names = set()
        for path in sorted(glob.glob(str(SRC / "utils" / "watchdog_probes*.py"))):
            for n in _parse_func_table(Path(path).read_text()):
                if n.startswith("probe_"):
                    names.add(n)
        assert len(names) >= 15, (
            f"only {len(names)} probe_* defs found — discovery broke; not passing vacuously")
        return names

    def test_core_daemon_probes_are_wired_output_checks(self):
        called = probe_calls_reachable_from(self._runner_src())
        assert len(called) >= 20, (
            f"only {len(called)} calls reachable from run_all_probes — parser "
            f"likely broke; refusing to pass vacuously")
        defined = self._probe_defined_names()
        for daemon, (kind, mech) in DAEMON_OUTPUT_COVERAGE.items():
            if kind != "probe":
                continue
            for probe in mech:
                assert probe not in PROCESS_STATE_PROBES, (
                    f"{daemon}: {probe} is a process-state (is-active) check — "
                    f"NOT an output assertion. §3c forbids it as coverage.")
                assert probe in defined, (
                    f"{daemon}: coverage probe {probe} is not defined in any "
                    f"watchdog_probes* module (renamed/removed?).")
                assert probe in called, (
                    f"{daemon}: output probe {probe} exists but is NOT called in "
                    f"run_all_probes → it can't actually watch the daemon. Wire "
                    f"the probe call into watchdog_runner.run_all_probes.")

    def test_watchdog_external_freshness_mechanism_exists(self):
        """The watchdog's output-liveness is asserted EXTERNALLY (a self-probe
        would be circular). honest_status.sh must carry the ts-freshness gate."""
        hs = (REPO / "scripts" / "honest_status.sh")
        assert hs.exists(), "honest_status.sh moved — watchdog §3c coverage unverifiable"
        text = hs.read_text()
        assert "WD_STALE_S" in text and "stale" in text, (
            "honest_status.sh lost the watchdog ts-freshness gate (WD_STALE_S) — "
            "a stale-but-valid watchdog.json would read 'clean' again (the §3c "
            "false-green for the watchdog daemon itself).")

    def test_coverage_set_matches_code_daemon_inventory(self):
        """Closed-set linkage (honest_failure #7): §3c's covered daemons must be
        EXACTLY the §3b-ii MeshForge-code daemons (+ rnsd, the substrate). Add a
        new code daemon → this FAILS until its output coverage is classified, so
        a daemon can never be deployed+restarted yet silently un-output-watched."""
        covered = set(DAEMON_OUTPUT_COVERAGE) | set(AUX_DAEMON_COVERAGE)
        expected = set(MESHFORGE_CODE_DAEMONS) | {"rnsd"}
        assert covered == expected, (
            f"§3c coverage set drifted from the code-daemon inventory. "
            f"Only in §3c: {sorted(covered - expected)}; "
            f"missing from §3c (classify their output coverage): "
            f"{sorted(expected - covered)}")

    def test_aux_entries_carry_an_honest_note(self):
        for daemon, note in AUX_DAEMON_COVERAGE.items():
            assert len(note) >= 30, (
                f"{daemon}: aux coverage note too thin — state HOW its output is "
                f"(or is not) watched, so the gap stays visible.")

    def test_red_unwired_probe_detected(self):
        """RED proof — a probe named as coverage but NOT called in the runner is
        caught. If this passed, §3c would bless a daemon whose probe never runs."""
        runner = "def run_all_probes():\n    sig = probe_real()\n    return []\n"
        called = probe_calls_reachable_from(runner)
        assert "probe_real" in called
        assert "probe_ghost" not in called  # the seeded un-wired coverage probe

    def test_red_isactive_mechanism_is_rejected(self):
        """RED proof — the §3c trap: offering a process-state probe as 'output'
        coverage must be detectable. The bound check below mirrors the loop in
        test_core_daemon_probes_are_wired_output_checks."""
        bad_mech = ("probe_service_inactive",)
        assert any(p in PROCESS_STATE_PROBES for p in bad_mech), (
            "the is-active sentinel set failed to flag a process-state probe — "
            "the §3c 'active ≠ doing the job' guard would be defeated")

    def test_red_missing_entry_fails_loud(self):
        """A renamed run_all_probes raises rather than passing an empty call set."""
        with pytest.raises(KeyError):
            probe_calls_reachable_from("def other():\n    pass\n")


# --- installer discoverability (2026-09-09, first-hour audit) ----------------
# The README's first instruction to a newcomer is `sudo bash
# scripts/install_noc.sh`. Before this date `--help` returned "Unknown option:
# --help" and exit 1 — the universal reflex failed on a 2,088-line root script.
# The five real options were documented in docs/install.md all along; what was
# missing is that the SCRIPT could not tell you at the terminal.
#
# These two invariants keep it that way, and keep usage() from drifting away
# from the parser it describes (honest_failure_modes #5 — two consumers of one
# list must be pinned together or they WILL drift).

_INSTALL_NOC = REPO / "scripts" / "install_noc.sh"


def _install_noc_text() -> str:
    return _INSTALL_NOC.read_text(encoding="utf-8")


def test_install_noc_help_needs_no_root_and_exits_zero():
    """A newcomer must be able to ask what a root script will do WITHOUT
    running it as root. Both spellings, exit 0, and it must not fall through to
    the root check or the mutation path."""
    import subprocess
    for flag in ("-h", "--help"):
        p = subprocess.run(["bash", str(_INSTALL_NOC), flag],
                           capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL)
        assert p.returncode == 0, f"{flag} exited {p.returncode}: {p.stderr[:200]}"
        assert "Usage:" in p.stdout, f"{flag} printed no usage"
        assert "must be run as root" not in p.stdout + p.stderr, \
            f"{flag} fell through to the root check"


def test_install_noc_usage_lists_every_option_the_parser_accepts():
    """usage() and the arg parser are two consumers of one list. A new option
    added to the `case` without a usage line makes the help quietly wrong —
    which is how the script came to have five undiscoverable options."""
    import re
    text = _install_noc_text()
    body = text.split("# Parse arguments", 1)[1]
    parsed = set(re.findall(r"^\s{8}(--[a-z][a-z0-9-]*)\)", body, re.M))
    parsed |= set(re.findall(r"^\s{8}-h\|(--[a-z][a-z0-9-]*)\)", body, re.M))
    assert parsed, "found no options in the parser — the regex needs updating"
    usage_block = text.split("usage() {", 1)[1].split("USAGE\n}", 1)[0]
    missing = sorted(o for o in parsed if o not in usage_block)
    assert not missing, f"parser accepts {missing} but usage() never mentions them"


# --- installer dry-run (2026-09-09) -----------------------------------------
# The dry-run's safety rests on THREE layers, and each has a pin below. The
# design constraint that drove it: a half-working dry-run that mutates anyway is
# FAR more dangerous than no dry-run — it would be an instrument claiming a
# safety it does not have.

_INSTALL_LIB = REPO / "scripts" / "lib" / "install_common.sh"


def test_dry_run_must_not_require_root():
    """LAYER 2, the load-bearing one. Shadowing cannot cover shell redirections
    or heredocs (they are not commands), so UNPRIVILEGED execution is what stops
    anything the shadow list misses — the kernel refuses it. If a future edit
    makes --dry-run require root, every gap in the shadow list silently converts
    from a loud abort into a real mutation. Pin it at the source."""
    text = (REPO / "scripts" / "install_noc.sh").read_text(encoding="utf-8")
    assert 'if [[ "$DRY_RUN" != "true" && $EUID -ne 0 ]]; then' in text, \
        "the root check must exempt --dry-run; see install_common.sh dry-run notes"


def test_dry_run_refuses_root():
    """LAYER 2's OTHER half, missing until 2026-09-09. The root check exempts
    --dry-run; nothing REFUSED root, so `sudo bash scripts/install_noc.sh
    --dry-run` — the spelling the usage text's own `Usage: sudo bash ...` line
    invites — ran the preview as uid 0 and dropped the load-bearing layer:
    every heredoc, `>` redirection to a hardcoded /etc path, udevadm call and
    the APT signing key would then mutate FOR REAL under a trap that printed
    "0 performed".

    Pinned two ways, because the string alone would survive an inverted
    comparison: the guard must EXIST, it must be evaluated BEFORE anything that
    mutates as root (mf_log_init creates /var/log/meshforge), and its real
    condition text — lifted out of the file — must actually fire for root +
    dry-run and stay quiet for the three other combinations.
    """
    import subprocess
    text = (REPO / "scripts" / "install_noc.sh").read_text(encoding="utf-8")
    guard = 'if [[ "$DRY_RUN" == "true" && $EUID -eq 0 ]]; then'
    assert guard in text, "--dry-run as root must be REFUSED, not merely un-required"
    assert text.index(guard) < text.index("mf_log_init"), \
        "the refusal must come before mf_log_init, which mkdirs /var/log/meshforge as root"
    assert text.index(guard) < text.index("mf_dry_run_enable"), \
        "the refusal must come before the shadows are installed"

    # DRILL the condition itself against all four combinations.
    cond = guard.replace("$EUID", "$FAKE_EUID")
    for dry, euid, expect in (("true", "0", "REFUSE"), ("true", "1000", "ok"),
                              ("false", "0", "ok"), ("false", "1000", "ok")):
        p = subprocess.run(
            ["bash", "-c", f'DRY_RUN={dry}; FAKE_EUID={euid}\n{cond}\n'
                           f'  echo REFUSE\nelse\n  echo ok\nfi'],
            capture_output=True, text=True, timeout=30)
        assert p.stdout.strip() == expect, \
            f"guard misfires for DRY_RUN={dry} EUID={euid}: {p.stdout!r}"


def _run_installer_dry_run(home, *extra, scrub_user=False):
    """Run the real installer preview with a SCRATCH HOME.

    The scratch HOME is not hygiene, it is the regression pin for the defect
    that made it necessary: `git config` was classified as a READ, so
    mf_git_sync's `git config --global --add safe.directory "$dir"` executed for
    real on every preview and appended a dead sandbox path to the operator's
    ~/.gitconfig — 35 of them on this box, one per suite run per fleet box,
    because `--add` never dedups. A test that shells out to a root installer
    must not be able to write to the operator's dotfiles at all.

    $USER is PINNED for the same reason the HOME is: it was ambient, so the
    verdict of every test below was a property of whoever launched pytest
    rather than of the code. Interactive shells export USER; cron does not, so
    on 2026-09-10 the 04:45 calibration_reverify cron got five aborts on a head
    the suite had passed hours earlier. A test whose verdict depends on
    un-pinned machine state pins nothing.

    `scrub_user=True` removes both login vars instead, which is the cron shape
    itself — that is a DIFFERENT assertion (the installer must resolve the
    operator from the real uid) and it has its own test below.
    """
    import subprocess, os, pwd
    env = dict(os.environ, HOME=str(home))
    env.pop("MF_DRY_RUN", None)
    # Exactly one login var either way, so the child's view of "who am I" is
    # decided here and not by the launcher. The pinned value comes from the
    # real uid, not from getpass/$USER, because those read the same ambient
    # env this pin exists to remove.
    env.pop("SUDO_USER", None)
    if scrub_user:
        env.pop("USER", None)
    else:
        env["USER"] = pwd.getpwuid(os.getuid()).pw_name
    return subprocess.run(
        ["bash", str(REPO / "scripts" / "install_noc.sh"), "--dry-run", *extra],
        capture_output=True, text=True, timeout=240,
        stdin=subprocess.DEVNULL, cwd=str(REPO), env=env)


def test_dry_run_completes_and_performs_nothing(tmp_path):
    """LAYER 3 + the end-to-end claim. A dry-run that aborts is a FINDING (an
    uncovered path); one that completes must say so and must report zero
    operations performed."""
    p = _run_installer_dry_run(tmp_path, "--client-only")
    assert p.returncode == 0, f"dry-run aborted (exit {p.returncode}): {p.stdout[-600:]}"
    assert "DRY RUN COMPLETE" in p.stdout
    assert "0 performed" in p.stdout, "the summary must state that nothing was done"
    assert "[dry-run] would:" in p.stdout, "a preview that previews nothing is not a preview"
    assert "must be run as root" not in p.stdout, "dry-run must not demand root"
    # The operator's git config is OUTSIDE the scratch HOME; the sandbox path
    # must not have reached it (finding: `git config` classified as a read).
    assert not (tmp_path / ".gitconfig").exists(), \
        "the preview wrote to ~/.gitconfig — `git config` must be shadowed as a mutator"


def test_dry_run_leaves_nothing_behind(tmp_path):
    """The sandbox is a mktemp dir under /tmp, which is TMPFS on the fleet's
    Pis — anything left there is unreclaimable RAM. Every preview used to build
    a real ~19 MB venv inside it and never remove it (there was no cleanup, and
    `rm` is shadowed anyway): 37 orphans totalling 636 MB were found on this
    box, one per preview PLUS one per suite run, on every box."""
    import glob
    before = set(glob.glob("/tmp/meshforge-dryrun-*"))
    p = _run_installer_dry_run(tmp_path, "--client-only")
    assert p.returncode == 0, p.stdout[-500:]
    leaked = sorted(set(glob.glob("/tmp/meshforge-dryrun-*")) - before)
    assert not leaked, f"dry-run left sandbox(es) behind: {leaked}"


def test_dry_run_walks_the_real_template_branches(tmp_path):
    """The sandbox INSTALL_DIR used to stay permanently EMPTY (git clone is
    shadowed), so every `[[ -f "$INSTALL_DIR/templates/..." ]]` gate took its
    inline-fallback branch and the preview was of a program a real fresh
    install never runs — the exact contract the shadow design promises to keep.
    The template-rendered units (User= substituted, the real hardening) were
    invisible. Now the sandbox is populated from the checkout."""
    p = _run_installer_dry_run(tmp_path, "--client-only")
    assert p.returncode == 0, p.stdout[-500:]
    assert "meshforge-map.service installed (User=" in p.stdout, \
        "the preview took the inline-fallback branch — the sandbox is empty again"
    assert "Verification script not found" not in p.stdout, \
        "the sandbox is missing scripts/ — the preview is not walking real branches"


def test_dry_run_does_not_build_a_real_venv(tmp_path):
    """`python3 -m venv` is not shadowable (shadowing python3 would break every
    read made through it), so it must be gated on DRY_RUN explicitly. Second
    reason it must be gated: on a box without python3-venv, phase 2's apt
    install was only PREVIEWED, so the real venv call failed and the preview
    aborted at [6/8] blaming the box."""
    text = (REPO / "scripts" / "install_noc.sh").read_text(encoding="utf-8")
    assert 'mf_dry_note "python3 -m venv' in text, "the venv must be previewed"
    real = text.index('\n        python3 -m venv "$VENV_DIR"')
    assert 'if [[ "$DRY_RUN" == "true" ]]; then' in text[real - 800:real], \
        "the REAL python3 -m venv must sit in the else of a DRY_RUN gate"
    p = _run_installer_dry_run(tmp_path, "--client-only")
    assert p.returncode == 0, p.stdout[-500:]
    assert "would: python3 -m venv" in p.stdout, "the venv must be PREVIEWED, not built"


@pytest.mark.skipif(os.getuid() == 0,
                    reason="as root the uid fallback yields root, which "
                           "resolve_operator_user refuses BY DESIGN "
                           "(test_dry_run_refuses_root pins that side)")
def test_dry_run_resolves_the_operator_without_any_login_env(tmp_path):
    """The installer must resolve the operator from the REAL UID when neither
    $SUDO_USER nor $USER is set.

    This is the cron shape, and it is the test that would have caught the
    2026-09-10 finding. `resolve_operator_user` read `${SUDO_USER:-$USER}` and
    exited 1 when both were empty — an env var is a REPRESENTATION of who is
    running, and cron and systemd set neither, while `id -un` is the thing
    itself. Consequences, both real:

      * the 04:45 calibration_reverify cron aborted five dry-run tests on a
        head the suite had passed hours earlier and recorded a VERIFIED claim
        as "broke". Note what that was NOT: a false positive. The claim (that
        honest_status.sh exited 0) held in the env it was made in, AND the head
        really did carry this defect — only the sparse env could see it. The
        re-derivation was right to go red and wrong about what had broken,
        which is why "known_benign" was never available here;
      * a genuine non-interactive install (cron, a systemd unit, a provisioning
        runner) refused to proceed for no reason a user could act on.

    Sibling pin: the SUDO_USER-first ORDER must survive this fix, because under
    sudo the real uid is root and the operator is the invoker.
    """
    p = _run_installer_dry_run(tmp_path, "--client-only", scrub_user=True)
    assert "Cannot resolve operator user" not in p.stdout + p.stderr, (
        "the preview refused to resolve an operator with an empty login env; "
        "it must fall back to the real uid: " + (p.stdout + p.stderr)[-500:])
    assert p.returncode == 0, f"dry-run aborted (exit {p.returncode}): {p.stdout[-600:]}"
    assert "DRY RUN COMPLETE" in p.stdout


def test_resolver_prefers_sudo_user_over_the_real_uid():
    """The fix above must not reorder the resolution. Under sudo `id -un` is
    root, so a uid-first resolver would install units owned by root and drift
    from the TUI's $SUDO_USER home — the exact failure the function's original
    comment was written to prevent."""
    text = (REPO / "scripts" / "install_noc.sh").read_text(encoding="utf-8")
    body = text[text.index("resolve_operator_user() {"):]
    body = body[:body.index("\n}\n")]
    assert 'local u="${SUDO_USER:-${USER:-$uid_user}}"' in body, \
        "resolution order must stay SUDO_USER -> USER -> real uid"
    assert '"$u" == "root"' in body, "root must still be refused as the operator"


def test_no_script_resolves_the_operator_from_advisory_env_alone():
    """Two consumers of one fact, derived two ways, WILL drift (hfm #5), and
    the first version of this guard proved it by being too narrow to catch the
    drift it was written for.

    It originally read install_noc.sh ONLY. The commit that added it fixed that
    one file, quoted "grep for its copies" in its own message, and left
    `REAL_USER="${SUDO_USER:-$USER}"` standing in scripts/update.sh — the DEPLOY
    path, and the worse shape of the two: it never aborts, it writes the units
    under the invoking user's home and then prints "✓ User service templates
    deployed". A guard scoped to the file you happened to be editing tests your
    memory, not the tree.

    The bare two-level form is the defect signature. Longer chains that end in
    a real resolution (`$(id -un)`, `$(whoami)`) are fine and are why this is a
    grep for the bare form rather than for the variable names.
    """
    import re
    offenders = []
    for path in sorted((REPO / "scripts").glob("*.sh")):
        for n, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\$\{SUDO_USER:-\$USER\}", ln):
                offenders.append(f"{path.name}:{n}: {ln.strip()}")
    assert not offenders, (
        "operator login resolved from advisory env alone — $USER and $SUDO_USER "
        "are both unset under cron and systemd. End the chain in the real uid "
        "(${SUDO_USER:-${USER:-$(id -un)}}) and handle the no-operator case "
        "explicitly:\n  " + "\n  ".join(offenders))


def test_dry_run_never_prompts_at_the_ownership_question():
    """"--dry-run never prompts" was true only of the FINAL prompt. The phase-1
    ownership prompt was guarded solely by mf_have_tty, so an interactive
    preview on any box that already has meshtasticd (every fleet box) BLOCKED
    on "Select mode [1/2/3/q]" — and answering `q` exited 0, which then printed
    "DRY RUN COMPLETE" for a preview that had covered phase 1 only. The honesty
    suite could not see it: it passes --client-only (skips the block) and
    stdin=DEVNULL (irrelevant to an explicit `< /dev/tty`).

    Invariant, and it is BROADER than the finding that produced it: every
    BLOCKING `read` in the installer — `< /dev/tty` or plain stdin, prompt or
    not — must be reached only through a branch that excludes DRY_RUN. Drilling
    the broader shape immediately found a second one the finding had not named:
    ask_radio_type's whiptail menu / `read -rp "Select [1/2/3]"`.

    Reads carrying `-t <timeout>` are exempt: they cannot block. (The one in
    flush_terminal_input is a 0.1s input drain, not a question.)"""
    import re
    text = (REPO / "scripts" / "install_noc.sh").read_text(encoding="utf-8")
    reads = [m for m in re.finditer(r"^[^\n#]*\bread\b[ \t]+[^\n]*", text, re.M)
             if not re.search(r"\bread\b[^\n]*\s-t\s", m.group(0))
             and "IFS=" not in m.group(0)]
    assert reads, "the regex found no prompts — it needs updating"
    fns = [m.start() for m in re.finditer(r"^[a-z_][a-z0-9_]*\(\) \{", text, re.M)]
    for m in reads:
        pos = m.start()
        # Scope = the enclosing function if we are inside one, else the block
        # just above. A DRY_RUN check must appear in that scope before the read.
        last_fn = max((s for s in fns if s < pos), default=-1)
        last_close = text.rfind("\n}\n", 0, pos)
        scope = text[last_fn:pos] if last_fn > last_close else text[max(0, pos - 1200):pos]
        assert 'DRY_RUN' in scope, (
            f"a blocking read at offset {pos} is not excluded from "
            f"--dry-run: {m.group(0).strip()[:70]!r}")
    # ...and whiptail is a prompt too, even though it is not a `read`.
    assert 'elif [[ "$DRY_RUN" == "true" ]]; then\n        # DRY-RUN NEVER PROMPTS' in text, \
        "ask_radio_type (whiptail menu) must be skipped under --dry-run"


def _extract_mf_on_exit(text: str) -> str:
    """Lift the REAL mf_on_exit out of install_noc.sh so the drill below tests
    the handler that ships, not a paraphrase of it in the test file. (The
    older exit-handler test does paraphrase — a copy cannot regress with the
    original.)"""
    start = text.index("mf_on_exit() {")
    end = text.index("trap mf_on_exit EXIT", start)
    return text[start:end]


def test_dry_run_complete_claims_coverage_not_exit_status():
    """"DRY RUN COMPLETE" is a claim about how much of the install was
    previewed, not about the exit status. Every `exit 0` used to print it — the
    ownership prompt's `q`, the SPI reboot gate at the end of phase 4 — so a
    preview that stopped inside phase 1 announced itself as a complete preview
    of an 8-phase install, and told the reader "To install for real: sudo bash
    ..." on the strength of it."""
    text = (REPO / "scripts" / "install_noc.sh").read_text(encoding="utf-8")
    assert text.count("MF_REACHED_END=1") == 1, \
        "MF_REACHED_END must be set at exactly one place: the end of the script"
    assert text.rindex("MF_REACHED_END=1") > text.rindex('mf_phase "8/8"'), \
        "MF_REACHED_END must be set AFTER the last phase, or it certifies nothing"

    handler = _extract_mf_on_exit(text)
    early = _run_bash(f'''
        source {_INSTALL_LIB}
        DRY_RUN=true; MF_DRY_RUN=true
        MF_PHASE="Checking existing installations..."; MF_PHASE_NUM="1/8"
{handler}
        ( exit 0 ); mf_on_exit
    ''').stdout
    assert "ENDED EARLY" in early, f"an early exit 0 must not claim completeness: {early!r}"
    assert "DRY RUN COMPLETE" not in early

    done = _run_bash(f'''
        source {_INSTALL_LIB}
        DRY_RUN=true; MF_DRY_RUN=true; MF_REACHED_END=1
{handler}
        ( exit 0 ); mf_on_exit
    ''').stdout
    assert "DRY RUN COMPLETE" in done, \
        "reaching the end must still produce the completion summary"


def test_no_tty_presence_checks_remain():
    """`[[ -c /dev/tty ]]` is a PRESENCE check: the node exists in a pipeline or
    under </dev/null, and the guarded `read < /dev/tty` then fails, aborting the
    installer under `set -e`. mf_have_tty is the FUNCTION check."""
    text = (REPO / "scripts" / "install_noc.sh").read_text(encoding="utf-8")
    assert "-c /dev/tty" not in text, \
        "use mf_have_tty (can we READ it?) not a check that the node exists"


#: Path VARIABLES that --dry-run redirects into its mktemp sandbox. A
#: redirection through one of these is safe: it lands in the sandbox, which is
#: strictly better than a preview line because the user can inspect the result.
#: Anything else must route through mf_write_stdin / mf_append_line.
_DRY_RUN_SANDBOXED_VARS = ("INSTALL_DIR", "VENV_DIR", "MESHTASTICD_CONFIG_DIR")


def _raw_write_offenders(text: str):
    """Return (literal_hits, var_names) for redirections that bypass the
    dry-run. Factored out of the guard so the guard itself can be DRILLED — a
    regex nobody ever fed a violation is an assertion, not a check.

    THREE shapes, and each was found by a bug the previous version missed:
      * line continuations must be JOINED, or `printf ... \\` + `> "$VAR"`
        never starts with a command name and the guard is inert;
      * the command list must NOT be a closed vocabulary — `sed ... > /etc/...`
        and `gpg --dearmor > /etc/...` were both invisible to
        (cat|tee|printf|echo);
      * the payload must be allowed to contain `#`. The old `[^|\\n#]*?` stopped
        at the FIRST hash, so `echo "# SPI enabled by MeshForge" >>
        "$BOOT_CONFIG"` — the one surviving raw append in the file — could
        never be seen. The guard had the same blind spot as the bug.
    """
    import re
    text = re.sub(r"\\\n\s*", " ", text)          # join continuations
    text = re.sub(r"^[ \t]*#.*$", "", text, flags=re.M)  # drop comment LINES

    # Any command, not a vocabulary. `2>/dev/null`-style redirects are excluded
    # by the target-path alternation (/dev is not a system config root).
    literal = re.findall(
        r"(?:^|[;&|])[^|\n]*?[^0-9\s>]\s*>{1,2}\s*/(?:etc|usr|var|lib|boot)/\S*",
        text, re.M)
    var_writes = re.findall(
        r'(?:^|[;&|])[^|\n]*?[^0-9\s>]\s*>{1,2}\s*"?\$\{?([A-Za-z_][A-Za-z0-9_]*)',
        text, re.M)
    return literal, sorted({v for v in var_writes if v not in _DRY_RUN_SANDBOXED_VARS})


def test_no_raw_writes_bypass_the_dry_run():
    """Redirections cannot be shadowed, so every write to a system path must
    route through mf_write_stdin / mf_append_line or the dry-run aborts on it.
    """
    text = (REPO / "scripts" / "install_noc.sh").read_text(encoding="utf-8")
    literal, unsafe = _raw_write_offenders(text)
    assert not literal, f"raw system-path writes bypass the dry-run: {literal[:3]}"
    assert not unsafe, (
        f"redirection(s) through un-sandboxed path variable(s) {unsafe} bypass "
        f"the dry-run — route them through mf_write_stdin/mf_append_line, or add "
        f"the variable to _DRY_RUN_SANDBOXED_VARS if --dry-run redirects it")


def test_raw_write_guard_catches_the_shapes_that_got_past_it():
    """DRILL the guard — an instrument with only one outcome is not evidence.

    Every line below is a real bypass this guard failed to catch at some point.
    If any of them stops being flagged, the guard has gone blind again and the
    test above becomes a green light over an unpreviewed system write.
    """
    planted = [
        # The #-in-payload blind spot: the surviving raw BOOT_CONFIG append.
        'echo "# SPI enabled by MeshForge for LoRa HAT" >> "$BOOT_CONFIG"',
        # Closed-vocabulary blind spots: neither sed nor gpg was in the list.
        'sed "s/@X@/y/g" tpl > /etc/systemd/system/meshtasticd.service',
        'curl -fsSL "$u" | gpg --dearmor > /etc/apt/trusted.gpg.d/meshtastic.gpg',
        # Line-continuation blind spot.
        'printf \'a\\n\' \\\n    > "$AUTO_UPGRADES_CONF"',
        # The original shape.
        'cat > /etc/meshforge/noc.yaml <<EOF',
    ]
    for line in planted:
        literal, unsafe = _raw_write_offenders(line + "\n")
        assert literal or unsafe, f"guard is BLIND to this bypass: {line!r}"

    # ...and it must not fire on the routed forms, or it would be uninformative.
    for ok in [
        'mf_append_line "$BOOT_CONFIG" "# SPI enabled by MeshForge for LoRa HAT"',
        'sed "s/@X@/y/g" tpl | mf_write_stdin /etc/systemd/system/x.service',
        'mf_write_stdin /etc/meshforge/noc.yaml <<EOF',
        'systemctl enable meshforge-map 2>/dev/null || true',
        'apt-get install -y -qq meshtasticd >/dev/null 2>&1',
        'command cp -a "$d" "$INSTALL_DIR/" 2>/dev/null || true',
    ]:
        literal, unsafe = _raw_write_offenders(ok + "\n")
        assert not literal and not unsafe, f"guard false-fires on: {ok!r}"


def test_apt_signing_key_write_is_previewable():
    """`curl ... | gpg --dearmor > /etc/apt/trusted.gpg.d/meshtastic.gpg` was
    the worst uncovered write in the file: add_meshtastic_repo is invoked as an
    `if` CONDITION, so set -e is OFF for its body — the unprivileged EACCES was
    swallowed as a bare "Permission denied", the shadowed apt-get update
    returned 0, and the function reported SUCCESS into a branch a real run
    never takes. As root (see the dry-run root refusal) it installed a real APT
    signing key during a "preview"."""
    import re
    text = (REPO / "scripts" / "install_noc.sh").read_text(encoding="utf-8")
    assert "| mf_write_stdin /etc/apt/trusted.gpg.d/meshtastic.gpg" in text, \
        "the signing-key write must route through mf_write_stdin"
    code = re.sub(r"^[ \t]*#.*$", "", text, flags=re.M)   # comments describe it
    assert "> /etc/apt/trusted.gpg.d" not in code, \
        "a raw redirect to the APT keyring is not previewable and fails silently"


def test_dry_run_read_detection_skips_leading_flags():
    """A shadowed READ returns 0 without output, so the script takes a DIFFERENT
    BRANCH under dry-run and the preview is of a program nobody runs. `git -C
    <dir> rev-parse` and `systemctl --user is-active` are the cases that broke
    it: the subcommand is not argv[1]."""
    import subprocess
    script = f'''
source {_INSTALL_LIB}
r() {{ if _mf_is_read "$@"; then echo READ; else echo MUT; fi; }}
r git -C /x rev-parse HEAD
r git clone u d
r systemctl --user is-active f
r systemctl enable f
r sed -e s/a/b/ f
r sed -i s/a/b/ f
r apt-get update
'''
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=30)
    assert p.returncode == 0, p.stderr[:300]
    assert p.stdout.split() == ["READ", "MUT", "READ", "MUT", "READ", "MUT", "MUT"], \
        f"read/mutation classification wrong: {p.stdout.split()}"


def test_git_config_write_is_classified_as_a_mutation():
    """`git config` was in the READ list wholesale, so mf_git_sync's
    `git config --global --add safe.directory "$dir"` executed for real on
    every preview and appended a DEAD sandbox path to the operator's
    ~/.gitconfig. `--add` never dedups: 35 of them accumulated on this box,
    one per preview and one per suite run, on every fleet box that runs it.

    The query forms are still reads — shadowing `git config --get` would make
    the script take a different BRANCH under dry-run, which is the failure the
    read passthrough exists to prevent."""
    import subprocess
    script = f'''
source {_INSTALL_LIB}
r() {{ if _mf_is_read "$@"; then echo READ; else echo MUT; fi; }}
r git config --global --add safe.directory /x
r git config user.name someone
r git config --global --get user.name
r git config --list
r git config --get-regexp '^safe'
'''
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=30)
    assert p.returncode == 0, p.stderr[:300]
    assert p.stdout.split() == ["MUT", "MUT", "READ", "READ", "READ"], \
        f"git config classification wrong: {p.stdout.split()}"


def test_udevadm_control_is_classified_as_a_mutation():
    """`udevadm control --reload-rules` / `udevadm trigger` were neither
    shadowed nor guarded, unlike the ALSA sibling twenty lines above. On a
    FRESH box (no /etc/udev/rules.d/99-meshtastic.rules) they sit in an `if`
    BODY under set -e, return rc=1 "Permission denied" unprivileged, and
    aborted the preview in phase 4 — phases 4-8 were never shown. Fleet boxes
    already have the rules file and skip the block, and CI's --client-only
    skips phase 4 entirely, which is why nothing saw it."""
    import subprocess
    script = f'''
source {_INSTALL_LIB}
r() {{ if _mf_is_read "$@"; then echo READ; else echo MUT; fi; }}
r udevadm control --reload-rules
r udevadm trigger
r udevadm info -q path -n /dev/spidev0.0
'''
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=30)
    assert p.returncode == 0, p.stderr[:300]
    assert p.stdout.split() == ["MUT", "MUT", "READ"], \
        f"udevadm classification wrong: {p.stdout.split()}"
    # ...and belt-and-braces for the REAL run, where a container/chroot with no
    # udevd fails the same way: both calls carry a non-fatal guard.
    text = (REPO / "scripts" / "install_noc.sh").read_text(encoding="utf-8")
    for call in ("udevadm control --reload-rules", "udevadm trigger"):
        for line in [ln for ln in text.splitlines()
                     if ln.strip().startswith(call)]:
            assert line.rstrip().endswith("\\") or "||" in line, \
                f"ungated udevadm call aborts the run under set -e: {line.strip()!r}"


def test_env_cannot_switch_the_library_into_dry_run():
    """MF_DRY_RUN was seeded from the ENVIRONMENT at source time and gates
    mf_pip_install / mf_ensure_pip / mf_verify_import / mf_write_stdin /
    mf_append_line for FIVE other scripts (install.sh, dev_setup.sh,
    configure_gateway.sh, fix_packaging_conflict.sh, healthcheck.sh) — none of
    which has a --dry-run flag or checks the variable. An exported
    MF_DRY_RUN=true therefore turned a REAL root install into printed "would:"
    lines returning 0: nothing pip-installed, no unit files written, and green
    checkmarks all the way to a completed install that fails at first launch.

    Dry-run is an explicit opt-in through mf_dry_run_enable, and an
    environment that asks for it is told plainly that it was ignored."""
    import subprocess, os
    env = dict(os.environ, MF_DRY_RUN="true")
    p = subprocess.run(
        ["bash", "-c", f'source {_INSTALL_LIB}; echo "MF_DRY_RUN=$MF_DRY_RUN"'],
        capture_output=True, text=True, timeout=30, env=env)
    assert p.returncode == 0, p.stderr[:300]
    assert "MF_DRY_RUN=false" in p.stdout, \
        f"the environment switched the library into dry-run: {p.stdout!r}"
    assert "IGNORED" in p.stderr, \
        "silently ignoring the variable is the same defect one layer down"

    # The real consumer-of-record: a write helper must actually WRITE.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "x.conf")
        q = subprocess.run(
            ["bash", "-c", f'source {_INSTALL_LIB}; mf_append_line "{f}" real'],
            capture_output=True, text=True, timeout=30, env=env)
        assert q.returncode == 0, q.stderr[:300]
        assert os.path.exists(f) and open(f).read() == "real\n", \
            "MF_DRY_RUN in the environment turned a real write into a preview"


def test_dry_note_survives_a_redirected_call_site():
    """mf_dry_note printed to the CURRENT stdout, so a shadowed mutator at a
    call site that redirects — `systemctl enable --now unattended-upgrades
    >/dev/null 2>&1`, `apt-get install ... meshtasticd >/dev/null 2>&1` — had
    its note swallowed by the call site's own /dev/null while the shadow
    returned 0 and a green checkmark printed underneath. CI's preview reached
    that line every run and reported "unattended-upgrades service enabled"
    inside a run that changed nothing.

    The saved fd is what fixes it; this drills the exact call shape."""
    # mf_dry_run_enable must NOT be redirected here: it saves the stdout it
    # sees, exactly as install_noc.sh calls it.
    out = _run_bash(f'''
        source {_INSTALL_LIB}
        mf_dry_run_enable
        systemctl enable --now unattended-upgrades >/dev/null 2>&1
        apt-get install -y -qq meshtasticd >/dev/null 2>&1
    ''').stdout
    assert "would: systemctl enable --now unattended-upgrades" in out, \
        "a redirected call site swallowed its own preview note"
    assert "would: apt-get install -y -qq meshtasticd" in out


def test_dry_run_summary_states_no_count_it_cannot_keep():
    """The summary used to print "N operation(s) previewed". mf_dry_note runs
    in a SUBSHELL at every pipeline call site (`printf ... | mf_write_stdin`),
    so those increments never reached the summary's shell and the number
    silently under-reported the transcript printed directly above it. A tally
    that can disagree with the evidence beside it is worse than no tally."""
    out = _run_bash(f'''
        source {_INSTALL_LIB}
        mf_dry_run_enable
        printf 'x\\n' | mf_write_stdin /etc/nope.conf
        mf_dry_run_summary
    ''').stdout
    assert "DRY RUN COMPLETE" in out and "0 performed" in out
    import re
    assert not re.search(r"\d+ operation", out), \
        "a previewed-operation COUNT cannot be kept across pipeline subshells"


def test_log_flush_waits_for_the_tee_to_drain(tmp_path):
    """`exec > >(tee -a "$LOG")` was never waited: bash exits before tee drains,
    so the EXIT trap's failure report / dry-run summary — the LAST thing
    written and the whole point of the trap — lands after the caller's prompt,
    interleaved with it, or is missed entirely by a wrapper that reads until
    the child exits.

    After mf_log_flush returns, tee has EXITED, so the transcript is complete
    by construction rather than by luck."""
    text = _INSTALL_LIB.read_text(encoding="utf-8")
    assert "MF_TEE_PID=$!" in text, "the tee's pid must be recorded to be waited on"
    verdict = tmp_path / "verdict.txt"
    out = _run_bash(f'''
        export HOME="{tmp_path}"
        source {_INSTALL_LIB}
        mf_log_init
        # A saved fd on a FILE (not the tee pipe), so it cannot itself hold the
        # pipe open — the verdict has to be written after stdout/stderr close.
        exec 9>"{verdict}"
        echo "TRAP-REPORT-MARKER"
        mf_log_flush
        grep -c "TRAP-REPORT-MARKER" "$MF_INSTALL_LOG" >&9
    ''', timeout=30)
    assert out.returncode == 0, (out.stdout[-400:], out.stderr[-400:])
    assert verdict.read_text().strip() == "1", \
        f"the transcript was not drained before flush returned: {verdict.read_text()!r}"


def test_installer_exit_handler_flushes_last(tmp_path):
    """mf_log_flush closes stdout, so it must be the LAST statement of the
    handler — and MF_DRY_FD (mf_dry_note's saved stdout) is a duplicate of the
    tee's input end, so it must be closed too or the wait DEADLOCKS every
    dry-run on its final line."""
    text = (REPO / "scripts" / "install_noc.sh").read_text(encoding="utf-8")
    handler = _extract_mf_on_exit(text)
    body = handler.rstrip().rstrip("}").rstrip()
    assert body.rstrip().endswith("mf_log_flush"), \
        "mf_log_flush must be the last statement of mf_on_exit (it closes stdout)"
    lib = _INSTALL_LIB.read_text(encoding="utf-8")
    assert "MF_DRY_FD" in lib.split("mf_log_flush() {", 1)[1].split("\n}", 1)[0], \
        "mf_log_flush must close MF_DRY_FD too, or the wait never returns"


# --- installer failure legibility (2026-09-09, the `set -e` gap) -------------
# install_noc.sh runs `set -e` across 8 phases of real mutation. An unhandled
# failure in phase 5 used to simply KILL the script — whatever the failing
# command printed, then nothing: no phase, no log pointer, no recovery path, on
# a box that was now partially configured.
#
# It does NOT roll back, on purpose: undoing an apt install or systemd unit
# automatically can remove something the user already depended on, and claiming
# transactional safety we do not have is the defect class these invariants
# exist to stop. The contract is legibility, and these pin it.

def _run_bash(script: str, timeout: int = 60):
    import subprocess
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=timeout)


def test_every_phase_goes_through_mf_phase():
    """The trap can only name the failing phase if every phase sets it. A raw
    `echo "[N/8] ..."` would print but leave MF_PHASE stale, so the report would
    confidently name the WRONG phase — worse than naming none."""
    import re
    text = (REPO / "scripts" / "install_noc.sh").read_text(encoding="utf-8")
    raw = re.findall(r'echo -e "\$\{CYAN\}\[\d/8\]', text)
    assert not raw, f"{len(raw)} phase marker(s) bypass mf_phase and leave MF_PHASE stale"
    assert len(re.findall(r'mf_phase "\d/8"', text)) >= 8


def test_failure_report_names_phase_log_and_recovery():
    """A failure report that omits any of these leaves the user guessing."""
    out = _run_bash(f'''
        source {_INSTALL_LIB}
        MF_PHASE="Installing Reticulum (RNS)..."; MF_PHASE_NUM="5/8"
        MF_INSTALL_LOG="/var/log/meshforge/install-TEST.log"
        mf_install_failed_report 1
    ''').stdout
    assert "INSTALL FAILED" in out
    assert "[5/8] Installing Reticulum" in out, "must name the phase that failed"
    assert "install-TEST.log" in out, "must point at the transcript"
    assert "Re-run the same command" in out, "must state the recovery path"
    assert "PARTIALLY CONFIGURED" in out, "must not imply a clean rollback"
    assert "HONEST LIMIT" in out, "must not overclaim that re-run always recovers"


def test_failure_report_says_so_when_there_is_no_transcript():
    """An unwritable log dir must not leave the report pointing at an empty
    path — absence stated, not implied (honest_failure_modes #2)."""
    out = _run_bash(f'''
        source {_INSTALL_LIB}
        MF_INSTALL_LOG=""
        mf_install_failed_report 3
    ''').stdout
    assert "no transcript was written" in out


def test_exit_handler_branches_on_mode():
    """One EXIT trap serves both modes; a dry-run abort is a COVERAGE finding
    and a real failure is a partial install. Mixing them would tell a dry-run
    user their box was modified when nothing was."""
    dry = _run_bash(f'''
        source {_INSTALL_LIB}
        DRY_RUN=true; MF_PHASE="Configuring NOC mode..."; MF_PHASE_NUM="7/8"
        mf_on_exit() {{ local rc=1
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "DRY RUN ABORTED (exit $rc)"; echo "NOTHING was changed."
                echo "Failed during: [${{MF_PHASE_NUM}}] ${{MF_PHASE}}"
            else mf_install_failed_report "$rc"; fi; }}
        mf_on_exit
    ''').stdout
    assert "NOTHING was changed" in dry and "[7/8]" in dry
    assert "PARTIALLY CONFIGURED" not in dry, \
        "a dry-run abort must never claim the box was modified"


def test_dry_run_summary_prints_exactly_once(tmp_path):
    """Regression pin. The summary was called BOTH by the EXIT trap and by an
    explicit call at the end of the script, so a clean dry-run printed it twice.
    Found by the drill, not by reading.

    Runs through the shared helper: this test used to shell out with the raw
    inherited environment and no scratch HOME, so it was outside the
    ~/.gitconfig protection its siblings have, and its verdict rode on the
    launcher's $USER like theirs did.
    """
    p = _run_installer_dry_run(tmp_path, "--client-only")
    assert p.returncode == 0, p.stdout[-400:]
    assert p.stdout.count("DRY RUN COMPLETE") == 1, \
        f"summary printed {p.stdout.count('DRY RUN COMPLETE')} times, expected 1"


def test_installer_writers_work_on_the_REAL_path(tmp_path):
    """End-of-session double tap, 2026-09-09 — the one thing fixed that day and
    never exercised live.

    20 installer writes were converted to mf_write_stdin / mf_append_line, and
    EVERY test of them ran under --dry-run, which returns early and never
    touches `command cat > "$1"`. Had that branch been broken, every REAL
    install would have failed while all 20 dry-run checks passed — the exact
    shape of the day's other four defects (a green check aimed at the wrong
    path). It was correct; it just had no coverage. Now it does.

    Two properties could have bitten silently:
      * TRUNCATION — these replaced `cat > f`, so a second write must REPLACE,
        not append. An appending helper would corrupt every rewritten unit file.
      * LITERALNESS — heredoc bodies and appended lines carry $ and backticks
        (e.g. LABEL="alsa_restore_std"); they must land verbatim, not expand.
    """
    import subprocess, textwrap
    d = tmp_path
    script = textwrap.dedent(f'''
        set -u
        source {_INSTALL_LIB}
        MF_DRY_RUN=false
        mf_write_stdin "{d}/u.service" <<'BODY'
        [Unit]
        BODY
        mf_write_stdin "{d}/u.service" <<'BODY2'
        replaced
        BODY2
        mf_append_line "{d}/c.txt" "first=1"
        mf_append_line "{d}/c.txt" "second=2"
        mf_append_line "{d}/c.txt" 'LABEL="x" $novar `nocmd`'
    ''')
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=30)
    assert p.returncode == 0, f"real write path failed: {p.stderr[:300]}"
    unit = (d / "u.service").read_text()
    assert unit.strip() == "replaced", \
        f"mf_write_stdin must TRUNCATE like `cat >`, got {unit!r}"
    cfg = (d / "c.txt").read_text().splitlines()
    assert cfg[:2] == ["first=1", "second=2"], f"append order wrong: {cfg!r}"
    assert cfg[2] == 'LABEL="x" $novar `nocmd`', \
        f"appended line must land LITERALLY, got {cfg[2]!r}"
