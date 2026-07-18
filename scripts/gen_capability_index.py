#!/usr/bin/env python3
"""Generate the TUI capability index + handler manifest from the live registry.

The 98-handler TUI surface is "dark" to the assistant unless something maps it:
the SKILL.md scope note records that the handler count alone drifted 60→64→96
because it was hand-maintained. This script derives the WHOLE command surface
(every section → handler → menu action) straight from ``get_all_handlers()`` so
it can never go stale silently — ``tests/test_capability_index.py`` fails the
build if the committed index drifts from the live registry.

ONE walker, TWO artifacts (honest_failure_modes #5 — no new drift surface):

* ``build_index_markdown()`` → ``capability_index.md`` — the human/skill-facing
  markdown, with ``_clean_desc``'d labels. Pinned by ``test_capability_index.py``.
* ``build_manifest_content()`` → ``src/launcher_tui/handlers/manifest.py`` — a
  generated Python module (``HANDLER_MANIFEST``) carrying each handler's RAW
  menu metadata + import path, so the registry can build the menu + validate
  tags WITHOUT importing the module, and import it lazily at first dispatch
  (``HandlerRegistry.register_lazy`` / ``_LazyHandler``). Pinned by
  ``test_handler_manifest.py``. This is step 1 of the manifest-lazy registry
  arc (``.claude/plans/manifest_lazy_handler_registry_design.md``): additive,
  no behavior change — nothing consumes the manifest yet (main.py stays eager).

Both artifacts share ONE construction pass (``collect_handler_descriptors()``);
``build_index_markdown`` / ``build_manifest_content`` / ``INDEX_PATH`` /
``MANIFEST_PATH`` are imported by the drift tests, so format + location live in
exactly one place.

Usage:
    python3 scripts/gen_capability_index.py          # write both artifacts
    python3 scripts/gen_capability_index.py --check   # exit 1 if either drifts
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
# Where the generated artifacts live — read by the skill/registry, pinned by
# the drift tests (one constant per artifact, honest_failure_modes #5).
INDEX_PATH = REPO_ROOT / ".claude" / "skills" / "meshforge" / "capability_index.md"
MANIFEST_PATH = REPO_ROOT / "src" / "launcher_tui" / "handlers" / "manifest.py"

# Mirror tests/conftest.py so handler imports resolve the same way the suite
# (and the real TUI) sees them: ``handlers`` + ``handler_protocol`` are top-level
# under launcher_tui, and handlers reach into ``utils`` under src.
_LAUNCHER = REPO_ROOT / "src" / "launcher_tui"
_SRC = REPO_ROOT / "src"
for _p in (str(_LAUNCHER), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _clean_desc(desc: str) -> str:
    """Make a whiptail-column-aligned label legible + table-safe.

    Menu descriptions are space-padded for the TUI's two-column layout
    ("Label      Detail"); collapse the gap to an em-dash separator and escape
    pipes so they cannot break the markdown table.
    """
    desc = desc.replace("|", r"\|")
    desc = re.sub(r"\s{2,}", " — ", desc.strip())
    return desc


def collect_handler_descriptors() -> List[dict]:
    """The shared walker: one construction pass over the live registry, RAW.

    Returns one descriptor dict per handler, in ``get_all_handlers()`` order:

      * ``handler_id``  — the dispatch key / unique id
      * ``module``      — ``cls.__module__`` (e.g. ``"handlers.rf_tools"``), the
                          importable path with ``src/launcher_tui`` on sys.path
      * ``class_name``  — ``cls.__name__``
      * ``menu_section``— the menu section this handler owns tags in
      * ``menu_items``  — the RAW ``menu_items()`` tuples ``(tag, desc, flag)``,
                          NOT ``_clean_desc``'d. The registry's dispatch-time
                          equality check compares these to the live values, so
                          they must be byte-exact (padded labels, ``flag=None``).
      * ``lifecycle``   — True when the handler defines ``on_startup`` OR
                          ``on_shutdown`` (not the both-hooks LifecycleHandler
                          isinstance: an on_startup-only handler like
                          startup_health is invoked directly by main.py and
                          MUST stay eager too — step-2 review BLOCKER-1).
      * ``error``       — ``"Type: msg"`` if the handler can't be constructed or
                          its ``menu_items()`` raises; ``None`` when healthy. A
                          silently-dropped handler would read as "this part of
                          the UI does nothing" (honest_failure_modes #1/#9), so
                          it is recorded, never omitted.

    Both emitters (markdown index + handler manifest) consume this, so the two
    artifacts can never disagree about the handler set.
    """
    from handlers import get_all_handlers
    from handler_protocol import TUIContext

    # A neutral context: no dialog, no feature flags. ``feature_enabled()``
    # returns True for everything, so the surface is the FULL command set
    # independent of any deployment profile (keeps both artifacts deterministic).
    neutral_ctx = TUIContext(dialog=None)

    descriptors: List[dict] = []
    for cls in get_all_handlers():
        d = {
            "handler_id": getattr(cls, "handler_id", "") or "",
            "module": cls.__module__,
            "class_name": cls.__name__,
            "menu_section": getattr(cls, "menu_section", "") or "",
            "menu_items": [],
            "lifecycle": False,
            "error": None,
        }
        try:
            inst = cls()
            if hasattr(inst, "set_context"):
                inst.set_context(neutral_ctx)
            # Instance attrs win over class attrs if the handler sets them.
            d["handler_id"] = getattr(inst, "handler_id", d["handler_id"]) or d["handler_id"]
            d["menu_section"] = getattr(inst, "menu_section", d["menu_section"]) or d["menu_section"]
            # Either hook ⇒ eager. isinstance(LifecycleHandler) needs BOTH
            # hooks, but main.py's direct get_handler(id).on_startup() bypass
            # makes any startup work eager-load-bearing (BLOCKER-1).
            d["lifecycle"] = (
                hasattr(inst, "on_startup") or hasattr(inst, "on_shutdown")
            )
            # Validate the 3-tuple shape INSIDE the try (a malformed row is an
            # error to record, never a generator crash) and only commit the
            # normalized list once the whole loop succeeds — a mid-list failure
            # leaves menu_items=[] + error set, exactly as the old collector did.
            norm: List[Tuple] = []
            for tag, desc, flag in inst.menu_items():
                norm.append((tag, desc, flag))
            d["menu_items"] = norm
        except Exception as exc:  # noqa: BLE001 — surfaced (error field), not dropped
            d["error"] = f"{type(exc).__name__}: {exc}"
        descriptors.append(d)
    return descriptors


def collect_handlers() -> List[dict]:
    """Markdown view of the registry (adapts ``collect_handler_descriptors()``).

    Kept as the ``build_index_markdown()``-shaped structure — class, handler_id,
    section, items (tag, CLEANED desc, flag-or-``""``), error — so the committed
    capability index is byte-identical across this refactor. An errored handler
    carries no items (the markdown renders its error row, ignoring items anyway).
    """
    rows: List[dict] = []
    for d in collect_handler_descriptors():
        entry = {
            "class": d["class_name"],
            "handler_id": d["handler_id"],
            "section": d["menu_section"],
            "items": [],
            "error": d["error"],
        }
        if d["error"] is None:
            for tag, desc, flag in d["menu_items"]:
                entry["items"].append((str(tag), _clean_desc(str(desc)), flag or ""))
        rows.append(entry)
    return rows


def build_index_markdown() -> str:
    """Render the capability index markdown. Pure function of the code/registry.

    Deterministic: sections sorted, handlers sorted by id, items sorted by tag.
    No environment, clock, or live-service state enters the output, so the
    committed file is a stable golden the drift test can pin exactly.
    """
    rows = collect_handlers()

    sections: dict = {}
    for r in rows:
        sections.setdefault(r["section"], []).append(r)

    total_handlers = len(rows)
    total_items = sum(len(r["items"]) for r in rows)
    total_sections = len(sections)
    errored = [r["class"] for r in rows if r["error"]]

    out: List[str] = []
    out.append("# MeshForge TUI Capability Index")
    out.append("")
    out.append("> AUTO-GENERATED by `scripts/gen_capability_index.py` — do NOT edit by hand.")
    out.append("> Pinned to the live handler registry by `tests/test_capability_index.py`;")
    out.append("> if that test fails, the TUI surface changed — regenerate with")
    out.append("> `python3 scripts/gen_capability_index.py` and commit the result.")
    out.append(">")
    out.append("> Purpose: make the TUI's command surface legible so it isn't \"dark\" to")
    out.append("> the assistant (the handler-count-drift problem). Asked \"can the TUI do")
    out.append("> X?\", grep this file for the capability, then open the handler under")
    out.append("> `src/launcher_tui/handlers/`.")
    out.append("")
    out.append(
        f"**{total_handlers} handlers · {total_items} menu actions · "
        f"{total_sections} sections** (all derived from `get_all_handlers()`, never hardcoded)."
    )
    out.append("")
    out.append(
        "A non-empty **Flag** means the action only appears when that "
        "deployment-profile feature is enabled (blank = always visible)."
    )
    out.append("")

    if errored:
        # Honest surface: a handler whose menu_items could not be read is a
        # real gap, shown loudly — never folded into a healthy-looking summary.
        out.append("> ⚠️ **Handlers whose menu items could not be read this build:** "
                   + ", ".join(sorted(errored)) + ". See the per-section error rows below.")
        out.append("")

    for section in sorted(sections):
        handlers = sorted(sections[section], key=lambda r: (r["handler_id"], r["class"]))
        title = section if section else "(no section)"
        out.append(f"## `{title}`")
        out.append("")
        out.append("| Action tag | Description | Flag | Handler |")
        out.append("|---|---|---|---|")
        any_row = False
        for r in handlers:
            if r["error"]:
                out.append(f"| _(error)_ | ⚠️ {r['error']} |  | {r['class']} |")
                any_row = True
                continue
            if not r["items"]:
                # Lifecycle/dispatch-only handler: listed so the surface is
                # complete, not silently absent.
                out.append(f"| _(no menu items)_ | lifecycle / dispatch only |  | {r['class']} |")
                any_row = True
                continue
            for tag, desc, flag in sorted(r["items"], key=lambda t: t[0]):
                flag_cell = f"`{flag}`" if flag else ""
                out.append(f"| `{tag}` | {desc} | {flag_cell} | {r['class']} |")
                any_row = True
        if not any_row:
            out.append("| _(none)_ |  |  |  |")
        out.append("")

    return "\n".join(out).rstrip("\n") + "\n"


def _render_manifest_entry(d: dict) -> str:
    """Render one descriptor as a Python dict literal for the manifest.

    ``repr()`` on every value keeps the source a faithful, round-trippable
    snapshot: string labels re-parse byte-exact and ``menu_items`` re-load as
    the same tuples the live ``menu_items()`` returns (the equality check).
    """
    lines = ["    {"]
    lines.append(f'        "handler_id": {d["handler_id"]!r},')
    lines.append(f'        "module": {d["module"]!r},')
    lines.append(f'        "class_name": {d["class_name"]!r},')
    lines.append(f'        "menu_section": {d["menu_section"]!r},')
    lines.append(f'        "lifecycle": {d["lifecycle"]!r},')
    lines.append(f'        "error": {d["error"]!r},')
    items = d["menu_items"]
    if not items:
        lines.append('        "menu_items": [],')
    else:
        lines.append('        "menu_items": [')
        for it in items:
            lines.append(f"            {tuple(it)!r},")
        lines.append("        ],")
    lines.append("    },")
    return "\n".join(lines)


def build_manifest_content() -> str:
    """Render ``handlers/manifest.py``. Pure function of the code/registry.

    Deterministic: entries sorted by (handler_id, class_name), so an unrelated
    reorder in ``get_all_handlers()`` does not churn the file. No environment,
    clock, or live-service state enters the output — a stable golden the drift
    test can pin exactly.
    """
    descriptors = sorted(
        collect_handler_descriptors(),
        key=lambda d: (d["handler_id"], d["class_name"]),
    )
    total = len(descriptors)
    total_items = sum(len(d["menu_items"]) for d in descriptors)

    out: List[str] = []
    out.append('"""AUTO-GENERATED handler manifest — do NOT edit by hand.')
    out.append("")
    out.append("Generated by ``scripts/gen_capability_index.py`` (run it to regenerate;")
    out.append("``--check`` fails if this file drifts from the live handler registry).")
    out.append("Pinned by ``tests/test_handler_manifest.py``.")
    out.append("")
    out.append("Each entry is a static, per-commit snapshot of one TUI handler's menu")
    out.append("metadata — enough to build the menu and validate tags WITHOUT importing")
    out.append("the handler's module. The module is imported at first dispatch of one of")
    out.append("its tags (``HandlerRegistry.register_lazy`` / ``_LazyHandler``). The")
    out.append("``menu_items`` tuples are RAW (padded labels, ``flag`` is None/str as the")
    out.append("handler returns them), so the dispatch-time equality check matches the")
    out.append("live ``menu_items()`` byte-for-byte. ``lifecycle: True`` handlers stay")
    out.append("EAGER (their on_startup/on_shutdown cannot run if imported lazily).")
    out.append('"""')
    out.append("")
    out.append(
        f"# {total} handlers · {total_items} menu actions "
        "(derived from get_all_handlers(), never hand-edited)."
    )
    out.append("")
    out.append("HANDLER_MANIFEST = [")
    for d in descriptors:
        out.append(_render_manifest_entry(d))
    out.append("]")
    return "\n".join(out) + "\n"


def _check_artifact(path: Path, content: str) -> bool:
    """Return True if ``path`` is IN DRIFT (missing or != freshly-built content)."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing != content:
        rel = os.path.relpath(path, REPO_ROOT)
        print(f"DRIFT: {rel} is out of date — run "
              f"`python3 scripts/gen_capability_index.py`", file=sys.stderr)
        return True
    return False


def main(argv: List[str]) -> int:
    check_only = "--check" in argv[1:]
    index_content = build_index_markdown()
    manifest_content = build_manifest_content()

    if check_only:
        # Check BOTH; report every drifting artifact, then fail if any drifted.
        drift = _check_artifact(INDEX_PATH, index_content)
        drift = _check_artifact(MANIFEST_PATH, manifest_content) or drift
        if drift:
            return 1
        print("OK: capability index + handler manifest match the live registry.")
        return 0

    written = []
    for path, content in ((INDEX_PATH, index_content),
                          (MANIFEST_PATH, manifest_content)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(f"{os.path.relpath(path, REPO_ROOT)} ({len(content)} bytes)")
    print("Wrote " + " + ".join(written) + ".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
