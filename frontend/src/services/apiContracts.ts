import type { Agent } from "../types";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue =
  JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export interface TenantChoice {
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  logo_url: string | null;
}

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  im_provider: string;
  timezone: string;
  country_region: string;
  is_active: boolean;
  sso_enabled: boolean;
  sso_domain: string | null;
  a2a_async_enabled: boolean;
  default_model_id: string | null;
  logo_url: string | null;
  created_at: string | null;
}

export interface TenantSetupResponse {
  tenant: Tenant;
  access_token: string | null;
  role?: "platform_admin" | "org_admin" | "agent_admin" | "member";
}

export interface ResolvedTenant {
  id: string;
  name: string;
  slug: string;
  sso_enabled: boolean;
  sso_domain: string | null;
  is_active: boolean;
}

export interface TokenUsageBucket {
  total_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  cache_hit_rate: number;
}

export interface TenantTokenUsage {
  today: TokenUsageBucket;
  month: TokenUsageBucket;
  total: TokenUsageBucket;
}

export interface OnboardingStatus {
  exists: boolean;
  status: "not_started" | "in_progress" | "completed";
  current_step: string;
  entry_mode: "create" | "join" | null;
  personal_assistant_agent_id: string | null;
  completed_at: string | null;
}

export interface PersonalAssistantResponse {
  agent: Pick<Agent, "id" | "name">;
  onboarding: OnboardingStatus;
}

export interface CompanyStats {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  sso_enabled: boolean;
  sso_domain: string | null;
  created_at: string | null;
  user_count: number;
  agent_count: number;
  agent_running_count: number;
  total_tokens: number;
  cache_read_tokens_total: number;
  org_admin_email: string | null;
}

export interface CompanyCreateResponse {
  company: CompanyStats;
  admin_invitation_code: string;
}

export interface TenantUpdate {
  name?: string;
  im_provider?: string;
  timezone?: string;
  country_region?: string;
  is_active?: boolean;
  sso_enabled?: boolean;
  sso_domain?: string | null;
  a2a_async_enabled?: boolean;
}

export interface PlatformSettings {
  allow_self_create_company: boolean;
  invitation_code_enabled: boolean;
  sso_custom_domain_redirect_enabled: boolean;
}

export interface AgentCreateRequest {
  name: string;
  role_description?: string;
  bio?: string;
  personality?: string;
  boundaries?: string;
  primary_model_id?: string | null;
  fallback_model_id?: string | null;
  permission_scope_type?: "company" | "user" | "custom";
  permission_scope_ids?: string[];
  permission_access_level?: "use" | "manage";
  max_tokens_per_day?: number | null;
  max_tokens_per_month?: number | null;
  skill_ids?: string[];
  tenant_id?: string;
  template_id?: string;
  agent_type?: "native" | "openclaw";
  openclaw_instruction?: string;
}

export type CreatedAgent = Agent & { api_key?: string };

export interface AgentMetrics {
  agent_id: string;
  agent_name: string;
  status: string;
  container: string;
  tokens: {
    used_today: number;
    used_month: number;
    used_total: number;
    cache_read_today: number;
    cache_read_month: number;
    cache_read_total: number;
    cache_creation_today: number;
    cache_creation_month: number;
    cache_creation_total: number;
  };
  tasks: {
    total: number;
    done: number;
    pending: number;
    completion_rate: number;
  };
  approvals: { total: number; pending: number };
  activity: { actions_last_24h: number };
}

export interface AgentCollaborator {
  id: string;
  name: string;
  role: string;
  status: string;
}

export interface AgentTemplate {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  is_builtin: boolean;
  capability_bullets?: string[];
}

export interface GatewayMessage {
  id: string;
  sender_agent_name: string | null;
  content: string;
  status: string;
  result: JsonValue;
  created_at: string | null;
  delivered_at: string | null;
  completed_at: string | null;
}

