/** API service layer */

import type { Agent, TokenResponse, User, Task } from "../types";
import type { OAuthTenantChoice } from "./oauthCallbackResponse";
import type {
  ActivityItem,
  AgentCollaborator,
  AgentCreateRequest,
  AgentMetrics,
  AgentTemplate,
  ChannelConfig,
  ChannelConfigRequest,
  ClawhubSkill,
  CompanyCreateResponse,
  CompanyStats,
  ControlScreenshotResponse,
  ControlStatusResponse,
  ControlUnlockResponse,
  CreatedAgent,
  Credential,
  CredentialMutationRequest,
  FileItem,
  FileLockResponse,
  FileMutationResponse,
  FilePreview,
  FileRevision,
  FocusApiItem,
  GatewayMessage,
  InboxMessage,
  JsonValue,
  LlmModel,
  OnboardingStatus,
  OrgDepartmentItem,
  PersonalAssistantResponse,
  PlatformSettings,
  ResolvedTenant,
  ExperienceEntry,
  Schedule,
  ScheduleCreateRequest,
  ScheduleHistoryItem,
  ScheduleRunResponse,
  ScheduleUpdateRequest,
  Skill,
  SkillDetail,
  SkillImportResult,
  SkillMutationRequest,
  SkillMutationResult,
  SkillUrlPreview,
  TaskCreateRequest,
  TaskTriggerResponse,
  Tenant,
  TenantChoice,
  TenantSetupResponse,
  TenantTokenUsage,
  TenantUpdate,
  Trigger,
  TriggerUpdateRequest,
  UploadResponse,
  WorkspaceUploadResponse,
} from "./apiContracts";
export type {
  ExperienceEntry,
  FocusApiItem,
  OrgDepartmentItem,
} from "./apiContracts";
import {
  AppError,
  parseHttpError,
  parseHttpErrorResponse,
  normalizeUnknownError,
} from "./apiError.ts";
import {
  parseActivityListResponse,
  parseAgentCollaboratorsResponse,
  parseAgentListResponse,
  parseAgentMetricsResponse,
  parseAgentResponse,
  parseAgentTemplatesResponse,
  parseApiKeyResponse,
  parseAuthRegisterResponse,
  parseChannelConfigResponse,
  parseClawhubConfiguredResponse,
  parseClawhubSkillResponse,
  parseClawhubSkillsResponse,
  parseCompanyCreateResponse,
  parseCompanyStatsListResponse,
  parseCompanyStatsResponse,
  parseControlScreenshotResponse,
  parseControlStatusResponse,
  parseControlUnlockResponse,
  parseContentResponse,
  parseConfiguredResponse,
  parseCredentialResponse,
  parseCredentialsResponse,
  parseCreatedAgentResponse,
  parseCurrentUrlResponse,
  parseDeletedResponse,
  parseFileItemsResponse,
  parseFileContentResponse,
  parseFileLockResponse,
  parseFileMutationResponse,
  parseFilePreviewResponse,
  parseFileRevisionsResponse,
  parseFocusListResponse,
  parseFocusResponse,
  parseGatewayMessagesResponse,
  parseInboxResponse,
  parseLlmModelsResponse,
  parseLoginResponse,
  parseOkMessageResponse,
  parseOkResponse,
  parseOnboardingStatusResponse,
  parseOrgDepartmentsResponse,
  parsePersonalAssistantResponse,
  parsePlatformSettingsResponse,
  parseResolvedTenantResponse,
  parseScheduleHistoryResponse,
  parseScheduleResponse,
  parseScheduleRunResponse,
  parseSchedulesResponse,
  parseSkillImportResponse,
  parseSkillDetailResponse,
  parseSkillMutationResult,
  parseSkillsResponse,
  parseSkillUrlPreviewResponse,
  parseSwitchTenantResponse,
  parseTaskLogsResponse,
  parseTaskResponse,
  parseTasksResponse,
  parseTaskTriggerResponse,
  parseTenantChoicesResponse,
  parseTenantResponse,
  parseTenantSetupResponse,
  parseTenantTokenUsageResponse,
  parseTokenResponse,
  parseTriggersResponse,
  parseUploadResponse,
  parseUserResponse,
  parseExperienceListResponse,
  parseExperienceResponse,
  parseExperienceDistillResponse,
  parseExperienceReferencesResponse,
  parseExperienceStatsResponse,
  parseHintResponse,
  parseRegistrationConfigResponse,
  parseSkillSettingsResponse,
  parseUnreadCountResponse,
  parseWebhookUrlResponse,
  parseWorkspaceUploadResponse,
  type ResponseParser,
} from "./apiResponseParsers.ts";

export { ApiError, AppError } from "./apiError.ts";
export type {
  ApiErrorContext,
  AppErrorContext,
  ErrorSource,
} from "./apiError.ts";

const API_BASE = "/api";

function isAuthEndpoint(url: string): boolean {
  return (
    url.startsWith("/auth/login") ||
    url.startsWith("/auth/register") ||
    url.startsWith("/auth/verify-email") ||
    url.startsWith("/auth/resend-verification") ||
    url.startsWith("/auth/forgot-password") ||
    url.startsWith("/auth/reset-password")
  );
}

