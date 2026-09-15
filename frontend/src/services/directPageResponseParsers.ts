import type { User } from "../types";
import type { JsonValue } from "./apiContracts";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringField(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  if (typeof value !== "string") throw new Error(`Invalid ${key}`);
  return value;
}

function numberField(record: Record<string, unknown>, key: string): number {
  const value = record[key];
  if (typeof value !== "number" || !Number.isFinite(value))
    throw new Error(`Invalid ${key}`);
  return value;
}

function booleanField(record: Record<string, unknown>, key: string): boolean {
  const value = record[key];
  if (typeof value !== "boolean") throw new Error(`Invalid ${key}`);
  return value;
}

function optionalString(
  record: Record<string, unknown>,
  key: string,
): string | undefined {
  const value = record[key];
  if (value === undefined || value === null) return undefined;
  return stringField(record, key);
}

function optionalBoolean(
  record: Record<string, unknown>,
  key: string,
): boolean | undefined {
  const value = record[key];
  if (value === undefined || value === null) return undefined;
  return booleanField(record, key);
}

function optionalNumber(
  record: Record<string, unknown>,
  key: string,
): number | undefined {
  const value = record[key];
  if (value === undefined || value === null) return undefined;
  return numberField(record, key);
}

function isJsonValue(value: unknown): value is JsonValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  )
    return true;
  if (Array.isArray(value)) return value.every(isJsonValue);
  return isRecord(value) && Object.values(value).every(isJsonValue);
}

export function parseDirectUser(value: unknown): User {
  if (!isRecord(value)) throw new Error("Invalid SSO session status user");
  const role = value.role;
  if (
    role !== "platform_admin" &&
    role !== "org_admin" &&
    role !== "agent_admin" &&
    role !== "member"
  )
    throw new Error("Invalid SSO session status user role");
  const nullableString = (key: string): string | undefined => {
    const item = value[key];
    if (item === undefined || item === null) return undefined;
    return stringField(value, key);
  };
  const avatarUrl = nullableString("avatar_url");
  const tenantId = nullableString("tenant_id");
  const title = nullableString("title");
  const feishuOpenId = nullableString("feishu_open_id");
  const isPlatformAdmin = optionalBoolean(value, "is_platform_admin");
  const emailVerified = optionalBoolean(value, "email_verified");
  return {
    id: stringField(value, "id"),
    username: nullableString("username") ?? "",
    email: nullableString("email") ?? "",
    display_name: stringField(value, "display_name"),
    role,
    is_active: booleanField(value, "is_active"),
    created_at: stringField(value, "created_at"),
    ...(avatarUrl !== undefined ? { avatar_url: avatarUrl } : {}),
    ...(tenantId !== undefined ? { tenant_id: tenantId } : {}),
    ...(title !== undefined ? { title } : {}),
    ...(feishuOpenId !== undefined ? { feishu_open_id: feishuOpenId } : {}),
    ...(isPlatformAdmin !== undefined
      ? { is_platform_admin: isPlatformAdmin }
      : {}),
    ...(emailVerified !== undefined ? { email_verified: emailVerified } : {}),
  };
}

export interface SsoProviderResponse {
  provider_type: string;
  url?: string;
  name?: string;
}

export function parseSsoProviders(value: unknown): SsoProviderResponse[] {
  if (!Array.isArray(value)) throw new Error("Invalid SSO providers response");
  return value.map((provider) => {
    if (!isRecord(provider)) throw new Error("Invalid SSO provider response");
    const url = optionalString(provider, "url");
    const name = optionalString(provider, "name");
    return {
      provider_type: stringField(provider, "provider_type"),
      ...(url !== undefined ? { url } : {}),
      ...(name !== undefined ? { name } : {}),
    };
  });
}

export interface SsoSessionStatusResponse {
  access_token?: string;
  user?: User;
  status?: string;
  error_msg?: string;
}

export function parseSsoSessionStatus(
  value: unknown,
): SsoSessionStatusResponse {
  if (!isRecord(value)) throw new Error("Invalid SSO session status response");
  const accessToken = optionalString(value, "access_token");
  const status = optionalString(value, "status");
  const errorMsg = optionalString(value, "error_msg");
  return {
    ...(accessToken !== undefined ? { access_token: accessToken } : {}),
    ...(value.user !== undefined ? { user: parseDirectUser(value.user) } : {}),
    ...(status !== undefined ? { status } : {}),
    ...(errorMsg !== undefined ? { error_msg: errorMsg } : {}),
  };
}

