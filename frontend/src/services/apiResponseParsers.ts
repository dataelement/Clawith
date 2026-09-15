import type { Agent, Task, TokenResponse, User } from "../types";
import { AppError } from "./apiError.ts";
import type {
  AgentCollaborator,
  AgentMetrics,
  AgentTemplate,
  CompanyCreateResponse,
  CompanyStats,
  ControlScreenshotResponse,
  ControlStatusResponse,
  ControlUnlockResponse,
  CreatedAgent,
  FileItem,
  FileLockResponse,
  FileMutationResponse,
  FilePreview,
  FileRevision,
  FocusApiItem,
  GatewayMessage,
  GroupWorkspaceUploadResponse,
  InboxMessage,
  JsonValue,
  LlmModel,
  OnboardingStatus,
  OrgDepartmentItem,
  PersonalAssistantResponse,
  PlatformSettings,
  ResolvedTenant,
  Schedule,
  ScheduleHistoryItem,
  ScheduleRunResponse,
  Skill,
  SkillDetail,
  SkillImportResult,
  SkillMutationResult,
  SkillUrlPreview,
  Tenant,
  TenantChoice,
  TenantSetupResponse,
  TenantTokenUsage,
  Trigger,
  UploadResponse,
  WorkspaceUploadResponse,
  Credential,
  ExperienceEntry,
  ActivityItem,
  ChannelConfig,
  TaskTriggerResponse,
} from "./apiContracts";
import type { OAuthTenantChoice } from "./oauthCallbackResponse";

export type ResponseParser<T> = (value: unknown) => T;

function invalid(path: string, expected: string): never {
  throw new AppError({
    message: `Invalid API response at ${path}: expected ${expected}`,
    code: "invalid_api_response",
    source: "http",
    retryable: false,
    details: { path, expected },
  });
}

function assertRecord(
  value: unknown,
  path: string,
): asserts value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    invalid(path, "object");
  }
}

function assertString(value: unknown, path: string): asserts value is string {
  if (typeof value !== "string") invalid(path, "string");
}

function assertNumber(value: unknown, path: string): asserts value is number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    invalid(path, "finite number");
  }
}

function assertBoolean(value: unknown, path: string): asserts value is boolean {
  if (typeof value !== "boolean") invalid(path, "boolean");
}

function assertNullableString(
  value: unknown,
  path: string,
): asserts value is string | null {
  if (value !== null) assertString(value, path);
}

function assertOptionalString(
  value: unknown,
  path: string,
): asserts value is string | undefined {
  if (value !== undefined) assertString(value, path);
}

function assertOptionalNumber(
  value: unknown,
  path: string,
): asserts value is number | undefined {
  if (value !== undefined) assertNumber(value, path);
}

function assertOptionalBoolean(
  value: unknown,
  path: string,
): asserts value is boolean | undefined {
  if (value !== undefined) assertBoolean(value, path);
}

function assertOptionalNullableString(
  value: unknown,
  path: string,
): asserts value is string | null | undefined {
  if (value !== undefined) assertNullableString(value, path);
}

function readString(
  record: Record<string, unknown>,
  key: string,
  path: string,
) {
  const value = record[key];
  assertString(value, `${path}.${key}`);
  return value;
}

function readNumber(
  record: Record<string, unknown>,
  key: string,
  path: string,
) {
  const value = record[key];
  assertNumber(value, `${path}.${key}`);
  return value;
}

function readBoolean(
  record: Record<string, unknown>,
  key: string,
  path: string,
) {
  const value = record[key];
  assertBoolean(value, `${path}.${key}`);
  return value;
}

function readNullableString(
  record: Record<string, unknown>,
  key: string,
  path: string,
) {
  const value = record[key];
  assertNullableString(value, `${path}.${key}`);
  return value;
}

function readOptionalString(
  record: Record<string, unknown>,
  key: string,
  path: string,
) {
  const value = record[key];
  assertOptionalString(value, `${path}.${key}`);
  return value;
}

function readOptionalNumber(
  record: Record<string, unknown>,
  key: string,
  path: string,
) {
  const value = record[key];
  assertOptionalNumber(value, `${path}.${key}`);
  return value;
}

function readOptionalBoolean(
  record: Record<string, unknown>,
  key: string,
  path: string,
) {
  const value = record[key];
  assertOptionalBoolean(value, `${path}.${key}`);
  return value;
}

function readOptionalNullableString(
  record: Record<string, unknown>,
  key: string,
  path: string,
) {
  const value = record[key];
  assertOptionalNullableString(value, `${path}.${key}`);
  return value;
}

function parseArray<T>(
  value: unknown,
  path: string,
  parser: (item: unknown, itemPath: string) => T,
): T[] {
  if (!Array.isArray(value)) invalid(path, "array");
  return value.map((item, index) => parser(item, `${path}[${index}]`));
}

function parseJsonRecord(
  value: unknown,
  path: string,
): Record<string, JsonValue> {
  assertRecord(value, path);
  const result: Record<string, JsonValue> = {};
  Object.entries(value).forEach(([key, item]) => {
    assertJsonValue(item, `${path}.${key}`);
    result[key] = item;
  });
  return result;
}

function assertStringArray(
  value: unknown,
  path: string,
): asserts value is string[] {
  if (!Array.isArray(value)) invalid(path, "string array");
  value.forEach((item, index) => assertString(item, `${path}[${index}]`));
}

function assertJsonValue(
  value: unknown,
  path: string,
): asserts value is JsonValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  ) {
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertJsonValue(item, `${path}[${index}]`));
    return;
  }
  assertRecord(value, path);
  Object.entries(value).forEach(([key, item]) =>
    assertJsonValue(item, `${path}.${key}`),
  );
}

function assertRole(
  value: unknown,
  path: string,
): asserts value is User["role"] {
  if (
    value !== "platform_admin" &&
    value !== "org_admin" &&
    value !== "agent_admin" &&
    value !== "member"
  ) {
    invalid(path, "user role");
  }
}

function assertUser(value: unknown, path: string): asserts value is User {
  assertRecord(value, path);
  assertString(value.id, `${path}.id`);
  assertString(value.username, `${path}.username`);
  assertString(value.email, `${path}.email`);
  assertString(value.display_name, `${path}.display_name`);
  assertRole(value.role, `${path}.role`);
  assertBoolean(value.is_active, `${path}.is_active`);
  assertString(value.created_at, `${path}.created_at`);
  assertOptionalString(value.avatar_url, `${path}.avatar_url`);
  assertOptionalBoolean(value.is_platform_admin, `${path}.is_platform_admin`);
  assertOptionalString(value.tenant_id, `${path}.tenant_id`);
  assertOptionalString(value.title, `${path}.title`);
  assertOptionalString(value.feishu_open_id, `${path}.feishu_open_id`);
  assertOptionalBoolean(value.email_verified, `${path}.email_verified`);
}

function parseUserAt(value: unknown, path: string): User {
  assertRecord(value, path);
  const normalized: Record<string, unknown> = { ...value };
  if (normalized.username === null) normalized.username = "";
  if (normalized.email === null) normalized.email = "";
  ["avatar_url", "tenant_id", "title", "feishu_open_id"].forEach((key) => {
    if (normalized[key] === null) delete normalized[key];
  });
  assertUser(normalized, path);
  return normalized;
}

function assertAgentStatus(
  value: unknown,
  path: string,
): asserts value is Agent["status"] {
  if (
    value !== "creating" &&
    value !== "running" &&
    value !== "idle" &&
    value !== "stopped" &&
    value !== "paused" &&
    value !== "error"
  ) {
    invalid(path, "agent status");
  }
}

