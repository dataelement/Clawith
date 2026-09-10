from __future__ import annotations

import ast
import re
import shlex
import subprocess
import symtable
import tomllib
from pathlib import Path

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONTEXT_MODULE = Path("app/services/agent_context.py")
CONTEXT_PACKAGE = Path("app/services/agent_context")
EXPERIENCE_IMPORT_IDENTITIES = (
    Path("app/api/experience"),
    Path("app/models/experience"),
    Path("app/models/experience_reference"),
    Path("app/services/experience_retrieval"),
)
EXPERIENCE_REINTRODUCTIONS = [
    (identity, representation)
    for identity in EXPERIENCE_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
MODEL_LLM_IMPORT_IDENTITIES = (
    Path("app/models/llm"),
    Path("app/services/llm"),
)
MODEL_LLM_REINTRODUCTIONS = [
    (identity, representation)
    for identity in MODEL_LLM_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
PERSISTENT_TASK_IMPORT_IDENTITIES = (
    Path("app/models/task"),
    Path("app/api/tasks"),
    Path("app/services/task_executor"),
)
PERSISTENT_TASK_REINTRODUCTIONS = [
    (identity, representation)
    for identity in PERSISTENT_TASK_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_TOOL_IMPORT_IDENTITIES = (
    Path("app/api/tools"),
    Path("app/models/tool"),
    Path("app/services/agent_tools"),
    Path("app/services/builtin_tool_definitions"),
    Path("app/services/tool_config"),
    Path("app/services/tool_exchange"),
    Path("app/services/tool_seeder"),
)
LEGACY_TOOL_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_TOOL_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_SKILL_IMPORT_IDENTITIES = (
    Path("app/api/skills"),
    Path("app/models/skill"),
    Path("app/services/skill_creator_content"),
    Path("app/services/skill_seeder"),
)
LEGACY_SKILL_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_SKILL_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_SKILL_CREATOR_FILES = Path("app/services/skill_creator_files")
OPENCLAW_GATEWAY_IMPORT_IDENTITIES = (
    Path("app/api/gateway"),
    Path("app/models/gateway_message"),
    Path("app/services/agent_manager"),
)
OPENCLAW_GATEWAY_REINTRODUCTIONS = [
    (identity, representation)
    for identity in OPENCLAW_GATEWAY_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_CREDENTIAL_IMPORT_IDENTITIES = (
    Path("app/api/agent_credentials"),
    Path("app/dao/agent_credential_dao"),
    Path("app/models/agent_credential"),
    Path("app/schemas/agent_credential"),
)
LEGACY_CREDENTIAL_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_CREDENTIAL_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_CREDENTIAL_DAO_EXPORT = "agent_credential_dao"
LEGACY_AGENT_IMPORT_IDENTITIES = (
    Path("app/models/agent"),
    Path("app/api/agents"),
    Path("app/dao/agent_dao"),
    Path("app/dao/agent_access_dao"),
    Path("app/services/agent_seeder"),
)
LEGACY_AGENT_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_AGENT_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_AGENT_DAO_EXPORTS = ("agent_dao", "agent_access_dao")
LEGACY_AGENT_RUN_EVENT_DAO_IMPORT_IDENTITY = Path("app/dao/agent_run_event_dao")
LEGACY_AGENT_RUN_EVENT_DAO_REINTRODUCTIONS = [
    (LEGACY_AGENT_RUN_EVENT_DAO_IMPORT_IDENTITY, representation)
    for representation in ("module", "package")
]
LEGACY_AGENT_RUN_EVENT_DAO_DOTTED_IMPORT_IDENTITY = (
    LEGACY_AGENT_RUN_EVENT_DAO_IMPORT_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_OKR_AGENT_HOOK_IMPORT_IDENTITY = Path("app/services/okr_agent_hook")
LEGACY_OKR_AGENT_HOOK_REINTRODUCTIONS = [
    (LEGACY_OKR_AGENT_HOOK_IMPORT_IDENTITY, representation)
    for representation in ("module", "package")
]
LEGACY_OKR_AGENT_HOOK_DOTTED_IMPORT_IDENTITY = (
    LEGACY_OKR_AGENT_HOOK_IMPORT_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_OKR_IMPORT_IDENTITIES = (
    Path("app/models/okr"),
    Path("app/api/okr"),
    Path("app/services/okr_daily_collection"),
    Path("app/services/okr_reporting"),
    Path("app/services/okr_scheduler"),
    Path("app/services/business_calendar"),
)
LEGACY_OKR_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_OKR_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_OKR_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_OKR_IMPORT_IDENTITIES
)
LEGACY_OKR_DEFINITION_ROOTS = (Path("app"),)
LEGACY_OKR_FORBIDDEN_DEFINITIONS = frozenset(
    {
        "class:OKRObjective",
        "class:OKRKeyResult",
        "class:OKRAlignment",
        "class:OKRProgressLog",
        "class:WorkReport",
        "class:MemberDailyReport",
        "class:CompanyReport",
        "class:OKRSettings",
        "table:okr_objectives",
        "table:okr_key_results",
        "table:okr_alignments",
        "table:okr_progress_logs",
        "table:work_reports",
        "table:member_daily_reports",
        "table:company_reports",
        "table:okr_settings",
    }
)
LEGACY_TOKEN_TRACKER_IMPORT_IDENTITY = Path("app/services/token_tracker")
LEGACY_TOKEN_TRACKER_REINTRODUCTIONS = [
    (LEGACY_TOKEN_TRACKER_IMPORT_IDENTITY, representation)
    for representation in ("module", "package")
]
LEGACY_TOKEN_TRACKER_DOTTED_IMPORT_IDENTITY = (
    LEGACY_TOKEN_TRACKER_IMPORT_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_WECOM_SERVICE_IMPORT_IDENTITY = Path("app/services/wecom_service")
LEGACY_WECOM_SERVICE_REINTRODUCTIONS = [
    (LEGACY_WECOM_SERVICE_IMPORT_IDENTITY, representation)
    for representation in ("module", "package")
]
LEGACY_WECOM_SERVICE_DOTTED_IMPORT_IDENTITY = (
    LEGACY_WECOM_SERVICE_IMPORT_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_IDENTITY_TENANT_IMPORT_IDENTITIES = (
    Path("app/models/user"),
    Path("app/models/tenant"),
    Path("app/models/tenant_setting"),
    Path("app/api/users"),
    Path("app/api/tenants"),
    Path("app/dao/identity_dao"),
    Path("app/dao/user_dao"),
    Path("app/dao/tenant_dao"),
)
LEGACY_IDENTITY_TENANT_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_IDENTITY_TENANT_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_IDENTITY_TENANT_DAO_EXPORTS = (
    "identity_dao",
    "user_dao",
    "tenant_dao",
)
LEGACY_AUTH_IMPORT_IDENTITIES = (
    Path("app/api/auth"),
    Path("app/services/auth_provider"),
    Path("app/services/auth_registry"),
    Path("app/services/registration_service"),
    Path("app/services/password_reset_service"),
    Path("app/services/email_verification_service"),
)
LEGACY_AUTH_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_AUTH_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_AUTH_PACKAGE_EXPORTS = {
    Path("app/api/__init__.py"): ("auth",),
    Path("app/services/__init__.py"): (
        "auth_provider",
        "auth_registry",
        "auth_provider_registry",
        "registration_service",
        "password_reset_service",
        "email_verification_service",
    ),
}
LEGACY_AUTH_DOTTED_IMPORT_IDENTITIES = tuple(
    dict.fromkeys(
        [
            identity.as_posix().replace("/", ".")
            for identity in LEGACY_AUTH_IMPORT_IDENTITIES
        ]
        + [
            f"{relative_path.parent.as_posix().replace('/', '.')}.{export}"
            for relative_path, exports in LEGACY_AUTH_PACKAGE_EXPORTS.items()
            for export in exports
        ]
    )
)
LEGACY_SSO_IMPORT_IDENTITIES = (
    Path("app/api/sso"),
    Path("app/api/google_workspace"),
    Path("app/models/identity"),
    Path("app/dao/identity_provider_dao"),
    Path("app/services/sso_service"),
    Path("app/services/sso_session_security"),
    Path("app/services/identity_provider_lookup"),
    Path("app/services/google_workspace_oauth"),
)
LEGACY_SSO_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_SSO_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_SSO_DAO_EXPORT = "identity_provider_dao"
LEGACY_SSO_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_SSO_IMPORT_IDENTITIES
)
LEGACY_ORGANIZATION_RELATIONSHIP_IMPORT_IDENTITIES = (
    Path("app/models/org"),
    Path("app/api/organization"),
    Path("app/api/relationships"),
    Path("app/dao/org_member_dao"),
    Path("app/services/org_sync_adapter"),
    Path("app/services/org_sync_service"),
    Path("app/services/access_relationships"),
)
LEGACY_ORGANIZATION_RELATIONSHIP_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_ORGANIZATION_RELATIONSHIP_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_ORGANIZATION_RELATIONSHIP_DAO_EXPORT = "org_member_dao"
LEGACY_ORGANIZATION_RELATIONSHIP_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_ORGANIZATION_RELATIONSHIP_IMPORT_IDENTITIES
)
LEGACY_INVITATION_IMPORT_IDENTITIES = (
    Path("app/models/invitation_code"),
    Path("app/dao/invitation_code_dao"),
)
LEGACY_INVITATION_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_INVITATION_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_INVITATION_DAO_EXPORT = "invitation_code_dao"
LEGACY_INVITATION_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_INVITATION_IMPORT_IDENTITIES
)
LEGACY_ONBOARDING_IMPORT_IDENTITIES = (
    Path("app/models/onboarding"),
    Path("app/api/onboarding"),
    Path("app/services/onboarding"),
)
LEGACY_ONBOARDING_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_ONBOARDING_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_ONBOARDING_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_ONBOARDING_IMPORT_IDENTITIES
)
LEGACY_DIRECTORY_IMPORT_IDENTITIES = (
    Path("app/api/directory"),
    Path("app/services/agent_directory"),
)
LEGACY_DIRECTORY_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_DIRECTORY_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_DIRECTORY_PACKAGE_EXPORTS = {
    Path("app/api/__init__.py"): ("directory",),
    Path("app/services/__init__.py"): ("agent_directory",),
}
LEGACY_DIRECTORY_DOTTED_IMPORT_IDENTITIES = tuple(
    dict.fromkeys(
        [
            identity.as_posix().replace("/", ".")
            for identity in LEGACY_DIRECTORY_IMPORT_IDENTITIES
        ]
        + [
            f"{relative_path.parent.as_posix().replace('/', '.')}.{export}"
            for relative_path, exports in LEGACY_DIRECTORY_PACKAGE_EXPORTS.items()
            for export in exports
        ]
    )
)
LEGACY_FOCUS_IMPORT_IDENTITIES = (
    Path("app/models/focus"),
    Path("app/dao/focus_dao"),
    Path("app/api/focus"),
    Path("app/services/focus_service"),
)
LEGACY_FOCUS_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_FOCUS_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_FOCUS_DAO_EXPORT = "focus_dao"
LEGACY_FOCUS_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_FOCUS_IMPORT_IDENTITIES
)
LEGACY_NOTIFICATION_IMPORT_IDENTITIES = (
    Path("app/models/notification"),
    Path("app/api/notification"),
    Path("app/services/notification_service"),
)
LEGACY_NOTIFICATION_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_NOTIFICATION_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_NOTIFICATION_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_NOTIFICATION_IMPORT_IDENTITIES
)
LEGACY_PUBLISHED_PAGE_IMPORT_IDENTITIES = (
    Path("app/models/published_page"),
    Path("app/api/pages"),
)
LEGACY_PUBLISHED_PAGE_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_PUBLISHED_PAGE_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_PUBLISHED_PAGE_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_PUBLISHED_PAGE_IMPORT_IDENTITIES
)
LEGACY_PLAZA_IMPORT_IDENTITIES = (
    Path("app/models/plaza"),
    Path("app/api/plaza"),
)
LEGACY_PLAZA_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_PLAZA_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_PLAZA_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_PLAZA_IMPORT_IDENTITIES
)
LEGACY_AGENT_TEMPLATE_IMPORT_IDENTITIES = (
    Path("app/dao/agent_template_dao"),
    Path("app/services/template_seeder"),
)
LEGACY_AGENT_TEMPLATE_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_AGENT_TEMPLATE_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_AGENT_TEMPLATE_DAO_EXPORT = "agent_template_dao"
LEGACY_AGENT_TEMPLATE_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_AGENT_TEMPLATE_IMPORT_IDENTITIES
)
LEGACY_AGENTBAY_IMPORT_IDENTITIES = (
    Path("app/api/agentbay_control"),
    Path("app/services/agentbay_client"),
    Path("app/services/agentbay_live"),
)
LEGACY_AGENTBAY_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_AGENTBAY_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_AGENTBAY_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_AGENTBAY_IMPORT_IDENTITIES
)
LEGACY_TENANT_KNOWLEDGE_PUBLICATION_IMPORT_IDENTITIES = (
    Path("app/services/enterprise_sync"),
)
LEGACY_TENANT_KNOWLEDGE_PUBLICATION_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_TENANT_KNOWLEDGE_PUBLICATION_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_SESSION_SUBSTRATE_IMPORT_IDENTITIES = (
    Path("app/models/chat_session"),
    Path("app/dao/chat_session_dao"),
    Path("app/dao/chat_message_dao"),
    Path("app/services/chat_session_service"),
    Path("app/services/channel_session"),
    Path("app/api/chat_sessions"),
    Path("app/api/websocket"),
)
LEGACY_SESSION_SUBSTRATE_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_SESSION_SUBSTRATE_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_SESSION_SUBSTRATE_DAO_EXPORTS = (
    "chat_session_dao",
    "chat_message_dao",
)
LEGACY_SESSION_SUBSTRATE_DEFINITION_ROOTS = (
    Path("app/models"),
    Path("app/schemas"),
    Path("app/modules"),
)
LEGACY_SESSION_SUBSTRATE_FORBIDDEN_DEFINITIONS = frozenset(
    {
        "class:ChatMessage",
        "table:chat_messages",
        "enum:chat_role_enum",
        "class:ChatMessageOut",
        "class:ChatSend",
    }
)
LEGACY_GROUP_PARTICIPANT_IMPORT_IDENTITIES = (
    Path("app/models/group"),
    Path("app/models/participant"),
    Path("app/dao/group_dao"),
    Path("app/dao/participant_dao"),
    Path("app/api/groups"),
    Path("app/api/group_websocket"),
    Path("app/services/group_chat_service"),
    Path("app/services/group_message_service"),
    Path("app/services/group_file_service"),
    Path("app/services/group_realtime"),
    Path("app/services/participant_identity"),
)
LEGACY_GROUP_PARTICIPANT_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_GROUP_PARTICIPANT_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_GROUP_PARTICIPANT_DAO_EXPORTS = ("group_dao", "participant_dao")
LEGACY_GROUP_PARTICIPANT_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_GROUP_PARTICIPANT_IMPORT_IDENTITIES
)
LEGACY_SCHEDULE_IMPORT_IDENTITIES = (
    Path("app/models/schedule"),
    Path("app/api/schedules"),
    Path("app/services/scheduler"),
    Path("app/scripts/migrate_schedules_to_triggers"),
)
LEGACY_SCHEDULE_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_SCHEDULE_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_SCHEDULE_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_SCHEDULE_IMPORT_IDENTITIES
)
LEGACY_SCHEDULE_DEFINITION_ROOTS = (Path("app"),)
LEGACY_SCHEDULE_FORBIDDEN_DEFINITIONS = frozenset(
    {"class:AgentSchedule", "table:agent_schedules"}
)
LEGACY_TRIGGER_WEBHOOK_IMPORT_IDENTITIES = (
    Path("app/models/trigger"),
    Path("app/models/trigger_execution"),
    Path("app/dao/trigger_dao"),
    Path("app/api/triggers"),
    Path("app/api/webhooks"),
    Path("app/services/trigger_daemon"),
    Path("app/services/trigger_runtime"),
)
LEGACY_TRIGGER_WEBHOOK_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_TRIGGER_WEBHOOK_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_TRIGGER_WEBHOOK_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_TRIGGER_WEBHOOK_IMPORT_IDENTITIES
)
LEGACY_TRIGGER_DAO_EXPORT = "trigger_dao"
LEGACY_TRIGGER_WEBHOOK_DEFINITION_ROOTS = (Path("app"),)
LEGACY_TRIGGER_WEBHOOK_FORBIDDEN_DEFINITIONS = frozenset(
    {
        "class:AgentTrigger",
        "class:TriggerExecution",
        "table:agent_triggers",
        "table:trigger_executions",
    }
)
LEGACY_HEARTBEAT_IMPORT_IDENTITIES = (
    Path("app/services/heartbeat"),
    Path("app/services/heartbeat_runtime"),
    Path("app/scripts/migrate_legacy_heartbeat_template"),
)
LEGACY_HEARTBEAT_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_HEARTBEAT_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_HEARTBEAT_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_HEARTBEAT_IMPORT_IDENTITIES
)
LEGACY_HEARTBEAT_TEMPLATE_PATH = Path("agent_template/HEARTBEAT.md")
LEGACY_HEARTBEAT_SANDBOX_SOURCE = Path(
    "app/services/sandbox/local/subprocess_backend.py"
)
LEGACY_HEARTBEAT_SANDBOX_FORBIDDEN_PATHS = frozenset(
    {"HEARTBEAT.md", "/HEARTBEAT.md"}
)
LEGACY_WORKSPACE_IMPORT_IDENTITIES = (
    Path("app/models/workspace"),
    Path("app/api/files"),
    Path("app/api/upload"),
    Path("app/services/workspace_collaboration"),
    Path("app/services/workspace_locking"),
    Path("app/services/workspace_paths"),
    Path("app/services/workspace_reconciliation"),
)
LEGACY_WORKSPACE_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_WORKSPACE_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_WORKSPACE_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_WORKSPACE_IMPORT_IDENTITIES
)
LEGACY_WORKSPACE_FORBIDDEN_DEFINITIONS = frozenset(
    {
        "class:WorkspaceEditLock",
        "class:WorkspaceFileRevision",
        "class:ResolvedWorkspacePath",
        "class:WorkspacePathError",
        "function:enterprise_info_root",
        "function:resolve_agent_visible_path",
        "function:resolve_path_within_root",
        "table:workspace_file_revisions",
        "table:workspace_edit_locks",
    }
)
LEGACY_A2A_IMPORT_IDENTITIES = (Path("app/services/collaboration"),)
LEGACY_A2A_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_A2A_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_A2A_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_A2A_IMPORT_IDENTITIES
)
LEGACY_ADVANCED_API_IMPORT_IDENTITY = Path("app/api/advanced")
LEGACY_ADVANCED_API_DOTTED_IMPORT_IDENTITY = (
    LEGACY_ADVANCED_API_IMPORT_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_ADVANCED_API_FORBIDDEN_FACTS = frozenset(
    {
        "class:DelegateRequest",
        "class:InterAgentMessage",
        "import:collaboration_service",
        "reference:collaboration_service",
        "reference:send_message_between_agents",
        "function:list_collaborators",
        "function:delegate_task",
        "function:send_inter_agent_message",
        "route:GET:/agents/{agent_id}/collaborators",
        "route:POST:/agents/{agent_id}/collaborate/delegate",
        "route:POST:/agents/{agent_id}/collaborate/message",
        "class:HandoverRequest",
        "class:TemplateCreate",
        "class:TemplateOut",
        "function:create_template",
        "function:delete_template",
        "function:get_agent_metrics",
        "function:get_template",
        "function:handover_agent",
        "function:list_templates",
        "route:DELETE:/templates/{template_id}",
        "route:GET:/agents/{agent_id}/metrics",
        "route:GET:/templates",
        "route:GET:/templates/{template_id}",
        "route:POST:/agents/{agent_id}/handover",
        "route:POST:/templates",
    }
)
LEGACY_ACTIVITY_API_IMPORT_IDENTITY = Path("app/api/activity")
LEGACY_ACTIVITY_API_DOTTED_IMPORT_IDENTITY = (
    LEGACY_ACTIVITY_API_IMPORT_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_ACTIVITY_API_FORBIDDEN_FACTS = frozenset(
    {
        "function:get_agent_activity",
        "function:get_conversation_messages",
        "function:list_conversations",
        "route:GET:/agents/{agent_id}/activity",
        "route:GET:/agents/{agent_id}/chat-history/conversations",
        "route:GET:/agents/{agent_id}/chat-history/{conv_id:path}",
    }
)
LEGACY_MESSAGES_API_IMPORT_IDENTITY = Path("app/api/messages")
LEGACY_MESSAGES_API_DOTTED_IMPORT_IDENTITY = (
    LEGACY_MESSAGES_API_IMPORT_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_MESSAGES_API_FORBIDDEN_FACTS = frozenset(
    {
        "function:get_inbox",
        "function:get_unread_count",
        "route:GET:/messages/inbox",
        "route:GET:/messages/unread-count",
    }
)
LEGACY_ADMIN_API_IMPORT_IDENTITY = Path("app/api/admin")
LEGACY_ADMIN_API_DOTTED_IMPORT_IDENTITY = (
    LEGACY_ADMIN_API_IMPORT_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_ADMIN_API_FORBIDDEN_FACTS = frozenset(
    {
        "class:CompanyCreateRequest",
        "class:CompanyCreateResponse",
        "class:CompanyStats",
        "class:PlatformSettingsOut",
        "class:PlatformSettingsUpdate",
        "function:create_company",
        "function:get_enhanced_metrics",
        "function:get_platform_leaderboards",
        "function:get_platform_settings",
        "function:get_platform_timeseries",
        "function:list_companies",
        "function:toggle_company",
        "function:update_platform_settings",
        "route:GET:/companies",
        "route:GET:/metrics/enhanced",
        "route:GET:/metrics/leaderboards",
        "route:GET:/metrics/timeseries",
        "route:GET:/platform-settings",
        "route:POST:/companies",
        "route:PUT:/companies/{company_id}/toggle",
        "route:PUT:/platform-settings",
    }
)
LEGACY_ENTERPRISE_TRANSPORT_IMPORT_IDENTITIES = (
    Path("app/api/enterprise"),
    Path("app/schemas/schemas"),
)
LEGACY_ENTERPRISE_TRANSPORT_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_ENTERPRISE_TRANSPORT_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_ENTERPRISE_TRANSPORT_DOTTED_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_ENTERPRISE_TRANSPORT_IMPORT_IDENTITIES
)
LEGACY_ENTERPRISE_TRANSPORT_TEST_PATHS = (
    Path("tests/test_enterprise_invites.py"),
    Path("tests/test_enterprise_system_settings_access.py"),
)
LEGACY_OBSERVABILITY_AUDIT_SERVICE_IDENTITIES = (
    Path("app/services/activity_logger"),
    Path("app/services/audit_logger"),
)
LEGACY_OBSERVABILITY_AUDIT_SERVICE_DOTTED_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_OBSERVABILITY_AUDIT_SERVICE_IDENTITIES
)
LEGACY_PLATFORM_SERVICE_IDENTITY = Path("app/services/platform_service")
LEGACY_PLATFORM_SERVICE_DOTTED_IDENTITY = (
    LEGACY_PLATFORM_SERVICE_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_QUOTA_GUARD_IDENTITY = Path("app/services/quota_guard")
LEGACY_QUOTA_GUARD_DOTTED_IDENTITY = (
    LEGACY_QUOTA_GUARD_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_REALTIME_SERVICE_IDENTITIES = (
    Path("app/services/realtime"),
    Path("app/services/realtime_runtime"),
)
LEGACY_REALTIME_SERVICE_DOTTED_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_REALTIME_SERVICE_IDENTITIES
)
LEGACY_RESOURCE_DISCOVERY_IDENTITY = Path("app/services/resource_discovery")
LEGACY_RESOURCE_DISCOVERY_DOTTED_IDENTITY = (
    LEGACY_RESOURCE_DISCOVERY_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_SYSTEM_EMAIL_SERVICE_IDENTITY = Path("app/services/system_email_service")
LEGACY_SYSTEM_EMAIL_SERVICE_DOTTED_IDENTITY = (
    LEGACY_SYSTEM_EMAIL_SERVICE_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_SYSTEM_EMAIL_TEST_PATH = Path("tests/test_system_email.py")
LEGACY_VISION_MAINTENANCE_IDENTITIES = (
    Path("app/services/vision_inject"),
    Path("app/scripts/backfill_department_paths"),
    Path("app/scripts/cleanup_duplicate_feishu_users"),
    Path("app/scripts/disable_plaza_social_tools"),
)
LEGACY_VISION_MAINTENANCE_DOTTED_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_VISION_MAINTENANCE_IDENTITIES
)
LEGACY_ORPHAN_MAINTENANCE_IDENTITIES = (
    Path("remove_old_tool"),
    Path("update_schema"),
    Path("scripts/backfill_chat_message_tenant_id"),
)
LEGACY_ORPHAN_MAINTENANCE_DOTTED_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_ORPHAN_MAINTENANCE_IDENTITIES
)
LEGACY_ORPHAN_MAINTENANCE_INVOCATIONS = (
    "remove_old_tool.py",
    "update_schema.py",
    "backfill_chat_message_tenant_id.py",
    "-m remove_old_tool",
    "-m update_schema",
    "-m scripts.backfill_chat_message_tenant_id",
    "-m backend.remove_old_tool",
    "-m backend.update_schema",
    "-m backend.scripts.backfill_chat_message_tenant_id",
)
LEGACY_ORPHAN_MAINTENANCE_ENTRYPOINTS = frozenset(
    {
        "remove_old_tool",
        "update_schema",
        "scripts.backfill_chat_message_tenant_id",
        "backend.remove_old_tool",
        "backend.update_schema",
        "backend.scripts.backfill_chat_message_tenant_id",
    }
)
LEGACY_MAINTENANCE_EXECUTABLE_SUFFIXES = frozenset({".sh", ".toml", ".yaml", ".yml"})
LEGACY_OBSERVABILITY_AUDIT_PERSISTENCE_IDENTITIES = (
    Path("app/dao/activity_dao"),
    Path("app/dao/agent_metrics_dao"),
    Path("app/models/activity_log"),
    Path("app/models/audit"),
)
LEGACY_OBSERVABILITY_AUDIT_PERSISTENCE_DOTTED_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_OBSERVABILITY_AUDIT_PERSISTENCE_IDENTITIES
)
LEGACY_OBSERVABILITY_AUDIT_DAO_EXPORTS = ("activity_dao", "agent_metrics_dao")
LEGACY_OBSERVABILITY_AUDIT_FORBIDDEN_FACTS = frozenset(
    {
        "class:AgentActivityLog",
        "class:AuditLog",
        "class:DailyTokenUsage",
        "class:EnterpriseInfo",
        "table:agent_activity_logs",
        "table:audit_logs",
        "table:daily_token_usage",
        "table:enterprise_info",
    }
)
LEGACY_RUN_SETTING_PERSISTENCE_IDENTITIES = (
    Path("app/dao/agent_run_dao"),
    Path("app/dao/system_setting_dao"),
    Path("app/models/system_settings"),
)
LEGACY_RUN_SETTING_PERSISTENCE_DOTTED_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_RUN_SETTING_PERSISTENCE_IDENTITIES
)
LEGACY_RUN_SETTING_DAO_EXPORTS = ("agent_run_dao", "system_setting_dao")
LEGACY_RUN_SETTING_FORBIDDEN_FACTS = frozenset(
    {"class:SystemSetting", "table:system_settings"}
)
LEGACY_CORE_COMPATIBILITY_IDENTITIES = (
    Path("app/core/middleware"),
    Path("app/core/permissions"),
    Path("app/core/error_contract"),
)
LEGACY_CORE_COMPATIBILITY_DOTTED_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_CORE_COMPATIBILITY_IDENTITIES
)
LEGACY_ERROR_CONTRACT_TEST = Path("tests/test_error_contract.py")
LEGACY_BASE_DAO_TEST = Path("tests/test_base_dao.py")
LEGACY_CORE_COMPATIBILITY_FORBIDDEN_FACTS = frozenset(
    {
        "class:RosterVisibility",
        "class:TenantContextMiddleware",
        "class:TraceIdMiddleware",
        "function:build_visible_agents_query",
        "function:can_manage_agent",
        "function:can_use_agent",
        "function:check_agent_access",
        "function:register_error_handlers",
    }
)
LEGACY_LOGGING_CONFIG_IDENTITY = Path("app/core/logging_config")
LEGACY_LOGGING_CONFIG_DOTTED_IDENTITY = (
    LEGACY_LOGGING_CONFIG_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_LOGGING_CONFIG_FORBIDDEN_DEFINITIONS = frozenset(
    {
        "assigned:NOISY_CONNECTION_LOGGERS",
        "assigned:configured_logger",
        "assigned:trace_id_var",
        "function:_disable_agentbay_logger_override",
        "function:configure_logging",
        "function:get_trace_id",
        "function:intercept_standard_logging",
        "function:new_trace_id",
        "function:quiet_noisy_connection_loggers",
        "function:set_trace_id",
    }
)
LEGACY_SECURITY_DAO_IDENTITIES = (
    Path("app/core/security"),
    Path("app/dao/base"),
    Path("app/dao/query_dao"),
)
LEGACY_SECURITY_DAO_DOTTED_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_SECURITY_DAO_IDENTITIES
)
LEGACY_SECURITY_DAO_FORBIDDEN_DEFINITIONS = frozenset(
    {
        "assigned:ROLE_HIERARCHY",
        "assigned:query_dao",
        "assigned:security",
        "class:BaseDAO",
        "class:QueryDAO",
        "class:TenantScopedBaseDAO",
        "function:create_access_token",
        "function:decode_access_token",
        "function:decrypt_data",
        "function:encrypt_data",
        "function:get_authenticated_user",
        "function:get_current_admin",
        "function:get_current_user",
        "function:hash_password",
        "function:hash_password_async",
        "function:identity_membership_query",
        "function:require_role",
        "function:tenant_context",
        "function:verify_password",
        "function:verify_password_async",
    }
)
LEGACY_CORE_EVENTS_IDENTITY = Path("app/core/events")
LEGACY_CORE_EVENTS_DOTTED_IDENTITY = (
    LEGACY_CORE_EVENTS_IDENTITY.as_posix().replace("/", ".")
)
LEGACY_CORE_EVENTS_FORBIDDEN_DEFINITIONS = frozenset(
    {
        "assigned:_redis_client",
        "function:close_redis",
        "function:get_redis",
        "function:publish_event",
    }
)
EMAIL_PROVIDER_SERVICE_SOURCE = Path("app/services/email_service.py")
EMAIL_PROVIDER_FORBIDDEN_STORAGE_IMPORTS = frozenset(
    {"app.services.storage", "app.services.storage_runtime"}
)
EMAIL_PROVIDER_REMOVED_SEND_FIELDS = frozenset(
    {"agent_id", "attachments", "workspace_path"}
)
LEGACY_SEED_SCRIPT = Path("seed.py")
LEGACY_BOOTSTRAP_IMPORT_IDENTITY = Path("app/scripts/bootstrap_db")
LEGACY_BOOTSTRAP_DOTTED_IMPORT_IDENTITY = (
    LEGACY_BOOTSTRAP_IMPORT_IDENTITY.as_posix().replace("/", ".")
)
SETUP_AND_STARTUP_SOURCES = (
    Path("setup.sh"),
    Path("restart.sh"),
    Path("backend/entrypoint.sh"),
)
LEGACY_STORAGE_IMPORT_IDENTITIES = (
    Path("app/services/storage"),
    Path("app/services/storage_runtime"),
)
LEGACY_STORAGE_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_STORAGE_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_STORAGE_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_STORAGE_IMPORT_IDENTITIES
)
LEGACY_STORAGE_TEST_PATHS = (
    Path("tests/test_storage_conditional_atomicity.py"),
    Path("tests/test_storage_fallback.py"),
    Path("tests/test_storage_s3.py"),
)
TARGET_OBJECT_STORAGE_PACKAGE_INIT = Path(
    "app/infrastructure/object_storage/__init__.py"
)
FEISHU_PROVIDER_TRANSPORT_SOURCE = Path("app/services/feishu_service.py")
DINGTALK_PROVIDER_TRANSPORT_SOURCE = Path("app/services/dingtalk_service.py")
LEGACY_FEISHU_AUTHORITY_METHODS = frozenset(
    {"get_app_access_token", "exchange_code_for_user", "login_or_register"}
)
LEGACY_FEISHU_CREDENTIAL_STATE = frozenset(
    {"app_id", "app_secret", "_app_access_token"}
)
LEGACY_DINGTALK_STREAM_WRAPPERS = frozenset({"download_dingtalk_media"})
LEGACY_CHANNEL_IMPORT_IDENTITIES = (
    Path("app/models/channel_config"),
    Path("app/models/channel_delivery"),
    Path("app/api/atlassian"),
    Path("app/api/dingtalk"),
    Path("app/api/discord_bot"),
    Path("app/api/feishu"),
    Path("app/api/slack"),
    Path("app/api/teams"),
    Path("app/api/wechat"),
    Path("app/api/wecom"),
    Path("app/api/whatsapp"),
    Path("app/services/atlassian_tool_service"),
    Path("app/services/channel_user_service"),
    Path("app/services/dingtalk_stream"),
    Path("app/services/discord_gateway"),
    Path("app/services/feishu_group_targets"),
    Path("app/services/feishu_ws"),
    Path("app/services/wechat_channel"),
    Path("app/services/wecom_stream"),
)
LEGACY_CHANNEL_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_CHANNEL_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_CHANNEL_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_CHANNEL_IMPORT_IDENTITIES
)
LEGACY_CHANNEL_PACKAGE_EXPORTS = {
    Path("app/models/__init__.py"): ("channel_config", "channel_delivery"),
    Path("app/api/__init__.py"): (
        "atlassian",
        "dingtalk",
        "discord_bot",
        "feishu",
        "slack",
        "teams",
        "wechat",
        "wecom",
        "whatsapp",
    ),
    Path("app/services/__init__.py"): (
        "atlassian_tool_service",
        "channel_user_service",
        "dingtalk_stream",
        "discord_gateway",
        "feishu_group_targets",
        "feishu_ws",
        "wechat_channel",
        "wecom_stream",
    ),
}
LEGACY_CHANNEL_CLEANUP_SCRIPT = Path(
    "scripts/remove_legacy_atlassian_agent_tool_secrets.py"
)
LEGACY_CHANNEL_FORBIDDEN_DEFINITIONS = frozenset(
    {
        "class:ChannelConfig",
        "class:ChannelDelivery",
        "class:ChannelConfigCreate",
        "class:ChannelConfigOut",
        "table:channel_configs",
        "table:channel_deliveries",
        "enum:channel_type_enum",
        "assigned:_CHANNEL_SECRET_KEY_PARTS",
        "function:_redact_channel_secrets",
    }
)
LEGACY_AUTONOMY_APPROVAL_IMPORT_IDENTITIES = (
    Path("app/services/autonomy_service"),
)
LEGACY_AUTONOMY_APPROVAL_REINTRODUCTIONS = [
    (identity, representation)
    for identity in LEGACY_AUTONOMY_APPROVAL_IMPORT_IDENTITIES
    for representation in ("module", "package")
]
LEGACY_AUTONOMY_APPROVAL_DOTTED_IMPORT_IDENTITIES = tuple(
    identity.as_posix().replace("/", ".")
    for identity in LEGACY_AUTONOMY_APPROVAL_IMPORT_IDENTITIES
)
LEGACY_AUTONOMY_APPROVAL_FORBIDDEN_FACTS = {
    Path("app/models/audit.py"): frozenset(
        {
            "class:ApprovalRequest",
            "table:approval_requests",
            "enum:approval_status_enum",
        }
    ),
    Path("app/api/enterprise.py"): frozenset(
        {
            "import:ApprovalRequest",
            "import:ApprovalRequestOut",
            "import:ApprovalAction",
            "import:autonomy_service",
            "reference:ApprovalRequest",
            "reference:ApprovalRequestOut",
            "reference:ApprovalAction",
            "reference:autonomy_service",
            "function:list_approvals",
            "function:resolve_approval",
            "route:GET:/approvals",
            "route:POST:/approvals/{approval_id}/resolve",
            "assigned:pending_approvals",
            "key:pending_approvals",
        }
    ),
    Path("app/api/advanced.py"): frozenset(
        {
            "class-field:TemplateCreate:default_autonomy_policy",
            "class-field:TemplateOut:default_autonomy_policy",
            "field:default_autonomy_policy",
            "key:default_autonomy_policy",
            "key:total_approvals",
            "key:pending_approvals",
        }
    ),
    Path("app/dao/agent_metrics_dao.py"): frozenset(
        {
            "import:ApprovalRequest",
            "reference:ApprovalRequest",
            "assigned:total_approvals",
            "assigned:pending_approvals",
            "key:total_approvals",
            "key:pending_approvals",
        }
    ),
    Path("app/schemas/schemas.py"): frozenset(
        {
            "class:ApprovalRequestOut",
            "class:ApprovalAction",
            "class-field:AgentCreate:autonomy_policy",
            "class-field:AgentOut:autonomy_policy",
            "class-field:AgentUpdate:autonomy_policy",
        }
    ),
    Path("app/services/feishu_service.py"): frozenset(
        {"function:send_approval_card"}
    ),
}
AGENT_TEMPLATE_METADATA_ROOT = Path("agent_templates")
LEGACY_TEMPLATE_AUTONOMY_FIELD = "default_autonomy_policy"
DELETED_AUTHORITY_GUARD_TEST = Path("tests/architecture/test_deleted_authorities.py")
DYNAMIC_MODULE_EXPORT_HOOK = "__getattr__"
DAO_PACKAGE_INIT = Path("app/dao/__init__.py")
REMOVED_ORPHAN_DIRECT_DEPENDENCIES = frozenset(
    {
        "anyascii",
        "dingtalk-stream",
        "discord-py",
        "langgraph",
        "langgraph-checkpoint-postgres",
        "markdown",
        "passlib",
        "pymupdf",
        "pynacl",
        "pycryptodome",
        "pypinyin",
        "psycopg",
        "python-jose",
        "python-multipart",
        "redis",
        "trafilatura",
        "wecom-aibot-sdk-python",
        "wuying-agentbay-sdk",
    }
)


