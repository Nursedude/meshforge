"""utils.rnsd_plugin_manifest — what will rnsd exec from interfaces/ next start?

R5 (2026-10-10): one external interface that panics while loading used to take
down every interface on the shared instance (fork fix 0f8f637f). The other half
is knowing, per box, WHICH plugins rnsd will load. These pin rnsd's own rules
(Reticulum.py: a stanza loads only if `enabled`/`interface_enabled` is present
and true; a type outside the built-in chain is `<configdir>/interfaces/<type>.py`)
and that every blind spot reads UNKNOWN, never "nothing loads".

Ambient state is pinned (CI 1edb2795 went red on this file: CI's minimal
profile has no RNS, so the real vendored ConfigObj was absent and every
classification read UNKNOWN — the tests had only ever run beside an installed
RNS). The classification tests use a small test-local parser; the REAL
ConfigObj path is one explicitly-skipped test; no parser at all is pinned as
UNKNOWN.
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

from utils import rnsd_plugin_manifest as m

_ROOT = Path(__file__).resolve().parents[1]
BUILTIN = {"AutoInterface", "TCPServerInterface", "TCPClientInterface", "RNodeInterface"}


def _mini_configobj(path):
    """Just enough ConfigObj for these fixtures: [section] / [[sub]] / k = v.
    Raises on an unclosed header, as ConfigObj does."""
    root, top, sub = {}, None, None
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            m2 = re.fullmatch(r"(\[+)\s*([^\[\]]+?)\s*(\]+)", line)
            if not m2 or len(m2.group(1)) != len(m2.group(3)):
                raise ValueError(f"bad header: {line!r}")
            if len(m2.group(1)) == 1:
                top = root.setdefault(m2.group(2), {}); sub = None
            else:
                sub = top.setdefault(m2.group(2), {})
            continue
        k, _, v = line.partition("=")
        (sub if sub is not None else top)[k.strip()] = v.strip()
    return root


@pytest.fixture
def pinned_parser(monkeypatch):
    monkeypatch.setattr(m, "_ConfigObj", _mini_configobj)
    monkeypatch.setattr(m, "_HAS_CONFIGOBJ", True)


_REAL_CONFIGOBJ = pytest.mark.skipif(
    not m._HAS_CONFIGOBJ, reason="RNS's vendored ConfigObj not importable here (CI minimal profile)")


def _box(tmp_path, config, plugins=()):
    cfg = tmp_path / "rns"
    (cfg / "interfaces").mkdir(parents=True)
    (cfg / "config").write_text(config)
    for name, body in dict(plugins).items():
        (cfg / "interfaces" / name).write_text(body)
    return cfg


CONFIG = """
[reticulum]
  share_instance = Yes
[interfaces]
  [[Default Interface]]
    type = AutoInterface
    enabled = yes
  [[SMCI leg]]
    type = SmartMeshCoreInterface
    enabled = yes
  [[Off plugin]]
    type = OffPlugin
    enabled = no
  [[Alt key]]
    type = AltKeyPlugin
    interface_enabled = True
  [[No enable key]]
    type = NoKeyPlugin
  [[Gone]]
    type = GonePlugin
    enabled = yes
