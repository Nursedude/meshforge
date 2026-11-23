#!/usr/bin/env python3
"""rns_rpc_key_pin.py — pin a per-box rpc_key into an RNS config, via THE pinner.

    rns_rpc_key_pin.py /etc/reticulum/config

The shell installers (install_noc.sh, fleet_restore.sh) used to pin with
``sed -i "/^\\[reticulum\\]/a …"`` behind a grep — the same guessed grammar
that wrote a second ``[reticulum]`` into BOM / indented / commented configs
(WITNESS-TUI r13, Fable r12 #1/#2). This runs ``utils.rns_config_setup``'s
``ensure_rpc_key``: the new bytes are checked against rnsd's own parser
before anything is written. The key is never printed.

Exit: 0 a valid key was already pinned · 10 a fresh key was pinned ·
3 an rpc_key is set but not 64-hex (left as-is) · 2 refused, nothing
written (reason on stderr) · 1 could not read/write the file.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from utils.rns_config_setup import ensure_rpc_key, is_valid_key  # noqa: E402


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 1
    path = Path(argv[0])
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


if __name__ == "__main__":
    sys.exit(main())
