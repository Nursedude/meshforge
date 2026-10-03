"""TX power truth — saved (prefs) vs applied (journal) vs pending (open transaction).

Born 2026-10-03: a tx_power change made from the meshtasticd WEB CLIENT sat in
an uncommitted settings transaction. `--get` read it back from RAM, it was
never applied to the radio (no `Set radio` line) and never saved, and the next
restart silently reverted it. Nothing in the app could show that.

Journal fixtures are VERBATIM `journalctl -o short-unix` lines captured that
day (a USB-radio box's CLI commit 08:53, a HAT box's web-client transaction
07:15–07:16) — only the hostname, ident and channel name are replaced with
placeholders (MF014). Firmware semantics asserted here were read at the pinned
source (RadioInterface.cpp:832-877, AdminModule.cpp:331-338/1366-1372,
portduino main.cpp) by two contextless reviewers and re-checked by the author.
"""
import os
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "launcher_tui"))
sys.path.insert(0, os.path.dirname(__file__))

from utils import radio_txpower_truth as rt  # noqa: E402
from utils.observation import Failed, Seen, Unobservable  # noqa: E402


# ── protobuf wire helpers (independent of the module under test) ──────────

def _varint(n: int) -> bytes:
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field_varint(num: int, val: int) -> bytes:
    return _varint(num << 3 | 0) + _varint(val)


def _field_bytes(num: int, payload: bytes) -> bytes:
    return _varint(num << 3 | 2) + _varint(len(payload)) + payload


def _local_config(lora: bytes, before: bytes = b"", after: bytes = b"") -> bytes:
    return before + _field_bytes(6, lora) + after


# ── decoder ────────────────────────────────────────────────────────────────

class TestDecode:
    def test_tx_power_read_from_lora_field_10(self):
        lora = _field_varint(1, 1) + _field_varint(7, 1) + _field_varint(10, 17) + _field_varint(11, 20)
        assert rt.decode_lora_tx_power(_local_config(lora)) == Seen(17)

    def test_other_messages_and_fixed_fields_are_skipped(self):
        device = _field_bytes(1, _field_varint(1, 2))
        lora = (_varint(6 << 3 | 5) + struct.pack("<f", 0.0) + _varint(99 << 3 | 1)
                + struct.pack("<d", 1.5) + _field_varint(10, 22))
        blob = _local_config(lora, before=device, after=_field_varint(8, 24))
        assert rt.decode_lora_tx_power(blob) == Seen(22)

    def test_absent_tx_power_is_zero_the_firmware_default(self):
        lora = _field_varint(1, 1) + _field_varint(7, 1)
        assert rt.decode_lora_tx_power(_local_config(lora)) == Seen(0)

    def test_negative_int32_is_a_ten_byte_varint(self):
        assert rt.decode_lora_tx_power(_local_config(_field_varint(10, -3))) == Seen(-3)

    def test_int32_is_truncated_to_32_bits(self):
        assert rt.decode_lora_tx_power(_local_config(_field_varint(10, (1 << 32) + 20))) == Seen(20)

    def test_repeated_lora_chunks_merge(self):
        # protobuf merges repeated embedded messages: a later chunk without
        # tx_power must NOT reset it to 0.
        blob = _field_bytes(6, _field_varint(10, 30)) + _field_bytes(6, _field_varint(11, 20))
        assert rt.decode_lora_tx_power(blob) == Seen(30)

    def test_no_lora_message_is_unobservable_not_zero(self):
        assert isinstance(rt.decode_lora_tx_power(_field_bytes(1, _field_varint(1, 2))), Unobservable)

    def test_lora_field_not_a_message_is_failed(self):
        assert isinstance(rt.decode_lora_tx_power(_field_varint(6, 5)), Failed)

    def test_truncated_file_is_failed_not_zero(self):
        assert isinstance(rt.decode_lora_tx_power(_local_config(_field_varint(10, 17))[:-1]), Failed)

    def test_empty_file_is_failed(self):
        assert isinstance(rt.decode_lora_tx_power(b""), Failed)

    def test_nul_filled_file_is_failed(self):
        # power-loss NUL corpse class: field number 0 is illegal protobuf.
        assert isinstance(rt.decode_lora_tx_power(b"\x00" * 64), Failed)

    def test_nul_block_inside_lora_is_failed_not_default(self):
        assert isinstance(rt.decode_lora_tx_power(_field_bytes(6, b"\x00" * 16)), Failed)

    def test_agrees_with_the_official_protobufs(self):
        """Witness I did not write: encode with meshtastic's own LocalConfig.
        Skips (and says so) where the library is absent; in CI it must run."""
        try:
            from meshtastic.protobuf import localonly_pb2 as localonly
        except ImportError:
            if os.environ.get("CI"):
                pytest.fail("meshtastic protobufs missing in CI — the only external witness for the field numbers")
            pytest.skip("meshtastic library not installed — official-protobuf witness not run")
        for power in (0, 1, 17, 22, 30, -5):
            c = localonly.LocalConfig()
            c.lora.use_preset = True
            c.lora.region = 1
            c.lora.tx_power = power
            c.lora.channel_num = 20
            c.lora.frequency_offset = 0.25
            c.device.role = 2
            c.network.wifi_ssid = "x"
            c.version = 24
            assert rt.decode_lora_tx_power(c.SerializeToString()) == Seen(power), power


