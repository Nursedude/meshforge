"""Guards for the 2026-07-29 review-pass fixes (`review/post-0728-window`).

10 findings, all confirmed against the code before fixing. Each test below pins
the defect, not just the fix — i.e. it fails on the ORIGINAL code. Where a fix
lives in shell, the test drives the real script with stubs on PATH rather than
paraphrasing its logic (the harness discipline `fleet_offline_check.sh` already
established).

The one finding whose PRESCRIPTION was rejected is `bug_003`: the review said to
delete the guarded `mini_dudeai` import, but that guard is the deliberate
convention in `watchdog_probes_mini.py` (four instances) and deleting it would
break the module on a box without the mini package. The diagnosis was right
though — "it's test-pinned" was imprecise — so the fallback is now observable and
pinned here instead.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
ROOT = os.path.join(os.path.dirname(__file__), "..")


# ── bug_001 (normal) — the witness that killed the tick ──────────────────────

class TestWatchVerdictWitnessDoesNotRaise:
    """`logger` was undefined, so the except handler raised NameError from
    inside itself — escaping _watch_verdicts, killing build_tick, and taking the
    capture cron down. The handler written to keep the tick alive was the one
    thing that could kill it."""

    def test_gate_failure_returns_none_and_does_not_raise(self, capsys):
        from mini_dudeai import claw_telemetry as ct

        def _boom(*a, **k):
            raise ValueError("boom")

        # Force the try-block to raise the way an import/signature drift would.
        import mini_dudeai.claw_rf_watch as crw
        calls = []
        orig = crw.classify_watch

        def _boom_counted(*a, **k):
            calls.append(1)
            raise ValueError("boom")

        crw.classify_watch = _boom_counted
        try:
            out = ct._watch_verdicts({"watched": {"!aa": {"age_s": 1}}},
                                     {"uptime_s": 99999})
        finally:
            crw.classify_watch = orig
        assert calls, "the gate was never invoked — test is vacuous"
        assert out is None                      # degrades, does not explode
        assert "watch verdict gate failed" in capsys.readouterr().out

    # The REAL firmware wire format. An earlier version of this test passed
    # "watched:" as the lora result, which parse_lora_stats rejects (it requires
    # mesh_heard_age_s) — so lora came back None, _watch_verdicts returned early,
    # and the patched classify_watch was NEVER CALLED. The test passed with the
    # bug in place: it proved nothing. Caught by red-drilling it against the
    # unfixed code, which is the only reason it was caught at all.
    LORA_RESULT = ("mesh_heard_age_s: 12 heard 497 pkts crc_err 2 runts 0 "
                   "last from=!e213a228 rssi=0 snr=6.8 "
                   "watch=!32962f10:100/33@0,!ddfb8065:never")

    def test_the_lora_fixture_actually_reaches_the_gate(self):
        """Guard the guard: if this payload ever stops producing a watch list,
        the test below silently goes vacuous again."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        parsed = parse_lora_stats(self.LORA_RESULT)
        assert parsed is not None and parsed["watched"], (
            "fixture no longer yields a watch list — the exception-path test "
            "below would stop exercising the exception path")

    def test_build_tick_survives_a_failing_gate(self):
        """The consequence that actually mattered: the whole tick was lost."""
        from mini_dudeai import claw_telemetry as ct
        import mini_dudeai.claw_rf_watch as crw

        calls = []

        def _boom(*a, **k):
            calls.append(1)
            raise ValueError("boom")

        env = {"ok": True, "result": "uptime_s: 99999"}
        orig = crw.classify_watch
        crw.classify_watch = _boom
        try:
            tick = ct.build_tick(
                1.0, "h", "dudeclaw-01", env, None,
                lora_reply={"ok": True, "result": self.LORA_RESULT})
        finally:
            crw.classify_watch = orig
        assert calls, "the gate was never invoked — test is vacuous"
        assert isinstance(tick, dict) and tick["device"] == "dudeclaw-01"
        assert tick["watch_verdicts"] is None
        assert tick["lora"]["heard_age_s"] == 12      # the rest of the tick survived

    def test_module_binds_its_witness(self):
        """Regression pin: the witness must resolve. `logger` never existed."""
        from mini_dudeai import claw_telemetry as ct
        assert callable(getattr(ct, "log_warning", None))
        # Match CODE, not prose: the fix's own comment names the old call.
        code = [ln for ln in open(os.path.join(
            ROOT, "src/mini_dudeai/claw_telemetry.py")).read().splitlines()
            if not ln.lstrip().startswith("#")]
        src = "\n".join(code)
        assert "logger." not in src, (
            "claw_telemetry has no `logger` binding — a logger.* call in an "
            "except handler raises NameError out of the handler and kills the tick")