async function fetchApiResponse(
  url: string,
  options: RequestInit,
): Promise<Response> {
  const token = localStorage.getItem("token");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${url}`, { ...options, headers });
  } catch (error) {
    throw normalizeUnknownError(error, {
      code: "network_error",
      source: "http",
      retryable: true,
    });
  }

  if (response.ok) return response;

  const apiError = await parseHttpErrorResponse(response);
  if (response.status === 401 && !isAuthEndpoint(url)) {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    window.location.href = "/login";
  }
  throw apiError;
}

function fetchJsonImpl<T>(
  url: string,
  options?: RequestInit,
  parser?: undefined,
): Promise<T extends unknown ? unknown : never>;
function fetchJsonImpl<T>(
  url: string,
  options: RequestInit,
  parser: ResponseParser<T>,
): Promise<T>;
async function fetchJsonImpl(
  url: string,
  options: RequestInit = {},
  parser?: ResponseParser<unknown>,
): Promise<unknown> {
  const res = await fetchApiResponse(url, options);

  if (res.status === 204) {
    if (!parser) return undefined;
    throw new AppError({
      message: "API returned no content for a JSON response",
      code: "invalid_api_response",
      source: "http",
      retryable: false,
    });
  }
  if (parser) {
    const value: unknown = await res.json();
    return parser(value);
  }
  return res.json();
}

function request<T>(
  url: string,
  options: RequestInit,
  parser: ResponseParser<T>,
): Promise<T> {
  return fetchJsonImpl(url, options, parser);
}

async function requestVoid(
  url: string,
  options: RequestInit = {},
): Promise<void> {
  const response = await fetchApiResponse(url, options);
  if (response.status !== 204) {
    throw new AppError({
      message: "API returned content where no content was expected",
      code: "invalid_api_response",
      source: "http",
      retryable: false,
    });
  }
}

/** Legacy/Internal generic fetcher */
export const fetchJson = fetchJsonImpl;
export const fetchVoid = requestVoid;

async function uploadFile(
  url: string,
  file: File,
  extraFields?: Record<string, string>,
): Promise<WorkspaceUploadResponse> {
  const token = localStorage.getItem("token");
  const formData = new FormData();
  formData.append("file", file);
  if (extraFields) {
    for (const [k, v] of Object.entries(extraFields)) {
      formData.append(k, v);
    }
  }
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${url}`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: formData,
    });
  } catch (error) {
    throw normalizeUnknownError(error, {
      code: "network_error",
      source: "http",
      retryable: true,
    });
  }
  if (!res.ok) {
    throw await parseHttpErrorResponse(res);
  }
  const value: unknown = await res.json();
  return parseWorkspaceUploadResponse(value);
}

// Upload with progress tracking via XMLHttpRequest.
// Returns { promise, abort } — call abort() to cancel the upload.
// Progress callback: 0-100 = upload phase, 101 = processing phase (server is parsing the file).
export function uploadFileWithProgress(
  url: "/chat/upload",
  file: File,
  onProgress?: (percent: number) => void,
  extraFields?: Record<string, string>,
  timeoutMs?: number,
): { promise: Promise<UploadResponse>; abort: () => void };
export function uploadFileWithProgress(
  url: string,
  file: File,
  onProgress?: (percent: number) => void,
  extraFields?: Record<string, string>,
  timeoutMs?: number,
): { promise: Promise<WorkspaceUploadResponse>; abort: () => void };
export function uploadFileWithProgress<T>(
  url: string,
  file: File,
  onProgress: ((percent: number) => void) | undefined,
  extraFields: Record<string, string> | undefined,
  timeoutMs: number | undefined,
  parser: ResponseParser<T>,
): { promise: Promise<T>; abort: () => void };
export function uploadFileWithProgress(
  url: string,
  file: File,
  onProgress?: (percent: number) => void,
  extraFields?: Record<string, string>,
  timeoutMs?: number,
  parser?: ResponseParser<unknown>,
): { promise: Promise<unknown>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<unknown>((resolve, reject) => {
    const token = localStorage.getItem("token");
    const formData = new FormData();
    formData.append("file", file);
    if (extraFields) {
      for (const [k, v] of Object.entries(extraFields)) {
        formData.append(k, v);
      }
    }
    xhr.open("POST", `${API_BASE}${url}`);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    // Upload phase: 0-100%
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    // Upload bytes finished → enter processing phase
    xhr.upload.onload = () => {
      if (onProgress) onProgress(101); // 101 = "processing" sentinel
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const value: unknown = JSON.parse(xhr.responseText);
          const responseParser =
            parser ??
            (url === "/chat/upload"
              ? parseUploadResponse
              : parseWorkspaceUploadResponse);
          resolve(responseParser(value));
        } catch {
          reject(
            new AppError({
              message: "Upload returned an invalid JSON response",
              code: "invalid_upload_response",
              source: "http",
              retryable: false,
            }),
          );
        }
      } else {
        reject(
          parseHttpError({
            status: xhr.status,
            statusText: xhr.statusText,
            bodyText: xhr.responseText,
            traceId: xhr.getResponseHeader("X-Trace-Id"),
          }),
        );
      }
    };
    xhr.onerror = () =>
      reject(
        new AppError({
          message: "Network error",
          code: "network_error",
          source: "http",
          retryable: true,
        }),
      );
    xhr.ontimeout = () =>
      reject(
        new AppError({
          message: "Upload timed out",
          code: "upload_timeout",
          source: "http",
          retryable: true,
        }),
      );
    xhr.onabort = () =>
      reject(
        new AppError({
          message: "Upload cancelled",
          code: "upload_cancelled",
          source: "http",
          retryable: false,
        }),
      );
    xhr.timeout = timeoutMs ?? 120_000;
    xhr.send(formData);
  });
  return { promise, abort: () => xhr.abort() };
}

