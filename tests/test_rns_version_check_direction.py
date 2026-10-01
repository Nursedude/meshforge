"""rns_version_check.py: drift direction decides the converge advice (2026-10-01).

The fleet was rolled to rns 1.3.8+mf.3 in place before the boxes pulled the pin
bump. The checker's only advice was "pip install --force-reinstall -r
requirements/rns.txt", which on a box whose checkout still pins mf.1 would
DOWNGRADE it and undo the roll. Exit code is unchanged (1 = drift either way).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("rns_version_check_t", ROOT / "scripts/rns_version_check.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run(monkeypatch, capsys, pins, installed):
    mod = _load()
    monkeypatch.setattr(mod, "pinned_versions", lambda: pins)
    monkeypatch.setattr(mod, "installed_version", lambda pkg: installed.get(pkg))
    rc = mod.main()
    return rc, capsys.readouterr().out


def test_ahead_of_checkout_pin_says_pull_not_reinstall(monkeypatch, capsys):
    rc, out = _run(monkeypatch, capsys,
                   {"rns": "1.3.8+mf.1", "lxmf": "1.0.1+mf.2"},
                   {"rns": "1.3.8+mf.3", "lxmf": "1.0.1+mf.4"})
    assert rc == 1
    assert "AHEAD" in out and "downgrade" in out
    assert "--force-reinstall" not in out


@pytest.mark.parametrize("installed", [
    {"rns": "1.3.8+mf.1", "lxmf": "1.0.1+mf.4"},   # behind
    {"rns": "1.5.5", "lxmf": "1.0.1+mf.4"},        # stock upstream, different base
    {"rns": "1.3.8+mf.5", "lxmf": "1.0.1+mf.2"},   # mixed: one ahead, one behind
])
def test_anything_not_strictly_ahead_keeps_the_reinstall_advice(monkeypatch, capsys, installed):
    rc, out = _run(monkeypatch, capsys, {"rns": "1.3.8+mf.3", "lxmf": "1.0.1+mf.4"}, installed)
    assert rc == 1
    assert "--force-reinstall" in out and "AHEAD" not in out


def test_compliant_is_unchanged(monkeypatch, capsys):
    rc, out = _run(monkeypatch, capsys, {"rns": "1.3.8+mf.3"}, {"rns": "1.3.8+mf.3"})
    assert rc == 0 and "compliant" in out


@pytest.mark.parametrize("v", ["1.3.8+mf.3", "1.3.8", "1.0.1+mf.12", "1.3.8+local.9", "", "abc", "1.3.8+mf."])
def test_script_and_probe_parse_versions_identically(v):
    # One rule, two consumers (honest_failure_modes #5): test-pinned together.
    sys.path.insert(0, str(ROOT / "src"))
    from utils.watchdog_probes_drift import _fork_version_key
    assert _load().fork_version_key(v) == _fork_version_key(v)
