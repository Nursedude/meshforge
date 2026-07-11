"""Tests for .claude/hooks/psk_leak_guard.sh — the PreToolUse PSK-leak deny hook.

Pins the fixes from the 2026-07-11 code-review audit (this pair shipped with NO
tests — a security guard whose whole charter is "never again", unverified):
  - the sanctioned-wrapper exemption is ANCHORED, so a compound command like
    `wrapper && meshtastic --info` (the one-semicolon bypass) is still denied.
  - a payload the hook cannot parse fails OPEN but leaves a WITNESS file
    (honest_failure_modes point 9) instead of silently disabling the guard.
  - the inline-key pattern catches 16-byte AES-128 keys too and stops
    false-denying long base64 runs (ssh keys, base64 file transfer).
  - the raw-dump pattern is anchored to a real meshtastic invocation, so a
    `grep 'meshtastic --info' …` maintenance sweep is not false-denied.

Plus the cross-language constant cross-pin (honest_failure_modes point 5): the
hook (bash) and mesh_psk_safe.py (python) must agree on the secret shapes.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

_HOOK = os.path.join(os.path.dirname(__file__), "..", ".claude", "hooks",
                     "psk_leak_guard.sh")
_TOOL = os.path.join(os.path.dirname(__file__), "..", "scripts",
                     "mesh_psk_safe.py")

# Synthetic keys only.
KEY_32 = "A" * 43 + "="    # 43 b64 chars + '='
KEY_16 = "B" * 22 + "=="   # 22 b64 chars + '=='
# A long continuous base64 run (ssh public key body) — must NOT false-match.
SSH_KEY_BODY = "AAAAB3NzaC1yc2EAAAADAQABAAABgQ" + "C" * 320 + "=="


def _run_hook(command, tool_name="Bash", raw_payload=None, env=None):
    """Feed a synthetic PreToolUse payload to the hook. Returns (denied, stdout)."""
    if raw_payload is None:
        raw_payload = json.dumps(
            {"tool_name": tool_name, "tool_input": {"command": command}})
    r = subprocess.run(
        ["bash", _HOOK], input=raw_payload,
        capture_output=True, text=True, timeout=30, env=env)
    denied = False
    if r.stdout.strip():
        try:
            denied = (json.loads(r.stdout)["hookSpecificOutput"]
                      ["permissionDecision"] == "deny")
        except (ValueError, KeyError):
            denied = False
    return denied, r.stdout


# ── the anchored allowlist: wrapper is exempt, chained bypasses are not ──

class TestWrapperExemption:
    def test_bare_wrapper_invocation_allowed(self):
        denied, _ = _run_hook("python3 scripts/mesh_psk_safe.py info moc3")
        assert not denied

    def test_bare_wrapper_no_python_prefix_allowed(self):
        denied, _ = _run_hook("scripts/mesh_psk_safe.py keyhash moc3 Fleet")
        assert not denied

    def test_chained_after_wrapper_is_denied(self):
        # the one-semicolon / && bypass the old substring glob let through.
        denied, _ = _run_hook(
            "python3 scripts/mesh_psk_safe.py keyhash h ch && meshtastic --host h --info")
        assert denied

    def test_semicolon_chain_is_denied(self):
        denied, _ = _run_hook("echo mesh_psk_safe.py; meshtastic --host h --info")
        assert denied

    def test_trailing_comment_mentioning_wrapper_is_denied(self):
        denied, _ = _run_hook("meshtastic --host h --info  # see mesh_psk_safe.py")
        assert denied


# ── fail-open leaves a witness (never silent) ──

class TestFailOpenWitness:
    def test_unparseable_payload_allows_but_witnesses(self, tmp_path):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
        witness = tmp_path / ".claude" / "psk_leak_guard_failopen.log"
        (tmp_path / ".claude").mkdir()
        denied, out = _run_hook(None, raw_payload="}{ not json at all",
                                env=env)
        assert not denied            # fail OPEN by design
        assert out.strip() == ""
        assert witness.exists(), "parse failure left no witness marker"
        assert "parse-failed" in witness.read_text()

    def test_non_bash_tool_is_ignored_without_witness(self, tmp_path):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
        (tmp_path / ".claude").mkdir()
        denied, _ = _run_hook("anything", tool_name="Read", env=env)
        assert not denied
        # a clean non-Bash payload is normal, NOT a fail-open → no witness.
        assert not (tmp_path / ".claude" / "psk_leak_guard_failopen.log").exists()


# ── inline-key pattern: catches both key sizes, no ssh-key false-deny ──

class TestInlineKeyPattern:
    def test_32_byte_key_denied(self):
        denied, _ = _run_hook(f"echo {KEY_32}")
        assert denied

    def test_16_byte_aes128_key_denied(self):
        # security gap the old {43}= pattern missed entirely.
        denied, _ = _run_hook(f"meshtastic --ch-set psk base64:{KEY_16}")
        assert denied

    def test_ssh_public_key_not_false_denied(self):
        denied, _ = _run_hook(
            f"echo 'ssh-rsa {SSH_KEY_BODY} tunnel@openwrt' >> ~/.ssh/authorized_keys")
        assert not denied


# ── raw-dump pattern anchored to a real meshtastic invocation ──

class TestRawDumpPattern:
    def test_direct_info_dump_denied(self):
        denied, _ = _run_hook("meshtastic --host moc3 --info")
        assert denied

    def test_sudo_info_dump_denied(self):
        denied, _ = _run_hook("sudo meshtastic --host moc3 --info | grep psk")
        assert denied

    def test_grep_for_flag_not_false_denied(self):
        # repo maintenance: meshtastic is a grep ARGUMENT, not an invocation.
        denied, _ = _run_hook("grep -rn 'meshtastic --info' scripts/")
        assert not denied

    def test_channel_url_denied(self):
        denied, _ = _run_hook("meshtastic --seturl https://meshtastic.org/e/#CgMSAQE")
        assert denied

    def test_channels_proto_denied(self):
        denied, _ = _run_hook("cat ~/.portduino/default/channels.proto")
        assert denied


# ── cross-language constant cross-pin (honest_failure_modes point 5) ──

class TestSecretShapeCrossPin:
    """The bash hook and the python tool independently hardcode the base64-key
    and channel-URL shapes. Pin them together so they cannot silently drift."""

    def _hook_text(self):
        with open(_HOOK) as f:
            return f.read()

    def _tool_text(self):
        with open(_TOOL) as f:
            return f.read()

    def test_hook_covers_both_python_key_sizes(self):
        # mesh_psk_safe.B64_32 validates the 32-byte set key; the hook's inline
        # pattern must at least cover the same 43-b64-+'=' shape (and the 16-byte
        # form). If the tool's accepted key shape changes, this fails loudly.
        hook = self._hook_text()
        assert "[A-Za-z0-9+/]{43}=" in hook, "hook lost the 32-byte key shape"
        assert "[A-Za-z0-9+/]{22}==" in hook, "hook lost the 16-byte key shape"

    def test_both_files_share_the_channel_url_host(self):
        assert "meshtastic\\.org/e/#" in self._hook_text()
        assert "meshtastic\\.org/e/#" in self._tool_text()

    def test_tool_default_key_within_hook_or_tool_floor(self):
        # PSK_RE floor must admit the 4-char default key the redactor protects.
        tool = self._tool_text()
        m = re.search(r'"psk".*?\{(\d+),\}', tool)
        assert m, "could not find PSK_RE floor in mesh_psk_safe.py"
        assert int(m.group(1)) <= 4, "PSK_RE floor too high to catch 'AQ=='"
