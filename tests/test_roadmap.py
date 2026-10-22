"""The roadmap-cohesion gates (decided 2026-10-19, built 2026-10-22).

`.claude/ROADMAP.md` is the domain's one list of future work; these tests make
the 25 decisions hold instead of being remembered. Shown failing on the
pre-migration file first: 0 of its Now/Next/Later bullets were parseable items,
and it still carried struck-through done items.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("roadmap", ROOT / "scripts" / "roadmap.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _doc(items_md):
    return ("# Roadmap\n\n## Now / Next / Later\n\n" + items_md +
            "\n## Rules that shape the roadmap\n")


GOOD = "### NOW\n- **R2** [MF] S+F — measure the pair\n"


def test_the_real_roadmap_passes_every_rule():
    mod = _load()
    assert mod.check() == 0


def test_real_roadmap_check_is_not_vacuous():
    # The pre-migration file parsed to ZERO items and passed every rule. Pin
    # only that the parse sees something — never today's contents (a tier
    # may legitimately empty out; non-author pass 2026-10-22).
    mod = _load()
    items, _ = mod.parse(mod.ROADMAP.read_text(encoding="utf-8"))
    assert items


def test_good_item_parses():
    items, errors = _load().parse(_doc(GOOD))
    assert errors == [] and items[0].id == "R2" and items[0].offering == "S+F"


def test_duplicate_id_fails():
    _, errors = _load().parse(_doc(GOOD + "- **R2** [MA] S — again\n"))
    assert any("duplicate ID R2" in e for e in errors)


def test_strike_through_fails():
    _, errors = _load().parse(_doc(GOOD + "  ~~done thing~~\n"))
    assert any("strike-through" in e for e in errors)


def test_unparseable_bullet_fails():
    _, errors = _load().parse(_doc(GOOD + "- a loose bullet with no ID\n"))
    assert any("not an item line" in e for e in errors)


def test_numbered_item_fails():
    # The pre-migration Now/Next/Later was a numbered list: 0 items parsed,
    # so every rule passed by having nothing to look at.
    _, errors = _load().parse(_doc("### NOW\n1. **moc3 antenna** — swap it\n"))
    assert any("numbered item" in e for e in errors)


def test_maps_item_needs_maps_mode():
    mod = _load()
    _, errors = mod.parse(_doc("### NEXT\n- **MP-1** [maps] S — a map thing\n"))
    assert any("maps:both" in e for e in errors)
    _, ok = mod.parse(_doc("### NEXT\n- **MP-1** [maps] S maps:both — a map thing\n"))
    assert ok == []


def test_offer_back_only_on_fork_in_later():
    mod = _load()
    _, errors = mod.parse(_doc("### NEXT\n- **R6** [fork] S offer-back — upstream\n"))
    assert any("offer-back" in e for e in errors)


def test_public_line_needs_status_and_testing_needs_help():
    mod = _load()
    _, e1 = mod.parse(_doc(GOOD + "  public: Measure it.\n"))
    assert any("needs status" in e for e in e1)
    _, e2 = mod.parse(_doc(GOOD + "  public: Measure it.\n  status: testing\n"))
    assert any("needs help" in e for e in e2)
    _, e3 = mod.parse(_doc(GOOD + "  public: Measure it.\n  status: works\n"))
    assert any("needs evidence" in e for e in e3)


def test_public_text_failing_mf014_or_mf015_is_refused():
    mod = _load()
    # Built at runtime: the literal values would (rightly) fail lint MF014/MF015
    # in this very file.
    host = "meshforge-" + "moc3"
    lan = ".".join(["192", "168", "1", "20"])
    items, _ = mod.parse(_doc(GOOD + f"  public: Runs on {host} at {lan}.\n"
                               "  status: planned\n"))
    refusals = mod._publish_refusals(items)
    assert any("MF014" in r for r in refusals)
    assert any("MF015" in r for r in refusals)


def test_public_roadmap_groups_by_reader():
    mod = _load()
    items, _ = mod.parse(_doc(
        "### NEXT\n"
        "- **A-1** [MF] S — one box\n  public: One box thing.\n  status: planned\n"
        "- **A-2** [MF] F — fleet\n  public: Fleet thing.\n  status: testing\n"
        "  help: two Pis — try it\n"
        "- **A-3** [MA] S — sister\n  public: Sister thing.\n  status: planned\n"
        "- **A-4** [MF] S — private, no public line\n"
        "- **A-5** [fork] S — fork only\n  public: Fork thing.\n  status: planned\n"
        "- **A-6** [MF] S+F — both\n  public: Both thing.\n  status: planned\n"))
    out = mod.render_public(items)
    one = out.index("## If you run one box")
    several = out.index("## If you run several boxes")
    sister = out.index("## Sister projects")
    assert one < out.index("One box thing") < several
    assert several < out.index("Fleet thing") < out.index("*Help wanted:* two Pis") < sister
    assert out.index("Sister thing") > sister
    assert "private, no public line" not in out
    assert one < out.index("Fork thing") < several      # [fork] is not a sister project
    assert out.count("Both thing") == 2                  # S+F serves both readers


def test_now_prints_ids(capsys, monkeypatch, tmp_path):
    mod = _load()
    f = tmp_path / "ROADMAP.md"
    f.write_text(_doc(GOOD + "### NEXT\n- **Z-9** [MF] S — later thing\n"), encoding="utf-8")
    monkeypatch.setattr(mod, "ROADMAP", f)
    monkeypatch.setattr(sys, "argv", ["roadmap.py", "--now"])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "ROADMAP NOW (1)" in out and "R2" in out and "Z-9" not in out


def test_now_unreadable_says_unknown_on_stdout(capsys, monkeypatch, tmp_path):
    # The SessionStart hook drops stderr: a silent failure would read as an
    # empty NOW.
    mod = _load()
    monkeypatch.setattr(mod, "ROADMAP", tmp_path / "missing.md")
    monkeypatch.setattr(sys, "argv", ["roadmap.py", "--now"])
    assert mod.main() == 2
    assert "UNKNOWN" in capsys.readouterr().out


def test_star_bullet_fails():
    _, errors = _load().parse(_doc(GOOD + "* **X-1** [MF] S — sneaky\n"))
    assert any("bullet" in e for e in errors)


def test_near_miss_and_repeated_fields_fail():
    mod = _load()
    for bad in ("  Public: Capital key.\n", "    public: Four spaces.\n",
                "  public : Space before colon.\n"):
        _, errors = mod.parse(_doc(GOOD + bad))
        assert any("malformed field" in e for e in errors), bad
    _, errors = mod.parse(_doc(GOOD + "  public: One.\n  public: Two.\n  status: planned\n"))
    assert any("repeats" in e for e in errors)


def test_provenance_pointer_must_resolve():
    mod = _load()
    items, _ = mod.parse(_doc(GOOD))
    assert mod.provenance_pointer_problems(items, "## x → ROADMAP `R2` (y)") == []
    assert mod.provenance_pointer_problems(items, "## x → ROADMAP `GONE-1` (y)")
    assert mod.provenance_pointer_problems(
        items, "## x → ROADMAP `GONE-1` (y)\n- WON'T DO `GONE-1` — reason") == []
    assert mod.provenance_pointer_problems(items, "## x → ROADMAP the thing (y)")


def test_unloadable_lint_is_unknown_not_a_rule_failure(monkeypatch):
    mod = _load()
    def boom(_items):
        raise AttributeError("load_local_operator_patterns renamed")
    monkeypatch.setattr(mod, "_publish_refusals", boom)
    rc, _items, msgs = mod.problems()
    assert rc == 2 and "UNKNOWN" in msgs[0]


def test_lint_runs_the_roadmap_gate():
    spec = importlib.util.spec_from_file_location("mf_lint2", ROOT / "scripts" / "lint.py")
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    assert lint.check_roadmap_rules() == []
    assert "Trim" in lint.CONTEXT_DOC_REMEDY[".claude/ROADMAP.md"] or \
        "trim" in lint.CONTEXT_DOC_REMEDY[".claude/ROADMAP.md"]


def test_roadmap_size_is_capped_by_lint():
    spec = importlib.util.spec_from_file_location("mf_lint", ROOT / "scripts" / "lint.py")
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    assert ".claude/ROADMAP.md" in lint.CONTEXT_DOC_LIMITS


def test_readme_links_the_public_roadmap_at_the_top():
    head = (ROOT / "README.md").read_text(encoding="utf-8")[:3000]
    assert "docs/roadmap.md" in head


def test_tester_report_template_exists():
    t = (ROOT / ".github" / "ISSUE_TEMPLATE" / "tester-report.md").read_text(encoding="utf-8")
    assert "labels: tester-report" in t or "labels: [tester-report]" in t
    for field in ("Hardware", "Firmware", "Offering", "Steps", "Result", "Logs"):
        assert field in t, field


def test_provenance_holds_no_open_queue():
    # Q3: the ROADMAP is the only list of future work. A new `## QUEUED` heading
    # or `- QUEUED` bullet in provenance is a second list — add an item instead.
    import re
    text = (ROOT / ".claude" / "audits" / "review_provenance.md").read_text(encoding="utf-8")
    open_rows = re.findall(r"^(?:#+\s*\**QUEUED|[-*]\s+\**QUEUED)\b.*$", text, re.M)
    assert not open_rows, [r[:80] for r in open_rows]
