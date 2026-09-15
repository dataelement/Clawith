import assert from "node:assert/strict";
import test from "node:test";

import {
  parseApprovalList,
  parseAuditLogList,
  parseCompanyIntroSetting,
  parseCreatedMcpTool,
  parseDashboardOkrObjectives,
  parseDashboardOkrPeriods,
  parseEnterpriseStats,
  parseEnterpriseToolList,
  parseInviteUsersResult,
  parseInvitationCodeCreate,
  parseInvitationCodeDeactivate,
  parseInvitationCodePage,
  parseMcpTestResult,
  parseNotificationItems,
  parsePublicNotificationBar,
  parseEmailExists,
  parseApiKey,
  parseAgentPermissions,
  parseVersion,
  parseSsoProviders,
  parseSsoSessionStatus,
  parseTenantDeleteResult,
  parseTenantQuotas,
  parseUnreadCount,
  parseUserManagementUsers,
} from "../src/services/directPageResponseParsers.ts";

test("SSO and notification parsers reject malformed successes", () => {
  assert.throws(() => parseSsoProviders([{ provider_type: 1 }]));
  assert.throws(() => parseSsoSessionStatus({ access_token: 1 }));
  assert.throws(() => parseUnreadCount({ unread_count: "1" }));
  assert.throws(() => parseNotificationItems([{ id: "n" }]));
});

test("user and OKR parsers reject malformed successes", () => {
  assert.throws(() => parseUserManagementUsers([{ id: "u" }]));
  assert.throws(() => parseInviteUsersResult({ invited: "1" }));
  assert.throws(() => parseDashboardOkrPeriods([{ start: 1 }]));
  assert.throws(() =>
    parseDashboardOkrObjectives([{ key_results: [{ status: 1 }] }]),
  );
});

test("enterprise parsers reject malformed successes", () => {
  assert.throws(() => parseTenantQuotas({ default_max_agents: "2" }));
  assert.throws(() => parseCompanyIntroSetting({ value: { content: 1 } }));
  assert.throws(() => parseEnterpriseStats({ total_users: "1" }));
  assert.throws(() => parseApprovalList([{ id: "a" }]));
  assert.throws(() => parseAuditLogList([{ id: "a" }]));
  assert.throws(() => parseEnterpriseToolList([{ id: "t" }]));
  assert.throws(() => parseCreatedMcpTool({ id: "t" }));
  assert.throws(() => parseMcpTestResult({ ok: true, tools: [{}] }));
  assert.throws(() => parseTenantDeleteResult({}));
});

test("representative direct page responses parse", () => {
  assert.deepEqual(parseUnreadCount({ unread_count: 2 }), { unread_count: 2 });
  assert.deepEqual(
    parseSsoProviders([{ provider_type: "feishu", url: "https://sso.test" }]),
    [{ provider_type: "feishu", url: "https://sso.test" }],
  );
  assert.deepEqual(parseTenantQuotas({ default_max_agents: 2 }), {
    default_max_agents: 2,
  });
  assert.deepEqual(parseCreatedMcpTool({ id: "t", name: "tool" }), {
    id: "t",
    name: "tool",
  });
  assert.equal(
    parseSsoSessionStatus({
      status: "authorized",
      access_token: "token",
      user: {
        id: "u",
        username: null,
        email: null,
        display_name: "User",
        role: "member",
        is_active: true,
        created_at: "2026-08-26T00:00:00Z",
      },
    }).user.username,
    "",
  );
  assert.equal(
    parseEnterpriseToolList([
      {
        agent_tool_id: "at",
        tool_id: "t",
        tool_name: "tool",
        tool_display_name: "Tool",
        enabled: true,
      },
    ])[0].id,
    "t",
  );
  assert.equal(
    parseInvitationCodePage({
      items: [
        {
          id: "i",
          code: "CODE",
          used_count: 0,
          max_uses: 1,
          is_active: true,
          created_at: null,
        },
      ],
      total: 1,
    }).total,
    1,
  );
  assert.throws(() => parseInvitationCodeCreate({ created: 1, codes: [1] }));
  assert.throws(() => parseInvitationCodeDeactivate({ status: "ok" }));
  assert.throws(() => parsePublicNotificationBar({ enabled: "yes" }));
  assert.throws(() => parseEmailExists({ exists: "yes" }));
  assert.throws(() => parseApiKey({ api_key: 1 }));
  assert.throws(() => parseAgentPermissions({ is_owner: true }));
  assert.throws(() => parseVersion({ version: 1 }));
});