// ─── Auth ─────────────────────────────────────────────
export const authApi = {
  register: (data: {
    username?: string;
    email: string;
    password: string;
    display_name: string;
    invitation_code?: string;
    provider?: string;
    provider_code?: string;
  }) =>
    request<{
      user_id: string;
      email: string;
      access_token: string;
      message: string;
      user?: User;
      needs_company_setup: boolean;
    }>(
      "/auth/register",
      { method: "POST", body: JSON.stringify(data) },
      parseAuthRegisterResponse,
    ),

  login: (data: {
    login_identifier: string;
    password: string;
    tenant_id?: string;
  }) =>
    request<
      | TokenResponse
      | {
          requires_tenant_selection: boolean;
          login_identifier: string;
          tenants: OAuthTenantChoice[];
        }
    >(
      "/auth/login",
      { method: "POST", body: JSON.stringify(data) },
      parseLoginResponse,
    ),

  forgotPassword: (data: { email: string }) =>
    request<{ ok: boolean; message: string }>(
      "/auth/forgot-password",
      { method: "POST", body: JSON.stringify(data) },
      parseOkMessageResponse,
    ),

  resetPassword: (data: { token: string; new_password: string }) =>
    request<{ ok: boolean }>(
      "/auth/reset-password",
      { method: "POST", body: JSON.stringify(data) },
      parseOkResponse,
    ),

  emailHint: (username: string) =>
    request<{ hint: string }>(
      `/auth/email-hint?username=${encodeURIComponent(username)}`,
      {},
      parseHintResponse,
    ),

  me: () => request<User>("/auth/me", {}, parseUserResponse),

  updateMe: (data: Partial<User>) =>
    request<User>(
      "/auth/me",
      { method: "PATCH", body: JSON.stringify(data) },
      parseUserResponse,
    ),

  verifyEmail: (token: string) =>
    request<TokenResponse>(
      "/auth/verify-email",
      { method: "POST", body: JSON.stringify({ token }) },
      parseTokenResponse,
    ),

  resendVerification: (email: string) =>
    request<{ ok: boolean; message: string }>(
      "/auth/resend-verification",
      { method: "POST", body: JSON.stringify({ email }) },
      parseOkMessageResponse,
    ),

  getMyTenants: () =>
    request<TenantChoice[]>("/auth/my-tenants", {}, parseTenantChoicesResponse),

  switchTenant: (tenantId: string) =>
    request<{ access_token: string; redirect_url?: string; message?: string }>(
      "/auth/switch-tenant",
      { method: "POST", body: JSON.stringify({ tenant_id: tenantId }) },
      parseSwitchTenantResponse,
    ),
};

// ─── Tenants ──────────────────────────────────────────
export const tenantApi = {
  selfCreate: (data: { name: string }) =>
    request<TenantSetupResponse>(
      "/tenants/self-create",
      { method: "POST", body: JSON.stringify(data) },
      parseTenantSetupResponse,
    ),

  join: (invitationCode: string) =>
    request<TenantSetupResponse>(
      "/tenants/join",
      {
        method: "POST",
        body: JSON.stringify({ invitation_code: invitationCode }),
      },
      parseTenantSetupResponse,
    ),

  registrationConfig: () =>
    request<{ allow_self_create_company: boolean }>(
      "/tenants/registration-config",
      {},
      parseRegistrationConfigResponse,
    ),

  resolveByDomain: (domain: string) =>
    request<ResolvedTenant>(
      `/tenants/resolve-by-domain?domain=${encodeURIComponent(domain)}`,
      {},
      parseResolvedTenantResponse,
    ),

  me: () => request<Tenant>("/tenants/me", {}, parseTenantResponse),

  tokenUsage: () =>
    request<TenantTokenUsage>(
      "/tenants/me/token-usage",
      {},
      parseTenantTokenUsageResponse,
    ),
};

export const onboardingApi = {
  status: () =>
    request<OnboardingStatus>(
      "/onboarding/status",
      {},
      parseOnboardingStatusResponse,
    ),

  start: (entryMode: "create" | "join") =>
    request<OnboardingStatus>(
      "/onboarding/start",
      { method: "POST", body: JSON.stringify({ entry_mode: entryMode }) },
      parseOnboardingStatusResponse,
    ),

  createPersonalAssistant: (data: {
    name: string;
    personality: string;
    work_style: string;
    boundaries?: string;
  }) =>
    request<PersonalAssistantResponse>(
      "/onboarding/personal-assistant",
      { method: "POST", body: JSON.stringify(data) },
      parsePersonalAssistantResponse,
    ),

  complete: () =>
    request<OnboardingStatus>(
      "/onboarding/complete",
      { method: "POST" },
      parseOnboardingStatusResponse,
    ),
};