export function parseSessionId(value: unknown): { session_id: string } {
  if (!isRecord(value)) throw new Error("Invalid SSO session response");
  return { session_id: stringField(value, "session_id") };
}

export function parseAuthorizationUrl(value: unknown): {
  authorization_url: string;
} {
  if (!isRecord(value)) throw new Error("Invalid authorization response");
  return { authorization_url: stringField(value, "authorization_url") };
}

export interface UserManagementUser {
  id: string;
  username: string;
  email: string;
  display_name: string;
  role: User["role"];
  is_active: boolean;
  quota_message_limit: number;
  quota_message_period: string;
  quota_messages_used: number;
  quota_max_agents: number;
  quota_agent_ttl_hours: number;
  agents_count: number;
  feishu_open_id?: string;
  created_at?: string;
  source?: string;
}

export function parseUserManagementUsers(value: unknown): UserManagementUser[] {
  if (!Array.isArray(value)) throw new Error("Invalid user list response");
  return value.map((user) => {
    if (!isRecord(user)) throw new Error("Invalid user response");
    const role = user.role;
    if (
      role !== "platform_admin" &&
      role !== "org_admin" &&
      role !== "agent_admin" &&
      role !== "member"
    )
      throw new Error("Invalid user role");
    return {
      id: stringField(user, "id"),
      username: stringField(user, "username"),
      email: stringField(user, "email"),
      display_name: stringField(user, "display_name"),
      role,
      is_active: booleanField(user, "is_active"),
      quota_message_limit: numberField(user, "quota_message_limit"),
      quota_message_period: stringField(user, "quota_message_period"),
      quota_messages_used: numberField(user, "quota_messages_used"),
      quota_max_agents: numberField(user, "quota_max_agents"),
      quota_agent_ttl_hours: numberField(user, "quota_agent_ttl_hours"),
      agents_count: numberField(user, "agents_count"),
      ...(optionalString(user, "feishu_open_id") !== undefined
        ? { feishu_open_id: optionalString(user, "feishu_open_id") }
        : {}),
      ...(optionalString(user, "created_at") !== undefined
        ? { created_at: optionalString(user, "created_at") }
        : {}),
      ...(optionalString(user, "source") !== undefined
        ? { source: optionalString(user, "source") }
        : {}),
    };
  });
}

export function parseInviteUsersResult(value: unknown): {
  invited: number;
  message: string;
} {
  if (!isRecord(value)) throw new Error("Invalid invite users response");
  return {
    invited: numberField(value, "invited"),
    message: stringField(value, "message"),
  };
}

export interface DashboardOkrPeriod {
  start: string;
  end: string;
  is_current: boolean;
}

export interface DashboardOkrObjective {
  key_results?: Array<{ status: string }>;
}

export function parseDashboardOkrPeriods(value: unknown): DashboardOkrPeriod[] {
  if (!Array.isArray(value)) throw new Error("Invalid OKR periods response");
  return value.map((period) => {
    if (!isRecord(period)) throw new Error("Invalid OKR period response");
    return {
      start: stringField(period, "start"),
      end: stringField(period, "end"),
      is_current: booleanField(period, "is_current"),
    };
  });
}

export function parseDashboardOkrObjectives(
  value: unknown,
): DashboardOkrObjective[] {
  if (!Array.isArray(value)) throw new Error("Invalid OKR objectives response");
  return value.map((objective) => {
    if (!isRecord(objective)) throw new Error("Invalid OKR objective response");
    if (objective.key_results === undefined) return {};
    if (!Array.isArray(objective.key_results))
      throw new Error("Invalid OKR objective key results");
    return {
      key_results: objective.key_results.map((result) => {
        if (!isRecord(result)) throw new Error("Invalid OKR objective result");
        return { status: stringField(result, "status") };
      }),
    };
  });
}

export function parseOkrSettings(value: unknown): { enabled: boolean } {
  if (!isRecord(value)) throw new Error("Invalid OKR settings response");
  return { enabled: booleanField(value, "enabled") };
}

export interface NotificationItemResponse {
  id: string;
  type: string;
  title: string;
  body?: string;
  sender_name?: string;
  created_at?: string;
  is_read: boolean;
  link?: string;
}

