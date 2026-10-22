#!/usr/bin/env python3
"""roadmap.py — the domain roadmap's item rules, and the public roadmap they generate.

`.claude/ROADMAP.md` is the domain's ONE list of future work (roadmap cohesion,
DECIDED 2026-10-19, 25 answers in `.claude/plans/roadmap_cohesion_proposal_2026_10_19.md`).
This file is the gate that makes those decisions hold instead of being
remembered (build:fix doctrine — gates, not prose):

- every item in Now / Next / Later is ONE parseable line:
      - **<ID>** [MF][MA] S+F twin→MA — <title>
  ID stable + unique (Q10), repo tags (Q1), offering S / F / S+F (Q6), optional
  twin owed (Q7/Q18), `maps:both` on every [maps] item unless a written reason
  (Q8), `offer-back` only on [fork] items in LATER (Q9);
- done items LEAVE the list (Q4) — no strike-through survives in it;
- an item may carry `public:` (one plain sentence) + `status:` works / testing /
  planned (Q20/Q21); `testing` needs `help:` (Q22); `works` needs `evidence:`;
- `docs/roadmap.md` is GENERATED from the public lines (Q19/Q20) and REFUSES a
  line that would fail MF014 / MF015 (Q24).

Usage:
    python3 scripts/roadmap.py --check      # exit 0 ok / 1 rule or drift failure / 2 unknown
    python3 scripts/roadmap.py --update     # rewrite docs/roadmap.md
    python3 scripts/roadmap.py --now        # print NOW (ID + title) — the session-start line (Q16)
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROADMAP = ROOT / ".claude" / "ROADMAP.md"
PUBLIC = ROOT / "docs" / "roadmap.md"
TEMPLATE_URL = ("https://github.com/Nursedude/meshforge/issues/new"
                "?template=tester-report.md")

SECTION_START = "## Now / Next / Later"
TIERS = ("NOW", "NEXT", "LATER")
REPOS = ("MF", "MA", "maps", "bot", "fork")
STATUS_LABEL = {"works": "Works today",
                "testing": "Being tested — help wanted",
                "planned": "Planned"}

ITEM_RE = re.compile(
    r"^- \*\*(?P<id>[A-Z][A-Z0-9]*(?:-[A-Za-z0-9]+)*)\*\* "
    r"(?P<repos>(?:\[(?:" + "|".join(REPOS) + r")\])+) "
    r"(?P<offering>S\+F|S|F)"
    r"(?P<extra>(?: (?:twin→MA|twin→MF|maps:both|maps:[a-z]+\([^)]+\)|offer-back))*)"
    r" — (?P<title>\S.*)$")
FIELD_RE = re.compile(r"^  (?P<key>public|status|help|evidence): (?P<val>\S.*)$")

# MF015 (security.md): no operator LAN address in anything published.
_PRIVATE_IP = re.compile(
    r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")


class Item:
    def __init__(self, tier, m, line_no):
        self.tier = tier
        self.id = m.group("id")
        self.repos = re.findall(r"\[(\w+)\]", m.group("repos"))
        self.offering = m.group("offering")
        self.extra = m.group("extra").split()
        self.title = m.group("title").strip()
        self.line_no = line_no
        self.fields = {}


def _section(text):
    start = text.find(SECTION_START)
    if start < 0:
        raise LookupError(f"no '{SECTION_START}' section")
    rest = text[start + len(SECTION_START):]
    end = re.search(r"^## ", rest, re.M)
    return text[:start].count("\n") + 1, rest[:end.start()] if end else rest


def parse(text):
    """(items, errors) for the Now / Next / Later section."""
    first_line, body = _section(text)
    items, errors, tier, cur = [], [], None, None
    for i, line in enumerate(body.splitlines(), start=first_line):
        h = re.match(r"^### (NOW|NEXT|LATER)\b", line)
        if h:
            tier, cur = h.group(1), None
            continue
        if re.match(r"^\d+[a-z]?\. ", line):
            # A numbered list is invisible to every rule below — the
            # pre-migration file's whole Now/Next/Later was one (0 items seen).
            errors.append(f"line {i}: numbered item — use `- **ID** [repo] S|F|S+F — title`")
            cur = None
            continue
        if line.startswith("- "):
            m = ITEM_RE.match(line)
            if tier is None:
                errors.append(f"line {i}: item outside ### NOW/NEXT/LATER")
                cur = None
            elif not m:
                errors.append(f"line {i}: not an item line "
                              f"(`- **ID** [repo] S|F|S+F — title`): {line[:70]}")
                cur = None
            else:
                cur = Item(tier, m, i)
                items.append(cur)
            continue
        f = FIELD_RE.match(line)
        if f and cur is not None:
            cur.fields[f.group("key")] = f.group("val").strip()
        elif line.strip() and not line.startswith("  ") and not line.startswith("#"):
            cur = None  # a prose paragraph ends the item
    if "~~" in body:
        errors.append("strike-through in Now/Next/Later — a done item LEAVES "
                      "the list (one provenance line), never struck in place")
    seen = {}
    for it in items:
        if it.id in seen:
            errors.append(f"duplicate ID {it.id} (lines {seen[it.id]} and {it.line_no})")
        seen.setdefault(it.id, it.line_no)
        if "maps" in it.repos and not any(x.startswith("maps:") for x in it.extra):
            errors.append(f"{it.id}: a [maps] item declares maps:both "
                          "(or maps:<mode>(<reason>))")
        if "offer-back" in it.extra and ("fork" not in it.repos or it.tier != "LATER"):
            errors.append(f"{it.id}: offer-back is a [fork] item in LATER")
        st = it.fields.get("status")
        if "public" in it.fields and st not in STATUS_LABEL:
            errors.append(f"{it.id}: public: needs status: works|testing|planned")
        if st and "public" not in it.fields:
            errors.append(f"{it.id}: status: without a public: line")
        if st == "testing" and "help" not in it.fields:
            errors.append(f"{it.id}: status: testing needs help: <setup> — <what to try>")
        if st == "works" and "evidence" not in it.fields:
            errors.append(f"{it.id}: status: works needs evidence: (a check or sha)")
    return items, errors


def _publish_refusals(items):
    """Public text that would fail MF014 / MF015 (Q24) — never published."""
    spec = importlib.util.spec_from_file_location("mf_lint", ROOT / "scripts" / "lint.py")
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    patterns = list(lint.MF014_PATTERNS) + list(lint.load_local_operator_patterns())
    out = []
    for it in items:
        for key in ("public", "help"):
            val = it.fields.get(key, "")
            for pat, why in patterns:
                if pat.search(val):
                    out.append(f"{it.id} {key}: MF014 — {why}")
            if _PRIVATE_IP.search(val):
                out.append(f"{it.id} {key}: MF015 — a LAN address")
    return out


def _reader(it):
    if "MF" not in it.repos and "maps" not in it.repos:
        return "Sister projects"
    if "maps" in it.repos:
        return "Maps — works inside MeshForge or on its own"
    return "If you run several boxes" if it.offering == "F" else "If you run one box"


READERS = ("If you run one box", "If you run several boxes",
           "Maps — works inside MeshForge or on its own", "Sister projects")


def render_public(items):
    pub = [it for it in items if "public" in it.fields]
    lines = [
        "# MeshForge roadmap",
        "",
        "<!-- GENERATED by scripts/roadmap.py --update from .claude/ROADMAP.md — "
        "do not edit by hand -->",
        "",
        "What we are building next, in plain words, by who it is for. Nothing here",
        "has a date. Three statuses: **Works today** (we can show you the evidence),",
        "**Being tested — help wanted** (it runs; we need more hardware and setups",
        "than we own), and **Planned**.",
        "",
        "If you try something marked *help wanted*, please tell us what happened with",
        f"a [test report]({TEMPLATE_URL}) — hardware, firmware, what you tried, what",
        "you saw. A report that moves an item's status is credited by issue number.",
        "",
    ]
    for reader in READERS:
        group = [it for it in pub if _reader(it) == reader]
        lines += [f"## {reader}", ""]
        if not group:
            lines += ["Nothing on the list for this right now.", ""]
            continue
        for st in ("works", "testing", "planned"):
            for it in [g for g in group if g.fields["status"] == st]:
                lines.append(f"- **{STATUS_LABEL[st]}** — {it.fields['public']} "
                             f"<sub>(ref {it.id})</sub>")
                if st == "testing":
                    lines.append(f"  - *Help wanted:* {it.fields['help']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def check():
    """0 ok · 1 a rule failed or docs/roadmap.md drifted · 2 unobservable."""
    try:
        items, errors = parse(ROADMAP.read_text(encoding="utf-8"))
    except (OSError, LookupError) as e:
        print(f"roadmap: UNKNOWN — {e}", file=sys.stderr)
        return 2
    refusals = _publish_refusals(items)
    for e in errors + refusals:
        print(f"  FAIL  {e}")
    if errors or refusals:
        return 1
    try:
        current = PUBLIC.read_text(encoding="utf-8")
    except OSError as e:
        print(f"roadmap: UNKNOWN — cannot read {PUBLIC}: {e}", file=sys.stderr)
        return 2
    if current != render_public(items):
        print("roadmap: docs/roadmap.md DRIFT — run `python3 scripts/roadmap.py --update`")
        return 1
    print(f"roadmap: {len(items)} items ok, docs/roadmap.md current")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--update", action="store_true")
    g.add_argument("--now", action="store_true")
    a = ap.parse_args()
    if a.check:
        return check()
    try:
        items, errors = parse(ROADMAP.read_text(encoding="utf-8"))
    except (OSError, LookupError) as e:
        print(f"roadmap: UNKNOWN — {e}", file=sys.stderr)
        return 2
    if a.now:
        now = [it for it in items if it.tier == "NOW"]
        print(f"🗺️ ROADMAP NOW ({len(now)}) — .claude/ROADMAP.md in meshforge is the domain's one list:")
        for it in now:
            print(f"  {it.id:<7} {' '.join('[' + r + ']' for r in it.repos)} {it.title}")
        if errors:
            print(f"  ⚠️ {len(errors)} roadmap rule failure(s) — run scripts/roadmap.py --check")
        return 0
    refusals = _publish_refusals(items)
    if errors or refusals:
        for e in errors + refusals:
            print(f"  FAIL  {e}")
        return 1
    PUBLIC.write_text(render_public(items), encoding="utf-8")
    print(f"roadmap: docs/roadmap.md written ({sum('public' in i.fields for i in items)} public items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