export const adminApi = {
  listCompanies: () =>
    request<CompanyStats[]>(
      "/admin/companies",
      {},
      parseCompanyStatsListResponse,
    ),

  createCompany: (data: { name: string }) =>
    request<CompanyCreateResponse>(
      "/admin/companies",
      { method: "POST", body: JSON.stringify(data) },
      parseCompanyCreateResponse,
    ),

  updateCompany: (id: string, data: TenantUpdate) =>
    request<Tenant>(
      `/tenants/${id}`,
      { method: "PUT", body: JSON.stringify(data) },
      parseTenantResponse,
    ),

  toggleCompany: (id: string) =>
    request<CompanyStats>(
      `/admin/companies/${id}/toggle`,
      { method: "PUT" },
      parseCompanyStatsResponse,
    ),

  getPlatformSettings: () =>
    request<PlatformSettings>(
      "/admin/platform-settings",
      {},
      parsePlatformSettingsResponse,
    ),

  updatePlatformSettings: (data: Partial<PlatformSettings>) =>
    request<PlatformSettings>(
      "/admin/platform-settings",
      { method: "PUT", body: JSON.stringify(data) },
      parsePlatformSettingsResponse,
    ),
};

// ─── Agents ───────────────────────────────────────────
export const agentApi = {
  list: (tenantId?: string) =>
    request<Agent[]>(
      `/agents/${tenantId ? `?tenant_id=${tenantId}` : ""}`,
      {},
      parseAgentListResponse,
    ),

  get: (id: string) => request<Agent>(`/agents/${id}`, {}, parseAgentResponse),

  create: (data: AgentCreateRequest) =>
    request<CreatedAgent>(
      "/agents/",
      { method: "POST", body: JSON.stringify(data) },
      parseCreatedAgentResponse,
    ),

  update: (id: string, data: Partial<Agent>) =>
    request<Agent>(
      `/agents/${id}`,
      { method: "PATCH", body: JSON.stringify(data) },
      parseAgentResponse,
    ),

  delete: (id: string) => requestVoid(`/agents/${id}`, { method: "DELETE" }),

  start: (id: string) =>
    request<Agent>(
      `/agents/${id}/start`,
      { method: "POST" },
      parseAgentResponse,
    ),

  stop: (id: string) =>
    request<Agent>(
      `/agents/${id}/stop`,
      { method: "POST" },
      parseAgentResponse,
    ),

  metrics: (id: string) =>
    request<AgentMetrics>(
      `/agents/${id}/metrics`,
      {},
      parseAgentMetricsResponse,
    ),

  collaborators: (id: string) =>
    request<AgentCollaborator[]>(
      `/agents/${id}/collaborators`,
      {},
      parseAgentCollaboratorsResponse,
    ),

  templates: () =>
    request<AgentTemplate[]>(
      "/agents/templates",
      {},
      parseAgentTemplatesResponse,
    ),

  // OpenClaw gateway
  generateApiKey: (id: string) =>
    request<{ api_key: string; message: string }>(
      `/agents/${id}/api-key`,
      { method: "POST" },
      parseApiKeyResponse,
    ),

  gatewayMessages: (id: string) =>
    request<GatewayMessage[]>(
      `/agents/${id}/gateway-messages`,
      {},
      parseGatewayMessagesResponse,
    ),
};

// ─── Tasks ────────────────────────────────────────────
export const taskApi = {
  list: (agentId: string, status?: string, type?: string) => {
    const params = new URLSearchParams();
    if (status) params.set("status_filter", status);
    if (type) params.set("type_filter", type);
    return request<Task[]>(
      `/agents/${agentId}/tasks/?${params}`,
      {},
      parseTasksResponse,
    );
  },

  create: (agentId: string, data: TaskCreateRequest) =>
    request<Task>(
      `/agents/${agentId}/tasks/`,
      { method: "POST", body: JSON.stringify(data) },
      parseTaskResponse,
    ),

  update: (agentId: string, taskId: string, data: Partial<Task>) =>
    request<Task>(
      `/agents/${agentId}/tasks/${taskId}`,
      { method: "PATCH", body: JSON.stringify(data) },
      parseTaskResponse,
    ),

  getLogs: (agentId: string, taskId: string) =>
    request<
      { id: string; task_id: string; content: string; created_at: string }[]
    >(`/agents/${agentId}/tasks/${taskId}/logs`, {}, parseTaskLogsResponse),

  trigger: (agentId: string, taskId: string) =>
    request<TaskTriggerResponse>(
      `/agents/${agentId}/tasks/${taskId}/trigger`,
      { method: "POST" },
      parseTaskTriggerResponse,
    ),
};