class DeletedAuthorityViolation(RuntimeError):
    """A deleted Backend authority is present in the target tree."""


def _normalized_dependency_name(requirement: str) -> str:
    name = re.split(r"[<>=!~;@\[]", requirement, maxsplit=1)[0].strip()
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_dependency_names(source: str) -> set[str]:
    data = tomllib.loads(source)
    project = data.get("project", {})
    requirements = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        requirements.extend(group)
    for group in data.get("dependency-groups", {}).values():
        requirements.extend(group)
    return {_normalized_dependency_name(requirement) for requirement in requirements}


def _assert_removed_orphan_dependencies_absent(backend_root: Path) -> None:
    dependency_names = _declared_dependency_names(
        (backend_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    restored = sorted(REMOVED_ORPHAN_DIRECT_DEPENDENCIES & dependency_names)
    if restored:
        raise DeletedAuthorityViolation(
            f"deleted-owner direct dependencies restored: {restored}"
        )


def _is_globals_call(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "globals"
        and not node.args
        and not node.keywords
    )


def _is_dynamic_export_hook_name(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == DYNAMIC_MODULE_EXPORT_HOOK


def _is_globals_hook_target(node: ast.expr) -> bool:
    if isinstance(node, (ast.Tuple, ast.List)):
        return any(_is_globals_hook_target(element) for element in node.elts)
    if isinstance(node, ast.Starred):
        return _is_globals_hook_target(node.value)
    return (
        isinstance(node, ast.Subscript)
        and _is_globals_call(node.value)
        and _is_dynamic_export_hook_name(node.slice)
    )


class _ModuleScopeDynamicExportHookVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.installs_hook = False

    def _visit_function_signature(
        self,
        *,
        decorators: list[ast.expr],
        arguments: ast.arguments,
    ) -> None:
        for decorator in decorators:
            self.visit(decorator)
        for default in (*arguments.defaults, *arguments.kw_defaults):
            if default is not None:
                self.visit(default)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_signature(
            decorators=node.decorator_list,
            arguments=node.args,
        )

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_signature(
            decorators=node.decorator_list,
            arguments=node.args,
        )

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_function_signature(decorators=[], arguments=node.args)

    def visit_Assign(self, node: ast.Assign) -> None:
        if any(_is_globals_hook_target(target) for target in node.targets):
            self.installs_hook = True
            return
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if _is_globals_hook_target(node.target):
            self.installs_hook = True
            return
        if node.value is not None:
            self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if _is_globals_hook_target(node.target):
            self.installs_hook = True
            return
        self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        calls_globals_setitem = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "__setitem__"
            and _is_globals_call(node.func.value)
            and bool(node.args)
            and _is_dynamic_export_hook_name(node.args[0])
        )
        calls_setattr = (
            isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and _is_dynamic_export_hook_name(node.args[1])
        )
        if calls_globals_setitem or calls_setattr:
            self.installs_hook = True
            return
        self.generic_visit(node)


def _assert_dao_package_exports_are_static(backend_root: Path) -> None:
    package_init = backend_root / DAO_PACKAGE_INIT
    if not package_init.is_file():
        return

    source = package_init.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(package_init))
    symbols = symtable.symtable(source, str(package_init), "exec")
    try:
        dynamic_hook = symbols.lookup(DYNAMIC_MODULE_EXPORT_HOOK)
    except KeyError:
        binds_dynamic_hook = False
    else:
        binds_dynamic_hook = (
            dynamic_hook.is_assigned()
            or dynamic_hook.is_imported()
            or dynamic_hook.is_namespace()
        )

    dynamic_installer = _ModuleScopeDynamicExportHookVisitor()
    dynamic_installer.visit(tree)
    if binds_dynamic_hook or dynamic_installer.installs_hook:
        raise DeletedAuthorityViolation(
            "app.dao package exports must be static; module-level __getattr__ is "
            "forbidden"
        )


def _assert_deleted_dao_package_exports(
    backend_root: Path,
    *,
    authority: str,
    exports: tuple[str, ...],
) -> None:
    package_init = backend_root / DAO_PACKAGE_INIT
    if not package_init.is_file():
        return

    tree = ast.parse(package_init.read_text(encoding="utf-8"), filename=str(package_init))
    for node in ast.walk(tree):
        for export in exports:
            references_export = (
                isinstance(node, ast.Name) and node.id == export
            ) or (
                isinstance(node, ast.Attribute) and node.attr == export
            ) or (
                isinstance(node, ast.Constant) and node.value == export
            ) or (
                isinstance(node, ast.keyword) and node.arg == export
            ) or (
                isinstance(node, ast.alias)
                and (
                    node.name.split(".")[-1] == export
                    or node.asname == export
                )
            )
            if references_export:
                raise DeletedAuthorityViolation(
                    f"deleted legacy {authority} DAO package export was "
                    f"reintroduced: {export}"
                )


def _assert_deleted_context_authority(backend_root: Path) -> None:
    module = backend_root / CONTEXT_MODULE
    package = backend_root / CONTEXT_PACKAGE
    if module.is_file():
        raise DeletedAuthorityViolation(
            f"deleted Context authority module was reintroduced: {CONTEXT_MODULE}"
        )
    if package.is_dir():
        raise DeletedAuthorityViolation(
            f"deleted Context authority package was reintroduced: {CONTEXT_PACKAGE}"
        )


def _assert_deleted_experience_authorities(backend_root: Path) -> None:
    for identity in EXPERIENCE_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted Experience authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted Experience authority package was reintroduced: {identity}"
            )


def _assert_deleted_model_llm_authorities(backend_root: Path) -> None:
    for identity in MODEL_LLM_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted Model/LLM authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted Model/LLM authority package was reintroduced: {identity}"
            )


def _assert_deleted_persistent_task_authorities(backend_root: Path) -> None:
    for identity in PERSISTENT_TASK_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted Persistent Task authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted Persistent Task authority package was reintroduced: {identity}"
            )


def _assert_deleted_legacy_tool_authorities(backend_root: Path) -> None:
    for identity in LEGACY_TOOL_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy Tool authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy Tool authority package was reintroduced: {identity}"
            )


def _assert_deleted_legacy_skill_authorities(backend_root: Path) -> None:
    for identity in LEGACY_SKILL_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy Skill authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy Skill authority package was reintroduced: {identity}"
            )

    creator_files = backend_root / LEGACY_SKILL_CREATOR_FILES
    if creator_files.exists():
        raise DeletedAuthorityViolation(
            "deleted legacy Skill creator-files path was reintroduced: "
            f"{LEGACY_SKILL_CREATOR_FILES}"
        )


def _assert_deleted_openclaw_gateway_authorities(backend_root: Path) -> None:
    for identity in OPENCLAW_GATEWAY_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted OpenClaw/Gateway authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted OpenClaw/Gateway authority package was reintroduced: {identity}"
            )


def _assert_deleted_legacy_credential_authorities(backend_root: Path) -> None:
    for identity in LEGACY_CREDENTIAL_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy Credential authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy Credential authority package was reintroduced: {identity}"
            )


def _assert_deleted_legacy_credential_dao_export(backend_root: Path) -> None:
    _assert_deleted_dao_package_exports(
        backend_root,
        authority="Credential",
        exports=(LEGACY_CREDENTIAL_DAO_EXPORT,),
    )


def _assert_deleted_legacy_agent_authorities(backend_root: Path) -> None:
    for identity in LEGACY_AGENT_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy Agent authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy Agent authority package was reintroduced: {identity}"
            )


def _assert_deleted_legacy_agent_dao_exports(backend_root: Path) -> None:
    _assert_deleted_dao_package_exports(
        backend_root,
        authority="Agent",
        exports=LEGACY_AGENT_DAO_EXPORTS,
    )


def _assert_deleted_legacy_agent_run_event_dao_authority(
    backend_root: Path,
) -> None:
    identity = LEGACY_AGENT_RUN_EVENT_DAO_IMPORT_IDENTITY
    module = (backend_root / identity).with_suffix(".py")
    package = backend_root / identity
    if module.is_file():
        raise DeletedAuthorityViolation(
            "deleted legacy Agent Run Event DAO compatibility module was "
            f"reintroduced: {identity}"
        )
    if package.is_dir():
        raise DeletedAuthorityViolation(
            "deleted legacy Agent Run Event DAO compatibility package was "
            f"reintroduced: {identity}"
        )


def _assert_tests_do_not_reference_deleted_agent_run_event_dao(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Agent Run Event DAO compatibility",
        deleted_identities=(LEGACY_AGENT_RUN_EVENT_DAO_DOTTED_IMPORT_IDENTITY,),
    )


def _assert_deleted_legacy_okr_agent_hook_authority(backend_root: Path) -> None:
    identity = LEGACY_OKR_AGENT_HOOK_IMPORT_IDENTITY
    module = (backend_root / identity).with_suffix(".py")
    package = backend_root / identity
    if module.is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy OKR Agent Hook module was reintroduced: {identity}"
        )
    if package.is_dir():
        raise DeletedAuthorityViolation(
            f"deleted legacy OKR Agent Hook package was reintroduced: {identity}"
        )


def _assert_tests_do_not_reference_deleted_okr_agent_hook(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="OKR Agent Hook",
        deleted_identities=(LEGACY_OKR_AGENT_HOOK_DOTTED_IMPORT_IDENTITY,),
    )


def _assert_deleted_legacy_okr_authorities(backend_root: Path) -> None:
    for identity in LEGACY_OKR_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy OKR authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy OKR authority package was reintroduced: {identity}"
            )


def _assert_tests_do_not_reference_deleted_okr_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="OKR",
        deleted_identities=LEGACY_OKR_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_application_does_not_restore_okr_definitions(
    backend_root: Path,
) -> None:
    source_paths: set[Path] = set()
    for relative_root in LEGACY_OKR_DEFINITION_ROOTS:
        source_root = backend_root / relative_root
        if source_root.is_dir():
            source_paths.update(source_root.rglob("*.py"))

    for source_path in sorted(source_paths):
        relative_path = source_path.relative_to(backend_root)
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        restored_facts = sorted(
            LEGACY_OKR_FORBIDDEN_DEFINITIONS & _source_contract_facts(tree)
        )
        if restored_facts:
            raise DeletedAuthorityViolation(
                "application source restores legacy OKR definitions: "
                f"{relative_path} -> {', '.join(restored_facts)}"
            )


def _assert_deleted_legacy_token_tracker_authority(backend_root: Path) -> None:
    identity = LEGACY_TOKEN_TRACKER_IMPORT_IDENTITY
    module = (backend_root / identity).with_suffix(".py")
    package = backend_root / identity
    if module.is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy Token Tracker module was reintroduced: {identity}"
        )
    if package.is_dir():
        raise DeletedAuthorityViolation(
            f"deleted legacy Token Tracker package was reintroduced: {identity}"
        )


def _assert_tests_do_not_reference_deleted_token_tracker(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Token Tracker",
        deleted_identities=(LEGACY_TOKEN_TRACKER_DOTTED_IMPORT_IDENTITY,),
    )


def _assert_deleted_legacy_wecom_service_authority(backend_root: Path) -> None:
    identity = LEGACY_WECOM_SERVICE_IMPORT_IDENTITY
    module = (backend_root / identity).with_suffix(".py")
    package = backend_root / identity
    if module.is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy WeCom service module was reintroduced: {identity}"
        )
    if package.is_dir():
        raise DeletedAuthorityViolation(
            f"deleted legacy WeCom service package was reintroduced: {identity}"
        )


def _assert_tests_do_not_reference_deleted_wecom_service(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="WeCom service",
        deleted_identities=(LEGACY_WECOM_SERVICE_DOTTED_IMPORT_IDENTITY,),
    )


def _assert_deleted_legacy_identity_tenant_authorities(backend_root: Path) -> None:
    for identity in LEGACY_IDENTITY_TENANT_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Identity/Tenant authority module was reintroduced: "
                f"{identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Identity/Tenant authority package was reintroduced: "
                f"{identity}"
            )


def _assert_deleted_legacy_identity_tenant_dao_exports(
    backend_root: Path,
) -> None:
    _assert_deleted_dao_package_exports(
        backend_root,
        authority="Identity/Tenant",
        exports=LEGACY_IDENTITY_TENANT_DAO_EXPORTS,
    )


def _assert_deleted_legacy_auth_authorities(backend_root: Path) -> None:
    for identity in LEGACY_AUTH_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy Auth authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy Auth authority package was reintroduced: {identity}"
            )


def _assert_deleted_legacy_auth_package_exports(backend_root: Path) -> None:
    for relative_path, exports in LEGACY_AUTH_PACKAGE_EXPORTS.items():
        package_init = backend_root / relative_path
        if not package_init.is_file():
            continue

        tree = ast.parse(
            package_init.read_text(encoding="utf-8"),
            filename=str(package_init),
        )
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                statement.name == DYNAMIC_MODULE_EXPORT_HOOK
            ):
                raise DeletedAuthorityViolation(
                    "deleted legacy Auth package exports can be restored by "
                    f"a module-level __getattr__ hook in {relative_path}"
                )

        for node in ast.walk(tree):
            references_dynamic_hook = (
                isinstance(node, ast.Name) and node.id == DYNAMIC_MODULE_EXPORT_HOOK
            ) or (
                isinstance(node, ast.Attribute)
                and node.attr == DYNAMIC_MODULE_EXPORT_HOOK
            ) or (
                isinstance(node, ast.Constant)
                and node.value == DYNAMIC_MODULE_EXPORT_HOOK
            ) or (
                isinstance(node, ast.keyword)
                and node.arg == DYNAMIC_MODULE_EXPORT_HOOK
            ) or (
                isinstance(node, ast.alias)
                and (
                    node.name.split(".")[-1] == DYNAMIC_MODULE_EXPORT_HOOK
                    or node.asname == DYNAMIC_MODULE_EXPORT_HOOK
                )
            )
            if references_dynamic_hook:
                raise DeletedAuthorityViolation(
                    "deleted legacy Auth package exports can be restored by "
                    f"a module-level __getattr__ hook in {relative_path}"
                )

            for export in exports:
                references_export = (
                    isinstance(node, ast.Name) and node.id == export
                ) or (
                    isinstance(node, ast.Attribute) and node.attr == export
                ) or (
                    isinstance(node, ast.Constant) and node.value == export
                ) or (
                    isinstance(node, ast.keyword) and node.arg == export
                ) or (
                    isinstance(node, ast.alias)
                    and (
                        node.name.split(".")[-1] == export
                        or node.asname == export
                    )
                )
                if references_export:
                    raise DeletedAuthorityViolation(
                        "deleted legacy Auth package export was reintroduced in "
                        f"{relative_path}: {export}"
                    )


def _assert_tests_do_not_import_deleted_auth_authorities(
    backend_root: Path,
) -> None:
    tests_root = backend_root / "tests"
    if not tests_root.is_dir():
        return

    for source_path in sorted(tests_root.rglob("*.py")):
        relative_path = source_path.relative_to(backend_root)
        if relative_path == DELETED_AUTHORITY_GUARD_TEST:
            continue

        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        imported_identities: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_identities.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_identities.append(node.module)
                imported_identities.extend(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )

        for imported_identity in imported_identities:
            for deleted_identity in LEGACY_AUTH_DOTTED_IMPORT_IDENTITIES:
                if imported_identity == deleted_identity or imported_identity.startswith(
                    f"{deleted_identity}."
                ):
                    raise DeletedAuthorityViolation(
                        "test imports deleted legacy Auth authority: "
                        f"{relative_path} -> {deleted_identity}"
                    )


def _assert_deleted_legacy_sso_authorities(backend_root: Path) -> None:
    for identity in LEGACY_SSO_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy SSO authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy SSO authority package was reintroduced: {identity}"
            )


def _assert_deleted_legacy_sso_dao_export(backend_root: Path) -> None:
    _assert_deleted_dao_package_exports(
        backend_root,
        authority="SSO",
        exports=(LEGACY_SSO_DAO_EXPORT,),
    )


def _assert_tests_do_not_import_deleted_sso_authorities(
    backend_root: Path,
) -> None:
    tests_root = backend_root / "tests"
    if not tests_root.is_dir():
        return

    for source_path in sorted(tests_root.rglob("*.py")):
        relative_path = source_path.relative_to(backend_root)
        if relative_path == DELETED_AUTHORITY_GUARD_TEST:
            continue

        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        imported_identities: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_identities.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_identities.append(node.module)
                imported_identities.extend(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )

        for imported_identity in imported_identities:
            for deleted_identity in LEGACY_SSO_DOTTED_IMPORT_IDENTITIES:
                if imported_identity == deleted_identity or imported_identity.startswith(
                    f"{deleted_identity}."
                ):
                    raise DeletedAuthorityViolation(
                        "test imports deleted legacy SSO authority: "
                        f"{relative_path} -> {deleted_identity}"
                    )


def _assert_deleted_legacy_organization_relationship_authorities(
    backend_root: Path,
) -> None:
    for identity in LEGACY_ORGANIZATION_RELATIONSHIP_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Organization/Relationship authority module was "
                f"reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Organization/Relationship authority package was "
                f"reintroduced: {identity}"
            )


def _assert_deleted_legacy_organization_relationship_dao_export(
    backend_root: Path,
) -> None:
    _assert_deleted_dao_package_exports(
        backend_root,
        authority="Organization/Relationship",
        exports=(LEGACY_ORGANIZATION_RELATIONSHIP_DAO_EXPORT,),
    )


def _assert_tests_do_not_import_deleted_organization_relationship_authorities(
    backend_root: Path,
) -> None:
    tests_root = backend_root / "tests"
    if not tests_root.is_dir():
        return

    for source_path in sorted(tests_root.rglob("*.py")):
        relative_path = source_path.relative_to(backend_root)
        if relative_path == DELETED_AUTHORITY_GUARD_TEST:
            continue

        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        imported_identities: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_identities.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_identities.append(node.module)
                imported_identities.extend(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )

        for imported_identity in imported_identities:
            for deleted_identity in (
                LEGACY_ORGANIZATION_RELATIONSHIP_DOTTED_IMPORT_IDENTITIES
            ):
                if imported_identity == deleted_identity or imported_identity.startswith(
                    f"{deleted_identity}."
                ):
                    raise DeletedAuthorityViolation(
                        "test imports deleted legacy Organization/Relationship authority: "
                        f"{relative_path} -> {deleted_identity}"
                    )


def _assert_deleted_legacy_invitation_authorities(backend_root: Path) -> None:
    for identity in LEGACY_INVITATION_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Invitation authority module was reintroduced: "
                f"{identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Invitation authority package was reintroduced: "
                f"{identity}"
            )


def _assert_deleted_legacy_invitation_dao_export(backend_root: Path) -> None:
    _assert_deleted_dao_package_exports(
        backend_root,
        authority="Invitation",
        exports=(LEGACY_INVITATION_DAO_EXPORT,),
    )


def _assert_tests_do_not_import_deleted_invitation_authorities(
    backend_root: Path,
) -> None:
    tests_root = backend_root / "tests"
    if not tests_root.is_dir():
        return

    for source_path in sorted(tests_root.rglob("*.py")):
        relative_path = source_path.relative_to(backend_root)
        if relative_path == DELETED_AUTHORITY_GUARD_TEST:
            continue

        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        imported_identities: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_identities.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_identities.append(node.module)
                imported_identities.extend(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )

        for imported_identity in imported_identities:
            for deleted_identity in LEGACY_INVITATION_DOTTED_IMPORT_IDENTITIES:
                if imported_identity == deleted_identity or imported_identity.startswith(
                    f"{deleted_identity}."
                ):
                    raise DeletedAuthorityViolation(
                        "test imports deleted legacy Invitation authority: "
                        f"{relative_path} -> {deleted_identity}"
                    )


def _assert_deleted_legacy_onboarding_authorities(backend_root: Path) -> None:
    for identity in LEGACY_ONBOARDING_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Onboarding authority module was reintroduced: "
                f"{identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Onboarding authority package was reintroduced: "
                f"{identity}"
            )


def _assert_tests_do_not_import_deleted_onboarding_authorities(
    backend_root: Path,
) -> None:
    tests_root = backend_root / "tests"
    if not tests_root.is_dir():
        return

    for source_path in sorted(tests_root.rglob("*.py")):
        relative_path = source_path.relative_to(backend_root)
        if relative_path == DELETED_AUTHORITY_GUARD_TEST:
            continue

        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        imported_identities: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_identities.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_identities.append(node.module)
                imported_identities.extend(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )

        for imported_identity in imported_identities:
            for deleted_identity in LEGACY_ONBOARDING_DOTTED_IMPORT_IDENTITIES:
                if imported_identity == deleted_identity or imported_identity.startswith(
                    f"{deleted_identity}."
                ):
                    raise DeletedAuthorityViolation(
                        "test imports deleted legacy Onboarding authority: "
                        f"{relative_path} -> {deleted_identity}"
                    )


def _assert_deleted_legacy_directory_authorities(backend_root: Path) -> None:
    for identity in LEGACY_DIRECTORY_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Directory authority module was reintroduced: "
                f"{identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Directory authority package was reintroduced: "
                f"{identity}"
            )


def _assert_deleted_legacy_directory_package_exports(backend_root: Path) -> None:
    for relative_path, exports in LEGACY_DIRECTORY_PACKAGE_EXPORTS.items():
        package_init = backend_root / relative_path
        if not package_init.is_file():
            continue

        tree = ast.parse(
            package_init.read_text(encoding="utf-8"),
            filename=str(package_init),
        )
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                statement.name == DYNAMIC_MODULE_EXPORT_HOOK
            ):
                raise DeletedAuthorityViolation(
                    "deleted legacy Directory package exports can be restored by "
                    f"a module-level __getattr__ hook in {relative_path}"
                )

        for node in ast.walk(tree):
            references_dynamic_hook = (
                isinstance(node, ast.Name) and node.id == DYNAMIC_MODULE_EXPORT_HOOK
            ) or (
                isinstance(node, ast.Attribute)
                and node.attr == DYNAMIC_MODULE_EXPORT_HOOK
            ) or (
                isinstance(node, ast.Constant)
                and node.value == DYNAMIC_MODULE_EXPORT_HOOK
            ) or (
                isinstance(node, ast.keyword)
                and node.arg == DYNAMIC_MODULE_EXPORT_HOOK
            ) or (
                isinstance(node, ast.alias)
                and (
                    node.name.split(".")[-1] == DYNAMIC_MODULE_EXPORT_HOOK
                    or node.asname == DYNAMIC_MODULE_EXPORT_HOOK
                )
            )
            if references_dynamic_hook:
                raise DeletedAuthorityViolation(
                    "deleted legacy Directory package exports can be restored by "
                    f"a module-level __getattr__ hook in {relative_path}"
                )

            for export in exports:
                imports_export_module = (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    == f"{relative_path.parent.as_posix().replace('/', '.')}.{export}"
                )
                references_export = (
                    imports_export_module
                    or (isinstance(node, ast.Name) and node.id == export)
                ) or (
                    isinstance(node, ast.Attribute) and node.attr == export
                ) or (
                    isinstance(node, ast.Constant) and node.value == export
                ) or (
                    isinstance(node, ast.keyword) and node.arg == export
                ) or (
                    isinstance(node, ast.alias)
                    and (
                        node.name.split(".")[-1] == export
                        or node.asname == export
                    )
                )
                if references_export:
                    raise DeletedAuthorityViolation(
                        "deleted legacy Directory package export was reintroduced in "
                        f"{relative_path}: {export}"
                    )


def _assert_tests_do_not_import_deleted_directory_authorities(
    backend_root: Path,
) -> None:
    tests_root = backend_root / "tests"
    if not tests_root.is_dir():
        return

    for source_path in sorted(tests_root.rglob("*.py")):
        relative_path = source_path.relative_to(backend_root)
        if relative_path == DELETED_AUTHORITY_GUARD_TEST:
            continue

        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        imported_identities: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_identities.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_identities.append(node.module)
                imported_identities.extend(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )

        for imported_identity in imported_identities:
            for deleted_identity in LEGACY_DIRECTORY_DOTTED_IMPORT_IDENTITIES:
                if imported_identity == deleted_identity or imported_identity.startswith(
                    f"{deleted_identity}."
                ):
                    raise DeletedAuthorityViolation(
                        "test imports deleted legacy Directory authority: "
                        f"{relative_path} -> {deleted_identity}"
                    )


def _assert_deleted_legacy_focus_authorities(backend_root: Path) -> None:
    for identity in LEGACY_FOCUS_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy Focus authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy Focus authority package was reintroduced: {identity}"
            )


def _assert_deleted_legacy_focus_dao_export(backend_root: Path) -> None:
    _assert_deleted_dao_package_exports(
        backend_root,
        authority="Focus",
        exports=(LEGACY_FOCUS_DAO_EXPORT,),
    )


