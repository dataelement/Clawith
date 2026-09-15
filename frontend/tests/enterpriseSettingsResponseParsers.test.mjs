import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { AppError } from "../src/services/apiError.ts";
import {
  parseAuthorizationUrl,
  parseClawhubSearchResults,
  parseConnectivityTestResult,
  parseDailyCollectionResult,
  parseEnterpriseTenant,
  parseIdentityProviders,
  parseLlmModels,
  parseLlmProviders,
  parseMembersWithoutOkr,
  parseOkrSettings,
  parseOrgDepartments,
  parseOrgMembers,
  parseOrgSyncResponse,
  parseRuntimeModelSettings,
  parseTenantDefaultModel,
  parseTenantTimezone,
  parseUrlSkillPreview,
} from "../src/pages/enterprise-settings/utils/responseParsers.ts";

const tenant = {
  id: "tenant-1",
  name: "Acme",
  slug: "acme",
  im_provider: "web_only",
  timezone: "Asia/Shanghai",
  country_region: "CN",
  is_active: true,
  sso_enabled: false,
  sso_domain: null,
  a2a_async_enabled: true,
  default_model_id: null,
  logo_url: null,
  created_at: null,
};

const invalidResponse = (fn) =>
  assert.throws(
    fn,
    (error) =>
      error instanceof AppError && error.code === "invalid_api_response",
  );

