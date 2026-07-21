#!/usr/bin/env python3
"""One-shot LXMF store-and-forward drill (plan step 2a-bis).

Throwaway identities, /tmp storage, nothing touches gateway config or
delivery_counters. Three modes:

  b-init  (on receiver box) : make B's identity, print its LXMF delivery hash
  a-send  (on sender box)   : send a PROPAGATED message to B via our node
  b-pull  (on receiver box) : bring B up, pull from the node, expect the message

Not repo-tracked on purpose: this is the manual proof that must pass BEFORE
adoption. The durable form is slice 3 (2a-ter).
"""
import os
import sys
import time

sys.path.insert(0, "/opt/meshforge/src")

import RNS  # noqa: E402
import LXMF  # noqa: E402
from utils.rns_init import open_reticulum  # noqa: E402

DRILL_DIR = "/tmp/propdrill"
PROP_NODE = "3968a2eeac25e2e7a7961f25842d3d85"
MARKER = "PROPDRILL-2026-07-21"


def _state_name(msg):
    for attr in ("DRAFT", "OUTBOUND", "SENDING", "SENT", "DELIVERED",
                 "REJECTED", "CANCELLED", "FAILED"):
        if hasattr(LXMF.LXMessage, attr) and getattr(LXMF.LXMessage, attr) == msg.state:
            return attr
    return str(msg.state)


def _identity(path):
    os.makedirs(DRILL_DIR, mode=0o700, exist_ok=True)
    if os.path.isfile(path):
        return RNS.Identity.from_file(path), False
    ident = RNS.Identity()
    ident.to_file(path)
    os.chmod(path, 0o600)
    return ident, True


def _router(role):
    store = os.path.join(DRILL_DIR, f"{role}_store")
    os.makedirs(store, mode=0o700, exist_ok=True)
    # autopeer=False: a drill client must never peer our fleet store outward.
    return LXMF.LXMRouter(storagepath=store, autopeer=False)


def b_init():
    ident, fresh = _identity(os.path.join(DRILL_DIR, "b_identity"))
    router = _router("b")
    dest = router.register_delivery_identity(ident, display_name="propdrill-B")
    # Deliberately NOT announced: B must be unknown to the network so a
    # DIRECT delivery cannot pass the drill in propagation's place.
    # B never announces, so A cannot RNS.Identity.recall() it. Hand the public
    # key over out-of-band instead — that keeps B genuinely unknown on the wire.
    pub = ident.get_public_key()
    with open(os.path.join(DRILL_DIR, "b_pub"), "wb") as fh:
        fh.write(pub)
    print(f"B_IDENTITY_FRESH={fresh}")
    print(f"B_DEST_HASH={RNS.hexrep(dest.hash, delimit=False)}")
    print(f"B_PUB_HEX={RNS.hexrep(pub, delimit=False)}")
    router.exit_handler()