# ── journal (verbatim lines) ──────────────────────────────────────────────

CLI_COMMIT = [
    "1791053625.772963 box meshtasticd-patched[1]: INFO  | 18:53:45 541424 [Router] Save changes to disk",
    "1791053625.772963 box meshtasticd-patched[1]: INFO  | 18:53:45 541424 [Router] Set radio: region=US, name=Regional, config=0, ch=19, power=17",
    "1791053625.835942 box meshtasticd-patched[1]: INFO  | 18:53:45 541424 [Router] Save /prefs/config.proto",
    "1791053632.860695 box meshtasticd-patched[1]: INFO  | 18:53:52 541431 Rebooting",
    "1791053632.916835 box meshtasticd-patched[1]: Portduino is starting, VFS root at /root/.portduino/default",
    "1791053633.066515 box meshtasticd-patched[1]: INFO  | 18:53:53 0 Set radio: region=US, name=Regional, config=0, ch=19, power=17",
    "1791053633.066623 box meshtasticd-patched[1]: INFO  | 18:53:53 0 Final Tx power: 17 dBm",
]
BOOT_30 = [
    "1791010092.000000 box meshtasticd[2]: Portduino is starting, VFS root at /root/.portduino/default",
    "1791010092.100000 box meshtasticd[2]: INFO  | 08:28:12 0 Set radio: region=US, name=ShortTurbo, config=8, ch=7, power=30",
    "1791010092.100100 box meshtasticd[2]: INFO  | 08:28:12 0 Final Tx power: 22 dBm",
]
WEB_OPEN = BOOT_30 + [
    "1791047740.551951 box meshtasticd[2]: INFO  | 17:15:39 31648 [Router] Begin transaction for editing settings",
    "1791047817.582321 box meshtasticd[2]: INFO  | 17:16:56 31725 [Router] Delay save of changes to disk until the open transaction is committed",
]
REBOOT = ["1791052000.000000 box meshtasticd[2]: INFO  | 18:47:16 0 Rebooting"]
COMMIT_EDIT = ["1791052100.000000 box meshtasticd[2]: INFO  | 18:48:00 0 [Router] Commit transaction for edited settings"]