# ── bug_010 (normal) — a page that never landed advanced the state ───────────

class TestOfflineCheckDeliveryGatesState:
    """ntfy_push returns 0 only on CONFIRMED delivery. Advancing alerted=1
    regardless meant a first page lost to a brief ntfy.sh dip was never retried:
    the next tick skips the first-alert branch and STILL-DOWN waits out the full
    interval — a REAL outage silent for 1 h (2 h quiet tier)."""

    SCRIPT = os.path.join(ROOT, "scripts", "fleet_offline_check.sh")

    def _stubs(self, tmp_path, *, delivered):
        """A PATH dir where ssh always fails and curl either confirms or not.

        ntfy_push needs rc=0 AND a 2xx AND a JSON "id" to call it delivered, so
        the undelivered stub returns a 500 with no id — the real shape of an
        ntfy.sh dip, which is the case that was never retried.
        """
        b = tmp_path / "bin"
        b.mkdir()
        (b / "ssh").write_text("#!/bin/sh\nexit 255\n")
        # curl is invoked with -w '\n%{http_code}', so the real shape is
        # "<body>\n<code>". ntfy_push takes tail -n1 as the code and sed-extracts
        # "id" from the body.
        reply = '{"id":"stubid1"}\n200\n' if delivered else 'upstream error\n500\n'
        stub = "#!/bin/sh\ncat <<'CURLEOF'\n" + reply + "CURLEOF\n"
        (b / "curl").write_text(stub)
        for f in ("ssh", "curl"):
            os.chmod(b / f, 0o755)
        return b

    def _run_tick(self, tmp_path, binp, statef):
        env = dict(os.environ)
        env.update({
            "PATH": "%s:%s" % (binp, os.environ.get("PATH", "")),
            "MESHFORGE_OFFLINE_PATH": "%s:/usr/local/bin:/usr/bin:/bin" % binp,
            "MESHFORGE_OFFLINE_BOXES": str(tmp_path / "boxes.json"),
            "MESHFORGE_OFFLINE_LOG": str(tmp_path / "alerts.log"),
            "MESHFORGE_OFFLINE_WITNESS": str(tmp_path / "witness.log"),
            "MESHFORGE_OFFLINE_STATE": str(statef),
            "MESHFORGE_OFFLINE_HB": str(tmp_path / "hb.log"),
            "MESHFORGE_NTFY_TOPIC": "unit-test-topic",
            "ALERT_THRESHOLD": "1",
        })
        return subprocess.run(["bash", self.SCRIPT], env=env,
                              capture_output=True, text=True, timeout=120)

    def _state_row(self, statef):
        for line in open(statef).read().splitlines():
            if line.startswith("alpha\t"):
                return line.split("\t")
        return None

    @pytest.mark.skipif(not os.path.exists(SCRIPT), reason="script absent")
    def test_failed_push_keeps_alerted_zero_so_the_next_tick_retries(self, tmp_path):
        (tmp_path / "boxes.json").write_text(json.dumps(
            {"ssh_user": "tester",
             "boxes": [{"name": "alpha", "host": "alpha.invalid"}]}))
        binp = self._stubs(tmp_path, delivered=False)   # ntfy.sh dip
        statef = tmp_path / "state.tsv"
        statef.write_text("")
        self._run_tick(tmp_path, binp, statef)
        row = self._state_row(statef)
        assert row is not None, "no state row written"
        assert row[2] == "0", (
            "alerted must stay 0 when the page was NOT delivered, so the next "
            "*/5 tick re-enters the first-alert branch instead of waiting out "
            "the hourly re-page interval; got row=%r" % (row,))

    @pytest.mark.skipif(not os.path.exists(SCRIPT), reason="script absent")
    def test_failed_push_is_witnessed_in_the_log(self, tmp_path):
        (tmp_path / "boxes.json").write_text(json.dumps(
            {"ssh_user": "tester",
             "boxes": [{"name": "alpha", "host": "alpha.invalid"}]}))
        binp = self._stubs(tmp_path, delivered=False)
        statef = tmp_path / "state.tsv"
        statef.write_text("")
        self._run_tick(tmp_path, binp, statef)
        log = (tmp_path / "alerts.log").read_text()
        assert "PUSH-FAILED" in log

    def test_state_advance_is_inside_the_delivery_branch(self):
        """Source-level pin: `set_state ... 1 "$NOW" "$NOW" 1` must not sit
        unconditionally after an `ntfy_push || echo` compound."""
        src = open(self.SCRIPT).read()
        assert 'if ntfy_push "Fleet box DOWN' in src, (
            "the first-alert push must gate the state write (if ntfy_push ...; "
            "then set_state ...), not run beside it")
        assert 'if ntfy_push "Fleet box STILL DOWN' in src


