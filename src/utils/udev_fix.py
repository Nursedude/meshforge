"""
Fix broken udev rules on Raspberry Pi OS.

Detects and fixes the known alsa-utils packaging bug where
90-alsa-restore.rules contains GOTO targets without matching LABELs.

Fix creates an override in /etc/udev/rules.d/ (never modifies package files
in /usr/lib/udev/rules.d/).
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import List, Tuple

from utils.service_check import _sudo_cmd, _sudo_write

logger = logging.getLogger(__name__)

ALSA_RULES_PKG = Path("/usr/lib/udev/rules.d/90-alsa-restore.rules")
ALSA_RULES_OVERRIDE = Path("/etc/udev/rules.d/90-alsa-restore.rules")


def check_broken_udev_rules() -> List[str]:
    """Return list of GOTO labels missing a matching LABEL in ALSA udev rules.

    Returns empty list if the file doesn't exist, is already overridden,
    or has no broken references.
    """
    if ALSA_RULES_OVERRIDE.exists():
        return []  # Already overridden

    if not ALSA_RULES_PKG.exists():
        return []  # Not applicable (no ALSA rules)

    try:
        content = ALSA_RULES_PKG.read_text()
    except OSError as e:
        logger.warning("Cannot read %s: %s", ALSA_RULES_PKG, e)
        return []

    gotos = set(re.findall(r'GOTO="([^"]+)"', content))
    labels = set(re.findall(r'LABEL="([^"]+)"', content))
    return sorted(gotos - labels)


def fix_broken_udev_rules() -> Tuple[bool, str]:
    """Detect and fix broken GOTO labels in ALSA udev rules.

    Reads the package-provided rules file, identifies any GOTO targets
    without a matching LABEL, and creates a corrected override in
    /etc/udev/rules.d/.

    Returns:
        Tuple of (success, message).
    """
    if ALSA_RULES_OVERRIDE.exists():
        return True, "ALSA udev rules already have an override in /etc/udev/rules.d/"

    missing = check_broken_udev_rules()
    if not missing:
        return True, "ALSA udev rules are OK (no broken GOTO labels)"

    logger.info("Broken GOTO labels in %s: %s", ALSA_RULES_PKG, missing)

    try:
        content = ALSA_RULES_PKG.read_text()
    except OSError as e:
        return False, f"Cannot read {ALSA_RULES_PKG}: {e}"

    # Insert missing LABELs before the final LABEL (or at EOF)
    lines = content.splitlines(keepends=True)
    insert_lines = [f'LABEL="{label}"\n' for label in missing]

    # Find last LABEL line to insert before it
    last_label_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if re.match(r'^LABEL="', lines[i].strip()):
            last_label_idx = i
            break

    if last_label_idx is not None:
        for offset, line in enumerate(insert_lines):
            lines.insert(last_label_idx + offset, line)
    else:
        lines.extend(insert_lines)

    corrected = "".join(lines)

    success, msg = _sudo_write(str(ALSA_RULES_OVERRIDE), corrected)
    if not success:
        return False, f"Failed to write override: {msg}"

    # Reload udev rules
    try:
        subprocess.run(
            _sudo_cmd(["udevadm", "control", "--reload-rules"]),
            capture_output=True,
            timeout=10,
        )
        logger.info("udev rules reloaded after ALSA fix")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("udevadm reload failed (non-fatal): %s", e)

    labels_str = ", ".join(missing)
    return True, f"Fixed ALSA udev rules: added missing LABEL(s) {labels_str}"
