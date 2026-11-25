"""honest_status's CI leg judges ONLY the CI workflow's run (2026-11-25).

The leg took the first `gh run list` entry for the head SHA from ANY workflow;
a manual stranger-drill run (observe mode) on the same SHA was reported as
"CI success". It now matches `workflowName` against CI_WORKFLOW_NAME — which
must equal the `name:` of .github/workflows/ci.yml, or the leg would never
find CI again (every head UNKNOWN). Two consumers of one constant: pinned.
"""
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent


def test_the_ci_leg_constant_is_the_ci_workflows_name():
    sh = (REPO / "scripts" / "honest_status.sh").read_text(encoding="utf-8")
    doc = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    const = re.search(r'^CI_WORKFLOW_NAME="([^"]+)"$', sh, re.M)
    assert const, "CI_WORKFLOW_NAME constant missing from honest_status.sh"
    # parsed, not regexed: a trailing `# comment` or quotes never break it (r1)
    assert const.group(1) == doc["name"]