function assertStringRecord(
  value: unknown,
  path: string,
): asserts value is Record<string, string> {
  assertRecord(value, path);
  Object.entries(value).forEach(([key, item]) =>
    assertString(item, `${path}.${key}`),
  );
}

function assertAgent(value: unknown, path: string): asserts value is Agent {
  assertRecord(value, path);
  assertString(value.id, `${path}.id`);
  assertString(value.name, `${path}.name`);
  assertString(value.role_description, `${path}.role_description`);
  assertAgentStatus(value.status, `${path}.status`);
  assertString(value.creator_id, `${path}.creator_id`);
  assertStringRecord(value.autonomy_policy, `${path}.autonomy_policy`);
  assertNumber(value.tokens_used_today, `${path}.tokens_used_today`);
  assertNumber(value.tokens_used_month, `${path}.tokens_used_month`);
  assertBoolean(value.heartbeat_enabled, `${path}.heartbeat_enabled`);
  assertNumber(
    value.heartbeat_interval_minutes,
    `${path}.heartbeat_interval_minutes`,
  );
  assertString(value.heartbeat_active_hours, `${path}.heartbeat_active_hours`);
  assertString(value.created_at, `${path}.created_at`);
  [
    "avatar_url",
    "bio",
    "primary_model_id",
    "fallback_model_id",
    "last_heartbeat_at",
    "timezone",
    "openclaw_last_seen",
    "last_active_at",
  ].forEach((key) => assertOptionalString(value[key], `${path}.${key}`));
  [
    "tokens_used_total",
    "cache_read_tokens_today",
    "cache_read_tokens_month",
    "cache_read_tokens_total",
    "cache_creation_tokens_today",
    "cache_creation_tokens_month",
    "cache_creation_tokens_total",
    "max_tokens_per_day",
    "max_tokens_per_month",
    "context_window_size",
    "unread_count",
  ].forEach((key) => assertOptionalNumber(value[key], `${path}.${key}`));
  assertOptionalBoolean(value.onboarded_for_me, `${path}.onboarded_for_me`);
  if (
    value.agent_type !== undefined &&
    value.agent_type !== "native" &&
    value.agent_type !== "openclaw"
  ) {
    invalid(`${path}.agent_type`, "native or openclaw");
  }
  if (
    value.access_mode !== undefined &&
    value.access_mode !== "company" &&
    value.access_mode !== "private" &&
    value.access_mode !== "custom"
  ) {
    invalid(`${path}.access_mode`, "company, private, or custom");
  }
  if (
    value.company_access_level !== undefined &&
    value.company_access_level !== "use" &&
    value.company_access_level !== "manage"
  ) {
    invalid(`${path}.company_access_level`, "use or manage");
  }
}

function parseAgentAt(value: unknown, path: string): Agent {
  assertRecord(value, path);
  const normalized: Record<string, unknown> = { ...value };
  [
    "avatar_url",
    "bio",
    "primary_model_id",
    "fallback_model_id",
    "last_heartbeat_at",
    "timezone",
    "openclaw_last_seen",
    "last_active_at",
  ].forEach((key) => {
    if (normalized[key] === null) delete normalized[key];
  });
  ["max_tokens_per_day", "max_tokens_per_month"].forEach((key) => {
    if (normalized[key] === null) delete normalized[key];
  });
  assertAgent(normalized, path);
  return normalized;
}

function assertTenant(value: unknown, path: string): asserts value is Tenant {
  assertRecord(value, path);
  assertString(value.id, `${path}.id`);
  assertString(value.name, `${path}.name`);
  assertString(value.slug, `${path}.slug`);
  assertString(value.im_provider, `${path}.im_provider`);
  assertString(value.timezone, `${path}.timezone`);
  assertString(value.country_region, `${path}.country_region`);
  assertBoolean(value.is_active, `${path}.is_active`);
  assertBoolean(value.sso_enabled, `${path}.sso_enabled`);
  assertNullableString(value.sso_domain, `${path}.sso_domain`);
  assertBoolean(value.a2a_async_enabled, `${path}.a2a_async_enabled`);
  assertNullableString(value.default_model_id, `${path}.default_model_id`);
  assertNullableString(value.logo_url, `${path}.logo_url`);
  assertNullableString(value.created_at, `${path}.created_at`);
}

function parseWithAssertion<T>(
  value: unknown,
  assertion: (input: unknown, path: string) => asserts input is T,
): T {
  assertion(value, "response");
  return value;
}

export const parseUserResponse: ResponseParser<User> = (value) =>
  parseUserAt(value, "response");

export const parseAgentResponse: ResponseParser<Agent> = (value) =>
  parseAgentAt(value, "response");

export const parseCreatedAgentResponse: ResponseParser<CreatedAgent> = (
  value,
) => {
  assertRecord(value, "response");
  assertOptionalString(value.api_key, "response.api_key");
  const agent = parseAgentAt(value, "response");
  return value.api_key === undefined
    ? agent
    : { ...agent, api_key: value.api_key };
};

export const parseAgentListResponse: ResponseParser<Agent[]> = (value) => {
  if (!Array.isArray(value)) invalid("response", "agent array");
  return value.map((item, index) => parseAgentAt(item, `response[${index}]`));
};

export const parseTenantResponse: ResponseParser<Tenant> = (value) =>
  parseWithAssertion(value, assertTenant);

export const parseTenantChoicesResponse: ResponseParser<TenantChoice[]> = (
  value,
) => {
  if (!Array.isArray(value)) invalid("response", "tenant choice array");
  return value.flatMap((item, index) => {
    const path = `response[${index}]`;
    assertRecord(item, path);
    if (item.tenant_id === null) return [];
    assertString(item.tenant_id, `${path}.tenant_id`);
    assertString(item.tenant_name, `${path}.tenant_name`);
    assertString(item.tenant_slug, `${path}.tenant_slug`);
    assertNullableString(item.logo_url, `${path}.logo_url`);
    return [
      {
        tenant_id: item.tenant_id,
        tenant_name: item.tenant_name,
        tenant_slug: item.tenant_slug,
        logo_url: item.logo_url,
      },
    ];
  });
};

export const parseTenantSetupResponse: ResponseParser<TenantSetupResponse> = (
  value,
) => {
  assertRecord(value, "response");
  assertTenant(value.tenant, "response.tenant");
  assertNullableString(value.access_token, "response.access_token");
  if (value.role !== undefined) assertRole(value.role, "response.role");
  return {
    tenant: value.tenant,
    access_token: value.access_token,
    ...(value.role === undefined ? {} : { role: value.role }),
  };
};

export const parseResolvedTenantResponse: ResponseParser<ResolvedTenant> = (
  value,
) => {
  assertRecord(value, "response");
  assertString(value.id, "response.id");
  assertString(value.name, "response.name");
  assertString(value.slug, "response.slug");
  assertBoolean(value.sso_enabled, "response.sso_enabled");
  assertNullableString(value.sso_domain, "response.sso_domain");
  assertBoolean(value.is_active, "response.is_active");
  return {
    id: value.id,
    name: value.name,
    slug: value.slug,
    sso_enabled: value.sso_enabled,
    sso_domain: value.sso_domain,
    is_active: value.is_active,
  };
};

function assertTokenUsageBucket(
  value: unknown,
  path: string,
): asserts value is TenantTokenUsage["today"] {
  assertRecord(value, path);
  assertNumber(value.total_tokens, `${path}.total_tokens`);
  assertNumber(value.cache_read_tokens, `${path}.cache_read_tokens`);
  assertNumber(value.cache_creation_tokens, `${path}.cache_creation_tokens`);
  assertNumber(value.cache_hit_rate, `${path}.cache_hit_rate`);
}