export function parseUnreadCount(value: unknown): { unread_count: number } {
  if (!isRecord(value)) throw new Error("Invalid unread count response");
  return { unread_count: numberField(value, "unread_count") };
}

export function parseNotificationItems(
  value: unknown,
): NotificationItemResponse[] {
  if (!Array.isArray(value)) throw new Error("Invalid notifications response");
  return value.map((item) => {
    if (!isRecord(item)) throw new Error("Invalid notification response");
    const body = optionalString(item, "body");
    const senderName = optionalString(item, "sender_name");
    const createdAt = optionalString(item, "created_at");
    const link = optionalString(item, "link");
    return {
      id: stringField(item, "id"),
      type: stringField(item, "type"),
      title: stringField(item, "title"),
      is_read: booleanField(item, "is_read"),
      ...(body !== undefined ? { body } : {}),
      ...(senderName !== undefined ? { sender_name: senderName } : {}),
      ...(createdAt !== undefined ? { created_at: createdAt } : {}),
      ...(link !== undefined ? { link } : {}),
    };
  });
}

export interface TenantQuotasResponse {
  default_message_limit?: number;
  default_message_period?: string;
  default_max_agents?: number;
  default_agent_ttl_hours?: number;
  default_max_llm_calls_per_day?: number;
  min_heartbeat_interval_minutes?: number;
  default_max_triggers?: number;
  min_poll_interval_floor?: number;
  max_webhook_rate_ceiling?: number;
}

export function parseTenantQuotas(value: unknown): TenantQuotasResponse {
  if (!isRecord(value)) throw new Error("Invalid tenant quota response");
  const result: TenantQuotasResponse = {};
  for (const key of [
    "default_message_limit",
    "default_max_agents",
    "default_agent_ttl_hours",
    "default_max_llm_calls_per_day",
    "min_heartbeat_interval_minutes",
    "default_max_triggers",
    "min_poll_interval_floor",
    "max_webhook_rate_ceiling",
  ] as const) {
    const item = optionalNumber(value, key);
    if (item !== undefined) result[key] = item;
  }
  const period = optionalString(value, "default_message_period");
  if (period !== undefined) result.default_message_period = period;
  return result;
}

export function parseCompanyIntroSetting(value: unknown): {
  value: { content?: string };
} {
  if (!isRecord(value) || !isRecord(value.value))
    throw new Error("Invalid company intro response");
  const content = optionalString(value.value, "content");
  return { value: content !== undefined ? { content } : {} };
}

export function parseEnterpriseStats(value: unknown): {
  total_users: number;
  running_agents: number;
  total_agents: number;
  pending_approvals: number;
} {
  if (!isRecord(value)) throw new Error("Invalid enterprise stats response");
  return {
    total_users: numberField(value, "total_users"),
    running_agents: numberField(value, "running_agents"),
    total_agents: numberField(value, "total_agents"),
    pending_approvals: numberField(value, "pending_approvals"),
  };
}

export interface ApprovalResponse {
  id: string;
  action_type: string;
  agent_name?: string;
  agent_id: string;
  created_at: string;
  status: "pending" | "approved" | "rejected";
}

export function parseApprovalList(value: unknown): ApprovalResponse[] {
  if (!Array.isArray(value)) throw new Error("Invalid approvals response");
  return value.map((approval) => {
    if (!isRecord(approval)) throw new Error("Invalid approval response");
    const status = approval.status;
    if (status !== "pending" && status !== "approved" && status !== "rejected")
      throw new Error("Invalid approval status");
    const agentName = optionalString(approval, "agent_name");
    return {
      id: stringField(approval, "id"),
      action_type: stringField(approval, "action_type"),
      agent_id: stringField(approval, "agent_id"),
      created_at: stringField(approval, "created_at"),
      status,
      ...(agentName !== undefined ? { agent_name: agentName } : {}),
    };
  });
}

export interface AuditLogResponse {
  id: string;
  action: string;
  created_at: string;
  agent_id?: string;
  details?: Record<string, JsonValue> | null;
}

