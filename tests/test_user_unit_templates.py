"""utils.user_unit_templates — the one rule every user-unit copier obeys.

Byte-identical in MeshForge and MeshAnchor. Born 2026-10-21: three copiers of
templates/systemd/*-user.service each guessed whether a template was safe to
copy raw; update.sh's guess overwrote meshcore-chat's rendered unit.
"""
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from utils import user_unit_templates as uut  # noqa: E402

DECL = "# MF-DEPLOY: rendered PANE_EXEC by scripts/install_pane.sh\n"
RENDERED_TMPL = DECL + "[Service]\nExecStart=/usr/bin/tmux new -d -s p '__PANE_EXEC__'\n"
GLOB_TMPL = "# a plain unit\n[Service]\nExecStart=/bin/true\n"


def _tmpl(tmp_path, name, text):
    d = tmp_path / "t"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(text)
    return p


# ── classification ──────────────────────────────────────────────────────

def test_a_plain_template_is_glob(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "plain-user.service", GLOB_TMPL))
    assert (c.kind, c.unit) == (uut.KIND_GLOB, "plain.service")


def test_a_declared_template_is_rendered_and_names_its_installer(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "pane-user.service", RENDERED_TMPL))
    assert c.kind == uut.KIND_RENDERED
    assert (c.token, c.installer) == ("__PANE_EXEC__", "scripts/install_pane.sh")


def test_an_undeclared_placeholder_is_refused_and_says_how_to_declare(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "x-user.service",
                                    "[Service]\nExecStart=__X__\n"))
    assert c.kind == uut.KIND_REFUSED and "MF-DEPLOY: rendered X by" in c.detail


def test_other_placeholder_shapes_are_refused_too(tmp_path):
    for i, line in enumerate(("ExecStart=@PANE@", "ExecStart={{ pane }}", "ExecStart=__1X__")):
        c = uut.classify_template(_tmpl(tmp_path, f"s{i}-user.service", f"[Service]\n{line}\n"))
        assert c.kind == uut.KIND_REFUSED, line


def test_a_token_only_in_a_comment_is_not_a_placeholder(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "c-user.service",
                                    "# pairs with __NOMADNET_EXEC__ elsewhere\n" + GLOB_TMPL))
    assert c.kind == uut.KIND_GLOB


def test_python_dunders_are_not_placeholders(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "d-user.service",
                                    "[Service]\nExecStart=python3 -m pkg.__main__ __pycache__\n"))
    assert c.kind == uut.KIND_GLOB


def test_a_declaration_whose_token_no_code_line_carries_is_refused(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "e-user.service", DECL + GLOB_TMPL))
    assert c.kind == uut.KIND_REFUSED


def test_an_unreadable_template_is_never_glob(tmp_path):
    p = _tmpl(tmp_path, "u-user.service", "")
    p.write_bytes(b"\xff\xfe\x00bad")  # not UTF-8
    assert uut.classify_template(p).kind == uut.KIND_UNREADABLE


# ── installed-unit audit ────────────────────────────────────────────────

def test_absent_is_inert(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "pane-user.service", RENDERED_TMPL))
    (tmp_path / "u").mkdir()
    assert uut.audit_unit(c, tmp_path / "u").state == uut.ABSENT


def test_a_raw_installed_unit_reads_raw_and_names_the_installer(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "pane-user.service", RENDERED_TMPL))
    u = tmp_path / "u"
    u.mkdir()
    (u / "pane.service").write_text(RENDERED_TMPL)
    a = uut.audit_unit(c, u)
    assert a.state == uut.RAW and "install_pane.sh" in a.detail


def test_a_rendered_unit_matching_its_template_is_current(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "pane-user.service", RENDERED_TMPL))
    u = tmp_path / "u"
    u.mkdir()
    (u / "pane.service").write_text(RENDERED_TMPL.replace("__PANE_EXEC__", "/home/op/w.sh"))
    assert uut.audit_unit(c, u).state == uut.CURRENT


def test_a_comment_only_difference_is_not_drift(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "pane-user.service", RENDERED_TMPL))
    u = tmp_path / "u"
    u.mkdir()
    rendered = RENDERED_TMPL.replace("__PANE_EXEC__", "/home/op/w.sh")
    (u / "pane.service").write_text(rendered.replace(DECL.replace("__PANE_EXEC__", "/home/op/w.sh"), ""))
    assert uut.audit_unit(c, u).state == uut.CURRENT


def test_a_rendered_unit_behind_its_template_is_stale(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "pane-user.service",
                                    RENDERED_TMPL + "ExecStartPre=/bin/gate\n"))
    u = tmp_path / "u"
    u.mkdir()
    (u / "pane.service").write_text(RENDERED_TMPL.replace("__PANE_EXEC__", "/home/op/w.sh"))
    a = uut.audit_unit(c, u)
    assert a.state == uut.STALE and "install_pane.sh" in a.detail