export const parseTenantTokenUsageResponse: ResponseParser<TenantTokenUsage> = (
  value,
) => {
  assertRecord(value, "response");
  assertTokenUsageBucket(value.today, "response.today");
  assertTokenUsageBucket(value.month, "response.month");
  assertTokenUsageBucket(value.total, "response.total");
  return { today: value.today, month: value.month, total: value.total };
};

function assertCompanyStats(
  value: unknown,
  path: string,
): asserts value is CompanyStats {
  assertRecord(value, path);
  ["id", "name", "slug"].forEach((key) =>
    assertString(value[key], `${path}.${key}`),
  );
  assertBoolean(value.is_active, `${path}.is_active`);
  assertBoolean(value.sso_enabled, `${path}.sso_enabled`);
  assertNullableString(value.sso_domain, `${path}.sso_domain`);
  assertNullableString(value.created_at, `${path}.created_at`);
  [
    "user_count",
    "agent_count",
    "agent_running_count",
    "total_tokens",
    "cache_read_tokens_total",
  ].forEach((key) => assertNumber(value[key], `${path}.${key}`));
  assertNullableString(value.org_admin_email, `${path}.org_admin_email`);
}

export const parseCompanyStatsListResponse: ResponseParser<CompanyStats[]> = (
  value,
) => {
  if (!Array.isArray(value)) invalid("response", "company array");
  value.forEach((item, index) =>
    assertCompanyStats(item, `response[${index}]`),
  );
  return value;
};

export const parseCompanyStatsResponse: ResponseParser<CompanyStats> = (
  value,
) => parseWithAssertion(value, assertCompanyStats);

export const parseCompanyCreateResponse: ResponseParser<
  CompanyCreateResponse
> = (value) => {
  assertRecord(value, "response");
  assertCompanyStats(value.company, "response.company");
  assertString(value.admin_invitation_code, "response.admin_invitation_code");
  return {
    company: value.company,
    admin_invitation_code: value.admin_invitation_code,
  };
};

export const parsePlatformSettingsResponse: ResponseParser<PlatformSettings> = (
  value,
) => {
  assertRecord(value, "response");
  assertBoolean(
    value.allow_self_create_company,
    "response.allow_self_create_company",
  );
  assertBoolean(
    value.invitation_code_enabled,
    "response.invitation_code_enabled",
  );
  assertBoolean(
    value.sso_custom_domain_redirect_enabled,
    "response.sso_custom_domain_redirect_enabled",
  );
  return {
    allow_self_create_company: value.allow_self_create_company,
    invitation_code_enabled: value.invitation_code_enabled,
    sso_custom_domain_redirect_enabled:
      value.sso_custom_domain_redirect_enabled,
  };
};

export const parseAgentTemplatesResponse: ResponseParser<AgentTemplate[]> = (
  value,
) => {
  if (!Array.isArray(value)) invalid("response", "agent template array");
  value.forEach((item, index) => {
    const path = `response[${index}]`;
    assertRecord(item, path);
    ["id", "name", "description", "icon", "category"].forEach((key) =>
      assertString(item[key], `${path}.${key}`),
    );
    assertBoolean(item.is_builtin, `${path}.is_builtin`);
    if (item.capability_bullets !== undefined) {
      assertStringArray(item.capability_bullets, `${path}.capability_bullets`);
    }
  });
  return value;
};

export const parseAgentCollaboratorsResponse: ResponseParser<
  AgentCollaborator[]
> = (value) => {
  if (!Array.isArray(value)) invalid("response", "collaborator array");
  value.forEach((item, index) => {
    const path = `response[${index}]`;
    assertRecord(item, path);
    ["id", "name", "role", "status"].forEach((key) =>
      assertString(item[key], `${path}.${key}`),
    );
  });
  return value;
};

export const parseAgentMetricsResponse: ResponseParser<AgentMetrics> = (
  value,
) => {
  assertRecord(value, "response");
  ["agent_id", "agent_name", "status", "container"].forEach((key) =>
    assertString(value[key], `response.${key}`),
  );
  const tokens = value.tokens;
  assertRecord(tokens, "response.tokens");
  [
    "used_today",
    "used_month",
    "used_total",
    "cache_read_today",
    "cache_read_month",
    "cache_read_total",
    "cache_creation_today",
    "cache_creation_month",
    "cache_creation_total",
  ].forEach((key) => assertNumber(tokens[key], `response.tokens.${key}`));
  const tasks = value.tasks;
  assertRecord(tasks, "response.tasks");
  ["total", "done", "pending", "completion_rate"].forEach((key) =>
    assertNumber(tasks[key], `response.tasks.${key}`),
  );
  const approvals = value.approvals;
  assertRecord(approvals, "response.approvals");
  assertNumber(approvals.total, "response.approvals.total");
  assertNumber(approvals.pending, "response.approvals.pending");
  const activity = value.activity;
  assertRecord(activity, "response.activity");
  assertNumber(activity.actions_last_24h, "response.activity.actions_last_24h");
  return {
    agent_id: readString(value, "agent_id", "response"),
    agent_name: readString(value, "agent_name", "response"),
    status: readString(value, "status", "response"),
    container: readString(value, "container", "response"),
    tokens: {
      used_today: readNumber(tokens, "used_today", "response.tokens"),
      used_month: readNumber(tokens, "used_month", "response.tokens"),
      used_total: readNumber(tokens, "used_total", "response.tokens"),
      cache_read_today: readNumber(
        tokens,
        "cache_read_today",
        "response.tokens",
      ),
      cache_read_month: readNumber(
        tokens,
        "cache_read_month",
        "response.tokens",
      ),
      cache_read_total: readNumber(
        tokens,
        "cache_read_total",
        "response.tokens",
      ),
      cache_creation_today: readNumber(
        tokens,
        "cache_creation_today",
        "response.tokens",
      ),
      cache_creation_month: readNumber(
        tokens,
        "cache_creation_month",
        "response.tokens",
      ),
      cache_creation_total: readNumber(
        tokens,
        "cache_creation_total",
        "response.tokens",
      ),
    },
    tasks: {
      total: readNumber(tasks, "total", "response.tasks"),
      done: readNumber(tasks, "done", "response.tasks"),
      pending: readNumber(tasks, "pending", "response.tasks"),
      completion_rate: readNumber(tasks, "completion_rate", "response.tasks"),
    },
    approvals: {
      total: readNumber(approvals, "total", "response.approvals"),
      pending: readNumber(approvals, "pending", "response.approvals"),
    },
    activity: {
      actions_last_24h: readNumber(
        activity,
        "actions_last_24h",
        "response.activity",
      ),
    },
  };
};

export const parseFileItemsResponse: ResponseParser<FileItem[]> = (value) => {
  if (!Array.isArray(value)) invalid("response", "file item array");
  value.forEach((item, index) => {
    const path = `response[${index}]`;
    assertRecord(item, path);
    assertString(item.name, `${path}.name`);
    assertString(item.path, `${path}.path`);
    assertBoolean(item.is_dir, `${path}.is_dir`);
    assertOptionalNumber(item.size, `${path}.size`);
  });
  return value;
};