export function parseAuditLogList(value: unknown): AuditLogResponse[] {
  if (!Array.isArray(value)) throw new Error("Invalid audit logs response");
  return value.map((log) => {
    if (!isRecord(log)) throw new Error("Invalid audit log response");
    const agentId = optionalString(log, "agent_id");
    let details: Record<string, JsonValue> | null | undefined;
    if (log.details === null) details = null;
    else if (log.details !== undefined) {
      if (!isRecord(log.details) || !isJsonValue(log.details))
        throw new Error("Invalid audit log details");
      details = log.details;
    }
    return {
      id: stringField(log, "id"),
      action: stringField(log, "action"),
      created_at: stringField(log, "created_at"),
      ...(agentId !== undefined ? { agent_id: agentId } : {}),
      ...(details !== undefined ? { details } : {}),
    };
  });
}

export interface EnterpriseToolResponse {
  id: string;
  name: string;
  display_name: string;
  description?: string;
  category?: string;
  type?: string;
  enabled: boolean;
  is_default: boolean;
  config?: Record<string, JsonValue> | null;
  config_schema?: { fields?: EnterpriseToolConfigField[] };
  inputSchema?: JsonValue;
  mcp_server_name?: string;
  mcp_server_url?: string;
  agent_tool_id?: string;
  configured?: boolean;
  installed_at?: string;
  installed_by_agent_name?: string;
  tool_display_name?: string;
}

export interface EnterpriseToolConfigField {
  key: string;
  label: string;
  type: "checkbox" | "select" | "number" | "textarea" | "password" | "text";
  default?: JsonValue;
  placeholder?: string;
  options?: Array<{ value: string; label: string }>;
  min?: number;
  max?: number;
  advanced?: boolean;
  depends_on?: Record<string, JsonValue[]>;
}

function parseToolConfigField(value: unknown): EnterpriseToolConfigField {
  if (!isRecord(value)) throw new Error("Invalid enterprise tool config field");
  const type = value.type;
  if (
    type !== "checkbox" &&
    type !== "select" &&
    type !== "number" &&
    type !== "textarea" &&
    type !== "password" &&
    type !== "text"
  )
    throw new Error("Invalid enterprise tool config field type");
  let options: EnterpriseToolConfigField["options"];
  if (value.options !== undefined) {
    if (!Array.isArray(value.options))
      throw new Error("Invalid enterprise tool config options");
    options = value.options.map((option) => {
      if (!isRecord(option)) throw new Error("Invalid enterprise tool option");
      return {
        value: stringField(option, "value"),
        label: stringField(option, "label"),
      };
    });
  }
  let dependsOn: Record<string, JsonValue[]> | undefined;
  if (value.depends_on !== undefined) {
    if (!isRecord(value.depends_on))
      throw new Error("Invalid enterprise tool dependencies");
    dependsOn = Object.fromEntries(
      Object.entries(value.depends_on).map(([key, items]) => {
        if (!Array.isArray(items) || !items.every(isJsonValue))
          throw new Error("Invalid enterprise tool dependencies");
        return [key, items];
      }),
    );
  }
  if (value.default !== undefined && !isJsonValue(value.default))
    throw new Error("Invalid enterprise tool default");
  const placeholder = optionalString(value, "placeholder");
  const min = optionalNumber(value, "min");
  const max = optionalNumber(value, "max");
  const advanced = optionalBoolean(value, "advanced");
  return {
    key: stringField(value, "key"),
    label: stringField(value, "label"),
    type,
    ...(value.default !== undefined ? { default: value.default } : {}),
    ...(placeholder !== undefined ? { placeholder } : {}),
    ...(options !== undefined ? { options } : {}),
    ...(min !== undefined ? { min } : {}),
    ...(max !== undefined ? { max } : {}),
    ...(advanced !== undefined ? { advanced } : {}),
    ...(dependsOn !== undefined ? { depends_on: dependsOn } : {}),
  };
}

