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


def test_a_rendered_unit_behind_its_template_differs(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "pane-user.service",
                                    RENDERED_TMPL + "ExecStartPre=/bin/gate\n"))
    u = tmp_path / "u"
    u.mkdir()
    (u / "pane.service").write_text(RENDERED_TMPL.replace("__PANE_EXEC__", "/home/op/w.sh"))
    a = uut.audit_unit(c, u)
    assert a.state == uut.DIFFERS and "install_pane.sh" in a.detail


def test_a_glob_unit_behind_its_template_differs(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "plain-user.service", GLOB_TMPL))
    u = tmp_path / "u"
    u.mkdir()
    (u / "plain.service").write_text("[Service]\nExecStart=/bin/false\n")
    assert uut.audit_unit(c, u).state == uut.DIFFERS


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
    assert lines[0].startswith("[RAW    ] pane.service") and len(lines) == 1  # current is counted, not listed
    assert uut.summarize([uut.UnitAudit(g, uut.ABSENT)])[:2] == (True, "no user-unit templates installed here")



# ── round 2 (non-author review of 6935a36a) ─────────────────────────────

AS_TMPL = ("# MF-DEPLOY: rendered PANE_EXEC by TUI: Pane > Install\n"
           "# MF-DEPLOY-AS: shared.service\n"
           "[Service]\nExecStart=/usr/bin/tmux new -d -s p '__PANE_EXEC__'\n")


def test_installs_as_names_the_unit_and_shadows_a_plain_template_of_that_name(tmp_path):
    _tmpl(tmp_path, "pane-user.service", AS_TMPL)
    _tmpl(tmp_path, "shared-user.service", GLOB_TMPL)
    by = {c.template.name: c for c in uut.templates(tmp_path / "t")}
    assert by["pane-user.service"].unit == "shared.service"
    assert by["shared-user.service"].kind == uut.KIND_SHADOWED


def test_plan_never_raw_copies_a_shadowed_template(tmp_path, capsys):
    _tmpl(tmp_path, "pane-user.service", AS_TMPL)
    _tmpl(tmp_path, "shared-user.service", GLOB_TMPL)
    uut.main(["plan", str(tmp_path / "t")])
    assert not [l for l in capsys.readouterr().out.splitlines() if l.startswith("copy")]


def test_a_unit_matching_ANY_candidate_template_is_current(tmp_path):
    """Two templates can produce shared.service; the installed daemon variant
    is current, not 'stale vs the tmux one'."""
    _tmpl(tmp_path, "pane-user.service", AS_TMPL)
    _tmpl(tmp_path, "shared-user.service", GLOB_TMPL)
    u = tmp_path / "u"
    u.mkdir()
    (u / "shared.service").write_text(GLOB_TMPL)
    rows = {r.cls.unit: r for r in uut.audit(tmp_path / "t", u)}
    assert rows["shared.service"].state == uut.CURRENT


def test_a_masked_unit_is_inert_not_drift(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "plain-user.service", GLOB_TMPL))
    u = tmp_path / "u"
    u.mkdir()
    (u / "plain.service").symlink_to("/dev/null")
    assert uut.audit_unit(c, u).state == uut.MASKED


def test_blank_lines_and_crlf_are_not_drift(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "plain-user.service", GLOB_TMPL))
    u = tmp_path / "u"
    u.mkdir()
    (u / "plain.service").write_text(GLOB_TMPL.replace("\n", "\r\n") + "\n\n")
    assert uut.audit_unit(c, u).state == uut.CURRENT


def test_a_typoed_declaration_is_unknown_with_the_template_cause_not_run_update(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "m-user.service",
                                    "# MF-DEPLOY: rendered M by scripts/inst.sh\n"
                                    "[Service]\nExecStart=__MM__\n"))
    u = tmp_path / "u"
    u.mkdir()
    (u / "m.service").write_text("[Service]\nExecStart=/bin/x\n")
    a = uut.audit_unit(c, u)
    assert a.state == uut.UNKNOWN and "declaration" in a.detail and "update.sh" not in a.detail


def test_differs_is_advisory_and_names_the_fix_only_if_this_app_owns_it(tmp_path):
    g = uut.classify_template(_tmpl(tmp_path, "plain-user.service", GLOB_TMPL))
    ok, verdict, lines = uut.summarize([uut.UnitAudit(g, uut.DIFFERS, "x"),
                                        uut.UnitAudit(g, uut.CURRENT)])
    assert ok and "advisory" in verdict
    u = tmp_path / "u"
    u.mkdir()
    (u / "plain.service").write_text("[Service]\nExecStart=/bin/other\n")
    assert "if this app owns it" in uut.audit_unit(g, u).detail


def test_unknown_is_labelled_could_not_judge(tmp_path):
    g = uut.classify_template(_tmpl(tmp_path, "plain-user.service", GLOB_TMPL))
    ok, verdict, _ = uut.summarize([uut.UnitAudit(g, uut.UNKNOWN, "x")])
    assert not ok and "could not judge" in verdict and "unreadable" not in verdict


def test_a_raw_unit_no_template_produces_is_still_reported(tmp_path):
    _tmpl(tmp_path, "plain-user.service", GLOB_TMPL)
    u = tmp_path / "u"
    u.mkdir()
    (u / "leftover.service").write_text("[Service]\nExecStart=__OLD_EXEC__\n")
    (u / "fine.service").write_text("[Service]\nExecStart=/bin/true\n")
    rows = {r.cls.unit: r for r in uut.audit(tmp_path / "t", u)}
    assert rows["leftover.service"].state == uut.RAW
    assert rows["leftover.service"].cls.kind == uut.KIND_ORPHAN
    assert "fine.service" not in rows


