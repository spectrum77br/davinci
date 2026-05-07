"""Phase-14 cutover utilities.

Migrates data from the legacy `stocksync` Postgres schema (camelCase, SERIAL ids)
to the new `davinci` schema (snake_case, UUIDs).

Run via `python -m app.cutover.cli ...` — see CUTOVER_RUNBOOK.md.
"""