test("enterprise settings fetchJson calls keep successful payloads unknown until parsed or discarded", () => {
  const files = [
    "../src/pages/enterprise-settings/tabs/OrgTab.tsx",
    "../src/pages/enterprise-settings/tabs/OkrTab.tsx",
    "../src/pages/enterprise-settings/tabs/LlmTab.tsx",
    "../src/pages/enterprise-settings/tabs/SkillsTab.tsx",
    "../src/pages/enterprise-settings/components/CompanyInfoEditors.tsx",
  ];
  for (const file of files) {
    const source = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.doesNotMatch(source, /fetchJson<(?!unknown>)/);
    assert.doesNotMatch(source, /fetchJson\s*\(/);
  }
  const companyEditors = readFileSync(
    new URL(
      "../src/pages/enterprise-settings/components/CompanyInfoEditors.tsx",
      import.meta.url,
    ),
    "utf8",
  );
  assert.match(companyEditors, /parseEnterpriseTenant\(await res\.json\(\)\)/);
});

test("enterprise tenant parsers accept complete tenants and reject malformed fields", () => {
  assert.equal(parseEnterpriseTenant(tenant).name, "Acme");
  assert.deepEqual(parseTenantTimezone(tenant), { timezone: "Asia/Shanghai" });
  invalidResponse(() => parseEnterpriseTenant({ ...tenant, timezone: 8 }));
});

test("enterprise identity and org parsers accept endpoint contracts", () => {
  assert.equal(
    parseIdentityProviders([
      {
        id: "provider-1",
        provider_type: "feishu",
        name: "Feishu",
        config: { app_id: "app-1" },
        sso_login_enabled: true,
        sso_domain: null,
      },
    ])[0].app_id,
    "app-1",
  );
  assert.equal(
    parseOrgDepartments({
      items: [{ id: "department-1", name: "Engineering", parent_id: null }],
      total_member: 2,
    }).items[0].name,
    "Engineering",
  );
  assert.equal(
    parseOrgMembers([{ id: "member-1", name: "Ada", title: null }])[0].name,
    "Ada",
  );
  assert.deepEqual(
    parseOrgSyncResponse({ departments: 1, members: 2, errors: [] }),
    {
      departments: 1,
      members: 2,
      errors: [],
    },
  );
  assert.deepEqual(parseOrgSyncResponse({ error: "provider unavailable" }), {
    error: "provider unavailable",
  });
  assert.equal(
    parseAuthorizationUrl({ authorization_url: "https://example.test/auth" }),
    "https://example.test/auth",
  );
});

test("enterprise identity and org parsers reject malformed successes", () => {
  invalidResponse(() =>
    parseIdentityProviders([
      {
        id: "provider-1",
        provider_type: "feishu",
        name: "Feishu",
        config: { app_id: 2 },
      },
    ]),
  );
  invalidResponse(() => parseOrgDepartments({ items: [], total_member: "2" }));
  invalidResponse(() => parseOrgMembers([{ id: "member-1", name: 7 }]));
  invalidResponse(() => parseOrgSyncResponse({ errors: [false] }));
  invalidResponse(() => parseOrgSyncResponse({}));
  invalidResponse(() => parseAuthorizationUrl({ authorization_url: 9 }));
});

const okrSettings = {
  enabled: true,
  first_enabled_at: null,
  daily_report_enabled: true,
  daily_report_time: "18:00",
  daily_report_skip_non_workdays: true,
  weekly_report_enabled: false,
  weekly_report_day: 0,
  period_frequency: "quarterly",
  period_length_days: null,
  period_frequency_locked: false,
  okr_agent_id: "agent-1",
};

test("enterprise OKR parsers accept endpoint contracts and reject malformed fields", () => {
  assert.equal(parseOkrSettings(okrSettings).period_frequency, "quarterly");
  assert.equal(
    parseDailyCollectionResult({ message: "started" }).message,
    "started",
  );
  assert.equal(
    parseMembersWithoutOkr({ okr_agent_id: null, company_okr_exists: false })
      .company_okr_exists,
    false,
  );
  invalidResponse(() => parseOkrSettings({ ...okrSettings, enabled: "yes" }));
  invalidResponse(() => parseDailyCollectionResult({ message: 2 }));
  invalidResponse(() => parseMembersWithoutOkr({ company_okr_exists: "no" }));
});

const llmModel = {
  id: "model-1",
  provider: "openai",
  model: "gpt-test",
  label: "Test",
  enabled: true,
  created_at: "2026-08-26T00:00:00Z",
};

test("enterprise LLM parsers accept endpoint contracts", () => {
  assert.equal(parseLlmModels([llmModel])[0].id, "model-1");
  assert.equal(
    parseLlmProviders([
      {
        provider: "openai",
        display_name: "OpenAI",
        protocol: "openai_compatible",
        default_base_url: null,
        supports_tool_choice: true,
        default_max_tokens: 4096,
      },
    ])[0].default_max_tokens,
    4096,
  );
  assert.equal(
    parseRuntimeModelSettings({
      tenant_id: "tenant-1",
      planning_model_id: "model-1",
      compact_model_id: null,
      planning_source: "database",
      compact_source: "unavailable",
      candidates: [llmModel],
    }).candidates[0].model,
    "gpt-test",
  );
  assert.deepEqual(parseTenantDefaultModel({ default_model_id: null }), {
    default_model_id: null,
  });
  assert.equal(
    parseConnectivityTestResult({ connection_success: true })
      .connection_success,
    true,
  );
});

test("enterprise LLM parsers reject malformed successes", () => {
  invalidResponse(() => parseLlmModels([{ ...llmModel, enabled: 1 }]));
  invalidResponse(() => parseLlmProviders([{ provider: "openai" }]));
  invalidResponse(() =>
    parseRuntimeModelSettings({
      tenant_id: "tenant-1",
      planning_model_id: null,
      compact_model_id: null,
      planning_source: "fallback",
      compact_source: "unavailable",
      candidates: [],
    }),
  );
  invalidResponse(() => parseTenantDefaultModel({ default_model_id: 5 }));
  invalidResponse(() =>
    parseConnectivityTestResult({ connection_success: "yes" }),
  );
});

test("enterprise Skill parsers accept endpoint contracts and reject malformed fields", () => {
  assert.equal(
    parseClawhubSearchResults([
      { slug: "search", displayName: "Search", summary: null, updatedAt: 1 },
    ])[0].displayName,
    "Search",
  );
  assert.equal(
    parseUrlSkillPreview({
      name: "Skill",
      description: "Description",
      tier: 1,
      files: [{ path: "SKILL.md", size: 10 }],
      total_size: 10,
      has_scripts: false,
    }).files[0].path,
    "SKILL.md",
  );
  invalidResponse(() =>
    parseClawhubSearchResults([{ slug: "search", displayName: 3 }]),
  );
  invalidResponse(() =>
    parseUrlSkillPreview({
      name: "Skill",
      tier: 1,
      files: [{ path: "SKILL.md", size: "10" }],
      total_size: 10,
      has_scripts: false,
    }),
  );
});