# ── round 3 (non-author review of 5bbba62a) ─────────────────────────────

def _udir(tmp_path):
    u = tmp_path / "u"
    u.mkdir(exist_ok=True)
    return u


def test_deploy_plan_installs_the_default_variant_on_a_fresh_box(tmp_path):
    """Round 2 left MA's daemon nomadnet with NO installer: a shadowed
    template must still install where the unit is absent."""
    _tmpl(tmp_path, "pane-user.service", AS_TMPL)
    _tmpl(tmp_path, "shared-user.service", GLOB_TMPL)
    acts = {(st.action, st.cls.template.name) for st in uut.deploy_plan(tmp_path / "t", _udir(tmp_path))}
    assert ("copy", "shared-user.service") in acts


def test_deploy_plan_refreshes_the_default_variant_in_place(tmp_path):
    _tmpl(tmp_path, "pane-user.service", AS_TMPL)
    _tmpl(tmp_path, "shared-user.service", GLOB_TMPL)
    u = _udir(tmp_path)
    (u / "shared.service").write_text("[Service]\nExecStart=/bin/older\n")
    acts = {(st.action, st.cls.template.name) for st in uut.deploy_plan(tmp_path / "t", u)}
    assert ("copy", "shared-user.service") in acts


def test_deploy_plan_never_switches_a_rendered_variant(tmp_path):
    _tmpl(tmp_path, "pane-user.service", AS_TMPL)
    _tmpl(tmp_path, "shared-user.service", GLOB_TMPL)
    u = _udir(tmp_path)
    (u / "shared.service").write_text(AS_TMPL.replace("__PANE_EXEC__", "/home/op/w.sh"))
    st = [s for s in uut.deploy_plan(tmp_path / "t", u) if s.cls.template.name == "shared-user.service"][0]
    assert st.action == "skip" and st.say and "not switched" in st.reason


def test_deploy_plan_never_writes_through_a_masked_unit(tmp_path):
    _tmpl(tmp_path, "plain-user.service", GLOB_TMPL)
    u = _udir(tmp_path)
    (u / "plain.service").symlink_to("/dev/null")
    st = uut.deploy_plan(tmp_path / "t", u)[0]
    assert st.action == "skip" and "symlink" in st.reason


def test_a_skip_is_said_only_where_installed_or_the_template_is_wrong(tmp_path):
    _tmpl(tmp_path, "pane-user.service", RENDERED_TMPL)
    _tmpl(tmp_path, "x-user.service", "[Service]\nExecStart=__X__\n")
    u = _udir(tmp_path)
    say = {st.cls.unit: st.say for st in uut.deploy_plan(tmp_path / "t", u)}
    assert say == {"pane.service": False, "x.service": True}
    (u / "pane.service").write_text("[Service]\n")  # some OTHER unit by that name
    assert not {st.cls.unit: st.say for st in uut.deploy_plan(tmp_path / "t", u)}["pane.service"]
    (u / "pane.service").write_text(RENDERED_TMPL.replace("__PANE_EXEC__", "/home/op/w.sh"))
    assert {st.cls.unit: st.say for st in uut.deploy_plan(tmp_path / "t", u)}["pane.service"]


def test_an_empty_render_is_broken_not_advisory(tmp_path):
    c = uut.classify_template(_tmpl(tmp_path, "pane-user.service", RENDERED_TMPL))
    u = _udir(tmp_path)
    (u / "pane.service").write_text(RENDERED_TMPL.replace("__PANE_EXEC__", ""))
    assert uut.audit_unit(c, u).state == uut.RAW


def test_the_orphan_scan_ignores_other_apps_legitimate_braces_and_ats(tmp_path):
    _tmpl(tmp_path, "plain-user.service", GLOB_TMPL)
    u = _udir(tmp_path)
    (u / "podman-foo.service").write_text("[Service]\nExecStart=/usr/bin/podman ps --format {{ID}}\n")
    (u / "mailer.service").write_text("[Service]\nExecStart=/bin/mail -f ops@HOST@example\n")
    assert {r.cls.unit for r in uut.audit(tmp_path / "t", u)} == {"plain.service"}


def test_the_orphan_scan_reads_through_a_link_and_witnesses_the_unreadable(tmp_path):
    _tmpl(tmp_path, "plain-user.service", GLOB_TMPL)
    u = _udir(tmp_path)
    real = tmp_path / "elsewhere.service"
    real.write_text("[Service]\nExecStart=__RAW_TOKEN__\n")
    (u / "linked.service").symlink_to(real)
    (u / "bad.service").write_bytes(b"\xff\xfe\x00")
    rows = {r.cls.unit: r.state for r in uut.audit(tmp_path / "t", u)}
    assert rows["linked.service"] == uut.RAW and rows["bad.service"] == uut.UNKNOWN


def test_one_unreadable_candidate_does_not_hide_a_matching_one(tmp_path):
    bad = _tmpl(tmp_path, "pane-user.service", AS_TMPL)
    good = uut.classify_template(_tmpl(tmp_path, "shared-user.service", GLOB_TMPL))
    cands = [uut.classify_template(bad), good]
    bad.write_bytes(b"\xff\xfe\x00")
    u = _udir(tmp_path)
    (u / "shared.service").write_text(GLOB_TMPL)
    assert uut.audit_unit(cands, u).state == uut.CURRENT


def test_summarize_lists_only_what_needs_a_look(tmp_path):
    g = uut.classify_template(_tmpl(tmp_path, "plain-user.service", GLOB_TMPL))
    ok, verdict, lines = uut.summarize([uut.UnitAudit(g, uut.CURRENT)] * 5 + [uut.UnitAudit(g, uut.DIFFERS, "x")])
    assert len(lines) == 1 and "5 current" in verdict
