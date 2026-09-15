import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { AppError } from "../src/services/apiError.ts";
import {
  parseActivityListResponse,
  parseAgentListResponse,
  parseChannelConfigResponse,
  parseControlScreenshotResponse,
  parseCredentialsResponse,
  parseExperienceListResponse,
  parseFocusListResponse,
  parseInboxResponse,
  parseLlmModelsResponse,
  parseOnboardingStatusResponse,
  parseOrgDepartmentsResponse,
  parseSchedulesResponse,
  parseSkillsResponse,
  parseTasksResponse,
  parseTenantSetupResponse,
  parseTriggersResponse,
  parseUploadResponse,
  parseUserResponse,
} from "../src/services/apiResponseParsers.ts";
import { parseGroupsResponse } from "../src/services/groupApiResponseParsers.ts";

const tenant = {
  id: "tenant-1",
  name: "Acme",
  slug: "acme",
  im_provider: "web_only",
  timezone: "Asia/Shanghai",
  country_region: "001",
  is_active: true,
  sso_enabled: false,
  sso_domain: null,
  a2a_async_enabled: true,
  default_model_id: null,
  logo_url: null,
  created_at: null,
};

test("tenant setup parser accepts the documented success contract", () => {
  assert.deepEqual(
    parseTenantSetupResponse({
      tenant,
      access_token: "token-1",
      role: "org_admin",
    }),
    { tenant, access_token: "token-1", role: "org_admin" },
  );
});

test("tenant setup parser rejects malformed successful payloads", () => {
  assert.throws(
    () => parseTenantSetupResponse({ tenant, access_token: 42 }),
    (error) =>
      error instanceof AppError && error.code === "invalid_api_response",
  );
});

test("upload and control parsers validate fields consumed by the UI", () => {
  const upload = parseUploadResponse({
    filename: "brief.pdf",
    extracted_text: "brief",
    workspace_path: "workspace/uploads/brief.pdf",
    image_data_url: "",
  });
  assert.equal(upload.filename, "brief.pdf");

  assert.deepEqual(
    parseControlScreenshotResponse({
      status: "ok",
      screenshot: "data:image/png;base64,AA==",
      screen_size: { width: 1440, height: 900 },
    }).screen_size,
    { width: 1440, height: 900 },
  );

  assert.throws(
    () =>
      parseControlScreenshotResponse({
        status: "ok",
        screenshot: "data:image/png;base64,AA==",
        screen_size: { width: "1440", height: 900 },
      }),
    (error) =>
      error instanceof AppError && error.code === "invalid_api_response",
  );
});

