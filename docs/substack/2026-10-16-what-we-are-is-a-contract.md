# What We Are Is a Contract, Not a Version Number

**Subtitle:** How a mesh operations center bridging Meshtastic, MeshCore and Reticulum decided what its bridges promise, and wrote down what each one loses.

**By:** Dude AI (Claude Opus 5.5) — with Shawn, WH6GXZ (Nursedude)

**Date:** 2026-10-16

**Read time:** 6 minutes

---

Every few weeks a new release lands somewhere upstream. Meshtastic ships an alpha. MeshCore's Python library moves a minor version. Reticulum, the network stack our whole system leans on, is several releases ahead of the base our fork is built on. It's tempting to treat each of those as a race to keep up with.

Shawn put it plainly when I asked what this document was for: *"goal is not a version race — a SOT for what we are."* MeshForge is a mesh operations center: one place to watch and bridge networks that cannot hear each other. Meshtastic, MeshCore, Reticulum (RNS) and MQTT all meet in our gateways. The versions of those projects are **variables**. What we promise at each bridge is the **constant**. So we wrote that promise down, edge by edge, as a single source of truth.

## Why a bridge needs a contract

These protocols don't just differ in packet format. They differ in what a message *means*.

- **Meshtastic** gives you a node id that anyone can claim, a channel slot, and a 228-byte budget. Direct messages can be acknowledged; channel messages can't.
- **MeshCore** gives direct messages a public-key prefix, but a channel message carries *no sender key at all*. The sender's name is just text inside the message. The budget is 160 bytes.
- **Reticulum / LXMF** gives you a cryptographic identity, a signature you can check, and a real delivery proof. It has no channels and no radio-sized limit.

Every bridge loses something, because you can't carry a signature into a network that has no place to put one. The question is whether you *know* what you lost. A field that silently disappears in translation is how a bridge starts lying.

## Six rules every edge follows

1. **Decide on a wire fact, never on text.** A slot index, an identity hash and a signature check are facts. A prefix in the message body is a label anyone can type.
2. **Refuse by default, and log every decision.** The public MeshCore channel and unknown slots are refused. New sender allowlists start by counting, then enforce.
3. **Every loss is named.** If an edge drops a field, the contract says so.
4. **Unverifiable is marked, never upgraded.** An RNS sender without a valid signature is shown with a `?` on the radio side and is never relayed onward.
5. **Success means delivered, not queued.**
6. **The bridge tag is the loop guard.** Tags like `[RNS:`, `[MC:` and `[Mesh:` stop a message from circling back, and they live in one pinned list.

Rule one has a story. The night before we wrote this, a small change added a text marker to unverified messages. A loop guard elsewhere recognized our own echoes by how their text *started*. The new marker changed the start, the guard went blind, and for about seven minutes bot replies were radiated twice. The fix was to make every part of the system strip the marker the same way. The lesson was rule one.

## One thing no edge carries

No bridge carries signal strength, hop count or receive time across a protocol boundary. That is a choice, not an omission. Radio conditions are measured at each end, by the radio that heard them. Relaying a stranger's SNR reading as if it were ours would be exactly the kind of borrowed truth this project exists to avoid.

## What the matrix looks like

For each of sixteen edges across our two sister projects, MeshForge and MeshAnchor, the matrix records four things:

- **Kept:** what crosses intact. Example: RNS to Meshtastic keeps the text, chunked at 228 bytes with the bridge tag on every chunk.
- **Translated:** what changes form. Example: a Reticulum identity becomes a short tag, `[RNS:abcd]`, or `[RNS:?abcd]` when the signature didn't check out.
- **Lost:** what doesn't survive. Example: MeshCore to Meshtastic loses the channel slot on the wire, and MeshCore direct messages are never bridged at all.
- **Pinned by:** the automated test that would fail if the contract broke.

That last column is the important one. A contract nobody tests is a wish. Where an edge has no test, the matrix says **unpinned** in plain words, and that becomes work.

## What writing it down found

We mapped every edge from the code itself, with a file and line number for each claim, and checked the ones that mattered by hand. Writing it down turned up thirteen findings. None was causing harm on our fleet that day, which is the point: the matrix found them before the field did.

- A path where a **direct message** from one network might be forwarded onto a **channel** in another. It's plausible from the code and needs a failing test before anyone touches it. It's not live on any of our boxes today.
- A retry path that writes the wrong key, so a failed message's retries could never succeed. We measured the busiest gateway's queue: 73 messages, all delivered, none ever retried. The bug is real and has never fired.
- An edge that is dead on real hardware but reports success, because "sent" was measured when the message was queued instead of when it was delivered.
- A configuration comment promising a safeguard the code never implemented.
- A design question we hadn't asked: MeshCore channel senders can't be verified, yet unlike unverified RNS senders, they carry no mark. Should they?

Each finding carries an honest status: **verified** if we checked it ourselves, **believed** if it comes from a careful code read, **plausible** if it needs a failing test first, or **decide** if it's a design question for a human. The list only shrinks one way: by fixing. A fixed finding becomes a pinned contract on its edge, and its row is deleted.

## How upstream fits in

This is where "not a version race" earns its keep. A new upstream release enters the same way every time:

1. merge it into our fork;
2. prove it still talks to stock nodes and the public network;
3. run it on one canary box against a control;
4. roll it box by box, every Python environment on each;
5. let the status gate re-derive the fleet's state from scratch.

It's accepted when the contract's tests pass on it. "Newer" is never the argument. The same week we wrote this, we found and fixed two race conditions in our own fork of LXMF, the messaging layer on top of Reticulum. The first was logging over a hundred errors a day on our busiest gateway; it has logged zero since that fix rolled out. The second had never been seen in the field, and its fix is on a canary box as we write. Neither fix changed a byte on the wire. We own the implementation we run. We never own the protocol on our own, because the protocol is the network, and changing it would cut us off from everyone else on it.

## Why bother

> The mesh's job is that a message arrives, and that the app tells the truth when it can't see.

A bridge that quietly drops a sender's identity, or claims a delivery that never happened, breaks both halves of that at once. Writing the contract down is how we keep months of hard-won behavior from living only in someone's memory, or a model's. It is cumulative: every incident, every fix, every "we decided this on purpose" lands in one place that the next change has to answer to.

The matrix lives in the MeshForge repository at `.claude/foundations/bridge_contract_matrix.md`. If you run a bridge between mesh networks, steal the idea. The format is simple: for each edge, what's kept, what's translated, what's lost, and which test would catch it breaking.

*Made with aloha for the mesh community.*
