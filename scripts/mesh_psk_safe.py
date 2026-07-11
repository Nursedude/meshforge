#!/usr/bin/env python3
"""mesh_psk_safe — the ONLY sanctioned way to read/verify/set Meshtastic channel
PSKs from an agent session, so a channel key can never reach the transcript.

Born 2026-07-10 after PSKs leaked to a session transcript TWICE (old keys, then
the freshly-rotated keys) via the identical vector: printing `meshtastic --info`
output where a grep swept the `"psk"` field along with it. A memory rule did not
stop it — this tool + the `psk_leak_guard` PreToolUse hook make the leak-prone
commands impossible instead (calibrated_claims: harness enforces, not disposition).

Every path here emits at most a sha256 PREFIX of a key, never the key bytes, never
a channel URL (URLs encode the PSK). Keys are only ever SET from a file, never a
literal argument.

Subcommands:
  info    <host[:port]>                 full --info with every psk + channel URL redacted
  keyhash <host[:port]> <channelName>   print sha256:16 of that channel's psk (nothing else)
  verify  <host[:port]> <channelName> <keyfile>
                                        MATCH / DIFFER vs the base64 key in <keyfile> (hashes only)
  setpsk  <host[:port]> <index> <keyfile|default|none|random>
                                        set slot <index>'s psk from a FILE (or a named default);
                                        never echoes the key
"""
import hashlib
import re
import subprocess
import sys

PSK_RE = re.compile(r'("psk"\s*:\s*")([A-Za-z0-9+/=]{8,})(")')
URL_RE = re.compile(r'https://meshtastic\.org/e/#[A-Za-z0-9_\-]+')
B64_32 = re.compile(r'^[A-Za-z0-9+/]{43}=$')  # 32-byte base64


def _hash8(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:8]


def _redact(text: str) -> str:
    text = PSK_RE.sub(lambda m: f'{m.group(1)}<redacted:sha256:{_hash8(m.group(2))}>{m.group(3)}', text)
    text = URL_RE.sub(lambda m: f'<channel-url:sha256:{_hash8(m.group(0))}>', text)
    return text


def _run_info(host: str) -> str:
    r = subprocess.run(
        ["meshtastic", "--host", host, "--info"],
        capture_output=True, text=True, timeout=90,
    )
    # redact BOTH streams before anything can be printed
    return _redact(r.stdout) + (_redact(r.stderr) if r.returncode else "")


def _channel_psk(host: str, name: str):
    """Return the raw psk string for a channel by name — used INTERNALLY only,
    never printed. Callers must reduce to a hash before output."""
    raw = subprocess.run(
        ["meshtastic", "--host", host, "--info"],
        capture_output=True, text=True, timeout=90,
    ).stdout
    for line in raw.splitlines():
        if f'"name": "{name}"' in line:
            m = PSK_RE.search(line)
            if m:
                return m.group(2)
    return None


def _load_key(keyfile: str) -> str:
    with open(keyfile) as f:
        return f.read().strip()


def cmd_info(host):
    print(_run_info(host))
    return 0


def cmd_keyhash(host, name):
    psk = _channel_psk(host, name)
    if psk is None:
        print(f"{name}: channel not found", file=sys.stderr)
        return 3
    print(f"{name}: sha256:{hashlib.sha256(psk.encode()).hexdigest()[:16]}")
    return 0


def cmd_verify(host, name, keyfile):
    psk = _channel_psk(host, name)
    if psk is None:
        print(f"{name}: channel not found", file=sys.stderr)
        return 3
    want = _load_key(keyfile)
    same = psk == want
    print(f"{name}: {'MATCH' if same else 'DIFFER'} "
          f"(box=sha256:{hashlib.sha256(psk.encode()).hexdigest()[:16]} "
          f"file=sha256:{hashlib.sha256(want.encode()).hexdigest()[:16]})")
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
        witness = f"sha256:{hashlib.sha256(key.encode()).hexdigest()[:16]}"
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
