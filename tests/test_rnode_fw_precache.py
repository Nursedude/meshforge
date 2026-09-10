"""Tests for scripts/rnode_fw_precache.py — the offline-flash readiness tool.

Every test builds its OWN zips and cache tree under ``tmp_path``. Nothing here
reads the real ``~/.config/rnodeconf`` cache, because a test whose verdict
depends on un-pinned machine state pins nothing: it would pass on a box that
happens to have run ``--fetch`` and fail everywhere else.

The load-bearing assertion is the negative one. ``--verify`` exists to say NOT
READY; a suite that only ever exercises the healthy path would let it degrade
into a function that always returns 0.
"""

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rnode_fw_precache.py"

_spec = importlib.util.spec_from_file_location("rnode_fw_precache", SCRIPT)
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)

# The board table and firmware URLs come from rnodeconf, which ships with rns.
# CI installs the minimal-deps profile and has no rns, so every test that reads
# the model table must SKIP there rather than fail — it is testing a dependency
# that is legitimately absent, not a defect. The zip-shape and cache tests below
# build their own fixtures and need none of this, so they still run on CI, which
# is the point: the skip is scoped to what actually needs the dependency.
#
# ⚠️ Scoped, and visible. A blanket module-level skip would hide the zip-shape
# coverage too, and a silent one would let this file quietly stop testing
# anything at all. pytest reports skips with this reason attached.
needs_rnodeconf = pytest.mark.skipif(
    not pc._HAS_RNODECONF,
    reason="RNS.Utilities.rnodeconf not importable (rns not installed — "
           "expected on the minimal-deps CI profile)")

FW_ESP = "rnode_firmware_heltec32v3.zip"
FW_NRF = "rnode_firmware_rak4631.zip"
VERSION = "1.86"


def _make_zip(path: Path, members) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name in members:
            z.writestr(name, b"x" * 16)


def _esp_members(fw: str):
    stem = fw[:-4]
    return [stem + s for s in pc.ESP32_SUFFIXES] + list(pc.ESP32_EXTRAS)


def _nrf_members(fw: str):
    stem = fw[:-4]
    return [stem + s for s in pc.NRF52_SUFFIXES] + list(pc.NRF52_EXTRAS)


def _seed(upd: Path, fw: str, members, version: str = VERSION) -> Path:
    """Write a cache entry the way do_fetch() would, hash and all."""
    zpath = upd / version / fw
    _make_zip(zpath, members)
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    (upd / version / (fw + ".version")).write_text(f"{version} {digest}")
    return zpath


class TestZipShape:
    def test_complete_esp32_set_accepted(self, tmp_path):
        z = tmp_path / FW_ESP
        _make_zip(z, _esp_members(FW_ESP))
        ok, why = pc.zip_shape_ok(z, FW_ESP)
        assert ok, why
        assert "ESP32" in why

    def test_complete_nrf52_set_accepted(self, tmp_path):
        z = tmp_path / FW_NRF
        _make_zip(z, _nrf_members(FW_NRF))
        ok, why = pc.zip_shape_ok(z, FW_NRF)
        assert ok, why
        assert "nRF52" in why

    @pytest.mark.parametrize("drop", [".bootloader", ".partitions", ".boot_app0", ".bin"])
    def test_missing_any_esp32_member_rejected(self, tmp_path, drop):
        members = [m for m in _esp_members(FW_ESP) if not m.endswith(drop)]
        z = tmp_path / FW_ESP
        _make_zip(z, members)
        ok, _ = pc.zip_shape_ok(z, FW_ESP)
        assert not ok, f"a zip missing {drop} must not read as flashable"

    def test_missing_bundled_esptool_rejected(self, tmp_path):
        """rnodeconf flashes with the zip's own esptool.py, not a system one."""
        members = [m for m in _esp_members(FW_ESP) if m != "esptool.py"]
        z = tmp_path / FW_ESP
        _make_zip(z, members)
        ok, _ = pc.zip_shape_ok(z, FW_ESP)
        assert not ok

    def test_wrong_target_zip_rejected(self, tmp_path):
        """An nRF52 payload sitting under an ESP32 filename is not flashable."""
        z = tmp_path / FW_ESP
        _make_zip(z, _nrf_members(FW_NRF))
        ok, why = pc.zip_shape_ok(z, FW_ESP)
        assert not ok
        assert "NEITHER" in why

    def test_unreadable_file_is_not_a_pass(self, tmp_path):
        z = tmp_path / FW_ESP
        z.write_bytes(b"not a zip at all")
        ok, why = pc.zip_shape_ok(z, FW_ESP)
        assert not ok
        assert "unreadable" in why


