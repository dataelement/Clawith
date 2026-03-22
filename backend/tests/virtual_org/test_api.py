import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, get_current_user
from app.database import Base, get_db
from app.models.agent import Agent, AgentPermission, AgentTemplate
from app.models.llm import LLMModel
from app.models.tenant import Tenant
from app.models.user import Department, User
from app.models.virtual_org import AgentVirtualOrg, AgentVirtualTag, VirtualDepartment


def _sqlite_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
        AgentPermission.__table__,
        VirtualDepartment.__table__,
        AgentVirtualOrg.__table__,
        AgentVirtualTag.__table__,
    ]


class AsyncSessionWrapper:
    def __init__(self, session: Session):
        self._session = session

    def add(self, obj):
        self._session.add(obj)

    async def execute(self, *args, **kwargs):
        return self._session.execute(*args, **kwargs)

    async def flush(self):
        self._session.flush()

    async def commit(self):
        self._session.commit()

    async def rollback(self):
        self._session.rollback()

    async def delete(self, obj):
        self._session.delete(obj)

    async def get(self, model, ident):
        return self._session.get(model, ident)

    async def run_sync(self, fn, *args, **kwargs):
        return fn(self._session, *args, **kwargs)


def _create_tenant(session: Session, slug: str = "tenant") -> Tenant:
    tenant = Tenant(name=slug.title(), slug=f"{slug}-{uuid.uuid4().hex[:6]}")
    session.add(tenant)
    session.commit()
    return tenant


def _create_user(session: Session, tenant_id: uuid.UUID, role: str, username: str) -> User:
    user = User(
        username=f"{username}-{uuid.uuid4().hex[:4]}",
        email=f"{username}-{uuid.uuid4().hex[:4]}@example.com",
        password_hash="hashed",
        display_name=username,
        tenant_id=tenant_id,
        role=role,
    )
    session.add(user)
    session.commit()
    return user


def _create_template(session: Session, name: str, source_key: str) -> AgentTemplate:
    template = AgentTemplate(
        name=name,
        description=name,
        icon="T",
        category="test",
        soul_template="",
        default_skills=[],
        default_autonomy_policy={},
        is_builtin=True,
        source_key=source_key,
    )
    session.add(template)
    session.commit()
    return template


def _create_agent(session: Session, tenant_id: uuid.UUID, creator_id: uuid.UUID, template_id: uuid.UUID, name: str) -> Agent:
    agent = Agent(
        name=name,
        role_description=name,
        creator_id=creator_id,
        tenant_id=tenant_id,
        template_id=template_id,
        status="idle",
    )
    session.add(agent)
    session.commit()
    return agent


def _create_virtual_department(session: Session, tenant_id: uuid.UUID, slug: str, name: str, sort_order: int = 0) -> VirtualDepartment:
    department = VirtualDepartment(
        name=name,
        slug=slug,
        tenant_id=tenant_id,
        sort_order=sort_order,
        org_level="department",
        is_core=slug not in {"expert-pool", "expert-unassigned"},
    )
    session.add(department)
    session.commit()
    return department


def _create_assignment(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    department_id: uuid.UUID,
    template_id: uuid.UUID,
    title: str,
    level: str,
    org_bucket: str,
) -> AgentVirtualOrg:
    assignment = AgentVirtualOrg(
        agent_id=agent_id,
        department_id=department_id,
        template_id=template_id,
        title=title,
        level=level,
        org_bucket=org_bucket,
        is_primary=True,
        is_org_primary_instance=True,
        tenant_id=tenant_id,
    )
    session.add(assignment)
    session.commit()
    return assignment


def _grant_company_access(session: Session, agent_id: uuid.UUID):
    session.add(AgentPermission(agent_id=agent_id, scope_type="company", access_level="use"))
    session.commit()


def _grant_user_access(session: Session, agent_id: uuid.UUID, user_id: uuid.UUID, access_level: str = "use"):
    session.add(AgentPermission(agent_id=agent_id, scope_type="user", scope_id=user_id, access_level=access_level))
    session.commit()


