import { randomUUID } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import axios from "axios";
import { getAccessToken } from "./auth";
import { stripTargetPrefix } from "./config";
import { resolveOriginalPeerId } from "./peer-id-registry";
import {
  readNamespaceJson,
  resolveNamespacePath,
  writeNamespaceJsonAtomic,
} from "./persistence-store";
import type {
  AICardInstance,
  AICardStreamingRequest,
  DingTalkConfig,
  DingTalkTrackingMetadata,
  Logger,
} from "./types";
import { AICardStatus } from "./types";
import { formatDingTalkErrorPayloadLog, getProxyBypassOption } from "./utils";

const DINGTALK_API = "https://api.dingtalk.com";
// Thinking/tool stream snippets are truncated to keep card updates compact.
const CARD_STATE_FILE_VERSION = 1;
const CARD_PENDING_NAMESPACE = "cards.active.pending";
const CARD_PROCESS_QUERY_NAMESPACE = "cards.content.quote-process-query";
const RECOVERY_FINALIZE_MESSAGE = "⚠️ 上一次回复处理中断，已自动结束。请重新发送你的问题。";
const AICARD_DEGRADE_DEFAULT_MS = 30 * 60 * 1000;

const aicardDegradeByAccount = new Map<string, { untilMs: number; reason: string }>();

function getAICardDegradeMs(config?: DingTalkConfig): number {
  const raw = config?.aicardDegradeMs;
  if (typeof raw === "number" && Number.isFinite(raw) && raw >= 60_000) {
    return raw;
  }
  return AICARD_DEGRADE_DEFAULT_MS;
}

function normalizeDegradeErrorMessage(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function shouldTriggerAICardDegrade(err: unknown): boolean {
  const maybeErr = err as {
    response?: { status?: number; data?: { message?: string } };
    message?: string;
  };
  const status = maybeErr.response?.status;
  const msg = normalizeDegradeErrorMessage(
    String(maybeErr.response?.data?.message || maybeErr.message || ""),
  );
  if (status === 403 || status === 429) {
    return true;
  }
  if (typeof status === "number" && status >= 500 && status < 600) {
    return true;
  }
  return [
    "ipnotinwhitelist",
    "forbiddenaccessdenied",
    "timeout",
    "etimedout",
    "econnreset",
    "eaiagain",
    "sockethangup",
    "badgateway",
  ].some((keyword) => msg.includes(keyword));
}

export function isAICardDegraded(accountId: string): boolean {
  const state = aicardDegradeByAccount.get(accountId);
  if (!state) {
    return false;
  }
  if (Date.now() >= state.untilMs) {
    aicardDegradeByAccount.delete(accountId);
    return false;
  }
  return true;
}

export function getAICardDegradeState(
  accountId: string,
): { remainingMs: number; reason: string } | null {
  const state = aicardDegradeByAccount.get(accountId);
  if (!state) {
    return null;
  }
  const remainingMs = state.untilMs - Date.now();
  if (remainingMs <= 0) {
    aicardDegradeByAccount.delete(accountId);
    return null;
  }
  return { remainingMs, reason: state.reason };
}

export function activateAICardDegrade(
  accountId: string,
  reason: string,
  config?: DingTalkConfig,
  log?: Logger,
): void {
  const durationMs = getAICardDegradeMs(config);
  const untilMs = Date.now() + durationMs;
  const existed = isAICardDegraded(accountId);
  aicardDegradeByAccount.set(accountId, { untilMs, reason });
  const minutes = Math.round(durationMs / 60000);
  if (existed) {
    log?.warn?.(
      `[DingTalk][AICard][Degrade] Extended for account=${accountId}, minutes=${minutes}, reason=${reason}`,
    );
  } else {
    log?.warn?.(
      `[DingTalk][AICard][Degrade] Activated for account=${accountId}, minutes=${minutes}, reason=${reason}`,
    );
  }
}

export function clearAICardDegrade(accountId: string, log?: Logger): void {
  if (!aicardDegradeByAccount.has(accountId)) {
    return;
  }
  const reason = aicardDegradeByAccount.get(accountId)?.reason || "";
  aicardDegradeByAccount.delete(accountId);
  log?.info?.(`[DingTalk][AICard][Degrade] Cleared for account=${accountId}, lastReason=${reason}`);
}

function extractCardProcessQueryKey(payload: unknown): string | undefined {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }
  const data = payload as Record<string, any>;
  const deliverResults = data.result?.deliverResults;
  if (Array.isArray(deliverResults)) {
    for (const item of deliverResults) {
      if (typeof item?.carrierId === "string" && item.carrierId.trim()) {
        return item.carrierId.trim();
      }
    }
  }
  return undefined;
}