def _assert_tests_do_not_import_deleted_focus_authorities(
    backend_root: Path,
) -> None:
    tests_root = backend_root / "tests"
    if not tests_root.is_dir():
        return

    for source_path in sorted(tests_root.rglob("*.py")):
        relative_path = source_path.relative_to(backend_root)
        if relative_path == DELETED_AUTHORITY_GUARD_TEST:
            continue

        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        imported_identities: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_identities.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_identities.append(node.module)
                imported_identities.extend(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )

        for imported_identity in imported_identities:
            for deleted_identity in LEGACY_FOCUS_DOTTED_IMPORT_IDENTITIES:
                if imported_identity == deleted_identity or imported_identity.startswith(
                    f"{deleted_identity}."
                ):
                    raise DeletedAuthorityViolation(
                        "test imports deleted legacy Focus authority: "
                        f"{relative_path} -> {deleted_identity}"
                    )


def _assert_deleted_legacy_notification_authorities(backend_root: Path) -> None:
    for identity in LEGACY_NOTIFICATION_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Notification authority module was reintroduced: "
                f"{identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Notification authority package was reintroduced: "
                f"{identity}"
            )


def _assert_tests_do_not_reference_deleted_notification_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Notification",
        deleted_identities=LEGACY_NOTIFICATION_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_tests_do_not_reference_deleted_authorities(
    backend_root: Path,
    *,
    authority: str,
    deleted_identities: tuple[str, ...],
    excluded_test_paths: frozenset[Path] = frozenset(),
) -> None:
    tests_root = backend_root / "tests"
    if not tests_root.is_dir():
        return

    for source_path in sorted(tests_root.rglob("*.py")):
        relative_path = source_path.relative_to(backend_root)
        if (
            relative_path == DELETED_AUTHORITY_GUARD_TEST
            or relative_path in excluded_test_paths
        ):
            continue

        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        referenced_identities: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                referenced_identities.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                referenced_identities.append(node.module)
                referenced_identities.extend(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                referenced_identities.append(node.value)

        for referenced_identity in referenced_identities:
            for deleted_identity in deleted_identities:
                if referenced_identity == deleted_identity or referenced_identity.startswith(
                    f"{deleted_identity}."
                ):
                    raise DeletedAuthorityViolation(
                        f"test references deleted legacy {authority} authority: "
                        f"{relative_path} -> {deleted_identity}"
                    )


def _assert_deleted_legacy_published_page_authorities(backend_root: Path) -> None:
    for identity in LEGACY_PUBLISHED_PAGE_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Published Page authority module was reintroduced: "
                f"{identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Published Page authority package was reintroduced: "
                f"{identity}"
            )


def _assert_tests_do_not_reference_deleted_published_page_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Published Page",
        deleted_identities=LEGACY_PUBLISHED_PAGE_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_deleted_legacy_plaza_authorities(backend_root: Path) -> None:
    for identity in LEGACY_PLAZA_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Plaza authority module was reintroduced: "
                f"{identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Plaza authority package was reintroduced: "
                f"{identity}"
            )


def _assert_tests_do_not_reference_deleted_plaza_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Plaza",
        deleted_identities=LEGACY_PLAZA_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_deleted_legacy_agent_template_authorities(backend_root: Path) -> None:
    for identity in LEGACY_AGENT_TEMPLATE_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Agent Template authority module was reintroduced: "
                f"{identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Agent Template authority package was reintroduced: "
                f"{identity}"
            )


def _assert_deleted_legacy_agent_template_dao_export(backend_root: Path) -> None:
    _assert_deleted_dao_package_exports(
        backend_root,
        authority="Agent Template",
        exports=(LEGACY_AGENT_TEMPLATE_DAO_EXPORT,),
    )


def _assert_tests_do_not_reference_deleted_agent_template_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Agent Template",
        deleted_identities=LEGACY_AGENT_TEMPLATE_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_deleted_legacy_agentbay_authorities(backend_root: Path) -> None:
    for identity in LEGACY_AGENTBAY_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy AgentBay authority module was reintroduced: "
                f"{identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy AgentBay authority package was reintroduced: "
                f"{identity}"
            )


def _assert_tests_do_not_reference_deleted_agentbay_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="AgentBay",
        deleted_identities=LEGACY_AGENTBAY_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_deleted_legacy_tenant_knowledge_publication_authority(
    backend_root: Path,
) -> None:
    for identity in LEGACY_TENANT_KNOWLEDGE_PUBLICATION_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Tenant Knowledge publication authority module was "
                f"reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Tenant Knowledge publication authority package was "
                f"reintroduced: {identity}"
            )


def _assert_deleted_legacy_session_substrate_authorities(
    backend_root: Path,
) -> None:
    for identity in LEGACY_SESSION_SUBSTRATE_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Session substrate authority module was "
                f"reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Session substrate authority package was "
                f"reintroduced: {identity}"
            )


def _assert_deleted_legacy_session_substrate_dao_exports(
    backend_root: Path,
) -> None:
    _assert_deleted_dao_package_exports(
        backend_root,
        authority="Session substrate",
        exports=LEGACY_SESSION_SUBSTRATE_DAO_EXPORTS,
    )


def _assert_model_schema_trees_do_not_restore_session_substrate_definitions(
    backend_root: Path,
) -> None:
    source_paths: set[Path] = set()
    for relative_root in LEGACY_SESSION_SUBSTRATE_DEFINITION_ROOTS:
        source_root = backend_root / relative_root
        if not source_root.is_dir():
            continue
        source_paths.update(source_root.rglob("*.py"))

    for source_path in sorted(source_paths):
        relative_path = source_path.relative_to(backend_root)
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        restored_facts = sorted(
            LEGACY_SESSION_SUBSTRATE_FORBIDDEN_DEFINITIONS
            & _source_contract_facts(tree)
        )
        if restored_facts:
            raise DeletedAuthorityViolation(
                "model or schema restores legacy Session substrate definitions: "
                f"{relative_path} -> {', '.join(restored_facts)}"
            )


def _assert_deleted_legacy_group_participant_authorities(
    backend_root: Path,
) -> None:
    for identity in LEGACY_GROUP_PARTICIPANT_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Group/Participant authority module was "
                f"reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Group/Participant authority package was "
                f"reintroduced: {identity}"
            )


def _assert_deleted_legacy_group_participant_dao_exports(
    backend_root: Path,
) -> None:
    _assert_deleted_dao_package_exports(
        backend_root,
        authority="Group/Participant",
        exports=LEGACY_GROUP_PARTICIPANT_DAO_EXPORTS,
    )


def _assert_tests_do_not_reference_deleted_group_participant_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Group/Participant",
        deleted_identities=LEGACY_GROUP_PARTICIPANT_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_deleted_legacy_schedule_authorities(backend_root: Path) -> None:
    for identity in LEGACY_SCHEDULE_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Schedule authority module was reintroduced: "
                f"{identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Schedule authority package was reintroduced: "
                f"{identity}"
            )


def _assert_tests_do_not_reference_deleted_schedule_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Schedule",
        deleted_identities=LEGACY_SCHEDULE_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_application_does_not_restore_schedule_definitions(
    backend_root: Path,
) -> None:
    source_paths: set[Path] = set()
    for relative_root in LEGACY_SCHEDULE_DEFINITION_ROOTS:
        source_root = backend_root / relative_root
        if source_root.is_dir():
            source_paths.update(source_root.rglob("*.py"))

    for source_path in sorted(source_paths):
        relative_path = source_path.relative_to(backend_root)
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        restored_facts = sorted(
            LEGACY_SCHEDULE_FORBIDDEN_DEFINITIONS & _source_contract_facts(tree)
        )
        if restored_facts:
            raise DeletedAuthorityViolation(
                "application source restores legacy Schedule definitions: "
                f"{relative_path} -> {', '.join(restored_facts)}"
            )


def _assert_deleted_legacy_trigger_webhook_authorities(
    backend_root: Path,
) -> None:
    for identity in LEGACY_TRIGGER_WEBHOOK_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Trigger/Webhook authority module was reintroduced: "
                f"{identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Trigger/Webhook authority package was reintroduced: "
                f"{identity}"
            )


def _assert_deleted_legacy_trigger_dao_export(backend_root: Path) -> None:
    _assert_deleted_dao_package_exports(
        backend_root,
        authority="Trigger/Webhook",
        exports=(LEGACY_TRIGGER_DAO_EXPORT,),
    )


def _assert_tests_do_not_reference_deleted_trigger_webhook_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Trigger/Webhook",
        deleted_identities=LEGACY_TRIGGER_WEBHOOK_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_application_does_not_restore_trigger_webhook_definitions(
    backend_root: Path,
) -> None:
    source_paths: set[Path] = set()
    for relative_root in LEGACY_TRIGGER_WEBHOOK_DEFINITION_ROOTS:
        source_root = backend_root / relative_root
        if source_root.is_dir():
            source_paths.update(source_root.rglob("*.py"))

    for source_path in sorted(source_paths):
        relative_path = source_path.relative_to(backend_root)
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        facts = LEGACY_TRIGGER_WEBHOOK_FORBIDDEN_DEFINITIONS & _source_contract_facts(tree)
        if relative_path == Path("app/modules/trigger/models.py"):
            facts -= {"table:agent_triggers"}
        restored_facts = sorted(facts)
        if restored_facts:
            raise DeletedAuthorityViolation(
                "application source restores legacy Trigger/Webhook definitions: "
                f"{relative_path} -> {', '.join(restored_facts)}"
            )


def _assert_deleted_legacy_heartbeat_authorities(backend_root: Path) -> None:
    for identity in LEGACY_HEARTBEAT_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Heartbeat authority module was reintroduced: "
                f"{identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Heartbeat authority package was reintroduced: "
                f"{identity}"
            )

    template_path = backend_root / LEGACY_HEARTBEAT_TEMPLATE_PATH
    if template_path.exists():
        raise DeletedAuthorityViolation(
            "deleted legacy Heartbeat template path was reintroduced: "
            f"{LEGACY_HEARTBEAT_TEMPLATE_PATH}"
        )

    sandbox_source = backend_root / LEGACY_HEARTBEAT_SANDBOX_SOURCE
    if not sandbox_source.is_file():
        return
    tree = ast.parse(
        sandbox_source.read_text(encoding="utf-8"),
        filename=str(sandbox_source),
    )
    restored_paths = sorted(
        {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in LEGACY_HEARTBEAT_SANDBOX_FORBIDDEN_PATHS
        }
    )
    if restored_paths:
        raise DeletedAuthorityViolation(
            "Sandbox recognizes the deleted legacy Heartbeat root path: "
            f"{', '.join(restored_paths)}"
        )


def _assert_tests_do_not_reference_deleted_heartbeat_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Heartbeat",
        deleted_identities=LEGACY_HEARTBEAT_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_deleted_legacy_workspace_authorities(backend_root: Path) -> None:
    if len(LEGACY_WORKSPACE_IMPORT_IDENTITIES) != 7:
        raise DeletedAuthorityViolation(
            "legacy Workspace authority inventory must contain exactly 7 identities"
        )
    for identity in LEGACY_WORKSPACE_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy Workspace authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy Workspace authority package was reintroduced: {identity}"
            )


def _assert_tests_do_not_reference_deleted_workspace_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Workspace",
        deleted_identities=LEGACY_WORKSPACE_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_application_does_not_restore_workspace_definitions(
    backend_root: Path,
) -> None:
    app_root = backend_root / "app"
    if not app_root.is_dir():
        return
    for source_path in sorted(app_root.rglob("*.py")):
        relative_path = source_path.relative_to(backend_root)
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        restored_facts = sorted(
            LEGACY_WORKSPACE_FORBIDDEN_DEFINITIONS & _source_contract_facts(tree)
        )
        if restored_facts:
            raise DeletedAuthorityViolation(
                "application source restores legacy Workspace definitions: "
                f"{relative_path} -> {', '.join(restored_facts)}"
            )


def _assert_sandbox_has_no_legacy_revision_branch(backend_root: Path) -> None:
    source_path = backend_root / "app/services/sandbox/local/subprocess_backend.py"
    if not source_path.is_file():
        return
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    arguments = {
        argument.arg
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
    }
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    facts = _source_contract_facts(tree)
    restored: list[str] = []
    if "record_revisions" in arguments:
        restored.append("argument:record_revisions")
    for identity in ("app.database", "app.services.workspace_collaboration"):
        if identity in imports:
            restored.append(f"import:{identity}")
    for reference in (
        "reference:delete_workspace_file",
        "reference:write_workspace_file",
    ):
        if reference in facts:
            restored.append(reference)
    if restored:
        raise DeletedAuthorityViolation(
            "Sandbox restores the dead Workspace revision branch: "
            + ", ".join(sorted(restored))
        )


def _assert_deleted_legacy_a2a_authorities(backend_root: Path) -> None:
    if len(LEGACY_A2A_IMPORT_IDENTITIES) != 1:
        raise DeletedAuthorityViolation(
            "legacy A2A authority inventory must contain exactly 1 identity"
        )
    for identity in LEGACY_A2A_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy A2A authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy A2A authority package was reintroduced: {identity}"
            )


def _assert_tests_do_not_reference_deleted_a2a_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="A2A",
        deleted_identities=LEGACY_A2A_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_deleted_legacy_advanced_api(backend_root: Path) -> None:
    module = (backend_root / LEGACY_ADVANCED_API_IMPORT_IDENTITY).with_suffix(".py")
    package = backend_root / LEGACY_ADVANCED_API_IMPORT_IDENTITY
    if module.is_file():
        raise DeletedAuthorityViolation(
            "deleted legacy advanced API module was reintroduced: "
            f"{LEGACY_ADVANCED_API_IMPORT_IDENTITY}"
        )
    if package.is_dir():
        raise DeletedAuthorityViolation(
            "deleted legacy advanced API package was reintroduced: "
            f"{LEGACY_ADVANCED_API_IMPORT_IDENTITY}"
        )


def _assert_tests_do_not_reference_deleted_advanced_api(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="advanced API",
        deleted_identities=(LEGACY_ADVANCED_API_DOTTED_IMPORT_IDENTITY,),
    )


def _assert_application_apis_do_not_restore_legacy_advanced_facts(
    backend_root: Path,
) -> None:
    api_root = backend_root / "app/api"
    if not api_root.is_dir():
        return
    for source_path in sorted(api_root.rglob("*.py")):
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        restored_facts = sorted(
            LEGACY_ADVANCED_API_FORBIDDEN_FACTS & _source_contract_facts(tree)
        )
        if restored_facts:
            relative_path = source_path.relative_to(backend_root)
            raise DeletedAuthorityViolation(
                "application API restores legacy advanced facts: "
                f"{relative_path} -> {', '.join(restored_facts)}"
            )


def _assert_deleted_legacy_activity_api(backend_root: Path) -> None:
    identity = LEGACY_ACTIVITY_API_IMPORT_IDENTITY
    if (backend_root / identity).with_suffix(".py").is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy Activity API module was reintroduced: {identity}"
        )
    if (backend_root / identity).is_dir():
        raise DeletedAuthorityViolation(
            f"deleted legacy Activity API package was reintroduced: {identity}"
        )


def _assert_tests_do_not_reference_deleted_activity_api(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Activity API",
        deleted_identities=(LEGACY_ACTIVITY_API_DOTTED_IMPORT_IDENTITY,),
    )


def _assert_application_apis_do_not_restore_legacy_activity_facts(
    backend_root: Path,
) -> None:
    api_root = backend_root / "app/api"
    if not api_root.is_dir():
        return
    for source_path in sorted(api_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        restored = sorted(
            LEGACY_ACTIVITY_API_FORBIDDEN_FACTS & _source_contract_facts(tree)
        )
        if restored:
            raise DeletedAuthorityViolation(
                "application API restores legacy Activity transport facts: "
                f"{source_path.relative_to(backend_root)} -> {', '.join(restored)}"
            )


def _assert_deleted_legacy_messages_api(backend_root: Path) -> None:
    identity = LEGACY_MESSAGES_API_IMPORT_IDENTITY
    if (backend_root / identity).with_suffix(".py").is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy Messages API module was reintroduced: {identity}"
        )
    if (backend_root / identity).is_dir():
        raise DeletedAuthorityViolation(
            f"deleted legacy Messages API package was reintroduced: {identity}"
        )


def _assert_tests_do_not_reference_deleted_messages_api(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Messages API",
        deleted_identities=(LEGACY_MESSAGES_API_DOTTED_IMPORT_IDENTITY,),
    )


def _assert_application_apis_do_not_restore_legacy_messages_facts(
    backend_root: Path,
) -> None:
    api_root = backend_root / "app/api"
    if not api_root.is_dir():
        return
    for source_path in sorted(api_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        restored = sorted(
            LEGACY_MESSAGES_API_FORBIDDEN_FACTS & _source_contract_facts(tree)
        )
        if restored:
            raise DeletedAuthorityViolation(
                "application API restores legacy Messages transport facts: "
                f"{source_path.relative_to(backend_root)} -> {', '.join(restored)}"
            )


def _assert_deleted_legacy_admin_api(backend_root: Path) -> None:
    identity = LEGACY_ADMIN_API_IMPORT_IDENTITY
    if (backend_root / identity).with_suffix(".py").is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy Admin API module was reintroduced: {identity}"
        )
    if (backend_root / identity).is_dir():
        raise DeletedAuthorityViolation(
            f"deleted legacy Admin API package was reintroduced: {identity}"
        )


def _assert_tests_do_not_reference_deleted_admin_api(backend_root: Path) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Admin API",
        deleted_identities=(LEGACY_ADMIN_API_DOTTED_IMPORT_IDENTITY,),
    )


def _assert_application_apis_do_not_restore_legacy_admin_facts(
    backend_root: Path,
) -> None:
    api_root = backend_root / "app/api"
    if not api_root.is_dir():
        return
    for source_path in sorted(api_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        restored = sorted(
            LEGACY_ADMIN_API_FORBIDDEN_FACTS & _source_contract_facts(tree)
        )
        if restored:
            raise DeletedAuthorityViolation(
                "application API restores legacy Platform Administration facts: "
                f"{source_path.relative_to(backend_root)} -> {', '.join(restored)}"
            )


def _assert_deleted_legacy_enterprise_transport(backend_root: Path) -> None:
    for identity in LEGACY_ENTERPRISE_TRANSPORT_IMPORT_IDENTITIES:
        if (backend_root / identity).with_suffix(".py").is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Enterprise transport module was reintroduced: "
                f"{identity}"
            )
        if (backend_root / identity).is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Enterprise transport package was reintroduced: "
                f"{identity}"
            )
    for test_path in LEGACY_ENTERPRISE_TRANSPORT_TEST_PATHS:
        if (backend_root / test_path).is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy Enterprise transport test was reintroduced: {test_path}"
            )


def _assert_tests_do_not_reference_deleted_enterprise_transport(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Enterprise transport",
        deleted_identities=LEGACY_ENTERPRISE_TRANSPORT_DOTTED_IDENTITIES,
    )


def _assert_deleted_observability_audit_services(backend_root: Path) -> None:
    for identity in LEGACY_OBSERVABILITY_AUDIT_SERVICE_IDENTITIES:
        if (backend_root / identity).with_suffix(".py").is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy observability/audit service module was reintroduced: {identity}"
            )
        if (backend_root / identity).is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy observability/audit service package was reintroduced: {identity}"
            )


def _assert_tests_do_not_reference_deleted_observability_audit_services(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="observability/audit service",
        deleted_identities=LEGACY_OBSERVABILITY_AUDIT_SERVICE_DOTTED_IDENTITIES,
    )


def _assert_deleted_platform_service(backend_root: Path) -> None:
    identity = LEGACY_PLATFORM_SERVICE_IDENTITY
    if (backend_root / identity).with_suffix(".py").is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy Platform service module was reintroduced: {identity}"
        )
    if (backend_root / identity).is_dir():
        raise DeletedAuthorityViolation(
            f"deleted legacy Platform service package was reintroduced: {identity}"
        )


def _assert_tests_do_not_reference_deleted_platform_service(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Platform service",
        deleted_identities=(LEGACY_PLATFORM_SERVICE_DOTTED_IDENTITY,),
    )


def _assert_deleted_quota_guard(backend_root: Path) -> None:
    identity = LEGACY_QUOTA_GUARD_IDENTITY
    if (backend_root / identity).with_suffix(".py").is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy quota guard module was reintroduced: {identity}"
        )
    if (backend_root / identity).is_dir():
        raise DeletedAuthorityViolation(
            f"deleted legacy quota guard package was reintroduced: {identity}"
        )


def _assert_tests_do_not_reference_deleted_quota_guard(backend_root: Path) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="quota guard",
        deleted_identities=(LEGACY_QUOTA_GUARD_DOTTED_IDENTITY,),
    )


def _assert_deleted_realtime_services(backend_root: Path) -> None:
    for identity in LEGACY_REALTIME_SERVICE_IDENTITIES:
        if (backend_root / identity).with_suffix(".py").is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy Realtime service module was reintroduced: {identity}"
            )
        if (backend_root / identity).is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy Realtime service package was reintroduced: {identity}"
            )


def _assert_tests_do_not_reference_deleted_realtime_services(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Realtime service",
        deleted_identities=LEGACY_REALTIME_SERVICE_DOTTED_IDENTITIES,
    )


def _assert_deleted_resource_discovery(backend_root: Path) -> None:
    identity = LEGACY_RESOURCE_DISCOVERY_IDENTITY
    if (backend_root / identity).with_suffix(".py").is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy resource discovery module was reintroduced: {identity}"
        )
    if (backend_root / identity).is_dir():
        raise DeletedAuthorityViolation(
            f"deleted legacy resource discovery package was reintroduced: {identity}"
        )


def _assert_tests_do_not_reference_deleted_resource_discovery(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="resource discovery",
        deleted_identities=(LEGACY_RESOURCE_DISCOVERY_DOTTED_IDENTITY,),
    )


def _assert_deleted_system_email_service(backend_root: Path) -> None:
    identity = LEGACY_SYSTEM_EMAIL_SERVICE_IDENTITY
    if (backend_root / identity).with_suffix(".py").is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy System Email service module was reintroduced: {identity}"
        )
    if (backend_root / identity).is_dir():
        raise DeletedAuthorityViolation(
            f"deleted legacy System Email service package was reintroduced: {identity}"
        )
    if (backend_root / LEGACY_SYSTEM_EMAIL_TEST_PATH).is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy System Email test was reintroduced: {LEGACY_SYSTEM_EMAIL_TEST_PATH}"
        )


def _assert_tests_do_not_reference_deleted_system_email_service(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="System Email service",
        deleted_identities=(LEGACY_SYSTEM_EMAIL_SERVICE_DOTTED_IDENTITY,),
    )


def _assert_deleted_vision_maintenance_authorities(backend_root: Path) -> None:
    for identity in LEGACY_VISION_MAINTENANCE_IDENTITIES:
        if (backend_root / identity).with_suffix(".py").is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy vision/maintenance module was reintroduced: {identity}"
            )
        if (backend_root / identity).is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy vision/maintenance package was reintroduced: {identity}"
            )


def _assert_tests_do_not_reference_deleted_vision_maintenance_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="vision/maintenance",
        deleted_identities=LEGACY_VISION_MAINTENANCE_DOTTED_IDENTITIES,
    )


def _assert_deleted_orphan_maintenance_authorities(backend_root: Path) -> None:
    for identity in LEGACY_ORPHAN_MAINTENANCE_IDENTITIES:
        if (backend_root / identity).with_suffix(".py").is_file():
            raise DeletedAuthorityViolation(
                f"deleted orphan maintenance module was reintroduced: {identity}"
            )
        if (backend_root / identity).is_dir():
            raise DeletedAuthorityViolation(
                f"deleted orphan maintenance package was reintroduced: {identity}"
            )


def _assert_tests_do_not_reference_deleted_orphan_maintenance(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="orphan maintenance",
        deleted_identities=LEGACY_ORPHAN_MAINTENANCE_DOTTED_IDENTITIES,
    )


def _assert_no_orphan_maintenance_executable_invocations(
    repository_root: Path,
) -> None:
    def executable_config_values(value: object) -> list[str]:
        commands: list[str] = []
        if isinstance(value, list):
            for item in value:
                commands.extend(executable_config_values(item))
            return commands
        if not isinstance(value, dict):
            return commands
        for raw_key, nested in value.items():
            key = str(raw_key).casefold()
            if key in {"command", "run", "script"}:
                if isinstance(nested, str):
                    commands.append(nested)
                elif isinstance(nested, list) and all(
                    isinstance(item, str) for item in nested
                ):
                    commands.append(shlex.join(nested))
                continue
            if key == "scripts" and isinstance(nested, dict):
                commands.extend(
                    item
                    for item in nested.values()
                    if isinstance(item, str)
                )
                continue
            commands.extend(executable_config_values(nested))
        return commands

    def restored_invocations(command_line: str) -> list[str]:
        try:
            tokens = _shell_tokens(command_line)
        except ValueError:
            tokens = re.findall(r"-m|[A-Za-z0-9_./:-]+", command_line)
        if not tokens:
            return []
        restored: set[str] = set()

        segments: list[list[str]] = [[]]
        for token in tokens:
            if token in {"&", "&&", ";", "|", "||"}:
                if segments[-1]:
                    segments.append([])
                continue
            segments[-1].append(token)

        forbidden_files = {
            "backfill_chat_message_tenant_id.py",
            "remove_old_tool.py",
            "update_schema.py",
        }
        uv_options_with_value = {
            "--allow-insecure-host",
            "--cache-dir",
            "--color",
            "--config-file",
            "--config-setting",
            "--config-settings-package",
            "--default-index",
            "--directory",
            "--env-file",
            "--exclude-newer",
            "--exclude-newer-package",
            "--extra",
            "--extra-index-url",
            "--find-links",
            "--fork-strategy",
            "--group",
            "--index",
            "--index-strategy",
            "--index-url",
            "--keyring-provider",
            "--link-mode",
            "--no-binary-package",
            "--no-build-isolation-package",
            "--no-build-package",
            "--no-extra",
            "--no-group",
            "--only-group",
            "--package",
            "--prerelease",
            "--project",
            "--python",
            "--python-platform",
            "--refresh-package",
            "--reinstall-package",
            "--resolution",
            "--upgrade-package",
            "--with",
            "--with-editable",
            "--with-requirements",
            "-C",
            "-P",
            "-f",
            "-i",
            "-p",
            "-w",
        }
        uv_flag_options = {
            "--active",
            "--all-extras",
            "--all-groups",
            "--all-packages",
            "--compile-bytecode",
            "--exact",
            "--frozen",
            "--isolated",
            "--locked",
            "--managed-python",
            "--native-tls",
            "--no-binary",
            "--no-build",
            "--no-build-isolation",
            "--no-cache",
            "--no-config",
            "--no-default-groups",
            "--no-dev",
            "--no-editable",
            "--no-env-file",
            "--no-index",
            "--no-managed-python",
            "--no-progress",
            "--no-project",
            "--no-python-downloads",
            "--no-sources",
            "--no-sync",
            "--offline",
            "--only-dev",
            "--quiet",
            "--refresh",
            "--reinstall",
            "--upgrade",
            "--verbose",
            "-U",
            "-n",
            "-q",
            "-v",
        }
        python_options_with_value = {"--check-hash-based-pycs", "-W", "-X"}
        python_long_flags = {
            "--help",
            "--help-all",
            "--help-env",
            "--help-xoptions",
            "--version",
        }
        wrappers = {"!", "command", "do", "env", "exec", "export", "if", "then"}

        for segment in segments:
            command_tokens = list(segment)
            while command_tokens and (
                _SHELL_ASSIGNMENT.match(command_tokens[0])
                or command_tokens[0] in wrappers
            ):
                command_tokens.pop(0)
            if not command_tokens:
                continue

            segment_has_legacy_reference = any(
                Path(token.strip("[],'\"")).name in forbidden_files
                or token.strip("[],'\"").split(":", 1)[0]
                in LEGACY_ORPHAN_MAINTENANCE_ENTRYPOINTS
                for token in command_tokens
            )
            if not segment_has_legacy_reference:
                continue

            if Path(command_tokens[0]).name == "uv":
                try:
                    run_index = command_tokens.index("run")
                except ValueError:
                    continue
                command_tokens = command_tokens[run_index + 1 :]
                while command_tokens and command_tokens[0].startswith("-"):
                    option = command_tokens.pop(0)
                    option_name = option.split("=", 1)[0]
                    if option in {"-m", "--module"}:
                        if (
                            command_tokens
                            and command_tokens[0]
                            in LEGACY_ORPHAN_MAINTENANCE_ENTRYPOINTS
                        ):
                            restored.add(f"-m {command_tokens[0]}")
                        command_tokens = []
                        break
                    if option in {"-s", "--gui-script", "--script"}:
                        if (
                            command_tokens
                            and Path(command_tokens[0]).name in forbidden_files
                        ):
                            restored.add(Path(command_tokens[0]).name)
                        command_tokens = []
                        break
                    if option_name in uv_flag_options or re.fullmatch(
                        r"-(?:q+|v+)", option
                    ):
                        continue
                    if option_name in uv_options_with_value:
                        if "=" not in option:
                            if not command_tokens:
                                restored.add(f"unparsed uv option {option}")
                                break
                            command_tokens.pop(0)
                        continue
                    restored.add(f"unparsed uv option {option}")
                    command_tokens = []
                    break
            if not command_tokens:
                continue

            command_token = command_tokens[0].strip("[],'\"")
            command = Path(command_token).name
            command_entrypoint = command_token.split(":", 1)[0]
            if command_entrypoint in LEGACY_ORPHAN_MAINTENANCE_ENTRYPOINTS:
                restored.add(command_token)
                continue
            if command in forbidden_files:
                restored.add(command)
                continue

            is_python = command.startswith("python")
            is_shell = command in {"bash", "sh"}
            if not is_python and not is_shell:
                continue

            arguments = command_tokens[1:]
            while arguments and arguments[0].startswith("-"):
                option = arguments.pop(0)
                if option == "--":
                    break
                if is_python and option == "-m":
                    if (
                        arguments
                        and arguments[0] in LEGACY_ORPHAN_MAINTENANCE_ENTRYPOINTS
                    ):
                        restored.add(f"-m {arguments[0]}")
                    arguments = []
                    break
                if option == "-c":
                    if arguments and is_shell:
                        restored.update(restored_invocations(arguments[0]))
                    elif arguments and any(
                        legacy in arguments[0]
                        for legacy in LEGACY_ORPHAN_MAINTENANCE_ENTRYPOINTS
                    ):
                        restored.add("python -c legacy maintenance reference")
                    arguments = []
                    break
                if is_python and (
                    option in python_options_with_value
                    or option.startswith(("-W", "-X"))
                ):
                    if option in python_options_with_value:
                        if not arguments:
                            restored.add(f"unparsed Python option {option}")
                            break
                        arguments.pop(0)
                    continue
                if is_python and (
                    option in python_long_flags
                    or re.fullmatch(r"-[bBdEhiIOPqRsuUvVx]+", option)
                ):
                    continue
                if is_shell and option in {"-e", "-f", "-n", "-u", "-v", "-x"}:
                    continue
                restored.add(f"unparsed interpreter option {option}")
                arguments = []
                break

            script = next(
                (
                    token.strip("[],'\"")
                    for token in arguments
                    if not token.startswith("-")
                ),
                None,
            )
            if script is not None and Path(script).name in forbidden_files:
                restored.add(Path(script).name)
        return sorted(restored)

    def yaml_command_values(source: str) -> list[str]:
        try:
            return executable_config_values(yaml.safe_load(source))
        except yaml.YAMLError:
            commands: list[str] = []
            lines = source.splitlines()
            field_pattern = re.compile(
                r"^(?P<indent>\s*)(?:-\s*)?(?:command|run|script)\s*:\s*(?P<value>.*)$"
            )
            index = 0
            while index < len(lines):
                match = field_pattern.match(lines[index])
                if match is None:
                    index += 1
                    continue
                base_indent = len(match.group("indent"))
                value = match.group("value").strip()
                nested: list[str] = []
                index += 1
                while index < len(lines):
                    candidate = lines[index]
                    if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= base_indent:
                        break
                    nested.append(candidate.strip())
                    index += 1
                commands.append(" ".join(([value] if value else []) + nested))
            return commands

    ignored_parts = {".git", ".venv", "artifacts", "node_modules"}
    for source_path in sorted(repository_root.rglob("*")):
        if not source_path.is_file():
            continue
        if source_path.suffix not in LEGACY_MAINTENANCE_EXECUTABLE_SUFFIXES:
            continue
        if ignored_parts & set(source_path.parts):
            continue
        source = source_path.read_text(encoding="utf-8")
        if source_path.suffix == ".sh":
            command_values = source.replace("\\\n", " ").splitlines()
        elif source_path.suffix == ".toml":
            command_values = executable_config_values(tomllib.loads(source))
        else:
            command_values = yaml_command_values(source)
        for command_index, command_value in enumerate(command_values, start=1):
            restored = restored_invocations(command_value)
            if restored:
                raise DeletedAuthorityViolation(
                    "shell or YAML restores orphan maintenance invocation: "
                    f"{source_path.relative_to(repository_root)}:{command_index} -> "
                    f"{', '.join(restored)}"
                )