export const parseFilePreviewResponse: ResponseParser<FilePreview> = (
  value,
) => {
  assertRecord(value, "response");
  ["path", "name", "content", "type", "kind", "content_hash"].forEach((key) =>
    assertOptionalString(value[key], `response.${key}`),
  );
  if (value.sheets !== undefined) {
    if (!Array.isArray(value.sheets)) invalid("response.sheets", "sheet array");
    value.sheets.forEach((sheet, sheetIndex) => {
      const path = `response.sheets[${sheetIndex}]`;
      assertRecord(sheet, path);
      assertOptionalString(sheet.name, `${path}.name`);
      if (!Array.isArray(sheet.rows)) invalid(`${path}.rows`, "row array");
      sheet.rows.forEach((row, rowIndex) =>
        assertStringArray(row, `${path}.rows[${rowIndex}]`),
      );
    });
  }
  return value;
};

export const parseFileLockResponse: ResponseParser<FileLockResponse> = (
  value,
) => {
  assertRecord(value, "response");
  assertString(value.status, "response.status");
  assertOptionalString(value.path, "response.path");
  assertOptionalNullableString(value.locked_by, "response.locked_by");
  return {
    status: value.status,
    ...(value.path === undefined ? {} : { path: value.path }),
    ...(value.locked_by === undefined ? {} : { locked_by: value.locked_by }),
  };
};

export const parseFileMutationResponse: ResponseParser<FileMutationResponse> = (
  value,
) => {
  assertRecord(value, "response");
  assertString(value.status, "response.status");
  assertOptionalString(value.path, "response.path");
  assertOptionalString(value.revision_id, "response.revision_id");
  return {
    status: value.status,
    ...(value.path === undefined ? {} : { path: value.path }),
    ...(value.revision_id === undefined
      ? {}
      : { revision_id: value.revision_id }),
  };
};

export const parseFileRevisionsResponse: ResponseParser<FileRevision[]> = (
  value,
) => {
  if (!Array.isArray(value)) invalid("response", "file revision array");
  value.forEach((item, index) => {
    const path = `response[${index}]`;
    assertRecord(item, path);
    assertString(item.id, `${path}.id`);
    assertString(item.path, `${path}.path`);
    assertString(item.created_at, `${path}.created_at`);
    assertOptionalNullableString(item.created_by, `${path}.created_by`);
    assertOptionalNumber(item.size, `${path}.size`);
  });
  return value;
};

export const parseUploadResponse: ResponseParser<UploadResponse> = (value) => {
  assertRecord(value, "response");
  assertString(value.filename, "response.filename");
  assertString(value.extracted_text, "response.extracted_text");
  assertString(value.workspace_path, "response.workspace_path");
  assertString(value.image_data_url, "response.image_data_url");
  assertOptionalString(value.path, "response.path");
  assertOptionalNumber(value.size, "response.size");
  assertOptionalString(value.version_token, "response.version_token");
  assertOptionalNullableString(value.modified_at, "response.modified_at");
  assertOptionalNullableString(value.revision_id, "response.revision_id");
  assertOptionalString(value.saved_filename, "response.saved_filename");
  return {
    filename: value.filename,
    extracted_text: value.extracted_text,
    workspace_path: value.workspace_path,
    image_data_url: value.image_data_url,
    ...(value.path === undefined ? {} : { path: value.path }),
    ...(value.size === undefined ? {} : { size: value.size }),
    ...(value.version_token === undefined
      ? {}
      : { version_token: value.version_token }),
    ...(value.modified_at === undefined
      ? {}
      : { modified_at: value.modified_at }),
    ...(value.revision_id === undefined
      ? {}
      : { revision_id: value.revision_id }),
    ...(value.saved_filename === undefined
      ? {}
      : { saved_filename: value.saved_filename }),
  };
};

export const parseWorkspaceUploadResponse: ResponseParser<
  WorkspaceUploadResponse
> = (value) => {
  assertRecord(value, "response");
  assertString(value.status, "response.status");
  assertString(value.path, "response.path");
  assertString(value.url, "response.url");
  assertString(value.filename, "response.filename");
  assertNumber(value.size, "response.size");
  assertNullableString(
    value.extracted_text_path,
    "response.extracted_text_path",
  );
  return {
    status: value.status,
    path: value.path,
    url: value.url,
    filename: value.filename,
    size: value.size,
    extracted_text_path: value.extracted_text_path,
  };
};

export const parseGroupWorkspaceUploadResponse: ResponseParser<
  GroupWorkspaceUploadResponse
> = (value) => {
  assertRecord(value, "response");
  assertString(value.path, "response.path");
  assertNumber(value.size, "response.size");
  assertString(value.version_token, "response.version_token");
  assertOptionalNullableString(value.modified_at, "response.modified_at");
  assertOptionalNullableString(value.revision_id, "response.revision_id");
  return {
    path: value.path,
    size: value.size,
    version_token: value.version_token,
    ...(value.modified_at === undefined
      ? {}
      : { modified_at: value.modified_at }),
    ...(value.revision_id === undefined
      ? {}
      : { revision_id: value.revision_id }),
  };
};

export const parseControlStatusResponse: ResponseParser<
  ControlStatusResponse
> = (value) => {
  assertRecord(value, "response");
  assertString(value.status, "response.status");
  assertOptionalString(value.detail, "response.detail");
  assertOptionalString(value.message, "response.message");
  assertOptionalBoolean(value.success, "response.success");
  return {
    status: value.status,
    ...(value.detail === undefined ? {} : { detail: value.detail }),
    ...(value.message === undefined ? {} : { message: value.message }),
    ...(value.success === undefined ? {} : { success: value.success }),
  };
};

export const parseControlScreenshotResponse: ResponseParser<
  ControlScreenshotResponse
> = (value) => {
  parseControlStatusResponse(value);
  assertRecord(value, "response");
  assertOptionalString(value.screenshot, "response.screenshot");
  if (value.screen_size !== undefined && value.screen_size !== null) {
    assertRecord(value.screen_size, "response.screen_size");
    assertNumber(value.screen_size.width, "response.screen_size.width");
    assertNumber(value.screen_size.height, "response.screen_size.height");
  }
  return {
    ...parseControlStatusResponse(value),
    ...(value.screenshot === undefined ? {} : { screenshot: value.screenshot }),
    ...(value.screen_size === undefined || value.screen_size === null
      ? {}
      : {
          screen_size: {
            width: readNumber(
              value.screen_size,
              "width",
              "response.screen_size",
            ),
            height: readNumber(
              value.screen_size,
              "height",
              "response.screen_size",
            ),
          },
        }),
  };
};

export const parseControlUnlockResponse: ResponseParser<
  ControlUnlockResponse
> = (value) => {
  parseControlStatusResponse(value);
  assertRecord(value, "response");
  assertOptionalBoolean(value.cookies_exported, "response.cookies_exported");
  assertOptionalNumber(value.cookie_count, "response.cookie_count");
  if (value.cookies !== undefined) {
    if (!Array.isArray(value.cookies))
      invalid("response.cookies", "JSON array");
    value.cookies.forEach((cookie, index) =>
      assertJsonValue(cookie, `response.cookies[${index}]`),
    );
  }
  return {
    ...parseControlStatusResponse(value),
    ...(value.cookies_exported === undefined
      ? {}
      : { cookies_exported: value.cookies_exported }),
    ...(value.cookie_count === undefined
      ? {}
      : { cookie_count: value.cookie_count }),
    ...(value.cookies === undefined ? {} : { cookies: value.cookies }),
  };
};

export const parseTokenResponse: ResponseParser<TokenResponse> = (value) => {
  assertRecord(value, "response");
  assertString(value.access_token, "response.access_token");
  assertString(value.token_type, "response.token_type");
  const user = parseUserAt(value.user, "response.user");
  assertOptionalBoolean(
    value.needs_company_setup,
    "response.needs_company_setup",
  );
  return {
    access_token: value.access_token,
    token_type: value.token_type,
    user,
    ...(value.needs_company_setup === undefined
      ? {}
      : { needs_company_setup: value.needs_company_setup }),
  };
};

