"""Contacts pane for the MeshCore TUI handler (roadmap 1b, ported 1e).

Ported from MeshAnchor's ``_meshcore_contacts.py`` (MA ``cebb7afa``) as a
mixin on ``MeshCoreHandler``; the host class supplies ``self.ctx`` and
``self.STATUS_API_BASE``. Here the table comes from the gateway's own
:9090 listener (``GET /api/json/meshcore/contacts``, utils.meshcore_status_api)
because MeshForge's live gateway shape has no :8081 chat API.

This pane renders the RADIO's own contact table joined against OUR receipt
clock from the node tracker. It is read-only — declared-vs-actual, never a
write to the radio (the TUI stays a surface).

⚠️ Why "last heard" comes from the tracker and NOT from the contact row
(measured on a live RAK table, 2026-09-21 — both field names mislead):

  last_advert  the SENDER's own clock. Observed range across 68 contacts:
               2022-12-31 .. 2084-12-21. Fine as "what they claimed",
               useless as a magnitude we can act on.

  lastmod      NOT a receipt clock. meshcore_py passes it back as
               ``get_contacts(lastmod=...)`` — an If-Modified-Since sync
               cursor — so the firmware stamps it when the RECORD changed,
               not when the node was heard. A pane sorting on it reports a
               node heard seconds ago as eight days silent.

The tracker's ``last_seen`` is stamped by us, on our clock, when an
advertisement actually arrives. That is the only honest answer to "when
did we last hear this node", and when we have no answer this pane says so
rather than borrowing a number from a field that means something else.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime

from backend import clear_screen


class MeshCoreContactsMixin:
    """Mixin: the radio's contact table, joined to our own receipt clock."""

    CONTACTS_TIMEOUT = 5

    # ── data ────────────────────────────────────────────────────────────

    def _contacts_fetch(self):
        """``(rows, error)`` from the gateway's contacts endpoint.

        On failure returns ``(None, reason)`` — never ``([], None)``, which
        would render "the radio knows nobody" for what is actually "we
        could not ask" (honest_failure_modes #1: empty is not error). A
        payload the gateway marks ``observed: false`` is the same claim
        and carries its own reason.
        """
        try:
            req = urllib.request.Request(
                f"{self.STATUS_API_BASE}/api/json/meshcore/contacts",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.CONTACTS_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Answered, not unreachable: a 404 is an older gateway build.
            return None, self._older_gateway_reason(e)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
            return None, str(e)
        if not isinstance(payload, dict):
            return None, "response was not a JSON object"
        if payload.get("observed") is False:
            return None, payload.get("reason") or "gateway could not read the table"
        rows = payload.get("contacts")
        if not isinstance(rows, list):
            return None, "response carried no 'contacts' list"
        return rows, None

    def _contacts_receipts(self):
        """``{pubkey_hex_lower: last_seen|None}`` from the node tracker.

        A key present with ``None`` means "we have heard this node but the
        receipt carries no stamp" — deliberately distinct from a key that
        is absent, which means "never heard since the tracker started".
        Those are different claims and the pane prints them differently.
        """
        try:
            # First-party: direct import, never safe_import. Imported lazily
            # so the pane still renders on a box with no gateway extras.
            from gateway.node_tracker import get_node_tracker
            nodes = get_node_tracker().get_meshcore_nodes()
        except Exception:
            # None (unobservable), never {} — the caller renders these as
            # UNKNOWN rather than as "never heard".
            return None
        out = {}
        for n in nodes or []:
            key = getattr(n, "meshcore_pubkey", "") or ""
            if not key:
                nid = getattr(n, "id", "") or ""
                key = nid.split(":", 1)[1] if ":" in nid else ""
            if key:
                out[key.lower()] = getattr(n, "last_seen", None)
        return out

    @staticmethod
    def _contacts_lookup(receipts, prefix):
        """Match a contact prefix against tracker keys of differing length.

        The tracker keys off whatever the advert carried (``pubkey_prefix``
        or a full ``public_key``); the contact row carries exactly 12 hex.
        Either may be a prefix of the other, so compare both directions.
        """
        if not receipts or not prefix:
            return False, None
        p = prefix.lower()
        for key, seen in receipts.items():
            if key.startswith(p) or p.startswith(key):
                return True, seen
        return False, None

    # ── rendering ───────────────────────────────────────────────────────

    @staticmethod
    def _contacts_age(seen):
        if not seen:
            return "?"
        delta = (datetime.now() - seen).total_seconds()
        if delta < 0:
            return "clock skew"
        if delta < 60:
            return f"{int(delta)}s ago"
        if delta < 3600:
            return f"{int(delta / 60)}m ago"
        if delta < 86400:
            return f"{delta / 3600:.1f}h ago"
        return f"{delta / 86400:.1f}d ago"

    @staticmethod
    def _contacts_path(out_path_len):
        """-1 is "no path, reached by flood" — never "-1 hops"."""
        if out_path_len is None:
            return "?"
        if out_path_len < 0:
            return "flood"
        if out_path_len == 0:
            return "direct"
        return f"{out_path_len} hop" + ("s" if out_path_len > 1 else "")

    @staticmethod
    def _contacts_position(lat, lon):
        """Exactly (0,0) is the firmware's "no position" sentinel.

        29 of 68 live rows carried it. Rendering it as a coordinate puts a
        Hawaii mesh node in the Gulf of Guinea (the 2026-09-02 sentinel
        class: an absent value leaking into the measurement domain).
        """
        if lat is None or lon is None:
            return "-"
        try:
            if float(lat) == 0.0 and float(lon) == 0.0:
                return "-"
            return f"{float(lat):.4f},{float(lon):.4f}"
        except (TypeError, ValueError):
            return "-"

    @staticmethod
    def _contacts_declared():
        """Contacts the operator declared as OURS (names or pubkey prefixes)."""
        try:
            from gateway.config import GatewayConfig
            cfg = GatewayConfig.load()
            declared = getattr(cfg.meshcore, "our_contacts", None)
        except Exception:
            return []
        return [str(t).strip() for t in (declared or []) if str(t).strip()]

    @staticmethod
    def _contacts_is_ours(declared, row):
        """The declared token naming this contact, else None.

        A token is either an adv_name or a pubkey prefix.
        """
        name = (row.get("name") or "").strip().lower()
        prefix = (row.get("prefix") or "").strip().lower()
        for token in declared:
            t = token.lower()
            if t == name:
                return token
            if prefix and (prefix.startswith(t) or t.startswith(prefix)):
                return token
        return None

    def _meshcore_contacts(self):
        """Render the radio's contact table with an honest last-heard column."""
        clear_screen()
        print("=== MeshCore Contacts ===\n")

        rows, err = self._contacts_fetch()
        if rows is None:
            print(f"  Could not read the contact table: {err}")
            print(f"  Endpoint: {self.STATUS_API_BASE}/api/json/meshcore/contacts")
            print("  The gateway serves it - start it in-app: Service Control -> meshforge-gateway -> Start")
            print("  (MeshCore must be enabled in gateway.json and the radio connected.)")
            self.ctx.wait_for_enter()
            return

        receipts = self._contacts_receipts()
        declared = self._contacts_declared()

        heard = 0
        decorated = []
        for row in rows:
            prefix = (row.get("prefix") or "").strip()
            known, seen = self._contacts_lookup(receipts, prefix)
            if known:
                heard += 1
            decorated.append((row, known, seen,
                              self._contacts_is_ours(declared, row)))

        # Most recently heard first; never-heard last, then by name.
        decorated.sort(key=lambda d: (
            d[2] is None, -(d[2].timestamp() if d[2] else 0),
            (d[0].get("name") or "").lower()))

        print(f"  {len(rows)} contact(s) in the radio's table; "
              f"{heard} heard by us since the tracker started.")
        if receipts is None:
            print("  ! node tracker unreadable - 'last heard' is UNKNOWN for "
                  "every row below, not 'never'.")
        print()
        print(f"  {'':1} {'NAME':<22} {'ROLE':<10} {'PREFIX':<13} "
              f"{'LAST HEARD':<14} {'PATH':<8} POSITION")
        print("  " + "-" * 88)

        for row, known, seen, ours in decorated:
            if receipts is None:
                last = "unknown"
            elif seen is not None:
                last = self._contacts_age(seen)
            elif known:
                # In the tracker but with no stamp. "Never" would be a lie;
                # a blank would read as one too.
                last = "heard, no ts"
            else:
                last = "not since boot"
            print(f"  {'*' if ours else ' '} "
                  f"{(row.get('name') or '(unnamed)')[:22]:<22} "
                  f"{(row.get('role') or '?')[:10]:<10} "
                  f"{(row.get('prefix') or '?')[:13]:<13} "
                  f"{last:<14} "
                  f"{self._contacts_path(row.get('out_path_len')):<8} "
                  f"{self._contacts_position(row.get('adv_lat'), row.get('adv_lon'))}")

        self._contacts_render_declared(rows, declared)
        self._contacts_render_legend()
        self.ctx.wait_for_enter()

    def _contacts_render_declared(self, rows, declared):
        """Declared-as-ours entries the radio's table does NOT carry.

        An absence is a ROW, never silence — a node we declared and cannot
        see is exactly the thing an operator opens this pane to learn.
        """
        if not declared:
            print("\n  No contacts declared as ours. Set meshcore.our_contacts")
            print("  (adv_names or pubkey prefixes) in gateway.json to mark them")
            print("  with * and to see declared nodes ABSENT from the radio's table.")
            return
        missing = [t for t in declared
                   if not any(self._contacts_is_ours([t], r) for r in rows)]
        if not missing:
            print(f"\n  All {len(declared)} declared contact(s) present in the table.")
            return
        print(f"\n  ABSENT - declared as ours, not in the radio's table "
              f"({len(missing)} of {len(declared)}):")
        for token in missing:
            print(f"    ! {token:<22} never advertised within this radio's hearing,")
            print(f"      {'':22} or advertising under a different name")

    @staticmethod
    def _contacts_render_legend():
        print("\n  last heard = OUR clock, stamped when an advert arrived.")
        print("  The row's own last_advert is the SENDER's clock (live range")
        print("  2022..2084) and lastmod is a record-modified sync cursor -")
        print("  neither is a receipt time, so neither is shown as one.")
        print("  path 'flood' = no stored route; position '-' = none advertised.")
