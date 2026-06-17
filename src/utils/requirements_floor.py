"""Single source of truth for the fleet's pip version FLOORS.

Two independent consumers compare an installed package version against the
FLEET BASELINE declared in ``requirements/*.txt``:

* ``utils.watchdog_probes_drift.probe_dep_version_drift`` — pages when a box's
  service-user env is BELOW the floor (a missed/failed update).
* ``updates.version_checker`` — the TUI "updates available" check: a component
  is a real laggard only when it is BELOW the reviewed floor, NOT merely when
  PyPI has moved past it (the phantom-update class, feedback_version_env_rigor).

If those two parsed the requirements file with even slightly different
semantics they would disagree about what "the floor" is — exactly the
two-constants-drift trap honest_failure_modes item 5 warns about (24,000 vs
24,576). This module is the ONE parser + ONE comparison both import, so the
floor constant and the below-floor test can never drift between them.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, Optional

# A requirements pin we treat as a floor: ``pkg>=X`` / ``pkg==X`` / ``pkg~=X``.
# Captures the package name and the bare version (stops at the first space,
# environment marker ``;``, extra ``,`` or comment ``#``). Anything else
# (bare ``pkg``, URL installs, ``<`` upper bounds) is intentionally ignored —
# it carries no floor we can compare against.
_REQ_FLOOR_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*(?:>=|==|~=)\s*([0-9][^\s;,#]*)")

# ``pkg>=2.7.9`` style core dotted version, lenient tail (``.postN`` etc.).
_DOTTED_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def default_core_requirements() -> Path:
    """Repo-relative path to ``requirements/core.txt`` — the file that carries
    the fleet baseline for meshtastic. ``src/utils/requirements_floor.py`` →
    ``<repo>/requirements/core.txt``."""
    return Path(__file__).resolve().parents[2] / "requirements" / "core.txt"


def read_requirement_floors(pkgs: Iterable[str], requirements_path) -> Dict[str, str]:
    """Parse minimum-version floors for ``pkgs`` from a requirements file.

    Returns ``{pkg_lower: floor}`` for the ``pkg>=X`` / ``pkg==X`` / ``pkg~=X``
    lines found, or ``{}`` when the file can't be read or none of ``pkgs``
    appear with a floor. Never raises — an unreadable SSOT is indeterminate
    (the caller must decide what an unknown floor means; it must NOT be read as
    "compliant").
    """
    want = {p.lower() for p in pkgs}
    floors: Dict[str, str] = {}
    try:
        with open(requirements_path) as fh:
            for raw in fh:
                m = _REQ_FLOOR_RE.match(raw)
                if m and m.group(1).lower() in want:
                    floors[m.group(1).lower()] = m.group(2)
    except OSError:
        return {}
    return floors


def read_floor(pkg: str, requirements_path=None) -> Optional[str]:
    """Convenience: the floor for a single ``pkg`` from core.txt (or a given
    path). ``None`` when no floor is parseable — never a guess."""
    path = requirements_path if requirements_path is not None else default_core_requirements()
    return read_requirement_floors([pkg], path).get(pkg.lower())


def _dotted_tuple(v) -> Optional[tuple]:
    """``2.7.9`` → ``(2, 7, 9)``; missing segments → 0. None if no dotted core."""
    m = _DOTTED_RE.match(str(v).lstrip("vV").strip())
    if not m:
        return None
    return tuple(int(x) if x else 0 for x in m.groups())


def version_below(have, floor) -> bool:
    """True iff installed ``have`` < ``floor``.

    Prefers ``packaging.version`` (handles ``2.7.10`` > ``2.7.9``, pre/post
    releases, ``+local`` correctly); falls back to a stdlib dotted-int tuple
    compare when ``packaging`` is unavailable. Anything unparseable, or either
    side missing, → ``False`` (never false-alarm — the conservative direction
    for both a watchdog page and a TUI "update available" badge).
    """
    if not have or not floor:
        return False
    try:
        from packaging.version import Version

        return Version(str(have)) < Version(str(floor))
    except Exception:
        pass
    ht, ft = _dotted_tuple(have), _dotted_tuple(floor)
    if ht is None or ft is None:
        return False
    return ht < ft