export function parseEnterpriseToolList(
  value: unknown,
): EnterpriseToolResponse[] {
  if (!Array.isArray(value))
    throw new Error("Invalid enterprise tools response");
  return value.map((tool) => {
    if (!isRecord(tool)) throw new Error("Invalid enterprise tool response");
    let config: Record<string, JsonValue> | null | undefined;
    if (tool.config === null) config = null;
    else if (tool.config !== undefined) {
      if (!isRecord(tool.config) || !isJsonValue(tool.config))
        throw new Error("Invalid enterprise tool config");
      config = tool.config;
    }
    const optionalKeys = [
      "description",
      "category",
      "type",
      "mcp_server_name",
      "mcp_server_url",
      "agent_tool_id",
      "installed_at",
      "installed_by_agent_name",
      "tool_display_name",
    ] as const;
    const agentToolId = optionalString(tool, "agent_tool_id");
    const id = optionalString(tool, "id") ?? optionalString(tool, "tool_id");
    const name =
      optionalString(tool, "name") ?? optionalString(tool, "tool_name");
    const displayName =
      optionalString(tool, "display_name") ??
      optionalString(tool, "tool_display_name");
    if (!id || !name || !displayName)
      throw new Error("Invalid enterprise tool identity");
    const isDefault =
      tool.is_default === undefined && agentToolId
        ? false
        : booleanField(tool, "is_default");
    const result: EnterpriseToolResponse = {
      id,
      name,
      display_name: displayName,
      enabled: booleanField(tool, "enabled"),
      is_default: isDefault,
    };
    for (const key of optionalKeys) {
      const item = optionalString(tool, key);
      if (item !== undefined) result[key] = item;
    }
    const configured = optionalBoolean(tool, "configured");
    if (configured !== undefined) result.configured = configured;
    if (agentToolId !== undefined) result.agent_tool_id = agentToolId;
    if (config !== undefined) result.config = config;
    if (tool.inputSchema !== undefined) {
      if (!isJsonValue(tool.inputSchema))
        throw new Error("Invalid enterprise tool input schema");
      result.inputSchema = tool.inputSchema;
    }
    if (tool.config_schema !== undefined) {
      if (!isRecord(tool.config_schema))
        throw new Error("Invalid enterprise tool config schema");
      const fields = tool.config_schema.fields;
      if (fields !== undefined && !Array.isArray(fields))
        throw new Error("Invalid enterprise tool config fields");
      result.config_schema =
        fields === undefined
          ? {}
          : { fields: fields.map(parseToolConfigField) };
    }
    return result;
  });
}

export function parseCreatedMcpTool(value: unknown): {
  id: string;
  name: string;
} {
  if (!isRecord(value)) throw new Error("Invalid created MCP tool response");
  return { id: stringField(value, "id"), name: stringField(value, "name") };
}

export interface McpTestResultResponse {
  ok: boolean;
  error?: string;
  tools?: Array<{
    name: string;
    description?: string;
    inputSchema?: JsonValue;
  }>;
}

export function parseMcpTestResult(value: unknown): McpTestResultResponse {
  if (!isRecord(value)) throw new Error("Invalid MCP test response");
  const error = optionalString(value, "error");
  let tools: McpTestResultResponse["tools"];
  if (value.tools !== undefined) {
    if (!Array.isArray(value.tools))
      throw new Error("Invalid MCP tools response");
    tools = value.tools.map((tool) => {
      if (!isRecord(tool)) throw new Error("Invalid MCP tool response");
      const description = optionalString(tool, "description");
      if (tool.inputSchema !== undefined && !isJsonValue(tool.inputSchema))
        throw new Error("Invalid MCP input schema");
      return {
        name: stringField(tool, "name"),
        ...(description !== undefined ? { description } : {}),
        ...(tool.inputSchema !== undefined
          ? { inputSchema: tool.inputSchema }
          : {}),
      };
    });
  }
  return {
    ok: booleanField(value, "ok"),
    ...(error !== undefined ? { error } : {}),
    ...(tools !== undefined ? { tools } : {}),
  };
}

export function parseTenantDeleteResult(value: unknown): {
  fallback_tenant_id: string;
} {
  if (!isRecord(value)) throw new Error("Invalid tenant delete response");
  return { fallback_tenant_id: stringField(value, "fallback_tenant_id") };
}

export function parseUpdatedAtSetting(value: unknown): {
  updated_at?: string | null;
} {
  if (!isRecord(value)) throw new Error("Invalid setting update response");
  const updatedAt = value.updated_at;
  if (
    updatedAt !== undefined &&
    updatedAt !== null &&
    typeof updatedAt !== "string"
  )
    throw new Error("Invalid setting updated_at");
  return updatedAt === undefined ? {} : { updated_at: updatedAt };
}

export function parsePublicNotificationBar(value: unknown): {
  enabled: boolean;
  text: string;
  updated_at: string | null;
} {
  if (!isRecord(value)) throw new Error("Invalid notification bar response");
  const updatedAt = value.updated_at;
  if (updatedAt !== null && typeof updatedAt !== "string")
    throw new Error("Invalid notification bar updated_at");
  return {
    enabled: booleanField(value, "enabled"),
    text: stringField(value, "text"),
    updated_at: updatedAt,
  };
}

