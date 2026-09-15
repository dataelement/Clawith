import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const adminCompanies = readFileSync(
  new URL("../src/pages/AdminCompanies.tsx", import.meta.url),
  "utf8",
);
const enterpriseSettings = readFileSync(
  new URL("../src/pages/EnterpriseSettings.tsx", import.meta.url),
  "utf8",
);
const platformDashboard = readFileSync(
  new URL("../src/pages/PlatformDashboard.tsx", import.meta.url),
  "utf8",
);
const livePanelUtils = readFileSync(
  new URL("../src/components/AgentBayLivePanel.utils.ts", import.meta.url),
  "utf8",
);
const enterpriseTools = enterpriseSettings;
const channelConfig = readFileSync(
  new URL("../src/components/ChannelConfig.tsx", import.meta.url),
  "utf8",
);
const invitationCodes = readFileSync(
  new URL("../src/pages/InvitationCodes.tsx", import.meta.url),
  "utf8",
);

test("admin configuration saves require an authoritative successful load", () => {
  assert.match(adminCompanies, /platformConfigReady/);
  assert.match(adminCompanies, /if \(!platformConfigReady\) return false/);
  assert.match(adminCompanies, /platformConfigLoadError/);
  assert.match(adminCompanies, /fetchJson<unknown>/);
  assert.match(adminCompanies, /parsePlatformSettings/);
  assert.match(adminCompanies, /parseNotificationBarSetting/);
  assert.match(adminCompanies, /parseSystemEmailSetting/);
  assert.match(adminCompanies, /parseEmailTemplates/);
  assert.match(adminCompanies, /parseIdentityProviders/);
  assert.doesNotMatch(
    adminCompanies,
    /getPlatformSettings\(\)[\s\S]*?\.catch\(\(\) => \{\}\)/,
  );
});

test("quota and company introduction failures cannot look editable and blank", () => {
  assert.match(enterpriseSettings, /quotaLoadState\.status !== "ready"/);
  assert.match(enterpriseSettings, /companyIntroLoadState\.status !== "ready"/);
  assert.match(enterpriseSettings, /companyIntroLoadState\.error/);
  assert.doesNotMatch(
    enterpriseSettings,
    /tenant-quotas[\s\S]{0,240}\.catch\(\(\) => \{\}\)/,
  );
});

test("platform metric refresh failures retain data and render an error", () => {
  assert.match(platformDashboard, /platformMetricsApi/);
  assert.match(platformDashboard, /setStatsError/);
  assert.match(platformDashboard, /setLeadersError/);
  assert.match(platformDashboard, /setEnhancedError/);
  assert.doesNotMatch(platformDashboard, /if \(!response\.ok\) return \[\]/);
});

test("live code truncation limit stays private to its owner", () => {
  assert.match(livePanelUtils, /const MAX_LIVE_CODE_OUTPUT_CHARS/);
  assert.doesNotMatch(
    livePanelUtils,
    /export const MAX_LIVE_CODE_OUTPUT_CHARS/,
  );
});

test("MCP credential failures cannot publish full import success", () => {
  assert.match(enterpriseTools, /importMcpToolsTransaction/);
  assert.doesNotMatch(
    enterpriseTools,
    /mcp-server[\s\S]{0,360}\.catch\(\(\) => \{\}\)/,
  );
  assert.match(enterpriseTools, /rollbackFailedIds/);
});

test("channel reads preserve non-404 and malformed response failures", () => {
  assert.match(channelConfig, /readOptionalChannelResource/);
  assert.doesNotMatch(
    channelConfig,
    /fetchAuth<StoredChannelConfig>[\s\S]{0,120}\.catch\(\(\) => null\)/,
  );
  assert.match(channelConfig, /channelReadError/);
});

test("invitation mutations publish success only after canonical success and reload", () => {
  assert.match(invitationCodes, /parseInvitationCodeCreate/);
  assert.match(invitationCodes, /parseInvitationCodeDeactivate/);
  assert.doesNotMatch(
    invitationCodes,
    /fetch\(["'`]\/api\/enterprise\/invitation-codes["'`][\s\S]{0,100}method:\s*["']POST/,
  );
  assert.doesNotMatch(
    invitationCodes,
    /fetch\(`\/api\/enterprise\/invitation-codes\/\$\{id\}`/,
  );
  assert.match(invitationCodes, /invitation-codes\/export/);
  assert.match(invitationCodes, /if \(!response\.ok\)/);
  assert.match(
    invitationCodes,
    /catch \(error\)[\s\S]*Failed to create invitation codes/,
  );
});

test("enterprise tool list failures retain data and expose retry", () => {
  assert.match(enterpriseTools, /allToolsError/);
  assert.match(enterpriseTools, /agentInstalledToolsError/);
  assert.doesNotMatch(enterpriseTools, /setAllTools\(\[\]\)/);
  assert.doesNotMatch(enterpriseTools, /setAgentInstalledTools\(\[\]\)/);
  assert.match(enterpriseTools, />\s*Retry\s*</);
});
