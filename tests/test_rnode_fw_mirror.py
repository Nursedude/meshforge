"""scripts/rnode_fw_mirror.verify must refuse anything that is not byte-identical
to upstream's release.json (2026-09-25: owning the RNode firmware)."""
import hashlib
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "rnode_fw_mirror", os.path.join(HERE, "..", "scripts", "rnode_fw_mirror.py"))
mirror = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mirror)


def _release(tmp_path, files):
    manifest = {}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
        manifest[name] = {"hash": hashlib.sha256(data).hexdigest(), "version": "9.99"}
    (tmp_path / "release.json").write_text(json.dumps(manifest))
    return tmp_path


def test_identical_release_verifies(tmp_path):
    assert mirror.verify(_release(tmp_path, {"a.zip": b"one", "b.zip": b"two"})) == []


def test_one_flipped_byte_is_refused(tmp_path):
    d = _release(tmp_path, {"a.zip": b"one", "b.zip": b"two"})
    (d / "b.zip").write_bytes(b"twO")
    problems = mirror.verify(d)
    assert len(problems) == 1 and problems[0].startswith("b.zip: sha256")


def test_missing_and_unlisted_zips_are_refused(tmp_path):
    d = _release(tmp_path, {"a.zip": b"one", "b.zip": b"two"})
    (d / "a.zip").unlink()
    (d / "rogue.zip").write_bytes(b"x")
    problems = " | ".join(mirror.verify(d))
    assert "a.zip: in release.json but not downloaded" in problems
    assert "rogue.zip: not in release.json" in problems


def test_no_manifest_is_not_a_pass(tmp_path):
    (tmp_path / "a.zip").write_bytes(b"one")
    assert mirror.verify(tmp_path) != []
