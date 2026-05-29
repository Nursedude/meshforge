"""Phase 0 inventory: enumerate every TUI selection (section, tag, desc, flag).

Read-only. Instantiates each handler with no context and calls menu_items().
"""
import os
import sys

# __file__-relative so it runs from anywhere (e.g. over SSH from $HOME), not
# just the repo root.
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_here, "..", "src"),
                os.path.join(_here, "..", "src", "launcher_tui")]

from handlers import get_all_handlers  # noqa: E402

rows = []
errors = []
for h in get_all_handlers():
    name = h.__name__
    try:
        inst = h()
    except Exception as e:  # noqa: BLE001
        errors.append((name, "__init__", repr(e)))
        continue
    section = getattr(inst, "menu_section", getattr(h, "menu_section", "?"))
    try:
        items = inst.menu_items()
    except Exception as e:  # noqa: BLE001
        errors.append((name, "menu_items", repr(e)))
        continue
    if not items:
        rows.append((section, "(no items)", name, "", ""))
        continue
    for it in items:
        tag = it[0] if len(it) > 0 else "?"
        desc = it[1] if len(it) > 1 else ""
        flag = it[2] if len(it) > 2 else ""
        rows.append((section, tag, name, desc, flag or ""))

rows.sort(key=lambda r: (r[0], r[2], r[1]))
print(f"TOTAL_SELECTIONS={len([r for r in rows if r[1] != '(no items)'])}")
print(f"HANDLERS={len(get_all_handlers())}")
print("=" * 100)
cur = None
for section, tag, handler, desc, flag in rows:
    if section != cur:
        print(f"\n## SECTION: {section}")
        cur = section
    print(f"  [{tag}] | {handler} | {desc} | flag={flag}")

if errors:
    print("\n" + "=" * 100)
    print("ERRORS:")
    for name, where, err in errors:
        print(f"  {name}.{where}: {err}")