def _assert_deleted_observability_audit_persistence(backend_root: Path) -> None:
    for identity in LEGACY_OBSERVABILITY_AUDIT_PERSISTENCE_IDENTITIES:
        if (backend_root / identity).with_suffix(".py").is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy observability/audit persistence module was reintroduced: {identity}"
            )
        if (backend_root / identity).is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy observability/audit persistence package was reintroduced: {identity}"
            )


def _assert_deleted_observability_audit_dao_exports(backend_root: Path) -> None:
    _assert_deleted_dao_package_exports(
        backend_root,
        authority="observability/audit",
        exports=LEGACY_OBSERVABILITY_AUDIT_DAO_EXPORTS,
    )


def _assert_tests_do_not_reference_deleted_observability_audit_persistence(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="observability/audit persistence",
        deleted_identities=LEGACY_OBSERVABILITY_AUDIT_PERSISTENCE_DOTTED_IDENTITIES,
    )


def _assert_application_does_not_restore_observability_audit_facts(
    backend_root: Path,
) -> None:
    app_root = backend_root / "app"
    if not app_root.is_dir():
        return
    for source_path in sorted(app_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        restored = sorted(
            LEGACY_OBSERVABILITY_AUDIT_FORBIDDEN_FACTS & _source_contract_facts(tree)
        )
        if restored:
            raise DeletedAuthorityViolation(
                "application restores legacy observability/audit persistence facts: "
                f"{source_path.relative_to(backend_root)} -> {', '.join(restored)}"
            )


def _assert_deleted_run_setting_persistence(backend_root: Path) -> None:
    for identity in LEGACY_RUN_SETTING_PERSISTENCE_IDENTITIES:
        if (backend_root / identity).with_suffix(".py").is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy Run/Settings persistence module was reintroduced: {identity}"
            )
        if (backend_root / identity).is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy Run/Settings persistence package was reintroduced: {identity}"
            )


def _assert_deleted_run_setting_dao_exports(backend_root: Path) -> None:
    _assert_deleted_dao_package_exports(
        backend_root,
        authority="Run/Settings",
        exports=LEGACY_RUN_SETTING_DAO_EXPORTS,
    )


def _assert_tests_do_not_reference_deleted_run_setting_persistence(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Run/Settings persistence",
        deleted_identities=LEGACY_RUN_SETTING_PERSISTENCE_DOTTED_IDENTITIES,
    )


def _assert_application_does_not_restore_run_setting_facts(
    backend_root: Path,
) -> None:
    app_root = backend_root / "app"
    if not app_root.is_dir():
        return
    for source_path in sorted(app_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        restored = sorted(
            LEGACY_RUN_SETTING_FORBIDDEN_FACTS & _source_contract_facts(tree)
        )
        if restored:
            raise DeletedAuthorityViolation(
                "application restores legacy Run/Settings persistence facts: "
                f"{source_path.relative_to(backend_root)} -> {', '.join(restored)}"
            )


def _assert_deleted_core_compatibility_authorities(backend_root: Path) -> None:
    for identity in LEGACY_CORE_COMPATIBILITY_IDENTITIES:
        if (backend_root / identity).with_suffix(".py").is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy core compatibility module was reintroduced: {identity}"
            )
        if (backend_root / identity).is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy core compatibility package was reintroduced: {identity}"
            )
    if (backend_root / LEGACY_ERROR_CONTRACT_TEST).is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy HTTP error-contract test was reintroduced: {LEGACY_ERROR_CONTRACT_TEST}"
        )


def _assert_legacy_base_dao_test_is_absent(backend_root: Path) -> None:
    if (backend_root / LEGACY_BASE_DAO_TEST).is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy BaseDAO test was reintroduced: {LEGACY_BASE_DAO_TEST}"
        )


def _assert_tests_do_not_reference_deleted_core_compatibility_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="core compatibility",
        deleted_identities=LEGACY_CORE_COMPATIBILITY_DOTTED_IDENTITIES,
    )


def _assert_application_does_not_restore_core_compatibility_facts(
    backend_root: Path,
) -> None:
    app_root = backend_root / "app"
    if not app_root.is_dir():
        return
    for source_path in sorted(app_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        restored = sorted(
            LEGACY_CORE_COMPATIBILITY_FORBIDDEN_FACTS
            & _source_contract_facts(tree)
        )
        if restored:
            raise DeletedAuthorityViolation(
                "application restores legacy core compatibility facts: "
                f"{source_path.relative_to(backend_root)} -> {', '.join(restored)}"
            )


def _assert_deleted_legacy_logging_config_authority(backend_root: Path) -> None:
    identity = LEGACY_LOGGING_CONFIG_IDENTITY
    if (backend_root / identity).with_suffix(".py").is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy logging configuration module was reintroduced: {identity}"
        )
    if (backend_root / identity).is_dir():
        raise DeletedAuthorityViolation(
            f"deleted legacy logging configuration package was reintroduced: {identity}"
        )


def _assert_tests_do_not_reference_deleted_logging_config(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="logging configuration",
        deleted_identities=(LEGACY_LOGGING_CONFIG_DOTTED_IDENTITY,),
    )


def _assert_application_does_not_restore_logging_config_definitions(
    backend_root: Path,
) -> None:
    app_root = backend_root / "app"
    if not app_root.is_dir():
        return
    for source_path in sorted(app_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        restored = sorted(
            LEGACY_LOGGING_CONFIG_FORBIDDEN_DEFINITIONS
            & _source_contract_facts(tree)
        )
        if restored:
            raise DeletedAuthorityViolation(
                "application restores legacy logging configuration definitions: "
                f"{source_path.relative_to(backend_root)} -> {', '.join(restored)}"
            )


def _assert_deleted_legacy_security_dao_authorities(backend_root: Path) -> None:
    for identity in LEGACY_SECURITY_DAO_IDENTITIES:
        if (backend_root / identity).with_suffix(".py").is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy Security/DAO module was reintroduced: {identity}"
            )
        if (backend_root / identity).is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy Security/DAO package was reintroduced: {identity}"
            )


def _assert_target_dao_package_is_empty(backend_root: Path) -> None:
    package_init = backend_root / DAO_PACKAGE_INIT
    if not package_init.is_file():
        raise DeletedAuthorityViolation(
            f"target DAO package initializer is missing: {DAO_PACKAGE_INIT}"
        )
    if package_init.read_text(encoding="utf-8") != "":
        raise DeletedAuthorityViolation(
            f"target DAO package initializer must remain empty: {DAO_PACKAGE_INIT}"
        )


def _assert_tests_do_not_reference_deleted_security_dao_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Security/DAO",
        deleted_identities=LEGACY_SECURITY_DAO_DOTTED_IDENTITIES,
    )


def _assert_application_does_not_restore_security_dao_definitions(
    backend_root: Path,
) -> None:
    app_root = backend_root / "app"
    if not app_root.is_dir():
        return
    for source_path in sorted(app_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        restored = sorted(
            LEGACY_SECURITY_DAO_FORBIDDEN_DEFINITIONS
            & _source_contract_facts(tree)
        )
        if restored:
            raise DeletedAuthorityViolation(
                "application restores legacy Security/DAO definitions: "
                f"{source_path.relative_to(backend_root)} -> {', '.join(restored)}"
            )


def _assert_deleted_legacy_core_events_authority(backend_root: Path) -> None:
    identity = LEGACY_CORE_EVENTS_IDENTITY
    if (backend_root / identity).with_suffix(".py").is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy core events module was reintroduced: {identity}"
        )
    if (backend_root / identity).is_dir():
        raise DeletedAuthorityViolation(
            f"deleted legacy core events package was reintroduced: {identity}"
        )


def _assert_tests_do_not_reference_deleted_core_events(backend_root: Path) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="core events",
        deleted_identities=(LEGACY_CORE_EVENTS_DOTTED_IDENTITY,),
    )


def _assert_application_does_not_restore_core_events_definitions(
    backend_root: Path,
) -> None:
    app_root = backend_root / "app"
    if not app_root.is_dir():
        return
    for source_path in sorted(app_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        restored = sorted(
            LEGACY_CORE_EVENTS_FORBIDDEN_DEFINITIONS
            & _source_contract_facts(tree)
        )
        if restored:
            raise DeletedAuthorityViolation(
                "application restores legacy core events definitions: "
                f"{source_path.relative_to(backend_root)} -> {', '.join(restored)}"
            )


def _assert_email_provider_is_decoupled_from_legacy_storage(
    backend_root: Path,
) -> None:
    source_path = backend_root / EMAIL_PROVIDER_SERVICE_SOURCE
    if not source_path.is_file():
        raise DeletedAuthorityViolation(
            f"retained email provider service is missing: {EMAIL_PROVIDER_SERVICE_SOURCE}"
        )
    tree = ast.parse(
        source_path.read_text(encoding="utf-8"),
        filename=str(source_path),
    )
    imported_identities: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_identities.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_identities.add(node.module)
            imported_identities.update(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if alias.name != "*"
            )

    forbidden_imports = sorted(
        forbidden
        for forbidden in EMAIL_PROVIDER_FORBIDDEN_STORAGE_IMPORTS
        if any(
            imported == forbidden or imported.startswith(f"{forbidden}.")
            for imported in imported_identities
        )
    )

    send_email = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "send_email"
        ),
        None,
    )
    if send_email is None:
        raise DeletedAuthorityViolation(
            "retained email provider service is missing send_email"
        )
    arguments = (
        send_email.args.posonlyargs
        + send_email.args.args
        + send_email.args.kwonlyargs
    )
    restored_fields = sorted(
        EMAIL_PROVIDER_REMOVED_SEND_FIELDS
        & {argument.arg for argument in arguments}
    )
    if forbidden_imports or restored_fields:
        details = []
        if forbidden_imports:
            details.append(f"imports={','.join(forbidden_imports)}")
        if restored_fields:
            details.append(f"send-fields={','.join(restored_fields)}")
        raise DeletedAuthorityViolation(
            "email provider service restores legacy storage coupling: "
            + "; ".join(details)
        )


def _assert_deleted_legacy_seed_bootstrap_authorities(
    backend_root: Path,
) -> None:
    seed_script = backend_root / LEGACY_SEED_SCRIPT
    if seed_script.is_file():
        raise DeletedAuthorityViolation(
            f"deleted legacy root seed script was reintroduced: {LEGACY_SEED_SCRIPT}"
        )

    bootstrap_module = (backend_root / LEGACY_BOOTSTRAP_IMPORT_IDENTITY).with_suffix(
        ".py"
    )
    bootstrap_package = backend_root / LEGACY_BOOTSTRAP_IMPORT_IDENTITY
    if bootstrap_module.is_file():
        raise DeletedAuthorityViolation(
            "deleted legacy bootstrap module was reintroduced: "
            f"{LEGACY_BOOTSTRAP_IMPORT_IDENTITY}"
        )
    if bootstrap_package.is_dir():
        raise DeletedAuthorityViolation(
            "deleted legacy bootstrap package was reintroduced: "
            f"{LEGACY_BOOTSTRAP_IMPORT_IDENTITY}"
        )


def _assert_tests_do_not_reference_deleted_bootstrap_authority(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="seed/bootstrap",
        deleted_identities=(LEGACY_BOOTSTRAP_DOTTED_IMPORT_IDENTITY,),
    )


_SHELL_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)
_LEGACY_DDL_REPAIR = re.compile(
    r"\b(?:ALTER\s+TABLE|CREATE\s+(?:UNIQUE\s+)?INDEX|UPDATE\s+\w+\s+SET)\b",
    re.IGNORECASE,
)


def _shell_tokens(line: str) -> list[str]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars="|&;<>")
    lexer.commenters = "#"
    lexer.whitespace_split = True
    return list(lexer)


def _expand_shell_assignments(value: str, assignments: dict[str, str]) -> str:
    expanded = value
    for name, assigned in assignments.items():
        expanded = expanded.replace(f"${{{name}}}", assigned)
        expanded = re.sub(rf"\${re.escape(name)}\b", assigned, expanded)
    return expanded


def _legacy_bootstrap_executable_facts(source: str) -> set[str]:
    facts: set[str] = set()
    assignments: dict[str, str] = {}
    logical_lines = source.replace("\\\n", " ").splitlines()
    for line in logical_lines:
        tokens = _shell_tokens(line)
        segments: list[list[str]] = [[]]
        for token in tokens:
            if token in {";", "&&", "||", "|", "&"}:
                if segments[-1]:
                    segments.append([])
                continue
            segments[-1].append(token)
        for segment in segments:
            expanded_tokens = [
                _expand_shell_assignments(token, assignments) for token in segment
            ]
            for token in expanded_tokens:
                assignment = _SHELL_ASSIGNMENT.match(token)
                if assignment:
                    assignments[assignment.group(1)] = assignment.group(2)
            command_tokens = [
                token
                for token in expanded_tokens
                if not _SHELL_ASSIGNMENT.match(token)
                and token not in {"if", "then", "!", "exec", "env", "export"}
            ]
            if not command_tokens:
                continue

            command = Path(command_tokens[0]).name
            has_redirection = any(
                token in {">", ">>", "<", "<<"} for token in expanded_tokens
            )
            expanded_line = " ".join(expanded_tokens)
            if command in {"echo", "printf"} and not has_redirection:
                continue

            invokes_process = command in {
                "bash",
                "python",
                "python3",
                "sh",
                "uv",
            } or command.startswith("python")
            if (
                (invokes_process or command == "seed.py")
                and re.search(r"(?:^|[\s/])seed\.py(?:\s|$)", expanded_line)
            ):
                facts.add("seed-script")
            if invokes_process and "app.scripts.bootstrap_db" in expanded_line:
                facts.add("bootstrap-module")
            if command == "alembic" or (
                invokes_process
                and re.search(r"(?:^|\s)alembic(?:\s|$)", expanded_line)
            ):
                facts.add("alembic")
            if (
                invokes_process
                and "app.scripts.setup_langgraph_checkpoints" in expanded_line
            ):
                facts.add("checkpoint-installer")
            if invokes_process and "create_all" in expanded_line:
                facts.add("create-all")
            if command in {"bash", "psql", "python", "python3", "sh"} and (
                _LEGACY_DDL_REPAIR.search(expanded_line)
            ):
                facts.add("ddl-repair")

            mutates_paths = command in {"install", "mkdir", "tee", "touch"} or (
                command in {"cat", "echo", "printf"} and has_redirection
            )
            if not mutates_paths:
                continue
            normalized_line = expanded_line.replace("\\", "/").casefold()
            materializes_agent_tree = "agent_data_dir" in normalized_line and any(
                path_segment in normalized_line
                for path_segment in ("/workspace", "/memory", "/skills")
            )
            materializes_owned_file = any(
                path in normalized_line for path in ("/memory.md", "/soul.md")
            )
            if materializes_agent_tree or materializes_owned_file:
                facts.add("workspace-materialization")
    return facts


def _assert_setup_and_startup_scripts_do_not_restore_legacy_bootstrap(
    repository_root: Path,
) -> None:
    for relative_path in SETUP_AND_STARTUP_SOURCES:
        source_path = repository_root / relative_path
        if not source_path.is_file():
            continue
        source = source_path.read_text(encoding="utf-8")
        restored_facts = sorted(_legacy_bootstrap_executable_facts(source))
        if restored_facts:
            raise DeletedAuthorityViolation(
                "setup or startup script restores legacy seed/bootstrap behavior: "
                f"{relative_path} -> {', '.join(restored_facts)}"
            )


def _assert_deleted_legacy_storage_authorities(backend_root: Path) -> None:
    if len(LEGACY_STORAGE_IMPORT_IDENTITIES) != 2:
        raise DeletedAuthorityViolation(
            "legacy storage authority inventory must contain exactly 2 identities"
        )
    for identity in LEGACY_STORAGE_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy storage authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy storage authority package was reintroduced: {identity}"
            )
    for test_path in LEGACY_STORAGE_TEST_PATHS:
        if (backend_root / test_path).is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy storage test path was reintroduced: {test_path}"
            )


def _assert_tests_do_not_reference_deleted_storage_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="storage",
        deleted_identities=LEGACY_STORAGE_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_target_object_storage_package_is_empty(backend_root: Path) -> None:
    package_init = backend_root / TARGET_OBJECT_STORAGE_PACKAGE_INIT
    if not package_init.is_file():
        raise DeletedAuthorityViolation(
            f"target object-storage package is missing: {TARGET_OBJECT_STORAGE_PACKAGE_INIT}"
        )
    if package_init.read_text(encoding="utf-8"):
        raise DeletedAuthorityViolation(
            "target object-storage package initializer must remain empty"
        )


def _provider_transport_application_imports(tree: ast.Module) -> set[str]:
    application_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "app" or alias.name.startswith("app.")
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level > 0:
            prefix = "." * node.level
            if node.module is None:
                application_imports.update(
                    f"{prefix}{alias.name}" for alias in node.names
                )
            else:
                application_imports.add(f"{prefix}{node.module}")
        elif node.module == "app":
            application_imports.update(f"app.{alias.name}" for alias in node.names)
        elif node.module is not None and node.module.startswith("app."):
            application_imports.add(node.module)
    return application_imports


def _assert_channel_provider_transports_are_isolated(backend_root: Path) -> None:
    feishu_source = backend_root / FEISHU_PROVIDER_TRANSPORT_SOURCE
    if feishu_source.is_file():
        tree = ast.parse(
            feishu_source.read_text(encoding="utf-8"),
            filename=str(feishu_source),
        )
        application_imports = _provider_transport_application_imports(tree)

        feishu_class = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == "FeishuService"
            ),
            None,
        )
        restored_methods: list[str] = []
        restored_state: list[str] = []
        tenant_token_contract_valid = False
        if feishu_class is not None:
            restored_methods = sorted(
                LEGACY_FEISHU_AUTHORITY_METHODS
                & {
                    node.name
                    for node in feishu_class.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
            )
            restored_state = sorted(
                LEGACY_FEISHU_CREDENTIAL_STATE
                & {
                    node.attr
                    for node in ast.walk(feishu_class)
                    if isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                }
            )
            tenant_token_method = next(
                (
                    node
                    for node in feishu_class.body
                    if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "get_tenant_access_token"
                ),
                None,
            )
            if tenant_token_method is not None:
                positional = tenant_token_method.args.posonlyargs + tenant_token_method.args.args
                required_count = len(positional) - len(tenant_token_method.args.defaults)
                required_names = {argument.arg for argument in positional[:required_count]}
                tenant_token_contract_valid = {"app_id", "app_secret"} <= required_names

        violations = []
        if application_imports:
            violations.append(f"imports={','.join(sorted(application_imports))}")
        if restored_methods:
            violations.append(f"methods={','.join(restored_methods)}")
        if restored_state:
            violations.append(f"state={','.join(restored_state)}")
        if not tenant_token_contract_valid:
            violations.append("get_tenant_access_token must require app_id and app_secret")
        if violations:
            raise DeletedAuthorityViolation(
                "Feishu provider transport restores legacy auth or credential authority: "
                + "; ".join(violations)
            )

    dingtalk_source = backend_root / DINGTALK_PROVIDER_TRANSPORT_SOURCE
    if not dingtalk_source.is_file():
        return
    tree = ast.parse(
        dingtalk_source.read_text(encoding="utf-8"),
        filename=str(dingtalk_source),
    )
    application_imports = _provider_transport_application_imports(tree)
    restored_wrappers = sorted(
        LEGACY_DINGTALK_STREAM_WRAPPERS
        & {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    )
    violations = []
    if application_imports:
        violations.append(f"imports={','.join(sorted(application_imports))}")
    if restored_wrappers:
        violations.append(f"wrappers={','.join(restored_wrappers)}")
    if violations:
        raise DeletedAuthorityViolation(
            "DingTalk provider transport restores application authority or stream wrapper: "
            + "; ".join(violations)
        )


def _assert_deleted_legacy_channel_authorities(backend_root: Path) -> None:
    if len(LEGACY_CHANNEL_IMPORT_IDENTITIES) != 19:
        raise DeletedAuthorityViolation(
            "legacy Channel authority inventory must contain exactly 19 identities"
        )
    for identity in LEGACY_CHANNEL_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                f"deleted legacy Channel authority module was reintroduced: {identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                f"deleted legacy Channel authority package was reintroduced: {identity}"
            )

    cleanup_script = backend_root / LEGACY_CHANNEL_CLEANUP_SCRIPT
    if cleanup_script.exists():
        raise DeletedAuthorityViolation(
            "deleted legacy Channel cleanup script was reintroduced: "
            f"{LEGACY_CHANNEL_CLEANUP_SCRIPT}"
        )


def _assert_deleted_legacy_channel_package_exports(backend_root: Path) -> None:
    for relative_path, exports in LEGACY_CHANNEL_PACKAGE_EXPORTS.items():
        package_init = backend_root / relative_path
        if not package_init.is_file():
            continue
        source = package_init.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(package_init))
        symbols = symtable.symtable(source, str(package_init), "exec")
        try:
            dynamic_hook = symbols.lookup(DYNAMIC_MODULE_EXPORT_HOOK)
        except KeyError:
            binds_dynamic_hook = False
        else:
            binds_dynamic_hook = (
                dynamic_hook.is_assigned()
                or dynamic_hook.is_imported()
                or dynamic_hook.is_namespace()
            )
        dynamic_installer = _ModuleScopeDynamicExportHookVisitor()
        dynamic_installer.visit(tree)
        if binds_dynamic_hook or dynamic_installer.installs_hook:
            raise DeletedAuthorityViolation(
                "deleted legacy Channel package exports can be restored by a "
                f"dynamic hook: {relative_path}"
            )

        for node in ast.walk(tree):
            for export in exports:
                references_export = (
                    isinstance(node, ast.Name) and node.id == export
                ) or (
                    isinstance(node, ast.Attribute) and node.attr == export
                ) or (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and (
                        node.value == export
                        or node.value.endswith(f".{export}")
                    )
                ) or (
                    isinstance(node, ast.keyword) and node.arg == export
                ) or (
                    isinstance(node, ast.alias)
                    and (
                        node.name.split(".")[-1] == export
                        or node.asname == export
                    )
                ) or (
                    isinstance(node, ast.ImportFrom)
                    and node.module is not None
                    and node.module.split(".")[-1] == export
                )
                if references_export:
                    raise DeletedAuthorityViolation(
                        "deleted legacy Channel package export was reintroduced: "
                        f"{relative_path} -> {export}"
                    )


def _assert_tests_do_not_reference_deleted_channel_authorities(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Channel",
        deleted_identities=LEGACY_CHANNEL_DOTTED_IMPORT_IDENTITIES,
    )


def _assert_application_does_not_restore_channel_definitions(
    backend_root: Path,
) -> None:
    app_root = backend_root / "app"
    if not app_root.is_dir():
        return
    for source_path in sorted(app_root.rglob("*.py")):
        relative_path = source_path.relative_to(backend_root)
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        facts = LEGACY_CHANNEL_FORBIDDEN_DEFINITIONS & _source_contract_facts(tree)
        if relative_path == Path("app/modules/channel/models.py"):
            facts -= {"table:channel_deliveries"}
        restored_facts = sorted(facts)
        if restored_facts:
            raise DeletedAuthorityViolation(
                "application source restores legacy Channel definitions: "
                f"{relative_path} -> {', '.join(restored_facts)}"
            )


def _assert_deleted_legacy_autonomy_approval_authority(
    backend_root: Path,
) -> None:
    for identity in LEGACY_AUTONOMY_APPROVAL_IMPORT_IDENTITIES:
        module = (backend_root / identity).with_suffix(".py")
        package = backend_root / identity
        if module.is_file():
            raise DeletedAuthorityViolation(
                "deleted legacy Autonomy/Approval authority module was reintroduced: "
                f"{identity}"
            )
        if package.is_dir():
            raise DeletedAuthorityViolation(
                "deleted legacy Autonomy/Approval authority package was reintroduced: "
                f"{identity}"
            )


def _assert_tests_do_not_reference_deleted_autonomy_approval_authority(
    backend_root: Path,
) -> None:
    _assert_tests_do_not_reference_deleted_authorities(
        backend_root,
        authority="Autonomy/Approval",
        deleted_identities=LEGACY_AUTONOMY_APPROVAL_DOTTED_IMPORT_IDENTITIES,
    )


def _assignment_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Attribute):
        return {target.attr}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {
            name
            for element in target.elts
            for name in _assignment_names(element)
        }
    return set()


def _string_value(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _source_contract_facts(tree: ast.Module) -> set[str]:
    facts: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                facts.add(f"import:{alias.asname or alias.name.split('.')[-1]}")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                facts.add(f"import:{alias.asname or alias.name}")
        elif isinstance(node, ast.ClassDef):
            facts.add(f"class:{node.name}")
            for statement in node.body:
                targets: list[ast.expr] = []
                value: ast.expr | None = None
                if isinstance(statement, ast.Assign):
                    targets.extend(statement.targets)
                    value = statement.value
                elif isinstance(statement, ast.AnnAssign):
                    targets.append(statement.target)
                    value = statement.value
                for target in targets:
                    for name in _assignment_names(target):
                        facts.add(f"class-field:{node.name}:{name}")
                        if name == "__tablename__":
                            table_name = _string_value(value)
                            if table_name:
                                facts.add(f"table:{table_name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts.add(f"function:{node.name}")
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not decorator.args:
                    continue
                if not isinstance(decorator.func, ast.Attribute):
                    continue
                if not (
                    isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "router"
                ):
                    continue
                route = _string_value(decorator.args[0])
                if route and decorator.func.attr in {"delete", "get", "post", "put"}:
                    facts.add(f"route:{decorator.func.attr.upper()}:{route}")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for name in _assignment_names(target):
                    facts.add(f"assigned:{name}")
        elif isinstance(node, ast.AnnAssign):
            for name in _assignment_names(node.target):
                facts.add(f"assigned:{name}")
        elif isinstance(node, ast.Attribute):
            facts.add(f"field:{node.attr}")
            facts.add(f"reference:{node.attr}")
        elif isinstance(node, ast.Name):
            facts.add(f"reference:{node.id}")
        elif isinstance(node, ast.Dict):
            for key in node.keys:
                key_name = _string_value(key)
                if key_name:
                    facts.add(f"key:{key_name}")
        elif isinstance(node, ast.Subscript):
            key_name = _string_value(node.slice)
            if key_name:
                facts.add(f"key:{key_name}")
        elif isinstance(node, ast.Call):
            function_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if function_name == "Enum":
                for keyword in node.keywords:
                    if keyword.arg == "name":
                        enum_name = _string_value(keyword.value)
                        if enum_name:
                            facts.add(f"enum:{enum_name}")
    return facts


def _assert_mixed_owners_do_not_restore_autonomy_approval_facts(
    backend_root: Path,
) -> None:
    for relative_path, forbidden_facts in (
        LEGACY_AUTONOMY_APPROVAL_FORBIDDEN_FACTS.items()
    ):
        source_path = backend_root / relative_path
        if not source_path.is_file():
            continue
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        restored_facts = sorted(forbidden_facts & _source_contract_facts(tree))
        if restored_facts:
            raise DeletedAuthorityViolation(
                "mixed retained owner restores legacy Autonomy/Approval facts: "
                f"{relative_path} -> {', '.join(restored_facts)}"
            )


def _assert_agent_templates_do_not_restore_autonomy_policy(
    backend_root: Path,
) -> None:
    metadata_root = backend_root / AGENT_TEMPLATE_METADATA_ROOT
    if not metadata_root.is_dir():
        return
    for metadata_path in sorted(metadata_root.rglob("meta.yaml")):
        relative_path = metadata_path.relative_to(backend_root)
        try:
            metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise DeletedAuthorityViolation(
                f"Agent Template metadata is invalid YAML: {relative_path}"
            ) from exc
        if not isinstance(metadata, dict):
            raise DeletedAuthorityViolation(
                f"Agent Template metadata must be a top-level mapping: {relative_path}"
            )
        if LEGACY_TEMPLATE_AUTONOMY_FIELD in metadata:
            raise DeletedAuthorityViolation(
                "Agent Template restores legacy Autonomy policy field: "
                f"{relative_path}"
            )


def test_legacy_context_import_identity_is_absent_from_target_tree() -> None:
    _assert_deleted_context_authority(BACKEND_ROOT)


def test_reintroduced_context_module_fails_the_guard(tmp_path: Path) -> None:
    module = tmp_path / CONTEXT_MODULE
    module.parent.mkdir(parents=True)
    module.write_text("async def build_agent_context(): ...\n", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted Context authority module was reintroduced",
    ):
        _assert_deleted_context_authority(tmp_path)


def test_reintroduced_context_package_fails_the_guard(tmp_path: Path) -> None:
    package = tmp_path / CONTEXT_PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted Context authority package was reintroduced",
    ):
        _assert_deleted_context_authority(tmp_path)


def test_legacy_experience_import_identities_are_absent_from_target_tree() -> None:
    _assert_deleted_experience_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    EXPERIENCE_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in EXPERIENCE_REINTRODUCTIONS
    ],
)
def test_reintroduced_experience_import_identity_fails_the_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted Experience authority {representation} was reintroduced",
    ):
        _assert_deleted_experience_authorities(tmp_path)


def test_legacy_model_llm_import_identities_are_absent_from_target_tree() -> None:
    _assert_deleted_model_llm_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    MODEL_LLM_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in MODEL_LLM_REINTRODUCTIONS
    ],
)
def test_reintroduced_model_llm_import_identity_fails_the_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted Model/LLM authority {representation} was reintroduced",
    ):
        _assert_deleted_model_llm_authorities(tmp_path)


def test_legacy_persistent_task_import_identities_are_absent_from_target_tree() -> None:
    _assert_deleted_persistent_task_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    PERSISTENT_TASK_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in PERSISTENT_TASK_REINTRODUCTIONS
    ],
)
def test_reintroduced_persistent_task_import_identity_fails_the_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted Persistent Task authority {representation} was reintroduced",
    ):
        _assert_deleted_persistent_task_authorities(tmp_path)


def test_legacy_tool_import_identities_are_absent_from_target_tree() -> None:
    _assert_deleted_legacy_tool_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_TOOL_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_TOOL_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_tool_import_identity_fails_the_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Tool authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_tool_authorities(tmp_path)


