#!/usr/bin/env python3
"""loop_gate — three circularity gates (operator 2026-11-15).

The defect class: a claim gains authority by being REPEATED, not by being
measured. A self-graded "6 of 16" read as an outside result across pages
(outside audit A22); a duplicate page drifted beside its original; a reviewer
was told an item was "accepted" with no date and no row. Every link in that
loop was a Claude session agreeing with a Claude session. These gates do not
add outside evidence — only the radios, CI, systemd and the operator's screen
do that — they stop repetition from passing for it.

  A. GROUND   (PreToolUse Artifact) a VERIFIED tag (`class="tag t-v"`) that a
     publish ADDS names its ground: data-ground="code|git|ci|test|live|radio|
     operator|upstream". `ai` is not ground — another model's page makes a
     claim "consistent with", never VERIFIED. Pre-existing ungrounded tags are
     a frozen per-page baseline that only shrinks (the MF025 pattern).
  B. TOPIC    (PreToolUse Artifact) every domain page is in
     `.claude/artifacts_registry.json`: one `authoritative` page per topic; a
     `superseded` page links its successor in its header; a `variant` (paste
     kit, Docs copy) links its authoritative page; a new domain page declares
     `<meta name="mf:topic">` on a topic with no authoritative page.
  C. ACCEPTED (PreToolUse Agent) a brief that tells a reviewer what NOT to
     report carries the block `loop_gate.py accepted <ARC>` renders from
     `~/deferred_work.json` rows (`review_accepted: <ARC>`, `accepted_text`,
     dated `review_after`). A hand-written or edited list is refused; an
     expired row refuses the whole list until it is re-decided.

Usage:
  loop_gate.py hook                 # PreToolUse JSON on stdin (settings.json)
  loop_gate.py accepted <ARC>       # print the accepted block for a brief
  loop_gate.py check                # registry + ledger consistency, exit 1 on problems

Fails OPEN on a payload it cannot parse, with a witness line (hfm #9).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
GROUNDS = ("code", "git", "ci", "test", "live", "radio", "operator", "upstream")
ROLES = ("authoritative", "variant", "superseded")
_DOMAIN = re.compile(r"mesh\s*forge|mesh\s*anchor", re.I)
_TV = re.compile(r'<span\b[^>]*\bclass="[^"]*\bt-v\b[^"]*"[^>]*>', re.I)
_GROUND = re.compile(r'\bdata-ground="([^"]*)"', re.I)
_TOPIC = re.compile(r'<meta\s+name="mf:topic"\s+content="([^"]+)"', re.I)
_ART_ID = re.compile(r"claude\.ai/(?:code/)?artifact/([A-Za-z0-9-]+)")
_BLOCK = re.compile(r"<!-- accepted-list (\S+) .*?<!-- /accepted-list -->", re.S)


class Refused(Exception):
    """A gate said no; the message says what to do instead."""


def _today() -> str:
    return os.environ.get("LOOP_GATE_TODAY") or _dt.date.today().isoformat()


def _registry_path() -> Path:
    return Path(os.environ.get("LOOP_GATE_REGISTRY")
                or _REPO / ".claude" / "artifacts_registry.json")


def _ledger_path() -> Path:
    env = os.environ.get("LOOP_GATE_LEDGER")
    if env:
        return Path(env)
    sys.path.insert(0, str(_REPO / "src"))
    from utils.paths import get_real_user_home  # first-party, MF001
    return get_real_user_home() / "deferred_work.json"


def load_registry() -> dict:
    return json.loads(_registry_path().read_text()).get("artifacts", {})


# ---------------------------------------------------------------- A. GROUND
def ungrounded(html: str) -> tuple[int, list[str]]:
    """(count of VERIFIED tags with no ground, list of bad ground values)."""
    n, bad = 0, []
    for tag in _TV.findall(html):
        m = _GROUND.search(tag)
        if not m:
            n += 1
        elif m.group(1) not in GROUNDS:
            bad.append(m.group(1))
    return n, bad


def _ground_refusal(added_n: int, bad: list[str], where: str) -> str:
    if bad:
        return (f"GROUND gate: {where} carries VERIFIED with data-ground="
                f"{sorted(set(bad))}. Another model's page is not ground: tag the "
                f"claim 'consistent with' (class t-b, text CONSISTENT) or re-derive "
                f"it from {'/'.join(GROUNDS)} and say which.")
    return (f"GROUND gate: {where} adds {added_n} VERIFIED tag(s) with no "
            f'data-ground. Add data-ground="<{"|".join(GROUNDS)}>" naming what '
            f"you measured, or downgrade the tag.")


# ---------------------------------------------------------------- B. TOPIC
def check_registry(reg: dict) -> list[str]:
    problems, auth = [], {}
    for aid, e in reg.items():
        role, topic = e.get("role"), e.get("topic")
        if role not in ROLES or not topic:
            problems.append(f"{aid}: role must be one of {ROLES} and topic set")
            continue
        if role == "authoritative":
            auth.setdefault(topic, []).append(aid)
    for topic, ids in auth.items():
        if len(ids) > 1:
            problems.append(f"topic {topic}: {len(ids)} authoritative pages {ids} "
                            "— one live claim per topic")
    for aid, e in reg.items():
        if e.get("role") == "superseded":
            s = e.get("successor", "")
            if not (s in reg or s.startswith("ROADMAP:")):
                problems.append(f"{aid}: superseded with no registered successor")
        if e.get("role") == "variant" and e.get("topic") not in auth:
            problems.append(f"{aid}: variant of topic {e.get('topic')} which has "
                            "no authoritative page")
    return problems


def _header(html: str) -> str:
    i = html.lower().find("<section")
    return html if i < 0 else html[:i]


def _authoritative(reg: dict, topic: str):
    return next((a for a, e in reg.items()
                 if e.get("topic") == topic and e.get("role") == "authoritative"), None)


def _apply_edits(text: str, edits) -> str:
    for e in edits or []:
        text = text.replace(e.get("old_str", ""), e.get("new_str", ""), 1)
    return text


def gate_artifact(ti: dict) -> str | None:
    if ti.get("action", "publish") != "publish" or ti.get("asset") or ti.get("type_url"):
        return None
    fp = ti.get("file_path")
    if not fp or not os.path.isfile(fp):
        return None                      # files-only publish / nothing to read
    raw = Path(fp).read_text(errors="replace")
    edits = ti.get("edits")
    html = _apply_edits(raw, edits)
    reg = load_registry()
    m = _ART_ID.search(ti.get("url") or "")

    if m:                                   # an update to an existing page
        aid = m.group(1)
        e = reg.get(aid)
        if e is None:
            return (f"TOPIC gate: artifact {aid} is not in .claude/artifacts_registry.json. "
                    "Register it (title, topic, role) — check first that no page "
                    "on that topic is already authoritative.")
        if edits:
            added = "".join(x.get("new_str", "") for x in edits)
            n, bad = ungrounded(added)
            if n or bad:
                return _ground_refusal(n, bad, "this edit")
        else:
            n, bad = ungrounded(html)
            raw_base = e.get("ungrounded_verified_baseline")
            base = int(raw_base or 0)
            if bad:
                return _ground_refusal(0, bad, "this page")
            if n > base and raw_base is None:
                return (f"GROUND gate: {aid}'s baseline is NOT MEASURED. Publish "
                        "with `edits` (new text is checked alone), or count the "
                        "LIVE page's ungrounded VERIFIED tags (`loop_gate.ungrounded`"
                        " on the Artifact read copy) and set it in the registry — a "
                        "committed edit, so the baseline is visible in git.")
            if n > base:
                return (_ground_refusal(n - base, [], "this page")
                        + f" (frozen baseline {base}, page now has {n})")
        if e["role"] == "superseded" and e.get("successor", "") not in _header(html):
            return (f"TOPIC gate: {aid} is SUPERSEDED by {e.get('successor')} — its "
                    "header (before the first <section>) must link the successor.")
        if e["role"] == "variant":
            a = _authoritative(reg, e["topic"])
            if a and a not in html:
                return (f"TOPIC gate: {aid} is a variant of {a} — link the "
                        "authoritative page so the two cannot drift as equals.")
        return None

    if not _DOMAIN.search(html):            # not a domain page
        return None
    t = _TOPIC.search(html)
    if not t:
        return ('TOPIC gate: a new MeshForge/MeshAnchor page needs <meta '
                'name="mf:topic" content="..."> — and a topic with no '
                "authoritative page yet. Update the existing page instead if one exists.")
    a = _authoritative(reg, t.group(1))
    if a:
        return (f"TOPIC gate: topic {t.group(1)} already has authoritative page {a} "
                f"(https://claude.ai/artifact/{a}). Update it, or register the new "
                "page as its variant first.")
    n, bad = ungrounded(html)
    if n or bad:
        return _ground_refusal(n, bad, "this new page")
    return None


# ---------------------------------------------------------------- C. ACCEPTED
def render_accepted(arc: str) -> str:
    rows = [r for r in json.loads(_ledger_path().read_text()).get("tasks", [])
            if r.get("review_accepted") == arc and r.get("status") == "open"]
    today = _today()
    expired = [r["id"] for r in rows if not r.get("review_after") or r["review_after"] < today]
    if expired:
        raise Refused(f"ACCEPTED gate: row(s) {expired} expired — re-decide each "
                      "(fix it, drop it, or give it a new review_after with a reason).")
    missing = [r["id"] for r in rows if not r.get("accepted_text")]
    if missing:
        raise Refused(f"ACCEPTED gate: row(s) {missing} carry no accepted_text.")
    lines = [f"  - {r['accepted_text']} ({r['id']}, until {r['review_after']})"
             for r in sorted(rows, key=lambda r: r["id"])]
    body = "\n".join(lines)
    h = hashlib.sha256(body.encode()).hexdigest()[:16]
    return (f"<!-- accepted-list {arc} {today} sha256:{h} -->\n{body}\n"
            "<!-- /accepted-list -->")


def gate_agent(ti: dict) -> str | None:
    prompt = ti.get("prompt") or ""
    if "ACCEPTED" not in prompt and "do not report these" not in prompt.lower():
        return None
    how = ("ACCEPTED gate: a reviewer's accepted list must be the block from "
           "`python3 scripts/loop_gate.py accepted <ARC>` (rows in ~/deferred_work.json "
           "with review_accepted, accepted_text, review_after) pasted right under "
           "the ACCEPTED line, unedited.")
    blocks = list(_BLOCK.finditer(prompt))
    if len(blocks) != 1:
        return how
    b = blocks[0]
    try:
        want = render_accepted(b.group(1))
    except Refused as e:
        return str(e)
    if b.group(0) != want:
        return "ACCEPTED gate: the block does not match the ledger today. " + how
    head = prompt[:b.start()].rstrip("\n").splitlines()
    tail = prompt[b.end():].lstrip("\n").splitlines()
    if not head or "ACCEPTED" not in head[-1] and "do not report" not in head[-1].lower():
        return how
    if tail and re.match(r"\s+- ", tail[0]):
        return "ACCEPTED gate: hand-written items follow the generated block. " + how
    return None


# ---------------------------------------------------------------- entry
def _witness(msg: str) -> None:
    p = Path(os.environ.get("LOOP_GATE_WITNESS")
             or _REPO / ".claude" / "loop_gate_failopen.log")
    try:
        with p.open("a") as f:
            f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def hook(stdin_text: str) -> str | None:
    try:
        d = json.loads(stdin_text)
        name, ti = d.get("tool_name"), d.get("tool_input") or {}
    except (ValueError, AttributeError):
        _witness(f"parse-failed payload_len={len(stdin_text)}")
        return None
    try:
        if name == "Artifact":
            return gate_artifact(ti)
        if name == "Agent":
            return gate_agent(ti)
    except (OSError, ValueError, KeyError) as e:   # registry/ledger unreadable
        return (f"loop_gate could not read its registry or ledger ({e!r}) — "
                "refusing rather than passing blind.")
    return None


def main(argv: list[str]) -> int:
    if argv[:1] == ["hook"]:
        why = hook(sys.stdin.read())
        if why:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": why}}))
        return 0
    if argv[:1] == ["accepted"] and len(argv) == 2:
        try:
            print(render_accepted(argv[1]))
        except Refused as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    if argv[:1] == ["check"]:
        problems = check_registry(load_registry())
        for p in problems:
            print("PROBLEM", p)
        print(f"{len(load_registry())} registered, {len(problems)} problem(s)")
        return 1 if problems else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
