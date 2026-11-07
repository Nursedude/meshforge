#!/usr/bin/env python3
"""roadmap_html.py — render `.claude/ROADMAP.md` as the shared roadmap PAGE.

The page is a claude.ai artifact (a read-only view the operator shares). It is
GENERATED, never hand-edited: the first generator lived in a session
scratchpad and was lost, and the page then drifted from the SSOT for a week
(2026-11-06). This script is the one way to rebuild it.

    python3 scripts/roadmap_html.py --out <path.html>   # 0 ok · 1 refused · 2 unknown

Then publish <path.html> to the existing artifact URL (same link, new version).
The item list reuses `roadmap.parse` (the same rules `--check` enforces); rule
failures are SHOWN on the page, never hidden. A LAN address anywhere in the
output refuses the page (MF015 — it is shared by link).
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TEMPLATE = HERE / "roadmap_page_template.html"


def _roadmap():
    if "roadmap" in sys.modules:
        return sys.modules["roadmap"]
    spec = importlib.util.spec_from_file_location("roadmap", HERE / "roadmap.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["roadmap"] = mod
    spec.loader.exec_module(mod)
    return mod


class PublishRefused(Exception):
    """The rendered page carries something that must never be shared."""


# ---------------------------------------------------------------- inline md
_CODE = re.compile(r"`([^`]+)`")
_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_EM = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", re.S)


def inline(text: str) -> str:
    keep: list[str] = []

    def stash(s):
        keep.append(s)
        return f"\x00{len(keep) - 1}\x00"

    text = _CODE.sub(lambda m: stash(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)
    text = _AUTOLINK.sub(lambda m: stash(
        f'<a href="{html.escape(m.group(1))}">{html.escape(m.group(1), quote=False)}</a>'), text)
    text = html.escape(text, quote=False)
    text = _LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _EM.sub(r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], text)


def slug(title: str) -> str:
    return "doc-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


# ----------------------------------------------------------------- block md
def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def markdown(text: str) -> tuple[str, list[tuple[str, str]]]:
    """(html, toc) for the subset ROADMAP.md uses: ## / ### headings, `>`
    quotes, pipe tables, `-` and `1.` lists with 2-space continuations,
    fenced code, paragraphs. The `#` title is dropped (the page has its own)."""
    lines = text.splitlines()
    out, toc, i = [], [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        h = re.match(r"^(#{1,6}) (.*)$", line)
        if h:
            level, title = len(h.group(1)), h.group(2).strip()
            if level == 2:
                toc.append((slug(title), title))
                out.append(f'<h2 id="{slug(title)}">{inline(title)}</h2>')
            elif level >= 3:
                out.append(f"<h3>{inline(title)}</h3>")
            i += 1
            continue
        if line.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            body = html.escape("\n".join(lines[i + 1:j]), quote=False)
            out.append(f"<pre><code>{body}</code></pre>")
            i = j + 1
            continue
        if line.startswith(">"):
            j = i
            while j < len(lines) and lines[j].startswith(">"):
                j += 1
            body = " ".join(l[1:].strip() for l in lines[i:j])
            out.append(f"<blockquote><p>{inline(body)}</p></blockquote>")
            i = j
            continue
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|?\s*$", lines[i + 1]):
            head = "".join(f"<th>{inline(c)}</th>" for c in _cells(line))
            j, rows = i + 2, []
            while j < len(lines) and lines[j].startswith("|"):
                rows.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in _cells(lines[j])) + "</tr>")
                j += 1
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>")
            i = j
            continue
        lm = re.match(r"^(- |\d+\. )", line)
        if lm:
            tag = "ul" if lm.group(1) == "- " else "ol"
            items, j = [], i
            while j < len(lines):
                m = re.match(r"^(- |\d+\. )(.*)$", lines[j])
                if m and (m.group(1) == "- ") == (tag == "ul"):
                    items.append([m.group(2)])
                elif lines[j].startswith("  ") and items:
                    items[-1].append(lines[j].strip())
                elif not lines[j].strip() and j + 1 < len(lines) and re.match(r"^(- |\d+\. |  )", lines[j + 1]):
                    pass  # a blank line inside one list
                else:
                    break
                j += 1
            lis = "".join("<li>" + inline("\n".join(it)) + "</li>" for it in items)
            out.append(f"<{tag}>{lis}</{tag}>")
            i = j
            continue
        j = i
        while j < len(lines) and lines[j].strip() and not re.match(r"^(#|>|\||- |\d+\. |```)", lines[j]):
            j += 1
        out.append(f"<p>{inline(' '.join(l.strip() for l in lines[i:j]))}</p>")
        i = max(j, i + 1)
    return "\n".join(out), toc


# -------------------------------------------------------------------- items
def item_details(text: str) -> dict[str, list[str]]:
    """{id: [detail lines]} — the indented non-field lines under each item."""
    rm = _roadmap()
    out, cur = {}, None
    for line in text.splitlines():
        m = rm.ITEM_RE.match(line)
        if m:
            cur = m.group("id")
            out.setdefault(cur, [])
            continue
        if cur and line.startswith("  ") and line.strip():
            if not rm.FIELD_RE.match(line):
                out[cur].append(line.strip())
            continue
        if line.strip():
            cur = None
    return out


def _chips(it) -> str:
    rm = _roadmap()
    chips = [f'<span class="chip repo">{r}</span>' for r in it.repos]
    chips.append(f'<span class="chip off">{it.offering}</span>')
    chips += [f'<span class="chip ex">{html.escape(x)}</span>' for x in it.extra]
    st = it.fields.get("status")
    if st in rm.STATUS_LABEL:
        chips.append(f'<span class="chip st st-{st}">{rm.STATUS_LABEL[st]}</span>')
    return "".join(chips)


def _item_li(it, details: list[str]) -> str:
    body = []
    if details:
        body.append("<p>" + "<br>".join(inline(d) for d in details) + "</p>")
    if "public" in it.fields:
        body.append(f'<p class="pub"><b>Public line</b> {inline(it.fields["public"])}</p>')
    if "help" in it.fields:
        body.append(f'<p class="pub"><b>Help wanted</b> {inline(it.fields["help"])}</p>')
    if "evidence" in it.fields:
        body.append(f'<p class="pub"><b>Evidence</b> {inline(it.fields["evidence"])}</p>')
    if not body:
        body.append('<p class="muted">No detail lines.</p>')
    q = " ".join([it.id, it.title, *details, *it.fields.values()]).lower()
    return (f'<li class="item" data-tier="{it.tier}" data-repos="{" ".join(it.repos)}" '
            f'data-q="{html.escape(q)}"><details><summary><span class="iid">{it.id}</span>'
            f'<span class="ititle">{inline(it.title)}</span><span class="chips">{_chips(it)}</span>'
            f'</summary><div class="idetail">{"".join(body)}</div></details></li>')


# --------------------------------------------------------------------- page
def render_page(text: str, sha: str, committed: str, generated: str = "") -> str:
    rm = _roadmap()
    items, errors = rm.parse(text)
    details = item_details(text)
    counts = {t: sum(1 for it in items if it.tier == t) for t in rm.TIERS}

    buttons = [f'    <button type="button" class="tier-btn" data-tier="ALL" aria-pressed="true">'
               f'All<span class="n">{len(items)}</span></button>']
    for t in rm.TIERS:
        buttons.append(f'    <button type="button" class="tier-btn" data-tier="{t}" aria-pressed="false">'
                       f'{t.title()}<span class="n">{counts[t]}</span></button>')
    used = [r for r in rm.REPOS if any(r in it.repos for it in items)]
    repo_buttons = "".join(f'<button type="button" class="rf" data-repo="{r}" aria-pressed="false">{r}</button>'
                           for r in used)

    last_set = re.search(r"Last set: ([^\n]*?)(?: History:|$)", text, re.M)
    prov = [f"From <code>.claude/ROADMAP.md</code> at <code>{html.escape(sha)}</code> · "
            f"committed {html.escape(committed)}"]
    if last_set:
        prov[0] += f" · Last set: {inline(last_set.group(1).strip())}"
    if generated:
        prov.append(f"page generated {html.escape(generated)} by <code>scripts/roadmap_html.py</code>")
    if errors:
        shown = "; ".join(html.escape(e) for e in errors[:5])
        prov.append(f'<span class="warn">{len(errors)} roadmap rule failure(s) — those lines are '
                    f'missing below: {shown}</span>')
    else:
        prov.append("Item rules: clean")

    doc_html, toc = markdown(text)
    page = (TEMPLATE.read_text(encoding="utf-8")
            .replace("@@PROV@@", "<br>".join(prov))
            .replace("@@TIER_BUTTONS@@", "\n".join(buttons))
            .replace("@@REPO_BUTTONS@@", repo_buttons)
            .replace("@@POOL@@", "\n".join(_item_li(it, details.get(it.id, [])) for it in items))
            .replace("@@TOC@@", "".join(f'<a href="#{s}">{inline(t)}</a>' for s, t in toc))
            .replace("@@DOC@@", doc_html))
    leak = rm._PRIVATE_IP.search(page)
    if leak:
        raise PublishRefused(f"MF015 — a LAN address ({leak.group(0)}) would be published")
    return page


def _git(*args) -> str:
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"git {args[0]} rc={r.returncode}")
    return r.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="where to write the page (never inside the repo)")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    if ROOT in out.parents:
        print("roadmap_html: refused — the page is an artifact, never a repo file; write it to a scratch path")
        return 1
    try:
        src = ROOT / ".claude" / "ROADMAP.md"
        text = src.read_text(encoding="utf-8")
        sha, committed = _git("log", "-1", "--format=%h|%cd", "--date=format:%Y-%m-%d %H:%M",
                              "--", ".claude/ROADMAP.md").split("|", 1)
        if _git("status", "--porcelain", "--", ".claude/ROADMAP.md"):
            sha += " + UNCOMMITTED edits"
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
        print(f"roadmap_html: UNKNOWN — {type(e).__name__}: {e}")
        return 2
    try:
        page = render_page(text, sha=sha, committed=committed,
                           generated=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"))
    except PublishRefused as e:
        print(f"roadmap_html: refused — {e}")
        return 1
    out.write_text(page, encoding="utf-8")
    items, errors = _roadmap().parse(text)
    print(f"roadmap_html: wrote {out} — {len(items)} items, {len(errors)} rule failure(s), "
          f"ROADMAP.md @ {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