def test_legacy_skill_authorities_are_absent_from_target_tree() -> None:
    _assert_deleted_legacy_skill_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_SKILL_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_SKILL_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_skill_import_identity_fails_the_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Skill authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_skill_authorities(tmp_path)


def test_reintroduced_legacy_skill_creator_files_path_fails_the_guard(
    tmp_path: Path,
) -> None:
    creator_files = tmp_path / LEGACY_SKILL_CREATOR_FILES
    creator_files.mkdir(parents=True)
    (creator_files / "generated.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Skill creator-files path was reintroduced",
    ):
        _assert_deleted_legacy_skill_authorities(tmp_path)


def test_openclaw_gateway_authorities_are_absent_from_target_tree() -> None:
    _assert_deleted_openclaw_gateway_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    OPENCLAW_GATEWAY_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in OPENCLAW_GATEWAY_REINTRODUCTIONS
    ],
)
def test_reintroduced_openclaw_gateway_import_identity_fails_the_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted OpenClaw/Gateway authority {representation} was reintroduced",
    ):
        _assert_deleted_openclaw_gateway_authorities(tmp_path)


def test_legacy_credential_authorities_are_absent_from_target_tree() -> None:
    _assert_deleted_legacy_credential_authorities(BACKEND_ROOT)


def test_dao_package_exports_are_static() -> None:
    _assert_dao_package_exports_are_static(BACKEND_ROOT)


@pytest.mark.parametrize(
    "package_source",
    [
        "def __getattr__(name):\n    return object()\n",
        "async def __getattr__(name):\n    return object()\n",
        "__getattr__ = lambda name: object()\n",
        "if True:\n    __getattr__: object = object()\n",
        "from app.hooks import resolve as __getattr__\n",
        'globals()["__getattr__"] = lambda name: object()\n',
        'globals()["__getattr__"], marker = object(), object()\n',
        'globals()["__getattr__"]: object = object()\n',
        'globals().__setitem__("__getattr__", lambda name: object())\n',
        'setattr(module, "__getattr__", lambda name: object())\n',
    ],
    ids=[
        "function-hook",
        "async-function-hook",
        "assigned-hook",
        "annotated-assigned-hook",
        "imported-hook",
        "globals-subscript-hook",
        "globals-unpacked-subscript-hook",
        "globals-annotated-subscript-hook",
        "globals-setitem-hook",
        "setattr-hook",
    ],
)
def test_dynamic_dao_package_export_hook_fails_guard(
    tmp_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="app.dao package exports must be static",
    ):
        _assert_dao_package_exports_are_static(tmp_path)


@pytest.mark.parametrize(
    "package_source",
    [
        'hook_name = "__getattr__"\n',
        "def helper():\n    def __getattr__(name):\n        return object()\n",
        "def helper():\n    __getattr__ = object()\n    return __getattr__\n",
        "def helper(module):\n    return module.__getattr__\n",
        "class Helper:\n    def __getattr__(self, name):\n        return object()\n",
        "helper.__getattr__ = object()\n",
    ],
    ids=[
        "inert-string",
        "nested-function",
        "local-binding",
        "attribute-reference",
        "class-hook",
        "unrelated-attribute-assignment",
    ],
)
def test_non_package_hook_reference_passes_static_dao_export_guard(
    tmp_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    _assert_dao_package_exports_are_static(tmp_path)


def test_legacy_credential_dao_package_export_is_absent_from_target_tree() -> None:
    _assert_deleted_legacy_credential_dao_export(BACKEND_ROOT)


def test_legacy_agent_authorities_are_absent_from_target_tree() -> None:
    _assert_deleted_legacy_agent_authorities(BACKEND_ROOT)


def test_legacy_agent_dao_package_exports_are_absent_from_target_tree() -> None:
    _assert_deleted_legacy_agent_dao_exports(BACKEND_ROOT)


def test_legacy_agent_run_event_dao_compatibility_authority_is_absent() -> None:
    _assert_deleted_legacy_agent_run_event_dao_authority(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_agent_run_event_dao() -> None:
    _assert_tests_do_not_reference_deleted_agent_run_event_dao(BACKEND_ROOT)


def test_legacy_okr_agent_hook_authority_is_absent() -> None:
    _assert_deleted_legacy_okr_agent_hook_authority(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_okr_agent_hook() -> None:
    _assert_tests_do_not_reference_deleted_okr_agent_hook(BACKEND_ROOT)


def test_legacy_okr_authorities_are_absent() -> None:
    _assert_deleted_legacy_okr_authorities(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_okr_authorities() -> None:
    _assert_tests_do_not_reference_deleted_okr_authorities(BACKEND_ROOT)


def test_application_does_not_restore_legacy_okr_definitions() -> None:
    _assert_application_does_not_restore_okr_definitions(BACKEND_ROOT)


def test_legacy_token_tracker_authority_is_absent() -> None:
    _assert_deleted_legacy_token_tracker_authority(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_token_tracker() -> None:
    _assert_tests_do_not_reference_deleted_token_tracker(BACKEND_ROOT)


def test_legacy_wecom_service_authority_is_absent() -> None:
    _assert_deleted_legacy_wecom_service_authority(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_wecom_service() -> None:
    _assert_tests_do_not_reference_deleted_wecom_service(BACKEND_ROOT)


def test_legacy_identity_tenant_authorities_are_absent_from_target_tree() -> None:
    _assert_deleted_legacy_identity_tenant_authorities(BACKEND_ROOT)


def test_legacy_identity_tenant_dao_exports_are_absent_from_target_tree() -> None:
    _assert_deleted_legacy_identity_tenant_dao_exports(BACKEND_ROOT)


def test_legacy_auth_authorities_are_absent_from_target_tree() -> None:
    _assert_deleted_legacy_auth_authorities(BACKEND_ROOT)


def test_legacy_auth_package_exports_are_absent_from_target_tree() -> None:
    _assert_deleted_legacy_auth_package_exports(BACKEND_ROOT)


def test_backend_tests_do_not_import_deleted_auth_authorities() -> None:
    _assert_tests_do_not_import_deleted_auth_authorities(BACKEND_ROOT)


def test_legacy_sso_authorities_are_absent_from_target_tree() -> None:
    _assert_deleted_legacy_sso_authorities(BACKEND_ROOT)


def test_legacy_sso_dao_package_export_is_absent_from_target_tree() -> None:
    _assert_deleted_legacy_sso_dao_export(BACKEND_ROOT)


def test_backend_tests_do_not_import_deleted_sso_authorities() -> None:
    _assert_tests_do_not_import_deleted_sso_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_CREDENTIAL_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_CREDENTIAL_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_credential_import_identity_fails_the_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Credential authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_credential_authorities(tmp_path)


@pytest.mark.parametrize(
    "package_source",
    [
        "from app.dao.agent_credential_dao import agent_credential_dao\n",
        "from app.dao.agent_credential_dao import agent_credential_dao as restored\n",
        "agent_credential_dao = object()\n",
        '__all__ = ["agent_credential_dao"]\n',
        'globals()["agent_credential_dao"] = object()\n',
    ],
    ids=[
        "direct-import",
        "aliased-import",
        "assignment-reexport",
        "all-exposure",
        "globals-restoration",
    ],
)
def test_reintroduced_legacy_credential_dao_package_export_fails_the_guard(
    tmp_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Credential DAO package export",
    ):
        _assert_deleted_legacy_credential_dao_export(tmp_path)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_AGENT_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_AGENT_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_agent_import_identity_fails_the_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Agent authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_agent_authorities(tmp_path)


@pytest.mark.parametrize(
    "package_source",
    [
        "from app.dao.agent_dao import agent_dao\n",
        "from app.dao.agent_access_dao import agent_access_dao as restored\n",
        "agent_dao = object()\n",
        '__all__ = ["agent_access_dao"]\n',
        'globals()["agent_dao"] = object()\n',
    ],
    ids=[
        "direct-import",
        "aliased-import",
        "assignment-reexport",
        "all-exposure",
        "globals-restoration",
    ],
)
def test_reintroduced_legacy_agent_dao_package_export_fails_the_guard(
    tmp_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Agent DAO package export",
    ):
        _assert_deleted_legacy_agent_dao_exports(tmp_path)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_AGENT_RUN_EVENT_DAO_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_AGENT_RUN_EVENT_DAO_REINTRODUCTIONS
    ],
)
def test_reintroduced_agent_run_event_dao_compatibility_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=(
            "deleted legacy Agent Run Event DAO compatibility "
            f"{representation} was reintroduced"
        ),
    ):
        _assert_deleted_legacy_agent_run_event_dao_authority(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.dao.agent_run_event_dao\n",
        "from app.dao import agent_run_event_dao\n",
        "from app.dao.agent_run_event_dao import agent_run_dao\n",
    ],
    ids=["module-import", "package-import", "symbol-import"],
)
def test_backend_test_static_reference_to_agent_run_event_dao_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_agent_run_event_dao.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Agent Run Event DAO compatibility",
    ):
        _assert_tests_do_not_reference_deleted_agent_run_event_dao(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        'module = importlib.import_module("app.dao.agent_run_event_dao")\n',
        'dao_path = "app.dao.agent_run_event_dao.agent_run_dao"\n',
    ],
    ids=["dynamic-module-import", "dotted-symbol-reference"],
)
def test_backend_test_dynamic_reference_to_agent_run_event_dao_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_agent_run_event_dao_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Agent Run Event DAO compatibility",
    ):
        _assert_tests_do_not_reference_deleted_agent_run_event_dao(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "from app.dao.agent_run_dao import agent_run_dao\n",
        'dao_path = "app.dao.agent_run_dao.agent_run_dao"\n',
    ],
    ids=["run-dao-static-import", "run-dao-dotted-reference"],
)
def test_agent_run_dao_reference_passes_agent_run_event_dao_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_agent_run_dao_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    _assert_tests_do_not_reference_deleted_agent_run_event_dao(tmp_path)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_OKR_AGENT_HOOK_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_OKR_AGENT_HOOK_REINTRODUCTIONS
    ],
)
def test_reintroduced_okr_agent_hook_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy OKR Agent Hook {representation} was reintroduced",
    ):
        _assert_deleted_legacy_okr_agent_hook_authority(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.services.okr_agent_hook\n",
        "from app.services import okr_agent_hook\n",
        "from app.services.okr_agent_hook import hook_new_agent\n",
    ],
    ids=["module-import", "package-import", "symbol-import"],
)
def test_backend_test_static_reference_to_okr_agent_hook_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_okr_agent_hook.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy OKR Agent Hook authority",
    ):
        _assert_tests_do_not_reference_deleted_okr_agent_hook(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        'module = importlib.import_module("app.services.okr_agent_hook")\n',
        'hook_path = "app.services.okr_agent_hook.hook_new_org_member"\n',
    ],
    ids=["dynamic-module-import", "dotted-hook-reference"],
)
def test_backend_test_dynamic_reference_to_okr_agent_hook_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_okr_agent_hook_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy OKR Agent Hook authority",
    ):
        _assert_tests_do_not_reference_deleted_okr_agent_hook(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "from app.modules.okr import __name__\n",
        "from app.services.timezone_utils import validate_timezone_name\n",
    ],
    ids=["target-okr-module", "timezone-validation"],
)
def test_target_okr_reference_passes_okr_agent_hook_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_target_okr_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    _assert_tests_do_not_reference_deleted_okr_agent_hook(tmp_path)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_OKR_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_OKR_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_okr_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy OKR authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_okr_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [f"import {identity}\n" for identity in LEGACY_OKR_DOTTED_IMPORT_IDENTITIES],
    ids=[f"{identity}-static" for identity in LEGACY_OKR_DOTTED_IMPORT_IDENTITIES],
)
def test_backend_test_static_reference_of_deleted_okr_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_okr_static.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy OKR authority",
    ):
        _assert_tests_do_not_reference_deleted_okr_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        f'target = "{identity}.restored"\n'
        for identity in LEGACY_OKR_DOTTED_IMPORT_IDENTITIES
    ],
    ids=[f"{identity}-dotted" for identity in LEGACY_OKR_DOTTED_IMPORT_IDENTITIES],
)
def test_backend_test_dotted_reference_of_deleted_okr_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_okr_dotted.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy OKR authority",
    ):
        _assert_tests_do_not_reference_deleted_okr_authorities(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        *(f"class {name}: ...\n" for name in (
            "OKRObjective",
            "OKRKeyResult",
            "OKRAlignment",
            "OKRProgressLog",
            "WorkReport",
            "MemberDailyReport",
            "CompanyReport",
            "OKRSettings",
        )),
        *(f'class RenamedOKR:\n    __tablename__ = "{name}"\n' for name in (
            "okr_objectives",
            "okr_key_results",
            "okr_alignments",
            "okr_progress_logs",
            "work_reports",
            "member_daily_reports",
            "company_reports",
            "okr_settings",
        )),
    ],
)
def test_restored_legacy_okr_definition_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/modules/okr/restored.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="application source restores legacy OKR definitions",
    ):
        _assert_application_does_not_restore_okr_definitions(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "from app.modules.okr import __name__\n",
        "from app.services.timezone_utils import validate_timezone_name\n",
    ],
    ids=["target-okr-module", "timezone-validation"],
)
def test_target_okr_names_pass_legacy_okr_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_target_okr_names.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    _assert_tests_do_not_reference_deleted_okr_authorities(tmp_path)


def test_target_okr_definitions_pass_legacy_okr_guard(tmp_path: Path) -> None:
    source_path = tmp_path / "app/modules/okr/service.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("class OKRPolicy: ...\n", encoding="utf-8")

    _assert_application_does_not_restore_okr_definitions(tmp_path)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_TOKEN_TRACKER_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_TOKEN_TRACKER_REINTRODUCTIONS
    ],
)
def test_reintroduced_token_tracker_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Token Tracker {representation} was reintroduced",
    ):
        _assert_deleted_legacy_token_tracker_authority(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.services.token_tracker\n",
        "from app.services import token_tracker\n",
        "from app.services.token_tracker import record_token_usage\n",
    ],
    ids=["module-import", "package-import", "symbol-import"],
)
def test_backend_test_static_reference_to_token_tracker_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_token_tracker.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Token Tracker authority",
    ):
        _assert_tests_do_not_reference_deleted_token_tracker(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        'module = importlib.import_module("app.services.token_tracker")\n',
        'tracker_path = "app.services.token_tracker.TokenUsage"\n',
    ],
    ids=["dynamic-module-import", "dotted-type-reference"],
)
def test_backend_test_dynamic_reference_to_token_tracker_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_token_tracker_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Token Tracker authority",
    ):
        _assert_tests_do_not_reference_deleted_token_tracker(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "from app.modules.observability import __name__\n",
    ],
    ids=["target-observability-module"],
)
def test_retained_token_reporting_reference_passes_token_tracker_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_retained_token_reporting_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    _assert_tests_do_not_reference_deleted_token_tracker(tmp_path)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_WECOM_SERVICE_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_WECOM_SERVICE_REINTRODUCTIONS
    ],
)
def test_reintroduced_wecom_service_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy WeCom service {representation} was reintroduced",
    ):
        _assert_deleted_legacy_wecom_service_authority(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.services.wecom_service\n",
        "from app.services import wecom_service\n",
        "from app.services.wecom_service import send_wecom_message\n",
    ],
    ids=["module-import", "package-import", "symbol-import"],
)
def test_backend_test_static_reference_to_wecom_service_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_wecom_service.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy WeCom service authority",
    ):
        _assert_tests_do_not_reference_deleted_wecom_service(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        'module = importlib.import_module("app.services.wecom_service")\n',
        'sender_path = "app.services.wecom_service.send_wecom_message"\n',
    ],
    ids=["dynamic-module-import", "dotted-sender-reference"],
)
def test_backend_test_dynamic_reference_to_wecom_service_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_wecom_service_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy WeCom service authority",
    ):
        _assert_tests_do_not_reference_deleted_wecom_service(tmp_path)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_IDENTITY_TENANT_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_IDENTITY_TENANT_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_identity_tenant_import_identity_fails_the_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=(
            "deleted legacy Identity/Tenant authority "
            f"{representation} was reintroduced"
        ),
    ):
        _assert_deleted_legacy_identity_tenant_authorities(tmp_path)


@pytest.mark.parametrize(
    "package_source",
    [
        "from app.dao.identity_dao import identity_dao\n",
        "from app.dao.user_dao import user_dao as restored\n",
        "tenant_dao = object()\n",
        '__all__ = ["identity_dao"]\n',
        'globals()["user_dao"] = object()\n',
    ],
    ids=[
        "direct-import",
        "aliased-import",
        "assignment-reexport",
        "all-exposure",
        "globals-restoration",
    ],
)
def test_reintroduced_legacy_identity_tenant_dao_export_fails_the_guard(
    tmp_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Identity/Tenant DAO package export",
    ):
        _assert_deleted_legacy_identity_tenant_dao_exports(tmp_path)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_AUTH_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_AUTH_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_auth_import_identity_fails_the_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Auth authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_auth_authorities(tmp_path)


@pytest.mark.parametrize(
    ("relative_path", "package_source"),
    [
        (Path("app/api/__init__.py"), "from app.api import auth\n"),
        (Path("app/api/__init__.py"), '__all__ = ["auth"]\n'),
        (Path("app/services/__init__.py"), "auth_provider = object()\n"),
        (
            Path("app/services/__init__.py"),
            "from app.services.auth_registry import auth_provider_registry\n",
        ),
        (Path("app/services/__init__.py"), "def __getattr__(name):\n    return object()\n"),
        (
            Path("app/services/__init__.py"),
            'globals()["registration_service"] = object()\n',
        ),
    ],
    ids=[
        "api-direct-import",
        "api-all-exposure",
        "services-assignment",
        "services-direct-import",
        "services-module-getattr",
        "services-globals-restoration",
    ],
)
def test_reintroduced_legacy_auth_package_export_fails_the_guard(
    tmp_path: Path,
    relative_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / relative_path
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Auth package export",
    ):
        _assert_deleted_legacy_auth_package_exports(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.api.auth\n",
        "from app.api import auth\n",
        "from app.services.auth_registry import auth_provider_registry\n",
        "from app.services import registration_service\n",
        "from app.services import auth_provider_registry as registry\n",
    ],
    ids=[
        "direct-module-import",
        "package-submodule-import",
        "service-symbol-import",
        "services-package-import",
        "aliased-package-export-import",
    ],
)
def test_backend_test_import_of_deleted_auth_authority_fails_the_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_auth_dependency.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test imports deleted legacy Auth authority",
    ):
        _assert_tests_do_not_import_deleted_auth_authorities(tmp_path)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_SSO_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_SSO_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_sso_import_identity_fails_the_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy SSO authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_sso_authorities(tmp_path)


@pytest.mark.parametrize(
    "package_source",
    [
        "from app.dao.identity_provider_dao import identity_provider_dao\n",
        "identity_provider_dao = object()\n",
        '__all__ = ["identity_provider_dao"]\n',
        'globals()["identity_provider_dao"] = object()\n',
    ],
    ids=[
        "direct-import",
        "assignment-reexport",
        "all-exposure",
        "globals-restoration",
    ],
)
def test_reintroduced_legacy_sso_dao_package_export_fails_the_guard(
    tmp_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy SSO DAO package export",
    ):
        _assert_deleted_legacy_sso_dao_export(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.api.sso\n",
        "from app.api import google_workspace\n",
        "from app.models.identity import IdentityProvider\n",
        "from app.dao import identity_provider_dao\n",
        "from app.services.sso_service import sso_service\n",
        "from app.services import google_workspace_oauth\n",
    ],
    ids=[
        "direct-api-import",
        "api-package-import",
        "model-import",
        "dao-package-import",
        "service-symbol-import",
        "services-package-import",
    ],
)
def test_backend_test_import_of_deleted_sso_authority_fails_the_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_sso_dependency.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test imports deleted legacy SSO authority",
    ):
        _assert_tests_do_not_import_deleted_sso_authorities(tmp_path)


def test_legacy_organization_relationship_import_identities_are_absent() -> None:
    _assert_deleted_legacy_organization_relationship_authorities(BACKEND_ROOT)


def test_legacy_organization_relationship_dao_export_is_absent() -> None:
    _assert_deleted_legacy_organization_relationship_dao_export(BACKEND_ROOT)


def test_backend_tests_do_not_import_deleted_organization_relationship_authorities() -> None:
    _assert_tests_do_not_import_deleted_organization_relationship_authorities(
        BACKEND_ROOT
    )


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_ORGANIZATION_RELATIONSHIP_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in (
            LEGACY_ORGANIZATION_RELATIONSHIP_REINTRODUCTIONS
        )
    ],
)
def test_reintroduced_legacy_organization_relationship_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=(
            "deleted legacy Organization/Relationship authority "
            f"{representation} was reintroduced"
        ),
    ):
        _assert_deleted_legacy_organization_relationship_authorities(tmp_path)


@pytest.mark.parametrize(
    "package_source",
    [
        "from app.dao.org_member_dao import org_member_dao\n",
        "org_member_dao = object()\n",
        '__all__ = ["org_member_dao"]\n',
        'globals()["org_member_dao"] = object()\n',
    ],
    ids=[
        "direct-import",
        "assignment-reexport",
        "all-exposure",
        "globals-restoration",
    ],
)
def test_reintroduced_legacy_organization_relationship_dao_export_fails_guard(
    tmp_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Organization/Relationship DAO package export",
    ):
        _assert_deleted_legacy_organization_relationship_dao_export(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.models.org\n",
        "from app.api import organization\n",
        "from app.api.relationships import router\n",
        "from app.dao import org_member_dao\n",
        "from app.services.org_sync_adapter import BaseOrgSyncAdapter\n",
        "from app.services import org_sync_service\n",
        "from app.services.access_relationships import ensure_access_granted_platform_relationships\n",
    ],
    ids=[
        "model-import",
        "api-package-import",
        "api-symbol-import",
        "dao-package-import",
        "sync-adapter-import",
        "sync-service-package-import",
        "access-relationships-import",
    ],
)
def test_backend_test_import_of_deleted_organization_relationship_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_organization_relationship.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test imports deleted legacy Organization/Relationship authority",
    ):
        _assert_tests_do_not_import_deleted_organization_relationship_authorities(
            tmp_path
        )


def test_legacy_invitation_import_identities_are_absent() -> None:
    _assert_deleted_legacy_invitation_authorities(BACKEND_ROOT)


def test_legacy_invitation_dao_export_is_absent() -> None:
    _assert_deleted_legacy_invitation_dao_export(BACKEND_ROOT)


def test_backend_tests_do_not_import_deleted_invitation_authorities() -> None:
    _assert_tests_do_not_import_deleted_invitation_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_INVITATION_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_INVITATION_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_invitation_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=(
            "deleted legacy Invitation authority "
            f"{representation} was reintroduced"
        ),
    ):
        _assert_deleted_legacy_invitation_authorities(tmp_path)


@pytest.mark.parametrize(
    "package_source",
    [
        "from app.dao.invitation_code_dao import invitation_code_dao\n",
        "invitation_code_dao = object()\n",
        '__all__ = ["invitation_code_dao"]\n',
        'globals()["invitation_code_dao"] = object()\n',
    ],
    ids=[
        "direct-import",
        "assignment-reexport",
        "all-exposure",
        "globals-restoration",
    ],
)
def test_reintroduced_legacy_invitation_dao_export_fails_guard(
    tmp_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Invitation DAO package export",
    ):
        _assert_deleted_legacy_invitation_dao_export(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.models.invitation_code\n",
        "from app.models.invitation_code import InvitationCode\n",
        "from app.dao import invitation_code_dao\n",
        "from app.dao.invitation_code_dao import InvitationCodeDAO\n",
    ],
    ids=[
        "model-import",
        "model-symbol-import",
        "dao-package-import",
        "dao-symbol-import",
    ],
)
def test_backend_test_import_of_deleted_invitation_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_invitation.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test imports deleted legacy Invitation authority",
    ):
        _assert_tests_do_not_import_deleted_invitation_authorities(tmp_path)


def test_legacy_onboarding_import_identities_are_absent() -> None:
    _assert_deleted_legacy_onboarding_authorities(BACKEND_ROOT)


def test_backend_tests_do_not_import_deleted_onboarding_authorities() -> None:
    _assert_tests_do_not_import_deleted_onboarding_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_ONBOARDING_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_ONBOARDING_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_onboarding_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=(
            "deleted legacy Onboarding authority "
            f"{representation} was reintroduced"
        ),
    ):
        _assert_deleted_legacy_onboarding_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.models.onboarding\n",
        "from app.models.onboarding import UserTenantOnboarding\n",
        "from app.api import onboarding\n",
        "from app.api.onboarding import router\n",
        "from app.services import onboarding\n",
        "from app.services.onboarding import resolve_onboarding_prompt\n",
    ],
    ids=[
        "model-import",
        "model-symbol-import",
        "api-package-import",
        "api-symbol-import",
        "service-package-import",
        "service-symbol-import",
    ],
)
def test_backend_test_import_of_deleted_onboarding_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_onboarding.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test imports deleted legacy Onboarding authority",
    ):
        _assert_tests_do_not_import_deleted_onboarding_authorities(tmp_path)


def test_legacy_directory_import_identities_are_absent() -> None:
    _assert_deleted_legacy_directory_authorities(BACKEND_ROOT)


def test_legacy_directory_package_exports_are_absent() -> None:
    _assert_deleted_legacy_directory_package_exports(BACKEND_ROOT)


def test_backend_tests_do_not_import_deleted_directory_authorities() -> None:
    _assert_tests_do_not_import_deleted_directory_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_DIRECTORY_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_DIRECTORY_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_directory_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=(
            "deleted legacy Directory authority "
            f"{representation} was reintroduced"
        ),
    ):
        _assert_deleted_legacy_directory_authorities(tmp_path)


@pytest.mark.parametrize(
    ("relative_path", "package_source"),
    [
        (
            Path("app/api/__init__.py"),
            "from app.api.directory import router\n",
        ),
        (
            Path("app/services/__init__.py"),
            "agent_directory = object()\n",
        ),
        (
            Path("app/api/__init__.py"),
            '__all__ = ["directory"]\n',
        ),
        (
            Path("app/services/__init__.py"),
            "def __getattr__(name):\n    return object()\n",
        ),
    ],
    ids=[
        "api-direct-import",
        "service-assignment-reexport",
        "api-all-exposure",
        "service-module-getattr",
    ],
)
def test_reintroduced_legacy_directory_package_export_fails_guard(
    tmp_path: Path,
    relative_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / relative_path
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Directory package export",
    ):
        _assert_deleted_legacy_directory_package_exports(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.api.directory\n",
        "from app.api import directory\n",
        "from app.api.directory import router\n",
        "import app.services.agent_directory\n",
        "from app.services import agent_directory\n",
        "from app.services.agent_directory import query_agent_directory\n",
    ],
    ids=[
        "api-import",
        "api-package-import",
        "api-symbol-import",
        "service-import",
        "service-package-import",
        "service-symbol-import",
    ],
)
def test_backend_test_import_of_deleted_directory_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_directory.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test imports deleted legacy Directory authority",
    ):
        _assert_tests_do_not_import_deleted_directory_authorities(tmp_path)


def test_legacy_focus_import_identities_are_absent() -> None:
    _assert_deleted_legacy_focus_authorities(BACKEND_ROOT)


def test_legacy_focus_dao_export_is_absent() -> None:
    _assert_deleted_legacy_focus_dao_export(BACKEND_ROOT)


def test_backend_tests_do_not_import_deleted_focus_authorities() -> None:
    _assert_tests_do_not_import_deleted_focus_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_FOCUS_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_FOCUS_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_focus_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Focus authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_focus_authorities(tmp_path)


@pytest.mark.parametrize(
    "package_source",
    [
        "from app.dao.focus_dao import focus_dao\n",
        "focus_dao = object()\n",
        '__all__ = ["focus_dao"]\n',
    ],
    ids=[
        "direct-import",
        "assignment-reexport",
        "all-exposure",
    ],
)
def test_reintroduced_legacy_focus_dao_export_fails_guard(
    tmp_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Focus DAO package export",
    ):
        _assert_deleted_legacy_focus_dao_export(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.models.focus\n",
        "from app.models import focus\n",
        "from app.models.focus import AgentFocusItem\n",
        "import app.dao.focus_dao\n",
        "from app.dao import focus_dao\n",
        "from app.dao.focus_dao import FocusDAO\n",
        "import app.api.focus\n",
        "from app.api import focus\n",
        "from app.api.focus import router\n",
        "import app.services.focus_service\n",
        "from app.services import focus_service\n",
        "from app.services.focus_service import list_focus_items\n",
    ],
    ids=[
        "model-import",
        "model-package-import",
        "model-symbol-import",
        "dao-import",
        "dao-package-import",
        "dao-symbol-import",
        "api-import",
        "api-package-import",
        "api-symbol-import",
        "service-import",
        "service-package-import",
        "service-symbol-import",
    ],
)
def test_backend_test_import_of_deleted_focus_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_focus.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test imports deleted legacy Focus authority",
    ):
        _assert_tests_do_not_import_deleted_focus_authorities(tmp_path)


def test_legacy_notification_import_identities_are_absent() -> None:
    _assert_deleted_legacy_notification_authorities(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_notification_authorities() -> None:
    _assert_tests_do_not_reference_deleted_notification_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_NOTIFICATION_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_NOTIFICATION_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_notification_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Notification authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_notification_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.models.notification\n",
        "from app.models import notification\n",
        "from app.models.notification import Notification\n",
        "import app.api.notification\n",
        "from app.api import notification\n",
        "from app.api.notification import router\n",
        "import app.services.notification_service\n",
        "from app.services import notification_service\n",
        "from app.services.notification_service import send_notification\n",
    ],
    ids=[
        "model-import",
        "model-package-import",
        "model-symbol-import",
        "api-import",
        "api-package-import",
        "api-symbol-import",
        "service-import",
        "service-package-import",
        "service-symbol-import",
    ],
)
def test_backend_test_import_of_deleted_notification_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_notification.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Notification authority",
    ):
        _assert_tests_do_not_reference_deleted_notification_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        (
            'monkeypatch.setattr('
            '"app.services.notification_service.send_notification", object())\n'
        ),
        'module = importlib.import_module("app.api.notification")\n',
        'model_path = "app.models.notification.Notification"\n',
    ],
    ids=[
        "monkeypatch-dotted-reference",
        "dynamic-import-reference",
        "model-dotted-reference",
    ],
)
def test_backend_test_dynamic_reference_of_deleted_notification_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_notification_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Notification authority",
    ):
        _assert_tests_do_not_reference_deleted_notification_authorities(tmp_path)


def test_unrelated_dynamic_test_reference_passes_notification_guard(
    tmp_path: Path,
) -> None:
    test_path = tmp_path / "tests/test_system_email_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        (
            'monkeypatch.setattr('
            '"app.services.email_service.send_email", object())\n'
        ),
        encoding="utf-8",
    )

    _assert_tests_do_not_reference_deleted_notification_authorities(tmp_path)


def test_legacy_published_page_import_identities_are_absent() -> None:
    _assert_deleted_legacy_published_page_authorities(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_published_page_authorities() -> None:
    _assert_tests_do_not_reference_deleted_published_page_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_PUBLISHED_PAGE_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_PUBLISHED_PAGE_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_published_page_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=(
            "deleted legacy Published Page authority "
            f"{representation} was reintroduced"
        ),
    ):
        _assert_deleted_legacy_published_page_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.models.published_page\n",
        "from app.models import published_page\n",
        "from app.models.published_page import PublishedPage\n",
        "import app.api.pages\n",
        "from app.api import pages\n",
        "from app.api.pages import router\n",
    ],
    ids=[
        "model-import",
        "model-package-import",
        "model-symbol-import",
        "api-import",
        "api-package-import",
        "api-symbol-import",
    ],
)
def test_backend_test_import_of_deleted_published_page_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_published_page.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Published Page authority",
    ):
        _assert_tests_do_not_reference_deleted_published_page_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        'module = importlib.import_module("app.api.pages")\n',
        'model_path = "app.models.published_page.PublishedPage"\n',
    ],
    ids=["dynamic-api-import-reference", "model-dotted-reference"],
)
def test_backend_test_dynamic_reference_of_deleted_published_page_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_published_page_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Published Page authority",
    ):
        _assert_tests_do_not_reference_deleted_published_page_authorities(tmp_path)