class TestJournal:
    def test_cli_commit(self):
        j = rt.parse_journal(CLI_COMMIT)
        assert (j.applied_power, j.chip_power, j.pending_since, j.lost_at) == (17, 17, None, None)
        assert j.vfs_root == "/root/.portduino/default"

    def test_web_transaction_is_pending_from_its_begin_and_not_applied(self):
        j = rt.parse_journal(WEB_OPEN)
        assert j.applied_power == 30 and j.chip_power == 22            # still on the air
        assert j.pending_since == pytest.approx(1791047740.551951)    # Begin, not the Delay

    def test_delay_without_begin_still_marks_pending(self):
        j = rt.parse_journal(BOOT_30 + WEB_OPEN[-1:])
        assert j.pending_since == pytest.approx(1791047817.582321)

    def test_commit_edit_closes_the_transaction(self):
        j = rt.parse_journal(WEB_OPEN + COMMIT_EDIT)
        assert j.pending_since is None and j.lost_at is None

    def test_reboot_with_open_transaction_is_lost(self):
        j = rt.parse_journal(WEB_OPEN + REBOOT)
        assert j.pending_since is None and j.lost_at == pytest.approx(1791052000.0)

    def test_lost_clears_once_a_newer_change_is_applied(self):
        j = rt.parse_journal(WEB_OPEN + REBOOT + CLI_COMMIT)
        assert j.lost_at is None and j.applied_power == 17

    def test_chip_power_belongs_to_the_last_set_radio(self):
        j = rt.parse_journal(CLI_COMMIT[:3])
        assert j.applied_power == 17 and j.chip_power is None


# ── verdict ────────────────────────────────────────────────────────────────

def _j(lines):
    return Seen(rt.parse_journal(lines))


class TestVerdict:
    def test_ok(self):
        assert rt.verdict(Seen(17), _j(CLI_COMMIT)).status == "OK"

    def test_saved_below_applied_is_drift(self):
        v = rt.verdict(Seen(10), _j(CLI_COMMIT))
        assert v.status == "DRIFT" and any("LOWERS" in line for line in v.lines)

    def test_saved_above_applied_is_clamped_not_drift(self):
        # RadioInterface.cpp: power > region limit (unlicensed) → limit, and
        # the clamped value is what `Set radio` logs. A restart re-clamps.
        v = rt.verdict(Seen(35), _j(BOOT_30))
        assert v.status == "CLAMPED" and not any("DRIFT" in line for line in v.lines)

    def test_pending_beats_everything_but_service(self):
        v = rt.verdict(Seen(30), _j(WEB_OPEN))
        assert v.status == "PENDING" and any("NOT on the air" in line for line in v.lines)

    def test_lost(self):
        assert rt.verdict(Seen(30), _j(WEB_OPEN + REBOOT)).status == "LOST"

    def test_saved_zero_is_default(self):
        assert rt.verdict(Seen(0), _j(CLI_COMMIT)).status == "DEFAULT"

    def test_failed_prefs_is_its_own_status(self):
        v = rt.verdict(Failed("prefs file is truncated"), _j(CLI_COMMIT))
        assert v.status == "FAILED" and any("FAILED" in line for line in v.lines)

    def test_unreadable_prefs_is_unobservable_never_ok(self):
        assert rt.verdict(Unobservable("not readable as this user"), _j(CLI_COMMIT)).status == "UNOBSERVABLE"

    def test_no_set_radio_line_is_unobservable(self):
        assert rt.verdict(Seen(17), _j(REBOOT)).status == "UNOBSERVABLE"

    def test_journal_unreadable_is_unobservable(self):
        assert rt.verdict(Seen(17), Unobservable("journalctl timed out")).status == "UNOBSERVABLE"

    def test_stopped_service_never_reads_ok(self):
        # ActiveEnterTimestamp survives a stop, so the journal window still
        # holds the dead run's `Set radio` — the service gate must win.
        v = rt.verdict(Seen(17), _j(CLI_COMMIT), Unobservable("meshtasticd is not_running — nothing is being applied"))
        assert v.status == "UNOBSERVABLE" and any("applies nothing" in line for line in v.lines)

    def test_stopped_service_beats_pending(self):
        assert rt.verdict(Seen(30), _j(WEB_OPEN), Unobservable("meshtasticd is failed")).status == "UNOBSERVABLE"

    def test_absent_service_is_absent_not_unhealthy(self):
        assert rt.verdict(Unobservable("x"), Unobservable("y"), Seen("absent")).status == "ABSENT"

    def test_chip_power_is_labelled_not_compared(self):
        v = rt.verdict(Seen(30), _j(BOOT_30))
        assert v.status == "OK" and any("chip drive" in line for line in v.lines)