export const parseLoginResponse: ResponseParser<
  | TokenResponse
  | {
      requires_tenant_selection: boolean;
      login_identifier: string;
      tenants: OAuthTenantChoice[];
    }
> = (value) => {
  assertRecord(value, "response");
  if (value.requires_tenant_selection === true) {
    assertString(value.login_identifier, "response.login_identifier");
    if (!Array.isArray(value.tenants))
      invalid("response.tenants", "tenant array");
    const tenants = value.tenants.flatMap((tenant, index) => {
      const path = `response.tenants[${index}]`;
      assertRecord(tenant, path);
      if (tenant.tenant_id === null) return [];
      assertString(tenant.tenant_id, `${path}.tenant_id`);
      assertString(tenant.tenant_name, `${path}.tenant_name`);
      assertString(tenant.tenant_slug, `${path}.tenant_slug`);
      assertNullableString(tenant.logo_url, `${path}.logo_url`);
      return [
        {
          tenant_id: tenant.tenant_id,
          tenant_name: tenant.tenant_name,
          tenant_slug: tenant.tenant_slug,
          ...(tenant.logo_url === null ? {} : { logo_url: tenant.logo_url }),
        },
      ];
    });
    return {
      requires_tenant_selection: true,
      login_identifier: value.login_identifier,
      tenants,
    };
  }
  return parseTokenResponse(value);
};

export const parseBooleanFlagResponse =
  (key: string): ResponseParser<Record<string, boolean>> =>
  (value) => {
    assertRecord(value, "response");
    return { [key]: readBoolean(value, key, "response") };
  };

export const parseStringEnvelopeResponse =
  (key: string): ResponseParser<Record<string, string>> =>
  (value) => {
    assertRecord(value, "response");
    return { [key]: readString(value, key, "response") };
  };

export const parseOkMessageResponse: ResponseParser<{
  ok: boolean;
  message: string;
}> = (value) => {
  assertRecord(value, "response");
  return {
    ok: readBoolean(value, "ok", "response"),
    message: readString(value, "message", "response"),
  };
};

export const parseOkResponse: ResponseParser<{ ok: boolean }> = (value) => {
  assertRecord(value, "response");
  return { ok: readBoolean(value, "ok", "response") };
};

export const parseAuthRegisterResponse: ResponseParser<{
  user_id: string;
  email: string;
  access_token: string;
  message: string;
  user?: User;
  needs_company_setup: boolean;
}> = (value) => {
  assertRecord(value, "response");
  if (value.user_id === undefined) {
    const token = parseTokenResponse(value);
    return {
      user_id: token.user.id,
      email: token.user.email,
      access_token: token.access_token,
      message: "",
      user: token.user,
      needs_company_setup: token.needs_company_setup ?? false,
    };
  }
  const user =
    value.user === undefined
      ? undefined
      : parseUserAt(value.user, "response.user");
  return {
    user_id: readString(value, "user_id", "response"),
    email: readString(value, "email", "response"),
    access_token: readString(value, "access_token", "response"),
    message: readString(value, "message", "response"),
    needs_company_setup: readBoolean(value, "needs_company_setup", "response"),
    ...(user === undefined ? {} : { user }),
  };
};

export const parseSwitchTenantResponse: ResponseParser<{
  access_token: string;
  redirect_url?: string;
  message?: string;
}> = (value) => {
  assertRecord(value, "response");
  const redirectUrl = readOptionalNullableString(
    value,
    "redirect_url",
    "response",
  );
  const message = readOptionalNullableString(value, "message", "response");
  return {
    access_token: readString(value, "access_token", "response"),
    ...(redirectUrl == null ? {} : { redirect_url: redirectUrl }),
    ...(message == null ? {} : { message }),
  };
};

function parseOnboardingStatusAt(
  value: unknown,
  path: string,
): OnboardingStatus {
  assertRecord(value, path);
  const status = readString(value, "status", path);
  if (
    status !== "not_started" &&
    status !== "in_progress" &&
    status !== "completed"
  ) {
    invalid(`${path}.status`, "onboarding status");
  }
  const entryMode = value.entry_mode;
  if (entryMode !== null && entryMode !== "create" && entryMode !== "join") {
    invalid(`${path}.entry_mode`, "create, join, or null");
  }
  return {
    exists: readBoolean(value, "exists", path),
    status,
    current_step: readString(value, "current_step", path),
    entry_mode: entryMode,
    personal_assistant_agent_id: readNullableString(
      value,
      "personal_assistant_agent_id",
      path,
    ),
    completed_at: readNullableString(value, "completed_at", path),
  };
}

export const parseOnboardingStatusResponse: ResponseParser<OnboardingStatus> = (
  value,
) => parseOnboardingStatusAt(value, "response");

export const parsePersonalAssistantResponse: ResponseParser<
  PersonalAssistantResponse
> = (value) => {
  assertRecord(value, "response");
  assertRecord(value.agent, "response.agent");
  return {
    agent: {
      id: readString(value.agent, "id", "response.agent"),
      name: readString(value.agent, "name", "response.agent"),
    },
    onboarding: parseOnboardingStatusAt(
      value.onboarding,
      "response.onboarding",
    ),
  };
};

function parseTaskAt(value: unknown, path: string): Task {
  assertRecord(value, path);
  const type = readString(value, "type", path);
  if (type !== "todo" && type !== "supervision")
    invalid(`${path}.type`, "task type");
  const status = readString(value, "status", path);
  if (
    status !== "pending" &&
    status !== "doing" &&
    status !== "done" &&
    status !== "paused"
  )
    invalid(`${path}.status`, "task status");
  const priority = readString(value, "priority", path);
  if (
    priority !== "low" &&
    priority !== "medium" &&
    priority !== "high" &&
    priority !== "urgent"
  )
    invalid(`${path}.priority`, "task priority");
  return {
    id: readString(value, "id", path),
    agent_id: readString(value, "agent_id", path),
    title: readString(value, "title", path),
    type,
    status,
    priority,
    assignee: readString(value, "assignee", path),
    created_by: readString(value, "created_by", path),
    created_at: readString(value, "created_at", path),
    updated_at: readString(value, "updated_at", path),
    ...optionalStringFields(value, path, [
      "description",
      "creator_username",
      "due_date",
      "supervision_target_name",
      "supervision_channel",
      "remind_schedule",
      "completed_at",
    ]),
  };
}

function optionalStringFields(
  value: Record<string, unknown>,
  path: string,
  keys: string[],
): Record<string, string> {
  const result: Record<string, string> = {};
  keys.forEach((key) => {
    const field = value[key];
    if (field === undefined || field === null) return;
    assertString(field, `${path}.${key}`);
    result[key] = field;
  });
  return result;
}

export const parseTaskResponse: ResponseParser<Task> = (value) =>
  parseTaskAt(value, "response");
export const parseTasksResponse: ResponseParser<Task[]> = (value) =>
  parseArray(value, "response", parseTaskAt);

export const parseTaskLogsResponse: ResponseParser<
  { id: string; task_id: string; content: string; created_at: string }[]
> = (value) =>
  parseArray(value, "response", (item, path) => {
    assertRecord(item, path);
    return {
      id: readString(item, "id", path),
      task_id: readString(item, "task_id", path),
      content: readString(item, "content", path),
      created_at: readString(item, "created_at", path),
    };
  });

