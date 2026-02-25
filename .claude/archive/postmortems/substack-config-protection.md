# Don't Touch My Config: How MeshForge Learned to Respect meshtasticd

*When your mesh NOC overwrites the file you just hand-edited*

**Date:** 2026-02-25
**Session:** claude/add-meshtastic-configs-RHL7f
**By:** Dude AI & WH6GXZ

---

You spend twenty minutes tuning your meshtasticd config. MaxNodes bumped to 400. Logging set to debug. Custom SPI pins for your hat. You save, restart, everything works.

Then you run MeshForge.

And your config is gone. Replaced with a 19-line skeleton that doesn't even know GPS exists.

That was the bug. MeshForge's SPI HAT configuration wizard was writing hardware settings directly to `/etc/meshtasticd/config.yaml` — the user's file — instead of using the overlay system (`config.d/`) that meshtasticd provides for exactly this purpose. Worse, the template MeshForge deployed on fresh installs was a stripped-down fragment. The real `config-dist.yaml` from the meshtastic/firmware project has Lora, GPS, I2C, Display, Touchscreen, Input, Logging, Webserver with SSL, HostMetrics, Config, and General sections. Ours had five.

We audited every config write in the codebase. Not just meshtasticd — Reticulum, Mosquitto, NomadNet, MeshChat. The findings:

- **One critical overwrite** in the SPI HAT wizard — removed
- **Two inline config duplicates** in the service menu — consolidated to the central template
- **One unguarded creation function** — added existence check
- **RNS, Mosquitto, NomadNet, MeshChat** — all clean (RNS backs up before writing, Mosquitto uses its own drop-in, others are read-only)

The template now matches upstream exactly. The inline fallback matches too. Fresh installs get the full config. Existing installs are never touched.

The real test: a clean Pi reimage with MeshForge from scratch. Because the only way to prove "we don't break your configs" is to start with nothing and verify what gets created.

Your config is yours. MeshForge uses overlays.

---

*Made with aloha for the mesh community.*

*73 de WH6GXZ*

---

**Dude AI** — AI Development Partner, MeshForge Project
**WH6GXZ (Nursedude)** — Architect, HAM General, Infrastructure Engineering

*MeshForge is open source: [github.com/Nursedude/meshforge](https://github.com/Nursedude/meshforge)*
