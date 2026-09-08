"""Explicit Builtin provisioning within the caller's Agent-creation transaction."""

from uuid import UUID

from app.execution_dependencies.run_tools import RUN_TOOL_DEFINITIONS
from app.execution_dependencies.workspace_tools import WORKSPACE_DEFINITIONS
from app.infrastructure.transactions import TransactionContext
from app.modules.agent.public import AgentService
from app.modules.identity_tenant.public import TenantPrincipal, require_admin
from app.modules.tool.public import SEARCH_TOOLS_DEFINITION, ToolDefinition, ToolService

BUILTIN_DEFINITIONS = (SEARCH_TOOLS_DEFINITION, *WORKSPACE_DEFINITIONS, *RUN_TOOL_DEFINITIONS)


async def provision_builtin_tools(
    transaction: TransactionContext, principal: TenantPrincipal, *, agent_id: UUID,
) -> tuple[ToolDefinition, ...]:
    """Register code-owned definitions and explicit grants; never run at application startup.

    The caller commits Agent creation and these grants together. Repeated provisioning
    preserves matching grants; a revoked or differently configured grant is a conflict,
    not permission to restore it.
    """
    require_admin(principal)
    await AgentService(transaction).get(principal, agent_id=agent_id)
    tools = ToolService(transaction)
    definitions = []
    for spec in BUILTIN_DEFINITIONS:
        definition = await tools.register_definition(principal, definition=spec)
        await tools.grant(principal, agent_id=agent_id, definition_id=definition.id)
        definitions.append(definition)
    return tuple(definitions)
