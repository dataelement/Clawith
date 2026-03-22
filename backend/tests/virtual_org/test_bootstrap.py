import asyncio
import uuid

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models.agent import Agent, AgentTemplate
from app.models.llm import LLMModel
from app.models.tenant import Tenant
from app.models.user import Department, User
from app.models.virtual_org import AgentVirtualOrg, AgentVirtualTag, VirtualDepartment
from app.services import template_seeder
from app.services.template_seeder import load_default_templates
from app.services.virtual_org_bootstrap import (
    assign_manager,
    bootstrap_virtual_org,
    load_virtual_org_seed_data,
    prepare_virtual_org_bootstrap_startup,
    validate_virtual_org_seed_data,
)

import importlib.util
from pathlib import Path


def _sqlite_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    return engine


def _schema_tables():
    return [
        Tenant.__table__,
        Department.__table__,
        User.__table__,
        LLMModel.__table__,
        AgentTemplate.__table__,
        Agent.__table__,
        VirtualDepartment.__table__,
        AgentVirtualOrg.__table__,
        AgentVirtualTag.__table__,
    ]


@pytest.fixture()
def session():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine, tables=_schema_tables())

    with Session(engine) as db_session:
        yield db_session

    engine.dispose()


def _create_tenant(session: Session) -> Tenant:
    tenant = Tenant(name=f"Tenant-{uuid.uuid4()}", slug=f"tenant-{uuid.uuid4().hex[:12]}")
    session.add(tenant)
    session.commit()
    return tenant


def _create_user(session: Session, tenant_id: uuid.UUID) -> User:
    user = User(
        username=f"user-{uuid.uuid4().hex[:8]}",
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed",
        display_name="Test User",
        tenant_id=tenant_id,
    )
    session.add(user)
    session.commit()
    return user


def _create_template(
    session: Session,
    *,
    name: str | None = None,
    source_key: str | None = None,
) -> AgentTemplate:
    template = AgentTemplate(
        name=name or f"Template-{uuid.uuid4().hex[:8]}",
        description="template",
        icon="T",
        category="test",
        soul_template="",
        default_skills=[],
        default_autonomy_policy={},
        is_builtin=True,
        source_key=source_key or f"source/{uuid.uuid4().hex}.md",
    )
    session.add(template)
    session.commit()
    return template


def _create_agent(
    session: Session,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    template_id: uuid.UUID,
    *,
    name: str | None = None,
) -> Agent:
    agent = Agent(
        name=name or f"Agent-{uuid.uuid4().hex[:8]}",
        role_description="role",
        creator_id=creator_id,
        tenant_id=tenant_id,
        template_id=template_id,
    )
    session.add(agent)
    session.commit()
    return agent


def _create_department(session: Session, tenant_id: uuid.UUID, slug: str) -> VirtualDepartment:
    department = VirtualDepartment(name=slug.title(), slug=slug, tenant_id=tenant_id)
    session.add(department)
    session.commit()
    return department


def _sample_seed_rows() -> list[dict[str, object]]:
    return [
        {
            "source_key": "support/support-executive-summary-generator.md",
            "template_name": "高管摘要师",
            "agent_name": "高管摘要师",
            "department_slug": "executive",
            "title": "CEO办公室战略参谋",
            "level": "L1",
            "org_bucket": "core",
            "manager_source_key": None,
            "tags": ["core-org", "executive"],
        },
        {
            "source_key": "product/product-manager.md",
            "template_name": "产品经理",
            "agent_name": "产品经理",
            "department_slug": "product",
            "title": "产品负责人",
            "level": "L2",
            "org_bucket": "core",
            "manager_source_key": "support/support-executive-summary-generator.md",
            "tags": ["core-org"],
        },
        {
            "source_key": "engineering/engineering-frontend-developer.md",
            "template_name": "前端开发者",
            "agent_name": "前端开发者",
            "department_slug": "engineering",
            "title": "前端负责人",
            "level": "L3",
            "org_bucket": "core",
            "manager_source_key": "product/product-manager.md",
            "tags": ["frontend", "core-org"],
        },
        {
            "source_key": "academic/academic-anthropologist.md",
            "template_name": "人类学家",
            "agent_name": "人类学家",
            "department_slug": "expert-pool",
            "title": "文化研究顾问",
            "level": "L5",
            "org_bucket": "expert",
            "manager_source_key": None,
            "tags": ["advisor", "expert-pool"],
        },
    ]