export interface TaskCreateRequest {
  title: string;
  description?: string;
  type?: "todo" | "supervision";
  status?: "pending" | "doing" | "done" | "paused";
  priority?: "low" | "medium" | "high" | "urgent";
  assignee?: string;
  due_date?: string | null;
  supervision_target_name?: string;
  supervision_channel?: string;
  remind_schedule?: string;
}

export interface TaskTriggerResponse {
  status: string;
  task_id: string;
}

export interface FileItem {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number;
}

export interface FilePreview {
  path?: string;
  name?: string;
  content?: string;
  type?: string;
  kind?: string;
  content_hash?: string;
  sheets?: Array<{ name?: string; rows: string[][] }>;
}

export interface FileLockResponse {
  status: string;
  path?: string;
  locked_by?: string | null;
}

export interface FileRevision {
  id: string;
  path: string;
  created_at: string;
  created_by?: string | null;
  size?: number;
}

export interface FileMutationResponse {
  status: string;
  path?: string;
  revision_id?: string;
}

export interface UploadResponse {
  path?: string;
  size?: number;
  version_token?: string;
  modified_at?: string | null;
  revision_id?: string | null;
  filename: string;
  saved_filename?: string;
  extracted_text: string;
  workspace_path: string;
  image_data_url: string;
}

export interface WorkspaceUploadResponse {
  status: string;
  path: string;
  url: string;
  filename: string;
  size: number;
  extracted_text_path: string | null;
}

export interface GroupWorkspaceUploadResponse {
  path: string;
  size: number;
  version_token: string;
  modified_at?: string | null;
  revision_id?: string | null;
}

export interface ChannelConfigRequest {
  channel_type: string;
  app_id?: string;
  app_secret?: string;
  encrypt_key?: string;
  verification_token?: string;
  extra_config?: { [key: string]: JsonValue | undefined };
}

export interface ChannelConfig {
  id: string;
  agent_id: string;
  channel_type: string;
  app_id: string | null;
  is_configured: boolean;
  is_connected: boolean;
  last_tested_at: string | null;
  extra_config: { [key: string]: JsonValue } | null;
  created_at: string;
}

export interface LlmModel {
  id: string;
  provider: string;
  model: string;
  base_url: string | null;
  label: string;
  temperature: number | null;
  api_key_masked: string;
  max_tokens_per_day: number | null;
  enabled: boolean;
  supports_vision: boolean;
  supports_tool_calling: boolean | null;
  tool_calling_capability_source: string | null;
  tool_calling_checked_at: string | null;
  tool_calling_error: string | null;
  max_output_tokens: number | null;
  request_timeout: number | null;
  created_at: string;
  deleted_at: string | null;
}

export interface ActivityItem {
  id: string;
  action_type: string;
  summary: string;
  detail: JsonValue;
  related_id: string | null;
  created_at: string | null;
}

export interface InboxMessage {
  id: string;
  sender_type: "agent";
  sender_name: string;
  content: string;
  session_title: string | null;
  created_at: string | null;
  read_at?: string | null;
}

export interface Schedule {
  id: string;
  agent_id: string;
  name: string;
  instruction: string;
  cron_expr: string;
  is_enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  run_count: number;
  created_by: string | null;
  creator_username: string | null;
  created_at: string | null;
  delivery_target_id: string | null;
}

export interface ScheduleCreateRequest {
  name: string;
  instruction: string;
  cron_expr: string;
  delivery_target_id?: string | null;
}

export type ScheduleUpdateRequest = Partial<
  Pick<
    Schedule,
    "name" | "instruction" | "cron_expr" | "is_enabled" | "delivery_target_id"
  >
>;

export interface ScheduleRunResponse {
  status: string;
  schedule_id: string;
  run_id: string;
}

export interface ScheduleHistoryItem {
  id: string;
  created_at: string | null;
  summary: string;
  instruction: string;
  reply: string;
}

