"""User-unit templates: which ones a generic copier may deploy, and what is installed.

ONE rule for every copier of ``templates/systemd/*-user.service`` (update.sh,
the TUI Updates handler, install_noc.sh) and for the TUI audit pane.

Why (2026-10-21, MeshAnchor meshcore-chat): update.sh copied every
``*-user.service`` template verbatim into ``~/.config/systemd/user/``. A
template carrying an install-time placeholder (``__CHAT_PANE_EXEC__``,
rendered by its installer) overwrote the working unit on 2026-10-19; the
running process hid it ~40 h, and the next restart ran the literal token —
the unit cycled in auto-restart and nothing in the app said so. Three copiers
held the same verbatim copy; each guessing on its own is how one was fixed
and two were missed.

The rule — a template DECLARES itself, nobody sniffs:

    # MF-DEPLOY: rendered TOKEN by <installer, free text>

(TOKEN without its __ delimiters: the template then carries __TOKEN__
exactly once, on the line its installer substitutes.)

* declared ``rendered`` → never copied by a generic copier; its installer
  renders it. The audit renders it against the installed unit to judge it.
* an undeclared placeholder outside comments → ``refused``: never copied, and
  said loudly (an author forgot the declaration).
* unreadable → ``unreadable``: never copied (absence of evidence is not
  evidence of no token).
* otherwise → ``glob``: the generic copiers deploy it as before.

Byte-identical in MeshForge and MeshAnchor (parity_check).

CLI (for the shell copiers; exit 0 unless the plan itself could not be made):

    python3 -m utils.user_unit_templates plan <templates_dir>
        copy<TAB><template path><TAB><unit name>
        skip<TAB><unit name><TAB><kind><TAB><detail>
    python3 -m utils.user_unit_templates audit <templates_dir> [<user_unit_dir>]
        one line per template; exit 1 if any installed unit is RAW, 2 if any
        state could not be read, else 0.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

SUFFIX = "-user.service"
DECLARATION = re.compile(
    r"^#\s*MF-DEPLOY:\s*rendered\s+([A-Z0-9][A-Z0-9_]*)\s+by\s+(.+?)\s*$", re.M)
# Placeholder shapes seen in templates/installers. Lowercase __x__ is left out on
# purpose: it collides with Python dunders (__main__, __pycache__) in ExecStart.
PLACEHOLDER = re.compile(r"__[A-Z0-9][A-Z0-9_]*__|@[A-Z][A-Z0-9_]*@|\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}")

KIND_GLOB = "glob"
KIND_RENDERED = "rendered"
KIND_REFUSED = "refused"
KIND_UNREADABLE = "unreadable"

# installed-unit states
ABSENT = "absent"          # not installed here — inert, by design or not yet
CURRENT = "current"        # matches its template (rendered: after substitution)
STALE = "stale"            # installed, differs from what its template gives now
RAW = "raw"                # installed unit still carries a placeholder — BROKEN
UNKNOWN = "unknown"        # could not read / could not judge


def unit_name(template: Path) -> str:
    return template.name[: -len(SUFFIX)] + ".service"


def _code_lines(text: str) -> List[str]:
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", ";"))]


def placeholders_in_code(text: str) -> List[str]:
    """Placeholder tokens outside comment lines (systemd comments: # and ;)."""
    found: List[str] = []
    for ln in _code_lines(text):
        found.extend(PLACEHOLDER.findall(ln))
    return found


@dataclass(frozen=True)
class TemplateClass:
    template: Path
    unit: str
    kind: str
    token: Optional[str] = None
    installer: Optional[str] = None
    detail: str = ""


def classify_template(template: Path) -> TemplateClass:
    name = unit_name(template)
    try:
        text = template.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return TemplateClass(template, name, KIND_UNREADABLE, detail=f"cannot read template: {e}")
    m = DECLARATION.search(text)
    if m:
        token, installer = f"__{m.group(1)}__", m.group(2)
        if token not in placeholders_in_code(text):
            return TemplateClass(template, name, KIND_REFUSED, token, installer,
                                 f"declares {token} but no ExecStart/code line carries it")
        return TemplateClass(template, name, KIND_RENDERED, token, installer,
                             f"rendered by {installer}")
    tokens = placeholders_in_code(text)
    if tokens:
        return TemplateClass(template, name, KIND_REFUSED, tokens[0], None,
                             f"undeclared placeholder {tokens[0]} — add "
                             f"'# MF-DEPLOY: rendered {tokens[0].strip('_@{} ')} by <installer>'")
    return TemplateClass(template, name, KIND_GLOB)


def templates(templates_dir: Path) -> List[TemplateClass]:
    return [classify_template(p) for p in sorted(Path(templates_dir).glob("*" + SUFFIX))]


def render(cls: TemplateClass, installed_text: str) -> Optional[str]:
    """The template with its token replaced by the value the INSTALLED unit
    carries on the same line, or None when the installed unit has no line of
    that shape (it was rendered from another template revision)."""
    text = cls.template.read_text(encoding="utf-8")
    for ln in _code_lines(text):
        if cls.token and cls.token in ln:
            pre, post = ln.split(cls.token, 1)
            pat = re.compile("^" + re.escape(pre) + "(.+?)" + re.escape(post) + "$", re.M)
            m = pat.search(installed_text)
            return text.replace(cls.token, m.group(1)) if m else None
    return None


@dataclass(frozen=True)
class UnitAudit:
    cls: TemplateClass
    state: str
    detail: str = ""


def audit_unit(cls: TemplateClass, user_dir: Path) -> UnitAudit:
    path = Path(user_dir) / cls.unit
    if not path.exists():
        return UnitAudit(cls, ABSENT, "not installed here")
    try:
        installed = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return UnitAudit(cls, UNKNOWN, f"cannot read installed unit: {e}")
    raw = placeholders_in_code(installed)
    if raw:
        fix = (f"re-run {cls.installer}" if cls.installer
               else "re-run the installer that renders it")
        return UnitAudit(cls, RAW, f"installed unit carries {raw[0]} — it cannot start; {fix}")
    if cls.kind == KIND_UNREADABLE:
        return UnitAudit(cls, UNKNOWN, cls.detail)
    try:
        expected = render(cls, installed) if cls.kind == KIND_RENDERED else cls.template.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return UnitAudit(cls, UNKNOWN, f"cannot read template: {e}")
    if cls.kind == KIND_REFUSED and cls.installer is None:
        return UnitAudit(cls, UNKNOWN, cls.detail)
    if expected is None:
        return UnitAudit(cls, STALE, f"installed {cls.token} line no longer matches the template; re-run {cls.installer}")
    # Judged on the lines systemd executes: a comment edit (this module's own
    # declaration line included) is not drift.
    if _code_lines(expected) == _code_lines(installed):
        return UnitAudit(cls, CURRENT)
    how = f"re-run {cls.installer}" if cls.kind == KIND_RENDERED else "run update.sh"
    return UnitAudit(cls, STALE, f"differs from its template; {how}")


def audit(templates_dir: Path, user_dir: Path) -> List[UnitAudit]:
    return [audit_unit(c, user_dir) for c in templates(templates_dir)]


def summarize(rows: List[UnitAudit]):
    """(ok, verdict line, detail lines) for the TUI — verdict first, the
    installed units only (absent is inert), and the command that fixes each.
    ok is False when any installed unit is RAW, STALE or UNKNOWN: a template
    fix that did not reach the box is not "applied"."""
    installed = [r for r in rows if r.state != ABSENT]
    if not installed:
        return True, "no user-unit templates installed here", []
    n = {s: sum(1 for r in installed if r.state == s) for s in (RAW, STALE, UNKNOWN, CURRENT)}
    parts = []
    if n[RAW]:
        parts.append(f"{n[RAW]} BROKEN (raw placeholder — cannot start)")
    if n[STALE]:
        parts.append(f"{n[STALE]} stale vs template")
    if n[UNKNOWN]:
        parts.append(f"{n[UNKNOWN]} UNKNOWN (unreadable)")
    ok = not parts
    verdict = (f"all {n[CURRENT]} installed user units current" if ok
               else ", ".join(parts) + f"; {n[CURRENT]} current")
    order = {RAW: 0, UNKNOWN: 1, STALE: 2, CURRENT: 3}
    lines = [f"[{r.state.upper():7}] {r.cls.unit}" + (f" — {r.detail}" if r.detail else "")
             for r in sorted(installed, key=lambda r: (order[r.state], r.cls.unit))]
    return ok, verdict, lines


def _default_user_dir() -> Path:
    from utils.paths import get_real_user_home
    return get_real_user_home() / ".config" / "systemd" / "user"


def main(argv: List[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("plan", "audit"):
        print(__doc__.split("CLI", 1)[1], file=sys.stderr)
        return 2
    tdir = Path(argv[1])
    if not tdir.is_dir():
        print(f"no templates dir: {tdir}", file=sys.stderr)
        return 2
    if argv[0] == "plan":
        for c in templates(tdir):
            if c.kind == KIND_GLOB:
                print(f"copy\t{c.template}\t{c.unit}")
            else:
                print(f"skip\t{c.unit}\t{c.kind}\t{c.detail}")
        return 0
    rows = audit(tdir, Path(argv[2]) if len(argv) > 2 else _default_user_dir())
    for r in rows:
        print(f"{r.state}\t{r.cls.unit}\t{r.cls.kind}\t{r.detail}")
    if any(r.state == RAW for r in rows):
        return 1
    return 2 if any(r.state == UNKNOWN for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