interface CreateAICardOptions {
  accountId?: string;
  storePath?: string;
  persistPending?: boolean;
}

interface PendingCardRecord {
  accountId: string;
  cardInstanceId: string;
  outTrackId?: string;
  conversationId: string;
  createdAt: number;
  lastUpdated: number;
  state: string;
}

interface PendingCardStateFile {
  version: number;
  updatedAt: number;
  pendingCards: PendingCardRecord[];
}

function getCardStateFilePath(storePath?: string): string | null {
  if (!storePath) {
    return null;
  }
  return resolveNamespacePath(CARD_PENDING_NAMESPACE, {
    storePath,
    format: "json",
  });
}

function getLegacyCardStateFilePath(storePath?: string): string | null {
  if (!storePath) {
    return null;
  }
  return path.join(path.dirname(storePath), "dingtalk-active-cards.json");
}

function normalizePendingState(parsed: Partial<PendingCardStateFile>): PendingCardStateFile {
  const records = Array.isArray(parsed.pendingCards) ? parsed.pendingCards : [];
  return {
    version: typeof parsed.version === "number" ? parsed.version : CARD_STATE_FILE_VERSION,
    updatedAt: typeof parsed.updatedAt === "number" ? parsed.updatedAt : Date.now(),
    pendingCards: records.filter((entry): entry is PendingCardRecord =>
      Boolean(
        entry &&
        typeof entry.accountId === "string" &&
        typeof entry.cardInstanceId === "string" &&
        (entry.outTrackId === undefined || typeof entry.outTrackId === "string") &&
        typeof entry.conversationId === "string",
      ),
    ),
  };
}

function readPendingCardState(storePath?: string, log?: Logger): PendingCardStateFile {
  if (!storePath) {
    return { version: CARD_STATE_FILE_VERSION, updatedAt: Date.now(), pendingCards: [] };
  }

  const filePath = getCardStateFilePath(storePath);
  const legacyPath = getLegacyCardStateFilePath(storePath);

  if (filePath && fs.existsSync(filePath)) {
    const parsed = readNamespaceJson<Partial<PendingCardStateFile>>(CARD_PENDING_NAMESPACE, {
      storePath,
      format: "json",
      fallback: {},
      log,
    });
    return normalizePendingState(parsed);
  }

  try {
    if (!legacyPath || !fs.existsSync(legacyPath)) {
      return { version: CARD_STATE_FILE_VERSION, updatedAt: Date.now(), pendingCards: [] };
    }
    const raw = fs.readFileSync(legacyPath, "utf-8");
    if (!raw.trim()) {
      return { version: CARD_STATE_FILE_VERSION, updatedAt: Date.now(), pendingCards: [] };
    }
    const normalized = normalizePendingState(JSON.parse(raw) as Partial<PendingCardStateFile>);
    writePendingCardState(normalized, storePath, log);
    return normalized;
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    log?.warn?.(`[DingTalk][AICard] Failed to read pending card state: ${message}`);
    return { version: CARD_STATE_FILE_VERSION, updatedAt: Date.now(), pendingCards: [] };
  }
}

function writePendingCardState(
  state: PendingCardStateFile,
  storePath?: string,
  log?: Logger,
): void {
  if (!storePath) {
    return;
  }
  writeNamespaceJsonAtomic(CARD_PENDING_NAMESPACE, {
    storePath,
    format: "json",
    data: state,
    log,
  });
}

function upsertPendingCard(card: AICardInstance, storePath?: string, log?: Logger): void {
  if (!card.accountId || !storePath) {
    return;
  }
  const state = readPendingCardState(storePath, log);
  const next: PendingCardRecord = {
    accountId: card.accountId,
    cardInstanceId: card.cardInstanceId,
    outTrackId: card.outTrackId,
    conversationId: card.conversationId,
    createdAt: card.createdAt,
    lastUpdated: card.lastUpdated,
    state: card.state,
  };
  const index = state.pendingCards.findIndex((item) => item.cardInstanceId === card.cardInstanceId);
  if (index >= 0) {
    state.pendingCards[index] = next;
  } else {
    state.pendingCards.push(next);
  }
  state.updatedAt = Date.now();
  writePendingCardState(state, storePath, log);
}

function removePendingCard(card: AICardInstance, log?: Logger): void {
  if (!card.accountId || !card.storePath) {
    return;
  }
  removePendingCardById(card.cardInstanceId, card.storePath, log);
}

