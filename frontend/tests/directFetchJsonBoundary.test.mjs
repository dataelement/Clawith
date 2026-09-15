import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const srcRoot = new URL("../src/", import.meta.url);

function sourceFiles(directory, prefix = "") {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const relative = path.posix.join(prefix, entry.name);
    if (entry.isDirectory()) {
      return sourceFiles(new URL(`${entry.name}/`, directory), relative);
    }
    return /\.(?:ts|tsx)$/.test(entry.name) ? [relative] : [];
  });
}

const files = sourceFiles(srcRoot);
const sources = new Map(
  files.map((file) => [file, readFileSync(new URL(file, srcRoot), "utf8")]),
);

const typedFetchJsonAllowlist = new Set([
  // Both are validated service boundaries: every concrete result has a parser.
  "services/groupApi.ts",
  "services/platformMetricsApi.ts",
]);

const rawFetchHelperAllowlist = new Set([
  // Canonical HTTP boundary with normalized errors and explicit response parsers.
  "services/api.ts",
  // Feature-local boundaries return unknown and parse before consumption.
  "components/ChannelConfig.tsx",
  "pages/agent-detail/components/ToolsManager.tsx",
  "pages/enterprise-settings/tabs/LlmTab.tsx",
  "pages/enterprise-settings/tabs/OkrTab.tsx",
]);

test("all frontend fetchJson consumers keep unparsed responses unknown", () => {
  for (const [file, source] of sources) {
    if (!typedFetchJsonAllowlist.has(file)) {
      assert.doesNotMatch(source, /fetchJson<(?!unknown>|void>)/, file);
    }
    assert.doesNotMatch(
      source,
      /fetchJson\s*\([^)]*\)\s*\.then\s*\(\s*\w+\s*=>\s*\w+\./,
      file,
    );
    if (file !== "services/api.ts") {
      assert.doesNotMatch(source, /\brequest</, file);
    }
  }
});

test("raw JSON fetches live only in validated helpers", () => {
  for (const [file, source] of sources) {
    if (!/\bfetch\s*\(/.test(source)) continue;
    if (rawFetchHelperAllowlist.has(file)) continue;

    for (const match of source.matchAll(/\.json\s*\(/g)) {
      const index = match.index ?? 0;
      const context = source.slice(Math.max(0, index - 260), index + 80);
      const parsesUnknown = /:\s*unknown\s*=\s*await[\s\S]*\.json\s*\(/.test(
        context,
      );
      const validatesInline = /parse[A-Z]\w*\(\s*await[\s\S]*\.json\s*\(/.test(
        context,
      );
      const readsOnlyErrorEnvelope = /\.json\s*\(\)\.catch\s*\(/.test(
        source.slice(index, index + 80),
      );
      assert.ok(
        parsesUnknown || validatesInline || readsOnlyErrorEnvelope,
        `${file}:${source.slice(0, index).split("\n").length}`,
      );
    }
  }
});

test("each direct response family uses strict parsers", () => {
  const source = [...sources.values()].join("\n");
  for (const parser of [
    "parseSsoProviders",
    "parseSsoSessionStatus",
    "parseUserManagementUsers",
    "parseDashboardOkrObjectives",
    "parseNotificationItems",
    "parseEnterpriseToolList",
    "parseMcpTestResult",
    "parseOAuthCallbackResponse",
    "parsePlatformSettings",
    "parseTimeSeries",
    "parsePublicNotificationBar",
    "parseInvitationCodePage",
    "parseAgentPermissions",
  ]) {
    assert.match(source, new RegExp(parser), parser);
  }
});
