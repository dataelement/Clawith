"""Create native group chat domain tables.

Revision ID: create_group_domain_schema
Revises: add_title_to_agent_focus_items
Create Date: 2026-07-13 18:43:00
"""

from collections.abc import Sequence
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "create_group_domain_schema"
down_revision: str | None = "add_title_to_agent_focus_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_GROUP_COLUMNS = {
    "id": (("uuid", True), False, None),
    "tenant_id": (("uuid", True), False, None),
    "name": (("string", 200), False, None),
    "description": (("text",), True, None),
    "created_by_participant_id": (("uuid", True), False, None),
    "deleted_at": (("datetime", True), True, None),
    "created_at": (("datetime", True), False, "now"),
    "updated_at": (("datetime", True), False, "now"),
}
_GROUP_MEMBER_COLUMNS = {
    "id": (("uuid", True), False, None),
    "group_id": (("uuid", True), False, None),
    "participant_id": (("uuid", True), False, None),
    "role": (("string", 20), False, "member"),
    "joined_at": (("datetime", True), False, "now"),
    "removed_at": (("datetime", True), True, None),
    "session_read_state": (("jsonb",), False, "empty_json_object"),
}


def _type_signature(column_type: sa.types.TypeEngine) -> tuple[object, ...]:
    if isinstance(column_type, postgresql.JSONB):
        return ("jsonb",)
    if isinstance(column_type, sa.Uuid):
        return ("uuid", bool(column_type.as_uuid))
    if isinstance(column_type, sa.DateTime):
        return ("datetime", bool(column_type.timezone))
    if isinstance(column_type, sa.Text):
        return ("text",)
    if isinstance(column_type, sa.String):
        return ("string", column_type.length)
    return (type(column_type).__name__.lower(),)


def _canonical_default(value: object | None) -> str | None:
    if value is None:
        return None

    normalized = re.sub(r"\s+", " ", str(value).strip().lower())
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()

    if normalized in {"now()", "current_timestamp", "current_timestamp()"}:
        return "now"
    if re.fullmatch(r"'member'(?:::(?:character varying|text))?", normalized):
        return "member"
    if normalized == "'{}'::jsonb" or normalized == "'{}'":
        return "empty_json_object"
    return normalized


def _require_columns(
    inspector: sa.Inspector,
    table_name: str,
    expected: dict[str, tuple[tuple[object, ...], bool, str | None]],
) -> None:
    actual = {column["name"]: column for column in inspector.get_columns(table_name)}
    missing = set(expected) - set(actual)
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise RuntimeError(f"Existing {table_name} table is missing required columns: {missing_list}")

    for column_name, (expected_type, expected_nullable, expected_default) in expected.items():
        column = actual[column_name]
        actual_type = _type_signature(column["type"])
        actual_nullable = bool(column["nullable"])
        actual_default = _canonical_default(column.get("default"))
        if (actual_type, actual_nullable, actual_default) != (
            expected_type,
            expected_nullable,
            expected_default,
        ):
            raise RuntimeError(
                f"Existing {table_name}.{column_name} has schema "
                f"type={actual_type!r}, nullable={actual_nullable!r}, default={actual_default!r}; "
                f"expected type={expected_type!r}, nullable={expected_nullable!r}, "
                f"default={expected_default!r}"
            )


def _require_primary_key(
    inspector: sa.Inspector,
    table_name: str,
    *,
    name: str,
    columns: tuple[str, ...],
) -> None:
    actual = inspector.get_pk_constraint(table_name)
    actual_spec = (actual.get("name"), tuple(actual.get("constrained_columns") or ()))
    expected_spec = (name, columns)
    if actual_spec != expected_spec:
        raise RuntimeError(f"Existing {table_name} table has primary key {actual_spec!r}; expected {expected_spec!r}")


def _require_foreign_keys(
    inspector: sa.Inspector,
    table_name: str,
    expected: dict[str, tuple[tuple[str, ...], str, tuple[str, ...], str]],
) -> None:
    actual_by_name = {constraint.get("name"): constraint for constraint in inspector.get_foreign_keys(table_name)}
    for name, expected_spec in expected.items():
        constraint = actual_by_name.get(name)
        if constraint is None:
            raise RuntimeError(f"Existing {table_name} table is missing foreign key: {name}")
        options = constraint.get("options") or {}
        ondelete = options.get("ondelete") or constraint.get("ondelete")
        actual_spec = (
            tuple(constraint.get("constrained_columns") or ()),
            constraint.get("referred_table"),
            tuple(constraint.get("referred_columns") or ()),
            str(ondelete).upper() if ondelete is not None else None,
        )
        if actual_spec != expected_spec:
            raise RuntimeError(
                f"Existing {table_name} foreign key {name} has schema {actual_spec!r}; expected {expected_spec!r}"
            )


def _require_unique_constraints(
    inspector: sa.Inspector,
    table_name: str,
    expected: dict[str, tuple[str, ...]],
) -> None:
    actual_by_name = {
        constraint.get("name"): tuple(constraint.get("column_names") or ())
        for constraint in inspector.get_unique_constraints(table_name)
    }
    for name, expected_columns in expected.items():
        actual_columns = actual_by_name.get(name)
        if actual_columns != expected_columns:
            raise RuntimeError(
                f"Existing {table_name} unique constraint {name} has columns {actual_columns!r}; "
                f"expected {expected_columns!r}"
            )


