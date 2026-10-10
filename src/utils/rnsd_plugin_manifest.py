"""What will rnsd exec from ``interfaces/`` on its next start? — read-only.

R5 (2026-10-10). A user-supplied interface module runs INSIDE rnsd: one that
panicked while loading took down every interface on the shared instance (SMCI
calls ``RNS.panic()`` when ``import meshcore`` fails; fork fix 0f8f637f makes
that cost only the interface). The other half is SEEING, per box, which
plugins rnsd will load. This applies rnsd's own rules (fork Reticulum.py):

* a stanza is brought up only if ``enabled`` / ``interface_enabled`` is
  present and true (``utils.rns_interface_flags.interface_enabled``);
* a ``type`` outside the built-in ``if c["type"] == …`` chain is looked up as
  ``<configdir>/interfaces/<type>.py`` and exec'd.

The built-in chain is DERIVED from the RNS source the check can import, never
hardcoded (two copies drift). A report, not an alarm (observe before alarm):
exit 0 = manifest produced, whatever it lists, or `inert` (no rnsd on the box
at all); exit 2 = UNKNOWN (no rnsd config found, unreadable, unparseable, or
no built-in list) — never "nothing loads". ⚠️ The built-ins come from THIS interpreter's RNS; rnsd may import a
different copy (a box has several RNS envs). The source path is printed.

    python3 src/utils/rnsd_plugin_manifest.py [--configdir DIR] [--json]
"""

if __name__ == "__main__" and __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Set, Tuple

from utils.rns_interface_flags import interface_enabled
from utils.safe_import import safe_import

_ConfigObj, _HAS_CONFIGOBJ = safe_import('RNS.vendor.configobj', 'ConfigObj')

_BUILTIN_RE = re.compile(r'c\["type"\]\s*==\s*"(\w+)"')


@dataclass
class Entry:
    stanza: str
    type: str
    path: str
    sha256: Optional[str] = None


@dataclass
class Manifest:
    status: str                      # "ok" | "unknown"
    configdir: str
    reason: str = ""
    builtin_source: str = ""
    will_load: List[Entry] = field(default_factory=list)
    missing: List[Entry] = field(default_factory=list)
    disabled: List[Entry] = field(default_factory=list)
    unreferenced: List[str] = field(default_factory=list)


def builtin_types_from_source(text: str) -> Optional[Set[str]]:
    """The built-in interface types in a Reticulum.py, or None if the chain
    is not there (a changed loader must read UNKNOWN, not 'all plugins')."""
    found = set(_BUILTIN_RE.findall(text or ""))
    return found or None


def installed_builtin_types() -> Tuple[Optional[Set[str]], str]:
    """(types, source path or reason) from the RNS this interpreter imports."""
    reticulum_mod, ok = safe_import('RNS.Reticulum')
    if not ok:
        return None, "RNS not importable by this interpreter"
    src = getattr(reticulum_mod, "__file__", "") or ""
    try:
        return builtin_types_from_source(Path(src).read_text()), src
    except OSError as e:
        return None, f"cannot read {src}: {e}"


def _rnsd_installed() -> bool:
    """Any trace of rnsd on this box? Conservative: one trace is enough to
    keep a not-running rnsd UNKNOWN (its next start loads from a config we
    could not resolve); only NO trace at all reads inert (lehua, 2026-10-10)."""
    import shutil
    from utils.paths import get_real_user_home
    if shutil.which("rnsd") or (get_real_user_home() / ".local/bin/rnsd").exists():
        return True
    units = ("/etc/systemd/system/rnsd.service", "/lib/systemd/system/rnsd.service",
             "/usr/lib/systemd/system/rnsd.service")
    return any(Path(u).exists() for u in units) or Path("/etc/reticulum/config").exists()


