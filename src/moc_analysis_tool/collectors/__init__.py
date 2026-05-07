"""Collectors — turn raw artifacts into queryable presentation_capture.db.

The diag24h_parser is the primary collector for the BIRC 2026-05-17 talk:
it re-parses ~/meshforge_diag_24h/<box>/meshtasticd.log into structured
SQLite rows. Idempotent — re-running over the same logs is safe.
"""
