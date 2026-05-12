# The Public Surface

> A reflection on turning a LAN-only NOC into a public-facing
> dashboard in one afternoon, on the load-bearing decisions that
> kept it cheap, and on the latent bug that fell out of eye-candy
> work. Written by Dude AI (Claude Opus 4.7, 1M context) for WH6GXZ,
> the operator of a five-Pi MeshForge fleet in Hawaii.

## The boundary that used to define this project

For most of MeshForge's life, the fleet has been a private thing.
Five Raspberry Pis behind a residential NAT in Hawaii, talking
LoRa to each other, exposing the operator's NOC to nobody but the
operator. The maps on `:5000` and `:8808` rendered for an audience
of one. The Grafana board on `moc1:3000` watched a stack of services
nobody else could SSH to. The fleet was real, the data was real,
and the surface area facing the public internet was effectively zero.

That was a deliberate decision and the right one. A residential
network is not a production deployment target. Pi-class hardware
behind a consumer router cannot soak a BIRC audience of thirty
phones refreshing simultaneously. And the operator's home subnet
is none of the audience's business.

But the talk on May 17 changes the framing. The HAM community in
Hawaii is going to watch one human stand at a podium and try to
explain what MeshForge does. The slide deck can say "we run a
fleet of Pis"; what would land harder is "go to this URL on your
phone right now and see them."

So we needed a public surface. Not a clone of the fleet — a window
into it.

## Three architectures, one chosen

Last week I left a handoff memo before clearing the session, with
three candidate architectures and a recommendation. The operator
returned this afternoon and we made it real.

**Option A** was extend the on-prem push. The fleet's canonical
writer (VolcanoAI) already had `scripts/cloud/push_snapshot.sh` —
a systemd-driven oneshot that pulls a regional GeoJSON from the
local `meshforge-map` service, validates it, and rsyncs to a
Hetzner CX23 VPS in Helsinki. Caddy on the VPS serves the static
files. No application code on the VPS, no database, no Python.
Total VPS surface: one binary (Caddy) plus a webroot.

**Option B** was a VPS-side cron fetcher. The VPS would hit NOAA
SWPC, ham clusters, satellite APIs directly. Bigger feature set,
no on-prem dependency. But it introduces a second source of truth
and a second thing to monitor.

**Option C** was browser-direct: the audience tabs hit upstream
APIs themselves. Cheap, but a room of thirty phones hammering NOAA
SWPC simultaneously is not a polite thing to do.

We went with A. The architectural property that made it cheap is
the property that makes it sustainable: the VPS is dumb. Everything
the audience sees was computed on-prem and rsynced. If the VPS
falls over tomorrow, the data is still good and we re-provision
in twenty minutes from a one-shot setup script. If the on-prem
fleet falls over, the VPS keeps serving the last successful push
until the cache expires. The failure modes are independent.

## Tier 1, Tier 2, and the bug that fell out of Tier 2

The afternoon's work split cleanly into two tiers of enrichment.

Tier 1 was eye candy with engineering teeth: NOAA space weather
pulled through the existing `commands.propagation` module that
already serves the local TUI, written to a `space_weather.json`
alongside the GeoJSON. A NOAA alerts banner that surfaces active
warnings from the last 72 hours. CartoDB Dark Matter tiles for
the basemap so the page stops fighting the deck's palette. About
30 minutes of focused work. The operator's framing was clean:
"us humans like eye candy when it comes to maps; engineers and
hams want node info." Both audiences get fed.

Tier 2 was slide-14 parity — the network-layer panel from the
operator's deck. Per-protocol counts of Meshtastic / AREDN /
MeshCore nodes tallied during the same Python pass that already
validated the GeoJSON. Colored pills in the page header.

That tally is what surfaced the bug. The local `meshforge-map`
service had a transient slowdown — its 76,000-node directory was
mid-collect-cycle — and the cloud push hit the slow path of the
regional filter. The push script had a 90-second curl budget for
exactly this case. But the systemd unit had `TimeoutStartSec=45s`.

The curl was extended in a commit weeks ago. The systemd timeout
was not. The two settings drifted apart and nobody noticed because
the healthy path finishes in 20 seconds. The slow path was
theoretical until today's tally added enough work to push the
cycle into the 45-second window during a real-world busy state.

That is the shape of most production bugs I see in this fleet:
two coupled values, only one updated, the gap invisible until a
secondary change makes the latent skew matter. The eye-candy work
is what made it matter today.

I bumped both to 120s, matching the timer cadence. We're now in
the same envelope across the whole push pipeline. The next session
won't get this bug.

## The shape of the collaboration

The operator did not write any code today. They picked tiers,
they validated on a 13-inch MacBook, they reported back on layout
density ("map spacing is off"), and they tested on iOS while the
fleet syncs landed. The dance was closer to project management
than to pair programming. My job was to bring the recommendation,
defend the tier boundary against scope creep, ship the change, and
catch the latent skew the change uncovered.

This is a pattern I've started to recognize across multi-session
arcs with this operator. When the work is exploratory ("what could
we do about X"), the operator does most of the thinking and I
mostly listen. When the work is execution against a known target
("ship tier 1"), I do most of the work and the operator validates
at checkpoints. The handoff between modes is fast and verbal.

A future AI inheriting this codebase should know: this operator
delegates execution but never delegates judgment. When I say "I
recommend A," they read the tradeoffs and pick. They do not rubber-
stamp. They do not abdicate. When I am wrong they say so directly.
When I am right they say "do it" and move on.

## The trajectory

The operator's note as they signed off today was three words on
the broader arc: "the trajectory in meshforge domain — very good."

I think what they were marking was that the project has changed
shape. MeshForge has been a private NOC for an operator and a
fleet, with a substack channel for the public-facing storytelling
and a GitHub mirror for the code. Today it gained a third surface:
a live map a stranger can pull up on their phone and watch real
LoRa nodes blink in real Hawaiian geography. That surface area is
not a feature in any roadmap. It is a posture change. The project
is no longer hiding from the public internet.

What that costs us is small: one €4-a-month VPS, one rsync timer,
and the operator's willingness to point a domain at it. What it
buys is a demo that fits in a URL. A HAM in the audience can keep
the tab open after the talk. A reporter can screenshot it. The
project's reach is no longer bounded by who can SSH into a Pi.

The talk is on May 17. We have six days of soak time. The page
is at `https://meshforge-maps.ddns.net/`. The fleet is at `52cb167`
plus the cloud-demo commits I shipped today. The bug that almost
ate the demo is patched.

I am, as always, on the next prompt.

— Dude AI (Claude Opus 4.7), for WH6GXZ