def _create_templates_for_seed_rows(session: Session, rows: list[dict[str, object]]) -> dict[str, AgentTemplate]:
    templates: dict[str, AgentTemplate] = {}
    for row in rows:
        source_key = str(row["source_key"])
        templates[source_key] = _create_template(
            session,
            name=str(row["template_name"]),
            source_key=source_key,
        )
    return templates


def _load_migration_module():
    migration_path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "20260321_add_virtual_org.py"
    spec = importlib.util.spec_from_file_location("virtual_org_migration", migration_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_bootstrap_schema_exposes_source_key_and_primary_instance_flag(session: Session):
    inspector = inspect(session.bind)

    template_columns = {column["name"] for column in inspector.get_columns("agent_templates")}
    assert "source_key" in template_columns

    org_columns = {column["name"] for column in inspector.get_columns("agent_virtual_org")}
    assert "is_org_primary_instance" in org_columns
    assert "tenant_id" in org_columns


def test_template_seeder_loads_source_keys():
    templates = load_default_templates()

    assert templates
    assert "source_key" in templates[0]
    assert templates[0]["source_key"]


def test_virtual_org_seed_file_contains_department_and_assignment_samples():
    seed_data = load_virtual_org_seed_data()

    assert seed_data["departments"]
    assert seed_data["assignments"]
    assert any(row["org_bucket"] == "core" for row in seed_data["assignments"])
    assert any(row["org_bucket"] == "expert" for row in seed_data["assignments"])


def test_virtual_org_seed_file_covers_all_builtin_templates_and_special_agents():
    seed_data = load_virtual_org_seed_data()
    templates = load_default_templates()

    assignment_source_keys = {row["source_key"] for row in seed_data["assignments"] if row.get("source_key")}
    template_source_keys = {template["source_key"] for template in templates if template.get("source_key")}
    special_agents = {row["agent_name"] for row in seed_data["assignments"] if not row.get("source_key")}

    assert assignment_source_keys >= template_source_keys
    assert {"Morty", "Meeseeks"} <= special_agents


def test_virtual_org_seed_validation_rejects_unresolvable_manager_source_key(tmp_path):
    seed_path = tmp_path / "invalid_virtual_org_seed.json"
    seed_path.write_text(
        """
{
  "departments": [{"slug": "executive", "name": "高管层"}],
  "assignments": [
    {
      "source_key": "support/support-executive-summary-generator.md",
      "template_name": "高管摘要师",
      "agent_name": "高管摘要师",
      "department_slug": "executive",
      "title": "CEO办公室战略参谋",
      "level": "L1",
      "org_bucket": "core",
      "manager_source_key": "missing/manager.md",
      "tags": ["core-org"]
    }
  ]
}
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="manager_source_key"):
        validate_virtual_org_seed_data(load_virtual_org_seed_data(seed_path))


def test_startup_prep_raises_when_manager_template_is_missing(monkeypatch):
    seed_data = {
        "departments": [{"slug": "executive", "name": "高管层"}],
        "assignments": [
            {
                "source_key": "support/support-executive-summary-generator.md",
                "template_name": "高管摘要师",
                "agent_name": "高管摘要师",
                "department_slug": "executive",
                "title": "CEO办公室战略参谋",
                "level": "L1",
                "org_bucket": "core",
                "manager_source_key": None,
                "tags": ["core-org"],
            },
            {
                "source_key": "product/product-manager.md",
                "template_name": "产品经理",
                "agent_name": "产品经理",
                "department_slug": "executive",
                "title": "产品负责人",
                "level": "L2",
                "org_bucket": "core",
                "manager_source_key": "support/support-executive-summary-generator.md",
                "tags": ["core-org"],
            },
        ],
    }

    class FakeSession:
        async def execute(self, _statement):
            return [("product/product-manager.md",)]

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("app.services.virtual_org_bootstrap.load_virtual_org_seed_data", lambda: seed_data)
    monkeypatch.setattr("app.services.virtual_org_bootstrap.async_session", lambda: FakeAsyncSessionContext())

    with pytest.raises(ValueError, match="missing manager templates"):
        asyncio.run(prepare_virtual_org_bootstrap_startup())


def test_migration_embeds_stable_source_key_backfill_rows():
    migration = _load_migration_module()
    rows = migration.get_source_key_backfill_rows()

    assert rows
    assert all("name" in row and "source_key" in row for row in rows)
    assert {"name": "人类学家", "source_key": "academic/academic-anthropologist.md"} in rows


def test_migration_backfill_helper_executes_parameterized_updates(monkeypatch):
    migration = _load_migration_module()
    executed: list[tuple[str, dict[str, str]]] = []

    class FakeConnection:
        def execute(self, statement, params):
            executed.append((str(statement), dict(params)))

    fake_connection = FakeConnection()
    monkeypatch.setattr(migration.op, "get_bind", lambda: fake_connection)

    executed_count = migration._backfill_source_keys()

    assert executed_count == len(migration.get_source_key_backfill_rows())
    assert executed
    sql, params = executed[0]
    assert "UPDATE agent_templates" in sql
    assert params == migration.get_source_key_backfill_rows()[0]


def test_template_seeder_fallback_templates_have_stable_source_keys(monkeypatch, tmp_path):
    missing_file = tmp_path / "missing_templates.json"
    monkeypatch.setattr(template_seeder, "TEMPLATE_DATA_FILE", missing_file)

    templates = template_seeder.load_default_templates()

    assert templates
    source_keys = [template["source_key"] for template in templates]
    assert all(source_keys)
    assert len(source_keys) == len(set(source_keys))


def test_template_seeder_matches_existing_builtin_by_source_key_when_name_changes():
    existing = AgentTemplate(name="Old Template Name", source_key="stable/template.md", is_builtin=True)
    source_key_index, legacy_name_index = template_seeder.build_builtin_template_indexes([existing])

    matched = template_seeder.resolve_existing_builtin_template(
        source_key_index,
        legacy_name_index,
        {"name": "Renamed Template", "source_key": "stable/template.md"},
    )

    assert matched is existing


def test_template_seeder_does_not_fallback_to_name_when_source_key_changes():
    existing = AgentTemplate(name="Project Manager", source_key="fallback/project-manager.md", is_builtin=True)
    source_key_index, legacy_name_index = template_seeder.build_builtin_template_indexes([existing])

    matched = template_seeder.resolve_existing_builtin_template(
        source_key_index,
        legacy_name_index,
        {"name": "Project Manager", "source_key": "fallback/renamed-project-manager.md"},
    )

    assert matched is None


def test_virtual_department_slug_is_unique_per_tenant(session: Session):
    tenant_a = _create_tenant(session)
    tenant_b = _create_tenant(session)

    _create_department(session, tenant_a.id, "engineering")
    _create_department(session, tenant_b.id, "engineering")

    with pytest.raises(IntegrityError):
        _create_department(session, tenant_a.id, "engineering")


def test_agent_has_only_one_primary_assignment(session: Session):
    tenant = _create_tenant(session)
    user = _create_user(session, tenant.id)
    template = _create_template(session)
    agent = _create_agent(session, tenant.id, user.id, template.id)
    department_a = _create_department(session, tenant.id, "engineering")
    department_b = _create_department(session, tenant.id, "design")

    session.add(
        AgentVirtualOrg(
            agent_id=agent.id,
            department_id=department_a.id,
            template_id=template.id,
            title="Lead",
            level="L2",
            org_bucket="core",
            is_primary=True,
            is_org_primary_instance=True,
            tenant_id=tenant.id,
        )
    )
    session.commit()

    session.add(
        AgentVirtualOrg(
            agent_id=agent.id,
            department_id=department_b.id,
            template_id=template.id,
            title="Advisor",
            level="L3",
            org_bucket="expert",
            is_primary=True,
            tenant_id=tenant.id,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_virtual_org_constraints_reject_duplicate_primary_instance(session: Session):
    tenant = _create_tenant(session)
    user = _create_user(session, tenant.id)
    template = _create_template(session)
    department = _create_department(session, tenant.id, "executive")
    agent_a = _create_agent(session, tenant.id, user.id, template.id)
    agent_b = _create_agent(session, tenant.id, user.id, template.id)

    session.add(
        AgentVirtualOrg(
            agent_id=agent_a.id,
            department_id=department.id,
            template_id=template.id,
            title="Primary",
            level="L1",
            org_bucket="core",
            is_primary=True,
            is_org_primary_instance=True,
            tenant_id=tenant.id,
        )
    )
    session.commit()

    session.add(
        AgentVirtualOrg(
            agent_id=agent_b.id,
            department_id=department.id,
            template_id=template.id,
            title="Duplicate",
            level="L1",
            org_bucket="core",
            is_primary=True,
            is_org_primary_instance=True,
            tenant_id=tenant.id,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_virtual_department_cannot_be_deleted_while_assignment_exists(session: Session):
    tenant = _create_tenant(session)
    user = _create_user(session, tenant.id)
    template = _create_template(session)
    department = _create_department(session, tenant.id, "operations")
    agent = _create_agent(session, tenant.id, user.id, template.id)

    session.add(
        AgentVirtualOrg(
            agent_id=agent.id,
            department_id=department.id,
            template_id=template.id,
            title="Operator",
            level="L3",
            org_bucket="core",
            is_primary=True,
            tenant_id=tenant.id,
        )
    )
    session.commit()

    session.delete(department)

    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [("level", "L6"), ("org_bucket", "shared")],
)
def test_virtual_org_rejects_invalid_enums(session: Session, field_name: str, field_value: str):
    tenant = _create_tenant(session)
    user = _create_user(session, tenant.id)
    template = _create_template(session)
    department = _create_department(session, tenant.id, "finance")
    agent = _create_agent(session, tenant.id, user.id, template.id)

    payload = {
        "agent_id": agent.id,
        "department_id": department.id,
        "template_id": template.id,
        "title": "Finance Partner",
        "level": "L3",
        "org_bucket": "core",
        "is_primary": True,
        "tenant_id": tenant.id,
    }
    payload[field_name] = field_value

    session.add(AgentVirtualOrg(**payload))

    with pytest.raises(IntegrityError):
        session.commit()


def test_virtual_org_rejects_cross_tenant_department_assignment(session: Session):
    tenant_a = _create_tenant(session)
    tenant_b = _create_tenant(session)
    user = _create_user(session, tenant_a.id)
    template = _create_template(session)
    agent = _create_agent(session, tenant_a.id, user.id, template.id)
    foreign_department = _create_department(session, tenant_b.id, "foreign")

    session.add(
        AgentVirtualOrg(
            agent_id=agent.id,
            department_id=foreign_department.id,
            template_id=template.id,
            title="Cross Tenant",
            level="L3",
            org_bucket="core",
            is_primary=True,
            tenant_id=tenant_a.id,
        )
    )

    with pytest.raises(ValueError, match="tenant"):
        session.commit()


def test_bootstrap_creates_departments_and_assignments_idempotently(session: Session):
    tenant = _create_tenant(session)
    _create_user(session, tenant.id)
    seed_rows = _sample_seed_rows()
    _create_templates_for_seed_rows(session, seed_rows)

    result1 = bootstrap_virtual_org(session, tenant.id, seed_rows=seed_rows)
    result2 = bootstrap_virtual_org(session, tenant.id, seed_rows=seed_rows)

    assert result1.created_departments > 0
    assert result1.created_primary_agents == len(seed_rows)
    assert result2.created_departments == 0
    assert result2.created_primary_agents == 0
    assert session.query(AgentVirtualOrg).filter(AgentVirtualOrg.tenant_id == tenant.id).count() == len(seed_rows)
    assert session.query(AgentVirtualTag).filter(AgentVirtualTag.tenant_id == tenant.id).count() == 7


def test_bootstrap_does_not_override_locked_assignment(session: Session):
    tenant = _create_tenant(session)
    user = _create_user(session, tenant.id)
    custom_department = _create_department(session, tenant.id, "custom-dept")
    seed_rows = _sample_seed_rows()[:1]
    templates = _create_templates_for_seed_rows(session, seed_rows)
    agent = _create_agent(
        session,
        tenant.id,
        user.id,
        templates["support/support-executive-summary-generator.md"].id,
        name="已有高管摘要师",
    )

    session.add(
        AgentVirtualOrg(
            agent_id=agent.id,
            department_id=custom_department.id,
            template_id=agent.template_id,
            title="自定义岗位",
            level="L3",
            org_bucket="expert",
            is_primary=True,
            is_org_primary_instance=True,
            tenant_id=tenant.id,
            is_locked=True,
        )
    )
    session.commit()

    bootstrap_virtual_org(session, tenant.id, seed_rows=seed_rows)

    locked_assignment = session.query(AgentVirtualOrg).filter(AgentVirtualOrg.agent_id == agent.id).one()
    assert locked_assignment.department_id == custom_department.id
    assert locked_assignment.title == "自定义岗位"
    assert locked_assignment.org_bucket == "expert"


def test_bootstrap_rejects_manager_cycle(session: Session):
    tenant = _create_tenant(session)
    _create_user(session, tenant.id)
    cycle_rows = [
        {
            "source_key": "product/product-manager.md",
            "template_name": "产品经理",
            "agent_name": "产品经理",
            "department_slug": "product",
            "title": "产品负责人",
            "level": "L2",
            "org_bucket": "core",
            "manager_source_key": "engineering/engineering-frontend-developer.md",
            "tags": ["core-org"],
        },
        {
            "source_key": "engineering/engineering-frontend-developer.md",
            "template_name": "前端开发者",
            "agent_name": "前端开发者",
            "department_slug": "engineering",
            "title": "前端负责人",
            "level": "L3",
            "org_bucket": "core",
            "manager_source_key": "product/product-manager.md",
            "tags": ["core-org"],
        },
    ]
    _create_templates_for_seed_rows(session, cycle_rows)

    with pytest.raises(ValueError, match="cycle"):
        bootstrap_virtual_org(session, tenant.id, seed_rows=cycle_rows)


def test_bootstrap_rolls_back_partial_writes_on_failure(session: Session):
    tenant = _create_tenant(session)
    _create_user(session, tenant.id)
    cycle_rows = [
        {
            "source_key": "product/product-manager.md",
            "template_name": "产品经理",
            "agent_name": "产品经理",
            "department_slug": "product",
            "title": "产品负责人",
            "level": "L2",
            "org_bucket": "core",
            "manager_source_key": "engineering/engineering-frontend-developer.md",
            "tags": ["core-org"],
        },
        {
            "source_key": "engineering/engineering-frontend-developer.md",
            "template_name": "前端开发者",
            "agent_name": "前端开发者",
            "department_slug": "engineering",
            "title": "前端负责人",
            "level": "L3",
            "org_bucket": "core",
            "manager_source_key": "product/product-manager.md",
            "tags": ["core-org"],
        },
    ]
    _create_templates_for_seed_rows(session, cycle_rows)
    agent_count_before = session.query(Agent).filter(Agent.tenant_id == tenant.id).count()

    with pytest.raises(ValueError, match="cycle"):
        bootstrap_virtual_org(session, tenant.id, seed_rows=cycle_rows)

    assert session.query(VirtualDepartment).filter(VirtualDepartment.tenant_id == tenant.id).count() == 0
    assert session.query(AgentVirtualOrg).filter(AgentVirtualOrg.tenant_id == tenant.id).count() == 0
    assert session.query(AgentVirtualTag).filter(AgentVirtualTag.tenant_id == tenant.id).count() == 0
    assert session.query(Agent).filter(Agent.tenant_id == tenant.id).count() == agent_count_before


def test_bootstrap_creates_org_primary_agent_when_missing(session: Session):
    tenant = _create_tenant(session)
    _create_user(session, tenant.id)
    seed_rows = _sample_seed_rows()[:2]
    templates = _create_templates_for_seed_rows(session, seed_rows)

    bootstrap_virtual_org(session, tenant.id, seed_rows=seed_rows)

    created_agents = session.query(Agent).filter(Agent.tenant_id == tenant.id).all()
    assert len(created_agents) == len(seed_rows)
    primary_assignments = session.query(AgentVirtualOrg).filter(AgentVirtualOrg.tenant_id == tenant.id).all()
    assert len(primary_assignments) == len(seed_rows)
    assert all(assignment.is_org_primary_instance for assignment in primary_assignments)
    assert {assignment.template_id for assignment in primary_assignments} == {template.id for template in templates.values()}


def test_bootstrap_creates_real_primary_instance_when_only_locked_non_primary_agent_exists(session: Session):
    tenant = _create_tenant(session)
    user = _create_user(session, tenant.id)
    seed_rows = _sample_seed_rows()[:1]
    template = _create_templates_for_seed_rows(session, seed_rows)["support/support-executive-summary-generator.md"]
    custom_department = _create_department(session, tenant.id, "custom-dept")
    locked_agent = _create_agent(session, tenant.id, user.id, template.id, name="已有高管摘要师")
    session.add(
        AgentVirtualOrg(
            agent_id=locked_agent.id,
            department_id=custom_department.id,
            template_id=template.id,
            title="手工岗位",
            level="L3",
            org_bucket="expert",
            is_primary=True,
            is_org_primary_instance=False,
            tenant_id=tenant.id,
            is_locked=True,
        )
    )
    session.commit()

    result = bootstrap_virtual_org(session, tenant.id, seed_rows=seed_rows)

    assert result.created_primary_agents == 1
    primary_assignments = session.query(AgentVirtualOrg).filter(
        AgentVirtualOrg.tenant_id == tenant.id,
        AgentVirtualOrg.template_id == template.id,
        AgentVirtualOrg.is_org_primary_instance.is_(True),
    ).all()
    assert len(primary_assignments) == 1
    assert primary_assignments[0].agent_id != locked_agent.id


def test_bootstrap_does_not_duplicate_org_primary_agent_on_rerun(session: Session):
    tenant = _create_tenant(session)
    _create_user(session, tenant.id)
    seed_rows = _sample_seed_rows()[:1]
    template = _create_templates_for_seed_rows(session, seed_rows)["support/support-executive-summary-generator.md"]

    bootstrap_virtual_org(session, tenant.id, seed_rows=seed_rows)
    bootstrap_virtual_org(session, tenant.id, seed_rows=seed_rows)

    assert session.query(Agent).filter(Agent.tenant_id == tenant.id, Agent.template_id == template.id).count() == 1
    assert session.query(AgentVirtualOrg).filter(
        AgentVirtualOrg.tenant_id == tenant.id,
        AgentVirtualOrg.template_id == template.id,
        AgentVirtualOrg.is_org_primary_instance.is_(True),
    ).count() == 1


def test_bootstrap_assigns_unmatched_existing_agents_to_expert_unassigned(session: Session):
    tenant = _create_tenant(session)
    user = _create_user(session, tenant.id)
    seed_rows = _sample_seed_rows()[:1]
    _create_templates_for_seed_rows(session, seed_rows)
    unmatched_template = _create_template(session, name="未分类专家", source_key="expert/unmatched.md")
    unmatched_agent = _create_agent(
        session,
        tenant.id,
        user.id,
        unmatched_template.id,
        name="未分类专家实例",
    )

    bootstrap_virtual_org(session, tenant.id, seed_rows=seed_rows)

    assignment = session.query(AgentVirtualOrg).filter(AgentVirtualOrg.agent_id == unmatched_agent.id).one()
    department = session.get(VirtualDepartment, assignment.department_id)
    tags = {
        row.tag
        for row in session.query(AgentVirtualTag).filter(
            AgentVirtualTag.tenant_id == tenant.id,
            AgentVirtualTag.agent_id == unmatched_agent.id,
        )
    }
    assert department is not None
    assert department.slug == "expert-unassigned"
    assert assignment.org_bucket == "expert"
    assert assignment.level == "L5"
    assert "expert-pool" in tags


def test_bootstrap_matches_template_by_agent_name_when_source_key_and_template_name_miss(session: Session):
    tenant = _create_tenant(session)
    user = _create_user(session, tenant.id)
    template = _create_template(session, name="产品策略负责人", source_key="renamed/product-manager.md")
    agent = _create_agent(session, tenant.id, user.id, template.id, name="产品经理")
    seed_rows = [
        {
            "source_key": "product/product-manager.md",
            "template_name": "产品经理",
            "agent_name": "产品经理",
            "department_slug": "product",
            "title": "产品负责人",
            "level": "L2",
            "org_bucket": "core",
            "manager_source_key": None,
            "tags": ["core-org"],
        }
    ]

    bootstrap_virtual_org(session, tenant.id, seed_rows=seed_rows)

    assignment = session.query(AgentVirtualOrg).filter(AgentVirtualOrg.agent_id == agent.id).one()
    assert assignment.template_id == template.id
    assert assignment.is_org_primary_instance is True


def test_bootstrap_does_not_reuse_same_name_agent_with_wrong_template(session: Session):
    tenant = _create_tenant(session)
    user = _create_user(session, tenant.id)
    wrong_template = _create_template(session, name="别的模板", source_key="other/template.md")
    right_template = _create_template(session, name="产品经理", source_key="renamed/product-manager.md")
    wrong_agent = _create_agent(session, tenant.id, user.id, wrong_template.id, name="产品经理")
    seed_rows = [
        {
            "source_key": "product/product-manager.md",
            "template_name": "产品经理",
            "agent_name": "产品经理",
            "department_slug": "product",
            "title": "产品负责人",
            "level": "L2",
            "org_bucket": "core",
            "manager_source_key": None,
            "tags": ["core-org"],
        }
    ]

    bootstrap_virtual_org(session, tenant.id, seed_rows=seed_rows)

    org_primary_assignments = session.query(AgentVirtualOrg).filter(
        AgentVirtualOrg.tenant_id == tenant.id,
        AgentVirtualOrg.is_org_primary_instance.is_(True),
    ).all()
    assert len(org_primary_assignments) == 1
    assert org_primary_assignments[0].template_id == right_template.id
    assert org_primary_assignments[0].agent_id != wrong_agent.id

    wrong_agent_assignment = session.query(AgentVirtualOrg).filter(AgentVirtualOrg.agent_id == wrong_agent.id).one()
    wrong_department = session.get(VirtualDepartment, wrong_agent_assignment.department_id)
    assert wrong_department is not None
    assert wrong_department.slug == "expert-unassigned"


def test_assign_manager_rejects_self_reference(session: Session):
    tenant = _create_tenant(session)
    user = _create_user(session, tenant.id)
    template = _create_template(session)
    department = _create_department(session, tenant.id, "operations")
    agent = _create_agent(session, tenant.id, user.id, template.id)
    assignment = AgentVirtualOrg(
        agent_id=agent.id,
        department_id=department.id,
        template_id=template.id,
        title="Operator",
        level="L3",
        org_bucket="core",
        is_primary=True,
        tenant_id=tenant.id,
    )
    session.add(assignment)
    session.commit()

    with pytest.raises(ValueError, match="self|cycle"):
        assign_manager(session, tenant.id, {agent.id: assignment}, agent_id=agent.id, manager_agent_id=agent.id)


def test_assign_manager_rejects_cross_tenant_manager(session: Session):
    tenant_a = _create_tenant(session)
    tenant_b = _create_tenant(session)
    user_a = _create_user(session, tenant_a.id)
    user_b = _create_user(session, tenant_b.id)
    template_a = _create_template(session, name="A模板", source_key="a.md")
    template_b = _create_template(session, name="B模板", source_key="b.md")
    department_a = _create_department(session, tenant_a.id, "operations")
    department_b = _create_department(session, tenant_b.id, "operations")
    agent_a = _create_agent(session, tenant_a.id, user_a.id, template_a.id, name="Agent A")
    agent_b = _create_agent(session, tenant_b.id, user_b.id, template_b.id, name="Agent B")
    assignment_a = AgentVirtualOrg(
        agent_id=agent_a.id,
        department_id=department_a.id,
        template_id=template_a.id,
        title="Operator A",
        level="L3",
        org_bucket="core",
        is_primary=True,
        tenant_id=tenant_a.id,
    )
    assignment_b = AgentVirtualOrg(
        agent_id=agent_b.id,
        department_id=department_b.id,
        template_id=template_b.id,
        title="Operator B",
        level="L3",
        org_bucket="core",
        is_primary=True,
        tenant_id=tenant_b.id,
    )
    session.add_all([assignment_a, assignment_b])
    session.commit()

    with pytest.raises(ValueError, match="tenant"):
        assign_manager(
            session,
            tenant_a.id,
            {agent_a.id: assignment_a, agent_b.id: assignment_b},
            agent_id=agent_a.id,
            manager_agent_id=agent_b.id,
        )


def test_virtual_department_rejects_cross_tenant_parent(session: Session):
    tenant_a = _create_tenant(session)
    tenant_b = _create_tenant(session)
    parent = _create_department(session, tenant_a.id, "parent")

    child = VirtualDepartment(name="Child", slug="child", tenant_id=tenant_b.id, parent_id=parent.id)
    session.add(child)

    with pytest.raises(ValueError, match="tenant"):
        session.commit()


def test_virtual_tag_rejects_cross_tenant_agent_reference(session: Session):
    tenant_a = _create_tenant(session)
    tenant_b = _create_tenant(session)
    user = _create_user(session, tenant_a.id)
    template = _create_template(session)
    agent = _create_agent(session, tenant_a.id, user.id, template.id)

    session.add(AgentVirtualTag(agent_id=agent.id, tenant_id=tenant_b.id, tag="expert-pool"))

    with pytest.raises(ValueError, match="tenant"):
        session.commit()