// ─── Files ────────────────────────────────────────────
export const fileApi = {
  list: (agentId: string, path: string = "") =>
    request<FileItem[]>(
      `/agents/${agentId}/files/?path=${encodeURIComponent(path)}`,
      {},
      parseFileItemsResponse,
    ),

  read: (agentId: string, path: string) =>
    request<{ path: string; content: string }>(
      `/agents/${agentId}/files/content?path=${encodeURIComponent(path)}`,
      {},
      parseFileContentResponse,
    ),

  write: (agentId: string, path: string, content: string) =>
    request<FileMutationResponse>(
      `/agents/${agentId}/files/content?path=${encodeURIComponent(path)}`,
      {
        method: "PUT",
        body: JSON.stringify({ content }),
      },
      parseFileMutationResponse,
    ),

  autosave: (
    agentId: string,
    path: string,
    content: string,
    sessionId?: string | null,
  ) =>
    request<FileMutationResponse>(
      `/agents/${agentId}/files/content?path=${encodeURIComponent(path)}`,
      {
        method: "PUT",
        body: JSON.stringify({
          content,
          autosave: true,
          session_id: sessionId || undefined,
        }),
      },
      parseFileMutationResponse,
    ),

  delete: (agentId: string, path: string) =>
    request<FileMutationResponse>(
      `/agents/${agentId}/files/content?path=${encodeURIComponent(path)}`,
      {
        method: "DELETE",
      },
      parseFileMutationResponse,
    ),

  preview: (agentId: string, path: string) =>
    request<FilePreview>(
      `/agents/${agentId}/files/preview?path=${encodeURIComponent(path)}`,
      {},
      parseFilePreviewResponse,
    ),

  lock: (agentId: string, path: string, sessionId?: string | null) =>
    request<FileLockResponse>(
      `/agents/${agentId}/files/locks`,
      {
        method: "POST",
        body: JSON.stringify({ path, session_id: sessionId || undefined }),
      },
      parseFileLockResponse,
    ),

  unlock: (agentId: string, path: string) =>
    request<FileLockResponse>(
      `/agents/${agentId}/files/locks?path=${encodeURIComponent(path)}`,
      {
        method: "DELETE",
      },
      parseFileLockResponse,
    ),

  revisions: (agentId: string, path: string) =>
    request<FileRevision[]>(
      `/agents/${agentId}/files/revisions?path=${encodeURIComponent(path)}`,
      {},
      parseFileRevisionsResponse,
    ),

  restoreRevision: (agentId: string, revisionId: string) =>
    request<FileMutationResponse>(
      `/agents/${agentId}/files/restore`,
      { method: "POST", body: JSON.stringify({ revision_id: revisionId }) },
      parseFileMutationResponse,
    ),

  upload: (
    agentId: string,
    file: File,
    path: string = "workspace/knowledge_base",
    onProgress?: (pct: number) => void,
  ) =>
    onProgress
      ? uploadFileWithProgress(
          `/agents/${agentId}/files/upload?path=${encodeURIComponent(path)}`,
          file,
          onProgress,
        ).promise
      : uploadFile(
          `/agents/${agentId}/files/upload?path=${encodeURIComponent(path)}`,
          file,
        ),

  importSkill: (agentId: string, skillId: string) =>
    request<FileMutationResponse>(
      `/agents/${agentId}/files/import-skill`,
      { method: "POST", body: JSON.stringify({ skill_id: skillId }) },
      parseFileMutationResponse,
    ),

  downloadUrl: (
    agentId: string,
    path: string,
    options?: { inline?: boolean },
  ) => {
    const token = localStorage.getItem("token");
    const params = new URLSearchParams({ path, token: token || "" });
    if (options?.inline) params.set("inline", "1");
    return `${API_BASE}/agents/${agentId}/files/download?${params.toString()}`;
  },
};

// ─── Focus ───────────────────────────────────────────
export const focusApi = {
  list: (agentId: string, includeCompleted = true) =>
    request<FocusApiItem[]>(
      `/agents/${agentId}/focus/?include_completed=${includeCompleted ? "true" : "false"}`,
      {},
      parseFocusListResponse,
    ),

  upsert: (
    agentId: string,
    data: {
      key?: string;
      title?: string | null;
      description: string;
      status?: FocusApiItem["status"];
      kind?: FocusApiItem["kind"];
      source?: string;
      metadata?: { [key: string]: JsonValue };
    },
  ) =>
    request<FocusApiItem>(
      `/agents/${agentId}/focus/`,
      { method: "POST", body: JSON.stringify(data) },
      parseFocusResponse,
    ),

  complete: (agentId: string, key: string) =>
    request<FocusApiItem>(
      `/agents/${agentId}/focus/${encodeURIComponent(key)}/complete`,
      { method: "POST" },
      parseFocusResponse,
    ),
};

// ─── Channel Config ───────────────────────────────────
export const channelApi = {
  get: (agentId: string) =>
    request<ChannelConfig>(
      `/agents/${agentId}/channel`,
      {},
      parseChannelConfigResponse,
    ),

  create: (agentId: string, data: ChannelConfigRequest) =>
    request<ChannelConfig>(
      `/agents/${agentId}/channel`,
      { method: "POST", body: JSON.stringify(data) },
      parseChannelConfigResponse,
    ),

  update: (agentId: string, data: ChannelConfigRequest) =>
    request<ChannelConfig>(
      `/agents/${agentId}/channel`,
      { method: "PUT", body: JSON.stringify(data) },
      parseChannelConfigResponse,
    ),

  delete: (agentId: string) =>
    requestVoid(`/agents/${agentId}/channel`, { method: "DELETE" }),

  webhookUrl: (agentId: string) =>
    request<{ webhook_url: string }>(
      `/agents/${agentId}/channel/webhook-url`,
      {},
      parseWebhookUrlResponse,
    ),
};

