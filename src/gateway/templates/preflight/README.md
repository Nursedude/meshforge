# Gateway Pre-Flight Templates

This directory contains **known-good configuration templates** that the
Gateway Pre-Flight handler (`src/launcher_tui/handlers/gateway_preflight.py`)
compares live system state against. Think of each template as a frozen
snapshot of "this config worked, field-validated, don't let it drift."

## Format

Each template is a JSON file with a schema roughly:

```json
{
  "$schema_version": 1,
  "name": "short-slug",
  "description": "human-readable context",
  "maintainer": "callsign",
  "validated_date": "YYYY-MM-DD",

  "<category>": {
    "<field>": {
      "expected": <value | true | min_version_string>,
      "severity": "fail" | "warn",
      "note": "optional context for operators"
    }
  }
}
```

Categories currently honored by the pre-flight handler:
- `meshtastic` — radio config (region, preset, channel_num, bridge channel state)
- `gateway` — `~/.config/meshforge/gateway.json` contents
- `packages` — required Python packages with `min_version`
- `services` — systemd services that must be `active`
- `rns_shared_instance` — rnsd reachability
- `nomadnet` — NomadNet identity consistency

Fields with `severity: "fail"` block the overall PASS verdict.
Fields with `severity: "warn"` surface drift but don't fail.

## Built-in templates

| File | Use case |
|------|----------|
| `shortturbo_slot8_meshforge.json` | MeshForge fleet-host-3 reference config: HAT on US/SHORT_TURBO/slot 8 bridged via a dedicated 'meshforge' MQTT channel. First field-validated 2026-04-18. |

## Exporting a template from a live node

The pre-flight handler's "Export current config as template" menu action
writes `~/.config/meshforge/templates/exported_<timestamp>.json`.
Review, rename, copy into this directory, and commit.

## Adding new templates

1. Start from an exported snapshot or clone an existing template.
2. Decide severity per field: `fail` for things that break the bridge,
   `warn` for conventions.
3. Test the template on a known-good node (should produce zero drift).
4. Submit PR. Templates are config, not code — no tests required beyond
   "loads without raising".