def test_a_glob_unit_behind_its_template_is_stale(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "plain-user.service", GLOB_TMPL))
    u = tmp_path / "u"
    u.mkdir()
    (u / "plain.service").write_text("[Service]\nExecStart=/bin/false\n")
    assert uut.audit_unit(c, u).state == uut.STALE


# ── the CLI the shell copiers consume ──────────────────────────────────

def test_plan_copies_glob_and_skips_everything_else(tmp_path, capsys):
    _tmpl(tmp_path, "plain-user.service", GLOB_TMPL)
    _tmpl(tmp_path, "pane-user.service", RENDERED_TMPL)
    _tmpl(tmp_path, "x-user.service", "[Service]\nExecStart=__X__\n")
    assert uut.main(["plan", str(tmp_path / "t")]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [l.split("\t")[0] + ":" + l.split("\t")[-1 if l.startswith("copy") else 1]
            for l in out] == ["skip:pane.service", "copy:plain.service", "skip:x.service"]


def test_audit_exit_codes(tmp_path):
    _tmpl(tmp_path, "pane-user.service", RENDERED_TMPL)
    u = tmp_path / "u"
    u.mkdir()
    assert uut.main(["audit", str(tmp_path / "t"), str(u)]) == 0
    (u / "pane.service").write_text(RENDERED_TMPL)
    assert uut.main(["audit", str(tmp_path / "t"), str(u)]) == 1


# ── this repo's real templates (closed enum) ───────────────────────────

def test_no_real_template_is_refused_or_unreadable():
    bad = [(c.unit, c.kind, c.detail) for c in uut.templates(REPO / "templates" / "systemd")
           if c.kind in (uut.KIND_REFUSED, uut.KIND_UNREADABLE)]
    assert not bad, bad


def test_every_real_rendered_template_names_an_installer_that_exists():
    rendered = [c for c in uut.templates(REPO / "templates" / "systemd")
                if c.kind == uut.KIND_RENDERED]
    assert rendered, "no rendered template — the declaration rule compares nothing here"
    for c in rendered:
        if c.installer.startswith("scripts/"):
            assert (REPO / c.installer.split()[0]).is_file(), c.installer
        else:
            assert c.installer.startswith("TUI: "), c.installer


# ── closed enum over COPIERS: no copier outside the rule ───────────────

import re as _re  # noqa: E402

_COPY = _re.compile(r"\bcp\b|copy2|copyfile|shutil\.copy|install -m")
_NAMED = _re.compile(r"[A-Za-z0-9._-]+-user\.service")
_GLOB = _re.compile(r"\*-user\.service|glob\(\s*['\"]\*-user")


def test_every_copier_of_a_user_template_obeys_the_one_rule():
    """2026-10-21: the update.sh cure missed the TUI's own copy (and
    install_noc.sh's, and provision_role's) — the third copier is found by a
    gate, not by memory. A file that COPIES and globs *-user.service must use
    utils.user_unit_templates; one that copies a NAMED rendered template must
    be that template's declared installer (or use the rule)."""
    rendered = {c.template.name: c.installer
                for c in uut.templates(REPO / "templates" / "systemd")
                if c.kind == uut.KIND_RENDERED}
    bad = []
    for base in (REPO / "src", REPO / "scripts"):
        for f in list(base.rglob("*.py")) + list(base.rglob("*.sh")):
            if f.name in ("user_unit_templates.py",):
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            if not _COPY.search(text) or "user_unit_templates" in text:
                continue
            rel = str(f.relative_to(REPO))
            if _GLOB.search(text):
                bad.append((rel, "globs *-user.service without the rule"))
            for name in set(_NAMED.findall(text)) & set(rendered):
                if not rendered[name].startswith(rel):
                    bad.append((rel, f"copies {name}, rendered by {rendered[name]}"))
    assert not bad, bad


def test_summarize_puts_the_worst_first_and_ignores_absent(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "pane-user.service", RENDERED_TMPL))
    g = uut.classify_template(_tmpl(tmp_path, "plain-user.service", GLOB_TMPL))
    rows = [uut.UnitAudit(g, uut.CURRENT), uut.UnitAudit(c, uut.RAW, "carries __PANE_EXEC__"),
            uut.UnitAudit(g, uut.ABSENT)]
    ok, verdict, lines = uut.summarize(rows)
    assert not ok and verdict.startswith("1 BROKEN")
    assert lines[0].startswith("[RAW    ] pane.service") and len(lines) == 2
    assert uut.summarize([uut.UnitAudit(g, uut.ABSENT)])[:2] == (True, "no user-unit templates installed here")