// ─── Enterprise ───────────────────────────────────────
export const enterpriseApi = {
  llmModels: () => {
    const tid = localStorage.getItem("current_tenant_id");
    return request<LlmModel[]>(
      `/enterprise/llm-models${tid ? `?tenant_id=${tid}` : ""}`,
      {},
      parseLlmModelsResponse,
    );
  },

  setDefaultModel: (modelId: string) =>
    requestVoid(`/enterprise/llm-models/${modelId}/set-default`, {
      method: "POST",
    }),
  templates: () =>
    request<AgentTemplate[]>(
      "/agents/templates",
      {},
      parseAgentTemplatesResponse,
    ),

  // Enterprise Knowledge Base
  kbFiles: (path: string = "") =>
    request<FileItem[]>(
      `/enterprise/knowledge-base/files?path=${encodeURIComponent(path)}`,
      {},
      parseFileItemsResponse,
    ),

  kbUpload: (file: File, subPath: string = "") =>
    uploadFile(
      `/enterprise/knowledge-base/upload?sub_path=${encodeURIComponent(subPath)}`,
      file,
    ),

  kbRead: (path: string) =>
    request<{ path: string; content: string }>(
      `/enterprise/knowledge-base/content?path=${encodeURIComponent(path)}`,
      {},
      parseFileContentResponse,
    ),

  kbWrite: (path: string, content: string) =>
    request<FileMutationResponse>(
      `/enterprise/knowledge-base/content?path=${encodeURIComponent(path)}`,
      {
        method: "PUT",
        body: JSON.stringify({ content }),
      },
      parseFileMutationResponse,
    ),

  kbDelete: (path: string) =>
    request<FileMutationResponse>(
      `/enterprise/knowledge-base/content?path=${encodeURIComponent(path)}`,
      {
        method: "DELETE",
      },
      parseFileMutationResponse,
    ),
};

// ─── Activity Logs ────────────────────────────────────
export const activityApi = {
  list: (agentId: string, limit = 50) =>
    request<ActivityItem[]>(
      `/agents/${agentId}/activity?limit=${limit}`,
      {},
      parseActivityListResponse,
    ),
};

// ─── Messages ─────────────────────────────────────────
export const messageApi = {
  inbox: (limit = 50) =>
    request<InboxMessage[]>(
      `/messages/inbox?limit=${limit}`,
      {},
      parseInboxResponse,
    ),

  unreadCount: () =>
    request<{ unread_count: number }>(
      "/messages/unread-count",
      {},
      parseUnreadCountResponse,
    ),

  markRead: (messageId: string) =>
    request<{ ok: boolean }>(
      `/messages/${messageId}/read`,
      { method: "PUT" },
      parseOkResponse,
    ).then(() => undefined),

  markAllRead: () =>
    request<{ ok: boolean }>(
      "/messages/read-all",
      { method: "PUT" },
      parseOkResponse,
    ).then(() => undefined),
};

// ─── Schedules ────────────────────────────────────────
export const scheduleApi = {
  list: (agentId: string) =>
    request<Schedule[]>(
      `/agents/${agentId}/schedules/`,
      {},
      parseSchedulesResponse,
    ),

  create: (agentId: string, data: ScheduleCreateRequest) =>
    request<Schedule>(
      `/agents/${agentId}/schedules/`,
      { method: "POST", body: JSON.stringify(data) },
      parseScheduleResponse,
    ),

  update: (agentId: string, scheduleId: string, data: ScheduleUpdateRequest) =>
    request<Schedule>(
      `/agents/${agentId}/schedules/${scheduleId}`,
      { method: "PATCH", body: JSON.stringify(data) },
      parseScheduleResponse,
    ),

  delete: (agentId: string, scheduleId: string) =>
    requestVoid(`/agents/${agentId}/schedules/${scheduleId}`, {
      method: "DELETE",
    }),

  trigger: (agentId: string, scheduleId: string) =>
    request<ScheduleRunResponse>(
      `/agents/${agentId}/schedules/${scheduleId}/run`,
      {
        method: "POST",
      },
      parseScheduleRunResponse,
    ),

  history: (agentId: string, scheduleId: string) =>
    request<ScheduleHistoryItem[]>(
      `/agents/${agentId}/schedules/${scheduleId}/history`,
      {},
      parseScheduleHistoryResponse,
    ),
};

