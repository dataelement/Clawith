import * as os from "node:os";
import * as path from "node:path";
import type { OpenClawConfig } from "openclaw/plugin-sdk";
import type { DingTalkConfig } from "./types";

const WINDOWS_ROOT_DIRECTORIES = new Set([
  "Users",
  "Program Files",
  "Program Files (x86)",
  "ProgramData",
  "Windows",
  "Documents and Settings",
]);
const DEFAULT_LEARNING_NOTE_TTL_MS = 6 * 60 * 60 * 1000;

function normalizeLearningConfig(
  config: DingTalkConfig,
  options: { applyDefaults: boolean },
): DingTalkConfig {
  const learningEnabled = config.learningEnabled ?? config.feedbackLearningEnabled;
  const learningAutoApply = config.learningAutoApply ?? config.feedbackLearningAutoApply;
  const learningNoteTtlMs = config.learningNoteTtlMs ?? config.feedbackLearningNoteTtlMs;
  return {
    ...config,
    learningEnabled: options.applyDefaults ? learningEnabled ?? false : learningEnabled,
    learningAutoApply: options.applyDefaults ? learningAutoApply ?? false : learningAutoApply,
    learningNoteTtlMs: options.applyDefaults
      ? learningNoteTtlMs ?? DEFAULT_LEARNING_NOTE_TTL_MS
      : learningNoteTtlMs,
  };
}

/**
 * Merge channel-level defaults into an account-specific config.
 * Account-level values take precedence; `accounts` key is excluded to avoid recursion.
 */
export function mergeAccountWithDefaults(
  channelCfg: DingTalkConfig,
  accountCfg: DingTalkConfig,
): DingTalkConfig {
  const { accounts: _accounts, ...defaults } = channelCfg;
  const normalizedAccountCfg = normalizeLearningConfig(accountCfg, { applyDefaults: false });
  const overrides: Partial<DingTalkConfig> = {};
  for (const [key, value] of Object.entries(normalizedAccountCfg)) {
    if (value !== undefined) {
      Object.assign(overrides, { [key]: value });
    }
  }
  return normalizeLearningConfig(
    {
      ...defaults,
      ...overrides,
    },
    { applyDefaults: true },
  );
}

/**
 * Resolve DingTalk config for an account.
 * Named accounts inherit channel-level defaults with account-level overrides.
 * Falls back to top-level config for single-account setups.
 */
export function getConfig(cfg: OpenClawConfig, accountId?: string): DingTalkConfig {
  const dingtalkCfg = cfg?.channels?.dingtalk as DingTalkConfig | undefined;
  if (!dingtalkCfg) {
    return {} as DingTalkConfig;
  }

  if (accountId && dingtalkCfg.accounts?.[accountId]) {
    return mergeAccountWithDefaults(dingtalkCfg, dingtalkCfg.accounts[accountId]);
  }

  if (accountId) {
    return normalizeLearningConfig(dingtalkCfg, { applyDefaults: true });
  }

  if (dingtalkCfg.accounts && Object.keys(dingtalkCfg.accounts).length > 0) {
    return dingtalkCfg;
  }

  return normalizeLearningConfig(dingtalkCfg, { applyDefaults: true });
}

export function isConfigured(cfg: OpenClawConfig, accountId?: string): boolean {
  const config = getConfig(cfg, accountId);
  return Boolean(config.clientId && config.clientSecret);
}

/**
 * Resolve relative paths against a base directory, with intelligent platform-specific handling.
 *
 * Supports:
 * - ~ and ~/ expansion to home directory
 * - Absolute paths (Unix: /path, Windows: \path or C:\path)
 * - Relative paths resolved against cwd
 * - Windows absolute paths without drive letters (e.g., Users\name\.openclaw\file.txt)
 * - Mixed path separators (/ and \)
 *
 * @param input - The path string to resolve
 * @returns The resolved absolute path
 */