function removePendingCardById(cardInstanceId: string, storePath?: string, log?: Logger): void {
  if (!storePath) {
    return;
  }
  const state = readPendingCardState(storePath, log);
  const remaining = state.pendingCards.filter((item) => item.cardInstanceId !== cardInstanceId);
  if (remaining.length === state.pendingCards.length) {
    return;
  }
  state.pendingCards = remaining;
  state.updatedAt = Date.now();
  writePendingCardState(state, storePath, log);
}

function listPendingCardsByAccount(
  accountId: string,
  storePath?: string,
  log?: Logger,
): PendingCardRecord[] {
  const state = readPendingCardState(storePath, log);
  return state.pendingCards.filter((item) => item.accountId === accountId);
}

function normalizeRecoveredState(state: string): AICardInstance["state"] {
  if (state === AICardStatus.PROCESSING || state === AICardStatus.INPUTING) {
    return state;
  }
  return AICardStatus.PROCESSING;
}

// Helper to identify card terminal states.
export function isCardInTerminalState(state: string): boolean {
  return state === AICardStatus.FINISHED || state === AICardStatus.FAILED;
}

export function formatContentForCard(content: string | undefined, type: "thinking" | "tool"): string {
  if (!content) {
    return "";
  }

  const emoji = type === "thinking" ? "🤔" : "🛠️";
  const label = type === "thinking" ? "思考中" : "工具执行";

  const escaped = content
    .split("\n")
    .map((line) => line.replace(/^_(?=[^ ])/, "*").replace(/(?<=[^ ])_(?=$)/, "*"))
    .join("\n");

  return `${emoji} **${label}**\n\n${escaped}`;
}

async function sendTemplateMismatchNotification(
  card: AICardInstance,
  text: string,
  log?: Logger,
): Promise<void> {
  const config = card.config;
  if (!config) {
    return;
  }
  try {
    const token = await getAccessToken(config, log);
    const { targetId, isExplicitUser } = stripTargetPrefix(card.conversationId);
    const resolvedTarget = resolveOriginalPeerId(targetId);
    const isGroup = !isExplicitUser && resolvedTarget.startsWith("cid");
    const url = isGroup
      ? "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
      : "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend";

    // Direct markdown fallback notification to user/group, without re-entering sendMessage card flow.
    const payload: Record<string, unknown> = {
      robotCode: config.robotCode || config.clientId,
      msgKey: "sampleMarkdown",
      msgParam: JSON.stringify({ title: "OpenClaw 提醒", text }),
    };

    if (isGroup) {
      payload.openConversationId = resolvedTarget;
    } else {
      payload.userIds = [resolvedTarget];
    }

    await axios({
      url,
      method: "POST",
      data: payload,
      headers: { "x-acs-dingtalk-access-token": token, "Content-Type": "application/json" },
    });
  } catch (sendErr: any) {
    log?.warn?.(`[DingTalk][AICard] Failed to send error notification to user: ${sendErr.message}`);
  }
}

/**
 * Send a proactive text message via card API (createAndDeliver + immediate finalize).
 * Used in card mode to replace oToMessages/batchSend for single-chat users.
 */
export async function sendProactiveCardText(
  config: DingTalkConfig,
  conversationId: string,
  content: string,
  log?: Logger,
): Promise<{ ok: boolean; error?: string } & DingTalkTrackingMetadata> {
  try {
    const card = await createAICard(config, conversationId, log, { persistPending: false });
    if (!card) {
      return { ok: false, error: "Failed to create AI card" };
    }
    await finishAICard(card, content, log);
    return {
      ok: true,
      processQueryKey: card.processQueryKey,
      outTrackId: card.outTrackId,
      cardInstanceId: card.cardInstanceId,
    };
  } catch (err: any) {
    log?.error?.(`[DingTalk][AICard] Proactive card send failed: ${err.message}`);
    return { ok: false, error: err.message };
  }
}

export async function recoverPendingCardsForAccount(
  config: DingTalkConfig,
  accountId: string,
  storePath?: string,
  log?: Logger,
): Promise<number> {
  return finalizePendingCardsByAccount(
    config,
    accountId,
    RECOVERY_FINALIZE_MESSAGE,
    storePath,
    "recover",
    log,
  );
}

export async function finalizeActiveCardsForAccount(
  config: DingTalkConfig,
  accountId: string,
  reason: string,
  storePath?: string,
  log?: Logger,
): Promise<number> {
  return finalizePendingCardsByAccount(config, accountId, reason, storePath, "finalize", log);
}