# ── prefs path ────────────────────────────────────────────────────────────

class TestPrefsPath:
    ES_FSDIR = ("{ path=/usr/local/sbin/meshtasticd-patched ; argv[]=/usr/local/sbin/meshtasticd-patched "
                "-c /etc/meshtasticd/config.yaml --fsdir=/var/lib/meshtasticd/.portduino/default ; }")

    def test_fsdir(self):
        assert rt.prefs_path_from(self.ES_FSDIR, "") == \
            Seen(Path("/var/lib/meshtasticd/.portduino/default/prefs/config.proto"))

    def test_short_d_form(self):
        assert rt.prefs_path_from("{ argv[]=/usr/bin/meshtasticd -c x -d /srv/mtd ; }", "") == \
            Seen(Path("/srv/mtd/prefs/config.proto"))

    def test_relative_fsdir_is_unobservable(self):
        assert isinstance(rt.prefs_path_from("{ argv[]=x --fsdir=state ; }", ""), Unobservable)

    def test_home_env_beats_user(self):
        assert rt.prefs_path_from("{ argv[]=x ; }", "meshtasticd", "/opt/mt") == \
            Seen(Path("/opt/mt/.portduino/default/prefs/config.proto"))

    def test_user_name(self, monkeypatch):
        import pwd
        monkeypatch.setattr(pwd, "getpwnam", lambda u: SimpleNamespace(pw_dir="/var/lib/meshtasticd"))
        assert rt.prefs_path_from("{ argv[]=x ; }", "meshtasticd") == \
            Seen(Path("/var/lib/meshtasticd/.portduino/default/prefs/config.proto"))

    def test_numeric_user(self, monkeypatch):
        import pwd
        monkeypatch.setattr(pwd, "getpwuid", lambda n: SimpleNamespace(pw_dir=f"/home/u{n}"))
        assert rt.prefs_path_from("{ argv[]=x ; }", "1000") == \
            Seen(Path("/home/u1000/.portduino/default/prefs/config.proto"))

    def test_unresolvable_user_is_unobservable_not_root(self, monkeypatch):
        import pwd

        def nope(u):
            raise KeyError(u)
        monkeypatch.setattr(pwd, "getpwnam", nope)
        assert isinstance(rt.prefs_path_from("{ argv[]=x ; }", "ghost"), Unobservable)

    def test_root_when_no_user(self):
        assert rt.prefs_path_from("{ argv[]=x ; }", "") == Seen(Path("/root/.portduino/default/prefs/config.proto"))

    def test_logged_root_and_unit_disagree_is_unobservable(self, monkeypatch):
        monkeypatch.setattr(rt, "_unit_prefs_path", lambda: Seen(Path("/root/.portduino/default/prefs/config.proto")))
        assert isinstance(rt.resolve_prefs("/var/lib/meshtasticd/.portduino/default"), Unobservable)

    def test_logged_root_wins_when_unit_unreadable(self, monkeypatch):
        monkeypatch.setattr(rt, "_unit_prefs_path", lambda: Unobservable("no ExecStart"))
        assert rt.resolve_prefs("/srv/x") == Seen(Path("/srv/x/prefs/config.proto"))


# ── live readers against faked system calls ───────────────────────────────

def _fake_run(responses):
    def run(argv, **kw):
        for prefix, (rc, out, err) in responses.items():
            if tuple(argv[:len(prefix)]) == prefix:
                return subprocess.CompletedProcess(argv, rc, out, err)
        raise AssertionError(f"unexpected call {argv}")
    return run