export interface Skill {
  id: string;
  name: string;
  description?: string | null;
  icon?: string | null;
  folder_name: string;
  is_default: boolean;
  created_at?: string | null;
}

export interface SkillDetail {
  id: string;
  name: string;
  description: string | null;
  category: string;
  icon: string | null;
  folder_name: string;
  is_builtin: boolean;
  files: Array<{ path: string; content: string }>;
}

export interface SkillMutationResult {
  id: string;
  name: string;
}

export interface SkillMutationRequest {
  name: string;
  description?: string;
  icon?: string;
  folder_name?: string;
  is_default?: boolean;
}

export interface ClawhubSkill {
  slug: string;
  name: string;
  description?: string;
  author?: string;
  tier?: number;
}

export interface SkillImportResult {
  name: string;
  file_count: number;
  tier?: number;
  path?: string;
  files_written?: number;
}

export interface SkillUrlPreview {
  name: string;
  description?: string;
  file_count: number;
  tier?: number;
  files?: string[];
}

export interface TriggerConfig {
  expr?: string;
  minutes?: number;
  at?: string;
  url?: string;
  token?: string;
  from_agent_name?: string;
  from_user_name?: string;
}

export interface Trigger {
  id: string;
  name: string;
  type: string;
  config: TriggerConfig;
  reason: string;
  focus_ref: string | null;
  is_enabled: boolean;
  is_system: boolean;
  fire_count: number;
  max_fires: number | null;
  cooldown_seconds: number;
  last_fired_at: string | null;
  created_at: string | null;
  expires_at: string | null;
  delivery_target_id: string | null;
}

export interface TriggerUpdateRequest {
  config?: TriggerConfig;
  reason?: string;
  is_enabled?: boolean;
  max_fires?: number;
  cooldown_seconds?: number;
  expires_at?: string;
  delivery_target_id?: string | null;
}

export interface Credential {
  id: string;
  agent_id: string;
  credential_type: string;
  platform: string;
  display_name: string;
  status: string;
  cookies_updated_at: string | null;
  last_login_at: string | null;
  last_injected_at: string | null;
  has_cookies: boolean;
  created_at: string;
  updated_at: string;
}

export interface CredentialMutationRequest {
  credential_type?: string;
  platform?: string;
  display_name?: string;
  cookies_json?: string;
  status?: string;
}

export interface ControlStatusResponse {
  status: string;
  detail?: string;
  message?: string;
  success?: boolean;
}

export interface ControlScreenshotResponse extends ControlStatusResponse {
  screenshot?: string;
  screen_size?: { width: number; height: number };
  width?: number;
  height?: number;
}

export interface ControlUnlockResponse extends ControlStatusResponse {
  cookies?: JsonValue[];
  cookies_exported?: boolean;
  cookie_count?: number;
}

export interface FocusApiItem {
  id: string;
  agent_id: string;
  key: string;
  title?: string | null;
  description: string;
  status: "in_progress" | "completed";
  kind: "normal" | "system";
  source: string;
  metadata?: { [key: string]: JsonValue };
  sort_order: number;
  completed_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ExperienceEntry {
  id: string;
  draft_of_id: string | null;
  tenant_id: string | null;
  title: string;
  body: string;
  applicability: string;
  status: "draft" | "published" | "retired";
  tags: string[];
  visibility_scope: "company" | "department" | "user";
  visibility_scope_id: string | null;
  origin: "chat" | "legacy_plaza";
  origin_session_id: string | null;
  origin_agent_id: string | null;
  created_by: string;
  reviewed_by: string | null;
  last_reviewed_at: string | null;
  retired_at: string | null;
  created_at: string;
  updated_at: string | null;
  created_by_name?: string | null;
  origin_agent_name?: string | null;
  can_manage?: boolean | null;
}

export interface OrgDepartmentItem {
  id: string;
  name: string;
  path?: string;
  parent_id?: string | null;
  member_count?: number;
}