// ─── Skills ───────────────────────────────────────────
export const skillApi = {
  list: () => request<Skill[]>("/skills/", {}, parseSkillsResponse),
  get: (id: string) =>
    request<SkillDetail>(`/skills/${id}`, {}, parseSkillDetailResponse),
  create: (data: SkillMutationRequest) =>
    request<SkillMutationResult>(
      "/skills/",
      { method: "POST", body: JSON.stringify(data) },
      parseSkillMutationResult,
    ),
  update: (id: string, data: Partial<SkillMutationRequest>) =>
    request<SkillMutationResult>(
      `/skills/${id}`,
      { method: "PUT", body: JSON.stringify(data) },
      parseSkillMutationResult,
    ),
  delete: (id: string) =>
    request<{ ok: boolean }>(
      `/skills/${id}`,
      { method: "DELETE" },
      parseOkResponse,
    ).then(() => undefined),
  // Path-based browse for FileBrowser
  browse: {
    list: (path: string) =>
      request<FileItem[]>(
        `/skills/browse/list?path=${encodeURIComponent(path)}`,
        {},
        parseFileItemsResponse,
      ),
    read: (path: string) =>
      request<{ content: string }>(
        `/skills/browse/read?path=${encodeURIComponent(path)}`,
        {},
        parseContentResponse,
      ),
    write: (path: string, content: string) =>
      request<FileMutationResponse>(
        "/skills/browse/write",
        { method: "PUT", body: JSON.stringify({ path, content }) },
        parseFileMutationResponse,
      ),
    delete: (path: string) =>
      request<FileMutationResponse>(
        `/skills/browse/delete?path=${encodeURIComponent(path)}`,
        {
          method: "DELETE",
        },
        parseFileMutationResponse,
      ),
  },
  // ClawHub marketplace integration
  clawhub: {
    search: (q: string) =>
      request<ClawhubSkill[]>(
        `/skills/clawhub/search?q=${encodeURIComponent(q)}`,
        {},
        parseClawhubSkillsResponse,
      ),
    detail: (slug: string) =>
      request<ClawhubSkill>(
        `/skills/clawhub/detail/${slug}`,
        {},
        parseClawhubSkillResponse,
      ),
    install: (slug: string) =>
      request<SkillImportResult>(
        "/skills/clawhub/install",
        { method: "POST", body: JSON.stringify({ slug }) },
        parseSkillImportResponse,
      ),
  },
  importFromUrl: (url: string) =>
    request<SkillImportResult>(
      "/skills/import-from-url",
      { method: "POST", body: JSON.stringify({ url }) },
      parseSkillImportResponse,
    ),
  previewUrl: (url: string) =>
    request<SkillUrlPreview>(
      "/skills/import-from-url/preview",
      { method: "POST", body: JSON.stringify({ url }) },
      parseSkillUrlPreviewResponse,
    ),
  // Tenant-level settings
  settings: {
    getToken: () =>
      request<{
        configured: boolean;
        source: string;
        masked: string;
        clawhub_configured: boolean;
        clawhub_masked: string;
      }>("/skills/settings/token", {}, parseSkillSettingsResponse),
    setToken: (github_token: string) =>
      request<{ configured: boolean }>(
        "/skills/settings/token",
        { method: "PUT", body: JSON.stringify({ github_token }) },
        parseConfiguredResponse,
      ),
    setClawhubKey: (clawhub_key: string) =>
      request<{ clawhub_configured: boolean }>(
        "/skills/settings/token",
        { method: "PUT", body: JSON.stringify({ clawhub_key }) },
        parseClawhubConfiguredResponse,
      ),
  },
  // Agent-level import (writes to agent workspace)
  agentImport: {
    fromClawhub: (agentId: string, slug: string) =>
      request<SkillImportResult>(
        `/agents/${agentId}/files/import-from-clawhub`,
        {
          method: "POST",
          body: JSON.stringify({ slug }),
        },
        parseSkillImportResponse,
      ),
    fromUrl: (agentId: string, url: string) =>
      request<SkillImportResult>(
        `/agents/${agentId}/files/import-from-url`,
        { method: "POST", body: JSON.stringify({ url }) },
        parseSkillImportResponse,
      ),
  },
};

// ─── Triggers (Aware Engine) ──────────────────────────
export const triggerApi = {
  list: (agentId: string) =>
    request<Trigger[]>(
      `/agents/${agentId}/triggers`,
      {},
      parseTriggersResponse,
    ),

  update: (agentId: string, triggerId: string, data: TriggerUpdateRequest) =>
    request<{ ok: boolean }>(
      `/agents/${agentId}/triggers/${triggerId}`,
      { method: "PATCH", body: JSON.stringify(data) },
      parseOkResponse,
    ),

  delete: (agentId: string, triggerId: string) =>
    request<{ ok: boolean }>(
      `/agents/${agentId}/triggers/${triggerId}`,
      { method: "DELETE" },
      parseOkResponse,
    ).then(() => undefined),
};

// ─── Agent Credentials ────────────────────────────────
export const credentialApi = {
  list: (agentId: string) =>
    request<Credential[]>(
      `/agents/${agentId}/credentials/`,
      {},
      parseCredentialsResponse,
    ),

  create: (agentId: string, data: CredentialMutationRequest) =>
    request<Credential>(
      `/agents/${agentId}/credentials/`,
      { method: "POST", body: JSON.stringify(data) },
      parseCredentialResponse,
    ),

  update: (
    agentId: string,
    credentialId: string,
    data: CredentialMutationRequest,
  ) =>
    request<Credential>(
      `/agents/${agentId}/credentials/${credentialId}`,
      { method: "PUT", body: JSON.stringify(data) },
      parseCredentialResponse,
    ),

  delete: (agentId: string, credentialId: string) =>
    requestVoid(`/agents/${agentId}/credentials/${credentialId}`, {
      method: "DELETE",
    }),
};