export function parseEmailExists(value: unknown): { exists: boolean } {
  if (!isRecord(value)) throw new Error("Invalid email existence response");
  return { exists: booleanField(value, "exists") };
}

export function parseVersion(value: unknown): {
  version: string;
  commit?: string;
} {
  if (!isRecord(value)) throw new Error("Invalid version response");
  const commit = optionalString(value, "commit");
  return {
    version: stringField(value, "version"),
    ...(commit !== undefined ? { commit } : {}),
  };
}

export interface InvitationCodeResponse {
  id: string;
  code: string;
  used_count: number;
  max_uses: number;
  is_active: boolean;
  created_at: string | null;
}

export function parseInvitationCodePage(value: unknown): {
  items: InvitationCodeResponse[];
  total: number;
} {
  if (!isRecord(value) || !Array.isArray(value.items))
    throw new Error("Invalid invitation code page response");
  return {
    items: value.items.map((item) => {
      if (!isRecord(item)) throw new Error("Invalid invitation code response");
      const createdAt = item.created_at;
      if (createdAt !== null && typeof createdAt !== "string")
        throw new Error("Invalid invitation code created_at");
      return {
        id: stringField(item, "id"),
        code: stringField(item, "code"),
        used_count: numberField(item, "used_count"),
        max_uses: numberField(item, "max_uses"),
        is_active: booleanField(item, "is_active"),
        created_at: createdAt,
      };
    }),
    total: numberField(value, "total"),
  };
}

export function parseInvitationCodeCreate(value: unknown): {
  created: number;
  codes: string[];
} {
  if (!isRecord(value) || !Array.isArray(value.codes))
    throw new Error("Invalid invitation code create response");
  if (!value.codes.every((code) => typeof code === "string"))
    throw new Error("Invalid invitation codes");
  return { created: numberField(value, "created"), codes: value.codes };
}

export function parseInvitationCodeDeactivate(value: unknown): {
  status: "deactivated";
} {
  if (!isRecord(value) || value.status !== "deactivated")
    throw new Error("Invalid invitation code deactivate response");
  return { status: "deactivated" };
}

export function parseApiKey(value: unknown): { api_key: string } {
  if (!isRecord(value)) throw new Error("Invalid API key response");
  return { api_key: stringField(value, "api_key") };
}

export function parseOptionalSystemApiKey(value: unknown): string | null {
  if (!isRecord(value)) throw new Error("Invalid system API key response");
  const setting = value.value;
  if (!isRecord(setting)) throw new Error("Invalid system API key value");
  if (setting.api_key === undefined || setting.api_key === null) return null;
  if (typeof setting.api_key !== "string")
    throw new Error("Invalid system API key");
  return setting.api_key;
}

export interface AgentPermissionsResponse {
  is_owner: boolean;
  scope_type: "company" | "user" | "custom";
  scope_ids: string[];
  scope_names?: Array<{ name: string }>;
  access_level: "use" | "manage";
}

export function parseAgentPermissions(
  value: unknown,
): AgentPermissionsResponse {
  if (!isRecord(value) || !Array.isArray(value.scope_ids))
    throw new Error("Invalid agent permissions response");
  if (!value.scope_ids.every((id) => typeof id === "string"))
    throw new Error("Invalid agent permission scope ids");
  const scopeType = value.scope_type;
  if (scopeType !== "company" && scopeType !== "user" && scopeType !== "custom")
    throw new Error("Invalid agent permission scope type");
  const accessLevel = value.access_level;
  if (accessLevel !== "use" && accessLevel !== "manage")
    throw new Error("Invalid agent permission access level");
  let scopeNames: Array<{ name: string }> | undefined;
  if (value.scope_names !== undefined) {
    if (!Array.isArray(value.scope_names))
      throw new Error("Invalid agent permission scope names");
    scopeNames = value.scope_names.map((item) => {
      if (!isRecord(item))
        throw new Error("Invalid agent permission scope name");
      return { name: stringField(item, "name") };
    });
  }
  return {
    is_owner: booleanField(value, "is_owner"),
    scope_type: scopeType,
    scope_ids: value.scope_ids,
    access_level: accessLevel,
    ...(scopeNames !== undefined ? { scope_names: scopeNames } : {}),
  };
}
