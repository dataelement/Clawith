import { AppError } from "../../../services/apiError.ts";
import { parseTenantResponse } from "../../../services/apiResponseParsers.ts";
import type { Tenant } from "../../../services/apiContracts.ts";

function invalid(path: string, expected: string): never {
  throw new AppError({
    message: `Invalid enterprise settings response at ${path}: expected ${expected}`,
    code: "invalid_api_response",
    source: "http",
    retryable: false,
    details: { path, expected },
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function record(value: unknown, path: string): Record<string, unknown> {
  if (!isRecord(value)) {
    return invalid(path, "object");
  }
  return value;
}

function string(value: unknown, path: string): string {
  if (typeof value !== "string") return invalid(path, "string");
  return value;
}

function boolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") return invalid(path, "boolean");
  return value;
}

function number(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return invalid(path, "finite number");
  }
  return value;
}

function optionalString(value: unknown, path: string): string | undefined {
  return value === undefined ? undefined : string(value, path);
}

function nullableString(value: unknown, path: string): string | null {
  return value === null ? null : string(value, path);
}

function optionalNullableString(
  value: unknown,
  path: string,
): string | null | undefined {
  return value === undefined ? undefined : nullableString(value, path);
}

function optionalBoolean(value: unknown, path: string): boolean | undefined {
  return value === undefined ? undefined : boolean(value, path);
}

function optionalNumber(value: unknown, path: string): number | undefined {
  return value === undefined ? undefined : number(value, path);
}

function optionalNullableNumber(
  value: unknown,
  path: string,
): number | null | undefined {
  return value === undefined
    ? undefined
    : value === null
      ? null
      : number(value, path);
}

function array<T>(
  value: unknown,
  path: string,
  parseItem: (item: unknown, path: string) => T,
): T[] {
  if (!Array.isArray(value)) return invalid(path, "array");
  return value.map((item, index) => parseItem(item, `${path}[${index}]`));
}

export const parseEnterpriseTenant = (value: unknown): Tenant =>
  parseTenantResponse(value);

export interface IdentityProviderConfig {
  app_id?: string;
  app_key?: string;
  app_secret?: string;
  client_id?: string;
  client_secret?: string;
  corp_id?: string;
  secret?: string;
  agent_id?: string;
  bot_id?: string;
  bot_secret?: string;
  verify_token?: string;
  verify_aes_key?: string;
  authorize_url?: string;
  token_url?: string;
  user_info_url?: string;
  scope?: string;
  google_admin_authorized_email?: string;
}

function parseIdentityProviderConfig(
  value: unknown,
  path: string,
): IdentityProviderConfig {
  if (value === null || value === undefined) return {};
  const item = record(value, path);
  return {
    app_id: optionalString(item.app_id, `${path}.app_id`),
    app_key: optionalString(item.app_key, `${path}.app_key`),
    app_secret: optionalString(item.app_secret, `${path}.app_secret`),
    client_id: optionalString(item.client_id, `${path}.client_id`),
    client_secret: optionalString(item.client_secret, `${path}.client_secret`),
    corp_id: optionalString(item.corp_id, `${path}.corp_id`),
    secret: optionalString(item.secret, `${path}.secret`),
    agent_id: optionalString(item.agent_id, `${path}.agent_id`),
    bot_id: optionalString(item.bot_id, `${path}.bot_id`),
    bot_secret: optionalString(item.bot_secret, `${path}.bot_secret`),
    verify_token: optionalString(item.verify_token, `${path}.verify_token`),
    verify_aes_key: optionalString(
      item.verify_aes_key,
      `${path}.verify_aes_key`,
    ),
    authorize_url: optionalString(item.authorize_url, `${path}.authorize_url`),
    token_url: optionalString(item.token_url, `${path}.token_url`),
    user_info_url: optionalString(item.user_info_url, `${path}.user_info_url`),
    scope: optionalString(item.scope, `${path}.scope`),
    google_admin_authorized_email: optionalString(
      item.google_admin_authorized_email,
      `${path}.google_admin_authorized_email`,
    ),
  };
}

export interface IdentityProvider {
  id: string;
  provider_type: string;
  name: string;
  config: IdentityProviderConfig;
  app_id: string;
  app_secret: string;
  authorize_url: string;
  token_url: string;
  user_info_url: string;
  scope: string;
  sso_domain?: string | null;
  sso_login_enabled?: boolean;
  last_synced_at?: string | null;
}

export function parseIdentityProvider(
  value: unknown,
  path = "identity provider",
): IdentityProvider {
  const item = record(value, path);
  const config = parseIdentityProviderConfig(item.config, `${path}.config`);
  return {
    id: string(item.id, `${path}.id`),
    provider_type: string(item.provider_type, `${path}.provider_type`),
    name: string(item.name, `${path}.name`),
    config,
    app_id:
      optionalString(item.app_id, `${path}.app_id`) ??
      config.app_id ??
      config.client_id ??
      "",
    app_secret:
      optionalString(item.app_secret, `${path}.app_secret`) ??
      config.app_secret ??
      config.client_secret ??
      "",
    authorize_url:
      optionalString(item.authorize_url, `${path}.authorize_url`) ??
      config.authorize_url ??
      "",
    token_url:
      optionalString(item.token_url, `${path}.token_url`) ??
      config.token_url ??
      "",
    user_info_url:
      optionalString(item.user_info_url, `${path}.user_info_url`) ??
      config.user_info_url ??
      "",
    scope:
      optionalString(item.scope, `${path}.scope`) ??
      config.scope ??
      "openid profile email",
    sso_domain: optionalNullableString(item.sso_domain, `${path}.sso_domain`),
    sso_login_enabled: optionalBoolean(
      item.sso_login_enabled,
      `${path}.sso_login_enabled`,
    ),
    last_synced_at: optionalNullableString(
      item.last_synced_at,
      `${path}.last_synced_at`,
    ),
  };
}

export const parseIdentityProviders = (value: unknown): IdentityProvider[] =>
  array(value, "identity providers", parseIdentityProvider);

export interface OrgDepartment {
  id: string;
  name: string;
  parent_id?: string | null;
  path?: string;
  member_count?: number;
}

export interface OrgDepartmentsResult {
  items: OrgDepartment[];
  total_member: number;
}

function parseOrgDepartment(value: unknown, path: string): OrgDepartment {
  const item = record(value, path);
  return {
    id: string(item.id, `${path}.id`),
    name: string(item.name, `${path}.name`),
    parent_id: optionalNullableString(item.parent_id, `${path}.parent_id`),
    path: optionalString(item.path, `${path}.path`),
    member_count: optionalNumber(item.member_count, `${path}.member_count`),
  };
}

export function parseOrgDepartments(value: unknown): OrgDepartmentsResult {
  const item = record(value, "org departments");
  return {
    items: array(item.items, "org departments.items", parseOrgDepartment),
    total_member: number(item.total_member, "org departments.total_member"),
  };
}

export interface OrgMember {
  id: string;
  name: string;
  provider_type?: string | null;
  title?: string | null;
  department_path?: string | null;
  department_id?: string | null;
}

function parseOrgMember(value: unknown, path: string): OrgMember {
  const item = record(value, path);
  return {
    id: string(item.id, `${path}.id`),
    name: string(item.name, `${path}.name`),
    provider_type: optionalNullableString(
      item.provider_type,
      `${path}.provider_type`,
    ),
    title: optionalNullableString(item.title, `${path}.title`),
    department_path: optionalNullableString(
      item.department_path,
      `${path}.department_path`,
    ),
    department_id: optionalNullableString(
      item.department_id,
      `${path}.department_id`,
    ),
  };
}

export const parseOrgMembers = (value: unknown): OrgMember[] =>
  array(value, "org members", parseOrgMember);

export type OrgSyncResponse =
  | { error: string }
  | { departments: number; members: number; errors: string[] };

export function parseOrgSyncResponse(value: unknown): OrgSyncResponse {
  const item = record(value, "org sync");
  if (item.error !== undefined) {
    return { error: string(item.error, "org sync.error") };
  }
  return {
    departments: number(item.departments, "org sync.departments"),
    members: number(item.members, "org sync.members"),
    errors: array(item.errors, "org sync.errors", string),
  };
}

export function parseAuthorizationUrl(value: unknown): string {
  const item = record(value, "authorization response");
  return string(
    item.authorization_url,
    "authorization response.authorization_url",
  );
}

export interface OkrSettings {
  enabled: boolean;
  first_enabled_at: string | null;
  daily_report_enabled: boolean;
  daily_report_time: string;
  daily_report_skip_non_workdays: boolean;
  weekly_report_enabled: boolean;
  weekly_report_day: number;
  period_frequency: "quarterly" | "monthly";
  period_length_days: number | null;
  period_frequency_locked: boolean;
  okr_agent_id?: string | null;
}

export function parseOkrSettings(value: unknown): OkrSettings {
  const item = record(value, "OKR settings");
  const frequency = string(
    item.period_frequency,
    "OKR settings.period_frequency",
  );
  if (frequency !== "quarterly" && frequency !== "monthly") {
    return invalid("OKR settings.period_frequency", "quarterly or monthly");
  }
  return {
    enabled: boolean(item.enabled, "OKR settings.enabled"),
    first_enabled_at: nullableString(
      item.first_enabled_at,
      "OKR settings.first_enabled_at",
    ),
    daily_report_enabled: boolean(
      item.daily_report_enabled,
      "OKR settings.daily_report_enabled",
    ),
    daily_report_time: string(
      item.daily_report_time,
      "OKR settings.daily_report_time",
    ),
    daily_report_skip_non_workdays: boolean(
      item.daily_report_skip_non_workdays,
      "OKR settings.daily_report_skip_non_workdays",
    ),
    weekly_report_enabled: boolean(
      item.weekly_report_enabled,
      "OKR settings.weekly_report_enabled",
    ),
    weekly_report_day: number(
      item.weekly_report_day,
      "OKR settings.weekly_report_day",
    ),
    period_frequency: frequency,
    period_length_days:
      item.period_length_days === null
        ? null
        : number(item.period_length_days, "OKR settings.period_length_days"),
    period_frequency_locked: boolean(
      item.period_frequency_locked,
      "OKR settings.period_frequency_locked",
    ),
    okr_agent_id: optionalNullableString(
      item.okr_agent_id,
      "OKR settings.okr_agent_id",
    ),
  };
}

export function parseTenantTimezone(value: unknown): { timezone: string } {
  const tenant = parseEnterpriseTenant(value);
  return { timezone: tenant.timezone };
}

export function parseDailyCollectionResult(value: unknown): {
  message: string;
} {
  const item = record(value, "daily collection");
  return { message: string(item.message, "daily collection.message") };
}

export interface MembersWithoutOkrResult {
  okr_agent_id: string | null;
  company_okr_exists: boolean;
}

export function parseMembersWithoutOkr(
  value: unknown,
): MembersWithoutOkrResult {
  const item = record(value, "members without OKR");
  return {
    okr_agent_id: nullableString(
      item.okr_agent_id,
      "members without OKR.okr_agent_id",
    ),
    company_okr_exists: boolean(
      item.company_okr_exists,
      "members without OKR.company_okr_exists",
    ),
  };
}

export interface LLMModel {
  id: string;
  provider: string;
  model: string;
  label: string;
  base_url?: string | null;
  api_key_masked?: string;
  max_tokens_per_day?: number | null;
  enabled: boolean;
  supports_vision?: boolean;
  supports_tool_calling?: boolean | null;
  tool_calling_capability_source?: "probe" | "builtin_registry" | null;
  tool_calling_checked_at?: string | null;
  tool_calling_error?: string | null;
  max_output_tokens?: number | null;
  request_timeout?: number | null;
  temperature?: number | null;
  created_at: string;
}

function parseLlmModel(value: unknown, path: string): LLMModel {
  const item = record(value, path);
  const source = item.tool_calling_capability_source;
  if (
    source !== undefined &&
    source !== null &&
    source !== "probe" &&
    source !== "builtin_registry"
  ) {
    return invalid(
      `${path}.tool_calling_capability_source`,
      "known source or null",
    );
  }
  return {
    id: string(item.id, `${path}.id`),
    provider: string(item.provider, `${path}.provider`),
    model: string(item.model, `${path}.model`),
    label: string(item.label, `${path}.label`),
    enabled: boolean(item.enabled, `${path}.enabled`),
    created_at: string(item.created_at, `${path}.created_at`),
    base_url: optionalNullableString(item.base_url, `${path}.base_url`),
    api_key_masked: optionalString(
      item.api_key_masked,
      `${path}.api_key_masked`,
    ),
    max_tokens_per_day: optionalNullableNumber(
      item.max_tokens_per_day,
      `${path}.max_tokens_per_day`,
    ),
    supports_vision: optionalBoolean(
      item.supports_vision,
      `${path}.supports_vision`,
    ),
    supports_tool_calling:
      item.supports_tool_calling === undefined
        ? undefined
        : item.supports_tool_calling === null
          ? null
          : boolean(
              item.supports_tool_calling,
              `${path}.supports_tool_calling`,
            ),
    tool_calling_capability_source: source,
    tool_calling_checked_at: optionalNullableString(
      item.tool_calling_checked_at,
      `${path}.tool_calling_checked_at`,
    ),
    tool_calling_error: optionalNullableString(
      item.tool_calling_error,
      `${path}.tool_calling_error`,
    ),
    max_output_tokens: optionalNullableNumber(
      item.max_output_tokens,
      `${path}.max_output_tokens`,
    ),
    request_timeout: optionalNullableNumber(
      item.request_timeout,
      `${path}.request_timeout`,
    ),
    temperature: optionalNullableNumber(
      item.temperature,
      `${path}.temperature`,
    ),
  };
}

export const parseLlmModelResponse = (value: unknown): LLMModel =>
  parseLlmModel(value, "LLM model");

export const parseLlmModels = (value: unknown): LLMModel[] =>
  array(value, "LLM models", parseLlmModel);

export interface LLMProviderSpec {
  provider: string;
  display_name: string;
  protocol: string;
  default_base_url?: string | null;
  supports_tool_choice: boolean;
  default_max_tokens: number;
}

function parseLlmProvider(value: unknown, path: string): LLMProviderSpec {
  const item = record(value, path);
  return {
    provider: string(item.provider, `${path}.provider`),
    display_name: string(item.display_name, `${path}.display_name`),
    protocol: string(item.protocol, `${path}.protocol`),
    default_base_url: optionalNullableString(
      item.default_base_url,
      `${path}.default_base_url`,
    ),
    supports_tool_choice: boolean(
      item.supports_tool_choice,
      `${path}.supports_tool_choice`,
    ),
    default_max_tokens: number(
      item.default_max_tokens,
      `${path}.default_max_tokens`,
    ),
  };
}

export const parseLlmProviders = (value: unknown): LLMProviderSpec[] =>
  array(value, "LLM providers", parseLlmProvider);

export interface RuntimeModelSettings {
  tenant_id: string;
  planning_model_id: string | null;
  compact_model_id: string | null;
  planning_source: "database" | "environment" | "unavailable";
  compact_source: "database" | "environment" | "unavailable";
  candidates: Array<Pick<LLMModel, "id" | "label" | "provider" | "model">>;
}

function runtimeSource(
  value: unknown,
  path: string,
): RuntimeModelSettings["planning_source"] {
  if (
    value !== "database" &&
    value !== "environment" &&
    value !== "unavailable"
  ) {
    return invalid(path, "known runtime model source");
  }
  return value;
}

export function parseRuntimeModelSettings(
  value: unknown,
): RuntimeModelSettings {
  const item = record(value, "runtime model settings");
  return {
    tenant_id: string(item.tenant_id, "runtime model settings.tenant_id"),
    planning_model_id: nullableString(
      item.planning_model_id,
      "runtime model settings.planning_model_id",
    ),
    compact_model_id: nullableString(
      item.compact_model_id,
      "runtime model settings.compact_model_id",
    ),
    planning_source: runtimeSource(
      item.planning_source,
      "runtime model settings.planning_source",
    ),
    compact_source: runtimeSource(
      item.compact_source,
      "runtime model settings.compact_source",
    ),
    candidates: array(
      item.candidates,
      "runtime model settings.candidates",
      (candidate, path) => {
        const model = record(candidate, path);
        return {
          id: string(model.id, `${path}.id`),
          label: string(model.label, `${path}.label`),
          provider: string(model.provider, `${path}.provider`),
          model: string(model.model, `${path}.model`),
        };
      },
    ),
  };
}

export function parseTenantDefaultModel(value: unknown): {
  default_model_id: string | null;
} {
  const item = record(value, "tenant default model");
  return {
    default_model_id: nullableString(
      item.default_model_id,
      "tenant default model.default_model_id",
    ),
  };
}

export interface ConnectivityTestResult {
  capability_recorded?: boolean;
  connection_success: boolean;
  tool_calling_supported?: boolean | null;
  tool_calling_error?: string | null;
  latency_ms?: number;
  error?: string | null;
}

export function parseConnectivityTestResult(
  value: unknown,
): ConnectivityTestResult {
  const item = record(value, "connectivity test");
  return {
    connection_success: boolean(
      item.connection_success,
      "connectivity test.connection_success",
    ),
    capability_recorded: optionalBoolean(
      item.capability_recorded,
      "connectivity test.capability_recorded",
    ),
    tool_calling_supported:
      item.tool_calling_supported === undefined
        ? undefined
        : item.tool_calling_supported === null
          ? null
          : boolean(
              item.tool_calling_supported,
              "connectivity test.tool_calling_supported",
            ),
    tool_calling_error: optionalNullableString(
      item.tool_calling_error,
      "connectivity test.tool_calling_error",
    ),
    latency_ms: optionalNumber(item.latency_ms, "connectivity test.latency_ms"),
    error: optionalNullableString(item.error, "connectivity test.error"),
  };
}

export interface ClawhubSearchResult {
  slug: string;
  displayName: string;
  summary?: string | null;
  score?: number | null;
  version?: string | null;
  updatedAt?: number | null;
}

function parseClawhubResult(value: unknown, path: string): ClawhubSearchResult {
  const item = record(value, path);
  return {
    slug: string(item.slug, `${path}.slug`),
    displayName: string(item.displayName, `${path}.displayName`),
    summary: optionalNullableString(item.summary, `${path}.summary`),
    score:
      item.score === null ? null : optionalNumber(item.score, `${path}.score`),
    version: optionalNullableString(item.version, `${path}.version`),
    updatedAt:
      item.updatedAt === null
        ? null
        : optionalNumber(item.updatedAt, `${path}.updatedAt`),
  };
}

export const parseClawhubSearchResults = (
  value: unknown,
): ClawhubSearchResult[] => array(value, "ClawHub search", parseClawhubResult);

export interface UrlSkillPreview {
  name: string;
  description?: string;
  tier: number;
  files: Array<{ path: string; size: number }>;
  total_size: number;
  has_scripts: boolean;
}

export function parseUrlSkillPreview(value: unknown): UrlSkillPreview {
  const item = record(value, "skill URL preview");
  return {
    name: string(item.name, "skill URL preview.name"),
    description: optionalString(
      item.description,
      "skill URL preview.description",
    ),
    tier: number(item.tier, "skill URL preview.tier"),
    files: array(item.files, "skill URL preview.files", (file, path) => {
      const parsed = record(file, path);
      return {
        path: string(parsed.path, `${path}.path`),
        size: number(parsed.size, `${path}.size`),
      };
    }),
    total_size: number(item.total_size, "skill URL preview.total_size"),
    has_scripts: boolean(item.has_scripts, "skill URL preview.has_scripts"),
  };
}
