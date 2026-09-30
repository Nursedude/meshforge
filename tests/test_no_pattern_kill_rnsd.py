"""`pkill -f rnsd` kills any process whose COMMAND LINE contains "rnsd" — an
operator's `journalctl -u rnsd -f`, an editor on rnsd.service, this repo's own
scripts. Measured 2026-09-29 on the manager box: `pgrep -af rnsd` matched a bash and
a ugrep besides rnsd; `pgrep -ax rnsd` matched only rnsd. Match the process
NAME (`-x`). Covers both the argv form and the operator-hint text, because a
hint is run by the operator exactly as written.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
ARGV = re.compile(r"""['"]pkill['"]\s*,\s*['"]-f['"]\s*,\s*['"]rnsd['"]""")
HINT = re.compile(r"pkill\s+-f\s+rnsd\b")


def test_scanner_sees_the_tree():
    assert len(list(SRC.rglob("*.py"))) > 50


def test_no_pattern_kill_of_rnsd_anywhere_in_src():
    hits = []
    for p in sorted(SRC.rglob("*.py")):
        for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue  # a comment explaining why -f is wrong is not a use
            if ARGV.search(line) or HINT.search(line):
                hits.append(f"{p.relative_to(SRC)}:{n}: {line.strip()}")
    assert hits == [], "\n".join(hits)
