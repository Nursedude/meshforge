"""The roadmap PAGE (a shared claude.ai artifact) is generated from
`.claude/ROADMAP.md`, never hand-edited.

Why (2026-11-06): the page's generator lived in a session scratchpad and was
lost; the page then sat frozen at a 10-30 commit while NOW changed under it
(7 items shown, 6 different items live) and it carried rows the SSOT never
had. A view nobody can regenerate is a representation that drifts silently.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    for name, fn in (("roadmap", "roadmap.py"), ("roadmap_html", "roadmap_html.py")):
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / fn)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules["roadmap_html"]


DOC = """# MeshForge Roadmap — SSOT

> Last set: 2026-11-06 — test fixture.

## Why we win

| Them | What only we do |
|---|---|
| **Alpha** | the `<hub>` & more |

## Now / Next / Later

### NOW

- **AA-1** [MF][MA] S+F twin→MA — first item with `code`
  A detail line that says **why**.
  public: A plain sentence.
  status: planned
- **BB-2** [MF] F — second item

### NEXT

- **CC-3** [maps] S maps:both — a maps item

### LATER

- **DD-4** [fork] S offer-back — later item

## Rules that shape the roadmap

- one rule
"""


def _page(text=DOC):
    mod = _load()
    return mod.render_page(text, sha="abc12345", committed="2026-11-06 17:00 HST")


def test_every_item_appears_once_in_its_tier():
    page = _page()
    for iid, tier in (("AA-1", "NOW"), ("BB-2", "NOW"), ("CC-3", "NEXT"), ("DD-4", "LATER")):
        chunks = [c for c in page.split('<li class="item" ')[1:]
                  if f'<span class="iid">{iid}</span>' in c]
        hits = [re.match(r'data-tier="(\w+)"', c).group(1) for c in chunks]
        assert hits == [tier], (iid, hits)


def test_tier_buttons_count_what_the_parser_sees():
    page = _page()
    assert re.search(r'data-tier="ALL"[^>]*>All<span class="n">4<', page)
    assert re.search(r'data-tier="NOW"[^>]*>Now<span class="n">2<', page)
    assert re.search(r'data-tier="NEXT"[^>]*>Next<span class="n">1<', page)
    assert re.search(r'data-tier="LATER"[^>]*>Later<span class="n">1<', page)


def test_detail_lines_and_public_line_are_rendered():
    page = _page()
    assert "A detail line that says <strong>why</strong>." in page
    assert "<b>Public line</b> A plain sentence." in page
    assert 'class="chip st st-planned">Planned' in page
    # an item with no detail says so instead of rendering an empty box
    bb = page.split('<span class="iid">BB-2</span>', 1)[1].split("</li>", 1)[0]
    assert "No detail lines." in bb


def test_doc_section_renders_markdown_and_escapes_html():
    page = _page()
    assert "<table>" in page and "<strong>Alpha</strong>" in page
    assert "<code>&lt;hub&gt;</code> &amp; more" in page
    assert '<h2 id="doc-why-we-win">Why we win</h2>' in page
    assert '<a href="#doc-why-we-win">Why we win</a>' in page
    assert "<script>alert" not in _page(DOC.replace("one rule", "<script>alert(1)</script>"))


def test_provenance_names_the_commit_and_rule_state():
    page = _page()
    assert "<code>abc12345</code>" in page and "2026-11-06 17:00 HST" in page
    assert "Last set: 2026-11-06" in page


def test_rule_failures_are_shown_not_hidden():
    bad = DOC.replace("- **BB-2** [MF] F — second item", "- **BB-2** [XX] F — bad repo")
    page = _page(bad)
    assert "rule failure" in page


def test_a_lan_address_refuses_the_page():
    # The page is shared by link (MF015): a LAN address must never reach it.
    mod = _load()
    with pytest.raises(mod.PublishRefused):
        mod.render_page(DOC.replace("one rule", "see 192.168.1.20"), sha="x", committed="y")


def test_page_contract_title_and_both_dark_mode_blocks():
    page = _page()
    assert "<title>MeshForge Roadmap</title>" in page
    assert '@media (prefers-color-scheme:dark){:root:not([data-theme="light"])' in page
    assert ':root[data-theme="dark"]' in page
    assert "@@" not in page  # every template slot filled


def test_the_real_roadmap_renders_with_the_live_now_count():
    mod = _load()
    text = (ROOT / ".claude" / "ROADMAP.md").read_text(encoding="utf-8")
    items, _ = sys.modules["roadmap"].parse(text)
    page = mod.render_page(text, sha="HEAD", committed="now")
    now = sum(1 for it in items if it.tier == "NOW")
    assert now > 0
    assert re.search(r'data-tier="NOW"[^>]*>Now<span class="n">%d<' % now, page)
    assert page.count('<li class="item"') == len(items)