def _is_group_member_role_check(sqltext: object) -> bool:
    normalized = re.sub(r"\s+", " ", str(sqltext).strip().lower())
    literals = set(re.findall(r"'([^']+)'", normalized))
    has_supported_operator = " in " in normalized or re.search(r"=\s*any\s*\(", normalized) is not None
    return (
        re.search(r"\brole\b", normalized) is not None
        and has_supported_operator
        and literals
        == {
            "manager",
            "member",
        }
    )


def _require_role_check(inspector: sa.Inspector) -> None:
    actual_by_name = {
        constraint.get("name"): constraint.get("sqltext")
        for constraint in inspector.get_check_constraints("group_members")
    }
    sqltext = actual_by_name.get("ck_group_members_role")
    if sqltext is None or not _is_group_member_role_check(sqltext):
        raise RuntimeError(
            "Existing group_members check constraint ck_group_members_role does not restrict role to manager/member"
        )


def _ensure_index(
    inspector: sa.Inspector,
    table_name: str,
    *,
    name: str,
    columns: tuple[str, ...],
) -> None:
    actual_by_name = {index.get("name"): index for index in inspector.get_indexes(table_name)}
    index = actual_by_name.get(name)
    if index is None:
        op.create_index(name, table_name, list(columns), unique=False)
        return

    actual_spec = (tuple(index.get("column_names") or ()), bool(index.get("unique")))
    expected_spec = (columns, False)
    if actual_spec != expected_spec:
        raise RuntimeError(f"Existing {table_name} index {name} has schema {actual_spec!r}; expected {expected_spec!r}")


def _create_groups() -> None:
    op.create_table(
        "groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_participant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_groups_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_participant_id"],
            ["participants.id"],
            name="fk_groups_created_by_participant_id_participants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_groups"),
    )


def _create_group_members() -> None:
    op.create_table(
        "group_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=20), server_default=sa.text("'member'"), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "session_read_state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('manager', 'member')", name="ck_group_members_role"),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name="fk_group_members_group_id_groups",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["participants.id"],
            name="fk_group_members_participant_id_participants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_group_members"),
        sa.UniqueConstraint("group_id", "participant_id", name="uq_group_members_group_participant"),
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    groups_created = "groups" not in existing_tables
    if groups_created:
        _create_groups()
    else:
        _require_columns(inspector, "groups", _GROUP_COLUMNS)
        _require_primary_key(
            inspector,
            "groups",
            name="pk_groups",
            columns=("id",),
        )
        _require_foreign_keys(
            inspector,
            "groups",
            {
                "fk_groups_tenant_id_tenants": (("tenant_id",), "tenants", ("id",), "RESTRICT"),
                "fk_groups_created_by_participant_id_participants": (
                    ("created_by_participant_id",),
                    "participants",
                    ("id",),
                    "RESTRICT",
                ),
            },
        )

    group_members_created = "group_members" not in existing_tables
    if group_members_created:
        _create_group_members()
    else:
        _require_columns(inspector, "group_members", _GROUP_MEMBER_COLUMNS)
        _require_primary_key(
            inspector,
            "group_members",
            name="pk_group_members",
            columns=("id",),
        )
        _require_foreign_keys(
            inspector,
            "group_members",
            {
                "fk_group_members_group_id_groups": (("group_id",), "groups", ("id",), "CASCADE"),
                "fk_group_members_participant_id_participants": (
                    ("participant_id",),
                    "participants",
                    ("id",),
                    "RESTRICT",
                ),
            },
        )
        _require_unique_constraints(
            inspector,
            "group_members",
            {"uq_group_members_group_participant": ("group_id", "participant_id")},
        )
        _require_role_check(inspector)

    if groups_created:
        op.create_index("ix_groups_tenant_id_deleted_at", "groups", ["tenant_id", "deleted_at"], unique=False)
    else:
        _ensure_index(
            inspector,
            "groups",
            name="ix_groups_tenant_id_deleted_at",
            columns=("tenant_id", "deleted_at"),
        )

    if group_members_created:
        op.create_index("ix_group_members_participant_id", "group_members", ["participant_id"], unique=False)
    else:
        _ensure_index(
            inspector,
            "group_members",
            name="ix_group_members_participant_id",
            columns=("participant_id",),
        )


def _require_empty_tables(bind: sa.Connection, table_names: list[str]) -> None:
    if not table_names:
        return

    quoted_tables = ", ".join(f'"{table_name}"' for table_name in table_names)
    bind.execute(sa.text(f"LOCK TABLE {quoted_tables} IN ACCESS EXCLUSIVE MODE"))
    for table_name in table_names:
        result = bind.execute(sa.text(f'SELECT 1 FROM "{table_name}" LIMIT 1'))
        if result.first() is not None:
            raise RuntimeError(f"Refusing to downgrade group domain schema because {table_name} contains data")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    tables_to_drop = [table_name for table_name in ("group_members", "groups") if table_name in existing_tables]

    _require_empty_tables(bind, tables_to_drop)

    if "group_members" in existing_tables:
        op.drop_table("group_members")
    if "groups" in existing_tables:
        op.drop_table("groups")