async function finalizePendingCardsByAccount(
  config: DingTalkConfig,
  accountId: string,
  reason: string,
  storePath: string | undefined,
  mode: "recover" | "finalize",
  log?: Logger,
): Promise<number> {
  if (!storePath) {
    return 0;
  }

  const pendingCards = listPendingCardsByAccount(accountId, storePath, log).filter(
    (item) => !isCardInTerminalState(item.state),
  );
  if (pendingCards.length === 0) {
    return 0;
  }

  let token = "";
  try {
    token = await getAccessToken(config, log);
  } catch (err: any) {
    const tokenFailureScope =
      mode === "recover" ? "pending card recovery" : "finalizing active cards";
    log?.warn?.(
      `[DingTalk][AICard] Failed to fetch token for ${tokenFailureScope}: ${err.message}`,
    );
    return 0;
  }

  let finalizedCount = 0;
  for (const entry of pendingCards) {
    const card: AICardInstance = {
      cardInstanceId: entry.cardInstanceId,
      accessToken: token,
      conversationId: entry.conversationId,
      accountId: entry.accountId,
      storePath,
      outTrackId: entry.outTrackId,
      createdAt: entry.createdAt || Date.now(),
      lastUpdated: entry.lastUpdated || Date.now(),
      state: normalizeRecoveredState(entry.state),
      config,
    };
    try {
      await finishAICard(card, reason, log);
      finalizedCount += 1;
    } catch (err: any) {
      const action = mode === "recover" ? "recover" : "finalize";
      log?.warn?.(
        `[DingTalk][AICard] Failed to ${action} active card ${entry.cardInstanceId}: ${err.message}`,
      );
      removePendingCardById(entry.cardInstanceId, storePath, log);
    }
  }
  return finalizedCount;
}

