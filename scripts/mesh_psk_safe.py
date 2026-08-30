#!/usr/bin/env python3
"""mesh_psk_safe — the ONLY sanctioned way to read/verify/set Meshtastic channel
PSKs from an agent session, so a channel key can never reach the transcript.

Born 2026-07-10 after PSKs leaked to a session transcript TWICE (old keys, then
the freshly-rotated keys) via the identical vector: printing `meshtastic --info`
output where a grep swept the `"psk"` field along with it. A memory rule did not
stop it — this tool + the `psk_leak_guard` PreToolUse hook make the leak-prone
commands impossible instead (calibrated_claims: harness enforces, not disposition).

Every path here emits at most a sha256 PREFIX of a key, never the key bytes, never
a channel URL (URLs encode the PSK) — into stdout/the transcript. Keys are read
from a FILE, never typed as a literal argument to THIS tool.

CAVEAT (inherent to the meshtastic CLI): `setpsk` passes `base64:<key>` as an
argv to the `meshtastic` child, so the key is briefly visible in that child's
/proc/<pid>/cmdline (and to argv-logging like auditd/psacct) for the duration of
the call. It never enters the transcript, but do not run `setpsk` while
untrusted processes or argv accounting are active on the target box. The upstream
CLI has no single-channel file/stdin form to avoid this.

Subcommands:
  info    <host[:port]>                 full --info with every psk + channel URL redacted
  keyhash <host[:port]> <channelName>   print sha256:16 of that channel's psk (nothing else)
  verify  <host[:port]> <channelName> <keyfile>
                                        MATCH / DIFFER vs the base64 key in <keyfile> (hashes only)
  setpsk  <host[:port]> <index> <keyfile|default|none|random>
                                        set slot <index>'s psk from a FILE (or a named default);
                                        never echoes the key
  copypsk <srchost[:port]> <channelName> <dsthost[:port]> <dstindex> [--dry-run]
                                        read <channelName>'s key from the source radio and set it
                                        on the destination slot IN ONE PROCESS — no keyfile, no
                                        transcript. The destination channel must already exist
                                        with the same name (create with `meshtastic --ch-add`;
                                        its random initial key gets overwritten). After the write
                                        the destination is read BACK and hash-compared (never
                                        trust the write). --dry-run stops before the write and
                                        prints only the source key's hash. setpsk's argv-visibility
                                        caveat applies to the destination box.
"""
import hashlib
import re
import subprocess
import sys

# PSK values are base64 of 1/16/32-byte keys → 4/24/44 chars. Floor of 2 (not
# 8) so the 1-byte default/simple key "AQ==" is redacted here AND found by
# _channel_psk — an 8-char floor made a default-psk channel read "channel not
# found" and printed "AQ==" unredacted.
PSK_RE = re.compile(r'("psk"\s*:\s*")([A-Za-z0-9+/=]{2,})(")')
URL_RE = re.compile(r'https://meshtastic\.org/e/#[A-Za-z0-9_\-]+')
B64_32 = re.compile(r'^[A-Za-z0-9+/]{43}=$')  # 32-byte base64

# Tri-state channel-lookup status (honest_failure_modes point 1: a transport
# failure must NOT read as a valid domain answer of "channel absent").
CH_OK = "ok"
CH_ABSENT = "absent"
CH_ERROR = "error"


def _hash8(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:8]