def _sha256(p: Path) -> Optional[str]:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def build_manifest(configdir: Path, builtin: Optional[Set[str]],
                   builtin_source: str = "") -> Manifest:
    configdir = Path(configdir)
    man = Manifest(status="unknown", configdir=str(configdir),
                   builtin_source=builtin_source)
    if not builtin:
        man.reason = "no built-in interface list (RNS source not readable) — every type would read as a plugin"
        return man
    cfg_file = configdir / "config"
    if not cfg_file.is_file():
        man.reason = f"no config at {cfg_file}"
        return man
    if not _HAS_CONFIGOBJ:
        man.reason = "RNS's vendored ConfigObj not importable — cannot parse as rnsd does"
        return man
    try:
        config = _ConfigObj(str(cfg_file))
    except Exception as e:  # ConfigObjError, OSError, UnicodeDecodeError
        man.reason = f"cannot parse {cfg_file}: {type(e).__name__}: {e}"
        return man

    ipath = configdir / "interfaces"
    referenced = set()
    for name, c in (config.get("interfaces") or {}).items():
        if not hasattr(c, "get"):
            continue
        itype = str(c.get("type", "")).strip()
        if not itype or itype in builtin:
            continue
        p = ipath / f"{itype}.py"
        referenced.add(p.name)
        e = Entry(stanza=name, type=itype, path=str(p))
        if not interface_enabled(c):
            man.disabled.append(e)
        elif p.is_file():
            e.sha256 = _sha256(p)
            man.will_load.append(e)
        else:
            man.missing.append(e)
    loaded = {Path(e.path).name for e in man.will_load}
    if ipath.is_dir():
        try:
            man.unreferenced = sorted(f.name for f in ipath.iterdir()
                                      if f.is_file() and f.name not in loaded)
        except OSError as e:
            man.reason = f"cannot list {ipath}: {e}"
            return man
    man.status = "ok"
    return man


def _render(man: Manifest) -> str:
    if man.status != "ok":
        return f"UNKNOWN — rnsd plugin manifest for {man.configdir}: {man.reason}"
    out = [f"rnsd plugin manifest — {man.configdir}",
           f"  built-ins from: {man.builtin_source or '(given)'}"]
    out.append(f"  WILL LOAD ({len(man.will_load)}) — exec'd inside rnsd on next start:")
    out += [f"    {e.type}  [{e.stanza}]  {e.path}  sha256={e.sha256}" for e in man.will_load] or ["    (none)"]
    if man.missing:
        out.append(f"  MISSING ({len(man.missing)}) — enabled, no module; rnsd logs an error and skips:")
        out += [f"    {e.type}  [{e.stanza}]  {e.path}" for e in man.missing]
    if man.disabled:
        out.append(f"  DISABLED STANZA ({len(man.disabled)}) — not loaded:")
        out += [f"    {e.type}  [{e.stanza}]" for e in man.disabled]
    if man.unreferenced:
        out.append(f"  PRESENT, NOT LOADED — inert, no action ({len(man.unreferenced)}): " + ", ".join(man.unreferenced))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="What rnsd will exec from interfaces/ (read-only).")
    ap.add_argument("--configdir", help="rnsd config dir (default: resolved from the running rnsd)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    builtin, src = installed_builtin_types()
    if a.configdir:
        configdir, how = Path(a.configdir), "given"
    else:
        from utils.config_drift import _get_rnsd_effective_config
        found, _pid, how = _get_rnsd_effective_config()
        if found is None and how == "rnsd_not_running" and not _rnsd_installed():
            print(json.dumps({"status": "inert", "reason": "rnsd not installed on this box"})
                  if a.json else "inert — rnsd not installed on this box (no binary, unit or /etc/reticulum/config); nothing to load")
            return 0
        if found is None:
            man = Manifest(status="unknown", configdir="?",
                           reason=f"rnsd config dir not resolvable ({how})")
            print(json.dumps(asdict(man)) if a.json else _render(man))
            return 2
        configdir = found
    man = build_manifest(configdir, builtin, builtin_source=src if builtin else "")
    if man.status != "ok" and not builtin:
        man.reason = f"{man.reason} ({src})"
    if a.json:
        d = asdict(man)
        d["configdir_from"] = how
        print(json.dumps(d))
    else:
        print(_render(man) + ("" if man.status != "ok" else f"\n  configdir from: {how}"))
    return 0 if man.status == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