export async function createAICard(
  config: DingTalkConfig,
  conversationId: string,
  log?: Logger,
  options: CreateAICardOptions = {},
): Promise<AICardInstance | null> {
  const accountId = options.accountId ?? "default";
  if (isAICardDegraded(accountId)) {
    const state = getAICardDegradeState(accountId);
    log?.warn?.(
      `[DingTalk][AICard][Degrade] Skip create for account=${accountId}, remainingMs=${state?.remainingMs || 0}, reason=${state?.reason || "unknown"}`,
    );
    return null;
  }

  try {
    const shouldPersistPending =
      options.persistPending ?? Boolean(options.accountId && options.storePath);
    const token = await getAccessToken(config, log);
    // Use randomUUID to avoid collisions across workers/restarts.
    const cardInstanceId = `card_${randomUUID()}`;

    log?.info?.(`[DingTalk][AICard] Creating and delivering card outTrackId=${cardInstanceId}`);

    const isGroup = conversationId.startsWith("cid");

    if (!config.cardTemplateId) {
      throw new Error("DingTalk cardTemplateId is not configured.");
    }

    // DingTalk createAndDeliver API payload.
    const cardTemplateKey = config.cardTemplateKey || "content";
    const cardParamMap = {
      config: JSON.stringify({ autoLayout: true, enableForward: true }),
      [cardTemplateKey]: "",
    };
    const createAndDeliverBody = {
      cardTemplateId: config.cardTemplateId,
      outTrackId: cardInstanceId,
      cardData: {
        cardParamMap,
      },
      callbackType: "STREAM",
      imGroupOpenSpaceModel: { supportForward: true },
      imRobotOpenSpaceModel: { supportForward: true },
      openSpaceId: isGroup
        ? `dtv1.card//IM_GROUP.${conversationId}`
        : `dtv1.card//IM_ROBOT.${conversationId}`,
      userIdType: 1,
      imGroupOpenDeliverModel: isGroup
        ? { robotCode: config.robotCode || config.clientId }
        : undefined,
      imRobotOpenDeliverModel: !isGroup
        ? { spaceType: "IM_ROBOT", robotCode: config.robotCode || config.clientId }
        : undefined,
    };

    if (isGroup && !config.robotCode) {
      log?.warn?.(
        "[DingTalk][AICard] robotCode not configured, using clientId as fallback. " +
          "For best compatibility, set robotCode explicitly in config.",
      );
    }

    log?.debug?.(
      `[DingTalk][AICard] POST /v1.0/card/instances/createAndDeliver body=${JSON.stringify(createAndDeliverBody)}`,
    );
    const resp = await axios.post(
      `${DINGTALK_API}/v1.0/card/instances/createAndDeliver`,
      createAndDeliverBody,
      {
        headers: { "x-acs-dingtalk-access-token": token, "Content-Type": "application/json" },
        ...getProxyBypassOption(config),
      },
    );
    log?.debug?.(
      `[DingTalk][AICard] CreateAndDeliver response: status=${resp.status} data=${JSON.stringify(resp.data)}`,
    );
    const responseData = resp.data as
      | {
          result?: DingTalkTrackingMetadata;
          processQueryKey?: unknown;
          outTrackId?: unknown;
          cardInstanceId?: unknown;
        }
      | undefined;
    const responseTracking = responseData?.result;
    const processQueryKey =
      typeof responseTracking?.processQueryKey === "string" &&
      responseTracking.processQueryKey.trim()
        ? responseTracking.processQueryKey.trim()
        : typeof responseData?.processQueryKey === "string" && responseData.processQueryKey.trim()
          ? responseData.processQueryKey.trim()
          : undefined;
    const outTrackId =
      typeof responseTracking?.outTrackId === "string" && responseTracking.outTrackId.trim()
        ? responseTracking.outTrackId.trim()
        : typeof responseData?.outTrackId === "string" && responseData.outTrackId.trim()
          ? responseData.outTrackId.trim()
          : cardInstanceId;
    const resolvedCardInstanceId =
      typeof responseTracking?.cardInstanceId === "string" && responseTracking.cardInstanceId.trim()
        ? responseTracking.cardInstanceId.trim()
        : typeof responseData?.cardInstanceId === "string" && responseData.cardInstanceId.trim()
          ? responseData.cardInstanceId.trim()
          : cardInstanceId;

    // Return the AI card instance with config reference for token refresh/recovery.
    const aiCardInstance: AICardInstance = {
      cardInstanceId: resolvedCardInstanceId,
      accessToken: token,
      conversationId,
      accountId,
      storePath: options.storePath,
      createdAt: Date.now(),
      lastUpdated: Date.now(),
      state: AICardStatus.PROCESSING,
      config,
      processQueryKey: processQueryKey || extractCardProcessQueryKey(resp.data),
      outTrackId,
    };
    if (shouldPersistPending) {
      upsertPendingCard(aiCardInstance, options.storePath, log);
    }

    clearAICardDegrade(accountId, log);
    return aiCardInstance;
  } catch (err: any) {
    log?.error?.(`[DingTalk][AICard] Create failed: ${err.message}`);
    if (err.response) {
      const status = err.response.status;
      const statusText = err.response.statusText;
      const statusLabel = status ? ` status=${status}${statusText ? ` ${statusText}` : ""}` : "";
      log?.error?.(`[DingTalk][AICard] Create error response${statusLabel}`);
      log?.error?.(
        formatDingTalkErrorPayloadLog("card.create", err.response.data, "[DingTalk][AICard]"),
      );
    }
    if (shouldTriggerAICardDegrade(err)) {
      activateAICardDegrade(
        accountId,
        `card.create:${err?.response?.status || "unknown"}`,
        config,
        log,
      );
    }
    return null;
  }
}