@pytest.fixture()
def api_env(monkeypatch):
    from app.api.agents import router as agents_router
    from app.api.virtual_org import router as virtual_org_router

    engine = _sqlite_engine()
    Base.metadata.create_all(engine, tables=_schema_tables())
    session = Session(engine)

    app = FastAPI()
    app.include_router(agents_router, prefix="/api")
    app.include_router(virtual_org_router, prefix="/api")

    async def override_get_db():
        db = AsyncSessionWrapper(session)
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)

    yield {"session": session, "client": client}

    client.close()
    session.close()
    engine.dispose()


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id), user.role)
    return {"Authorization": f"Bearer {token}"}


def test_virtual_org_overview_returns_expected_sections(api_env):
    session = api_env["session"]
    client = api_env["client"]

    tenant = _create_tenant(session, "overview")
    admin = _create_user(session, tenant.id, "platform_admin", "admin")
    template = _create_template(session, "高管摘要师", "support/support-executive-summary-generator.md")
    executive = _create_virtual_department(session, tenant.id, "executive", "高管层")
    expert_pool = _create_virtual_department(session, tenant.id, "expert-pool", "专家库")
    agent = _create_agent(session, tenant.id, admin.id, template.id, "高管摘要师")
    _create_assignment(
        session,
        tenant_id=tenant.id,
        agent_id=agent.id,
        department_id=executive.id,
        template_id=template.id,
        title="CEO办公室战略参谋",
        level="L1",
        org_bucket="core",
    )
    session.add(AgentVirtualTag(agent_id=agent.id, tenant_id=tenant.id, tag="executive"))
    session.commit()

    response = client.get("/api/virtual-org/overview", headers=_auth_header(admin))

    assert response.status_code == 200
    data = response.json()
    assert "executives" in data
    assert "departments" in data
    assert "expert_pool" in data


def test_virtual_org_overview_filters_to_visible_agents_for_member(api_env):
    session = api_env["session"]
    client = api_env["client"]

    tenant = _create_tenant(session, "member")
    admin = _create_user(session, tenant.id, "platform_admin", "admin")
    member = _create_user(session, tenant.id, "member", "member")
    visible_template = _create_template(session, "产品经理", "product/product-manager.md")
    hidden_template = _create_template(session, "隐藏模板", "product/hidden-product-manager.md")
    department = _create_virtual_department(session, tenant.id, "product", "产品部")
    visible_agent = _create_agent(session, tenant.id, admin.id, visible_template.id, "产品经理")
    hidden_agent = _create_agent(session, tenant.id, admin.id, hidden_template.id, "隐藏角色")
    _create_assignment(session, tenant_id=tenant.id, agent_id=visible_agent.id, department_id=department.id, template_id=visible_template.id, title="产品负责人", level="L2", org_bucket="core")
    _create_assignment(session, tenant_id=tenant.id, agent_id=hidden_agent.id, department_id=department.id, template_id=hidden_template.id, title="隐藏岗位", level="L3", org_bucket="core")
    _grant_user_access(session, visible_agent.id, member.id)

    response = client.get("/api/virtual-org/overview", headers=_auth_header(member))

    assert response.status_code == 200
    body = response.json()
    agent_names = {agent["name"] for dept in body["departments"] for agent in dept["core_agents"]}
    assert "产品经理" in agent_names
    assert "隐藏角色" not in agent_names


