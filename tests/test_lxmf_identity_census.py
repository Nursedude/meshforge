"""scripts/lxmf_identity_census.py — which identity on THIS box is which hash.

2026-10-07: 58cecbd0 (the MeshAnchor gateway) was a "lone hash" for months,
named only after hashing its key file by hand. This makes that a command:
find identity files, print their PUBLIC lxmf.delivery address + the app
the path implies. Read-only; never prints key material; a directory it
cannot read is reported as blind, never silently skipped.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

RNS = pytest.importorskip("RNS")

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "lxmf_identity_census.py"
_spec = importlib.util.spec_from_file_location("lxmf_identity_census", _SCRIPT)
census = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(census)


def _ident(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    i = RNS.Identity()
    i.to_file(str(path))
    return RNS.Destination.hash_from_name_and_identity("lxmf.delivery", i).hex()


def test_finds_and_hashes_known_apps(tmp_path):
    gw = _ident(tmp_path / ".config" / "meshanchor" / "gateway_identity")
    nn = _ident(tmp_path / ".nomadnetwork" / "storage" / "identity")
    rows = {r["path"]: r for r in census.scan(tmp_path)}
    g = rows[str(tmp_path / ".config/meshanchor/gateway_identity")]
    assert g["lxmf_delivery"] == gw and g["app"] == "MeshAnchor gateway"
    n = rows[str(tmp_path / ".nomadnetwork/storage/identity")]
    assert n["lxmf_delivery"] == nn and n["app"] == "NomadNet"


def test_unknown_location_still_reported(tmp_path):
    h = _ident(tmp_path / "somewhere" / "my_identity")
    rows = census.scan(tmp_path)
    assert [r["lxmf_delivery"] for r in rows] == [h]
    assert rows[0]["app"] == "unknown app"


def test_non_identity_files_are_ignored(tmp_path):
    (tmp_path / "identity_notes.txt").write_text("not a key")
    (tmp_path / "identity").write_bytes(b"\x00" * 10)      # wrong size
    assert census.scan(tmp_path) == []


def test_output_never_contains_key_material(tmp_path, capsys):
    p = tmp_path / ".config" / "meshforge" / "gateway_identity"
    _ident(p)
    key_hex = p.read_bytes().hex()
    census.main(["--home", str(tmp_path)])
    out = capsys.readouterr().out
    assert key_hex not in out and key_hex[:32] not in out
    assert "MeshForge gateway" in out


def test_unreadable_dir_is_reported_blind(tmp_path):
    locked = tmp_path / ".config" / "locked"
    locked.mkdir(parents=True)
    locked.chmod(0)
    try:
        blind = census.scan_blind(tmp_path)
    finally:
        locked.chmod(0o755)
    import os
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    assert str(locked) in blind


def test_transport_and_interactive_nomadnet_are_labelled(tmp_path):
    _ident(tmp_path / ".reticulum" / "storage" / "transport_identity")
    _ident(tmp_path / ".nomadnetwork-interactive" / "storage" / "identity")
    apps = sorted(r["app"] for r in census.scan(tmp_path))
    assert apps == ["NomadNet", "rnsd transport identity (not an LXMF inbox)"]


def test_backup_and_snapshot_copies_are_marked(tmp_path):
    _ident(tmp_path / "fleet-configs" / "moc.superseded-2026" / "meshforge"
           / "gateway_identity")
    _ident(tmp_path / "meshforge-backup-2026" / "gateway_identity")
    assert {r["copy"] for r in census.scan(tmp_path)} == {True}


def test_missing_rns_is_unknown_not_a_crash(tmp_path, monkeypatch, capsys):
    import builtins
    real = builtins.__import__

    def no_rns(name, *a, **k):
        if name == "RNS":
            raise ImportError("no RNS here")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_rns)
    rc = census.main(["--home", str(tmp_path), "--box", "x"])
    assert rc == 2
    assert "UNKNOWN" in capsys.readouterr().out


def test_depth_capped_dirs_are_reported_not_silently_skipped(tmp_path, monkeypatch):
    """2026-10-07: MAX_DEPTH 6 silently skipped the DR snapshot gateway keys
    (fleet-configs/<box>/snapshot/home/<user>/.config/meshforge/ = depth 7)."""
    monkeypatch.setattr(census, "MAX_DEPTH", 2)
    _ident(tmp_path / "a" / "b" / "c" / "d" / "gateway_identity")
    assert census.scan(tmp_path) == []                      # beyond reach…
    capped = census.scan_capped(tmp_path)
    assert capped and all(str(tmp_path / "a") in c for c in capped)  # …but SAID


def test_default_depth_reaches_snapshot_keys(tmp_path):
    h = _ident(tmp_path / "fleet-configs" / "moc" / "snapshot" / "home" / "u"
               / ".config" / "meshforge" / "gateway_identity")
    assert [r["lxmf_delivery"] for r in census.scan(tmp_path)] == [h]


def test_main_prints_depth_blind(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(census, "MAX_DEPTH", 1)
    (tmp_path / "x" / "y" / "z").mkdir(parents=True)
    census.main(["--home", str(tmp_path), "--box", "b"])
    assert "BLIND (depth limit" in capsys.readouterr().out
