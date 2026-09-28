"""HAT overlay sanitizer — :9443 protection.

The moc3 incident on 2026-05-18 exposed an upstream-vendored HAT template
(`/etc/meshtasticd/available.d/lora-MeshAdv-900M30S.yaml` from
chrismyers2000/MeshAdv-Pi-Hat, bundled into meshtasticd 2.7.x) that ships
a stray `Webserver: Port: 443` block. Copying it into config.d/ — the
standard activation flow — silently moves meshtasticd's HTTPS API off
:9443, which breaks every MeshForge consumer that posts to
`/api/v1/toradio` (the gateway TX SSOT in `meshtastic_protobuf_client`).

These tests pin the sanitizer behavior so the next operator activating
this HAT can't reintroduce the regression.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import yaml

from launcher_tui.handlers.meshtasticd_config import (
    _HAT_OVERLAY_FORBIDDEN_KEYS,
    _sanitize_hat_overlay,
)


# Verbatim copy of the broken upstream template that moc3 had installed
# (modulo trailing whitespace). Kept inline so the regression is locked
# against the actual bytes that triggered the incident.
MOC3_BROKEN_TEMPLATE = """\
# MeshAdv-Pi E22-900M30S SPI Configuration (1W High-Power)
# Hardware: SX1262 (Ebyte E22-900M30S), +30dBm (1W)
# Source: meshtastic/firmware bin/config.d

Lora:
  Module: sx1262
  CS: 21
  IRQ: 16
  Busy: 20
  Reset: 18
  TXen: 13
  RXen: 12
  DIO3_TCXO_VOLTAGE: true

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
"""


class TestSanitizerStripsTheMoc3Webserver:
    """Pin the :9443-recovery contract against the exact template that bit moc3."""

    def test_webserver_block_is_stripped(self):
        sanitized, stripped = _sanitize_hat_overlay(MOC3_BROKEN_TEMPLATE)
        loaded = yaml.safe_load(sanitized) or {}
        assert "Webserver" not in loaded, (
            "Webserver block survived the sanitizer — moc3 incident would recur. "
            f"Got: {sorted(loaded)}"
        )
        assert "Webserver" in stripped, (
            f"Sanitizer didn't report Webserver in stripped list: {stripped}"
        )

    def test_port_443_is_not_anywhere_in_output(self):
        """Belt-and-suspenders: even if a future bug nested Webserver elsewhere,
        the actual byte 443 next to a Port: must not survive."""
        sanitized, _ = _sanitize_hat_overlay(MOC3_BROKEN_TEMPLATE)
        assert "Port: 443" not in sanitized, (
            "Port: 443 leaked through — meshtasticd will bind :443 instead "
            "of :9443 and break gateway TX SSOT"
        )

    def test_lora_block_survives_intact(self):
        """The whole point of HAT templates is the Lora block. It must NOT
        be touched by the sanitizer."""
        sanitized, _ = _sanitize_hat_overlay(MOC3_BROKEN_TEMPLATE)
        loaded = yaml.safe_load(sanitized)
        assert loaded["Lora"]["Module"] == "sx1262"
        assert loaded["Lora"]["CS"] == 21
        assert loaded["Lora"]["DIO3_TCXO_VOLTAGE"] is True


class TestSanitizerLeavesCleanTemplatesAlone:
    """A HAT overlay with only radio-config blocks must round-trip untouched."""

    def test_clean_lora_only_template_unchanged(self):
        clean = (
            "Lora:\n"
            "  Module: sx1262\n"
            "  CS: 21\n"
        )
        sanitized, stripped = _sanitize_hat_overlay(clean)
        assert stripped == []
        assert sanitized == clean, (
            "Sanitizer rewrote a clean template — preserve operator content "
            "(comments, formatting) when nothing needs stripping."
        )


class TestSanitizerFailsSafe:
    """Don't silently mangle on parse error or unexpected shape."""

    def test_malformed_yaml_passes_through(self):
        broken = "Lora:\n  - this is not\n    a valid: mapping\n   bad indent"
        sanitized, stripped = _sanitize_hat_overlay(broken)
        assert sanitized == broken
        assert stripped == []

    def test_non_mapping_top_level_passes_through(self):
        listy = "- one\n- two\n"
        sanitized, stripped = _sanitize_hat_overlay(listy)
        assert sanitized == listy
        assert stripped == []


