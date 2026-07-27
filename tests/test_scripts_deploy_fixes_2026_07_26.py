"""Pins for the 2026-07-26 verified-review deploy/scripts fixes (D1..D15).

Each class names the review finding it pins. The shell/unit-file pins are
static (the repo has no busybox ash to execute), the Python ones behavioral.
"""
import importlib.util
import logging
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


def _load_script(name, modname):
    spec = importlib.util.spec_from_file_location(
        modname, REPO_ROOT / "scripts" / name)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


# ── D1: systemd expands ${...}/% before bash sees the ExecStart line ─────────

class TestMapServiceCorsEscape:
    UNIT = REPO_ROOT / "scripts" / "meshforge-map.service"

    def test_unit_carries_the_double_escaped_form(self):
        """Drill-proven 2026-07-26: a bare ${ip%.*} is expanded by systemd
        ("Invalid environment variable name evaluates to an empty string:
        ip%.*") and the derived CORS /24 prefix collapses to "http://.".
        The unit must carry $${ip%%.*} so bash receives ${ip%.*}."""
        text = self.UNIT.read_text()
        assert "$${ip%%.*}" in text
        # The broken single-escaped form must not reappear anywhere.
        assert "http://${ip%.*}" not in text


# ── D5/D14a: rtun watchdog honest DEAD counting + bounded log ────────────────

class TestRtunWatchdogTemplate:
    SH = REPO_ROOT / "templates" / "openwrt" / "rtun_watchdog.sh"

    def test_dead_log_reports_observed_and_unobserved_counts(self):
        """The old line claimed "after 3 tries" while logging only the LAST
        attempt's output — which could be '<empty/timeout>' from an attempt
        that observed nothing."""
        text = self.SH.read_text()
        assert "dead=$DEAD_OBSERVED observed" in text
        assert "$UNOBSERVED unobserved of $PROBE_TRIES" in text
        assert "loop='$LAST_OBSERVED'" in text
        assert "${LOOP:-<empty/timeout>}" not in text, (
            "the DEAD leg must log the last OBSERVED reply, never an empty "
            "timeout artifact")

    def test_dead_cycle_counts_only_from_an_observed_failure(self):
        text = self.SH.read_text()
        assert '[ "$DEAD_OBSERVED" -eq 0 ]' in text
        assert 'LAST_OBSERVED="$LOOP"' in text

    def test_log_is_capped(self):
        text = self.SH.read_text()
        assert "tail -n 200" in text and "65536" in text

    def test_heartbeat_semantics_untouched_with_limitation_noted(self):
        text = self.SH.read_text()
        assert 'echo "$TS TUN_OK" > "$HEARTBEAT"' in text
        assert "KNOWN LIMITATION" in text


# ── D7: history sidecar lock is bounded and degrades with a witness ──────────

class TestHistoryLockDegradesWithWitness:
    def _fresh_history(self):
        # Re-import so the once-per-process warn flag starts clean per test.
        for k in list(sys.modules):
            if k in ("mini_dudeai.history",):
                del sys.modules[k]
        from mini_dudeai import history
        history._lock_degraded_warned = False
        return history

    def test_busy_lock_degrades_to_unlocked_append_with_one_warning(
            self, tmp_path, monkeypatch, caplog):
        history = self._fresh_history()
        import fcntl

        def busy(fd, op):
            raise BlockingIOError("Resource temporarily unavailable")

        monkeypatch.setattr(fcntl, "flock", busy)
        monkeypatch.setattr(history.time, "sleep", lambda s: None)
        path = str(tmp_path / "hist.jsonl")
        with caplog.at_level(logging.WARNING, logger="mini_dudeai.history"):
            assert history.append_jsonl(path, [{"a": 1}], 10_000) is None
            assert history.append_jsonl(path, [{"a": 2}], 10_000) is None
        assert Path(path).read_text().count("\n") == 2, "append must survive"
        warns = [r for r in caplog.records if "UNLOCKED append" in r.message]
        assert len(warns) == 1, "exactly one witness per process, naming the path"
        assert path in warns[0].message

    def test_healthy_lock_path_appends_without_warning(self, tmp_path, caplog):
        history = self._fresh_history()
        path = str(tmp_path / "hist.jsonl")
        with caplog.at_level(logging.WARNING, logger="mini_dudeai.history"):
            assert history.append_jsonl(path, [{"a": 1}], 10_000) is None
        assert not [r for r in caplog.records if "UNLOCKED" in r.message]


