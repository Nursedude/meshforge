#!/usr/bin/env python3
"""rns_rpc_key_pin.py — the ONE rpc_key reader/pinner for shell callers.

    rns_rpc_key_pin.py CONFIG                     pin a per-box key (writes)
    rns_rpc_key_pin.py --check CONFIG             is a key pinned? (read-only)
    rns_rpc_key_pin.py --copy-from SERVER CLIENT  pin SERVER's key into CLIENT

Every answer comes from ``utils.rns_config_reader.rnsd_rpc_key`` (the key
rnsd USES, read with rnsd's own parser) and every write passes the
``rns_config_setup`` write boundary (new bytes checked against rnsd's parser
first). Shell scripts call this instead of grep/sed (WITNESS-TUI r13 + r13.2,
Fable r12 #1/#2, r14 #1: the shell greps were wrong on 30-34 of 88 shapes and
a quoted key rnsd reads parked MeshChatX). The key is never printed.

Exit codes:
  pin:        0 a valid key already pinned · 10 a fresh key pinned ·
              3 an rpc_key is set but invalid (left as-is) · 2 refused,
              nothing written · 1 could not read/write
  --check:    0 pinned (valid) · 4 no key · 3 set but invalid · 2 rnsd
              cannot load the file · 1 could not read it
  --copy-from: 0 CLIENT already carries SERVER's key · 10 copied (a
              ``.bak-<ts>-rpckey`` backup is kept) · 4 CLIENT carries a
              DIFFERENT valid key (never overwritten) · 3 SERVER has no
              usable key · 2 refused, nothing written · 1 could not read/write
"""

import shutil
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from utils.rns_config_reader import RNSConfigError, rnsd_rpc_key  # noqa: E402
from utils.rns_config_setup import (  # noqa: E402
    ensure_rpc_key, is_valid_key, pin_rpc_key_bytes,
)


def _usage() -> int:
    print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
    return 1


def _check(path: Path) -> int:
    try:
        raw = path.read_bytes()
    except OSError as e:
        print(f"rpc_key UNKNOWN — could not read {path}: {e}", file=sys.stderr)
        return 1
    try:
        state, _value = rnsd_rpc_key(raw)
    except RNSConfigError as e:
        print(f"rpc_key UNKNOWN — rnsd cannot load {path}: {e}", file=sys.stderr)
        return 2
    if state == "valid":
        print(f"rpc_key pinned in {path}")
        return 0
    if state == "invalid":
        print(f"rpc_key in {path} is set but not a 64-hex key MeshForge can "
              f"share with clients — replace or remove it", file=sys.stderr)
        return 3
    print(f"no rpc_key under [reticulum] in {path}", file=sys.stderr)
    return 4


def _copy(server: Path, client: Path) -> int:
    try:
        s_state, s_key = rnsd_rpc_key(server.read_bytes())
        c_raw = client.read_bytes()
    except OSError as e:
        print(f"rpc_key NOT copied — {e}", file=sys.stderr)
        return 1
    except RNSConfigError as e:
        print(f"rpc_key NOT copied — rnsd cannot load {server}: {e}", file=sys.stderr)
        return 3
    if s_state != "valid":
        print(f"{server} carries no pinned rpc_key rnsd uses — nothing to copy", file=sys.stderr)
        return 3
    try:
        new_raw, c_key, _gen = pin_rpc_key_bytes(c_raw, key=s_key)
    except ValueError as e:      # RNSConfigError / RPCKeyWriteRefused
        print(f"rpc_key NOT copied — {client} left untouched: {e}", file=sys.stderr)
        return 2
    if new_raw is None:
        if c_key == s_key:
            print("already pinned (same key as rnsd)")
            return 0
        if is_valid_key(c_key):
            print(f"{client} carries a DIFFERENT rpc_key — not overwritten", file=sys.stderr)
            return 4
        print(f"{client} has an invalid rpc_key — left as-is; replace or "
              f"remove it by hand", file=sys.stderr)
        return 2
    try:
        shutil.copy2(client, f"{client}.bak-{time.strftime('%Y%m%d-%H%M%S')}-rpckey")
        client.write_bytes(new_raw)
    except OSError as e:
        print(f"rpc_key NOT copied — {e}", file=sys.stderr)
        return 1
    print(f"pinned rnsd's rpc_key into {client}")
    return 10


def _pin(path: Path) -> int:
    try:
        key, generated = ensure_rpc_key(path)
    except ValueError as e:      # RNSConfigError / RPCKeyWriteRefused
        print(f"rpc_key NOT pinned — {path} left untouched: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"rpc_key NOT pinned — {e}", file=sys.stderr)
        return 1
    if generated:
        print(f"pinned a fresh rpc_key in {path}")
        return 10
    if is_valid_key(key):
        print(f"existing rpc_key preserved in {path}")
        return 0
    print(f"{path} has an rpc_key that is not 64-hex — left as-is; "
          f"replace or remove it by hand", file=sys.stderr)
    return 3


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "--check":
        return _check(Path(argv[1]))
    if len(argv) == 3 and argv[0] == "--copy-from":
        return _copy(Path(argv[1]), Path(argv[2]))
    if len(argv) == 1 and not argv[0].startswith("-"):
        return _pin(Path(argv[0]))
    return _usage()


if __name__ == "__main__":
    sys.exit(main())
