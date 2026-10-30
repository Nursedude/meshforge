"""No tell silently deleted — the persistent_issues context trim (phase 2c, 2026-10-29).

The tell table in ``.claude/foundations/persistent_issues.md`` loads on every
turn, so its rows were cut to symptom | class | quick check | trap, and the
whole pre-trim file moved byte for byte into ``persistent_issues_archive.md``.
An operator greps the SYMPTOM at a terminal, so the symptom cell is the
contract: it stays word for word in the live table, and the archive keeps the
exact original (pinned by hash, so a story cannot be edited out of it).
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / ".claude" / "foundations" / "persistent_issues.md"
ARCHIVE = ROOT / ".claude" / "foundations" / "persistent_issues_archive.md"
FIXTURE = json.loads((ROOT / "tests" / "fixtures"
                      / "persistent_issues_tells_2026_10_29.json")
                     .read_text(encoding="utf-8"))
FROZEN = FIXTURE["symptoms"]
VERBATIM_HEADING = "## Tell rows — verbatim before the 2026-10-29 trim"
FENCE_OPEN, FENCE_CLOSE = "~~~~markdown\n", "\n~~~~\n"
ROW_CAP_BYTES = 700


def _tell_rows(text):
    """Rows of the table under the tells heading — the table ends at its
    first non-row line, so a heading added later cannot hide rows."""
    lines = text[text.index("### Quick diagnostic tells"):].split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith("| "))
    rows = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        rows.append(line)
    return [r for r in rows if not r.startswith(("| Symptom", "|---"))]


def _snapshot():
    text = ARCHIVE.read_text(encoding="utf-8")
    section = text[text.index(VERBATIM_HEADING):]
    body = section[section.index(FENCE_OPEN) + len(FENCE_OPEN):]
    return body[:body.index(FENCE_CLOSE) + 1]


def test_every_frozen_symptom_is_still_a_live_tell():
    live = {row.split(" | ")[0][2:] for row in _tell_rows(
        LIVE.read_text(encoding="utf-8"))}
    missing = [s for s in FROZEN if s not in live]
    assert not missing, f"tell rows deleted or their symptom reworded: {missing}"


def test_the_archive_keeps_the_original_file_byte_for_byte():
    digest = hashlib.sha256(_snapshot().encode("utf-8")).hexdigest()
    assert digest == FIXTURE["snapshot_sha256"], (
        "the archived pre-trim snapshot was edited — it must stay byte-exact")


def test_the_snapshot_holds_every_original_row():
    snap = _snapshot()
    missing = [s for s in FROZEN if f"\n| {s} | " not in snap]
    assert not missing, f"original row not archived: {missing}"


def test_live_tell_rows_stay_short():
    long_rows = [(len(r.encode()), r[:60]) for r in _tell_rows(
        LIVE.read_text(encoding="utf-8")) if len(r.encode()) > ROW_CAP_BYTES]
    assert not long_rows, (
        f"rows over {ROW_CAP_BYTES} B — move the story to the archive: {long_rows}")
