"""Explicit Builtin provisioning within the caller's Agent-creation transaction."""

from uuid import UUID

from app.execution_dependencies.a2a_tools import A2A_DEFINITION
from app.execution_dependencies.attachment_tools import READ_ATTACHMENT_DEFINITION, SAVE_ATTACHMENT_DEFINITION
from app.execution_dependencies.document_tools import READ_DOCUMENT_DEFINITION
from app.execution_dependencies.message_tools import SEND_MESSAGE
from app.execution_dependencies.run_tools import RUN_TOOL_DEFINITIONS
from app.execution_dependencies.schedule_tools import SCHEDULE_TOOL_DEFINITIONS
from app.execution_dependencies.session_tools import SESSION_TOOL_DEFINITIONS
from app.execution_dependencies.temp_file_tools import TEMP_FILE_DEFINITION
from app.execution_dependencies.workspace_tools import WORKSPACE_DEFINITIONS
from app.infrastructure.transactions import TransactionContext
from app.modules.agent.public import AgentService
from app.modules.identity_tenant.public import TenantPrincipal, require_admin
from app.modules.tool.public import SEARCH_TOOLS_DEFINITION, ToolDefinition, ToolService

BUILTIN_DEFINITIONS = (SEARCH_TOOLS_DEFINITION, *WORKSPACE_DEFINITIONS, *RUN_TOOL_DEFINITIONS, SEND_MESSAGE, *SESSION_TOOL_DEFINITIONS, A2A_DEFINITION, *SCHEDULE_TOOL_DEFINITIONS, READ_ATTACHMENT_DEFINITION, SAVE_ATTACHMENT_DEFINITION, READ_DOCUMENT_DEFINITION, TEMP_FILE_DEFINITION)


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