def a_send(b_hash):
    ident, _ = _identity(os.path.join(DRILL_DIR, "a_identity"))
    router = _router("a")
    source = router.register_delivery_identity(ident, display_name="propdrill-A")
    router.set_outbound_propagation_node(bytes.fromhex(PROP_NODE))

    dest_ident = RNS.Identity.recall(bytes.fromhex(b_hash))
    if dest_ident is None:
        # Expected: B never announced, so we have no recalled identity.
        # Build the destination from B's public key file instead.
        pub = os.path.join(DRILL_DIR, "b_pub")
        if not os.path.isfile(pub):
            print("FAIL: no recalled identity for B and no b_pub file present")
            sys.exit(2)
        # NOT RNS.Identity.from_bytes() — that loads a PRIVATE key and will
        # silently accept 64 bytes of PUBLIC key, yielding a valid-looking but
        # completely different identity (live-caught 2026-07-21: addressed the
        # drill message to a phantom destination the node then stored forever).
        pub_bytes = open(pub, "rb").read()
        dest_ident = RNS.Identity(create_keys=False)
        # load_public_key()'s docstring promises True/False but the function has
        # NO return statement — it returns None on success AND on failure. Check
        # the state it populates, not its return value (live-caught 2026-07-21).
        dest_ident.load_public_key(pub_bytes)
        if getattr(dest_ident, "hash", None) is None:
            print("FAIL: could not load B public key")
            sys.exit(2)

    dest = RNS.Destination(dest_ident, RNS.Destination.OUT, RNS.Destination.SINGLE,
                           "lxmf", "delivery")
    computed = RNS.hexrep(dest.hash, delimit=False)
    if computed != b_hash:
        # Fail LOUD rather than storing a message at an address nobody asks
        # for — that reads as "node accepted it" while proving nothing.
        print(f"FAIL: computed destination {computed} != B's published {b_hash}")
        sys.exit(2)
    print(f"  destination verified: {computed}")

    msg = LXMF.LXMessage(dest, source, f"{MARKER} body", f"{MARKER} title",
                         desired_method=LXMF.LXMessage.PROPAGATED)
    router.handle_outbound(msg)

    deadline = time.time() + 180
    last = None
    while time.time() < deadline:
        st = _state_name(msg)
        if st != last:
            print(f"  state={st}  t+{int(time.time() - (deadline - 180))}s", flush=True)
            last = st
        if msg.state in (LXMF.LXMessage.SENT, LXMF.LXMessage.DELIVERED):
            print(f"A_SEND_RESULT=OK state={st}")
            print(f"A_MSG_HASH={RNS.hexrep(msg.hash, delimit=False)}")
            router.exit_handler()
            return
        if msg.state in (LXMF.LXMessage.FAILED, LXMF.LXMessage.REJECTED,
                         LXMF.LXMessage.CANCELLED):
            print(f"A_SEND_RESULT=FAIL state={st}")
            router.exit_handler()
            sys.exit(1)
        time.sleep(2)
    print(f"A_SEND_RESULT=TIMEOUT state={_state_name(msg)}")
    router.exit_handler()
    sys.exit(1)


def b_pull():
    ident, fresh = _identity(os.path.join(DRILL_DIR, "b_identity"))
    if fresh:
        print("FAIL: b_identity was regenerated — the drill message is addressed "
              "to the old one. Aborting rather than reporting a false negative.")
        sys.exit(2)
    router = _router("b")
    received = []

    def _cb(message):
        received.append(message)

    router.register_delivery_callback(_cb)
    router.register_delivery_identity(ident, display_name="propdrill-B")
    # NOT set_inbound_propagation_node() — it raises NotImplementedError in
    # lxmf 1.0.1+mf.1 ("inbound/outbound differentiation is currently not
    # implemented"). One setter serves both directions. Live-caught 2026-07-21.
    router.set_outbound_propagation_node(bytes.fromhex(PROP_NODE))

    print("  requesting messages from propagation node...", flush=True)
    router.request_messages_from_propagation_node(ident, max_messages=0)

    pr_names = {getattr(LXMF.LXMRouter, a): a for a in dir(LXMF.LXMRouter)
                if a.startswith("PR_")}
    deadline = time.time() + 180
    last = None
    while time.time() < deadline and not received:
        st = pr_names.get(router.propagation_transfer_state,
                          router.propagation_transfer_state)
        if st != last:
            print(f"  pr_state={st}", flush=True)
            last = st
        time.sleep(2)

    if not received:
        print("B_PULL_RESULT=FAIL no message received")
        router.exit_handler()
        sys.exit(1)
    for m in received:
        title = m.title.decode("utf-8", "replace") if isinstance(m.title, bytes) else str(m.title)
        content = m.content.decode("utf-8", "replace") if isinstance(m.content, bytes) else str(m.content)
        print(f"B_PULL_RESULT=OK title={title!r} content={content!r}")
        print(f"B_MARKER_MATCH={MARKER in title and MARKER in content}")
    router.exit_handler()


if __name__ == "__main__":
    mode = sys.argv[1]
    r = open_reticulum("/etc/reticulum", require_listener=True)
    if r is None:
        print("FAIL: open_reticulum returned None (rnsd degraded) — UNKNOWN, not a drill failure")
        sys.exit(3)
    if mode == "b-init":
        b_init()
    elif mode == "a-send":
        a_send(sys.argv[2])
    elif mode == "b-pull":
        b_pull()
    else:
        print(f"unknown mode {mode}")
        sys.exit(2)