def test_virtual_org_agents_endpoint_supports_department_filter_and_pagination(api_env):
    session = api_env["session"]
    client = api_env["client"]

    tenant = _create_tenant(session, "list")
    admin = _create_user(session, tenant.id, "platform_admin", "admin")
    template_a = _create_template(session, "前端开发者A模板", "engineering/engineering-frontend-developer-a.md")
    template_b = _create_template(session, "前端开发者B模板", "engineering/engineering-frontend-developer-b.md")
    template_c = _create_template(session, "设计师C模板", "design/design-ui-designer-c.md")
    engineering = _create_virtual_department(session, tenant.id, "engineering", "研发部")
    design = _create_virtual_department(session, tenant.id, "design", "设计部")
    agent_a = _create_agent(session, tenant.id, admin.id, template_a.id, "前端开发者A")
    agent_b = _create_agent(session, tenant.id, admin.id, template_b.id, "前端开发者B")
    agent_c = _create_agent(session, tenant.id, admin.id, template_c.id, "设计师C")
    _create_assignment(session, tenant_id=tenant.id, agent_id=agent_a.id, department_id=engineering.id, template_id=template_a.id, title="前端一", level="L3", org_bucket="core")
    _create_assignment(session, tenant_id=tenant.id, agent_id=agent_b.id, department_id=engineering.id, template_id=template_b.id, title="前端二", level="L3", org_bucket="core")
    _create_assignment(session, tenant_id=tenant.id, agent_id=agent_c.id, department_id=design.id, template_id=template_c.id, title="设计三", level="L4", org_bucket="expert")

    response = client.get(
        f"/api/virtual-org/agents?department_id={engineering.id}&org_bucket=core&page=1&page_size=1",
        headers=_auth_header(admin),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 1


def test_virtual_org_patch_agent_updates_assignment_and_tags(api_env):
    session = api_env["session"]
    client = api_env["client"]

    tenant = _create_tenant(session, "patch")
    admin = _create_user(session, tenant.id, "platform_admin", "admin")
    template = _create_template(session, "法务合规员", "support/legal-compliance.md")
    dept_a = _create_virtual_department(session, tenant.id, "legal", "法务部")
    dept_b = _create_virtual_department(session, tenant.id, "expert-pool", "专家库")
    agent = _create_agent(session, tenant.id, admin.id, template.id, "法务合规员")
    _create_assignment(session, tenant_id=tenant.id, agent_id=agent.id, department_id=dept_a.id, template_id=template.id, title="法务负责人", level="L2", org_bucket="core")

    response = client.patch(
        f"/api/virtual-org/agents/{agent.id}",
        headers=_auth_header(admin),
        json={"department_id": str(dept_b.id), "level": "L5", "org_bucket": "expert", "tags": ["advisor", "expert-pool"]},
    )

    assert response.status_code == 200
    session.expire_all()
    assignment = session.query(AgentVirtualOrg).filter(AgentVirtualOrg.agent_id == agent.id).one()
    tags = {row.tag for row in session.query(AgentVirtualTag).filter(AgentVirtualTag.agent_id == agent.id)}
    assert assignment.department_id == dept_b.id
    assert assignment.level == "L5"
    assert assignment.org_bucket == "expert"
    assert tags == {"advisor", "expert-pool"}


def test_virtual_org_bootstrap_requires_admin(api_env, monkeypatch):
    session = api_env["session"]
    client = api_env["client"]

    tenant = _create_tenant(session, "bootstrap")
    admin = _create_user(session, tenant.id, "platform_admin", "admin")
    member = _create_user(session, tenant.id, "member", "member")

    def fake_bootstrap(sync_session, tenant_id, *, force=False, **_kwargs):
        return {"tenant_id": str(tenant_id), "force": force}

    monkeypatch.setattr("app.api.virtual_org.bootstrap_virtual_org", fake_bootstrap)

    forbidden = client.post("/api/virtual-org/bootstrap", headers=_auth_header(member), json={"force": False})
    allowed = client.post("/api/virtual-org/bootstrap", headers=_auth_header(admin), json={"force": True})

    assert forbidden.status_code == 403
    assert allowed.status_code == 200


def test_agent_detail_includes_virtual_org_payload(api_env):
    session = api_env["session"]
    client = api_env["client"]

    tenant = _create_tenant(session, "detail")
    admin = _create_user(session, tenant.id, "platform_admin", "admin")
    template = _create_template(session, "后端架构师", "engineering/backend-architect.md")
    department = _create_virtual_department(session, tenant.id, "engineering", "研发部")
    agent = _create_agent(session, tenant.id, admin.id, template.id, "后端架构师")
    _create_assignment(session, tenant_id=tenant.id, agent_id=agent.id, department_id=department.id, template_id=template.id, title="后端负责人", level="L2", org_bucket="core")
    session.add_all([
        AgentVirtualTag(agent_id=agent.id, tenant_id=tenant.id, tag="core-org"),
        AgentVirtualTag(agent_id=agent.id, tenant_id=tenant.id, tag="backend"),
    ])
    session.commit()

    response = client.get(f"/api/agents/{agent.id}", headers=_auth_header(admin))

    assert response.status_code == 200
    body = response.json()
    assert "virtual_org" in body
    assert body["virtual_org"]["department_name"] == "研发部"
    assert body["virtual_org"]["level"] == "L2"
    assert set(body["virtual_org"]["tags"]) == {"core-org", "backend"}