# ── bug_005 — the enum listed one class twice ───────────────────────────────

class TestSignalClassesHasNoDuplicates:
    def test_no_duplicate_entries(self):
        from utils.watchdog_probe_core import SIGNAL_CLASSES
        seen, dupes = set(), []
        for c in SIGNAL_CLASSES:
            (dupes.append(c) if c in seen else None)
            seen.add(c)
        assert dupes == [], (
            "duplicate SIGNAL_CLASSES entries double-count in "
            "fleet_truth.merge_coverage (green/red/dark incremented per visit "
            "while the classes dict dedupes): %r" % dupes)

    def test_coverage_total_matches_unique_class_count(self):
        """The observable consequence — /fleet reported total=59 over 58 keys."""
        from utils.watchdog_probe_core import SIGNAL_CLASSES
        from utils import fleet_truth as ft
        wd = {"signals": [], "coverage": {"classes": {}}}
        cov = ft.merge_coverage(wd, list(SIGNAL_CLASSES))
        assert cov["total"] == len(set(SIGNAL_CLASSES))
        assert cov["green"] + cov["red"] + cov["dark"] == len(cov["classes"])


# ── bug_013 — a debouncing tick claimed a positive observation ──────────────

class TestUplinkDebounceIsIndeterminate:
    def test_debounce_tick_notes_indeterminate_not_clean(self, tmp_path):
        from utils.watchdog_probe_core import (collect_dispositions,
                                               reset_dispositions)
        from utils.watchdog_probes import probe_claw_uplink_node_moved
        mac = "02:00:5e:00:11:22"
        cfg = tmp_path / "nodes.json"
        cfg.write_text(json.dumps([{"name": "HAP", "mac": mac,
                                    "expected_ip": "192.0.2.250"}]))
        arp = tmp_path / "arp"
        arp.write_text("IP address HW type Flags HW address\n"
                       "192.0.2.99 0x1 0x2 %s * eth0\n" % mac)
        reset_dispositions()
        sig = probe_claw_uplink_node_moved(
            config_path=str(cfg), arp_path=str(arp),
            state_path=str(tmp_path / "streak.json"), now=1.0,
            pinhole_path=str(tmp_path / "no-nft"), debounce_ticks=2)
        assert sig is None
        d = collect_dispositions()["claw_uplink_node_moved"]
        assert d["disp"] == "indeterminate", (
            "drift WAS observed this tick, so `clean` both contradicts the "
            "reason string and — being in watchdog_tracker's "
            "_CLEAR_PERMITTING_DISPOSITIONS — lets a debouncing tick clear a "
            "held signal; got %r" % (d,))


