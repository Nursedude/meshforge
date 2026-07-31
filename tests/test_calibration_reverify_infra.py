"""calibration_reverify.sh must never mint a verdict from a run that proved nothing.

Adversarial review 2026-07-31, finding 1: the classifier call at the heart of
the reverify job had no guard on its OWN failure to run. A missing (rc 127) or
exec-bit-stripped (rc 126) ``pytest_verdict.sh`` fell through the tri-state to
``code_exit=1`` — and ``rederive_and_persist`` then flipped every open claim on
HEAD to **broke** from a run that verified nothing. ``mf_pytest_checked`` and
``honest_status`` both fail closed on exactly this case; the one consumer that
WRITES ledger verdicts was the one without the guard.

These drive the real script end-to-end against a sandbox repo (git + trivial
suite + stub lint) with the ledger confined via ``CALIBRATION_LEDGER_PATH`` —
no test touches this box's real ledger or runs the real suite.
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "calibration_reverify.sh"
REAL_CLASSIFIER = REPO_ROOT / "scripts" / "pytest_verdict.sh"


def _make_sandbox_repo(tmp):
    """A minimal repo the script can fully traverse: git HEAD, a 1-test suite,
    a stub lint, and the REAL classifier (each test then degrades it)."""
    repo = Path(tmp) / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "tests" / "test_trivial.py").write_text("def test_ok():\n    assert True\n")
    (repo / "scripts" / "lint.py").write_text("print('stub lint ok')\n")
    shutil.copy(REAL_CLASSIFIER, repo / "scripts" / "pytest_verdict.sh")
    os.chmod(repo / "scripts" / "pytest_verdict.sh", 0o755)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "commit", "-qm", "seed"]):
        subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True,
                       timeout=30)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=30,
                          check=True).stdout.strip()
    return repo, head


def _seed_ledger(tmp, head):
    """One open VERIFIED claim on the sandbox HEAD — the thing at stake."""
    ledger = Path(tmp) / "ledger.jsonl"
    ledger.write_text(json.dumps({
        "kind": "claim", "id": "cafe00000001", "ts": 1.0,
        "session_id": None, "model_id": None, "source": "manual",
        "claim_class": "completion", "claim_text": "seed claim",
        "evidence": "seeded for test", "head_full": head, "status": "open",
    }) + "\n")
    return ledger


def _run(repo, ledger):
    env = dict(
        os.environ,
        MESHFORGE_REPO=str(repo),
        CALIBRATION_LEDGER_PATH=str(ledger),
        # The heredoc inserts <sandbox>/src on sys.path, which has no
        # mini_dudeai — point python at the real package explicitly.
        PYTHONPATH=str(REPO_ROOT / "src"),
    )
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=300)


def _verdicts(ledger):
    return [json.loads(line) for line in ledger.read_text().splitlines()
            if json.loads(line).get("kind") == "verdict"]


class TestClassifierInfraFailureWritesNoVerdict(unittest.TestCase):
    """rc 127/126 from the classifier is an infra failure, never a code verdict."""

    def test_missing_classifier_exits_3_and_flips_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = _make_sandbox_repo(tmp)
            ledger = _seed_ledger(tmp, head)
            (repo / "scripts" / "pytest_verdict.sh").unlink()
            r = _run(repo, ledger)
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn("did not run", r.stderr)
            self.assertEqual(_verdicts(ledger), [],
                             "a classifier that never ran minted a verdict")

    def test_non_executable_classifier_exits_3_and_flips_nothing(self):
        """The exec-bit case is distinct (rc 126, the file EXISTS) — a deploy
        chmod loss must not read differently from a missing file."""
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = _make_sandbox_repo(tmp)
            ledger = _seed_ledger(tmp, head)
            clf = repo / "scripts" / "pytest_verdict.sh"
            os.chmod(clf, stat.S_IRUSR | stat.S_IWUSR)
            r = _run(repo, ledger)
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertEqual(_verdicts(ledger), [],
                             "a classifier that could not exec minted a verdict")


class TestHealthyPathStillFlips(unittest.TestCase):
    """The guard must not break the job it guards: with the real classifier in
    place and a green sandbox suite, the open claim flips to held and the
    verdict names this instrument (not honest_status)."""

    def test_green_run_holds_the_claim_via_reverify_instrument(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = _make_sandbox_repo(tmp)
            ledger = _seed_ledger(tmp, head)
            r = _run(repo, ledger)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            vs = _verdicts(ledger)
            self.assertEqual(len(vs), 1, ledger.read_text())
            self.assertEqual(vs[0]["outcome"], "held")
            self.assertIn("calibration_reverify", vs[0]["detail"])


if __name__ == "__main__":
    unittest.main()
