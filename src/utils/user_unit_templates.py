"""User-unit templates: which ones a generic copier may deploy, and what is installed.

ONE rule for every copier of ``templates/systemd/*-user.service`` (update.sh,
the TUI Updates handler, install_noc.sh, provision_role) and for the TUI
"User Unit Templates" pane.

Why (2026-10-21, MeshAnchor meshcore-chat): update.sh copied every
``*-user.service`` template verbatim into ``~/.config/systemd/user/``. A
template carrying an install-time placeholder (``__CHAT_PANE_EXEC__``,
rendered by its installer) overwrote the working unit on 2026-10-19; the
running process hid it ~40 h, and the next restart ran the literal token —
the unit cycled in auto-restart and nothing in the app said so. Four copiers
held the same verbatim copy; each guessing on its own is how one was fixed
and three were missed.

The rule — a template DECLARES itself, nobody sniffs:

    # MF-DEPLOY: rendered TOKEN by <installer, free text>
    # MF-DEPLOY-AS: <unit>.service      (optional: the installed name, when
                                         it is not <template minus -user>)

(TOKEN without its __ delimiters: the template then carries __TOKEN__
exactly once, on the line its installer substitutes.)

* declared ``rendered`` → never copied by a generic copier; its installer
  renders it. The audit renders it against the installed unit to judge it.
* a plain template whose unit NAME a rendered template also installs to →
  ``shadowed``: never raw-copied (it would overwrite the rendered unit —
  MeshAnchor's nomadnet-tmux installs as nomadnet.service).
* an undeclared placeholder outside comments, or a declaration whose token no
  code line carries → ``refused``: never copied, and said loudly.
* unreadable → ``unreadable``: never copied (absence of evidence is not
  evidence of no token).
* otherwise → ``glob``: the generic copiers deploy it as before.

The audit is per INSTALLED UNIT NAME against every template here that can
produce it. Only ``raw`` (the unit cannot start) and ``unknown`` (could not
judge) are failures. ``differs`` is ADVISORY: a box carrying MeshForge AND
MeshAnchor shares one ~/.config/systemd/user, so a unit the other app
installed legitimately differs from this checkout's template — the pane says
so and names the fix only "if this app owns it".

Byte-identical in MeshForge and MeshAnchor (parity_check).

CLI (for the shell copiers; exit 0 unless the plan itself could not be made):

    python3 -m utils.user_unit_templates plan <templates_dir>
        copy<TAB><template path><TAB><unit name>
        skip<TAB><unit name><TAB><kind><TAB><detail>
    python3 -m utils.user_unit_templates audit <templates_dir> [<user_unit_dir>]
        one line per installed-or-templated unit; exit 1 if any installed unit
        is RAW, 2 if any could not be judged, else 0.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

SUFFIX = "-user.service"
DECLARATION = re.compile(
    r"^#\s*MF-DEPLOY:\s*rendered\s+([A-Z0-9][A-Z0-9_]*)\s+by\s+(.+?)\s*$", re.M)
INSTALLS_AS = re.compile(r"^#\s*MF-DEPLOY-AS:\s*([A-Za-z0-9@._-]+\.service)\s*$", re.M)
# Placeholder shapes seen in templates/installers. Lowercase __x__ is left out on
# purpose: it collides with Python dunders (__main__, __pycache__) in ExecStart.
PLACEHOLDER = re.compile(r"__[A-Z0-9][A-Z0-9_]*__|@[A-Z][A-Z0-9_]*@|\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}")

KIND_GLOB = "glob"
KIND_RENDERED = "rendered"
KIND_SHADOWED = "shadowed"
KIND_REFUSED = "refused"
KIND_UNREADABLE = "unreadable"
KIND_ORPHAN = "orphan"         # an installed unit no template here produces (audit only)

# installed-unit states
ABSENT = "absent"          # not installed here — inert, by design or not yet
MASKED = "masked"          # symlink to /dev/null — the operator's decision, inert
CURRENT = "current"        # matches a template here (rendered: after substitution)
DIFFERS = "differs"        # installed, matches no template here — ADVISORY
RAW = "raw"                # installed unit still carries a placeholder — BROKEN
UNKNOWN = "unknown"        # could not judge (unreadable unit or template)

FAILING = (RAW, UNKNOWN)


def _code_lines(text: str) -> List[str]:
    """The lines systemd acts on: no comments (# and ;), no blank lines, no
    trailing whitespace / CR — a comment or spacing edit is not drift."""
    out = []
    for ln in text.splitlines():
        s = ln.rstrip()
        if s and not s.lstrip().startswith(("#", ";")):
            out.append(s)
    return out


def placeholders_in_code(text: str) -> List[str]:
    """Placeholder tokens outside comment lines."""
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
    default = template.name[: -len(SUFFIX)] + ".service"
    try:
        text = template.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return TemplateClass(template, default, KIND_UNREADABLE, detail=f"cannot read template: {e}")
    m_as = INSTALLS_AS.search(text)
    name = m_as.group(1) if m_as else default
    m = DECLARATION.search(text)
    if m:
        token, installer = f"__{m.group(1)}__", m.group(2)
        if token not in placeholders_in_code(text):
            return TemplateClass(template, name, KIND_REFUSED, token, installer,
                                 f"{template.name} declares {m.group(1)} but no code line "
                                 f"carries {token} — fix the template's declaration")
        return TemplateClass(template, name, KIND_RENDERED, token, installer,
                             f"rendered by {installer}")
    tokens = placeholders_in_code(text)
    if tokens:
        return TemplateClass(template, name, KIND_REFUSED, tokens[0], None,
                             f"{template.name} has an undeclared placeholder {tokens[0]} — "
                             f"add '# MF-DEPLOY: rendered {tokens[0].strip('_@{} ')} by <installer>'")
    return TemplateClass(template, name, KIND_GLOB)


def templates(templates_dir: Path) -> List[TemplateClass]:
    classes = [classify_template(p) for p in sorted(Path(templates_dir).glob("*" + SUFFIX))]
    rendered_units = {c.unit: c for c in classes if c.kind == KIND_RENDERED}
    out = []
    for c in classes:
        r = rendered_units.get(c.unit)
        if c.kind == KIND_GLOB and r is not None:
            c = TemplateClass(c.template, c.unit, KIND_SHADOWED, None, r.installer,
                              f"{c.unit} is also rendered by {r.installer} "
                              f"({r.template.name}); a raw copy would overwrite it")
        out.append(c)
    return out


def render(cls: TemplateClass, installed_text: str) -> Optional[str]:
    """The template with its token replaced by the value the INSTALLED unit
    carries on the same line, or None when the installed unit has no line of
    that shape (another template, or another revision, produced it)."""
    text = cls.template.read_text(encoding="utf-8")
    for ln in _code_lines(text):
        if cls.token and cls.token in ln:
            pre, post = ln.split(cls.token, 1)
            pat = re.compile("^" + re.escape(pre) + "(.+?)" + re.escape(post) + r"\s*$", re.M)
            m = pat.search(installed_text)
            return text.replace(cls.token, m.group(1)) if m else None
    return None


@dataclass(frozen=True)
class UnitAudit:
    cls: TemplateClass           # the template the verdict is about (first candidate)
    state: str
    detail: str = ""


def _fixes(cands: Sequence[TemplateClass]) -> str:
    out = []
    for c in cands:
        if c.kind == KIND_RENDERED:
            out.append(f"re-run {c.installer}")
        elif c.kind == KIND_GLOB:
            out.append(f"run update.sh ({c.template.name})")
    return " or ".join(dict.fromkeys(out)) or "re-run the installer that renders it"


def audit_unit(cands: Union[TemplateClass, Sequence[TemplateClass]], user_dir: Path) -> UnitAudit:
    cands = [cands] if isinstance(cands, TemplateClass) else list(cands)
    head = cands[0]
    path = Path(user_dir) / head.unit
    if path.is_symlink() and os.path.realpath(path) == "/dev/null":
        return UnitAudit(head, MASKED, "masked (symlink to /dev/null) — the operator's choice")
    if not path.exists():
        return UnitAudit(head, ABSENT, "not installed here")
    try:
        installed = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return UnitAudit(head, UNKNOWN, f"cannot read installed unit: {e}")
    raw = placeholders_in_code(installed)
    if raw:
        return UnitAudit(head, RAW, f"installed unit carries {raw[0]} — it cannot start; "
                                    f"{_fixes([c for c in cands if c.kind == KIND_RENDERED] or cands)}")
    judgeable = [c for c in cands if c.kind in (KIND_GLOB, KIND_SHADOWED, KIND_RENDERED)]
    if not judgeable:
        return UnitAudit(head, UNKNOWN, "; ".join(c.detail for c in cands))
    for c in judgeable:
        try:
            expected = (render(c, installed) if c.kind == KIND_RENDERED
                        else c.template.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as e:
            return UnitAudit(c, UNKNOWN, f"cannot read template: {e}")
        if expected is not None and _code_lines(expected) == _code_lines(installed):
            return UnitAudit(c, CURRENT)
    names = ", ".join(c.template.name for c in judgeable)
    return UnitAudit(head, DIFFERS,
                     f"matches no template here ({names}) — another app's unit or an older "
                     f"render; if this app owns it: {_fixes(judgeable)}")


def audit(templates_dir: Path, user_dir: Path) -> List[UnitAudit]:
    by_unit: Dict[str, List[TemplateClass]] = {}
    for c in templates(templates_dir):
        by_unit.setdefault(c.unit, []).append(c)
    # rendered candidates first: their installer is the fix a RAW unit needs
    order = {KIND_RENDERED: 0, KIND_GLOB: 1, KIND_SHADOWED: 2, KIND_REFUSED: 3, KIND_UNREADABLE: 4}
    rows = [audit_unit(sorted(cs, key=lambda c: order[c.kind]), user_dir)
            for _, cs in sorted(by_unit.items())]
    # A raw unit no template here produces is still a unit that cannot start
    # (a leftover from an older install name, or the other app's) — say so.
    for path in sorted(Path(user_dir).glob("*.service")) if Path(user_dir).is_dir() else []:
        if path.name in by_unit or path.is_symlink():
            continue
        try:
            raw = placeholders_in_code(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if raw:
            orphan = TemplateClass(path, path.name, KIND_ORPHAN)
            rows.append(UnitAudit(orphan, RAW, f"carries {raw[0]} and no template here installs "
                                               f"{path.name} — a leftover or another app's unit; "
                                               f"it cannot start as written"))
    return rows


def summarize(rows: List[UnitAudit]):
    """(ok, verdict line, detail lines) for the TUI — verdict first, the
    installed units only (absent/masked are inert), and the fix for each.
    ok is False only for RAW (cannot start) and UNKNOWN (could not judge);
    DIFFERS is advisory, never a FAIL — on a box shared by both apps it is
    the other app's unit as often as drift."""
    installed = [r for r in rows if r.state not in (ABSENT, MASKED)]
    if not installed:
        return True, "no user-unit templates installed here", []
    n = {s: sum(1 for r in installed if r.state == s) for s in (RAW, UNKNOWN, DIFFERS, CURRENT)}
    parts = []
    if n[RAW]:
        parts.append(f"{n[RAW]} BROKEN (raw placeholder — cannot start)")
    if n[UNKNOWN]:
        parts.append(f"{n[UNKNOWN]} UNKNOWN (could not judge)")
    if n[DIFFERS]:
        parts.append(f"{n[DIFFERS]} differ from this checkout's templates (advisory)")
    ok = not (n[RAW] or n[UNKNOWN])
    verdict = (", ".join(parts) + f"; {n[CURRENT]} current" if parts
               else f"all {n[CURRENT]} installed user units current")
    order = {RAW: 0, UNKNOWN: 1, DIFFERS: 2, CURRENT: 3}
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