# ── bug_003 — the fallback is observable now, not silent ────────────────────

class TestVerdictConstantsAreNotOnTheFallback:
    def test_real_import_succeeded(self):
        """Wherever mini is importable (every fleet box, and CI) the probe must
        be using claw_rf_watch's OWN constants. If this trips, the probe is
        comparing against values the gate no longer emits and every verdict
        folds to `blind` — a permanently quiet gate."""
        from utils import watchdog_probes_claw_watch as w
        assert w._VERDICT_CONSTANTS_FROM_FALLBACK is False

    def test_constants_match_the_owning_module(self):
        from utils import watchdog_probes_claw_watch as w
        from mini_dudeai import claw_rf_watch as owner
        assert (w.HEARD, w.SILENT, w.UNOBSERVABLE) == (
            owner.HEARD, owner.SILENT, owner.UNOBSERVABLE)


# ── bug_002 — a latent NameError in a firewall rewriter ────────────────────

class TestGenClawPinholeAnnotationsResolve:
    def test_plan_type_hints_evaluate(self):
        """`Optional` was used but never imported; PEP 563 hid it until
        something evaluated the annotation."""
        import importlib.util as u
        import typing
        p = os.path.join(ROOT, "scripts", "gen_claw_pinhole.py")
        spec = u.spec_from_file_location("gcp_under_test", p)
        m = u.module_from_spec(spec)
        spec.loader.exec_module(m)
        typing.get_type_hints(m.plan)      # raised NameError before the fix


# ── bug_006 / bug_012 — shell-side fixes ───────────────────────────────────

class TestSelfhealRefusesLoudOnLockContention:
    SCRIPT = os.path.join(ROOT, "scripts", "claw_pinhole_selfheal.sh")

    def test_no_permissive_lock_fallthrough(self):
        src = open(self.SCRIPT).read()
        assert "flock -w 30 9 2>/dev/null || true" not in src
        assert "flock -n 9" in src, (
            "the lock must refuse loud: a timeout/unwritable-lock fallthrough "
            "rewrites the firewall unserialized, which is the case the block "
            "exists to exclude (honest_failure_modes #8)")

    def test_contended_lock_skips_without_touching_the_firewall(self, tmp_path):
        """Hold the lock, run the script, assert it declined."""
        lock = tmp_path / "pinhole.lock"
        lock.write_text("")
        verdicts = tmp_path / "verdicts"
        verdicts.mkdir()
        holder = subprocess.Popen(
            ["flock", str(lock), "sleep", "8"])
        try:
            env = dict(os.environ)
            env["CLAW_PINHOLE_SELFHEAL_LOCK"] = str(lock)
            # Route the verdict into the sandbox. Without these, `say CONCERN`
            # goes through the REAL cron_verdict.sh into ~/cron_verdicts.log on
            # every box that runs the suite — and on boxes with no claw-pinhole
            # cron nothing ever supersedes it, so the fleet reads a manufactured
            # "two firewall writers" CONCERN forever (live 2026-07-31; the
            # tests-must-pin-ambient-state class, writer form).
            env["CRON_VERDICT_LOG"] = str(verdicts / "cron_verdicts.log")
            env["CRON_VERDICT_OUT_DIR"] = str(verdicts)
            r = subprocess.run(["bash", self.SCRIPT], env=env,
                               capture_output=True, text=True, timeout=60)
            assert r.returncode == 0, r.stderr[-400:]
            logged = (verdicts / "cron_verdicts.log").read_text()
            assert ("claw_pinhole_drift CONCERN" in logged
                    and "skipping this cycle" in logged), (
                "expected the contended-lock skip verdict in the sandboxed "
                "ledger — rc==0 alone also matches the uncontended check "
                "path: %r" % logged)
        finally:
            holder.kill()
            holder.wait()