export const parseTaskTriggerResponse: ResponseParser<TaskTriggerResponse> = (
  value,
) => {
  assertRecord(value, "response");
  return {
    status: readString(value, "status", "response"),
    task_id: readString(value, "task_id", "response"),
  };
};

export const parseFileContentResponse: ResponseParser<{
  path: string;
  content: string;
}> = (value) => {
  assertRecord(value, "response");
  return {
    path: readString(value, "path", "response"),
    content: readString(value, "content", "response"),
  };
};

export const parseContentResponse: ResponseParser<{ content: string }> = (
  value,
) => {
  assertRecord(value, "response");
  return { content: readString(value, "content", "response") };
};

function parseFocusAt(value: unknown, path: string): FocusApiItem {
  assertRecord(value, path);
  const status = readString(value, "status", path);
  if (status !== "in_progress" && status !== "completed")
    invalid(`${path}.status`, "focus status");
  const kind = readString(value, "kind", path);
  if (kind !== "normal" && kind !== "system")
    invalid(`${path}.kind`, "focus kind");
  const metadata =
    value.metadata === undefined
      ? undefined
      : parseJsonRecord(value.metadata, `${path}.metadata`);
  const title = readOptionalNullableString(value, "title", path);
  const completedAt = readOptionalNullableString(value, "completed_at", path);
  const createdAt = readOptionalNullableString(value, "created_at", path);
  const updatedAt = readOptionalNullableString(value, "updated_at", path);
  return {
    id: readString(value, "id", path),
    agent_id: readString(value, "agent_id", path),
    key: readString(value, "key", path),
    description: readString(value, "description", path),
    status,
    kind,
    source: readString(value, "source", path),
    sort_order: readNumber(value, "sort_order", path),
    ...(title === undefined ? {} : { title }),
    ...(metadata === undefined ? {} : { metadata }),
    ...(completedAt === undefined ? {} : { completed_at: completedAt }),
    ...(createdAt === undefined ? {} : { created_at: createdAt }),
    ...(updatedAt === undefined ? {} : { updated_at: updatedAt }),
  };
}

export const parseFocusResponse: ResponseParser<FocusApiItem> = (value) =>
  parseFocusAt(value, "response");
export const parseFocusListResponse: ResponseParser<FocusApiItem[]> = (value) =>
  parseArray(value, "response", parseFocusAt);

export const parseChannelConfigResponse: ResponseParser<ChannelConfig> = (
  value,
) => {
  assertRecord(value, "response");
  const extraConfig =
    value.extra_config === null
      ? null
      : parseJsonRecord(value.extra_config, "response.extra_config");
  return {
    id: readString(value, "id", "response"),
    agent_id: readString(value, "agent_id", "response"),
    channel_type: readString(value, "channel_type", "response"),
    app_id: readNullableString(value, "app_id", "response"),
    is_configured: readBoolean(value, "is_configured", "response"),
    is_connected: readBoolean(value, "is_connected", "response"),
    last_tested_at: readNullableString(value, "last_tested_at", "response"),
    extra_config: extraConfig,
    created_at: readString(value, "created_at", "response"),
  };
};

function parseLlmModelAt(value: unknown, path: string): LlmModel {
  assertRecord(value, path);
  return {
    id: readString(value, "id", path),
    provider: readString(value, "provider", path),
    model: readString(value, "model", path),
    base_url: readNullableString(value, "base_url", path),
    label: readString(value, "label", path),
    temperature:
      value.temperature === null
        ? null
        : readNumber(value, "temperature", path),
    api_key_masked: readString(value, "api_key_masked", path),
    max_tokens_per_day:
      value.max_tokens_per_day === null
        ? null
        : readNumber(value, "max_tokens_per_day", path),
    enabled: readBoolean(value, "enabled", path),
    supports_vision: readBoolean(value, "supports_vision", path),
    supports_tool_calling:
      value.supports_tool_calling === null
        ? null
        : readBoolean(value, "supports_tool_calling", path),
    tool_calling_capability_source: readNullableString(
      value,
      "tool_calling_capability_source",
      path,
    ),
    tool_calling_checked_at: readNullableString(
      value,
      "tool_calling_checked_at",
      path,
    ),
    tool_calling_error: readNullableString(value, "tool_calling_error", path),
    max_output_tokens:
      value.max_output_tokens === null
        ? null
        : readNumber(value, "max_output_tokens", path),
    request_timeout:
      value.request_timeout === null
        ? null
        : readNumber(value, "request_timeout", path),
    created_at: readString(value, "created_at", path),
    deleted_at: readNullableString(value, "deleted_at", path),
  };
}

export const parseLlmModelsResponse: ResponseParser<LlmModel[]> = (value) =>
  parseArray(value, "response", parseLlmModelAt);

function parseActivityAt(value: unknown, path: string): ActivityItem {
  assertRecord(value, path);
  assertJsonValue(value.detail, `${path}.detail`);
  return {
    id: readString(value, "id", path),
    action_type: readString(value, "action_type", path),
    summary: readString(value, "summary", path),
    detail: value.detail,
    related_id: readNullableString(value, "related_id", path),
    created_at: readNullableString(value, "created_at", path),
  };
}

export const parseActivityListResponse: ResponseParser<ActivityItem[]> = (
  value,
) => parseArray(value, "response", parseActivityAt);

function parseInboxAt(value: unknown, path: string): InboxMessage {
  assertRecord(value, path);
  if (value.sender_type !== "agent") invalid(`${path}.sender_type`, "agent");
  const readAt = readOptionalNullableString(value, "read_at", path);
  return {
    id: readString(value, "id", path),
    sender_type: "agent",
    sender_name: readString(value, "sender_name", path),
    content: readString(value, "content", path),
    session_title: readNullableString(value, "session_title", path),
    created_at: readNullableString(value, "created_at", path),
    ...(readAt === undefined ? {} : { read_at: readAt }),
  };
}

export const parseInboxResponse: ResponseParser<InboxMessage[]> = (value) =>
  parseArray(value, "response", parseInboxAt);

function parseScheduleAt(value: unknown, path: string): Schedule {
  assertRecord(value, path);
  return {
    id: readString(value, "id", path),
    agent_id: readString(value, "agent_id", path),
    name: readString(value, "name", path),
    instruction: readString(value, "instruction", path),
    cron_expr: readString(value, "cron_expr", path),
    is_enabled: readBoolean(value, "is_enabled", path),
    last_run_at: readNullableString(value, "last_run_at", path),
    next_run_at: readNullableString(value, "next_run_at", path),
    run_count: readNumber(value, "run_count", path),
    created_by: readNullableString(value, "created_by", path),
    creator_username: readNullableString(value, "creator_username", path),
    created_at: readNullableString(value, "created_at", path),
    delivery_target_id: readNullableString(value, "delivery_target_id", path),
  };
}

export const parseScheduleResponse: ResponseParser<Schedule> = (value) =>
  parseScheduleAt(value, "response");
export const parseSchedulesResponse: ResponseParser<Schedule[]> = (value) =>
  parseArray(value, "response", parseScheduleAt);

export const parseScheduleRunResponse: ResponseParser<ScheduleRunResponse> = (
  value,
) => {
  assertRecord(value, "response");
  return {
    status: readString(value, "status", "response"),
    schedule_id: readString(value, "schedule_id", "response"),
    run_id: readString(value, "run_id", "response"),
  };
};

export const parseScheduleHistoryResponse: ResponseParser<
  ScheduleHistoryItem[]
> = (value) =>
  parseArray(value, "response", (item, path) => {
    assertRecord(item, path);
    return {
      id: readString(item, "id", path),
      created_at: readNullableString(item, "created_at", path),
      summary: readString(item, "summary", path),
      instruction: readString(item, "instruction", path),
      reply: readString(item, "reply", path),
    };
  });