export async function streamAICard(
  card: AICardInstance,
  content: string,
  finished: boolean = false,
  log?: Logger,
): Promise<void> {
  if (card.state === AICardStatus.FINISHED) {
    log?.debug?.(
      `[DingTalk][AICard] Skip stream update because card already finalized: outTrackId=${card.cardInstanceId}`,
    );
    return;
  }

  // Refresh token defensively before DingTalk 2h token horizon.
  const tokenAge = Date.now() - card.createdAt;
  const tokenRefreshThreshold = 90 * 60 * 1000;

  if (tokenAge > tokenRefreshThreshold && card.config) {
    log?.debug?.("[DingTalk][AICard] Token age exceeds threshold, refreshing...");
    try {
      card.accessToken = await getAccessToken(card.config, log);
      log?.debug?.("[DingTalk][AICard] Token refreshed successfully");
    } catch (err: any) {
      log?.warn?.(`[DingTalk][AICard] Failed to refresh token: ${err.message}`);
    }
  }

  // Always use full replacement to make client rendering deterministic.
  const streamBody: AICardStreamingRequest = {
    outTrackId: card.outTrackId || card.cardInstanceId,
    guid: randomUUID(),
    key: card.config?.cardTemplateKey || "content",
    content: content,
    isFull: true,
    isFinalize: finished,
    isError: false,
  };

  log?.debug?.(
    `[DingTalk][AICard] PUT /v1.0/card/streaming contentLen=${content.length} isFull=true isFinalize=${finished} guid=${streamBody.guid} payload=${JSON.stringify(streamBody)}`,
  );

  try {
    const streamResp = await axios.put(`${DINGTALK_API}/v1.0/card/streaming`, streamBody, {
      headers: {
        "x-acs-dingtalk-access-token": card.accessToken,
        "Content-Type": "application/json",
      },
      ...(card.config ? getProxyBypassOption(card.config) : {}),
    });
    log?.debug?.(
      `[DingTalk][AICard] Streaming response: status=${streamResp.status}, data=${JSON.stringify(streamResp.data)}`,
    );

    card.lastUpdated = Date.now();
    card.lastStreamedContent = content;
    if (finished) {
      card.state = AICardStatus.FINISHED;
      removePendingCard(card, log);
    } else if (card.state === AICardStatus.PROCESSING) {
      card.state = AICardStatus.INPUTING;
    }
  } catch (err: any) {
    // 500 unknownError usually means cardTemplateKey mismatch with template variable names.
    if (err.response?.status === 500 && err.response?.data?.code === "unknownError") {
      const usedKey = streamBody.key;
      const cardTemplateId = card.config?.cardTemplateId || "(unknown)";
      const errorMsg =
        `⚠️ **[DingTalk] AI Card 串流更新失败 (500 unknownError)**\n\n` +
        `这通常是因为 \`cardTemplateKey\` (当前值: \`${usedKey}\`) 与钉钉卡片模板 \`${cardTemplateId}\` 中定义的正文变量名不匹配。\n\n` +
        `**建议操作**：\n` +
        `1. 前往钉钉开发者后台检查该模板的“变量管理”\n` +
        `2. 确保配置中的 \`cardTemplateKey\` 与模板中用于显示内容的字段变量名完全一致\n\n` +
        `*注意：当前及后续消息将自动转为 Markdown 发送，直到问题修复。*\n` +
        `*参考文档: https://github.com/soimy/openclaw-channel-dingtalk/blob/main/README.md#3-%E5%BB%BA%E7%AB%8B%E5%8D%A1%E7%89%87%E6%A8%A1%E6%9D%BF%E5%8F%AF%E9%80%89`;

      log?.error?.(
        `[DingTalk][AICard] Streaming failed with 500 unknownError. Key: ${usedKey}, Template: ${cardTemplateId}. ` +
          `Verify that "cardTemplateKey" matches the content field variable name in your card template.`,
      );

      card.state = AICardStatus.FAILED;
      card.lastUpdated = Date.now();
      removePendingCard(card, log);
      await sendTemplateMismatchNotification(card, errorMsg, log);
      throw err;
    }

    // Retry once on 401 with refreshed token.
    if (err.response?.status === 401 && card.config) {
      log?.warn?.("[DingTalk][AICard] Received 401 error, attempting token refresh and retry...");
      try {
        card.accessToken = await getAccessToken(card.config, log);
        const retryResp = await axios.put(`${DINGTALK_API}/v1.0/card/streaming`, streamBody, {
          headers: {
            "x-acs-dingtalk-access-token": card.accessToken,
            "Content-Type": "application/json",
          },
          ...(card.config ? getProxyBypassOption(card.config) : {}),
        });
        log?.debug?.(
          `[DingTalk][AICard] Retry after token refresh succeeded: status=${retryResp.status}`,
        );
        card.lastUpdated = Date.now();
        card.lastStreamedContent = content;
        if (finished) {
          card.state = AICardStatus.FINISHED;
          removePendingCard(card, log);
        } else if (card.state === AICardStatus.PROCESSING) {
          card.state = AICardStatus.INPUTING;
        }
        return;
      } catch (retryErr: any) {
        log?.error?.(`[DingTalk][AICard] Retry after token refresh failed: ${retryErr.message}`);
        if (retryErr.response?.data !== undefined) {
          log?.error?.(
            formatDingTalkErrorPayloadLog(
              "card.stream.retryAfterRefresh",
              retryErr.response.data,
              "[DingTalk][AICard]",
            ),
          );
        }
      }
    }

    card.state = AICardStatus.FAILED;
    card.lastUpdated = Date.now();
    removePendingCard(card, log);
    if (card.accountId && shouldTriggerAICardDegrade(err)) {
      activateAICardDegrade(
        card.accountId,
        `card.stream:${err?.response?.status || "unknown"}`,
        card.config,
        log,
      );
    }
    log?.error?.(`[DingTalk][AICard] Streaming update failed: ${err.message}`);
    if (err.response?.data !== undefined) {
      log?.error?.(
        formatDingTalkErrorPayloadLog("card.stream", err.response.data, "[DingTalk][AICard]"),
      );
    }
    throw err;
  }
}