def test_unrelated_dynamic_test_reference_passes_published_page_guard(
    tmp_path: Path,
) -> None:
    test_path = tmp_path / "tests/test_unrelated_page_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        'module = importlib.import_module("app.services.text_extractor")\n',
        encoding="utf-8",
    )

    _assert_tests_do_not_reference_deleted_published_page_authorities(tmp_path)


def test_legacy_plaza_import_identities_are_absent() -> None:
    _assert_deleted_legacy_plaza_authorities(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_plaza_authorities() -> None:
    _assert_tests_do_not_reference_deleted_plaza_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_PLAZA_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_PLAZA_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_plaza_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Plaza authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_plaza_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.models.plaza\n",
        "from app.models import plaza\n",
        "from app.models.plaza import PlazaPost\n",
        "import app.api.plaza\n",
        "from app.api import plaza\n",
        "from app.api.plaza import router\n",
    ],
    ids=[
        "model-import",
        "model-package-import",
        "model-symbol-import",
        "api-import",
        "api-package-import",
        "api-symbol-import",
    ],
)
def test_backend_test_import_of_deleted_plaza_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_plaza.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Plaza authority",
    ):
        _assert_tests_do_not_reference_deleted_plaza_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        'module = importlib.import_module("app.api.plaza")\n',
        'model_path = "app.models.plaza.PlazaPost"\n',
    ],
    ids=["dynamic-api-import-reference", "model-dotted-reference"],
)
def test_backend_test_dynamic_reference_of_deleted_plaza_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_plaza_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Plaza authority",
    ):
        _assert_tests_do_not_reference_deleted_plaza_authorities(tmp_path)


def test_unrelated_dynamic_test_reference_passes_plaza_guard(tmp_path: Path) -> None:
    test_path = tmp_path / "tests/test_unrelated_social_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        'module = importlib.import_module("app.modules.heartbeat")\n',
        encoding="utf-8",
    )

    _assert_tests_do_not_reference_deleted_plaza_authorities(tmp_path)


def test_legacy_agent_template_import_identities_are_absent() -> None:
    _assert_deleted_legacy_agent_template_authorities(BACKEND_ROOT)


def test_legacy_agent_template_dao_export_is_absent() -> None:
    _assert_deleted_legacy_agent_template_dao_export(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_agent_template_authorities() -> None:
    _assert_tests_do_not_reference_deleted_agent_template_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_AGENT_TEMPLATE_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_AGENT_TEMPLATE_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_agent_template_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Agent Template authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_agent_template_authorities(tmp_path)


@pytest.mark.parametrize(
    "package_source",
    [
        "from app.dao.agent_template_dao import agent_template_dao\n",
        "agent_template_dao = object()\n",
        '__all__ = ["agent_template_dao"]\n',
    ],
    ids=[
        "direct-import",
        "assignment-reexport",
        "all-exposure",
    ],
)
def test_reintroduced_legacy_agent_template_dao_export_fails_guard(
    tmp_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Agent Template DAO package export",
    ):
        _assert_deleted_legacy_agent_template_dao_export(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.dao.agent_template_dao\n",
        "from app.dao import agent_template_dao\n",
        "from app.dao.agent_template_dao import AgentTemplateDAO\n",
        "import app.services.template_seeder\n",
        "from app.services import template_seeder\n",
        "from app.services.template_seeder import seed_agent_templates\n",
    ],
    ids=[
        "dao-import",
        "dao-package-import",
        "dao-symbol-import",
        "service-import",
        "service-package-import",
        "service-symbol-import",
    ],
)
def test_backend_test_import_of_deleted_agent_template_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_agent_template.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Agent Template authority",
    ):
        _assert_tests_do_not_reference_deleted_agent_template_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        'module = importlib.import_module("app.services.template_seeder")\n',
        'dao_path = "app.dao.agent_template_dao.AgentTemplateDAO"\n',
    ],
    ids=["dynamic-service-import-reference", "dao-dotted-reference"],
)
def test_backend_test_dynamic_reference_of_deleted_agent_template_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_agent_template_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Agent Template authority",
    ):
        _assert_tests_do_not_reference_deleted_agent_template_authorities(tmp_path)


def test_unrelated_dynamic_test_reference_passes_agent_template_guard(
    tmp_path: Path,
) -> None:
    test_path = tmp_path / "tests/test_unrelated_template_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        'module = importlib.import_module("app.services.text_extractor")\n',
        encoding="utf-8",
    )

    _assert_tests_do_not_reference_deleted_agent_template_authorities(tmp_path)


def test_legacy_agentbay_import_identities_are_absent() -> None:
    _assert_deleted_legacy_agentbay_authorities(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_agentbay_authorities() -> None:
    _assert_tests_do_not_reference_deleted_agentbay_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_AGENTBAY_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_AGENTBAY_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_agentbay_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy AgentBay authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_agentbay_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.api.agentbay_control\n",
        "from app.api.agentbay_control import control_lock\n",
        "import app.services.agentbay_client\n",
        "from app.services.agentbay_client import AgentBayClient\n",
        "import app.services.agentbay_live\n",
        "from app.services.agentbay_live import detect_agentbay_env\n",
    ],
    ids=[
        "control-import",
        "control-symbol-import",
        "client-import",
        "client-symbol-import",
        "live-import",
        "live-symbol-import",
    ],
)
def test_backend_test_import_of_deleted_agentbay_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_agentbay.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy AgentBay authority",
    ):
        _assert_tests_do_not_reference_deleted_agentbay_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        'module = importlib.import_module("app.api.agentbay_control")\n',
        'client_path = "app.services.agentbay_client.AgentBayClient"\n',
        'monkeypatch.setattr("app.services.agentbay_live.detect_agentbay_env", fake)\n',
    ],
    ids=[
        "dynamic-control-import-reference",
        "client-dotted-reference",
        "live-monkeypatch-reference",
    ],
)
def test_backend_test_dynamic_reference_of_deleted_agentbay_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_agentbay_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy AgentBay authority",
    ):
        _assert_tests_do_not_reference_deleted_agentbay_authorities(tmp_path)


def test_unrelated_dynamic_test_reference_passes_agentbay_guard(tmp_path: Path) -> None:
    test_path = tmp_path / "tests/test_unrelated_agentbay_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        'module = importlib.import_module("app.services.email_service")\n',
        encoding="utf-8",
    )

    _assert_tests_do_not_reference_deleted_agentbay_authorities(tmp_path)


def test_legacy_tenant_knowledge_publication_import_identity_is_absent() -> None:
    _assert_deleted_legacy_tenant_knowledge_publication_authority(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_TENANT_KNOWLEDGE_PUBLICATION_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in (
            LEGACY_TENANT_KNOWLEDGE_PUBLICATION_REINTRODUCTIONS
        )
    ],
)
def test_reintroduced_legacy_tenant_knowledge_publication_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=(
            "deleted legacy Tenant Knowledge publication authority "
            f"{representation} was reintroduced"
        ),
    ):
        _assert_deleted_legacy_tenant_knowledge_publication_authority(tmp_path)


def test_legacy_session_substrate_authorities_are_absent() -> None:
    _assert_deleted_legacy_session_substrate_authorities(BACKEND_ROOT)


def test_legacy_session_substrate_dao_exports_are_absent() -> None:
    _assert_deleted_legacy_session_substrate_dao_exports(BACKEND_ROOT)


def test_model_schema_trees_do_not_restore_session_substrate_definitions() -> None:
    _assert_model_schema_trees_do_not_restore_session_substrate_definitions(
        BACKEND_ROOT
    )


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_SESSION_SUBSTRATE_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_SESSION_SUBSTRATE_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_session_substrate_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=(
            "deleted legacy Session substrate authority "
            f"{representation} was reintroduced"
        ),
    ):
        _assert_deleted_legacy_session_substrate_authorities(tmp_path)


@pytest.mark.parametrize("export", LEGACY_SESSION_SUBSTRATE_DAO_EXPORTS)
def test_reintroduced_legacy_session_substrate_dao_export_fails_guard(
    tmp_path: Path,
    export: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(f"__all__ = [{export!r}]\n", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Session substrate DAO package export was reintroduced",
    ):
        _assert_deleted_legacy_session_substrate_dao_exports(tmp_path)


def test_dynamic_session_substrate_dao_export_hook_fails_guard(
    tmp_path: Path,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(
        "def __getattr__(name):\n    return object()\n",
        encoding="utf-8",
    )

    with pytest.raises(
        DeletedAuthorityViolation,
        match="app.dao package exports must be static",
    ):
        _assert_dao_package_exports_are_static(tmp_path)


@pytest.mark.parametrize(
    ("relative_path", "source"),
    [
        (Path("app/models/message.py"), "class ChatMessage: ...\n"),
        (
            Path("app/modules/session/model.py"),
            'class Legacy:\n    __tablename__ = "chat_messages"\n',
        ),
        (
            Path("app/modules/session/model.py"),
            'role = Enum("user", name="chat_role_enum")\n',
        ),
        (Path("app/modules/session/schema.py"), "class ChatMessageOut: ...\n"),
        (Path("app/modules/session/schema.py"), "class ChatSend: ...\n"),
    ],
    ids=[
        "chat-message-model",
        "chat-messages-table",
        "chat-role-enum",
        "chat-message-out-schema",
        "chat-send-schema",
    ],
)
def test_restored_session_substrate_fact_fails_guard(
    tmp_path: Path,
    relative_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / relative_path
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="model or schema restores legacy Session substrate definitions",
    ):
        _assert_model_schema_trees_do_not_restore_session_substrate_definitions(
            tmp_path
        )


def test_target_session_input_and_agent_reply_pass_session_substrate_guard(
    tmp_path: Path,
) -> None:
    safe_sources = {
        Path("app/modules/session/model.py"): (
            'class SessionInput:\n    __tablename__ = "session_inputs"\n'
        ),
        Path("app/modules/session/schema.py"): "class AgentReply: ...\n",
    }
    for relative_path, source in safe_sources.items():
        source_path = tmp_path / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(source, encoding="utf-8")

    _assert_model_schema_trees_do_not_restore_session_substrate_definitions(
        tmp_path
    )


def test_legacy_group_participant_authorities_are_absent() -> None:
    _assert_deleted_legacy_group_participant_authorities(BACKEND_ROOT)


def test_legacy_group_participant_dao_exports_are_absent() -> None:
    _assert_deleted_legacy_group_participant_dao_exports(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_group_participant_authorities() -> None:
    _assert_tests_do_not_reference_deleted_group_participant_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_GROUP_PARTICIPANT_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_GROUP_PARTICIPANT_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_group_participant_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=(
            "deleted legacy Group/Participant authority "
            f"{representation} was reintroduced"
        ),
    ):
        _assert_deleted_legacy_group_participant_authorities(tmp_path)


@pytest.mark.parametrize(
    "package_source",
    [
        "from app.dao.group_dao import group_dao\n",
        "from app.dao.participant_dao import participant_dao as restored\n",
        "group_dao = object()\n",
        '__all__ = ["participant_dao"]\n',
        'globals()["group_dao"] = object()\n',
    ],
    ids=[
        "direct-import",
        "aliased-import",
        "assignment-reexport",
        "all-exposure",
        "globals-restoration",
    ],
)
def test_reintroduced_legacy_group_participant_dao_export_fails_guard(
    tmp_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Group/Participant DAO package export was reintroduced",
    ):
        _assert_deleted_legacy_group_participant_dao_exports(tmp_path)


def test_dynamic_group_participant_dao_export_hook_fails_guard(
    tmp_path: Path,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(
        "def __getattr__(name):\n    return object()\n",
        encoding="utf-8",
    )

    with pytest.raises(
        DeletedAuthorityViolation,
        match="app.dao package exports must be static",
    ):
        _assert_dao_package_exports_are_static(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.models.group\n",
        "from app.models.participant import Participant\n",
        "from app.dao import group_dao\n",
        "from app.api.group_websocket import websocket_group_chat\n",
        "from app.services.group_chat_service import GroupChatService\n",
        "from app.services.participant_identity import get_or_create_user_participant\n",
    ],
    ids=[
        "group-model-import",
        "participant-model-symbol-import",
        "dao-package-import",
        "websocket-symbol-import",
        "group-service-symbol-import",
        "participant-service-symbol-import",
    ],
)
def test_backend_test_static_group_participant_reference_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_group_participant.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Group/Participant authority",
    ):
        _assert_tests_do_not_reference_deleted_group_participant_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        'module = importlib.import_module("app.api.groups")\n',
        'service_path = "app.services.group_message_service.send_group_message"\n',
        'monkeypatch.setattr("app.services.group_realtime.publish_group_message_created", fake)\n',
    ],
    ids=[
        "dynamic-api-import",
        "dotted-service-reference",
        "dotted-monkeypatch-reference",
    ],
)
def test_backend_test_dynamic_group_participant_reference_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_group_participant_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Group/Participant authority",
    ):
        _assert_tests_do_not_reference_deleted_group_participant_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "from app.infrastructure.object_storage.local import LocalStorageBackend\n",
        "from app.modules.trigger import __name__\n",
    ],
    ids=[
        "object-storage-infrastructure",
        "target-trigger-module",
    ],
)
def test_retained_group_adjacent_reference_passes_group_participant_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_retained_group_adjacent_reference.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    _assert_tests_do_not_reference_deleted_group_participant_authorities(tmp_path)


def test_legacy_schedule_authorities_are_absent() -> None:
    _assert_deleted_legacy_schedule_authorities(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_schedule_authorities() -> None:
    _assert_tests_do_not_reference_deleted_schedule_authorities(BACKEND_ROOT)


def test_application_does_not_restore_legacy_schedule_definitions() -> None:
    _assert_application_does_not_restore_schedule_definitions(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_SCHEDULE_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_SCHEDULE_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_schedule_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Schedule authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_schedule_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.models.schedule\n",
        "from app.api.schedules import router\n",
        'module = importlib.import_module("app.services.scheduler")\n',
        'monkeypatch.setattr("app.scripts.migrate_schedules_to_triggers.run", fake)\n',
    ],
    ids=[
        "model-import",
        "api-symbol-import",
        "dynamic-service-import",
        "dotted-migration-reference",
    ],
)
def test_backend_test_reference_of_deleted_schedule_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_schedule.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Schedule authority",
    ):
        _assert_tests_do_not_reference_deleted_schedule_authorities(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "class AgentSchedule: ...\n",
        'class RenamedSchedule:\n    __tablename__ = "agent_schedules"\n',
    ],
    ids=["class-name", "table-name"],
)
def test_restored_schedule_definition_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/modules/trigger/restored.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="application source restores legacy Schedule definitions",
    ):
        _assert_application_does_not_restore_schedule_definitions(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "from app.modules.trigger import __name__\n",
        "from app.modules.heartbeat import __name__\n",
        "from app.modules.okr import __name__\n",
        "from app.services.timezone_utils import validate_timezone_name\n",
    ],
    ids=[
        "target-trigger-module",
        "target-heartbeat-module",
        "target-okr-module",
        "timezone-validation",
    ],
)
def test_retained_trigger_and_heartbeat_reference_passes_schedule_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_retained_trigger_heartbeat.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    _assert_tests_do_not_reference_deleted_schedule_authorities(tmp_path)


def test_target_trigger_and_heartbeat_definitions_pass_schedule_guard(
    tmp_path: Path,
) -> None:
    safe_sources = {
        Path("app/modules/trigger/service.py"): "class TriggerPolicy: ...\n",
        Path("app/modules/heartbeat/service.py"): "class HeartbeatPolicy: ...\n",
    }
    for relative_path, source in safe_sources.items():
        source_path = tmp_path / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(source, encoding="utf-8")

    _assert_application_does_not_restore_schedule_definitions(tmp_path)


def test_legacy_trigger_webhook_authorities_are_absent() -> None:
    _assert_deleted_legacy_trigger_webhook_authorities(BACKEND_ROOT)


def test_legacy_trigger_dao_export_is_absent() -> None:
    _assert_deleted_legacy_trigger_dao_export(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_trigger_webhook_authorities() -> None:
    _assert_tests_do_not_reference_deleted_trigger_webhook_authorities(BACKEND_ROOT)


def test_application_does_not_restore_legacy_trigger_webhook_definitions() -> None:
    _assert_application_does_not_restore_trigger_webhook_definitions(BACKEND_ROOT)


@pytest.mark.parametrize("owner,table", [("trigger", "agent_triggers"), ("channel", "channel_deliveries")])
@pytest.mark.parametrize("correct_owner", [True, False])
def test_s2_reused_table_names_are_limited_to_their_schema_owner(
    tmp_path: Path, owner: str, table: str, correct_owner: bool
) -> None:
    path = tmp_path / "app/modules" / (owner if correct_owner else "session") / "models.py"
    path.parent.mkdir(parents=True)
    path.write_text(f'class Record:\n    __tablename__ = "{table}"\n', encoding="utf-8")
    guard = (
        _assert_application_does_not_restore_trigger_webhook_definitions
        if owner == "trigger" else _assert_application_does_not_restore_channel_definitions
    )
    if correct_owner:
        guard(tmp_path)
    else:
        with pytest.raises(DeletedAuthorityViolation, match="restores legacy"):
            guard(tmp_path)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_TRIGGER_WEBHOOK_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_TRIGGER_WEBHOOK_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_trigger_webhook_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=(
            "deleted legacy Trigger/Webhook authority "
            f"{representation} was reintroduced"
        ),
    ):
        _assert_deleted_legacy_trigger_webhook_authorities(tmp_path)


@pytest.mark.parametrize(
    "package_source",
    [
        "from app.dao.trigger_dao import trigger_dao\n",
        "from app.dao.trigger_dao import trigger_dao as restored\n",
        "trigger_dao = object()\n",
        '__all__ = ["trigger_dao"]\n',
        'globals()["trigger_dao"] = object()\n',
    ],
    ids=[
        "direct-import",
        "aliased-import",
        "assignment-reexport",
        "all-exposure",
        "globals-restoration",
    ],
)
def test_reintroduced_legacy_trigger_dao_export_fails_guard(
    tmp_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Trigger/Webhook DAO package export was reintroduced",
    ):
        _assert_deleted_legacy_trigger_dao_export(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.models.trigger\n",
        "from app.models.trigger_execution import TriggerExecution\n",
        "from app.dao import trigger_dao\n",
        "from app.api.triggers import router\n",
        'module = importlib.import_module("app.api.webhooks")\n',
        'monkeypatch.setattr("app.services.trigger_daemon._tick", fake)\n',
        'target = "app.services.trigger_runtime.intake.TriggerRuntimeIntake"\n',
    ],
    ids=[
        "trigger-model-import",
        "execution-model-import",
        "dao-package-import",
        "trigger-api-import",
        "dynamic-webhook-api-import",
        "dotted-daemon-reference",
        "dotted-runtime-reference",
    ],
)
def test_backend_test_reference_of_deleted_trigger_webhook_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_trigger_webhook.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Trigger/Webhook authority",
    ):
        _assert_tests_do_not_reference_deleted_trigger_webhook_authorities(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "class AgentTrigger: ...\n",
        "class TriggerExecution: ...\n",
        'class RenamedTrigger:\n    __tablename__ = "agent_triggers"\n',
        'class RenamedExecution:\n    __tablename__ = "trigger_executions"\n',
    ],
    ids=[
        "trigger-class-name",
        "execution-class-name",
        "trigger-table-name",
        "execution-table-name",
    ],
)
def test_restored_trigger_webhook_definition_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/modules/trigger/restored.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="application source restores legacy Trigger/Webhook definitions",
    ):
        _assert_application_does_not_restore_trigger_webhook_definitions(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "from app.modules.trigger import __name__\n",
        "from app.modules.heartbeat import __name__\n",
    ],
    ids=[
        "target-trigger-module",
        "target-heartbeat-module",
    ],
)
def test_retained_target_and_channel_reference_passes_trigger_webhook_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_retained_trigger_webhook_names.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    _assert_tests_do_not_reference_deleted_trigger_webhook_authorities(tmp_path)


def test_target_trigger_heartbeat_and_channel_definitions_pass_guard(
    tmp_path: Path,
) -> None:
    safe_sources = {
        Path("app/modules/trigger/service.py"): "class TriggerPolicy: ...\n",
        Path("app/modules/heartbeat/service.py"): "class HeartbeatPolicy: ...\n",
        Path("app/api/feishu.py"): "def feishu_event_webhook(): ...\n",
    }
    for relative_path, source in safe_sources.items():
        source_path = tmp_path / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(source, encoding="utf-8")

    _assert_application_does_not_restore_trigger_webhook_definitions(tmp_path)


def test_legacy_heartbeat_authorities_are_absent() -> None:
    _assert_deleted_legacy_heartbeat_authorities(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_heartbeat_authorities() -> None:
    _assert_tests_do_not_reference_deleted_heartbeat_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_HEARTBEAT_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_HEARTBEAT_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_heartbeat_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Heartbeat authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_heartbeat_authorities(tmp_path)


@pytest.mark.parametrize("representation", ["file", "directory"])
def test_reintroduced_legacy_heartbeat_template_path_fails_guard(
    tmp_path: Path,
    representation: str,
) -> None:
    template_path = tmp_path / LEGACY_HEARTBEAT_TEMPLATE_PATH
    template_path.parent.mkdir(parents=True, exist_ok=True)
    if representation == "file":
        template_path.write_text("legacy heartbeat", encoding="utf-8")
    else:
        template_path.mkdir()

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Heartbeat template path was reintroduced",
    ):
        _assert_deleted_legacy_heartbeat_authorities(tmp_path)


@pytest.mark.parametrize(
    "legacy_path",
    sorted(LEGACY_HEARTBEAT_SANDBOX_FORBIDDEN_PATHS),
)
def test_restored_sandbox_heartbeat_root_path_fails_guard(
    tmp_path: Path,
    legacy_path: str,
) -> None:
    source_path = tmp_path / LEGACY_HEARTBEAT_SANDBOX_SOURCE
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        f"ROOT_FILES = ({legacy_path!r},)\n",
        encoding="utf-8",
    )

    with pytest.raises(
        DeletedAuthorityViolation,
        match="Sandbox recognizes the deleted legacy Heartbeat root path",
    ):
        _assert_deleted_legacy_heartbeat_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.services.heartbeat\n",
        "from app.services.heartbeat_runtime import enqueue_heartbeat_runtime\n",
        "from app.scripts import migrate_legacy_heartbeat_template\n",
        'module = importlib.import_module("app.services.heartbeat")\n',
        'monkeypatch.setattr("app.services.heartbeat_runtime.enqueue_heartbeat_runtime", fake)\n',
        'target = "app.scripts.migrate_legacy_heartbeat_template.main"\n',
    ],
    ids=[
        "heartbeat-service-import",
        "heartbeat-runtime-import",
        "heartbeat-script-import",
        "dynamic-service-import",
        "dotted-runtime-reference",
        "dotted-script-reference",
    ],
)
def test_backend_test_reference_of_deleted_heartbeat_authority_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_heartbeat.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Heartbeat authority",
    ):
        _assert_tests_do_not_reference_deleted_heartbeat_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "from app.modules.heartbeat import __name__\n",
        "from app.services.sandbox.execution_lease import ExecutionLease\n",
        'metric_name = "heartbeat_count"\n',
        'task_name = "sandbox.heartbeat"\n',
    ],
    ids=[
        "target-heartbeat-module",
        "sandbox-execution-lease",
        "lock-heartbeat-count",
        "sandbox-heartbeat-word",
    ],
)
def test_retained_heartbeat_names_pass_legacy_heartbeat_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_retained_heartbeat_names.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    _assert_tests_do_not_reference_deleted_heartbeat_authorities(tmp_path)


def test_nonlegacy_heartbeat_template_path_passes_guard(tmp_path: Path) -> None:
    template_path = tmp_path / "app/templates/HEARTBEAT.md"
    template_path.parent.mkdir(parents=True)
    template_path.write_text("target template inventory", encoding="utf-8")

    _assert_deleted_legacy_heartbeat_authorities(tmp_path)


def test_legacy_workspace_authorities_are_absent_from_target_tree() -> None:
    _assert_deleted_legacy_workspace_authorities(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_workspace_authorities() -> None:
    _assert_tests_do_not_reference_deleted_workspace_authorities(BACKEND_ROOT)


def test_application_does_not_restore_legacy_workspace_definitions() -> None:
    _assert_application_does_not_restore_workspace_definitions(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_WORKSPACE_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_WORKSPACE_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_workspace_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Workspace authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_workspace_authorities(tmp_path)


@pytest.mark.parametrize(
    ("identity", "reference_kind"),
    [
        (identity, reference_kind)
        for identity in LEGACY_WORKSPACE_DOTTED_IMPORT_IDENTITIES
        for reference_kind in ("static", "dotted")
    ],
)
def test_backend_test_reference_of_deleted_workspace_authority_fails_guard(
    tmp_path: Path,
    identity: str,
    reference_kind: str,
) -> None:
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'target = "{identity}.restored"\n'
    )
    test_path = tmp_path / "tests/test_restored_workspace.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Workspace authority",
    ):
        _assert_tests_do_not_reference_deleted_workspace_authorities(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "class WorkspaceFileRevision: ...\n",
        "class WorkspaceEditLock: ...\n",
        'class Restored:\n    __tablename__ = "workspace_file_revisions"\n',
        'class Restored:\n    __tablename__ = "workspace_edit_locks"\n',
    ],
    ids=[
        "file-revision-class",
        "edit-lock-class",
        "file-revisions-table",
        "edit-locks-table",
    ],
)
def test_restored_workspace_definition_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/modules/workspace/restored.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="application source restores legacy Workspace definitions",
    ):
        _assert_application_does_not_restore_workspace_definitions(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "from app.infrastructure.object_storage.base import StorageBackend\n",
        "from app.infrastructure.object_storage.local import LocalStorageBackend\n",
        "from app.services.sandbox.config import SandboxConfig\n",
        "from app.services.sandbox.workspace_policy import SandboxWorkspacePolicy\n",
        "from app.modules.workspace import __name__\n",
    ],
    ids=[
        "object-storage-contract",
        "object-storage-local",
        "sandbox",
        "sandbox-workspace-policy",
        "target-workspace-module",
    ],
)
def test_retained_workspace_adjacent_reference_passes_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_retained_workspace_adjacent.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    _assert_tests_do_not_reference_deleted_workspace_authorities(tmp_path)


def test_sandbox_has_no_legacy_workspace_revision_branch() -> None:
    _assert_sandbox_has_no_legacy_revision_branch(BACKEND_ROOT)


@pytest.mark.parametrize(
    "source",
    [
        "async def merge(*, record_revisions=False): ...\n",
        "from app.database import async_session\n",
        "from app.services.workspace_collaboration import write_workspace_file\n",
        "result = write_workspace_file()\n",
        "result = delete_workspace_file()\n",
    ],
)
def test_restored_sandbox_workspace_revision_branch_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/services/sandbox/local/subprocess_backend.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="revision branch"):
        _assert_sandbox_has_no_legacy_revision_branch(tmp_path)


def test_sandbox_workspace_publication_without_revision_branch_passes_guard(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "app/services/sandbox/local/subprocess_backend.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "async def merge(*, workspace_mode, publish_paths): ...\n",
        encoding="utf-8",
    )
    _assert_sandbox_has_no_legacy_revision_branch(tmp_path)


def test_legacy_a2a_authority_is_absent_from_target_tree() -> None:
    _assert_deleted_legacy_a2a_authorities(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_a2a_authority() -> None:
    _assert_tests_do_not_reference_deleted_a2a_authorities(BACKEND_ROOT)


def test_legacy_advanced_api_is_absent() -> None:
    _assert_deleted_legacy_advanced_api(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_advanced_api() -> None:
    _assert_tests_do_not_reference_deleted_advanced_api(BACKEND_ROOT)


def test_application_apis_do_not_restore_legacy_advanced_facts() -> None:
    _assert_application_apis_do_not_restore_legacy_advanced_facts(BACKEND_ROOT)


@pytest.mark.parametrize("representation", ["module", "package"])
def test_reintroduced_legacy_advanced_api_fails_guard(
    tmp_path: Path,
    representation: str,
) -> None:
    authority = tmp_path / LEGACY_ADVANCED_API_IMPORT_IDENTITY
    if representation == "module":
        authority.parent.mkdir(parents=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy advanced API {representation} was reintroduced",
    ):
        _assert_deleted_legacy_advanced_api(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_advanced_api_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    source = (
        f"import {LEGACY_ADVANCED_API_DOTTED_IMPORT_IDENTITY}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{LEGACY_ADVANCED_API_DOTTED_IMPORT_IDENTITY}")\n'
    )
    test_path = tmp_path / "tests/test_restored_advanced.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy advanced API authority",
    ):
        _assert_tests_do_not_reference_deleted_advanced_api(tmp_path)


def test_legacy_activity_api_is_absent() -> None:
    _assert_deleted_legacy_activity_api(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_activity_api() -> None:
    _assert_tests_do_not_reference_deleted_activity_api(BACKEND_ROOT)


def test_application_apis_do_not_restore_legacy_activity_facts() -> None:
    _assert_application_apis_do_not_restore_legacy_activity_facts(BACKEND_ROOT)


@pytest.mark.parametrize("representation", ["module", "package"])
def test_reintroduced_legacy_activity_api_fails_guard(
    tmp_path: Path,
    representation: str,
) -> None:
    authority = tmp_path / LEGACY_ACTIVITY_API_IMPORT_IDENTITY
    if representation == "module":
        authority.parent.mkdir(parents=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Activity API {representation} was reintroduced",
    ):
        _assert_deleted_legacy_activity_api(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_activity_api_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    source = (
        f"import {LEGACY_ACTIVITY_API_DOTTED_IMPORT_IDENTITY}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{LEGACY_ACTIVITY_API_DOTTED_IMPORT_IDENTITY}")\n'
    )
    test_path = tmp_path / "tests/test_restored_activity_api.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Activity API authority",
    ):
        _assert_tests_do_not_reference_deleted_activity_api(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "async def get_agent_activity(): ...\n",
        "async def list_conversations(): ...\n",
        "async def get_conversation_messages(): ...\n",
        '@router.get("/agents/{agent_id}/activity")\nasync def restored(): ...\n',
        '@router.get("/agents/{agent_id}/chat-history/conversations")\nasync def restored(): ...\n',
        '@router.get("/agents/{agent_id}/chat-history/{conv_id:path}")\nasync def restored(): ...\n',
    ],
)
def test_restored_activity_transport_fact_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/api/restored_activity.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="application API restores legacy Activity transport facts",
    ):
        _assert_application_apis_do_not_restore_legacy_activity_facts(tmp_path)


