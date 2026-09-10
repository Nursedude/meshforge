"""Per-probe isolation in ``run_all_probes`` (pass-3 finding 1, 2026-09-09).

Before the fix there was no ``try:`` between probes: one probe raising
reached the tick-level handler in ``main``, which set ``signals = []`` and
``coverage = None`` — every class blanked for the tick. These tests PLANT a
raising probe and assert the other probes' signals survive, the raise is
witnessed (log + ``indeterminate`` on the classes the probe owns), and the
ownership table the isolation relies on agrees with the probes' own bodies.
"""
from __future__ import annotations

import ast
import glob
import logging
import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import utils.watchdog_runner as wr  # noqa: E402
from utils import watchdog_isolation as iso  # noqa: E402
from utils.watchdog_probe_core import SIGNAL_CLASSES, Signal  # noqa: E402


_LIST_PROBES = iso.LIST_RETURNING_PROBES


def _stub_every_probe(monkeypatch, overrides: dict) -> None:
    """Replace every ``probe_*`` on the runner with a PLAIN stub (so no real
    subprocess runs), then run the PRODUCTION installer over the runner's
    namespace — the same call the runner makes at import. ``overrides``
    maps probe name → callable. monkeypatch restores the originals."""
    for name in dir(wr):
        if not name.startswith("probe_"):
            continue
        fn = overrides.get(name)
        if fn is None:
            ret = [] if name in _LIST_PROBES else None
            fn = (lambda *a, _r=ret, **k: _r)
        monkeypatch.setattr(wr, name, fn)
    monkeypatch.setattr(wr, "run_rnstatus", lambda **k: None)
    wrapped = iso.install_probe_isolation(wr.__dict__)
    assert wrapped >= 50, f"installer wrapped only {wrapped} probes"


def _healthy_signal(unit):
    return Signal(cls="service_inactive", subject=unit, severity="degraded",
                  detail="planted")


def _boom(*a, **k):
    raise NameError("name '_rnsd_unit_enabled' is not defined")


class TestOneRaisingProbeDoesNotBlankTheTick:

    def test_other_signals_survive_and_the_raise_is_witnessed(
            self, monkeypatch, caplog):
        _stub_every_probe(monkeypatch, {
            "probe_service_inactive": _healthy_signal,
            "probe_rns_namespace_collision": _boom,
        })
        with caplog.at_level(logging.ERROR, logger="watchdog"):
            signals = wr.run_all_probes(
                rns_instance_name="volcano",
                services_expected_active=("rnsd.service",),
                services_wedge_check=(),
            )
        # The probe AFTER the raise still ran and its signal is present.
        assert [s.cls for s in signals] == ["service_inactive"]
        cov = wr.build_coverage(signals)
        got = cov["rns_namespace_collision"]
        assert got["disp"] == "indeterminate", got
        assert "probe raised NameError" in got["reason"]
        # The traceback landed in the journal, not just a one-liner.
        rec = [r for r in caplog.records
               if "probe_rns_namespace_collision raised NameError" in r.getMessage()]
        assert rec and rec[0].exc_info is not None
        assert iso.probe_raise_counts.get("probe_rns_namespace_collision", 0) >= 1

    def test_list_returning_probe_raise_yields_empty_list_not_none(
            self, monkeypatch):
        """The runner ``extend``s these; a None would TypeError the tick."""
        _stub_every_probe(monkeypatch, {
            "probe_memory_cap_engaged": _boom,
            "probe_service_inactive": _healthy_signal,
        })
        signals = wr.run_all_probes(
            rns_instance_name=None,
            services_expected_active=("rnsd.service",),
            services_wedge_check=(),
        )
        assert [s.cls for s in signals] == ["service_inactive"]
        assert wr.build_coverage(signals)["memory_cap_engaged"]["disp"] == \
            "indeterminate"

    def test_multi_class_probe_marks_every_class_it_owns(self, monkeypatch):
        _stub_every_probe(monkeypatch, {
            "probe_rns_shared_instance_responsive": _boom,
        })
        signals = wr.run_all_probes(
            rns_instance_name="volcano",
            services_expected_active=(), services_wedge_check=(),
        )
        cov = wr.build_coverage(signals)
        for cls in ("rns_shared_instance_unresponsive",
                    "rns_instance_name_mismatch"):
            assert cov[cls]["disp"] == "indeterminate", (cls, cov[cls])


class TestIsolationIsInstalledOnEveryProbe:

    def test_every_probe_on_the_runner_is_wrapped(self):
        unwrapped = [n for n in dir(wr) if n.startswith("probe_")
                     and getattr(getattr(wr, n), "__wrapped__", None) is None]
        assert not unwrapped, unwrapped

    def test_call_sites_keep_literal_probe_syntax(self):
        """The honesty-invariant wiring gate walks ``probe_x(...)`` calls by
        AST inside run_all_probes; isolation must not hide them."""
        src = (SRC / "utils" / "watchdog_runner.py").read_text()
        body = re.search(r"def run_all_probes\(.*?\n    return signals\n",
                         src, re.S).group(0)
        assert len(set(re.findall(r"\b(probe_\w+)\(", body))) >= 50


class TestOwnershipTableAgreesWithProbeBodies:
    """The closed consumer for ``_PROBE_OWNS_OVERRIDES`` + the name rule:
    re-derive each called probe's classes from the SIGNAL_CLASSES string
    literals in its own body and fail on any disagreement
    (honest_failure_modes #7 — a table nobody checks drifts)."""

    @staticmethod
    def _literal_owned() -> dict:
        classes = set(SIGNAL_CLASSES)
        owned = {}
        for path in sorted(glob.glob(str(SRC / "utils" / "watchdog_probes*.py"))):
            tree = ast.parse(Path(path).read_text())
            for fn in tree.body:
                if isinstance(fn, ast.FunctionDef) and fn.name.startswith("probe_"):
                    found = {n.value for n in ast.walk(fn)
                             if isinstance(n, ast.Constant)
                             and isinstance(n.value, str) and n.value in classes}
                    owned[fn.name] = found
        return owned

    def test_table_matches_literals_for_every_called_probe(self):
        src = (SRC / "utils" / "watchdog_runner.py").read_text()
        body = re.search(r"def run_all_probes\(.*?\n    return signals\n",
                         src, re.S).group(0)
        called = sorted(set(re.findall(r"\b(probe_\w+)\(", body)))
        literal = self._literal_owned()
        drift = {}
        for name in called:
            table = set(iso.probe_owned_classes(name))
            if not table or table != literal.get(name, set()):
                drift[name] = (sorted(table), sorted(literal.get(name, set())))
        assert not drift, f"ownership table vs probe bodies: {drift}"

    def test_list_returning_probes_are_the_declared_closed_set(self):
        assert set(_LIST_PROBES) == {
            "probe_lxmf_process_wedge", "probe_tracer_peer_unreachable",
            "probe_memory_cap_engaged"}


if __name__ == "__main__":
    pytest.main([__file__])
