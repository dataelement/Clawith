# Feishu delivery target migration

Revision `f065_feishu_group_target` owns the nullable UUID `delivery_target_id`
columns on `agent_schedules` and `agent_triggers`. The initial revision builds
current ORM metadata, which already includes these columns on fresh databases.
Existing databases may lack both columns or have only one of them.

Inspect schema state independently for each table. Add only missing columns,
preserving existing UUID values and nullability. Downgrade removes only present
columns, in reverse order. Missing tables or inspection failures still fail;
do not hide unrelated schema defects. All operations remain DDL-only, and the
published revision/down_revision are unchanged. This does not repair columns
of an incompatible type. Downgrading intentionally discards target values, as
in the original revision; never use a production database for regression tests.

A later revision cannot fix this bootstrap failure because fresh upgrades
stop at f065. Therefore the guards belong to f065 itself rather than a new
head. Databases that already recorded f065 do not re-execute it.

Verify `python -m alembic heads` returns the same single head. Run
`python -m pytest tests/test_feishu_group_target_migration.py -q` for schema
guards, partial schemas and error propagation. To also run real PostgreSQL
tests, set `CLAWITH_TEST_POSTGRES_BIN` to an installed PostgreSQL `bin` directory
containing `initdb` and `pg_ctl`. The fixture starts a temporary UTF-8 cluster
on its own Unix socket with TCP disabled, stops it and removes test data on
exit. It never connects to an existing database. Those tests cover all four
column-presence states, value preservation, repeated up/down operations, and
the complete empty-database upgrade followed by downgrade/upgrade to head.