class TestVerify:
    def test_intact_cache_is_ready(self, tmp_path, capsys):
        _seed(tmp_path, FW_ESP, _esp_members(FW_ESP))
        assert pc.do_verify(tmp_path, [FW_ESP]) == 0
        assert "READY" in capsys.readouterr().out

    def test_empty_cache_is_not_ready(self, tmp_path):
        assert pc.do_verify(tmp_path, [FW_ESP]) == 1

    def test_corrupt_zip_is_not_ready(self, tmp_path):
        z = _seed(tmp_path, FW_ESP, _esp_members(FW_ESP))
        data = bytearray(z.read_bytes())
        data[len(data) // 2] ^= 0xFF
        z.write_bytes(bytes(data))
        assert pc.do_verify(tmp_path, [FW_ESP]) == 1

    def test_missing_version_file_is_not_ready(self, tmp_path):
        """Without it rnodeconf has no hash to verify against offline."""
        _seed(tmp_path, FW_ESP, _esp_members(FW_ESP))
        (tmp_path / VERSION / (FW_ESP + ".version")).unlink()
        assert pc.do_verify(tmp_path, [FW_ESP]) == 1

    def test_malformed_version_file_is_not_ready(self, tmp_path):
        _seed(tmp_path, FW_ESP, _esp_members(FW_ESP))
        (tmp_path / VERSION / (FW_ESP + ".version")).write_text("1.86")
        assert pc.do_verify(tmp_path, [FW_ESP]) == 1

    def test_one_bad_board_fails_the_whole_run(self, tmp_path):
        """A partial cache must not read as ready — you cannot flash the other board."""
        _seed(tmp_path, FW_ESP, _esp_members(FW_ESP))
        assert pc.do_verify(tmp_path, [FW_ESP, FW_NRF]) == 1

    def test_a_second_good_version_still_counts(self, tmp_path):
        """A stale broken entry alongside a good one must not veto readiness."""
        _seed(tmp_path, FW_ESP, _esp_members(FW_ESP)[:-2], version="1.80")
        _seed(tmp_path, FW_ESP, _esp_members(FW_ESP), version="1.86")
        assert pc.do_verify(tmp_path, [FW_ESP]) == 0


@needs_rnodeconf
class TestBoardResolution:
    def test_exact_name_resolves(self):
        resolved, unknown = pc.resolve_boards([FW_ESP])
        assert resolved == [FW_ESP]
        assert unknown == []

    def test_unique_substring_resolves(self):
        resolved, unknown = pc.resolve_boards(["heltec32v3"])
        assert resolved == [FW_ESP]
        assert unknown == []

    def test_ambiguous_substring_refuses_rather_than_guessing(self):
        resolved, unknown = pc.resolve_boards(["heltec32"])
        assert resolved == []
        assert len(unknown) == 1 and "ambiguous" in unknown[0]

    def test_unknown_board_reported(self):
        resolved, unknown = pc.resolve_boards(["definitely_not_a_board"])
        assert resolved == []
        assert len(unknown) == 1 and "no firmware matches" in unknown[0]

    def test_homebrew_none_firmware_never_reaches_the_table(self):
        """Models 0xFE/0xFF carry a None firmware name — a None key crashes matching."""
        assert all(fw for fw in pc.known_firmware()), "a falsy firmware name leaked in"

    def test_field_kit_entries_are_all_real_firmware(self):
        """Guards against a typo'd FIELD_KIT silently fetching nothing."""
        known = pc.known_firmware()
        for fw in pc.FIELD_KIT:
            assert fw in known, f"{fw} is not a firmware rnodeconf knows about"


@needs_rnodeconf
class TestFetchDegradedPaths:
    def test_unreachable_manifest_fails_loud(self, tmp_path, monkeypatch):
        """No manifest means no trustworthy version or hash — must not guess."""
        def boom(*a, **kw):
            raise pc.URLError("no route to host")
        monkeypatch.setattr(pc, "_download", boom)
        assert pc.do_fetch(tmp_path, [FW_ESP]) == 1

    def test_firmware_absent_from_manifest_fails(self, tmp_path, monkeypatch):
        def fake(url, dest, timeout=None):
            Path(dest).write_text(json.dumps({"some_other_firmware.zip":
                                              {"version": "1.86", "hash": "ab"}}))
        monkeypatch.setattr(pc, "_download", fake)
        assert pc.do_fetch(tmp_path, [FW_ESP]) == 1

    def test_hash_mismatch_removes_the_corpse(self, tmp_path, monkeypatch):
        """A bad download must not be left behind looking like a cache entry."""
        def fake(url, dest, timeout=None):
            dest = Path(dest)
            if dest.name == "release_info.json":
                dest.write_text(json.dumps({FW_ESP: {"version": VERSION, "hash": "de" * 32}}))
            else:
                _make_zip(dest, _esp_members(FW_ESP))
        monkeypatch.setattr(pc, "_download", fake)
        assert pc.do_fetch(tmp_path, [FW_ESP]) == 1
        assert not (tmp_path / VERSION / FW_ESP).exists()


class _Resp:
    """A urlopen() response: chunks, then b"" — or an exception mid-stream."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read(self, n):
        item = self._chunks.pop(0) if self._chunks else b""
        if isinstance(item, BaseException):
            raise item
        return item

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestDownloadIsBounded:
    """Finding 11 (2026-09-09): both urlretrieve() calls had no timeout at
    all; DOWNLOAD_TIMEOUT was defined and never used. On a lossy WAN a
    stalled TCP connection mid-zip hung --fetch forever, and a Ctrl-C left a
    partial zip at the cache path that only the NEXT run rejected."""

    def test_urlopen_is_given_the_timeout(self, tmp_path, monkeypatch):
        seen = {}

        def fake_urlopen(url, timeout=None):
            seen["url"], seen["timeout"] = url, timeout
            return _Resp([b"abc", b"def"])
        monkeypatch.setattr(pc, "urlopen", fake_urlopen)
        dest = tmp_path / "x.zip"
        assert pc._download("http://example.invalid/x.zip", dest) == 6
        assert dest.read_bytes() == b"abcdef"
        assert seen["timeout"] == pc.DOWNLOAD_TIMEOUT, "an unbounded download on a lossy WAN"

    def test_a_stall_raises_and_leaves_no_partial_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "urlopen",
                            lambda url, timeout=None: _Resp([b"abc", TimeoutError("timed out")]))
        dest = tmp_path / "x.zip"
        with pytest.raises(OSError):
            pc._download("http://example.invalid/x.zip", dest)
        assert not dest.exists(), "a half zip at the cache path looks like a cache entry"
        assert list(tmp_path.glob("*.part")) == []

    def test_a_keyboard_interrupt_leaves_no_partial_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "urlopen",
                            lambda url, timeout=None: _Resp([b"abc", KeyboardInterrupt()]))
        dest = tmp_path / "x.zip"
        with pytest.raises(KeyboardInterrupt):
            pc._download("http://example.invalid/x.zip", dest)
        assert not dest.exists() and list(tmp_path.glob("*.part")) == []

    def test_nothing_in_the_tool_calls_urlretrieve(self):
        assert "urlretrieve(" not in SCRIPT.read_text(encoding="utf-8")

    @needs_rnodeconf
    def test_a_stalled_zip_reports_fail_and_leaves_no_corpse(self, tmp_path, monkeypatch):
        manifest = json.dumps({FW_ESP: {"version": VERSION, "hash": "de" * 32}}).encode()

        def fake_urlopen(url, timeout=None):
            assert timeout == pc.DOWNLOAD_TIMEOUT
            if url == pc.rc.firmware_version_url:
                return _Resp([manifest])
            return _Resp([b"PK\x03\x04", TimeoutError("timed out")])
        monkeypatch.setattr(pc, "urlopen", fake_urlopen)
        assert pc.do_fetch(tmp_path, [FW_ESP]) == 1
        assert not (tmp_path / VERSION / FW_ESP).exists()
        assert list((tmp_path / VERSION).glob("*.part")) == []