// ─── AgentBay Take Control ────────────────────────────
export const controlApi = {
  click: (
    agentId: string,
    data: { session_id: string; x: number; y: number; button?: string },
  ) =>
    request<ControlStatusResponse>(
      `/agents/${agentId}/control/click`,
      { method: "POST", body: JSON.stringify(data) },
      parseControlStatusResponse,
    ),

  type: (agentId: string, data: { session_id: string; text: string }) =>
    request<ControlStatusResponse>(
      `/agents/${agentId}/control/type`,
      { method: "POST", body: JSON.stringify(data) },
      parseControlStatusResponse,
    ),

  pressKeys: (agentId: string, data: { session_id: string; keys: string[] }) =>
    request<ControlStatusResponse>(
      `/agents/${agentId}/control/press_keys`,
      { method: "POST", body: JSON.stringify(data) },
      parseControlStatusResponse,
    ),

  /** Simulate a natural human drag (Bezier curve trajectory) for slider CAPTCHAs. */
  drag: (
    agentId: string,
    data: {
      session_id: string;
      from_x: number;
      from_y: number;
      to_x: number;
      to_y: number;
      duration_ms?: number;
    },
  ) =>
    request<ControlStatusResponse>(
      `/agents/${agentId}/control/drag`,
      { method: "POST", body: JSON.stringify(data) },
      parseControlStatusResponse,
    ),

  /** Get the current active page URL from the browser session (for auto-populating domain). */
  currentUrl: (agentId: string, data: { session_id: string }) =>
    request<{ status: string; url: string }>(
      `/agents/${agentId}/control/current-url`,
      { method: "POST", body: JSON.stringify(data) },
      parseCurrentUrlResponse,
    ),

  screenshot: (agentId: string, data: { session_id: string }) =>
    request<ControlScreenshotResponse>(
      `/agents/${agentId}/control/screenshot`,
      {
        method: "POST",
        body: JSON.stringify(data),
      },
      parseControlScreenshotResponse,
    ),

  lock: (
    agentId: string,
    data: { session_id: string; platform_hint?: string; env_type?: string },
  ) =>
    request<ControlStatusResponse>(
      `/agents/${agentId}/control/lock`,
      { method: "POST", body: JSON.stringify(data) },
      parseControlStatusResponse,
    ),

  unlock: (
    agentId: string,
    data: {
      session_id: string;
      export_cookies?: boolean;
      platform_hint?: string;
    },
  ) =>
    request<ControlUnlockResponse>(
      `/agents/${agentId}/control/unlock`,
      { method: "POST", body: JSON.stringify(data) },
      parseControlUnlockResponse,
    ),
};

// ─── Experience Library ───────────────────────────────
export type ExperienceView = "team" | "mine" | "all";

export const experienceApi = {
  list: (
    params: {
      view?: ExperienceView;
      status?: string;
      tag?: string;
      q?: string;
    } = {},
  ) => {
    const qs = new URLSearchParams();
    if (params.view) qs.set("view", params.view);
    if (params.status) qs.set("status", params.status);
    if (params.tag) qs.set("tag", params.tag);
    if (params.q) qs.set("q", params.q);
    const s = qs.toString();
    return request<ExperienceEntry[]>(
      `/experience/entries${s ? `?${s}` : ""}`,
      {},
      parseExperienceListResponse,
    );
  },
  get: (id: string) =>
    request<ExperienceEntry>(
      `/experience/entries/${id}`,
      {},
      parseExperienceResponse,
    ),
  createDraftFromContent: (data: {
    agent_id: string;
    content: string;
    session_id?: string;
  }) =>
    request<ExperienceEntry>(
      "/experience/drafts",
      { method: "POST", body: JSON.stringify(data) },
      parseExperienceResponse,
    ),
  // Distill chat content into title / body / applicability WITHOUT persisting (human confirms in the editor).
  distill: (data: { agent_id: string; content: string; session_id?: string }) =>
    request<{
      title: string;
      body: string;
      applicability: string;
      tags: string[];
      extracted: boolean;
    }>(
      "/experience/distill",
      { method: "POST", body: JSON.stringify(data) },
      parseExperienceDistillResponse,
    ),
  create: (data: Partial<ExperienceEntry>) =>
    request<ExperienceEntry>(
      "/experience/entries",
      { method: "POST", body: JSON.stringify(data) },
      parseExperienceResponse,
    ),
  createRevision: (id: string, data: Partial<ExperienceEntry>) =>
    request<ExperienceEntry>(
      `/experience/entries/${id}/draft`,
      { method: "POST", body: JSON.stringify(data) },
      parseExperienceResponse,
    ),
  update: (id: string, data: Partial<ExperienceEntry>) =>
    request<ExperienceEntry>(
      `/experience/entries/${id}`,
      { method: "PATCH", body: JSON.stringify(data) },
      parseExperienceResponse,
    ),
  publish: (id: string) =>
    request<ExperienceEntry>(
      `/experience/entries/${id}/publish`,
      { method: "POST" },
      parseExperienceResponse,
    ),
  retire: (id: string) =>
    request<ExperienceEntry>(
      `/experience/entries/${id}/retire`,
      { method: "POST" },
      parseExperienceResponse,
    ),
  remove: (id: string) =>
    request<{ deleted: boolean }>(
      `/experience/entries/${id}`,
      { method: "DELETE" },
      parseDeletedResponse,
    ),
  review: (id: string) =>
    request<ExperienceEntry>(
      `/experience/entries/${id}/review`,
      { method: "POST" },
      parseExperienceResponse,
    ),
  references: (id: string) =>
    request<{ entry_id: string; read_count: number; cited_count: number }>(
      `/experience/entries/${id}/references`,
      {},
      parseExperienceReferencesResponse,
    ),
  stats: () =>
    request<{
      total: number;
      today: number;
      cited: number;
      top_contributors: { name: string; count: number }[];
    }>("/experience/stats", {}, parseExperienceStatsResponse),
};

// ─── Org structure (synced from Feishu/DingTalk/WeCom; empty until org sync runs) ───
export const orgApi = {
  departments: () =>
    request<{ items: OrgDepartmentItem[]; total_member: number }>(
      "/enterprise/org/departments",
      {},
      parseOrgDepartmentsResponse,
    ),
};