export async function finishAICard(
  card: AICardInstance,
  content: string,
  log?: Logger,
): Promise<void> {
  log?.debug?.(`[DingTalk][AICard] Starting finish, final content length=${content.length}`);
  await streamAICard(card, content, true, log);
  if (card.conversationId && content.trim() && card.accountId && card.processQueryKey) {
    cacheCardContentByProcessQueryKey(
      card.accountId,
      card.conversationId,
      card.processQueryKey,
      content,
      card.storePath,
    );
  }
}

interface ProcessQueryCardContentEntry {
  content: string;
  createdAt: number;
  expiresAt: number;
}

interface PersistedProcessQueryCardContent {
  updatedAt: number;
  entries: Record<string, ProcessQueryCardContentEntry>;
}

function readProcessQueryCardContent(
  accountId: string,
  conversationId: string,
  storePath?: string,
): PersistedProcessQueryCardContent {
  if (!storePath) {
    return { updatedAt: 0, entries: {} };
  }
  return readNamespaceJson<PersistedProcessQueryCardContent>(CARD_PROCESS_QUERY_NAMESPACE, {
    storePath,
    scope: { accountId, conversationId },
    format: "json",
    fallback: { updatedAt: 0, entries: {} },
  });
}

function writeProcessQueryCardContent(
  accountId: string,
  conversationId: string,
  data: PersistedProcessQueryCardContent,
  storePath?: string,
): void {
  if (!storePath) {
    return;
  }
  writeNamespaceJsonAtomic(CARD_PROCESS_QUERY_NAMESPACE, {
    storePath,
    scope: { accountId, conversationId },
    format: "json",
    data,
  });
}

function purgeExpiredProcessQueryEntries(
  persisted: PersistedProcessQueryCardContent,
  nowMs: number,
): boolean {
  let changed = false;
  for (const [key, entry] of Object.entries(persisted.entries)) {
    if (!entry || typeof entry.expiresAt !== "number" || nowMs >= entry.expiresAt) {
      delete persisted.entries[key];
      changed = true;
    }
  }
  return changed;
}

export function cacheCardContentByProcessQueryKey(
  accountId: string,
  conversationId: string,
  processQueryKey: string,
  content: string,
  storePath?: string,
): void {
  if (!processQueryKey.trim() || !content.trim() || !storePath) {
    return;
  }
  const nowMs = Date.now();
  const persisted = readProcessQueryCardContent(accountId, conversationId, storePath);
  purgeExpiredProcessQueryEntries(persisted, nowMs);
  persisted.entries[processQueryKey] = {
    content,
    createdAt: nowMs,
    expiresAt: nowMs + CARD_CACHE_TTL_MS,
  };
  persisted.updatedAt = nowMs;
  writeProcessQueryCardContent(accountId, conversationId, persisted, storePath);
}

export function getCardContentByProcessQueryKey(
  accountId: string,
  conversationId: string,
  processQueryKey: string,
  storePath?: string,
): string | null {
  if (!processQueryKey.trim() || !storePath) {
    return null;
  }
  const nowMs = Date.now();
  const persisted = readProcessQueryCardContent(accountId, conversationId, storePath);
  const changed = purgeExpiredProcessQueryEntries(persisted, nowMs);
  const entry = persisted.entries[processQueryKey];
  if (!entry) {
    if (changed) {
      persisted.updatedAt = nowMs;
      writeProcessQueryCardContent(accountId, conversationId, persisted, storePath);
    }
    return null;
  }
  return entry.content;
}

// ============ Card content cache (for quoted card lookup) ============

const CARD_CACHE_TTL_MS = 24 * 60 * 60 * 1000;
const CARD_CACHE_MAX_PER_CONVERSATION = 20;
const CARD_CACHE_MAX_CONVERSATIONS = 500;
const CARD_CACHE_MATCH_WINDOW_MS = 2000;
const CARD_CONTENT_NAMESPACE = "cards.content.quote-lookup";

interface CardContentEntry {
  content: string;
  createdAt: number;
  expiresAt: number;
}

interface CardConversationBucket {
  entries: CardContentEntry[];
  lastActiveAt: number;
}

interface PersistedCardContentBucket {
  updatedAt: number;
  entries: CardContentEntry[];
}

const cardContentStore = new Map<string, CardConversationBucket>();

