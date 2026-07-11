"""Tests for scripts/mesh_psk_safe.py — the redacting-only channel PSK tool.

Pins the honest-failure-modes contract added 2026-07-11 after a code-review
audit (this pair shipped with NO tests):
  - _redact never lets a real key through, INCLUDING short/default keys (the
    old {8,} floor printed "AQ==" verbatim and made a default-psk channel read
    "channel not found"); the redaction is a hash prefix, never the key bytes.
  - a transport failure (unreachable radio / CLI nonzero) is reported as its own
    state (rc 4), NEVER conflated with "channel not found" (rc 3) — the ambiguity
    class that split the mesh in the 07-11 incident this tool was born from.
  - `info` propagates the radio's exit code instead of always returning 0.

All meshtastic round-trips are mocked; no test touches a real radio.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
from unittest import mock

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                       "mesh_psk_safe.py")
_spec = importlib.util.spec_from_file_location("mesh_psk_safe", _SCRIPT)
mps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mps)

# Synthetic keys only (never a live secret — feedback_never_live_secrets).
KEY_32 = "A" * 43 + "="    # 43 b64 chars + '='  = 32-byte key
KEY_16 = "B" * 22 + "=="   # 22 b64 chars + '==' = 16-byte key
KEY_DEFAULT = "AQ=="                                      # 1-byte simple/default


def _cp(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["meshtastic"], returncode=returncode, stdout=stdout, stderr=stderr)


# ── redaction: no key length escapes, output is a hash never the key ──

class TestRedact:
    @pytest.mark.parametrize("key", [KEY_32, KEY_16, KEY_DEFAULT])
    def test_psk_field_is_redacted_at_every_key_size(self, key):
        line = f'{{"psk": "{key}", "name": "Fleet"}}'
        out = mps._redact(line)
        assert key not in out, f"key {key!r} leaked through _redact"
        assert "<redacted:sha256:" in out

    def test_default_key_no_longer_prints_verbatim(self):
        # regression for the {8,} floor: "AQ==" (4 chars) used to pass through.
        assert "AQ==" not in mps._redact('{"psk": "AQ=="}')

    def test_channel_url_is_redacted(self):
        url = "https://meshtastic.org/e/#CgMSAQEaBFRlc3Q"
        out = mps._redact(f"URL: {url}")
        assert url not in out
        assert "<channel-url:sha256:" in out

    def test_non_secret_text_untouched(self):
        txt = '{"longName": "moc3", "region": "US"}'
        assert mps._redact(txt) == txt


# ── tri-state channel lookup: transport failure != channel absent ──

class TestChannelLookupTriState:
    def test_present_channel_returns_ok_and_key(self):
        info = '{"name": "Fleet", "psk": "%s"}' % KEY_32
        with mock.patch.object(mps, "_raw_info", return_value=_cp(stdout=info)):
            status, psk = mps._channel_psk("h", "Fleet")
        assert status == mps.CH_OK
        assert psk == KEY_32

    def test_missing_channel_on_good_read_is_absent(self):
        info = '{"name": "Other", "psk": "%s"}' % KEY_32
        with mock.patch.object(mps, "_raw_info", return_value=_cp(stdout=info)):
            status, psk = mps._channel_psk("h", "Fleet")
        assert status == mps.CH_ABSENT
        assert psk is None

    def test_transport_failure_is_error_not_absent(self):
        # rc != 0 (unreachable/CLI error) must NOT read as "channel not found".
        with mock.patch.object(mps, "_raw_info",
                               return_value=_cp(returncode=1, stderr="connect refused")):
            status, psk = mps._channel_psk("h", "Fleet")
        assert status == mps.CH_ERROR
        assert psk is None

    def test_default_key_channel_is_found(self):
        # regression: {8,} floor made a default-psk channel look absent.
        info = '{"name": "LongFast", "psk": "AQ=="}'
        with mock.patch.object(mps, "_raw_info", return_value=_cp(stdout=info)):
            status, psk = mps._channel_psk("h", "LongFast")
        assert status == mps.CH_OK
        assert psk == "AQ=="


# ── command exit codes reflect reality ──

class TestExitCodes:
    def test_keyhash_transport_failure_returns_4_not_3(self, capsys):
        with mock.patch.object(mps, "_raw_info",
                               return_value=_cp(returncode=1)):
            rc = mps.cmd_keyhash("h", "Fleet")
        assert rc == 4  # transport error, distinct from 3 (absent)
        assert "could not read radio" in capsys.readouterr().err

    def test_keyhash_absent_channel_returns_3(self, capsys):
        info = '{"name": "Other", "psk": "%s"}' % KEY_32
        with mock.patch.object(mps, "_raw_info", return_value=_cp(stdout=info)):
            rc = mps.cmd_keyhash("h", "Fleet")
        assert rc == 3
        assert "channel not found" in capsys.readouterr().err

    def test_keyhash_ok_prints_hash_never_key(self, capsys):
        info = '{"name": "Fleet", "psk": "%s"}' % KEY_32
        with mock.patch.object(mps, "_raw_info", return_value=_cp(stdout=info)):
            rc = mps.cmd_keyhash("h", "Fleet")
        out = capsys.readouterr().out
        assert rc == 0
        assert KEY_32 not in out
        assert "sha256:" in out

    def test_info_propagates_radio_failure(self, capsys):
        # unconditional-return-0 regression: a dead radio must not exit 0.
        with mock.patch.object(mps, "_raw_info",
                               return_value=_cp(returncode=1, stderr="Errno -2")):
            rc = mps.cmd_info("deadhost")
        assert rc == 4

    def test_info_success_returns_0_and_redacts(self, capsys):
        info = '{"name": "Fleet", "psk": "%s"}' % KEY_32
        with mock.patch.object(mps, "_raw_info", return_value=_cp(stdout=info)):
            rc = mps.cmd_info("h")
        out = capsys.readouterr().out
        assert rc == 0
        assert KEY_32 not in out

    def test_verify_match_and_differ(self, capsys, tmp_path):
        kf = tmp_path / "k"
        kf.write_text(KEY_32 + "\n")
        info = '{"name": "Fleet", "psk": "%s"}' % KEY_32
        with mock.patch.object(mps, "_raw_info", return_value=_cp(stdout=info)):
            assert mps.cmd_verify("h", "Fleet", str(kf)) == 0
        kf.write_text(KEY_16 + "\n")
        with mock.patch.object(mps, "_raw_info", return_value=_cp(stdout=info)):
            assert mps.cmd_verify("h", "Fleet", str(kf)) == 1
        assert KEY_32 not in capsys.readouterr().out


class TestSetpskRefusesBadKey:
    def test_non_base64_keyfile_refused(self, capsys, tmp_path):
        kf = tmp_path / "k"
        kf.write_text("not-a-key")
        rc = mps.cmd_setpsk("h", "1", str(kf))
        assert rc == 2
        assert "not a 32-byte base64 key" in capsys.readouterr().err