function parseSkillAt(value: unknown, path: string): Skill {
  assertRecord(value, path);
  const description = readOptionalNullableString(value, "description", path);
  const icon = readOptionalNullableString(value, "icon", path);
  const createdAt = readOptionalNullableString(value, "created_at", path);
  return {
    id: readString(value, "id", path),
    name: readString(value, "name", path),
    folder_name: readString(value, "folder_name", path),
    is_default: readBoolean(value, "is_default", path),
    ...(description === undefined ? {} : { description }),
    ...(icon === undefined ? {} : { icon }),
    ...(createdAt === undefined ? {} : { created_at: createdAt }),
  };
}

export const parseSkillResponse: ResponseParser<Skill> = (value) =>
  parseSkillAt(value, "response");
export const parseSkillsResponse: ResponseParser<Skill[]> = (value) =>
  parseArray(value, "response", parseSkillAt);

export const parseSkillDetailResponse: ResponseParser<SkillDetail> = (
  value,
) => {
  assertRecord(value, "response");
  const files = parseArray(value.files, "response.files", (item, path) => {
    assertRecord(item, path);
    return {
      path: readString(item, "path", path),
      content: readString(item, "content", path),
    };
  });
  return {
    id: readString(value, "id", "response"),
    name: readString(value, "name", "response"),
    description: readNullableString(value, "description", "response"),
    category: readString(value, "category", "response"),
    icon: readNullableString(value, "icon", "response"),
    folder_name: readString(value, "folder_name", "response"),
    is_builtin: readBoolean(value, "is_builtin", "response"),
    files,
  };
};

export const parseSkillMutationResult: ResponseParser<SkillMutationResult> = (
  value,
) => {
  assertRecord(value, "response");
  return {
    id: readString(value, "id", "response"),
    name: readString(value, "name", "response"),
  };
};

function parseClawhubSkillAt(
  value: unknown,
  path: string,
): {
  slug: string;
  name: string;
  description?: string;
  author?: string;
  tier?: number;
} {
  assertRecord(value, path);
  const description = readOptionalString(value, "description", path);
  const author = readOptionalString(value, "author", path);
  const tier = readOptionalNumber(value, "tier", path);
  return {
    slug: readString(value, "slug", path),
    name: readString(value, "name", path),
    ...(description === undefined ? {} : { description }),
    ...(author === undefined ? {} : { author }),
    ...(tier === undefined ? {} : { tier }),
  };
}

export const parseClawhubSkillResponse = (value: unknown) =>
  parseClawhubSkillAt(value, "response");
export const parseClawhubSkillsResponse = (value: unknown) =>
  parseArray(value, "response", parseClawhubSkillAt);

export const parseSkillImportResponse: ResponseParser<SkillImportResult> = (
  value,
) => {
  assertRecord(value, "response");
  const tier = readOptionalNumber(value, "tier", "response");
  const path = readOptionalString(value, "path", "response");
  const filesWritten = readOptionalNumber(value, "files_written", "response");
  return {
    name: readString(value, "name", "response"),
    file_count: readNumber(value, "file_count", "response"),
    ...(tier === undefined ? {} : { tier }),
    ...(path === undefined ? {} : { path }),
    ...(filesWritten === undefined ? {} : { files_written: filesWritten }),
  };
};

export const parseSkillUrlPreviewResponse: ResponseParser<SkillUrlPreview> = (
  value,
) => {
  assertRecord(value, "response");
  if (value.files !== undefined)
    assertStringArray(value.files, "response.files");
  const description = readOptionalString(value, "description", "response");
  const tier = readOptionalNumber(value, "tier", "response");
  return {
    name: readString(value, "name", "response"),
    file_count: readNumber(value, "file_count", "response"),
    ...(description === undefined ? {} : { description }),
    ...(tier === undefined ? {} : { tier }),
    ...(value.files === undefined ? {} : { files: value.files }),
  };
};

function parseTriggerAt(value: unknown, path: string): Trigger {
  assertRecord(value, path);
  assertRecord(value.config, `${path}.config`);
  const config = value.config;
  const expr = readOptionalString(config, "expr", `${path}.config`);
  const minutes = readOptionalNumber(config, "minutes", `${path}.config`);
  const at = readOptionalString(config, "at", `${path}.config`);
  const url = readOptionalString(config, "url", `${path}.config`);
  const token = readOptionalString(config, "token", `${path}.config`);
  const fromAgentName = readOptionalString(
    config,
    "from_agent_name",
    `${path}.config`,
  );
  const fromUserName = readOptionalString(
    config,
    "from_user_name",
    `${path}.config`,
  );
  return {
    id: readString(value, "id", path),
    name: readString(value, "name", path),
    type: readString(value, "type", path),
    config: {
      ...(expr === undefined ? {} : { expr }),
      ...(minutes === undefined ? {} : { minutes }),
      ...(at === undefined ? {} : { at }),
      ...(url === undefined ? {} : { url }),
      ...(token === undefined ? {} : { token }),
      ...(fromAgentName === undefined
        ? {}
        : { from_agent_name: fromAgentName }),
      ...(fromUserName === undefined ? {} : { from_user_name: fromUserName }),
    },
    reason: readString(value, "reason", path),
    focus_ref: readNullableString(value, "focus_ref", path),
    is_enabled: readBoolean(value, "is_enabled", path),
    is_system: readBoolean(value, "is_system", path),
    fire_count: readNumber(value, "fire_count", path),
    max_fires:
      value.max_fires === null ? null : readNumber(value, "max_fires", path),
    cooldown_seconds: readNumber(value, "cooldown_seconds", path),
    last_fired_at: readNullableString(value, "last_fired_at", path),
    created_at: readNullableString(value, "created_at", path),
    expires_at: readNullableString(value, "expires_at", path),
    delivery_target_id: readNullableString(value, "delivery_target_id", path),
  };
}

export const parseTriggersResponse: ResponseParser<Trigger[]> = (value) =>
  parseArray(value, "response", parseTriggerAt);

function parseCredentialAt(value: unknown, path: string): Credential {
  assertRecord(value, path);
  return {
    id: readString(value, "id", path),
    agent_id: readString(value, "agent_id", path),
    credential_type: readString(value, "credential_type", path),
    platform: readString(value, "platform", path),
    display_name: readString(value, "display_name", path),
    status: readString(value, "status", path),
    cookies_updated_at: readNullableString(value, "cookies_updated_at", path),
    last_login_at: readNullableString(value, "last_login_at", path),
    last_injected_at: readNullableString(value, "last_injected_at", path),
    has_cookies: readBoolean(value, "has_cookies", path),
    created_at: readString(value, "created_at", path),
    updated_at: readString(value, "updated_at", path),
  };
}

export const parseCredentialResponse: ResponseParser<Credential> = (value) =>
  parseCredentialAt(value, "response");
export const parseCredentialsResponse: ResponseParser<Credential[]> = (value) =>
  parseArray(value, "response", parseCredentialAt);