function loadCardContentBucketFromPersistence(
  accountId: string,
  conversationId: string,
  storePath?: string,
): CardConversationBucket | null {
  if (!storePath) {
    return null;
  }
  const persisted = readNamespaceJson<PersistedCardContentBucket>(CARD_CONTENT_NAMESPACE, {
    storePath,
    scope: { accountId, conversationId },
    format: "json",
    fallback: { updatedAt: 0, entries: [] },
  });
  if (!Array.isArray(persisted.entries) || persisted.entries.length === 0) {
    return null;
  }

  const now = Date.now();
  const entries: CardContentEntry[] = [];
  for (const entry of persisted.entries) {
    if (
      !entry ||
      typeof entry.content !== "string" ||
      typeof entry.createdAt !== "number" ||
      typeof entry.expiresAt !== "number" ||
      now >= entry.expiresAt
    ) {
      continue;
    }
    const insertAt = entries.findIndex((item) => item.createdAt > entry.createdAt);
    if (insertAt < 0) {
      entries.push(entry);
    } else {
      entries.splice(insertAt, 0, entry);
    }
  }
  const normalizedEntries = entries.slice(-CARD_CACHE_MAX_PER_CONVERSATION);

  if (normalizedEntries.length === 0) {
    return null;
  }
  return {
    entries: normalizedEntries,
    lastActiveAt: now,
  };
}

function persistCardContentBucket(
  accountId: string,
  conversationId: string,
  bucket: CardConversationBucket,
  storePath?: string,
): void {
  if (!storePath) {
    return;
  }
  writeNamespaceJsonAtomic(CARD_CONTENT_NAMESPACE, {
    storePath,
    scope: { accountId, conversationId },
    format: "json",
    data: {
      updatedAt: Date.now(),
      entries: bucket.entries,
    } satisfies PersistedCardContentBucket,
  });
}

export function cacheCardContent(
  accountId: string,
  conversationId: string,
  content: string,
  createdAt: number,
  storePath?: string,
): void {
  const scopedKey = `${accountId}:${conversationId}`;
  let bucket = cardContentStore.get(scopedKey);
  if (!bucket) {
    bucket = { entries: [], lastActiveAt: Date.now() };
    cardContentStore.set(scopedKey, bucket);
    if (cardContentStore.size > CARD_CACHE_MAX_CONVERSATIONS) {
      let oldestKey: string | undefined;
      let oldestTime = Infinity;
      for (const [key, b] of cardContentStore) {
        if (b.lastActiveAt < oldestTime) {
          oldestTime = b.lastActiveAt;
          oldestKey = key;
        }
      }
      if (oldestKey) {
        cardContentStore.delete(oldestKey);
      }
    }
  }
  bucket.lastActiveAt = Date.now();

  const now = Date.now();
  bucket.entries = bucket.entries.filter((e) => now < e.expiresAt);

  bucket.entries.push({ content, createdAt, expiresAt: now + CARD_CACHE_TTL_MS });

  if (bucket.entries.length > CARD_CACHE_MAX_PER_CONVERSATION) {
    bucket.entries.sort((a, b) => a.createdAt - b.createdAt);
    bucket.entries = bucket.entries.slice(-CARD_CACHE_MAX_PER_CONVERSATION);
  }

  persistCardContentBucket(accountId, conversationId, bucket, storePath);
}

export function findCardContent(
  accountId: string,
  conversationId: string,
  repliedCreatedAt: number,
  storePath?: string,
): string | null {
  const scopedKey = `${accountId}:${conversationId}`;
  let bucket = cardContentStore.get(scopedKey);
  if (!bucket && storePath) {
    const loaded = loadCardContentBucketFromPersistence(accountId, conversationId, storePath);
    if (loaded) {
      cardContentStore.set(scopedKey, loaded);
      bucket = loaded;
      if (cardContentStore.size > CARD_CACHE_MAX_CONVERSATIONS) {
        let oldestKey: string | undefined;
        let oldestTime = Infinity;
        for (const [key, b] of cardContentStore) {
          if (b.lastActiveAt < oldestTime) {
            oldestTime = b.lastActiveAt;
            oldestKey = key;
          }
        }
        if (oldestKey) {
          cardContentStore.delete(oldestKey);
        }
      }
    }
  }
  if (!bucket) {
    return null;
  }
  bucket.lastActiveAt = Date.now();

  let bestContent: string | null = null;
  let bestDelta = Infinity;

  for (const entry of bucket.entries) {
    if (Date.now() >= entry.expiresAt) {
      continue;
    }
    const delta = Math.abs(entry.createdAt - repliedCreatedAt);
    if (delta <= CARD_CACHE_MATCH_WINDOW_MS && delta < bestDelta) {
      bestDelta = delta;
      bestContent = entry.content;
    }
  }

  return bestContent;
}

export function clearCardContentCacheForTest(): void {
  cardContentStore.clear();
}