class TestLiveReaders:
    def test_journal_rc1_no_stderr_is_no_matches(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _fake_run({("journalctl",): (1, "", "")}))
        assert rt._journal_since("Sun 2026-09-27 02:30:01 HST") == Seen(rt.parse_journal([]))

    def test_journal_rc1_with_stderr_is_unobservable(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _fake_run({("journalctl",): (1, "", "Failed to parse timestamp: x")}))
        obs = rt._journal_since("x")
        assert isinstance(obs, Unobservable) and "Failed to parse" in obs.why

    def test_journal_lines_parsed(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _fake_run({("journalctl",): (0, "\n".join(CLI_COMMIT) + "\n", "")}))
        match rt._journal_since("x"):
            case Seen(value=f):
                assert f.applied_power == 17 and f.vfs_root == "/root/.portduino/default"
            case other:
                pytest.fail(f"expected Seen, got {other}")

    def test_read_prefs_permission_denied_says_sudo(self, monkeypatch, tmp_path):
        p = tmp_path / "config.proto"
        p.write_bytes(_local_config(_field_varint(10, 17)))

        def deny(self):
            raise PermissionError()
        monkeypatch.setattr(Path, "read_bytes", deny)
        obs = rt._read_prefs(p)
        assert isinstance(obs, Unobservable) and "sudo" in obs.why

    def test_read_prefs_real_file(self, tmp_path):
        p = tmp_path / "config.proto"
        p.write_bytes(_local_config(_field_varint(10, 17)))
        assert rt._read_prefs(p) == Seen(17)

    def test_read_service_maps_not_installed_to_absent(self, monkeypatch):
        import utils.service_check as sc
        monkeypatch.setattr(sc, "check_service", lambda name: SimpleNamespace(state=sc.ServiceState.NOT_INSTALLED))
        assert rt.read_service() == Seen("absent")

    def test_read_service_not_running_is_unobservable(self, monkeypatch):
        import utils.service_check as sc
        monkeypatch.setattr(sc, "check_service", lambda name: SimpleNamespace(state=sc.ServiceState.NOT_RUNNING))
        assert isinstance(rt.read_service(), Unobservable)


# ── TUI handler: blindness renders as blindness ───────────────────────────

class TestHandler:
    def _handler(self):
        from handler_test_utils import make_handler_context
        from handlers.meshtasticd_txpower import MeshtasticdTxPowerHandler
        h = MeshtasticdTxPowerHandler()
        h.set_context(make_handler_context())
        h.ctx.safe_call = lambda title, fn, *a, **k: fn(*a, **k)
        return h

    def test_menu_item_lives_in_the_meshtasticd_section(self):
        from handlers.meshtasticd_txpower import MeshtasticdTxPowerHandler
        assert MeshtasticdTxPowerHandler.menu_section == "meshtasticd"
        assert [t for t, _, _ in MeshtasticdTxPowerHandler().menu_items()] == ["txpower_truth"]

    def test_unobservable_never_renders_ok(self, monkeypatch):
        h = self._handler()
        monkeypatch.setattr(rt, "read_all",
                            lambda: rt.verdict(Unobservable("not readable — run the TUI with sudo"), _j(CLI_COMMIT)))
        h.execute("txpower_truth")
        text = h.ctx.dialog.last_msgbox_text
        assert text.startswith("Status: UNOBSERVABLE") and "NOT a healthy reading" in text and "sudo" in text

    def test_pending_advises_commit_edit_not_set(self, monkeypatch):
        h = self._handler()
        monkeypatch.setattr(rt, "read_all", lambda: rt.verdict(Seen(30), _j(WEB_OPEN)))
        h.execute("txpower_truth")
        text = h.ctx.dialog.last_msgbox_text
        assert text.startswith("Status: PENDING")
        assert "--commit-edit" in text and "will NOT fix it" in text

    def test_never_writes(self):
        """Read-only, checked on the AST. Every subprocess call is one of the
        two read commands; no write method; no radio session."""
        import ast
        import inspect
        from handlers import meshtasticd_txpower
        allowed = {("systemctl", "show"), ("journalctl", "-u")}
        for mod in (meshtasticd_txpower, rt):
            tree = ast.parse(inspect.getsource(mod))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    assert node.attr not in ("write_bytes", "write_text", "TCPInterface", "unlink", "Popen"), node.attr
                if isinstance(node, ast.Name):
                    assert node.id not in ("TCPInterface", "open"), node.id
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "run"):
                    argv = node.args[0]
                    assert isinstance(argv, ast.List), "subprocess argv must be a literal list"
                    head = tuple(e.value for e in argv.elts[:2] if isinstance(e, ast.Constant))
                    assert head in allowed, head
