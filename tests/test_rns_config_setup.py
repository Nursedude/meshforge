"""Tests for utils/rns_config_setup.py — rpc_key install-time guard.

The guard is load-bearing for Issue #41 (each box gets its own
per-box rpc_key; no silent copy-paste of other people's keys). These
tests lock the contract:

  - generate_rpc_key yields valid 64-hex lowercase, unique per call
  - render_template substitutes __RPC_KEY__ or inserts under [reticulum]
  - ensure_rpc_key preserves valid keys, fills in missing ones,
    substitutes __RPC_KEY__ placeholders, and refuses to silently
    replace invalid keys (unless force=True)
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.rns_config_setup import (
    PLACEHOLDER,
    ensure_rpc_key,
    generate_rpc_key,
    is_valid_key,
    render_template,
)


class TestGenerateRpcKey:
    def test_length_is_64(self):
        assert len(generate_rpc_key()) == 64

    def test_all_lowercase_hex(self):
        key = generate_rpc_key()
        assert is_valid_key(key)
        assert key == key.lower()

    def test_unique_across_calls(self):
        keys = {generate_rpc_key() for _ in range(200)}
        # 200 random 32-byte draws collide with probability ~0; any
        # duplicate means the CSPRNG is broken.
        assert len(keys) == 200


class TestIsValidKey:
    def test_valid(self):
        assert is_valid_key("a" * 64)
        assert is_valid_key("0123456789abcdef" * 4)

    def test_rejects_uppercase(self):
        assert not is_valid_key("A" * 64)

    def test_rejects_short(self):
        assert not is_valid_key("a" * 63)

    def test_rejects_long(self):
        assert not is_valid_key("a" * 65)

    def test_rejects_non_hex(self):
        assert not is_valid_key("g" * 64)

    def test_rejects_empty(self):
        assert not is_valid_key("")


class TestRenderTemplate:
    def test_substitutes_placeholder(self):
        tpl = (
            "[reticulum]\n"
            "  share_instance = Yes\n"
            f"  rpc_key = {PLACEHOLDER}\n"
        )
        out = render_template(tpl)
        assert PLACEHOLDER not in out
        # Fresh key lands in the same spot
        assert "rpc_key = " in out
        # Extract the key and verify format
        key_line = [line for line in out.splitlines()
                    if 'rpc_key' in line][0]
        key = key_line.split('=', 1)[1].strip()
        assert is_valid_key(key)

    def test_substitutes_with_explicit_key(self):
        tpl = f"[reticulum]\n  rpc_key = {PLACEHOLDER}\n"
        explicit = "a" * 64
        out = render_template(tpl, rpc_key=explicit)
        assert f"rpc_key = {explicit}" in out
        assert PLACEHOLDER not in out

    def test_rejects_invalid_explicit_key(self):
        with pytest.raises(ValueError):
            render_template("anything", rpc_key="not-hex")
        with pytest.raises(ValueError):
            render_template("anything", rpc_key="ABCD" * 16)  # uppercase

    def test_inserts_under_reticulum_when_placeholder_absent(self):
        tpl = (
            "[reticulum]\n"
            "  share_instance = Yes\n"
            "  shared_instance_port = 37428\n"
            "\n"
            "[logging]\n"
            "  loglevel = 4\n"
        )
        out = render_template(tpl)
        lines = out.splitlines()
        # rpc_key line must exist, be inside [reticulum], before [logging]
        ret_idx = next(i for i, line in enumerate(lines)
                       if line.strip() == "[reticulum]")
        log_idx = next(i for i, line in enumerate(lines)
                       if line.strip() == "[logging]")
        key_idx = next(i for i, line in enumerate(lines)
                       if 'rpc_key' in line)
        assert ret_idx < key_idx < log_idx
        # And it's a real key
        key = lines[key_idx].split('=', 1)[1].strip()
        assert is_valid_key(key)

    def test_inserts_when_reticulum_is_last_section(self):
        tpl = (
            "[interfaces]\n"
            "  # stuff\n"
            "\n"
            "[reticulum]\n"
            "  share_instance = Yes\n"
        )
        out = render_template(tpl)
        assert "rpc_key = " in out
        # Insertion comes after share_instance, not above [interfaces]
        lines = out.splitlines()
        ret_idx = next(i for i, line in enumerate(lines)
                       if line.strip() == "[reticulum]")
        key_idx = next(i for i, line in enumerate(lines)
                       if 'rpc_key' in line)
        assert key_idx > ret_idx

    def test_creates_reticulum_section_when_absent(self):
        tpl = "[interfaces]\n  # stuff\n"
        out = render_template(tpl)
        assert "[reticulum]" in out
        assert "rpc_key = " in out


class TestEnsureRpcKey:
    def test_preserves_valid_key(self, tmp_path):
        existing_key = "b" * 64
        cfg = tmp_path / "config"
        cfg.write_text(
            f"[reticulum]\n  share_instance = Yes\n  rpc_key = {existing_key}\n"
        )
        mtime_before = cfg.stat().st_mtime
        key, generated = ensure_rpc_key(cfg)
        assert key == existing_key
        assert generated is False
        # File untouched
        assert cfg.stat().st_mtime == mtime_before

    def test_substitutes_placeholder(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            f"[reticulum]\n  share_instance = Yes\n"
            f"  rpc_key = {PLACEHOLDER}\n"
        )
        key, generated = ensure_rpc_key(cfg)
        assert generated is True
        assert is_valid_key(key)
        text = cfg.read_text()
        assert PLACEHOLDER not in text
        assert f"rpc_key = {key}" in text

    def test_inserts_when_missing(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            "[reticulum]\n  share_instance = Yes\n  shared_instance_port = 37428\n"
        )
        key, generated = ensure_rpc_key(cfg)
        assert generated is True
        assert is_valid_key(key)
        assert f"rpc_key = {key}" in cfg.read_text()

    def test_idempotent(self, tmp_path):
        """Second call must NOT regenerate the key."""
        cfg = tmp_path / "config"
        cfg.write_text("[reticulum]\n  share_instance = Yes\n")
        key1, gen1 = ensure_rpc_key(cfg)
        key2, gen2 = ensure_rpc_key(cfg)
        assert gen1 is True
        assert gen2 is False
        assert key1 == key2

    def test_preserves_invalid_key_without_force(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            "[reticulum]\n  share_instance = Yes\n  rpc_key = not-hex\n"
        )
        key, generated = ensure_rpc_key(cfg, force=False)
        assert generated is False
        # Returned value is the broken string so Config Doctor can flag it
        assert key == "not-hex"
        # File untouched
        assert "rpc_key = not-hex" in cfg.read_text()

    def test_force_replaces_invalid_key(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            "[reticulum]\n  share_instance = Yes\n  rpc_key = bogus\n"
        )
        key, generated = ensure_rpc_key(cfg, force=True)
        assert generated is True
        assert is_valid_key(key)
        text = cfg.read_text()
        assert "rpc_key = bogus" not in text
        assert f"rpc_key = {key}" in text

    def test_skips_commented_key_line(self, tmp_path):
        """A commented rpc_key shouldn't be treated as present."""
        cfg = tmp_path / "config"
        cfg.write_text(
            "[reticulum]\n  share_instance = Yes\n"
            f"  # rpc_key = {'c' * 64}\n"
        )
        key, generated = ensure_rpc_key(cfg)
        assert generated is True
        assert is_valid_key(key)
        # The comment line stays, and a new uncommented line is added
        text = cfg.read_text()
        assert f"# rpc_key = {'c' * 64}" in text
        assert f"rpc_key = {key}" in text


