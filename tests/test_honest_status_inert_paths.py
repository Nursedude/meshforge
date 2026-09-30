"""honest_status's audit-record allowlist must stay apart from every path a
running box READS (2026-09-29, the docs-drift loop fix).

The fleet SHA leg now counts a box behind ONLY by commits to
HS_AUDIT_RECORD_GLOBS as converged-with-a-note. That is safe only while no
running code consumes those files. The offline oracle reads repo markdown as
its retrieval corpus (`mini_dudeai.offline_oracle.default_roots`), and agent
sessions on a box read CLAUDE.md and its @-includes — so "docs are inert" is
FALSE here, and the allowlist must never drift into either set. Two consumers
of one artifact share one check (honest_failure_modes #5).
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mini_dudeai import offline_oracle  # noqa: E402

SCRIPT = ROOT / "scripts" / "honest_status.sh"


def _audit_globs():
    m = re.search(r"^HS_AUDIT_RECORD_GLOBS='([^']*)'", SCRIPT.read_text(), re.M)
    assert m, "HS_AUDIT_RECORD_GLOBS not found in honest_status.sh — the parse is aimed wrong"
    globs = m.group(1).split()
    assert globs, "allowlist parsed EMPTY — would test nothing"
    return globs


def _oracle_repo_globs():
    out = []
    for _label, pattern in offline_oracle.default_roots():
        try:
            rel = os.path.relpath(pattern, ROOT)
        except ValueError:
            continue
        if not rel.startswith(".."):          # repo-relative roots only
            out.append(rel)
    assert out, "no repo-relative oracle roots — the comparison would be vacuous"
    return out


def _tracked():
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.strip(), r.stderr
    return r.stdout.split()


def test_allowlist_matches_real_audit_records():
    hits = [f for f in _tracked() if any(fnmatch.fnmatch(f, g) for g in _audit_globs())]
    assert any(f.endswith("review_provenance.md") for f in hits), hits


def test_no_oracle_corpus_file_is_an_audit_record():
    audit, oracle = _audit_globs(), _oracle_repo_globs()
    both = [f for f in _tracked()
            if any(fnmatch.fnmatch(f, g) for g in audit)
            and any(fnmatch.fnmatch(f, o) for o in oracle)]
    assert both == [], f"allowlist overlaps the oracle corpus: {both}"


def test_allowlist_never_reaches_an_agent_or_code_input():
    # Paths a box CONSUMES: agent instructions, hooks, settings, skills, code.
    for g in _audit_globs():
        for consumed in ("CLAUDE.md", ".claude/rules/x.md", ".claude/foundations/x.md",
                         ".claude/research/x.md", ".claude/skills/x/SKILL.md",
                         ".claude/hooks/x.py", ".claude/settings.json", "docs/x.md",
                         "src/x.py", "scripts/x.sh", "templates/x.yaml"):
            assert not fnmatch.fnmatch(consumed, g), (g, consumed)