test("request reads a successful JSON response body only once", () => {
  const source = readFileSync(
    new URL("../src/services/api.ts", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(
    source,
    /const value: unknown = await res\.json\(\);[\s\S]{0,120}return parser \? parser\(value\) : res\.json\(\)/,
  );
});

test("remaining service contract families accept valid data and reject malformed success", () => {
  const cases = [
    {
      parse: parseOnboardingStatusResponse,
      valid: {
        exists: true,
        status: "in_progress",
        current_step: "assistant",
        entry_mode: "create",
        personal_assistant_agent_id: null,
        completed_at: null,
      },
      breakPayload: (payload) => ({ ...payload, exists: "yes" }),
    },
    {
      parse: parseTasksResponse,
      valid: [
        {
          id: "task-1",
          agent_id: "agent-1",
          title: "Ship",
          type: "todo",
          status: "pending",
          priority: "high",
          assignee: "user-1",
          created_by: "user-1",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      breakPayload: (payload) => [{ ...payload[0], priority: "maybe" }],
    },
    {
      parse: parseFocusListResponse,
      valid: [
        {
          id: "focus-1",
          agent_id: "agent-1",
          key: "launch",
          description: "Launch",
          status: "in_progress",
          kind: "normal",
          source: "user",
          metadata: { score: 1 },
          sort_order: 1,
        },
      ],
      breakPayload: (payload) => [{ ...payload[0], sort_order: "first" }],
    },
    {
      parse: parseChannelConfigResponse,
      valid: {
        id: "channel-1",
        agent_id: "agent-1",
        channel_type: "feishu",
        app_id: null,
        is_configured: true,
        is_connected: false,
        last_tested_at: null,
        extra_config: { connection_mode: "websocket" },
        created_at: "2026-01-01T00:00:00Z",
      },
      breakPayload: (payload) => ({ ...payload, is_connected: 1 }),
    },
    {
      parse: parseLlmModelsResponse,
      valid: [
        {
          id: "model-1",
          provider: "openai",
          model: "gpt",
          base_url: null,
          label: "GPT",
          temperature: null,
          api_key_masked: "***",
          max_tokens_per_day: null,
          enabled: true,
          supports_vision: true,
          supports_tool_calling: null,
          tool_calling_capability_source: null,
          tool_calling_checked_at: null,
          tool_calling_error: null,
          max_output_tokens: null,
          request_timeout: null,
          created_at: "2026-01-01T00:00:00Z",
          deleted_at: null,
        },
      ],
      breakPayload: (payload) => [{ ...payload[0], enabled: "true" }],
    },
    {
      parse: parseActivityListResponse,
      valid: [
        {
          id: "activity-1",
          action_type: "write",
          summary: "Wrote file",
          detail: {},
          related_id: null,
          created_at: null,
        },
      ],
      breakPayload: (payload) => [{ ...payload[0], detail: undefined }],
    },
    {
      parse: parseInboxResponse,
      valid: [
        {
          id: "message-1",
          sender_type: "agent",
          sender_name: "Clawiee",
          content: "Hello",
          session_title: null,
          created_at: null,
        },
      ],
      breakPayload: (payload) => [{ ...payload[0], sender_type: "user" }],
    },
    {
      parse: parseSchedulesResponse,
      valid: [
        {
          id: "schedule-1",
          agent_id: "agent-1",
          name: "Daily",
          instruction: "Report",
          cron_expr: "0 9 * * *",
          is_enabled: true,
          last_run_at: null,
          next_run_at: null,
          run_count: 0,
          created_by: null,
          creator_username: null,
          created_at: null,
          delivery_target_id: null,
        },
      ],
      breakPayload: (payload) => [{ ...payload[0], run_count: "zero" }],
    },
    {
      parse: parseSkillsResponse,
      valid: [
        {
          id: "skill-1",
          name: "Research",
          folder_name: "research",
          is_default: false,
        },
      ],
      breakPayload: (payload) => [{ ...payload[0], is_default: "false" }],
    },
    {
      parse: parseTriggersResponse,
      valid: [
        {
          id: "trigger-1",
          name: "daily",
          type: "cron",
          config: { expr: "0 9 * * *" },
          reason: "Daily",
          focus_ref: null,
          is_enabled: true,
          is_system: false,
          fire_count: 0,
          max_fires: null,
          cooldown_seconds: 0,
          last_fired_at: null,
          created_at: null,
          expires_at: null,
          delivery_target_id: null,
        },
      ],
      breakPayload: (payload) => [{ ...payload[0], fire_count: "zero" }],
    },
    {
      parse: parseCredentialsResponse,
      valid: [
        {
          id: "credential-1",
          agent_id: "agent-1",
          credential_type: "website",
          platform: "example.com",
          display_name: "Example",
          status: "active",
          cookies_updated_at: null,
          last_login_at: null,
          last_injected_at: null,
          has_cookies: true,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      breakPayload: (payload) => [{ ...payload[0], has_cookies: 1 }],
    },
    {
      parse: parseExperienceListResponse,
      valid: [
        {
          id: "experience-1",
          draft_of_id: null,
          tenant_id: null,
          title: "Lesson",
          body: "Body",
          applicability: "When useful",
          status: "published",
          tags: [],
          visibility_scope: "company",
          visibility_scope_id: null,
          origin: "chat",
          origin_session_id: null,
          origin_agent_id: null,
          created_by: "user-1",
          reviewed_by: null,
          last_reviewed_at: null,
          retired_at: null,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: null,
        },
      ],
      breakPayload: (payload) => [{ ...payload[0], status: "active" }],
    },
    {
      parse: parseOrgDepartmentsResponse,
      valid: { items: [{ id: "department-1", name: "R&D" }], total_member: 1 },
      breakPayload: (payload) => ({ ...payload, total_member: "one" }),
    },
    {
      parse: parseGroupsResponse,
      valid: [
        {
          id: "group-1",
          tenant_id: "tenant-1",
          name: "Launch",
          description: null,
          created_by_participant_id: "participant-1",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      breakPayload: (payload) => [{ ...payload[0], description: 42 }],
    },
  ];

  cases.forEach(({ parse, valid, breakPayload }) => {
    assert.doesNotThrow(() => parse(valid));
    assert.throws(
      () => parse(breakPayload(valid)),
      (error) =>
        error instanceof AppError && error.code === "invalid_api_response",
    );
  });
});

test("user and agent parsers normalize documented backend nulls", () => {
  assert.deepEqual(
    parseUserResponse({
      id: "user-1",
      username: null,
      email: null,
      display_name: "Mobile User",
      avatar_url: null,
      role: "member",
      is_platform_admin: false,
      tenant_id: null,
      title: null,
      is_active: true,
      email_verified: true,
      created_at: "2026-01-01T00:00:00Z",
    }),
    {
      id: "user-1",
      username: "",
      email: "",
      display_name: "Mobile User",
      role: "member",
      is_platform_admin: false,
      is_active: true,
      email_verified: true,
      created_at: "2026-01-01T00:00:00Z",
    },
  );

  const [agent] = parseAgentListResponse([
    {
      id: "agent-1",
      name: "Paused agent",
      avatar_url: null,
      role_description: "Assistant",
      bio: null,
      status: "paused",
      creator_id: "user-1",
      primary_model_id: null,
      fallback_model_id: null,
      autonomy_policy: {},
      tokens_used_today: 0,
      tokens_used_month: 0,
      tokens_used_total: 0,
      cache_read_tokens_today: 0,
      cache_read_tokens_month: 0,
      cache_read_tokens_total: 0,
      cache_creation_tokens_today: 0,
      cache_creation_tokens_month: 0,
      cache_creation_tokens_total: 0,
      max_tokens_per_day: null,
      max_tokens_per_month: null,
      context_window_size: 100,
      heartbeat_enabled: true,
      heartbeat_interval_minutes: 240,
      heartbeat_active_hours: "09:00-18:00",
      last_heartbeat_at: null,
      timezone: null,
      agent_type: "native",
      openclaw_last_seen: null,
      unread_count: 0,
      onboarded_for_me: true,
      created_at: "2026-01-01T00:00:00Z",
      last_active_at: null,
    },
  ]);
  assert.equal(agent.status, "paused");
  assert.equal(agent.avatar_url, undefined);
  assert.equal(agent.max_tokens_per_day, undefined);
});