# ── D8: claw instance glob must not treat backups as instances ───────────────

class TestClawInstanceGlobFiltersArtifacts:
    def test_backup_and_dotted_suffixes_are_skipped(self, tmp_path):
        psr = _load_script("promote_seed_rules.py", "promote_seed_rules_d8")
        from mini_dudeai.presets.standalone import CLAW_RULES_BASENAME
        root, _dot, ext = CLAW_RULES_BASENAME.rpartition(".")
        good = tmp_path / f"{root}.claw02.{ext}"
        good.write_text("{}")
        for suffix in ("old", "bak", "BACKUP", "tmp", "orig", "save", "a.b"):
            (tmp_path / f"{root}.{suffix}.{ext}").write_text("{}")
        assert psr.claw_instance_live_paths(str(tmp_path)) == [str(good)]

    def test_instance_loop_catches_oserror_not_only_promoteerror(self):
        """Source pin: a raw write-path OSError on instance 2 must be reported
        as that instance's error, not crash after the primary was rewritten."""
        src = (REPO_ROOT / "scripts" / "promote_seed_rules.py").read_text()
        assert "except (PromoteError, OSError) as exc:" in src

    def test_accepted_instance_list_is_printed_before_applying(self):
        src = (REPO_ROOT / "scripts" / "promote_seed_rules.py").read_text()
        assert '"instance targets: "' in src


# ── D10: selftest never FAILs from a guessed module name ─────────────────────

class TestSelftestUnmappedPackageWarns:
    def test_guessed_name_failure_downgrades_to_unmapped_warning(self, capsys):
        st = _load_script("selftest.py", "meshforge_selftest_d10")
        got = st.check_import("definitely_not_a_real_module_xyz",
                              "ghost-pkg", guessed=True)
        assert got is None, "guessed-name failure must be WARN (None), not FAIL"
        out = capsys.readouterr().out
        assert "unmapped package ghost-pkg" in out
        assert "_PKG_TO_MODULE" in out

    def test_mapped_name_failure_still_fails(self, capsys):
        st = _load_script("selftest.py", "meshforge_selftest_d10b")
        got = st.check_import("definitely_not_a_real_module_xyz", "ghost-pkg")
        assert got is False
        assert "not installed" in capsys.readouterr().out


# ── D11: the ack button must need a deliberate tap (POST, not GET) ───────────

class TestNtfyAckActionIsPostOnly:
    SH = REPO_ROOT / "scripts" / "fleet_ntfy_ack.sh"

    def test_action_is_http_post(self):
        text = self.SH.read_text()
        m = re.search(r'-H "Actions: ([^"]+)"', text)
        assert m, "Actions header missing"
        action = m.group(1)
        assert action.startswith("http, "), action
        assert "method=POST" in action and "body=ack" in action

    def test_no_get_publish_url_action_remains(self):
        """A view action opening /publish?message=ack is ack-able by any
        link-prefetcher — a forged receipt."""
        assert "publish?message=ack" not in self.SH.read_text()


# ── D12: failed retrieval is not observed absence ────────────────────────────

class TestRetrievalFailureIsNotAbsence:
    def test_rank_exception_is_marked_and_rendered_as_failure(self, monkeypatch):
        from mini_dudeai import cadence_fallback as cf
        from mini_dudeai import offline_oracle

        monkeypatch.setattr(
            offline_oracle, "load_corpus_indexed",
            lambda roots=None: ([{"path": "x", "heading": "h", "text": "t"}],
                                [], None))

        def boom(*a, **kw):
            raise ValueError("index corrupt")

        monkeypatch.setattr(offline_oracle, "rank", boom)
        proposed = [{"key": "k1", "summary": "s", "kind": "fact",
                     "status": "proposed"}]
        ctx, note = cf.retrieve_context(proposed)
        assert note is None
        assert ctx["k1"][0]["retrieval_failed"] == "ValueError"

        prompt = cf.build_user_prompt(proposed, context=ctx)
        assert "retrieval FAILED for k1: ValueError" in prompt
        assert "no matching record found" not in prompt

    def test_genuine_absence_still_reads_as_absence(self):
        from mini_dudeai import cadence_fallback as cf
        proposed = [{"key": "k1", "summary": "s"}]
        prompt = cf.build_user_prompt(proposed, context={})
        assert "no matching record found" in prompt
        assert "retrieval FAILED" not in prompt