function parseExperienceAt(value: unknown, path: string): ExperienceEntry {
  assertRecord(value, path);
  const status = readString(value, "status", path);
  if (status !== "draft" && status !== "published" && status !== "retired")
    invalid(`${path}.status`, "experience status");
  const visibility = readString(value, "visibility_scope", path);
  if (
    visibility !== "company" &&
    visibility !== "department" &&
    visibility !== "user"
  )
    invalid(`${path}.visibility_scope`, "visibility scope");
  const origin = readString(value, "origin", path);
  if (origin !== "chat" && origin !== "legacy_plaza")
    invalid(`${path}.origin`, "experience origin");
  assertStringArray(value.tags, `${path}.tags`);
  const createdByName = readOptionalNullableString(
    value,
    "created_by_name",
    path,
  );
  const originAgentName = readOptionalNullableString(
    value,
    "origin_agent_name",
    path,
  );
  return {
    id: readString(value, "id", path),
    draft_of_id: readNullableString(value, "draft_of_id", path),
    tenant_id: readNullableString(value, "tenant_id", path),
    title: readString(value, "title", path),
    body: readString(value, "body", path),
    applicability: readString(value, "applicability", path),
    status,
    tags: value.tags,
    visibility_scope: visibility,
    visibility_scope_id: readNullableString(value, "visibility_scope_id", path),
    origin,
    origin_session_id: readNullableString(value, "origin_session_id", path),
    origin_agent_id: readNullableString(value, "origin_agent_id", path),
    created_by: readString(value, "created_by", path),
    reviewed_by: readNullableString(value, "reviewed_by", path),
    last_reviewed_at: readNullableString(value, "last_reviewed_at", path),
    retired_at: readNullableString(value, "retired_at", path),
    created_at: readString(value, "created_at", path),
    updated_at: readNullableString(value, "updated_at", path),
    ...(createdByName === undefined ? {} : { created_by_name: createdByName }),
    ...(originAgentName === undefined
      ? {}
      : { origin_agent_name: originAgentName }),
    ...(value.can_manage === undefined
      ? {}
      : {
          can_manage:
            value.can_manage === null
              ? null
              : readOptionalBoolean(value, "can_manage", path),
        }),
  };
}

export const parseExperienceResponse: ResponseParser<ExperienceEntry> = (
  value,
) => parseExperienceAt(value, "response");
export const parseExperienceListResponse: ResponseParser<ExperienceEntry[]> = (
  value,
) => parseArray(value, "response", parseExperienceAt);

function parseOrgDepartmentAt(value: unknown, path: string): OrgDepartmentItem {
  assertRecord(value, path);
  const departmentPath = readOptionalString(value, "path", path);
  const parentId = readOptionalNullableString(value, "parent_id", path);
  const memberCount = readOptionalNumber(value, "member_count", path);
  return {
    id: readString(value, "id", path),
    name: readString(value, "name", path),
    ...(departmentPath === undefined ? {} : { path: departmentPath }),
    ...(parentId === undefined ? {} : { parent_id: parentId }),
    ...(memberCount === undefined ? {} : { member_count: memberCount }),
  };
}

export const parseOrgDepartmentsResponse: ResponseParser<{
  items: OrgDepartmentItem[];
  total_member: number;
}> = (value) => {
  assertRecord(value, "response");
  return {
    items: parseArray(value.items, "response.items", parseOrgDepartmentAt),
    total_member: readNumber(value, "total_member", "response"),
  };
};

export const parseGatewayMessagesResponse: ResponseParser<GatewayMessage[]> = (
  value,
) =>
  parseArray(value, "response", (item, path) => {
    assertRecord(item, path);
    assertJsonValue(item.result, `${path}.result`);
    return {
      id: readString(item, "id", path),
      sender_agent_name: readNullableString(item, "sender_agent_name", path),
      content: readString(item, "content", path),
      status: readString(item, "status", path),
      result: item.result,
      created_at: readNullableString(item, "created_at", path),
      delivered_at: readNullableString(item, "delivered_at", path),
      completed_at: readNullableString(item, "completed_at", path),
    };
  });

export const parseHintResponse: ResponseParser<{ hint: string }> = (value) => {
  assertRecord(value, "response");
  return { hint: readString(value, "hint", "response") };
};

export const parseRegistrationConfigResponse: ResponseParser<{
  allow_self_create_company: boolean;
}> = (value) => {
  assertRecord(value, "response");
  return {
    allow_self_create_company: readBoolean(
      value,
      "allow_self_create_company",
      "response",
    ),
  };
};

export const parseApiKeyResponse: ResponseParser<{
  api_key: string;
  message: string;
}> = (value) => {
  assertRecord(value, "response");
  return {
    api_key: readString(value, "api_key", "response"),
    message: readString(value, "message", "response"),
  };
};

export const parseWebhookUrlResponse: ResponseParser<{
  webhook_url: string;
}> = (value) => {
  assertRecord(value, "response");
  return { webhook_url: readString(value, "webhook_url", "response") };
};

export const parseUnreadCountResponse: ResponseParser<{
  unread_count: number;
}> = (value) => {
  assertRecord(value, "response");
  return { unread_count: readNumber(value, "unread_count", "response") };
};

export const parseCurrentUrlResponse: ResponseParser<{
  status: string;
  url: string;
}> = (value) => {
  assertRecord(value, "response");
  return {
    status: readString(value, "status", "response"),
    url: readString(value, "url", "response"),
  };
};

export const parseSkillSettingsResponse: ResponseParser<{
  configured: boolean;
  source: string;
  masked: string;
  clawhub_configured: boolean;
  clawhub_masked: string;
}> = (value) => {
  assertRecord(value, "response");
  return {
    configured: readBoolean(value, "configured", "response"),
    source: readString(value, "source", "response"),
    masked: readString(value, "masked", "response"),
    clawhub_configured: readBoolean(value, "clawhub_configured", "response"),
    clawhub_masked: readString(value, "clawhub_masked", "response"),
  };
};

export const parseConfiguredResponse: ResponseParser<{
  configured: boolean;
}> = (value) => {
  assertRecord(value, "response");
  return { configured: readBoolean(value, "configured", "response") };
};

export const parseClawhubConfiguredResponse: ResponseParser<{
  clawhub_configured: boolean;
}> = (value) => {
  assertRecord(value, "response");
  return {
    clawhub_configured: readBoolean(value, "clawhub_configured", "response"),
  };
};

export const parseExperienceDistillResponse: ResponseParser<{
  title: string;
  body: string;
  applicability: string;
  tags: string[];
  extracted: boolean;
}> = (value) => {
  assertRecord(value, "response");
  assertStringArray(value.tags, "response.tags");
  return {
    title: readString(value, "title", "response"),
    body: readString(value, "body", "response"),
    applicability: readString(value, "applicability", "response"),
    tags: value.tags,
    extracted: readBoolean(value, "extracted", "response"),
  };
};

export const parseDeletedResponse: ResponseParser<{ deleted: boolean }> = (
  value,
) => {
  assertRecord(value, "response");
  return { deleted: readBoolean(value, "deleted", "response") };
};

export const parseExperienceReferencesResponse: ResponseParser<{
  entry_id: string;
  read_count: number;
  cited_count: number;
}> = (value) => {
  assertRecord(value, "response");
  return {
    entry_id: readString(value, "entry_id", "response"),
    read_count: readNumber(value, "read_count", "response"),
    cited_count: readNumber(value, "cited_count", "response"),
  };
};

export const parseExperienceStatsResponse: ResponseParser<{
  total: number;
  today: number;
  cited: number;
  top_contributors: { name: string; count: number }[];
}> = (value) => {
  assertRecord(value, "response");
  return {
    total: readNumber(value, "total", "response"),
    today: readNumber(value, "today", "response"),
    cited: readNumber(value, "cited", "response"),
    top_contributors: parseArray(
      value.top_contributors,
      "response.top_contributors",
      (item, path) => {
        assertRecord(item, path);
        return {
          name: readString(item, "name", path),
          count: readNumber(item, "count", path),
        };
      },
    ),
  };
};