def _hash16(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _redact(text: str) -> str:
    text = PSK_RE.sub(lambda m: f'{m.group(1)}<redacted:sha256:{_hash8(m.group(2))}>{m.group(3)}', text)
    text = URL_RE.sub(lambda m: f'<channel-url:sha256:{_hash8(m.group(0))}>', text)
    return text


def _raw_info(host: str) -> subprocess.CompletedProcess:
    """Single `meshtastic --info` round-trip. The ONE place the command shape
    (argv, timeout) lives — info + channel lookup both go through it so their
    transport semantics can never diverge."""
    return subprocess.run(
        ["meshtastic", "--host", host, "--info"],
        capture_output=True, text=True, timeout=90,
    )


def _run_info(host: str):
    """Return (redacted_text, returncode). rc is propagated so callers never
    report an unreachable radio as a successful dump."""
    r = _raw_info(host)
    # redact BOTH streams before anything can be printed
    text = _redact(r.stdout) + (_redact(r.stderr) if r.returncode else "")
    return text, r.returncode


def _channel_psk(host: str, name: str):
    """Return (status, psk) where status is CH_OK/CH_ABSENT/CH_ERROR. psk is
    the raw key (INTERNAL only — callers must reduce to a hash before output).
    A nonzero meshtastic exit yields CH_ERROR, distinct from CH_ABSENT, so a
    momentarily-unreachable box is never misreported as "channel not found"."""
    r = _raw_info(host)
    if r.returncode != 0:
        return CH_ERROR, None
    # NOTE: relies on meshtastic --info rendering a channel's "name" and "psk"
    # on the same line (its compact JSON-per-channel form). A future CLI format
    # change surfaces as CH_ABSENT, never as a wrong key.
    for line in r.stdout.splitlines():
        if f'"name": "{name}"' in line:
            m = PSK_RE.search(line)
            if m:
                return CH_OK, m.group(2)
    return CH_ABSENT, None


def _load_key(keyfile: str) -> str:
    with open(keyfile) as f:
        return f.read().strip()


def _report_lookup_failure(name, status):
    """Emit the right message + exit code for a non-OK channel lookup. Keeps
    CH_ERROR (transport, rc 4) distinct from CH_ABSENT (rc 3)."""
    if status == CH_ERROR:
        print(f"{name}: could not read radio (unreachable / CLI error)", file=sys.stderr)
        return 4
    print(f"{name}: channel not found", file=sys.stderr)
    return 3


def cmd_info(host):
    text, rc = _run_info(host)
    print(text)
    if rc != 0:
        print(f"info: meshtastic exited {rc} (radio unreachable / error)", file=sys.stderr)
        return 4
    return 0


def cmd_keyhash(host, name):
    status, psk = _channel_psk(host, name)
    if status != CH_OK:
        return _report_lookup_failure(name, status)
    print(f"{name}: sha256:{_hash16(psk)}")
    return 0


def cmd_verify(host, name, keyfile):
    status, psk = _channel_psk(host, name)
    if status != CH_OK:
        return _report_lookup_failure(name, status)
    want = _load_key(keyfile)
    same = psk == want
    print(f"{name}: {'MATCH' if same else 'DIFFER'} "
          f"(box=sha256:{_hash16(psk)} file=sha256:{_hash16(want)})")
    return 0 if same else 1


def cmd_setpsk(host, index, keyspec):
    named = {"default", "none", "random"}
    if keyspec in named:
        psk_arg = keyspec
        witness = keyspec
    else:
        key = _load_key(keyspec)
        if not B64_32.match(key):
            print(f"refusing: {keyspec} is not a 32-byte base64 key", file=sys.stderr)
            return 2
        psk_arg = f"base64:{key}"
        witness = f"sha256:{_hash16(key)}"
    r = subprocess.run(
        ["meshtastic", "--host", host, "--ch-index", str(index), "--ch-set", "psk", psk_arg],
        capture_output=True, text=True, timeout=90,
    )
    # NEVER print r.stdout/stderr raw — it may echo the key
    if r.returncode == 0:
        print(f"slot {index}: psk set ({witness})")
        return 0
    print(f"slot {index}: set FAILED (rc={r.returncode}); stderr redacted", file=sys.stderr)
    return 1


def cmd_copypsk(srchost, name, dsthost, index, dry_run=False):
    status, psk = _channel_psk(srchost, name)
    if status != CH_OK:
        return _report_lookup_failure(name, status)
    if not B64_32.match(psk):
        # default (AQ==) / simple keys are public knowledge — use `setpsk <host> <i> default`
        print(f"refusing: source '{name}' key is not a 32-byte base64 key "
              f"(sha256:{_hash16(psk)}); for default/simple keys use setpsk", file=sys.stderr)
        return 2
    if dry_run:
        print(f"DRY-RUN {name}: would set {dsthost} slot {index} "
              f"to src key sha256:{_hash16(psk)}")
        return 0
    r = subprocess.run(
        ["meshtastic", "--host", dsthost, "--ch-index", str(index), "--ch-set", "psk", f"base64:{psk}"],
        capture_output=True, text=True, timeout=90,
    )
    # NEVER print r.stdout/stderr raw — it may echo the key
    if r.returncode != 0:
        print(f"{dsthost} slot {index}: set FAILED (rc={r.returncode}); stderr redacted",
              file=sys.stderr)
        return 1
    # Re-derive: read the destination back and compare hashes — the write alone
    # is not evidence the key landed (wrong slot, settle race, CLI drift).
    dstatus, dpsk = _channel_psk(dsthost, name)
    if dstatus != CH_OK:
        print(f"{dsthost} slot {index}: psk set but verify read failed — state UNKNOWN; "
              f"re-check with: keyhash {dsthost} {name}", file=sys.stderr)
        return 4
    same = dpsk == psk
    print(f"{name}: copy {'VERIFIED (src==dst)' if same else 'DIFFER — dst does not match src'} "
          f"(src=sha256:{_hash16(psk)} dst=sha256:{_hash16(dpsk)})")
    return 0 if same else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, rest = argv[1], argv[2:]
    try:
        if cmd == "info" and len(rest) == 1:
            return cmd_info(*rest)
        if cmd == "keyhash" and len(rest) == 2:
            return cmd_keyhash(*rest)
        if cmd == "verify" and len(rest) == 3:
            return cmd_verify(*rest)
        if cmd == "setpsk" and len(rest) == 3:
            return cmd_setpsk(*rest)
        if cmd == "copypsk" and len(rest) in (4, 5):
            dry = "--dry-run" in rest
            args = [a for a in rest if a != "--dry-run"]
            if len(args) == 4:
                return cmd_copypsk(*args, dry_run=dry)
    except subprocess.TimeoutExpired:
        print(f"{cmd}: meshtastic timed out", file=sys.stderr)
        return 4
    except FileNotFoundError as e:
        print(f"{cmd}: {e}", file=sys.stderr)
        return 4
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
