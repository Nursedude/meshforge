#!/usr/bin/env python3
"""Record one fleet_ntfy_push.sh publish outcome — the witness's writer.

Split out of the shell script because a read-modify-write of counters wants a
real JSON parse, and because a corrupt state file must be recoverable without
silently resetting the history it was supposed to carry.

Called under flock by fleet_ntfy_push.sh (see the WITNESS block there for why
this exists at all). Never raises into the caller: paging is advisory, and a
witness that breaks a page is worse than no witness.

argv: <state_path> <status> <http_code> <error> <title>
  status in {ok, failed, no_topic}
"""
from __future__ import annotations

import json
import os
import sys
import time

MAX_TITLE = 120


def _load(path: str):
    """(state, prior_unreadable). A corrupt file starts a fresh record but says
    so — silently resetting the counters would erase the very history the
    witness exists to keep, and read as a healthy channel."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data, False
        return {}, True
    except FileNotFoundError:
        return {}, False
    except (ValueError, OSError):
        return {}, True


def main(argv) -> int:
    if len(argv) < 2:
        return 0
    path, status = argv[0], argv[1]
    code = argv[2] if len(argv) > 2 else ""
    err = argv[3] if len(argv) > 3 else ""
    title = (argv[4] if len(argv) > 4 else "")[:MAX_TITLE]

    st, prior_unreadable = _load(path)
    now = time.time()

    st["last_attempt_ts"] = now
    st["last_status"] = status
    st["last_http_code"] = code
    st["last_error"] = err
    st["last_title"] = title
    if prior_unreadable:
        # Sticky until a human or a later healthy run clears it; the counts
        # below are known-incomplete and must not look authoritative.
        st["prior_state_unreadable"] = True

    if status == "ok":
        st["last_ok_ts"] = now
        st["sends_ok"] = int(st.get("sends_ok", 0) or 0) + 1
        st["consecutive_failures"] = 0
    elif status == "failed":
        st["last_fail_ts"] = now
        st["sends_failed"] = int(st.get("sends_failed", 0) or 0) + 1
        st["consecutive_failures"] = int(
            st.get("consecutive_failures", 0) or 0) + 1
    else:  # no_topic — nothing was attempted, so neither counter moves.
        st["sends_no_topic"] = int(st.get("sends_no_topic", 0) or 0) + 1

    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        os.replace(tmp, path)          # atomic within the same filesystem
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
