"""DialogBackend.menu() auto-fit — does a screen use the terminal it has?

The TUI arc listed "whether the 22-item screens scroll correctly on a
24-row terminal" as UNKNOWN: the fit arithmetic at backend.py was
plausible and no test covered it. scripts/tui_smoke.py answered it
against real whiptail and found the opposite problem — list_height read
**14 at 24, 30, 40 and 60 rows**, so a 22-item menu scrolled identically
on a terminal that could show every row with space left over. `needed`
was computed from the DEFAULT list_height rather than len(choices), so
the grow step only ever grew the box to fit 14 rows.

These tests are the CI-side pin. The PTY drill cannot run under pytest
(whiptail opens /dev/tty; CI has no controlling terminal), so what runs
here is the arithmetic, driven through the real menu() with the
subprocess stubbed at _run.
"""

import contextlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src',
                                'launcher_tui'))

import backend as be  # noqa: E402


@contextlib.contextmanager
def _terminal(rows, cols):
    """Pretend the terminal is rows x cols, and ALWAYS put it back.

    os.get_terminal_size is process-global and backend.py reads it on
    every dialog, so a patch that outlived this block would silently
    re-size every later test in the session. Restored in a finally, not
    by garbage collection.
    """
    real_size = os.get_terminal_size
    real_run = be.DialogBackend._run
    captured = {}

    def fake_run(self, args, timeout=None):
        # args: --title T --menu TEXT h w lh -- tag desc ...
        captured['h'] = int(args[4])
        captured['lh'] = int(args[6])
        return 0, args[8]

    os.get_terminal_size = lambda *a: os.terminal_size((cols, rows))
    be.DialogBackend._run = fake_run
    try:
        yield captured
    finally:
        os.get_terminal_size = real_size
        be.DialogBackend._run = real_run


def _fit(n_items, rows, cols=100, text="one line subtitle:",
         status_bar=False):
    """Run the REAL menu() at a fake terminal size; return (box_h, list_h)."""
    with _terminal(rows, cols) as captured:
        d = be.DialogBackend()
        if status_bar:
            class _Bar:
                def get_status_line(self):
                    return "status"
            d.set_status_bar(_Bar())
        choices = [(f"t{i}", f"Item{i}   some description")
                   for i in range(n_items)]
        d.menu("Title", text, choices)
        return captured['h'], captured['lh']


class TestListGrowsWithTerminal:
    """The defect scripts/tui_smoke.py found, pinned."""

    def test_tall_terminal_shows_every_row(self):
        for rows in (30, 40, 60):
            _h, lh = _fit(22, rows)
            assert lh >= 22, (
                f"a 22-item menu shows only {lh} rows on a {rows}-row "
                "terminal — the list is not growing with the terminal "
                "(regression of the 2026-09-16 fit fix)")

    def test_list_height_actually_varies_with_terminal_height(self):
        """The tell that exposed it: the number never moved."""
        seen = {lh for rows in (24, 30, 40, 60)
                for _h, lh in [_fit(22, rows)]}
        assert len(seen) > 1, (
            f"list_height is {seen} at every terminal height from 24 to "
            "60 rows — that is the pre-fix behaviour, where `needed` was "
            "computed from the default 14 instead of len(choices)")

    def test_short_terminal_still_shrinks(self):
        """F3's small-terminal behaviour must survive the growth fix."""
        h, lh = _fit(22, 10)
        assert lh == 4, f"expected the 4-row floor on a 10-row terminal, got {lh}"
        assert h <= 10, f"box {h} rows on a 10-row terminal"

    def test_box_never_exceeds_the_terminal(self):
        for rows in (8, 10, 16, 22, 24, 30, 40, 60):
            for n in (1, 13, 22, 29):
                h, _lh = _fit(n, rows)
                assert h <= rows, (
                    f"box {h} rows on a {rows}-row terminal ({n} items)")

    def test_backtitle_overhead_is_respected(self):
        """A status bar costs two rows; the box must leave room for it."""
        for rows in (24, 30, 40):
            h, _lh = _fit(22, rows, status_bar=True)
            assert h <= rows - 2, (
                f"box {h} rows on a {rows}-row terminal with a backtitle "
                "— the two status-bar rows were not reserved")

    def test_small_menu_is_not_inflated(self):
        """Growth must not pad a short menu into a giant empty box."""
        h, lh = _fit(3, 60)
        assert lh <= 14, f"a 3-item menu asked for {lh} list rows"
        assert h <= 22, f"a 3-item menu produced a {h}-row box"
