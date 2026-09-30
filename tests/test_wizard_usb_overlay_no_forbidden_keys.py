"""The setup wizard's USB overlay and the shipped overlay examples must not
carry keys that override config.yaml (#58; 2026-09-29 double-tap finding 2).

`_create_usb_config` built `config.d/usb-serial.yaml` inline with `TCP:`,
`Webserver: Port: 443` and `Logging:` and wrote it raw — the activation
sanitizer (a3d9e924) covers TEMPLATE paths, and this one is not a template.
The two `examples/configs/meshtasticd-*.yaml` files say "copy to config.d/"
and carried the same blocks. MeshAnchor fixed both in e40d6685; the fix never
travelled back. The forbidden-key set is the ONE constant the sanitizer and
Config Doctor already share, so the three consumers cannot drift apart.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import yaml

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT / "src"), str(_ROOT / "src" / "launcher_tui"), str(_ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.meshtasticd_templates import HAT_OVERLAY_FORBIDDEN_KEYS  # noqa: E402
from handler_test_utils import make_handler_context  # noqa: E402

EXAMPLES = _ROOT / "examples" / "configs"


def test_usb_overlay_content_carries_only_serial():
    from handlers.first_run import usb_overlay_content
    assert yaml.safe_load(usb_overlay_content("/dev/ttyUSB0")) == {
        "Serial": {"Device": "/dev/ttyUSB0"}}


def test_wizard_writes_no_forbidden_key_through_its_real_path(tmp_path):
    """Drive `_create_usb_config` itself — the consumer path — with the
    config.d directory redirected to tmp, and read back what it WROTE."""
    from handlers import first_run

    real_path = first_run.Path

    def redirect(p, *a):
        s = str(p)
        if s.startswith("/etc/meshtasticd"):
            return real_path(tmp_path) / s.lstrip("/")
        return real_path(p, *a)

    h = first_run.FirstRunHandler()
    h.set_context(make_handler_context())
    with patch.object(first_run, "Path", side_effect=redirect), \
         patch.object(first_run, "apply_config_and_restart", return_value=(True, "ok")):
        h._create_usb_config("/dev/ttyACM0")

    written = list(tmp_path.rglob("usb-serial.yaml"))
    assert len(written) == 1, written          # the write happened, and only once
    loaded = yaml.safe_load(written[0].read_text())
    assert sorted(HAT_OVERLAY_FORBIDDEN_KEYS & set(loaded)) == [], loaded
    assert loaded["Serial"]["Device"] == "/dev/ttyACM0"


def test_overlay_examples_carry_no_forbidden_keys():
    # examples/README.md: `sudo cp examples/configs/meshtasticd-usb.yaml
    # /etc/meshtasticd/config.d/` — #58 by the documented path.
    files = sorted(EXAMPLES.glob("meshtasticd-*.yaml"))
    assert len(files) == 2, [f.name for f in files]
    carrying = {f.name: sorted(HAT_OVERLAY_FORBIDDEN_KEYS & set(yaml.safe_load(f.read_text()) or {}))
                for f in files}
    assert all(not keys for keys in carrying.values()), carrying