class TestForbiddenKeysContract:
    """The set must include Webserver (the moc3 case). Locked here so a
    future cleanup can't quietly drop it."""

    def test_webserver_is_forbidden(self):
        assert "Webserver" in _HAT_OVERLAY_FORBIDDEN_KEYS, (
            "Webserver must stay in the forbidden set — see moc3 2026-05-18"
        )


class TestShippedTemplatesCarryNoWebserver:
    """TUI audit finding 10 (2026-09-28): the sanitizer only guards the TUI's
    activation path. The installer copies templates/available.d/ into
    /etc/meshtasticd/available.d/, and install_noc.sh, templates/config.yaml
    and the orchestrator all tell the operator to activate one with a raw
    `cp` into config.d/ — which never passes the sanitizer. All 33 shipped
    templates (and all 36 inline fallbacks) carried `Webserver: Port: 443`,
    so the documented manual path re-created the moc3 incident. Measured the
    same day: meshanchor-server's config.d/usb-serial.yaml still carries it
    (inert only because meshtasticd is not installed there)."""

    REPO = Path(__file__).parent.parent

    def test_repo_available_d_templates(self):
        files = sorted((self.REPO / "templates" / "available.d").glob("*.yaml"))
        assert files, "no templates found — the glob is aimed wrong"
        carrying = [f.name for f in files
                    if "Webserver" in (yaml.safe_load(f.read_text()) or {})]
        assert carrying == []

    def test_inline_fallback_templates(self):
        from core.meshtasticd_templates import RADIO_TEMPLATES
        assert RADIO_TEMPLATES
        carrying = [k for k, v in RADIO_TEMPLATES.items()
                    if "Webserver" in (yaml.safe_load(v["config"]) or {})]
        assert carrying == []

    def test_spi_hat_generator(self):
        from config.spi_hats import SPIHatConfigurator
        gen = SPIHatConfigurator.generate_config_yaml
        out = gen(SPIHatConfigurator.__new__(SPIHatConfigurator),
                  {"hat": "x", "module": "sx1262"})
        assert "Webserver" not in (yaml.safe_load(out) or {})


class TestEveryActivationPathSanitizes:
    """2026-09-28: the sanitizer guarded ONE of three activation paths. The
    first-run wizard (two copies) and the config file manager copied
    available.d templates into config.d/ raw — including the upstream
    package's templates, which is exactly where moc3's 443 came from."""

    def test_handler_uses_the_core_sanitizer(self):
        from core.meshtasticd_templates import sanitize_hat_overlay
        assert _sanitize_hat_overlay is sanitize_hat_overlay

    def test_wizard_install_strips_webserver(self, tmp_path):
        from launcher_tui.handlers.first_run import _install_overlay
        src = tmp_path / "lora-MeshAdv-900M30S.yaml"
        src.write_text(MOC3_BROKEN_TEMPLATE)
        dst = tmp_path / "config.d.yaml"
        stripped = _install_overlay(src, dst)
        assert "Webserver" in stripped
        loaded = yaml.safe_load(dst.read_text())
        assert "Webserver" not in loaded and "Lora" in loaded

    def test_no_raw_template_copy_into_config_d(self):
        # Source guard: the activation paths may not copy a template raw.
        root = Path(__file__).parent.parent / "src"
        for rel in ("launcher_tui/handlers/first_run.py",
                    "config/config_file_manager.py"):
            text = (root / rel).read_text()
            assert "sanitize_hat_overlay" in text, rel
        wizard = (root / "launcher_tui/handlers/first_run.py").read_text()
        assert "shutil.copy2(src, dst)" not in wizard
        assert "shutil.copy2(source, dest)" not in wizard
        cfm = (root / "config/config_file_manager.py").read_text()
        activate = cfm[cfm.index('"""Select a config from available.d'):]
        activate = activate[:activate.index("\n    def ")]
        assert "shutil.copy2" not in activate