def test_legacy_messages_api_is_absent() -> None:
    _assert_deleted_legacy_messages_api(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_messages_api() -> None:
    _assert_tests_do_not_reference_deleted_messages_api(BACKEND_ROOT)


def test_application_apis_do_not_restore_legacy_messages_facts() -> None:
    _assert_application_apis_do_not_restore_legacy_messages_facts(BACKEND_ROOT)


@pytest.mark.parametrize("representation", ["module", "package"])
def test_reintroduced_legacy_messages_api_fails_guard(
    tmp_path: Path,
    representation: str,
) -> None:
    authority = tmp_path / LEGACY_MESSAGES_API_IMPORT_IDENTITY
    if representation == "module":
        authority.parent.mkdir(parents=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Messages API {representation} was reintroduced",
    ):
        _assert_deleted_legacy_messages_api(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_messages_api_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    identity = LEGACY_MESSAGES_API_DOTTED_IMPORT_IDENTITY
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_messages_api.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Messages API authority",
    ):
        _assert_tests_do_not_reference_deleted_messages_api(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "async def get_inbox(): ...\n",
        "async def get_unread_count(): ...\n",
        '@router.get("/messages/inbox")\nasync def restored(): ...\n',
        '@router.get("/messages/unread-count")\nasync def restored(): ...\n',
    ],
)
def test_restored_messages_transport_fact_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/api/restored_messages.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match="application API restores legacy Messages transport facts",
    ):
        _assert_application_apis_do_not_restore_legacy_messages_facts(tmp_path)


def test_legacy_admin_api_is_absent() -> None:
    _assert_deleted_legacy_admin_api(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_admin_api() -> None:
    _assert_tests_do_not_reference_deleted_admin_api(BACKEND_ROOT)


def test_application_apis_do_not_restore_legacy_admin_facts() -> None:
    _assert_application_apis_do_not_restore_legacy_admin_facts(BACKEND_ROOT)


@pytest.mark.parametrize("representation", ["module", "package"])
def test_reintroduced_legacy_admin_api_fails_guard(
    tmp_path: Path,
    representation: str,
) -> None:
    authority = tmp_path / LEGACY_ADMIN_API_IMPORT_IDENTITY
    if representation == "module":
        authority.parent.mkdir(parents=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Admin API {representation} was reintroduced",
    ):
        _assert_deleted_legacy_admin_api(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_admin_api_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    identity = LEGACY_ADMIN_API_DOTTED_IMPORT_IDENTITY
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_admin_api.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Admin API authority",
    ):
        _assert_tests_do_not_reference_deleted_admin_api(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        *(f"class {name}: ...\n" for name in (
            "CompanyStats", "CompanyCreateRequest", "CompanyCreateResponse",
            "PlatformSettingsOut", "PlatformSettingsUpdate",
        )),
        *(f"async def {name}(): ...\n" for name in (
            "list_companies", "create_company", "toggle_company",
            "get_platform_timeseries", "get_platform_leaderboards",
            "get_enhanced_metrics", "get_platform_settings",
            "update_platform_settings",
        )),
        '@router.get("/companies")\nasync def restored(): ...\n',
        '@router.post("/companies")\nasync def restored(): ...\n',
        '@router.put("/companies/{company_id}/toggle")\nasync def restored(): ...\n',
        '@router.get("/metrics/timeseries")\nasync def restored(): ...\n',
        '@router.get("/metrics/leaderboards")\nasync def restored(): ...\n',
        '@router.get("/metrics/enhanced")\nasync def restored(): ...\n',
        '@router.get("/platform-settings")\nasync def restored(): ...\n',
        '@router.put("/platform-settings")\nasync def restored(): ...\n',
    ],
)
def test_restored_admin_transport_fact_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/api/restored_admin.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match="application API restores legacy Platform Administration facts",
    ):
        _assert_application_apis_do_not_restore_legacy_admin_facts(tmp_path)


def test_legacy_enterprise_transport_is_absent() -> None:
    _assert_deleted_legacy_enterprise_transport(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_enterprise_transport() -> None:
    _assert_tests_do_not_reference_deleted_enterprise_transport(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_ENTERPRISE_TRANSPORT_REINTRODUCTIONS,
)
def test_reintroduced_legacy_enterprise_transport_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Enterprise transport {representation} was reintroduced",
    ):
        _assert_deleted_legacy_enterprise_transport(tmp_path)


@pytest.mark.parametrize("test_path", LEGACY_ENTERPRISE_TRANSPORT_TEST_PATHS)
def test_reintroduced_legacy_enterprise_transport_test_fails_guard(
    tmp_path: Path,
    test_path: Path,
) -> None:
    restored = tmp_path / test_path
    restored.parent.mkdir(parents=True)
    restored.write_text("", encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Enterprise transport test was reintroduced",
    ):
        _assert_deleted_legacy_enterprise_transport(tmp_path)


@pytest.mark.parametrize(
    ("identity", "reference_kind"),
    [
        (identity, reference_kind)
        for identity in LEGACY_ENTERPRISE_TRANSPORT_DOTTED_IDENTITIES
        for reference_kind in ("static", "dotted")
    ],
)
def test_backend_test_reference_of_deleted_enterprise_transport_fails_guard(
    tmp_path: Path,
    identity: str,
    reference_kind: str,
) -> None:
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_enterprise_transport.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Enterprise transport authority",
    ):
        _assert_tests_do_not_reference_deleted_enterprise_transport(tmp_path)


def test_observability_audit_orphan_services_are_absent() -> None:
    _assert_deleted_observability_audit_services(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_observability_audit_services() -> None:
    _assert_tests_do_not_reference_deleted_observability_audit_services(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    [
        (identity, representation)
        for identity in LEGACY_OBSERVABILITY_AUDIT_SERVICE_IDENTITIES
        for representation in ("module", "package")
    ],
)
def test_reintroduced_observability_audit_service_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy observability/audit service {representation} was reintroduced",
    ):
        _assert_deleted_observability_audit_services(tmp_path)


@pytest.mark.parametrize(
    ("identity", "reference_kind"),
    [
        (identity, reference_kind)
        for identity in LEGACY_OBSERVABILITY_AUDIT_SERVICE_DOTTED_IDENTITIES
        for reference_kind in ("static", "dotted")
    ],
)
def test_backend_test_reference_of_deleted_observability_audit_service_fails_guard(
    tmp_path: Path,
    identity: str,
    reference_kind: str,
) -> None:
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_observability_audit.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy observability/audit service authority",
    ):
        _assert_tests_do_not_reference_deleted_observability_audit_services(tmp_path)


def test_legacy_platform_service_is_absent() -> None:
    _assert_deleted_platform_service(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_platform_service() -> None:
    _assert_tests_do_not_reference_deleted_platform_service(BACKEND_ROOT)


@pytest.mark.parametrize("representation", ["module", "package"])
def test_reintroduced_platform_service_fails_guard(
    tmp_path: Path,
    representation: str,
) -> None:
    authority = tmp_path / LEGACY_PLATFORM_SERVICE_IDENTITY
    if representation == "module":
        authority.parent.mkdir(parents=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Platform service {representation} was reintroduced",
    ):
        _assert_deleted_platform_service(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_platform_service_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    identity = LEGACY_PLATFORM_SERVICE_DOTTED_IDENTITY
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_platform_service.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Platform service authority",
    ):
        _assert_tests_do_not_reference_deleted_platform_service(tmp_path)


def test_legacy_quota_guard_is_absent() -> None:
    _assert_deleted_quota_guard(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_quota_guard() -> None:
    _assert_tests_do_not_reference_deleted_quota_guard(BACKEND_ROOT)


@pytest.mark.parametrize("representation", ["module", "package"])
def test_reintroduced_quota_guard_fails_guard(
    tmp_path: Path,
    representation: str,
) -> None:
    authority = tmp_path / LEGACY_QUOTA_GUARD_IDENTITY
    if representation == "module":
        authority.parent.mkdir(parents=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy quota guard {representation} was reintroduced",
    ):
        _assert_deleted_quota_guard(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_quota_guard_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    identity = LEGACY_QUOTA_GUARD_DOTTED_IDENTITY
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_quota_guard.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy quota guard authority",
    ):
        _assert_tests_do_not_reference_deleted_quota_guard(tmp_path)


def test_legacy_realtime_services_are_absent() -> None:
    _assert_deleted_realtime_services(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_realtime_services() -> None:
    _assert_tests_do_not_reference_deleted_realtime_services(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    [
        (identity, representation)
        for identity in LEGACY_REALTIME_SERVICE_IDENTITIES
        for representation in ("module", "package")
    ],
)
def test_reintroduced_realtime_service_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Realtime service {representation} was reintroduced",
    ):
        _assert_deleted_realtime_services(tmp_path)


@pytest.mark.parametrize(
    ("identity", "reference_kind"),
    [
        (identity, reference_kind)
        for identity in LEGACY_REALTIME_SERVICE_DOTTED_IDENTITIES
        for reference_kind in ("static", "dotted")
    ],
)
def test_backend_test_reference_of_deleted_realtime_service_fails_guard(
    tmp_path: Path,
    identity: str,
    reference_kind: str,
) -> None:
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}.router")\n'
    )
    test_path = tmp_path / "tests/test_restored_realtime.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Realtime service authority",
    ):
        _assert_tests_do_not_reference_deleted_realtime_services(tmp_path)


def test_legacy_resource_discovery_is_absent() -> None:
    _assert_deleted_resource_discovery(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_resource_discovery() -> None:
    _assert_tests_do_not_reference_deleted_resource_discovery(BACKEND_ROOT)


@pytest.mark.parametrize("representation", ["module", "package"])
def test_reintroduced_resource_discovery_fails_guard(
    tmp_path: Path,
    representation: str,
) -> None:
    authority = tmp_path / LEGACY_RESOURCE_DISCOVERY_IDENTITY
    if representation == "module":
        authority.parent.mkdir(parents=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy resource discovery {representation} was reintroduced",
    ):
        _assert_deleted_resource_discovery(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_resource_discovery_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    identity = LEGACY_RESOURCE_DISCOVERY_DOTTED_IDENTITY
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_resource_discovery.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy resource discovery authority",
    ):
        _assert_tests_do_not_reference_deleted_resource_discovery(tmp_path)


def test_legacy_system_email_service_is_absent() -> None:
    _assert_deleted_system_email_service(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_system_email_service() -> None:
    _assert_tests_do_not_reference_deleted_system_email_service(BACKEND_ROOT)


@pytest.mark.parametrize("representation", ["module", "package", "test"])
def test_reintroduced_system_email_service_fails_guard(
    tmp_path: Path,
    representation: str,
) -> None:
    authority = tmp_path / LEGACY_SYSTEM_EMAIL_SERVICE_IDENTITY
    if representation == "module":
        authority.parent.mkdir(parents=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    elif representation == "package":
        authority.mkdir(parents=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    else:
        test_path = tmp_path / LEGACY_SYSTEM_EMAIL_TEST_PATH
        test_path.parent.mkdir(parents=True)
        test_path.write_text("", encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="was reintroduced"):
        _assert_deleted_system_email_service(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_system_email_service_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    identity = LEGACY_SYSTEM_EMAIL_SERVICE_DOTTED_IDENTITY
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_system_email.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy System Email service authority",
    ):
        _assert_tests_do_not_reference_deleted_system_email_service(tmp_path)


def test_legacy_vision_maintenance_authorities_are_absent() -> None:
    _assert_deleted_vision_maintenance_authorities(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_vision_maintenance_authorities() -> None:
    _assert_tests_do_not_reference_deleted_vision_maintenance_authorities(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    [
        (identity, representation)
        for identity in LEGACY_VISION_MAINTENANCE_IDENTITIES
        for representation in ("module", "package")
    ],
)
def test_reintroduced_vision_maintenance_authority_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy vision/maintenance {representation} was reintroduced",
    ):
        _assert_deleted_vision_maintenance_authorities(tmp_path)


@pytest.mark.parametrize(
    ("identity", "reference_kind"),
    [
        (identity, reference_kind)
        for identity in LEGACY_VISION_MAINTENANCE_DOTTED_IDENTITIES
        for reference_kind in ("static", "dotted")
    ],
)
def test_backend_test_reference_of_deleted_vision_maintenance_authority_fails_guard(
    tmp_path: Path,
    identity: str,
    reference_kind: str,
) -> None:
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_vision_maintenance.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy vision/maintenance authority",
    ):
        _assert_tests_do_not_reference_deleted_vision_maintenance_authorities(tmp_path)


def test_orphan_maintenance_authorities_and_invocations_are_absent() -> None:
    _assert_deleted_orphan_maintenance_authorities(BACKEND_ROOT)
    _assert_tests_do_not_reference_deleted_orphan_maintenance(BACKEND_ROOT)
    _assert_no_orphan_maintenance_executable_invocations(BACKEND_ROOT.parent)


@pytest.mark.parametrize(
    ("identity", "representation"),
    [
        (identity, representation)
        for identity in LEGACY_ORPHAN_MAINTENANCE_IDENTITIES
        for representation in ("module", "package")
    ],
)
def test_reintroduced_orphan_maintenance_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="was reintroduced"):
        _assert_deleted_orphan_maintenance_authorities(tmp_path)


@pytest.mark.parametrize(
    ("identity", "reference_kind"),
    [
        (identity, reference_kind)
        for identity in LEGACY_ORPHAN_MAINTENANCE_DOTTED_IDENTITIES
        for reference_kind in ("static", "dotted")
    ],
)
def test_backend_test_reference_of_deleted_orphan_maintenance_fails_guard(
    tmp_path: Path,
    identity: str,
    reference_kind: str,
) -> None:
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_maintenance.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="maintenance authority"):
        _assert_tests_do_not_reference_deleted_orphan_maintenance(tmp_path)


@pytest.mark.parametrize(
    "invocation",
    LEGACY_ORPHAN_MAINTENANCE_INVOCATIONS,
)
def test_restored_orphan_maintenance_shell_or_yaml_invocation_fails_guard(
    tmp_path: Path,
    invocation: str,
) -> None:
    workflow = tmp_path / ".github/workflows/maintenance.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(f"steps:\n  - run: uv run python {invocation}\n", encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="maintenance invocation"):
        _assert_no_orphan_maintenance_executable_invocations(tmp_path)


@pytest.mark.parametrize(
    ("relative_path", "source"),
    [
        (
            Path("scripts/legacy.sh"),
            "uv run python backend/remove_old_tool.py\n",
        ),
        (
            Path("pyproject.toml"),
            '[project.scripts]\nlegacy-schema = "update_schema:main"\n',
        ),
        (
            Path("deploy/job.yaml"),
            "job:\n  command: python scripts/backfill_chat_message_tenant_id.py --apply\n",
        ),
        (
            Path("deploy/extra.yaml"),
            "job:\n  run: uv run --extra dev python backend/remove_old_tool.py\n",
        ),
        (
            Path("deploy/project.yaml"),
            "job:\n  run: uv run --project backend python update_schema.py\n",
        ),
        (
            Path("scripts/python-x.sh"),
            "python -X dev backend/remove_old_tool.py\n",
        ),
        (
            Path("scripts/python-w.sh"),
            "python -W ignore scripts/backfill_chat_message_tenant_id.py\n",
        ),
    ],
)
def test_restored_orphan_maintenance_executable_field_fails_guard(
    tmp_path: Path,
    relative_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / relative_path
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="maintenance invocation"):
        _assert_no_orphan_maintenance_executable_invocations(tmp_path)


def test_current_migration_and_script_fixtures_pass_orphan_maintenance_guards(
    tmp_path: Path,
) -> None:
    current_sources = {
        Path("backend/alembic/versions/001_current_schema.py"): "revision = '001'\n",
        Path("backend/scripts/current_backfill.py"): "def main(): ...\n",
        Path(".github/workflows/migrate.yml"): (
            "description: remove_old_tool.py and update_schema.py are retired\n"
            "steps:\n"
            "  - name: backfill_chat_message_tenant_id.py is obsolete\n"
            "    run: echo remove_old_tool.py is retired\n"
            "  - run: uv run alembic upgrade head\n"
        ),
        Path("backend/pyproject.toml"): (
            "[project]\n"
            'description = "update_schema.py is not an executable entry"\n'
            "[tool.current]\n"
            'note = "backfill_chat_message_tenant_id.py remains deleted"\n'
        ),
        Path("scripts/validate.sh"): (
            "# python backend/remove_old_tool.py is intentionally absent\n"
            "echo update_schema.py is retired\n"
            "rg remove_old_tool.py backend\n"
            "grep -R backfill_chat_message_tenant_id.py backend\n"
            "test ! -f update_schema.py\n"
            "uv run --extra dev rg remove_old_tool.py backend\n"
            "uv run python backend/scripts/validate_goal_gates.py\n"
        ),
        Path("backend/tests/test_current_script.py"): (
            "from app.infrastructure.database import Base\n"
        ),
    }
    for relative_path, source in current_sources.items():
        source_path = tmp_path / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(source, encoding="utf-8")
    _assert_deleted_orphan_maintenance_authorities(tmp_path / "backend")
    _assert_tests_do_not_reference_deleted_orphan_maintenance(tmp_path / "backend")
    _assert_no_orphan_maintenance_executable_invocations(tmp_path)


def test_legacy_observability_audit_persistence_is_absent() -> None:
    _assert_deleted_observability_audit_persistence(BACKEND_ROOT)
    _assert_deleted_observability_audit_dao_exports(BACKEND_ROOT)
    _assert_tests_do_not_reference_deleted_observability_audit_persistence(BACKEND_ROOT)
    _assert_application_does_not_restore_observability_audit_facts(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    [
        (identity, representation)
        for identity in LEGACY_OBSERVABILITY_AUDIT_PERSISTENCE_IDENTITIES
        for representation in ("module", "package")
    ],
)
def test_reintroduced_observability_audit_persistence_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="was reintroduced"):
        _assert_deleted_observability_audit_persistence(tmp_path)


@pytest.mark.parametrize(
    ("identity", "reference_kind"),
    [
        (identity, reference_kind)
        for identity in LEGACY_OBSERVABILITY_AUDIT_PERSISTENCE_DOTTED_IDENTITIES
        for reference_kind in ("static", "dotted")
    ],
)
def test_backend_test_reference_of_deleted_observability_audit_persistence_fails_guard(
    tmp_path: Path,
    identity: str,
    reference_kind: str,
) -> None:
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_observability_persistence.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="persistence authority"):
        _assert_tests_do_not_reference_deleted_observability_audit_persistence(tmp_path)


@pytest.mark.parametrize("export", LEGACY_OBSERVABILITY_AUDIT_DAO_EXPORTS)
def test_reintroduced_observability_audit_dao_export_fails_guard(
    tmp_path: Path,
    export: str,
) -> None:
    dao_init = tmp_path / DAO_PACKAGE_INIT
    dao_init.parent.mkdir(parents=True)
    dao_init.write_text(f"from app.dao import {export}\n", encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="package export"):
        _assert_deleted_observability_audit_dao_exports(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "class AgentActivityLog: ...\n",
        "class DailyTokenUsage: ...\n",
        "class AuditLog: ...\n",
        "class EnterpriseInfo: ...\n",
        'class Restored:\n    __tablename__ = "agent_activity_logs"\n',
        'class Restored:\n    __tablename__ = "daily_token_usage"\n',
        'class Restored:\n    __tablename__ = "audit_logs"\n',
        'class Restored:\n    __tablename__ = "enterprise_info"\n',
    ],
)
def test_restored_observability_audit_fact_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/modules/observability/restored.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="persistence facts"):
        _assert_application_does_not_restore_observability_audit_facts(tmp_path)


def test_legacy_run_setting_persistence_is_absent() -> None:
    _assert_deleted_run_setting_persistence(BACKEND_ROOT)
    _assert_deleted_run_setting_dao_exports(BACKEND_ROOT)
    _assert_tests_do_not_reference_deleted_run_setting_persistence(BACKEND_ROOT)
    _assert_application_does_not_restore_run_setting_facts(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    [
        (identity, representation)
        for identity in LEGACY_RUN_SETTING_PERSISTENCE_IDENTITIES
        for representation in ("module", "package")
    ],
)
def test_reintroduced_run_setting_persistence_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="was reintroduced"):
        _assert_deleted_run_setting_persistence(tmp_path)


@pytest.mark.parametrize(
    ("identity", "reference_kind"),
    [
        (identity, reference_kind)
        for identity in LEGACY_RUN_SETTING_PERSISTENCE_DOTTED_IDENTITIES
        for reference_kind in ("static", "dotted")
    ],
)
def test_backend_test_reference_of_deleted_run_setting_persistence_fails_guard(
    tmp_path: Path,
    identity: str,
    reference_kind: str,
) -> None:
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_run_setting_persistence.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="persistence authority"):
        _assert_tests_do_not_reference_deleted_run_setting_persistence(tmp_path)


@pytest.mark.parametrize("export", LEGACY_RUN_SETTING_DAO_EXPORTS)
def test_reintroduced_run_setting_dao_export_fails_guard(
    tmp_path: Path,
    export: str,
) -> None:
    dao_init = tmp_path / DAO_PACKAGE_INIT
    dao_init.parent.mkdir(parents=True)
    dao_init.write_text(f"from app.dao import {export}\n", encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="package export"):
        _assert_deleted_run_setting_dao_exports(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "class SystemSetting: ...\n",
        'class Restored:\n    __tablename__ = "system_settings"\n',
    ],
)
def test_restored_run_setting_fact_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/modules/platform_administration/restored.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="persistence facts"):
        _assert_application_does_not_restore_run_setting_facts(tmp_path)


def test_legacy_core_compatibility_authorities_are_absent() -> None:
    _assert_deleted_core_compatibility_authorities(BACKEND_ROOT)
    _assert_tests_do_not_reference_deleted_core_compatibility_authorities(BACKEND_ROOT)
    _assert_application_does_not_restore_core_compatibility_facts(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    [
        (identity, representation)
        for identity in LEGACY_CORE_COMPATIBILITY_IDENTITIES
        for representation in ("module", "package")
    ],
)
def test_reintroduced_core_compatibility_authority_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="was reintroduced"):
        _assert_deleted_core_compatibility_authorities(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_core_compatibility_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    identity = LEGACY_CORE_COMPATIBILITY_DOTTED_IDENTITIES[0]
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_core_compatibility.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="compatibility authority"):
        _assert_tests_do_not_reference_deleted_core_compatibility_authorities(tmp_path)


def test_reintroduced_legacy_error_contract_test_fails_guard(tmp_path: Path) -> None:
    test_path = tmp_path / LEGACY_ERROR_CONTRACT_TEST
    test_path.parent.mkdir(parents=True)
    test_path.write_text("", encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="test was reintroduced"):
        _assert_deleted_core_compatibility_authorities(tmp_path)


def test_legacy_base_dao_test_is_absent() -> None:
    _assert_legacy_base_dao_test_is_absent(BACKEND_ROOT)


def test_reintroduced_legacy_base_dao_test_fails_guard(tmp_path: Path) -> None:
    test_path = tmp_path / LEGACY_BASE_DAO_TEST
    test_path.parent.mkdir(parents=True)
    test_path.write_text("", encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="test was reintroduced"):
        _assert_legacy_base_dao_test_is_absent(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "class RosterVisibility: ...\n",
        "class TenantContextMiddleware: ...\n",
        "class TraceIdMiddleware: ...\n",
        "def build_visible_agents_query(): ...\n",
        "async def can_manage_agent(): ...\n",
        "async def can_use_agent(): ...\n",
        "async def check_agent_access(): ...\n",
        "def register_error_handlers(): ...\n",
    ],
)
def test_restored_core_compatibility_fact_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/core/restored.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="compatibility facts"):
        _assert_application_does_not_restore_core_compatibility_facts(tmp_path)


def test_legacy_logging_config_authority_is_absent() -> None:
    _assert_deleted_legacy_logging_config_authority(BACKEND_ROOT)
    _assert_tests_do_not_reference_deleted_logging_config(BACKEND_ROOT)
    _assert_application_does_not_restore_logging_config_definitions(BACKEND_ROOT)


@pytest.mark.parametrize("representation", ["module", "package"])
def test_reintroduced_legacy_logging_config_identity_fails_guard(
    tmp_path: Path,
    representation: str,
) -> None:
    authority = tmp_path / LEGACY_LOGGING_CONFIG_IDENTITY
    if representation == "module":
        authority.parent.mkdir(parents=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="was reintroduced"):
        _assert_deleted_legacy_logging_config_authority(tmp_path)


def test_adjacent_core_modules_pass_logging_config_identity_guard(
    tmp_path: Path,
) -> None:
    for relative_path in (
        Path("app/core/email.py"),
    ):
        source_path = tmp_path / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("", encoding="utf-8")
    _assert_deleted_legacy_logging_config_authority(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_logging_config_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    source = (
        f"import {LEGACY_LOGGING_CONFIG_DOTTED_IDENTITY}\n"
        if reference_kind == "static"
        else (
            "module = importlib.import_module("
            f'"{LEGACY_LOGGING_CONFIG_DOTTED_IDENTITY}")\n'
        )
    )
    test_path = tmp_path / "tests/test_restored_logging_config.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="configuration authority"):
        _assert_tests_do_not_reference_deleted_logging_config(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "from app.core.email import force_ipv4\n",
    ],
)
def test_adjacent_core_reference_passes_logging_config_test_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_adjacent_core.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")
    _assert_tests_do_not_reference_deleted_logging_config(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "trace_id_var = object()\n",
        "NOISY_CONNECTION_LOGGERS = {}\n",
        "configured_logger = object()\n",
        "def get_trace_id(): ...\n",
        "def set_trace_id(value): ...\n",
        "def new_trace_id(): ...\n",
        "def _disable_agentbay_logger_override(): ...\n",
        "def configure_logging(): ...\n",
        "def quiet_noisy_connection_loggers(): ...\n",
        "def intercept_standard_logging(): ...\n",
    ],
)
def test_restored_logging_config_definition_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/infrastructure/restored_logging.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="configuration definitions"):
        _assert_application_does_not_restore_logging_config_definitions(tmp_path)


def test_adjacent_logging_definition_passes_logging_config_guard(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "app/infrastructure/provider_logging.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "provider_log_levels = {}\ndef configure_provider_logger(): ...\n",
        encoding="utf-8",
    )
    _assert_application_does_not_restore_logging_config_definitions(tmp_path)


def test_legacy_security_dao_authorities_are_absent() -> None:
    _assert_deleted_legacy_security_dao_authorities(BACKEND_ROOT)
    _assert_target_dao_package_is_empty(BACKEND_ROOT)
    _assert_tests_do_not_reference_deleted_security_dao_authorities(BACKEND_ROOT)
    _assert_application_does_not_restore_security_dao_definitions(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    [
        (identity, representation)
        for identity in LEGACY_SECURITY_DAO_IDENTITIES
        for representation in ("module", "package")
    ],
)
def test_reintroduced_legacy_security_dao_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="was reintroduced"):
        _assert_deleted_legacy_security_dao_authorities(tmp_path)


@pytest.mark.parametrize(
    "package_source",
    [
        "from app.dao.base import BaseDAO\n",
        "query_dao = object()\n",
        '__all__ = ["query_dao"]\n',
    ],
)
def test_nonempty_legacy_dao_package_initializer_fails_guard(
    tmp_path: Path,
    package_source: str,
) -> None:
    package_init = tmp_path / DAO_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(package_source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="must remain empty"):
        _assert_target_dao_package_is_empty(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_security_dao_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    identity = LEGACY_SECURITY_DAO_DOTTED_IDENTITIES[0]
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_security_dao.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="Security/DAO authority"):
        _assert_tests_do_not_reference_deleted_security_dao_authorities(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "class BaseDAO: ...\n",
        "class TenantScopedBaseDAO: ...\n",
        "class QueryDAO: ...\n",
        "def decrypt_data(value, key): ...\n",
        "def create_access_token(): ...\n",
        "async def get_current_user(): ...\n",
        "def tenant_context(value): ...\n",
        "query_dao = object()\n",
    ],
)
def test_restored_security_dao_definition_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/infrastructure/restored_legacy.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="Security/DAO definitions"):
        _assert_application_does_not_restore_security_dao_definitions(tmp_path)


def test_explicit_sandbox_secret_decoder_passes_security_dao_guard(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "app/services/sandbox/config.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "def decode_sandbox_secret(value): return value\n",
        encoding="utf-8",
    )
    _assert_deleted_legacy_security_dao_authorities(tmp_path)
    _assert_application_does_not_restore_security_dao_definitions(tmp_path)


def test_legacy_core_events_authority_is_absent() -> None:
    _assert_deleted_legacy_core_events_authority(BACKEND_ROOT)
    _assert_tests_do_not_reference_deleted_core_events(BACKEND_ROOT)
    _assert_application_does_not_restore_core_events_definitions(BACKEND_ROOT)


@pytest.mark.parametrize("representation", ["module", "package"])
def test_reintroduced_legacy_core_events_identity_fails_guard(
    tmp_path: Path,
    representation: str,
) -> None:
    authority = tmp_path / LEGACY_CORE_EVENTS_IDENTITY
    if representation == "module":
        authority.parent.mkdir(parents=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="was reintroduced"):
        _assert_deleted_legacy_core_events_authority(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_core_events_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    source = (
        f"import {LEGACY_CORE_EVENTS_DOTTED_IDENTITY}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{LEGACY_CORE_EVENTS_DOTTED_IDENTITY}")\n'
    )
    test_path = tmp_path / "tests/test_restored_core_events.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="core events authority"):
        _assert_tests_do_not_reference_deleted_core_events(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "_redis_client = None\n",
        "async def get_redis(): ...\n",
        "async def publish_event(channel, data): ...\n",
        "async def close_redis(): ...\n",
    ],
)
def test_restored_core_events_definition_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/infrastructure/restored_events.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    with pytest.raises(DeletedAuthorityViolation, match="core events definitions"):
        _assert_application_does_not_restore_core_events_definitions(tmp_path)


def test_injected_sandbox_lease_redis_passes_core_events_guard(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "app/services/sandbox/execution_lease.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "class SandboxLeaseRedis: ...\n",
        encoding="utf-8",
    )
    _assert_deleted_legacy_core_events_authority(tmp_path)
    _assert_application_does_not_restore_core_events_definitions(tmp_path)


def test_target_a2a_package_remains_empty() -> None:
    package_init = BACKEND_ROOT / "app/modules/a2a/__init__.py"
    assert package_init.is_file()
    assert package_init.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_A2A_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_A2A_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_a2a_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy A2A authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_a2a_authorities(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_a2a_authority_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    identity = LEGACY_A2A_DOTTED_IMPORT_IDENTITIES[0]
    source = (
        f"from {identity} import CollaborationService\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}")\n'
    )
    test_path = tmp_path / "tests/test_restored_a2a.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy A2A authority",
    ):
        _assert_tests_do_not_reference_deleted_a2a_authorities(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "class TemplateCreate: ...\n",
        "class TemplateOut: ...\n",
        "class HandoverRequest: ...\n",
        "async def list_templates(): ...\n",
        "async def get_template(): ...\n",
        "async def create_template(): ...\n",
        "async def delete_template(): ...\n",
        "async def handover_agent(): ...\n",
        "async def get_agent_metrics(): ...\n",
        '@router.get("/templates")\nasync def restored(): ...\n',
        '@router.get("/templates/{template_id}")\nasync def restored(): ...\n',
        '@router.post("/templates")\nasync def restored(): ...\n',
        '@router.delete("/templates/{template_id}")\nasync def restored(): ...\n',
        '@router.post("/agents/{agent_id}/handover")\nasync def restored(): ...\n',
        '@router.get("/agents/{agent_id}/metrics")\nasync def restored(): ...\n',
    ],
)
def test_restored_residual_advanced_api_fact_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/api/restored_residual.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="application API restores legacy advanced facts",
    ):
        _assert_application_apis_do_not_restore_legacy_advanced_facts(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "class DelegateRequest: ...\n",
        "class InterAgentMessage: ...\n",
        "from app.services.collaboration import collaboration_service\n",
        "async def list_collaborators(): ...\n",
        "async def delegate_task(): ...\n",
        "async def send_inter_agent_message(): ...\n",
        '@router.get("/agents/{agent_id}/collaborators")\nasync def restored(): ...\n',
        '@router.post("/agents/{agent_id}/collaborate/delegate")\nasync def restored(): ...\n',
        '@router.post("/agents/{agent_id}/collaborate/message")\nasync def restored(): ...\n',
        "result = service.send_message_between_agents()\n",
    ],
    ids=[
        "delegate-request",
        "inter-agent-message",
        "collaboration-service-import",
        "list-collaborators-handler",
        "delegate-task-handler",
        "send-message-handler",
        "collaborators-route",
        "delegate-route",
        "message-route",
        "send-message-service-call",
    ],
)
def test_restored_advanced_api_a2a_fact_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/api/restored_advanced.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="application API restores legacy advanced facts",
    ):
        _assert_application_apis_do_not_restore_legacy_advanced_facts(tmp_path)


