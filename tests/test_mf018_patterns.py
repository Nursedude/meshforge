"""MF018 pattern drill — the Q3 sweep's guard must actually fire.

A guard that has never failed is not evidence it works (the Issue #29
Layer-2 lesson: 8 contracts passed every run while enforcing nothing).
Each planted violation here is a REAL string shape found in the tree on
2026-08-14 that the pre-sweep patterns missed for months; each clean
line is a code shape (argv lists, helpers) that must NOT fire.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import lint


def _count(text):
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as f:
        f.write(text)
        path = f.name
    try:
        n, _hits = lint._count_in_domain_escapes(path)
        return n
    finally:
        os.unlink(path)


class TestNewPatternsFire:
    # Every one of these is a verbatim shape from the 2026-08-14 audit's
    # 39-string gap list. If a pattern regresses, its planted twin fails.
    VIOLATIONS = [
        'print("  Fix: sudo systemctl enable meshforge-map")',
        '"Start rnsd:  sudo systemctl start rnsd\\n"',
        '"  sudo apt install -y tmux\\n"',
        '"  sudo apt-get install mosquitto\\n"',
        'hint="check \'systemctl status rnsd\'"',
        '"sudo pip3 install --break-system-packages meshtastic"',
        '"  sudo pipx upgrade nomadnet"',
        'f"Try: pip3 install {module}"',
        '"check \'journalctl -u meshforge-gateway\'"',
        '"  sudo systemctl edit rnsd\\n"',
    ]

    def test_each_planted_violation_fires(self):
        missed = [v for v in self.VIOLATIONS if _count(v) == 0]
        assert not missed, (
            f"{len(missed)} planted violation(s) no longer fire — the "
            f"pattern regressed: {missed}"
        )


class TestCodeShapesStayClean:
    # argv lists and internal code must never trip the prose patterns —
    # these are real shapes from the tree.
    CLEAN = [
        "cmd = ['sudo', 'python3', str(cli), 'normalize', '--yes']",
        "subprocess.run(['systemctl', 'is-active', 'rnsd'], timeout=5)",
        "cmd = ['journalctl']",
        "argv = ['sudo', '-u', user, 'systemctl', '--user', 'restart']",
        "proc = subprocess.run(['apt-get', 'install', '-y', 'whiptail'])",
        "from utils.pip_install import pip_install",
        "start_service('meshforge-map')",
    ]

    def test_code_shapes_do_not_fire(self):
        noisy = [c for c in self.CLEAN if _count(c) > 0]
        assert not noisy, (
            f"pattern false-positives on code shapes: {noisy}"
        )


class TestMarkerStillExempts:
    def test_marked_line_is_skipped(self):
        line = ('print("  sudo systemctl start rnsd")'
                '  # in-domain-ok: test exemption')
        assert _count(line) == 0

    def test_tui_scan_is_currently_clean(self):
        # The sweep's end state: zero unmarked escapes in the live tree.
        import glob
        dirty = {}
        for f in glob.glob(os.path.join(
                os.path.dirname(__file__), '..',
                'src', 'launcher_tui', '**', '*.py'), recursive=True):
            n, hits = lint._count_in_domain_escapes(f)
            if n:
                dirty[os.path.basename(f)] = hits
        assert not dirty, (
            f"unmarked shell-escape(s) crept back in: {dirty} — close them "
            "with an in-app action/pointer, or mark a genuine exception"
        )