export function resolveRelativePath(input: string): string {
  const trimmed = input.trim();
  if (!trimmed) {
    return trimmed;
  }

  const segments = (value: string): string[] => value.split(/[\\/]+/).filter(Boolean);
  const pathSegments = segments(trimmed);
  const firstSegment = pathSegments[0];

  // Expand bare "~" and "~/" or "~\\" prefixes into the user home directory.
  if (trimmed === "~") {
    return path.resolve(os.homedir());
  }
  if (trimmed.startsWith("~/") || trimmed.startsWith("~\\")) {
    return path.resolve(os.homedir(), ...segments(trimmed.slice(2)));
  }

  if (process.platform === "win32") {
    // On Windows, OpenClaw may drop the leading "\" from root-based paths like
    // "Users\name\.openclaw\workspace\file.xlsx". Only recover paths that start
    // with well-known root directories to avoid misclassifying ordinary relative paths.
    if (/^[a-zA-Z]:[\\/]/.test(trimmed)) {
      return path.win32.normalize(trimmed);
    }
    if (firstSegment && /^[a-zA-Z]:$/.test(firstSegment)) {
      return path.win32.resolve(`${firstSegment}\\`, ...pathSegments.slice(1));
    }
    if (firstSegment && WINDOWS_ROOT_DIRECTORIES.has(firstSegment)) {
      return path.win32.resolve("\\", ...pathSegments);
    }
  }
  // Treat both "/" and "\\" as absolute root prefixes for cross-platform input.
  if (/^[\\/]/.test(trimmed)) {
    return path.resolve(path.sep, ...pathSegments);
  }

  // Resolve relative path against cwd; supports mixed separators and "..\\..".
  return path.resolve(process.cwd(), ...pathSegments);
}

export const resolveUserPath = resolveRelativePath;

export function resolveGroupConfig(
  cfg: DingTalkConfig,
  groupId: string,
): { systemPrompt?: string } | undefined {
  // Group config supports exact match first, then wildcard fallback.
  const groups = cfg.groups;
  if (!groups) {
    return undefined;
  }
  return groups[groupId] || groups["*"] || undefined;
}

function hasOwn(obj: unknown, key: string): boolean {
  return typeof obj === "object" && obj !== null && Object.prototype.hasOwnProperty.call(obj, key);
}

function resolveAgentIdentityEmoji(cfg: OpenClawConfig, agentId?: string | null): string | undefined {
  const targetAgentId = String(agentId || "").trim();
  if (!targetAgentId) {
    return undefined;
  }
  const agents = Array.isArray((cfg as any)?.agents?.list) ? (cfg as any).agents.list : [];
  const agent = agents.find((entry: any) => String(entry?.id || "").trim() === targetAgentId);
  const emoji = typeof agent?.identity?.emoji === "string" ? agent.identity.emoji.trim() : "";
  return emoji || undefined;
}

export function resolveAckReactionSetting(params: {
  cfg: OpenClawConfig;
  accountId?: string | null;
  agentId?: string | null;
}): string | undefined {
  const dingtalk = (params.cfg?.channels as any)?.dingtalk;
  const accountId = String(params.accountId || "").trim();
  const accountConfig =
    accountId && dingtalk?.accounts && typeof dingtalk.accounts === "object"
      ? dingtalk.accounts[accountId]
      : undefined;

  if (hasOwn(accountConfig, "ackReaction")) {
    return typeof accountConfig.ackReaction === "string" ? accountConfig.ackReaction.trim() : "";
  }
  if (hasOwn(dingtalk, "ackReaction")) {
    return typeof dingtalk.ackReaction === "string" ? dingtalk.ackReaction.trim() : "";
  }

  const messages = (params.cfg as any)?.messages;
  if (hasOwn(messages, "ackReaction")) {
    return typeof messages.ackReaction === "string" ? messages.ackReaction.trim() : "";
  }

  return resolveAgentIdentityEmoji(params.cfg, params.agentId);
}

/**
 * Strip group/user prefixes used by CLI targeting.
 * Returns raw DingTalk target ID and whether caller explicitly requested a user target.
 */
export function stripTargetPrefix(target: string): { targetId: string; isExplicitUser: boolean } {
  if (target.startsWith("group:")) {
    return { targetId: target.slice(6), isExplicitUser: false };
  }
  if (target.startsWith("user:")) {
    return { targetId: target.slice(5), isExplicitUser: true };
  }
  return { targetId: target, isExplicitUser: false };
}
