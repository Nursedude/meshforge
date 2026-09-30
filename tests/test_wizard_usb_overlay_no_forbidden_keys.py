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

import yaml

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT / "src"), str(_ROOT / "src" / "launcher_tui"), str(_ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.meshtasticd_templates import HAT_OVERLAY_FORBIDDEN_KEYS  # noqa: E402

EXAMPLES = _ROOT / "examples" / "configs"


# The wizard's own USB overlay writer (`usb_overlay_content`,
# `_create_usb_config`) was REMOVED 2026-09-30: meshtasticd has no `Serial:`
# key, so what it wrote configured nothing. The wizard now writes only
# sanitized CH341 `Lora:` overlays — tests/test_wizard_usb_routes_by_kind.py.


def test_overlay_examples_carry_no_forbidden_keys():
    # examples/README.md: `sudo cp examples/configs/meshtasticd-usb.yaml
    # /etc/meshtasticd/config.d/` — #58 by the documented path.
    files = sorted(EXAMPLES.glob("meshtasticd-*.yaml"))
    assert len(files) == 1, [f.name for f in files]   # the USB example was removed (B7)
    carrying = {f.name: sorted(HAT_OVERLAY_FORBIDDEN_KEYS & set(yaml.safe_load(f.read_text()) or {}))
                for f in files}
    assert all(not keys for keys in carrying.values()), carrying