def test_target_a2a_and_unrelated_collaboration_terms_pass_legacy_guard(
    tmp_path: Path,
) -> None:
    test_path = tmp_path / "tests/test_target_a2a.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("from app.modules.a2a import __name__\n", encoding="utf-8")
    advanced_source = tmp_path / "app/api/collaboration_reporting.py"
    advanced_source.parent.mkdir(parents=True)
    advanced_source.write_text(
        '"""Collaboration-facing reporting APIs."""\n'
        "class CollaborationSummary: ...\n"
        "async def collaboration_summary(): ...\n",
        encoding="utf-8",
    )

    _assert_tests_do_not_reference_deleted_a2a_authorities(tmp_path)
    _assert_application_apis_do_not_restore_legacy_advanced_facts(tmp_path)


def test_email_provider_is_decoupled_from_legacy_storage() -> None:
    _assert_email_provider_is_decoupled_from_legacy_storage(BACKEND_ROOT)


@pytest.mark.parametrize(
    "source",
    [
        "import app.services.storage\nasync def send_email(config, to, subject, body, cc=None): ...\n",
        (
            "from app.services.storage_runtime import get_storage_backend\n"
            "async def send_email(config, to, subject, body, cc=None): ...\n"
        ),
        "async def send_email(config, to, subject, body, cc=None, attachments=None): ...\n",
        "async def send_email(config, to, subject, body, cc=None, workspace_path=None): ...\n",
        "async def send_email(config, to, subject, body, cc=None, agent_id=None): ...\n",
    ],
    ids=[
        "storage-facade-import",
        "storage-runtime-import",
        "attachments-field",
        "workspace-path-field",
        "agent-id-field",
    ],
)
def test_restored_email_storage_coupling_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / EMAIL_PROVIDER_SERVICE_SOURCE
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="email provider service restores legacy storage coupling",
    ):
        _assert_email_provider_is_decoupled_from_legacy_storage(tmp_path)


def test_core_email_service_passes_email_storage_decoupling_guard(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / EMAIL_PROVIDER_SERVICE_SOURCE
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "from app.core.email import send_smtp_email\n"
        "async def send_email(config, to, subject, body, cc=None): ...\n",
        encoding="utf-8",
    )

    _assert_email_provider_is_decoupled_from_legacy_storage(tmp_path)


def test_legacy_seed_bootstrap_authorities_are_absent() -> None:
    _assert_deleted_legacy_seed_bootstrap_authorities(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_bootstrap_authority() -> None:
    _assert_tests_do_not_reference_deleted_bootstrap_authority(BACKEND_ROOT)


def test_setup_and_startup_scripts_do_not_restore_legacy_bootstrap() -> None:
    _assert_setup_and_startup_scripts_do_not_restore_legacy_bootstrap(
        BACKEND_ROOT.parent
    )


@pytest.mark.parametrize(
    "representation",
    ["seed-script", "bootstrap-module", "bootstrap-package"],
)
def test_reintroduced_legacy_seed_bootstrap_authority_fails_guard(
    tmp_path: Path,
    representation: str,
) -> None:
    if representation == "seed-script":
        (tmp_path / LEGACY_SEED_SCRIPT).write_text("", encoding="utf-8")
    elif representation == "bootstrap-module":
        module = (tmp_path / LEGACY_BOOTSTRAP_IMPORT_IDENTITY).with_suffix(".py")
        module.parent.mkdir(parents=True)
        module.write_text("", encoding="utf-8")
    else:
        package = tmp_path / LEGACY_BOOTSTRAP_IMPORT_IDENTITY
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(DeletedAuthorityViolation, match="was reintroduced"):
        _assert_deleted_legacy_seed_bootstrap_authorities(tmp_path)


@pytest.mark.parametrize("reference_kind", ["static", "dotted"])
def test_backend_test_reference_of_deleted_bootstrap_authority_fails_guard(
    tmp_path: Path,
    reference_kind: str,
) -> None:
    source = (
        f"import {LEGACY_BOOTSTRAP_DOTTED_IMPORT_IDENTITY}\n"
        if reference_kind == "static"
        else (
            "module = importlib.import_module("
            f'"{LEGACY_BOOTSTRAP_DOTTED_IMPORT_IDENTITY}")\n'
        )
    )
    test_path = tmp_path / "tests/test_restored_bootstrap.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy seed/bootstrap authority",
    ):
        _assert_tests_do_not_reference_deleted_bootstrap_authority(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "python backend/seed.py\n",
        'SEED_COMMAND="python backend/seed.py"\nexec $SEED_COMMAND\n',
        "python -m app.scripts.bootstrap_db\n",
        "uv run alembic upgrade head\n",
        "echo safe; uv run alembic upgrade head\n",
        "python -m app.scripts.setup_langgraph_checkpoints\n",
        'python -c "Base.metadata.create_all()"\n',
        'psql "$DATABASE_URL" -c "ALTER TABLE users ADD COLUMN legacy INTEGER"\n',
        'mkdir -p "$AGENT_DATA_DIR/$agent_id/workspace"\n',
        'touch "$workspace/soul.md"\n',
        'printf "# Memory" > "$workspace/memory/memory.md"\n',
    ],
    ids=[
        "seed-script",
        "assigned-seed-command",
        "bootstrap-module",
        "alembic",
        "echo-then-alembic",
        "checkpoint-installer",
        "create-all",
        "inline-schema-patch",
        "agent-workspace",
        "soul-file",
        "memory-file",
    ],
)
def test_restored_setup_or_startup_bootstrap_behavior_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    setup_script = tmp_path / "setup.sh"
    setup_script.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="setup or startup script restores legacy seed/bootstrap behavior",
    ):
        _assert_setup_and_startup_scripts_do_not_restore_legacy_bootstrap(tmp_path)


def test_target_health_startup_and_operator_alembic_pass_bootstrap_guard(
    tmp_path: Path,
) -> None:
    setup_script = tmp_path / "setup.sh"
    setup_script.write_text(
        "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1\n",
        encoding="utf-8",
    )
    operator_script = tmp_path / "scripts/operator_migration.sh"
    operator_script.parent.mkdir(parents=True)
    operator_script.write_text("uv run alembic current\n", encoding="utf-8")

    _assert_setup_and_startup_scripts_do_not_restore_legacy_bootstrap(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "# python backend/seed.py\n# ALTER TABLE users ADD COLUMN legacy INTEGER\n",
        'echo "Run python -m app.scripts.bootstrap_db only in the legacy checkout"\n',
        'echo "Schema repair no longer calls create_all or writes soul.md"\n',
        'BOOTSTRAP_DOCUMENTATION="python backend/seed.py"\n',
        (
            "export AGENT_DATA_DIR=/data/agents\n"
            "env AGENT_DATA_DIR=/data/agents uvicorn app.main:app\n"
        ),
    ],
    ids=[
        "comments",
        "log-documentation",
        "schema-log",
        "documentation-assignment",
        "environment-pass-through",
    ],
)
def test_nonexecuting_bootstrap_text_passes_script_guard(
    tmp_path: Path,
    source: str,
) -> None:
    setup_script = tmp_path / "setup.sh"
    setup_script.write_text(source, encoding="utf-8")

    _assert_setup_and_startup_scripts_do_not_restore_legacy_bootstrap(tmp_path)


def test_legacy_storage_authorities_are_absent() -> None:
    _assert_deleted_legacy_storage_authorities(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_storage_authorities() -> None:
    _assert_tests_do_not_reference_deleted_storage_authorities(BACKEND_ROOT)


def test_target_object_storage_package_initializer_is_empty() -> None:
    _assert_target_object_storage_package_is_empty(BACKEND_ROOT)


def test_target_object_storage_package_reexport_fails_guard(tmp_path: Path) -> None:
    package_init = tmp_path / TARGET_OBJECT_STORAGE_PACKAGE_INIT
    package_init.parent.mkdir(parents=True)
    package_init.write_text(
        "from app.infrastructure.object_storage.local import LocalStorageBackend\n",
        encoding="utf-8",
    )

    with pytest.raises(
        DeletedAuthorityViolation,
        match="package initializer must remain empty",
    ):
        _assert_target_object_storage_package_is_empty(tmp_path)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_STORAGE_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_STORAGE_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_storage_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy storage authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_storage_authorities(tmp_path)


@pytest.mark.parametrize("test_path", LEGACY_STORAGE_TEST_PATHS, ids=str)
def test_reintroduced_legacy_storage_test_path_fails_guard(
    tmp_path: Path,
    test_path: Path,
) -> None:
    restored_test = tmp_path / test_path
    restored_test.parent.mkdir(parents=True, exist_ok=True)
    restored_test.write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy storage test path was reintroduced",
    ):
        _assert_deleted_legacy_storage_authorities(tmp_path)


@pytest.mark.parametrize(
    ("identity", "reference_kind"),
    [
        (identity, reference_kind)
        for identity in LEGACY_STORAGE_DOTTED_IMPORT_IDENTITIES
        for reference_kind in ("static", "dotted")
    ],
)
def test_backend_test_reference_of_deleted_storage_authority_fails_guard(
    tmp_path: Path,
    identity: str,
    reference_kind: str,
) -> None:
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'module = importlib.import_module("{identity}.local")\n'
    )
    test_path = tmp_path / "tests/test_restored_storage.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy storage authority",
    ):
        _assert_tests_do_not_reference_deleted_storage_authorities(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "from app.infrastructure.object_storage.base import StorageBackend\n",
        "from app.infrastructure.object_storage.local import LocalStorageBackend\n",
        "from app.infrastructure.object_storage.s3 import S3StorageBackend\n",
        "from app.modules.workspace import __name__\n",
    ],
)
def test_target_object_storage_references_pass_legacy_storage_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_target_object_storage.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    _assert_tests_do_not_reference_deleted_storage_authorities(tmp_path)


def test_channel_provider_transports_are_isolated_from_legacy_authorities() -> None:
    _assert_channel_provider_transports_are_isolated(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("feishu_source", "expected_detail"),
    [
        ("from app.config import get_settings\n", "imports=app.config"),
        ("from app.core.security import create_access_token\n", "imports=app.core.security"),
        ("from app.dao import query_dao\n", "imports=app.dao"),
        ("from app.models.identity import IdentityProvider\n", "imports=app.models.identity"),
        ("from app.models.user import User\n", "imports=app.models.user"),
        ("from app.models.org import OrgMember\n", "imports=app.models.org"),
        (
            "from app.services.registration_service import registration_service\n",
            "imports=app.services.registration_service",
        ),
        ("from app import config\n", "imports=app.config"),
        ("from . import channel_session\n", "imports=.channel_session"),
        ("from ..models import user\n", "imports=..models"),
        (
            (
                "class FeishuService:\n"
                "    async def get_app_access_token(self): ...\n"
                "    async def get_tenant_access_token(self, app_id, app_secret): ...\n"
            ),
            "methods=get_app_access_token",
        ),
        (
            (
                "class FeishuService:\n"
                "    def __init__(self): self.app_secret = 'secret'\n"
                "    async def get_tenant_access_token(self, app_id, app_secret): ...\n"
            ),
            "state=app_secret",
        ),
        (
            (
                "class FeishuService:\n"
                "    async def get_tenant_access_token(self, app_id=None, app_secret=None): ...\n"
            ),
            "must require app_id and app_secret",
        ),
    ],
    ids=[
        "config-import",
        "security-import",
        "dao-import",
        "identity-provider-import",
        "user-import",
        "organization-import",
        "registration-service-import",
        "package-config-import",
        "relative-sibling-import",
        "relative-parent-import",
        "legacy-app-token-method",
        "default-credential-state",
        "optional-tenant-token-credentials",
    ],
)
def test_restored_feishu_auth_or_credential_authority_fails_guard(
    tmp_path: Path,
    feishu_source: str,
    expected_detail: str,
) -> None:
    source_path = tmp_path / FEISHU_PROVIDER_TRANSPORT_SOURCE
    source_path.parent.mkdir(parents=True)
    source_path.write_text(feishu_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="Feishu provider transport restores legacy auth or credential authority",
    ) as raised:
        _assert_channel_provider_transports_are_isolated(tmp_path)

    assert expected_detail in str(raised.value)


@pytest.mark.parametrize(
    "method_name",
    sorted(LEGACY_FEISHU_AUTHORITY_METHODS - {"get_app_access_token"}),
)
def test_restored_feishu_identity_method_fails_provider_transport_guard(
    tmp_path: Path,
    method_name: str,
) -> None:
    source_path = tmp_path / FEISHU_PROVIDER_TRANSPORT_SOURCE
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        (
            "class FeishuService:\n"
            "    async def get_tenant_access_token(self, app_id, app_secret): ...\n"
            f"    async def {method_name}(self): ...\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(DeletedAuthorityViolation, match=f"methods={method_name}"):
        _assert_channel_provider_transports_are_isolated(tmp_path)


def test_restored_dingtalk_stream_wrapper_fails_provider_transport_guard(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / DINGTALK_PROVIDER_TRANSPORT_SOURCE
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "async def download_dingtalk_media(app_id, app_secret, download_code): ...\n",
        encoding="utf-8",
    )

    with pytest.raises(
        DeletedAuthorityViolation,
        match="DingTalk provider transport restores application authority or stream wrapper",
    ):
        _assert_channel_provider_transports_are_isolated(tmp_path)


@pytest.mark.parametrize(
    ("dingtalk_source", "expected_detail"),
    [
        (
            "from app.services import dingtalk_stream\n",
            "imports=app.services",
        ),
        ("from . import channel_session\n", "imports=.channel_session"),
        ("from ..models import user\n", "imports=..models"),
    ],
    ids=["application-import", "relative-sibling-import", "relative-parent-import"],
)
def test_restored_dingtalk_application_import_fails_provider_transport_guard(
    tmp_path: Path,
    dingtalk_source: str,
    expected_detail: str,
) -> None:
    feishu_source = tmp_path / FEISHU_PROVIDER_TRANSPORT_SOURCE
    feishu_source.parent.mkdir(parents=True)
    feishu_source.write_text(
        "class FeishuService:\n"
        "    async def get_tenant_access_token(self, app_id, app_secret): ...\n",
        encoding="utf-8",
    )
    dingtalk_path = tmp_path / DINGTALK_PROVIDER_TRANSPORT_SOURCE
    dingtalk_path.write_text(dingtalk_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="DingTalk provider transport restores application authority or stream wrapper",
    ) as raised:
        _assert_channel_provider_transports_are_isolated(tmp_path)

    assert expected_detail in str(raised.value)


def test_explicit_provider_operations_pass_channel_transport_guard(tmp_path: Path) -> None:
    feishu_source = tmp_path / FEISHU_PROVIDER_TRANSPORT_SOURCE
    feishu_source.parent.mkdir(parents=True)
    feishu_source.write_text(
        (
            "import httpx\n"
            "from loguru import logger\n"
            "import lark_oapi\n"
            "class FeishuService:\n"
            "    async def get_tenant_access_token(self, app_id, app_secret): ...\n"
            "    async def send_message(self, app_id, app_secret): ...\n"
            "    async def create_approval_instance(self, app_id, app_secret): ...\n"
        ),
        encoding="utf-8",
    )
    dingtalk_source = tmp_path / DINGTALK_PROVIDER_TRANSPORT_SOURCE
    dingtalk_source.parent.mkdir(parents=True, exist_ok=True)
    dingtalk_source.write_text(
        (
            "import json\n"
            "import httpx\n"
            "from loguru import logger\n"
            "async def send_dingtalk_message(app_id, app_secret, user_id, message): ...\n"
        ),
        encoding="utf-8",
    )

    _assert_channel_provider_transports_are_isolated(tmp_path)


def test_legacy_channel_authorities_are_absent_from_target_tree() -> None:
    _assert_deleted_legacy_channel_authorities(BACKEND_ROOT)


def test_legacy_channel_package_exports_are_absent() -> None:
    _assert_deleted_legacy_channel_package_exports(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_channel_authorities() -> None:
    _assert_tests_do_not_reference_deleted_channel_authorities(BACKEND_ROOT)


def test_application_does_not_restore_legacy_channel_definitions() -> None:
    _assert_application_does_not_restore_channel_definitions(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_CHANNEL_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_CHANNEL_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_channel_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=f"deleted legacy Channel authority {representation} was reintroduced",
    ):
        _assert_deleted_legacy_channel_authorities(tmp_path)


@pytest.mark.parametrize("representation", ["file", "directory"])
def test_reintroduced_legacy_channel_cleanup_script_fails_guard(
    tmp_path: Path,
    representation: str,
) -> None:
    script_path = tmp_path / LEGACY_CHANNEL_CLEANUP_SCRIPT
    script_path.parent.mkdir(parents=True)
    if representation == "file":
        script_path.write_text("", encoding="utf-8")
    else:
        script_path.mkdir()

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Channel cleanup script was reintroduced",
    ):
        _assert_deleted_legacy_channel_authorities(tmp_path)


@pytest.mark.parametrize(
    ("package_path", "source"),
    [
        (Path("app/api/__init__.py"), "from .feishu import router\n"),
        (
            Path("app/models/__init__.py"),
            "from app.models.channel_config import ChannelConfig\n",
        ),
        (
            Path("app/services/__init__.py"),
            '__all__ = ["dingtalk_stream"]\n',
        ),
        (Path("app/api/__init__.py"), "whatsapp = object()\n"),
    ],
    ids=["relative-import", "absolute-import", "all-export", "assignment-export"],
)
def test_restored_static_channel_package_export_fails_guard(
    tmp_path: Path,
    package_path: Path,
    source: str,
) -> None:
    package_init = tmp_path / package_path
    package_init.parent.mkdir(parents=True)
    package_init.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Channel package export was reintroduced",
    ):
        _assert_deleted_legacy_channel_package_exports(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "def __getattr__(name): return None\n",
        "globals()['__getattr__'] = lambda name: None\n",
    ],
    ids=["function-hook", "globals-hook"],
)
def test_restored_dynamic_channel_package_export_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    package_init = tmp_path / "app/services/__init__.py"
    package_init.parent.mkdir(parents=True)
    package_init.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted legacy Channel package exports can be restored by a dynamic hook",
    ):
        _assert_deleted_legacy_channel_package_exports(tmp_path)


@pytest.mark.parametrize(
    ("identity", "reference_kind"),
    [
        (identity, reference_kind)
        for identity in LEGACY_CHANNEL_DOTTED_IMPORT_IDENTITIES
        for reference_kind in ("static", "dotted")
    ],
)
def test_backend_test_reference_of_deleted_channel_authority_fails_guard(
    tmp_path: Path,
    identity: str,
    reference_kind: str,
) -> None:
    source = (
        f"import {identity}\n"
        if reference_kind == "static"
        else f'target = "{identity}.restored"\n'
    )
    test_path = tmp_path / "tests/test_restored_channel.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Channel authority",
    ):
        _assert_tests_do_not_reference_deleted_channel_authorities(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "class ChannelConfig: ...\n",
        "class ChannelDelivery: ...\n",
        "class ChannelConfigCreate: ...\n",
        "class ChannelConfigOut: ...\n",
        'class Restored:\n    __tablename__ = "channel_configs"\n',
        'class Restored:\n    __tablename__ = "channel_deliveries"\n',
        'channel_type = Enum("feishu", name="channel_type_enum")\n',
        '_CHANNEL_SECRET_KEY_PARTS = ("secret",)\n',
        "def _redact_channel_secrets(value): return value\n",
    ],
    ids=[
        "config-class",
        "delivery-class",
        "create-schema",
        "out-schema",
        "config-table",
        "delivery-table",
        "channel-enum",
        "schema-secret-parts",
        "schema-redaction-helper",
    ],
)
def test_restored_channel_definition_fails_guard(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "app/modules/channel/restored.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="application source restores legacy Channel definitions",
    ):
        _assert_application_does_not_restore_channel_definitions(tmp_path)


def test_retained_channel_provider_and_target_references_pass_guard(
    tmp_path: Path,
) -> None:
    test_path = tmp_path / "tests/test_retained_channel_provider.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        (
            "from app.services.feishu_service import FeishuAPIError, feishu_service\n"
            "from app.services.feishu_contact_search import search_feishu_contacts\n"
            "from app.services.dingtalk_service import send_dingtalk_message\n"
            "from app.services.dingtalk_token import dingtalk_token_manager\n"
            "from app.services.dingtalk_reaction import add_thinking_reaction\n"
            "from app.services.mcp_client import MCPClient\n"
            "from app.modules.channel import __name__\n"
        ),
        encoding="utf-8",
    )

    _assert_tests_do_not_reference_deleted_channel_authorities(tmp_path)


def test_legacy_autonomy_approval_authority_is_absent() -> None:
    _assert_deleted_legacy_autonomy_approval_authority(BACKEND_ROOT)


def test_backend_tests_do_not_reference_deleted_autonomy_approval_authority() -> None:
    _assert_tests_do_not_reference_deleted_autonomy_approval_authority(BACKEND_ROOT)


def test_mixed_owners_do_not_restore_autonomy_approval_symbols() -> None:
    _assert_mixed_owners_do_not_restore_autonomy_approval_facts(BACKEND_ROOT)


def test_agent_templates_do_not_restore_autonomy_policy() -> None:
    _assert_agent_templates_do_not_restore_autonomy_policy(BACKEND_ROOT)


@pytest.mark.parametrize(
    ("identity", "representation"),
    LEGACY_AUTONOMY_APPROVAL_REINTRODUCTIONS,
    ids=[
        f"{identity.as_posix()}-{representation}"
        for identity, representation in LEGACY_AUTONOMY_APPROVAL_REINTRODUCTIONS
    ],
)
def test_reintroduced_legacy_autonomy_approval_identity_fails_guard(
    tmp_path: Path,
    identity: Path,
    representation: str,
) -> None:
    authority = tmp_path / identity
    if representation == "module":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.with_suffix(".py").write_text("", encoding="utf-8")
    else:
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match=(
            "deleted legacy Autonomy/Approval authority "
            f"{representation} was reintroduced"
        ),
    ):
        _assert_deleted_legacy_autonomy_approval_authority(tmp_path)


@pytest.mark.parametrize(
    "test_source",
    [
        "import app.services.autonomy_service\n",
        "from app.services import autonomy_service\n",
        "from app.services.autonomy_service import AutonomyService\n",
        'module = importlib.import_module("app.services.autonomy_service")\n',
        (
            'monkeypatch.setattr("app.services.autonomy_service.autonomy_service", '
            "object())\n"
        ),
    ],
    ids=[
        "service-import",
        "service-package-import",
        "service-symbol-import",
        "dynamic-service-import",
        "monkeypatch-dotted-reference",
    ],
)
def test_backend_test_reference_of_deleted_autonomy_approval_fails_guard(
    tmp_path: Path,
    test_source: str,
) -> None:
    test_path = tmp_path / "tests/test_restored_autonomy_approval.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="test references deleted legacy Autonomy/Approval authority",
    ):
        _assert_tests_do_not_reference_deleted_autonomy_approval_authority(tmp_path)


def test_unrelated_feishu_approval_reference_passes_autonomy_guard(
    tmp_path: Path,
) -> None:
    test_path = tmp_path / "tests/test_feishu_approval_transport.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "from app.services.feishu_service import feishu_service\n",
        encoding="utf-8",
    )

    _assert_tests_do_not_reference_deleted_autonomy_approval_authority(tmp_path)


@pytest.mark.parametrize(
    ("relative_path", "test_source"),
    [
        (Path("app/models/audit.py"), "class ApprovalRequest: ...\n"),
        (
            Path("app/models/audit.py"),
            'class Legacy:\n    __tablename__ = "approval_requests"\n',
        ),
        (
            Path("app/models/audit.py"),
            'status = Enum("pending", name="approval_status_enum")\n',
        ),
        (
            Path("app/api/enterprise.py"),
            (
                '@router.get("/approvals", response_model=ApprovalRequestOut)\n'
                "async def list_approvals(): ...\n"
            ),
        ),
        (
            Path("app/api/enterprise.py"),
            (
                '@router.post("/approvals/{approval_id}/resolve")\n'
                "async def resolve_approval(): ...\n"
            ),
        ),
        (
            Path("app/api/advanced.py"),
            "class TemplateCreate:\n    default_autonomy_policy: dict = {}\n",
        ),
        (
            Path("app/api/advanced.py"),
            'payload = {"total_approvals": 1, "pending_approvals": 1}\n',
        ),
        (
            Path("app/dao/agent_metrics_dao.py"),
            "from app.models.audit import ApprovalRequest\n",
        ),
        (
            Path("app/dao/agent_metrics_dao.py"),
            "total_approvals, pending_approvals = (1, 1)\n",
        ),
        (Path("app/schemas/schemas.py"), "class ApprovalRequestOut: ...\n"),
        (Path("app/schemas/schemas.py"), "class ApprovalAction: ...\n"),
        (
            Path("app/schemas/schemas.py"),
            "class AgentCreate:\n    autonomy_policy: dict | None = None\n",
        ),
        (
            Path("app/services/feishu_service.py"),
            "class FeishuService:\n    async def send_approval_card(self): ...\n",
        ),
    ],
    ids=[
        "model-class",
        "model-table",
        "model-enum",
        "enterprise-list-route-response",
        "enterprise-resolve-route",
        "advanced-template-field",
        "advanced-metric-keys",
        "metrics-model-import",
        "metrics-assignments",
        "approval-response-schema",
        "approval-action-schema",
        "agent-autonomy-field",
        "feishu-runtime-card-method",
    ],
)
def test_restored_mixed_owner_autonomy_approval_fact_fails_guard(
    tmp_path: Path,
    relative_path: Path,
    test_source: str,
) -> None:
    source_path = tmp_path / relative_path
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(test_source, encoding="utf-8")

    with pytest.raises(
        DeletedAuthorityViolation,
        match="mixed retained owner restores legacy Autonomy/Approval facts",
    ):
        _assert_mixed_owners_do_not_restore_autonomy_approval_facts(tmp_path)


def test_unrelated_mixed_owner_symbols_pass_autonomy_approval_guard(
    tmp_path: Path,
) -> None:
    safe_sources = {
        Path("app/models/audit.py"): (
            'class AuditLog: ...\nnote = "ApprovalRequest is retired"\n'
        ),
        Path("app/api/enterprise.py"): (
            'approvals = []\ntext = "/approvals is unavailable"\n'
        ),
        Path("app/api/advanced.py"): (
            'approvals = []\npayload = {"approvals": "unavailable"}\n'
        ),
        Path("app/dao/agent_metrics_dao.py"): 'result = {"recent_actions": 0}\n',
        Path("app/schemas/schemas.py"): "class AuditLogOut: ...\n",
        Path("app/services/feishu_service.py"): (
            "class FeishuService:\n"
            "    async def create_approval_instance(self): ...\n"
            "    async def query_approval_instances(self): ...\n"
            "    async def get_approval_instance(self): ...\n"
        ),
    }
    for relative_path, source in safe_sources.items():
        source_path = tmp_path / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(source, encoding="utf-8")

    _assert_mixed_owners_do_not_restore_autonomy_approval_facts(tmp_path)


def test_native_feishu_approval_instance_methods_pass_autonomy_guard(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "app/services/feishu_service.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        (
            "class FeishuService:\n"
            "    async def create_approval_instance(self): ...\n"
            "    async def query_approval_instances(self): ...\n"
            "    async def get_approval_instance(self): ...\n"
        ),
        encoding="utf-8",
    )

    _assert_mixed_owners_do_not_restore_autonomy_approval_facts(tmp_path)


@pytest.mark.parametrize(
    "policy_key",
    [
        "default_autonomy_policy",
        "'default_autonomy_policy'",
        '"default_autonomy_policy"',
    ],
    ids=["plain-key", "single-quoted-key", "double-quoted-key"],
)
def test_restored_agent_template_autonomy_policy_fails_guard(
    tmp_path: Path,
    policy_key: str,
) -> None:
    metadata_path = tmp_path / "agent_templates/restored/meta.yaml"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        f'name: restored\n{policy_key}:\n  read_files: "L1"\n',
        encoding="utf-8",
    )

    with pytest.raises(
        DeletedAuthorityViolation,
        match="Agent Template restores legacy Autonomy policy field",
    ):
        _assert_agent_templates_do_not_restore_autonomy_policy(tmp_path)


def test_agent_template_without_autonomy_policy_passes_guard(
    tmp_path: Path,
) -> None:
    metadata_path = tmp_path / "agent_templates/safe/meta.yaml"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        (
            "name: safe\n"
            "description: default_autonomy_policy is retired\n"
            "default_skills: []\n"
        ),
        encoding="utf-8",
    )

    _assert_agent_templates_do_not_restore_autonomy_policy(tmp_path)


@pytest.mark.parametrize(
    ("metadata_source", "expected_error"),
    [
        ("name: [unterminated\n", "Agent Template metadata is invalid YAML"),
        ("- name: list-entry\n", "Agent Template metadata must be a top-level mapping"),
    ],
    ids=["invalid-yaml", "non-mapping-yaml"],
)
def test_invalid_agent_template_metadata_fails_closed(
    tmp_path: Path,
    metadata_source: str,
    expected_error: str,
) -> None:
    metadata_path = tmp_path / "agent_templates/invalid/meta.yaml"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(metadata_source, encoding="utf-8")

    with pytest.raises(DeletedAuthorityViolation, match=expected_error):
        _assert_agent_templates_do_not_restore_autonomy_policy(tmp_path)


def test_deleted_owner_direct_dependencies_are_absent() -> None:
    _assert_removed_orphan_dependencies_absent(BACKEND_ROOT)


def test_backend_dependency_lock_is_tracked_and_current() -> None:
    repository_root = BACKEND_ROOT.parent
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "backend/uv.lock"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0, "backend/uv.lock must be tracked"

    current = subprocess.run(
        ["uv", "lock", "--check"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert current.returncode == 0, current.stderr


@pytest.mark.parametrize("dependency", sorted(REMOVED_ORPHAN_DIRECT_DEPENDENCIES))
def test_restored_deleted_owner_direct_dependency_fails_guard(
    tmp_path: Path,
    dependency: str,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "fixture"\nversion = "0"\ndependencies = ["{dependency}>=1"]\n',
        encoding="utf-8",
    )

    with pytest.raises(
        DeletedAuthorityViolation,
        match="deleted-owner direct dependencies restored",
    ):
        _assert_removed_orphan_dependencies_absent(tmp_path)


def test_retained_dependency_fixture_passes_deleted_owner_guard(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """[project]
name = "fixture"
version = "0"
dependencies = ["lxml-html-clean>=0.4", "aioboto3>=13", "azure-identity>=1", "croniter>=6", "pillow>=11"]

[project.optional-dependencies]
dev = ["pytest>=8"]
""",
        encoding="utf-8",
    )

    _assert_removed_orphan_dependencies_absent(tmp_path)
