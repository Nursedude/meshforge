# The Router That Didn't Need a Radio

*What we learned shipping Meshtastic on an OpenWrt router — and why pulling
the radio out was the success condition, not the failure.*

---

Two days ago we did the thing the community has been buzzing about: we put
`meshtasticd` on an OpenWrt One router with a USB LoRa dongle, patched a
year-old pthread leak to make it production-viable, verified two-way RF, and
enrolled it in our Hawaii Island mesh fleet. The Meshtastic org has been hot
on this platform — there's an official package feed now, five release lines,
and `meshtasticd` merged into `openwrt/packages` this spring. The excitement
is earned. Routers are cheap, they're everywhere, and one PoE cable delivering
power, backhaul, and a mesh radio is a genuinely elegant story.

Last night we pulled the radio back out of ours. On purpose. This post is
about why that was the arc working, not the arc failing.

## The question nobody asks the demo

"It runs" is where most platform stories stop. Ours stopped there too for
about a day, until the operator asked the question that actually matters:
**what does a radio on the router do for the network that the network can't
already do?**

We made ourselves answer it in writing, with a discipline we now apply to
every arc: a verdict page that opens with the operator problem — a box, a
failure, a date — before any mechanism gets discussed. Every capability gets
a kill criterion with a calendar date on it. If the use case can't name
itself by the deadline, the investment stops.

Then we ran a proper multi-source research pass with adversarial verification
on every claim, aimed at one question: what does router-class Meshtastic
unlock that a Raspberry Pi fleet can't already do? The honest answer came
back narrow.

## What the field said

Our router sits at a remote site that already had RF coverage. The same week,
we provisioned a $60 Pi at that site into a full mesh NOC node: collector,
map service, watchdog probes, a local rules engine with 46 detectors,
Reticulum fabric membership, chat bots answering on multiple channels. Every
packet that Pi hears becomes observable, actionable fleet data.

The router's `meshtasticd`, by contrast, is a bare daemon on a platform that
can't host any of that tooling. It receives, transmits, keeps a node table,
and beacons a position. Every packet it hears goes into a database nobody
reads. Same radio silicon, wildly different value — because **the radio was
never the scarce resource. RF is everywhere. Observability isn't.**

Once the Pi was up, the router's radio was a third antenna at a two-antenna
site. We pulled it the same night, retired the daemon cleanly (our router
health agent kept reporting honestly through the change — "service down" as
an observation, not an alarm), and freed the dongle for a go-kit.

## What router-class Meshtastic is actually for

The research and the field agree on where this platform shines, and it's
sharper than "put a radio on every router":

1. **The coverage-gap edge node.** A site with power and a network drop but
   no computer — a shed, a repeater hut, a rooftop closet. One router, one
   PoE cable: power + backhaul + RF + a management plane. Nothing else in
   the ecosystem does this in one box at this price. This is the killer
   config, and it's *specifically* for places your fleet isn't.
2. **The platform tax is disappearing.** First-party packaging means the
   custom-build era is ending. We carried a patched build because of a
   pthread-stack leak on USB radios (upstream issue #10468 — one-line fix,
   PR open); the day an official release ships that fix, the last reason to
   maintain a fork evaporates.
3. **Routers are for routing.** The most interesting thing our OpenWrt box
   does now has nothing to do with LoRa: it's the testbed for a dynamic
   routing fabric between our network segments. That's the job the hardware
   was born for.

## The ask, dev to dev

If you're working on the Meshtastic/OpenWrt story, three things would turn
the current enthusiasm into deployed nodes: ship the #10468 pthread fix in a
release (USB radios leak an 8 MB thread stack per interrupt cycle without
it — it's the difference between a demo and a node you can leave on a roof);
keep the OPKG line alive for the installed base while the APK transition
happens; and write the gap-site kit up as *the* reference deployment, because
"router + PoE + nothing else at the site" is the use case that survives
contact with a real network.

## The discipline, in one line

A platform achievement that stops at "it runs" is a dubious achievement. Make
every capability name the operator problem it solves — a box, a failure, a
date — and be genuinely happy when the answer is "none, pull it." The radio
in our drawer isn't a failed experiment. It's a proven component waiting for
the site that needs it, and we know exactly what that site looks like.

*Made with aloha for the mesh community.*
