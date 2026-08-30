"""Pins for scripts/claw_copy_fleet_channel.py — the radio->claw key bridge.

The two properties worth pinning are SECURITY properties, and neither is
visible to a test that can only call main():

  1. The PSK is never an argv element. argv is world-readable through /proc,
     which is precisely the exposure mesh_psk_safe's own setpsk caveat warns
     about; this tool exists so a key can move without that.
  2. persist is always stated EXPLICITLY. claw_set_fleet_channel.py defaults
     --persist ON while this tool defaults OFF, so an omitted flag would let
     one tool silently flip the other's security posture — the key would land
     in a claw's flash because two defaults disagreed, not because anyone
     decided (honest_failure_modes #5).

Written 2026-08-30 after the bridge was driven live; before this the only
evidence was a one-shot drill, which proves it worked once and pins nothing.
"""
import base64
import importlib.util

import pytest

_spec = importlib.util.spec_from_file_location(
    "claw_copy_fleet_channel",
    "/opt/meshforge/scripts/claw_copy_fleet_channel.py")
ccfc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ccfc)

# GENERATED, never a literal. A 32-byte base64 string committed to the repo is
# key-SHAPED, and the psk_leak_guard hook refuses commands containing one — for
# good reason. Deriving it keeps the shape (which is what the argv assertions
# need) without putting a key-looking constant in the tree.
FAKE_KEY = base64.b64encode(b"not-a-real-key--32-bytes-long!!!").decode()


class TestKeyNeverInArgv:
    def test_psk_is_absent_from_the_ssh_argv(self):
        """The whole point of the bridge: the key goes to stdin, never argv."""
        cmd = ccfc.build_ssh_cmd("moc2", "dudeclaw-01", "meshforge",
                                 "localhost:4222", persist=True)
        joined = " ".join(cmd)
        assert FAKE_KEY not in joined
        assert "--psk" not in joined or "--psk-stdin" in joined
        # No argv element should even look like a 32-byte base64 key.
        for part in cmd:
            assert not ccfc._mps.B64_32.match(part), f"key-shaped argv: {part!r}"

    def test_stdin_transport_is_requested(self):
        """--psk-stdin must be passed, or the remote would prompt and hang."""
        cmd = ccfc.build_ssh_cmd("moc2", "d", "n", "s", persist=False)
        assert "--psk-stdin" in cmd


class TestPersistIsAlwaysExplicit:
    """The remote defaults --persist ON, this tool defaults OFF. Neither
    default may be allowed to win by omission."""

    def test_persist_true_passes_persist(self):
        assert "--persist" in ccfc.build_ssh_cmd("h", "d", "n", "s", persist=True)

    def test_persist_false_passes_no_persist_not_silence(self):
        cmd = ccfc.build_ssh_cmd("h", "d", "n", "s", persist=False)
        assert "--no-persist" in cmd, (
            "omitting the flag would inherit the REMOTE's --persist=True "
            "default and write the fleet key to flash unasked")
        assert "--persist" not in cmd

    @pytest.mark.parametrize("persist", [True, False])
    def test_exactly_one_persist_flag_either_way(self, persist):
        cmd = ccfc.build_ssh_cmd("h", "d", "n", "s", persist=persist)
        assert sum(1 for c in cmd if c in ("--persist", "--no-persist")) == 1


class TestParseChannelHash:
    def test_parses_the_firmware_reply_verbatim(self):
        """Shape copied from a REAL claw reply captured 2026-08-30, not
        invented: the firmware emits "Channel set: '<name>' (hash 0x%02x)"."""
        text = ("{'ok': True, 'result': \"Channel set: 'meshforge' "
                "(hash 0xa2) (persisted)\"}")
        assert ccfc.parse_channel_hash(text) == "0xa2"

    def test_missing_hash_is_none_not_a_match(self):
        """A reply carrying no hash is UNKNOWN. Returning something
        comparable here would let --expect-hash pass on no evidence."""
        assert ccfc.parse_channel_hash("Channel set: 'meshforge'") is None
        assert ccfc.parse_channel_hash("") is None
        assert ccfc.parse_channel_hash(None) is None

    def test_case_is_normalised_so_expect_hash_compares(self):
        assert ccfc.parse_channel_hash("hash 0xA2") == "0xa2"


class TestReusesMeshPskSafe:
    def test_key_reader_is_imported_not_reimplemented(self):
        """Two implementations of 'read this channel's key' would drift, and
        the copy here is the one nobody audits. The bridge must borrow
        mesh_psk_safe's, so a fix there reaches this tool for free."""
        assert hasattr(ccfc._mps, "_channel_psk")
        assert hasattr(ccfc._mps, "B64_32")
        assert ccfc._mps.CH_OK == "ok"