# ── D13: TRIAGE_FRESH_S and the launcher's 86400 are one constant ────────────

class TestTriageFreshnessConstantsAgree:
    def test_shell_and_python_freshness_windows_agree(self):
        """hfm #5: two consumers of one artifact must share ONE constant.
        cadence_fallback.TRIAGE_FRESH_S gates the witness in Python;
        mini_cadence_launch.sh drops a stale witness with a hardcoded bound."""
        from mini_dudeai import cadence_fallback as cf
        sh = (REPO_ROOT / "scripts" / "mini_cadence_launch.sh").read_text()
        m = re.search(r'"\$_tw_age"\s+-gt\s+(\d+)', sh)
        assert m, "stale-witness age gate not found in mini_cadence_launch.sh"
        assert int(m.group(1)) == int(cf.TRIAGE_FRESH_S), (
            f"mini_cadence_launch.sh gates at {m.group(1)}s but "
            f"cadence_fallback.TRIAGE_FRESH_S is {cf.TRIAGE_FRESH_S}s")


# ── D15: promote_seed_rules invocations are time-bounded ─────────────────────

class TestPromoteSeedCallsAreBounded:
    def test_fleet_sync_and_update_wrap_promote_with_timeout(self):
        for rel in ("scripts/fleet_sync.sh", "scripts/update.sh"):
            text = (REPO_ROOT / rel).read_text()
            for m in re.finditer(r"^.*promote_seed_rules\.py --apply.*$",
                                 text, re.M):
                line = m.group(0)
                if line.lstrip().startswith("#"):
                    continue
                assert "timeout 60" in line, f"{rel}: unbounded call: {line}"


# ── D6: lint operator-pattern loader (sudo home + tab-separated format) ──────

def _load_lint():
    spec = importlib.util.spec_from_file_location(
        "lint_d6", REPO_ROOT / "scripts" / "lint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestLintOperatorPatternsSudoAndFormat:
    def test_tab_separator_preserves_regex_with_space_runs(self, tmp_path):
        """A regex containing 2+ consecutive spaces was truncated at the space
        run; when a tab separator is present it must win."""
        lint = _load_lint()
        p = tmp_path / "pats.txt"
        p.write_text("secret  host\toperator hostname with spaces\n")
        pats = lint.load_local_operator_patterns(str(p))
        assert len(pats) == 1
        assert pats[0][1] == "operator hostname with spaces"
        assert pats[0][0].search("ssh secret  host")
        assert not pats[0][0].search("secret host")

    def test_two_space_separator_still_works_without_tab(self, tmp_path):
        lint = _load_lint()
        p = tmp_path / "pats.txt"
        p.write_text("secrethost   operator hostname\n")
        pats = lint.load_local_operator_patterns(str(p))
        assert len(pats) == 1 and pats[0][1] == "operator hostname"

    def test_sudo_user_home_is_used_for_the_default_path(self, tmp_path,
                                                         monkeypatch):
        """MF001 in miniature: under sudo, expanduser('~') is /root and the
        deny-patterns file silently vanished — leak gate OFF, no witness."""
        lint = _load_lint()
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "lint_operator_patterns.txt").write_text("leakpat\tfound it\n")

        class _Pw:
            pw_dir = str(tmp_path)

        import types
        fake_pwd = types.SimpleNamespace(getpwnam=lambda u: _Pw())
        monkeypatch.setitem(sys.modules, "pwd", fake_pwd)
        monkeypatch.setenv("SUDO_USER", "operatoruser")
        monkeypatch.setattr(lint.os, "geteuid", lambda: 0, raising=False)
        monkeypatch.delenv(lint.MF014_LOCAL_PATTERNS_ENV, raising=False)
        pats = lint.load_local_operator_patterns()
        assert len(pats) == 1 and pats[0][1] == "found it"