"""


def test_classifies_every_stanza_by_rnsds_own_rules(tmp_path, pinned_parser):
    cfg = _box(tmp_path, CONFIG, {
        "SmartMeshCoreInterface.py": "interface_class = None\n",
        "AltKeyPlugin.py": "x = 1\n",
        "OffPlugin.py": "x = 2\n",
        "NoKeyPlugin.py": "x = 3\n",
        "Meshtastic_Interface.py.disabled": "x = 4\n",
        "Stray.py": "x = 5\n",
    })
    r = m.build_manifest(cfg, BUILTIN)
    assert r.status == "ok"
    loads = {e.type: e for e in r.will_load}
    assert set(loads) == {"SmartMeshCoreInterface", "AltKeyPlugin"}
    smci = loads["SmartMeshCoreInterface"]
    assert smci.stanza == "SMCI leg"
    assert smci.sha256 == hashlib.sha256(b"interface_class = None\n").hexdigest()
    assert [e.type for e in r.missing] == ["GonePlugin"]
    # disabled / no enable key: rnsd skips the stanza, so the file is NOT loaded
    assert {e.type for e in r.disabled} == {"OffPlugin", "NoKeyPlugin"}
    # files nothing references: inert (incl. the moc3 `.disabled` convention)
    assert set(r.unreferenced) == {"Meshtastic_Interface.py.disabled", "Stray.py",
                                   "OffPlugin.py", "NoKeyPlugin.py"}
    assert "AutoInterface" not in {e.type for e in r.will_load + r.missing + r.disabled}


def test_no_external_interfaces_is_ok_and_empty(tmp_path, pinned_parser):
    cfg = _box(tmp_path, "[interfaces]\n  [[A]]\n    type = AutoInterface\n    enabled = yes\n")
    r = m.build_manifest(cfg, BUILTIN)
    assert r.status == "ok" and r.will_load == [] and r.missing == []


@pytest.mark.parametrize("make,why", [
    (lambda p: p / "absent", "no config"),
    (lambda p: _box(p, "[interfaces\n  broken"), "parse"),
])
def test_unreadable_or_unparseable_config_is_unknown_never_empty(tmp_path, make, why, pinned_parser):
    """An absent/broken config must not read as 'rnsd loads nothing' (hfm #1/#2)."""
    r = m.build_manifest(make(tmp_path), BUILTIN)
    assert r.status == "unknown" and why in r.reason, r.reason
    assert r.will_load == []


def test_missing_builtin_list_is_unknown(tmp_path):
    """Without the built-in set every built-in would read as a plugin."""
    cfg = _box(tmp_path, CONFIG)
    r = m.build_manifest(cfg, None)
    assert r.status == "unknown" and "built-in" in r.reason


def test_builtin_types_are_derived_from_the_rns_source():
    src = ('                if c["type"] == "AutoInterface":\n'
           '                if c["type"] == "RNodeMultiInterface":\n'
           '                    interface = X\n')
    assert m.builtin_types_from_source(src) == {"AutoInterface", "RNodeMultiInterface"}
    assert m.builtin_types_from_source("no chain here") is None


def test_derived_builtins_from_the_installed_rns_cover_the_known_chain():
    """Against the REAL installed RNS (skip if absent): the derived set must
    contain the types the fleet configs use, so none reads as a plugin."""
    types, src = m.installed_builtin_types()
    if types is None:
        pytest.skip(f"RNS not importable here: {src}")
    assert {"AutoInterface", "RNodeInterface", "TCPServerInterface",
            "TCPClientInterface", "BackboneInterface"} <= types, (src, types)


@_REAL_CONFIGOBJ
def test_cli_reports_and_exits_0_on_ok(tmp_path):
    cfg = _box(tmp_path, CONFIG, {"SmartMeshCoreInterface.py": "x\n"})
    r = subprocess.run([sys.executable, str(_ROOT / "src/utils/rnsd_plugin_manifest.py"),
                        "--configdir", str(cfg)], capture_output=True, text=True, timeout=60,
                       cwd=str(_ROOT))
    assert r.returncode == 0, r.stderr
    assert "WILL LOAD" in r.stdout and "SmartMeshCoreInterface" in r.stdout, r.stdout
    assert "MISSING" in r.stdout and "GonePlugin" in r.stdout


def test_no_rnsd_on_the_box_is_inert_not_unknown(monkeypatch, capsys):
    """lehua 2026-10-10: unit not-found, no /etc/reticulum, no rnsd binary.
    Absent by design must read `inert` (exit 0), never UNKNOWN."""
    import utils.config_drift as cd
    monkeypatch.setattr(cd, "_get_rnsd_effective_config", lambda: (None, None, "rnsd_not_running"))
    monkeypatch.setattr(m, "_rnsd_installed", lambda: False)
    assert m.main([]) == 0
    assert "inert" in capsys.readouterr().out


def test_rnsd_installed_but_stopped_stays_unknown(monkeypatch, capsys):
    """Installed but not running: its next start WILL load from a config we
    could not resolve — that is a blind spot, not an absence."""
    import utils.config_drift as cd
    monkeypatch.setattr(cd, "_get_rnsd_effective_config", lambda: (None, None, "rnsd_not_running"))
    monkeypatch.setattr(m, "_rnsd_installed", lambda: True)
    assert m.main([]) == 2
    assert "UNKNOWN" in capsys.readouterr().out


def test_cli_exits_2_when_unknown(tmp_path):
    r = subprocess.run([sys.executable, str(_ROOT / "src/utils/rnsd_plugin_manifest.py"),
                        "--configdir", str(tmp_path / "nope")], capture_output=True,
                       text=True, timeout=60, cwd=str(_ROOT))
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "UNKNOWN" in r.stdout


@_REAL_CONFIGOBJ
def test_real_vendored_configobj_classifies_like_the_pinned_parser(tmp_path):
    """The real parser rnsd uses must agree with the test-local one."""
    cfg = _box(tmp_path, CONFIG, {"SmartMeshCoreInterface.py": "x\n", "AltKeyPlugin.py": "y\n"})
    r = m.build_manifest(cfg, BUILTIN)
    assert r.status == "ok", r.reason
    assert {e.type for e in r.will_load} == {"SmartMeshCoreInterface", "AltKeyPlugin"}
    assert [e.type for e in r.missing] == ["GonePlugin"]


def test_no_parser_reads_unknown_never_empty(tmp_path, monkeypatch):
    """CI's shape, pinned: without RNS's ConfigObj the manifest cannot parse
    as rnsd does, and must say so rather than report zero plugins."""
    monkeypatch.setattr(m, "_HAS_CONFIGOBJ", False)
    r = m.build_manifest(_box(tmp_path, CONFIG), BUILTIN)
    assert r.status == "unknown" and "ConfigObj" in r.reason, r.reason
    assert r.will_load == []