class TestShippedTemplate:
    """The repo's templates/reticulum.conf must carry exactly one
    __RPC_KEY__ placeholder so the installer substitutes it at
    install time instead of silently leaving a broken config."""

    def test_template_has_exactly_one_placeholder(self):
        import pathlib
        tpl = (
            pathlib.Path(__file__).resolve().parent.parent
            / 'templates' / 'reticulum.conf'
        )
        text = tpl.read_text()
        occurrences = text.count(PLACEHOLDER)
        # One literal placeholder on the rpc_key line. Comments explain
        # what the placeholder is but must not include the raw token
        # (same invariant as Issue #45's nomadnet-user.service template).
        assert occurrences == 1, (
            f"{tpl} has {occurrences} occurrences of {PLACEHOLDER} "
            f"(expected exactly 1)"
        )

    def test_template_placeholder_is_on_rpc_key_line(self):
        import pathlib
        tpl = (
            pathlib.Path(__file__).resolve().parent.parent
            / 'templates' / 'reticulum.conf'
        )
        text = tpl.read_text()
        # The rpc_key line must match `rpc_key = __RPC_KEY__`
        assert f"rpc_key = {PLACEHOLDER}" in text

    def test_render_produces_valid_config(self):
        """Rendering the real template must produce a usable RNS config."""
        import pathlib
        tpl = (
            pathlib.Path(__file__).resolve().parent.parent
            / 'templates' / 'reticulum.conf'
        )
        out = render_template(tpl.read_text())
        assert PLACEHOLDER not in out
        # Must still have the key stanza
        assert "[reticulum]" in out
        assert "share_instance = Yes" in out
        # rpc_key line must be there and carry a valid key
        key_line = [line for line in out.splitlines()
                    if line.strip().startswith('rpc_key ')
                    and not line.strip().startswith('#')]
        assert len(key_line) == 1
        key = key_line[0].split('=', 1)[1].strip()
        assert is_valid_key(key)
