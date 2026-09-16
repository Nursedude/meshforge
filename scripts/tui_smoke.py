#!/usr/bin/env python3
"""Render every TUI screen through REAL whiptail, in a real PTY.

The register the TUI arc opened records ``RAN vs READ = 0 / 103``: every
menu label in this project has been *read* by a test and none had ever
been *drawn*. Unit tests prove a handler is registered, that its tag
dispatches, and that ``menu_items()`` returns a list. None of that
touches whiptail, which opens ``/dev/tty`` directly and therefore cannot
be exercised by pytest under CI — there is no controlling terminal
there. So the render layer, the part the operator actually looks at, has
always been BELIEVED.

This driver makes it VERIFIED. For every screen the TUI builds, at
several terminal sizes, it forks a PTY, runs the real
``DialogBackend.menu()`` inside it, captures what whiptail painted on
that terminal, and reports how many of the screen's labels physically
appeared.

⚠️ IT NEVER DISPATCHES. It calls ``menu()`` with the real choice list
and reads the selection back; it does not run ``execute()``, does not
call a handler method, and cannot restart a service or reboot a box.
Walking a NOC's leaves automatically on a live gateway is a different
and much riskier program than proving its menus draw, and it is not
this one. Every screen is rendered and then dismissed.

Why several terminal sizes: ``DialogBackend.menu()`` carries auto-fit
arithmetic (grow the box to its content, then shrink the list if it
still will not fit) that no test covered, and the arc's own open
questions list "whether the 22-item screens scroll correctly on a 24-row
terminal" as UNKNOWN. 24x80 is the floor that matters — it is what a
phone SSH session and a serial console give you in the field.

Usage:
    python3 scripts/tui_smoke.py                  # default size matrix
    python3 scripts/tui_smoke.py --sizes 24x80
    python3 scripts/tui_smoke.py --json out.json

Exit code: 0 when every screen rendered on every size, 1 otherwise, 2
when the driver could not run at all (no whiptail, no pty).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import select
import shutil
import struct
import sys
import tempfile
import termios
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
TUI = SRC / "launcher_tui"

DEFAULT_SIZES = ((24, 80), (30, 100), (40, 120))
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\x1b[=>]")


# --------------------------------------------------------------- screens

def collect_screens():
    """Every (name, title, subtitle, choices) the TUI renders.

    Built from the same code the TUI uses — ``_build_section_menu``
    merging the live registry with SECTION_ORDERINGS, and the main
    menu's own builder — so this renders the real lists rather than a
    reconstruction that could drift from them.
    """
    sys.path.insert(0, str(SRC))
    sys.path.insert(0, str(TUI))
    import logging
    # The registry logs one DEBUG line per handler; this driver's output
    # IS the report, so keep it readable.
    logging.disable(logging.INFO)
    from types import SimpleNamespace

    import main as tui_main
    from handler_protocol import TUIContext
    from handler_registry import HandlerRegistry
    from handlers import get_all_handlers

    ctx = TUIContext(dialog=SimpleNamespace())
    registry = HandlerRegistry(ctx)
    ctx.registry = registry
    for cls in get_all_handlers():
        registry.register(cls())

    screens = []

    # The top-level menu, from its own builder (it has conditional rows).
    captured = []
    fake = SimpleNamespace(
        _get_menu_status_hint=lambda: "MeshForge smoke",
        _feature_enabled=lambda f: True,
        _MAX_DIALOG_RETRIES=3,
        _handle_main_choice=lambda c: None,
        dialog=SimpleNamespace(
            menu=lambda t, s, c: (captured.append(list(c)), "x")[1],
            yesno=lambda *a: True),
    )
    tui_main.MeshForgeLauncher._run_main_menu(fake)
    if captured:
        screens.append(("main", "MeshForge NOC", "Network Operations Center",
                        captured[0]))

    builder = tui_main.MeshForgeLauncher._build_section_menu
    holder = SimpleNamespace(_registry=registry)
    for section in sorted(registry.section_names):
        if section == "main":
            continue
        ordering = tui_main.SECTION_ORDERINGS.get(section)
        choices = builder(holder, section, [], ordering)
        if len(choices) > 1:          # more than the bare "Back"
            screens.append((section, section.replace("_", " ").title(),
                            f"{section} section:", choices))
    return screens


# ------------------------------------------------------------------- pty

def render_in_pty(title, subtitle, choices, rows, cols, timeout=25.0):
    """Draw one screen with real whiptail on a rows x cols PTY.

    Returns a dict: ok, selected, painted (labels seen on screen),
    total, note.
    """
    fd, result_path = tempfile.mkstemp(prefix="tui_smoke_", suffix=".json")
    os.close(fd)
    master, slave = os.openpty()
    # Size the PTY BEFORE forking. Setting it afterwards races the child:
    # DialogBackend.menu() calls os.get_terminal_size() immediately, and a
    # child that won the race would silently measure the default 24 rows
    # and we would "verify" a size we never actually tested.
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    pid = os.fork()
    if pid == 0:                                     # ---------- child
        try:
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            for target in (0, 1, 2):
                os.dup2(slave, target)
            if slave > 2:
                os.close(slave)
            os.close(master)
            sys.path.insert(0, str(SRC))
            sys.path.insert(0, str(TUI))
            from backend import DialogBackend
            backend = DialogBackend()
            selected = backend.menu(title, subtitle, choices)
            size = os.get_terminal_size()
            with open(result_path, "w") as fh:
                json.dump({"selected": selected, "rows": size.lines,
                           "cols": size.columns}, fh)
            os._exit(0)
        except BaseException as exc:                 # noqa: BLE001
            import traceback
            try:
                with open(result_path, "w") as fh:
                    json.dump({"error": repr(exc),
                               "tb": traceback.format_exc()}, fh)
            except Exception:
                pass
            os._exit(3)

    # ------------------------------------------------------------ parent
    os.close(slave)
    painted = bytearray()
    deadline = time.monotonic() + timeout
    last_data = time.monotonic()
    sent_enter = False
    reaped = False
    eof = False
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                eof = True          # slave side closed: the child is done
            else:
                if chunk:
                    painted += chunk
                    last_data = time.monotonic()
                else:
                    eof = True
        # whiptail has finished painting once the terminal goes quiet;
        # Enter then accepts the highlighted (first) row.
        if not sent_enter and painted and time.monotonic() - last_data > 0.6:
            os.write(master, b"\r")
            sent_enter = True
        done, _code = os.waitpid(pid, os.WNOHANG)
        if done:
            reaped = True
            break
        if eof:
            # EOF is the child closing the PTY, which it does on the way
            # out — wait for it rather than calling a clean exit a
            # timeout, which is what the first version of this loop did.
            os.waitpid(pid, 0)
            reaped = True
            break
    note = ""
    if not reaped:
        try:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        except OSError:
            pass
        note = ("timed out — whiptail never returned"
                if sent_enter else
                "timed out — screen never went quiet, Enter was never sent")
    os.close(master)

    try:
        with open(result_path) as fh:
            payload = json.load(fh)
    except Exception:
        payload = {}
    os.unlink(result_path)

    screen = _ANSI.sub("", painted.decode("utf-8", "replace"))
    # A label counts as PAINTED only if its visible text is on the
    # screen. First word, because whiptail truncates to the box width
    # and the labels carry column-alignment padding.
    seen = 0
    for _tag, label in choices:
        word = label.strip().split()[0] if label.strip() else ""
        if word and word in screen:
            seen += 1

    first_tag = choices[0][0] if choices else None
    ok = (payload.get("error") is None
          and payload.get("selected") == first_tag
          and payload.get("rows") == rows
          and payload.get("cols") == cols
          and not note)
    if payload.get("error"):
        note = payload["error"]
    elif not note and payload.get("selected") != first_tag:
        note = f"selected {payload.get('selected')!r}, expected {first_tag!r}"
    elif not note and (payload.get("rows"), payload.get("cols")) != (rows, cols):
        note = (f"child measured {payload.get('rows')}x{payload.get('cols')},"
                f" PTY was {rows}x{cols}")
    return {"ok": ok, "selected": payload.get("selected"), "painted": seen,
            "total": len(choices), "note": note,
            "bytes": len(painted)}


# ------------------------------------------------------------------ main

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--sizes", default=None,
                   help="comma list like 24x80,40x120")
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--only", default=None, help="one screen name")
    args = p.parse_args(argv)

    if not shutil.which("whiptail") and not shutil.which("dialog"):
        print("UNKNOWN: no whiptail/dialog on this box — nothing was "
              "rendered. Absent is not healthy; install whiptail to run "
              "this drill.", file=sys.stderr)
        return 2

    sizes = DEFAULT_SIZES
    if args.sizes:
        sizes = tuple(tuple(int(v) for v in s.lower().split("x"))
                      for s in args.sizes.split(","))

    screens = collect_screens()
    if args.only:
        screens = [s for s in screens if s[0] == args.only]
    if not screens:
        print("UNKNOWN: no screens collected", file=sys.stderr)
        return 2

    rows_out, failures = [], 0
    labels_total = labels_painted = 0
    print(f"tui_smoke — {len(screens)} screen(s) x {len(sizes)} size(s), "
          f"real whiptail in a PTY\n")
    for name, title, subtitle, choices in screens:
        for rows, cols in sizes:
            r = render_in_pty(title, subtitle, choices, rows, cols)
            r.update(screen=name, rows=rows, cols=cols)
            rows_out.append(r)
            labels_total += r["total"]
            labels_painted += r["painted"]
            mark = "ok  " if r["ok"] else "FAIL"
            if not r["ok"]:
                failures += 1
            print(f"  {mark} {name:<18} {rows:>3}x{cols:<4} "
                  f"painted {r['painted']:>2}/{r['total']:<2}"
                  + (f"  {r['note']}" if r["note"] else ""))

    print(f"\nscreens rendered: {len(rows_out) - failures}/{len(rows_out)}")
    print(f"labels painted on a real terminal: {labels_painted}/{labels_total}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows_out, indent=1))
        print(f"json: {args.json_out}")
    if failures:
        print(f"\nFAIL — {failures} screen/size combination(s) did not render")
        return 1
    print("\nPASS — every screen rendered at every size")
    return 0


if __name__ == "__main__":
    sys.exit(main())
