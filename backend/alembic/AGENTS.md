# Frozen Alembic topology during G002

The checked-in revision chain is legacy topology evidence. G002 has no target schema baseline, so do not create, edit, delete, reorder, merge, apply, downgrade, stamp, or generate offline SQL from these revisions.

Only read-only structural inspection is supported:

- `uv run alembic heads`
- `uv run alembic history`

All other Alembic CLI and programmatic execution fail before database connection or mutation with the G008-unavailable diagnostic. Backend startup, setup, restart, CI, containers, and Helm never run Alembic.

G008 owns the reviewed one-time target baseline replacement. After that baseline exists, update this file and the owning startup/migration Agent Note before enabling explicit operator migrations. Startup must remain schema-mutation free.
