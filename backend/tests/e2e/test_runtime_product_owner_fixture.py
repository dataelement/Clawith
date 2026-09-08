"""Real application/Runner/Provider-adapter/Tool/store path; the product owner is a fixture."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401
from sqlalchemy import text

from app import application
from app.execution_dependencies import resources as composition
from app.execution_dependencies.provisioning import provision_builtin_tools
from app.execution_dependencies.runtime import capture_snapshot
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.agent.public import AgentService
from app.modules.credential.public import Secret
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.model.public import ModelHardLimits, ModelService
from app.modules.run.public import InputContent, RunService, SourceIdentity
from app.modules.tool.public import ToolResolutionScope
from app.modules.workspace.public import WorkspaceSubject


class ProductOwnerFixture:
    """Owns only its fixture output table; receives the same settlement transaction."""

    async def record_outcome(self, transaction, *, run, outcome):
        await transaction.session.execute(text(
            "INSERT INTO fixture_product_outcomes (run_id, status, output) VALUES (:run_id, :status, :output)"
        ), {"run_id": run.id, "status": outcome.status, "output": outcome.output})


async def configure_agent(execution, sessions):
    async with transaction(sessions) as tx:
        identity = IdentityService(tx)
        account = await identity.create_account()
        tenant = await identity.create_tenant(name="Runtime fixture")
        member = await identity.create_membership(tenant_id=tenant.id, account_id=account.id,
            display_name="Owner", role="tenant_admin")
        principal = TenantPrincipal(account.id, member.id, tenant.id, "tenant_admin")
        credential = await execution.credentials(tx).create(principal, kind="api_key", provider="fixture",
            label="Model", secret=Secret("fixture-provider-secret"), owner_kind="tenant")
        model = await ModelService(tx).create(principal, credential_id=credential.id, provider="fixture",
            model_name="fixture", endpoint="https://provider.invalid/v1", context_limit=65536, output_limit=2048,
            capability_source="administrator", capabilities={"supports_tool_calling": True},
            settings_version=1, settings={"protocol": "openai_chat"}, enabled=False)
    accepted = await execution.model.validate_configuration(tenant_id=tenant.id, credential_id=credential.id,
        provider=model.provider, protocol="openai_chat", model_name=model.model_name, endpoint=model.endpoint,
        administrator_limits=ModelHardLimits(65536, 2048), settings=model.settings, capabilities=model.capabilities)
    async with transaction(sessions) as tx:
        await ModelService(tx).set_enabled(principal, model_id=model.id, enabled=True, acceptance=accepted)
        agent = await AgentService(tx).create(principal, name="Writer", soul="Help with careful work.",
            timezone="UTC", model_id=model.id)
        await provision_builtin_tools(tx, principal, agent_id=agent.id)
    resolved = await execution.model.resolve_policy(tenant_id=tenant.id, model_id=model.id, protocol="openai_chat")
    return principal, agent, resolved


async def eventually(sessions, tenant_id, run_id, status):
    async with asyncio.timeout(10):
        while True:
            async with transaction(sessions) as tx:
                view = await RunService(tx).get(tenant_id=tenant_id, run_id=run_id)
            if view.status == status:
                return view
            if view.status in ("Failed", "Cancelled", "Interrupted"):
                pytest.fail(f"Run unexpectedly ended as {view.status}")
            await asyncio.sleep(0.01)


async def test_application_runtime_writes_workspace_and_commits_owner_output(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811 — imported Pytest fixture.
    observed = []

    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
                "tool_calls": [{"id": "probe", "function": {"name": "capability_probe", "arguments": '{"value":"ok"}'}}]}}]})
        observed.append(body)
        if not any(message["role"] == "tool" for message in body["messages"]):
            message = {"content": "Writing the report.", "tool_calls": [{"id": "write-report", "function": {
                "name": "write_file", "arguments": json.dumps({"workspace": "current", "path": "files/report.md",
                    "content": "report result", "expected_revision": None})}}]}
            reason = "tool_calls"
        else:
            message, reason = {"content": "Report written."}, "stop"
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": reason}]})

    monkeypatch.setattr(composition, "create_stateless_http_client",
        lambda **kwargs: create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    async with test_database.sessions.begin() as session:
        await session.execute(text("CREATE TABLE fixture_product_outcomes (run_id uuid PRIMARY KEY, status text, output text)"))
    app = application.create_app(configured(tmp_path), outcome_consumer=ProductOwnerFixture())
    async with app.router.lifespan_context(app):
        execution, runtime = app.state.execution, app.state.runtime
        principal, agent, model = await configure_agent(execution, test_database.sessions)
        scope = await execution.workspace.direct_scope(principal, agent_id=agent.id, run_id=uuid4())
        await execution.workspace.ensure(scope, scope.output)
        await execution.workspace.ensure(scope, WorkspaceSubject("agent", agent.id))
        snapshot = await capture_snapshot(execution, app.state.database, agent=agent, model=model, workspace=scope,
            tools=ToolResolutionScope(principal, agent.id, "main"))
        source = SourceIdentity("product_fixture", principal.membership_id, "request-1")
        started = await runtime.start(snapshot=snapshot, input=InputContent("Write my report."), source=source)
        assert started.created
        assert not (await runtime.start(snapshot=snapshot, input=InputContent("duplicate"), source=source)).created
        await eventually(test_database.sessions, principal.tenant_id, started.run.id, "Completed")
        assert (await execution.workspace.read(scope, scope.output, "files/report.md")).content == b"report result"
        async with transaction(test_database.sessions) as tx:
            output = (await tx.session.execute(text("SELECT status, output FROM fixture_product_outcomes"))).one()
            page = await RunService(tx).read_history(tenant_id=principal.tenant_id, run_id=started.run.id)
        assert tuple(output) == ("Completed", "Report written.")
        assert len(page.entries) == 7  # initial, two inputs/two results, Tool result, terminal.
        assert len(observed) == 2 and observed[0]["messages"][0] == observed[1]["messages"][0]
        assert '"status": "error"' not in json.dumps(observed[1]["messages"])
        assert "fixture-provider-secret" not in json.dumps(observed)
        assert runtime.dispatcher.admitted == 0
    assert runtime.dispatcher.active == 0 and not hasattr(app.state, "runtime")