class TestFleetHostsShellStripsCarriageReturn:
    def test_crlf_parity_with_python(self, tmp_path):
        """A CRLF file made every SHELL consumer ssh to "moc1\\r" while every
        PYTHON consumer read "moc1" — the deploy tools and the gate that
        verifies them disagreeing about who is in the fleet."""
        from utils.fleet_hosts import parse_fleet_hosts_text
        f = tmp_path / "fleet_hosts"
        f.write_bytes(b"moc1\r\nmoc2\r\n# c\r\n")
        lib = os.path.join(ROOT, "scripts", "lib", "fleet_hosts.sh")
        # text=False DELIBERATELY. With text=True, Python's universal-newline
        # translation rewrites \r and \r\n to \n on the way in — so the harness
        # ERASED the exact byte it exists to detect, and this test passed against
        # the unfixed lib. A checker must not destroy the evidence it checks
        # (the 2026-07-25 self-confirming-checker lesson, in test form). Caught
        # only by red-drilling this test against the original code.
        out = subprocess.run(
            ["bash", "-c",
             '. "%s"; MESHFORGE_FLEET_HOSTS="%s" fleet_hosts_resolve '
             '/opt/meshforge >/dev/null; printf "%%s" "$FLEET_HOSTS_LIST"'
             % (lib, f)],
            capture_output=True, text=False, timeout=60)
        raw = out.stdout.decode("utf-8", "replace")
        assert "\r" not in raw, (
            "shell resolver leaked a carriage return into the host list (%r) — "
            "every ssh would target 'moc1\\r' while Python consumers read "
            "'moc1'" % raw)
        shell = [h for h in raw.split("\n") if h]
        py = parse_fleet_hosts_text(f.read_text())
        assert shell == py, (
            "shell %r != python %r — one SSOT, two answers" % (shell, py))


# ── bug_004 — a test suite whose verdict depended on the box it ran on ─────

class TestUplinkTestsPinTheirPinhole:
    """`TestClawUplinkNodeMoved._run` left `pinhole_path` at its default, i.e.
    the REAL /etc/nftables.conf of whatever machine ran the suite. Green in CI
    (no such file) and red on moc2 — the one box whose 4222 allowlist is the
    entire reason the pinhole leg exists. feedback_tests_must_pin_ambient_state."""

    def test_helper_passes_an_explicit_pinhole_path(self):
        src = open(os.path.join(ROOT, "tests",
                                "test_probe_claw_uplink_node.py")).read()
        run_def = src.split("def _run(", 1)[1].split("\n    def ", 1)[0]
        assert "pinhole_path" in run_def, (
            "_run must pin pinhole_path — unpinned it reads the host's real "
            "/etc/nftables.conf and the tests' verdict depends on the box")


# ── bug_011 — additional movers were dropped on the floor ──────────────────

class TestEveryMovedUplinkTravels:
    def test_second_mover_is_not_lost(self, tmp_path):
        from utils.watchdog_probes import probe_claw_uplink_node_moved
        m1, m2 = "02:00:5e:00:11:22", "02:00:5e:00:33:44"
        cfg = tmp_path / "nodes.json"
        cfg.write_text(json.dumps([
            {"name": "HAP-A", "mac": m1, "expected_ip": "192.0.2.250"},
            {"name": "HAP-B", "mac": m2, "expected_ip": "192.0.2.251"},
        ]))
        arp = tmp_path / "arp"
        arp.write_text("IP address HW type Flags HW address\n"
                       "192.0.2.98 0x1 0x2 %s * eth0\n"
                       "192.0.2.99 0x1 0x2 %s * eth0\n" % (m1, m2))
        sp = str(tmp_path / "streak.json")
        nft = str(tmp_path / "no-nft")
        sig = None
        for _ in range(3):                      # ride out the debounce
            sig = probe_claw_uplink_node_moved(
                config_path=str(cfg), arp_path=str(arp), state_path=sp,
                now=1.0, pinhole_path=nft)
        assert sig is not None
        assert sig.extra["moved_count"] == 2
        names = {o["name"] for o in sig.extra["also_moved"]}
        assert names, "the second mover vanished — no subject, no tracker state"
        assert "HAP-B" in names or "HAP-A" in names
        assert "ALSO MOVED" in sig.detail
        assert sig.subject == "2 uplink nodes"
