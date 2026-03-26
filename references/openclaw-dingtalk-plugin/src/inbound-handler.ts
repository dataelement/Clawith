import axios from "axios";
import { normalizeAllowFrom, isSenderAllowed, isSenderGroupAllowed } from "./access-control";
import { extractAttachmentText } from "./attachment-text-extractor";
import { getAccessToken } from "./auth";
import {
  createAICard,
  findCardContent,
  finishAICard,
  formatContentForCard,
  getCardContentByProcessQueryKey,
  isCardInTerminalState,
} from "./card-service";
import { classifyAckReactionEmoji } from "./ack-reaction-classifier";
import { resolveAckReactionSetting, resolveGroupConfig } from "./config";
import { formatGroupMembers, noteGroupMember } from "./group-members-store";
import { setCurrentLogger } from "./logger-context";
import {
  formatLearnAppliedReply,
  formatLearnCommandHelp,
  formatLearnDeletedReply,
  formatLearnDisabledReply,
  formatLearnListReply,
  formatOwnerOnlyDeniedReply,
  formatOwnerStatusReply,
  formatTargetSetSavedReply,
  formatWhereAmIReply,
  formatWhoAmIReply,
  isLearningOwner,
  parseLearnCommand,
} from "./learning-command-service";
import { extractMessageContent } from "./message-utils";
import { prepareMediaInput, resolveOutboundMediaType } from "./media-utils";
import { registerPeerId } from "./peer-id-registry";
import {
  clearProactiveRiskObservationsForTest,
  getProactiveRiskObservationForAny,
} from "./proactive-risk-registry";
import {
  appendQuoteJournalEntry,
  DEFAULT_JOURNAL_TTL_DAYS,
  resolveQuotedMessageById,
} from "./quote-journal";
import { getDingTalkRuntime } from "./runtime";
import { sendBySession, sendMessage, sendProactiveMedia } from "./send-service";
import { clearSessionPeerOverride, getSessionPeerOverride, setSessionPeerOverride } from "./session-peer-store";
import { resolveDingTalkSessionPeer } from "./session-routing";
import type { DingTalkConfig, HandleDingTalkMessageParams, MediaFile } from "./types";
import { AICardStatus } from "./types";
import { createCardDraftController } from "./card-draft-controller";
import { acquireSessionLock } from "./session-lock";
import { cacheInboundDownloadCode, getCachedDownloadCode } from "./quoted-msg-cache";
import { downloadGroupFile, getUnionIdByStaffId, resolveQuotedFile } from "./quoted-file-service";
import {
  formatSessionAliasBoundReply,
  formatSessionAliasClearedReply,
  formatSessionAliasReply,
  formatSessionAliasSetReply,
  formatSessionAliasUnboundReply,
  formatSessionAliasValidationErrorReply,
  parseSessionCommand,
  validateSessionAlias,
} from "./session-command-service";
import {
  applyManualTargetLearningRule,
  applyManualTargetsLearningRule,
  applyManualGlobalLearningRule,
  applyManualSessionLearningNote,
  applyTargetSetLearningRule,
  buildLearningContextBlock,
  createOrUpdateTargetSet,
  deleteManualRule,
  disableManualRule,
  isFeedbackLearningEnabled,
  listLearningTargetSets,
  listScopedLearningRules,
  resolveManualForcedReply,
} from "./feedback-learning-service";
import { attachNativeAckReaction, recallNativeAckReactionWithRetry } from "./ack-reaction-service";
import { formatDingTalkErrorPayloadLog, maskSensitiveData } from "./utils";

const DEFAULT_PROACTIVE_HINT_COOLDOWN_HOURS = 24;
const MIN_THINKING_REACTION_VISIBLE_MS = 1200;
const ATTACHMENT_TEXT_PREFIX = "[附件内容摘录]";
const proactiveHintLastSentAt = new Map<string, number>();

export function resetProactivePermissionHintStateForTest(): void {
  proactiveHintLastSentAt.clear();
  clearProactiveRiskObservationsForTest();
}

function shouldSendProactivePermissionHint(params: {
  isDirect: boolean;
  accountId: string;
  senderId: string;
  senderOriginalId?: string;
  senderStaffId?: string;
  config: DingTalkConfig;
  nowMs: number;
}): boolean {
  if (!params.isDirect) {
    return false;
  }

  const hintConfig = params.config.proactivePermissionHint;
  if (hintConfig?.enabled === false) {
    return false;
  }

  const targetId = (params.senderId || "").trim();
  if (!targetId) {
    return false;
  }

  const riskTargets = [params.senderId, params.senderOriginalId, params.senderStaffId]
    .map((id) => (id || "").trim())
    .filter((id, index, arr) => Boolean(id) && arr.indexOf(id) === index);
  if (riskTargets.length === 0) {
    return false;
  }

  const riskObservation = getProactiveRiskObservationForAny(params.accountId, riskTargets, params.nowMs);
  if (!riskObservation || riskObservation.source !== "proactive-api") {
    return false;
  }

  const cooldownHours =
    hintConfig?.cooldownHours && hintConfig.cooldownHours > 0
      ? hintConfig.cooldownHours
      : DEFAULT_PROACTIVE_HINT_COOLDOWN_HOURS;
  const cooldownMs = cooldownHours * 60 * 60 * 1000;
  const key = `${params.accountId}:${targetId}`;
  const lastSentAt = proactiveHintLastSentAt.get(key) || 0;
  if (params.nowMs - lastSentAt < cooldownMs) {
    return false;
  }

  proactiveHintLastSentAt.set(key, params.nowMs);
  return true;
}

function stripQuotedPrefixForJournal(value: string): string {
  return value
    .replace(/^\[引用消息: .*?\]\n\n/s, "")
    .replace(/^\[这是一条引用消息，原消息ID: .*?\]\n\n/s, "")
    .trim();
}

function sanitizeGroupPromptName(value?: string): string {
  return (value || "")
    .replace(/[\r\n,=]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function buildGroupTurnContextPrompt(params: {
  conversationId: string;
  senderDingtalkId: string;
  senderName?: string;
}): string {
  const sanitizedSenderName = sanitizeGroupPromptName(params.senderName) || "Unknown";
  return [
    "Current DingTalk group turn context:",
    `- conversationId: ${params.conversationId}`,
    `- senderDingtalkId: ${params.senderDingtalkId}`,
    `- senderName: ${sanitizedSenderName}`,
    "Treat senderDingtalkId and senderName as the authoritative sender for this turn. Do not guess the current sender from GroupMembers.",
  ].join("\n");
}

type ReplyStreamPayload = {
  text?: string;
};

type ReplyChunkInfo = {
  kind?: string;
};

/**
 * Download DingTalk media file via runtime media service (sandbox-compatible).
 * Files are stored in the global media inbound directory.
 */
export async function downloadMedia(
  config: DingTalkConfig,
  downloadCode: string,
  log?: any,
): Promise<MediaFile | null> {
  const rt = getDingTalkRuntime();
  const formatAxiosErrorData = (value: unknown): string | undefined => {
    if (value === null || value === undefined) {
      return undefined;
    }
    if (Buffer.isBuffer(value)) {
      return `<buffer ${value.length} bytes>`;
    }
    if (value instanceof ArrayBuffer) {
      return `<arraybuffer ${value.byteLength} bytes>`;
    }
    if (typeof value === "string") {
      return value.length > 500 ? `${value.slice(0, 500)}…` : value;
    }
    try {
      return JSON.stringify(maskSensitiveData(value));
    } catch {
      return String(value);
    }
  };

  if (!downloadCode) {
    log?.error?.("[DingTalk] downloadMedia requires downloadCode to be provided.");
    return null;
  }
  if (!config.robotCode) {
    if (log?.error) {
      log.error("[DingTalk] downloadMedia requires robotCode to be configured.");
    }
    return null;
  }
  try {
    const token = await getAccessToken(config, log);
    const response = await axios.post(
      "https://api.dingtalk.com/v1.0/robot/messageFiles/download",
      { downloadCode, robotCode: config.robotCode },
      { headers: { "x-acs-dingtalk-access-token": token } },
    );
    const payload = response.data as Record<string, any>;
    const downloadUrl = payload?.downloadUrl ?? payload?.data?.downloadUrl;
    if (!downloadUrl) {
      const payloadDetail = formatAxiosErrorData(payload);
      log?.error?.(
        `[DingTalk] downloadMedia missing downloadUrl. payload=${payloadDetail ?? "unknown"}`,
      );
      return null;
    }
    const mediaResponse = await axios.get(downloadUrl, { responseType: "arraybuffer" });
    const contentType = mediaResponse.headers["content-type"] || "application/octet-stream";
    const buffer = Buffer.from(mediaResponse.data as ArrayBuffer);

    const maxBytes =
      config.mediaMaxMb && config.mediaMaxMb > 0 ? config.mediaMaxMb * 1024 * 1024 : undefined;
    const saved = maxBytes
      ? await rt.channel.media.saveMediaBuffer(buffer, contentType, "inbound", maxBytes)
      : await rt.channel.media.saveMediaBuffer(buffer, contentType, "inbound");
    log?.debug?.(`[DingTalk] Media saved: ${saved.path}`);
    return { path: saved.path, mimeType: saved.contentType ?? contentType };
  } catch (err: any) {
    if (log?.error) {
      if (axios.isAxiosError(err)) {
        const status = err.response?.status;
        const statusText = err.response?.statusText;
        const dataDetail = formatAxiosErrorData(err.response?.data);
        const code = err.code ? ` code=${err.code}` : "";
        const statusLabel = status ? ` status=${status}${statusText ? ` ${statusText}` : ""}` : "";
        log.error(
          `[DingTalk] Failed to download media:${statusLabel}${code} message=${err.message}`,
        );
        if (err.response?.data !== undefined) {
          log.error(formatDingTalkErrorPayloadLog("inbound.downloadMedia", err.response.data));
        } else if (dataDetail) {
          log.error(`[DingTalk] downloadMedia response data: ${dataDetail}`);
        }
      } else {
        log.error(`[DingTalk] Failed to download media: ${err.message}`);
      }
    }
    return null;
  }
}

export async function handleDingTalkMessage(params: HandleDingTalkMessageParams): Promise<void> {
  const { cfg, accountId, data, sessionWebhook, log, dingtalkConfig } = params;
  const rt = getDingTalkRuntime();

  // Save logger globally so shared services can log consistently without threading log everywhere.
  setCurrentLogger(log);

  log?.debug?.("[DingTalk] Full Inbound Data: " + JSON.stringify(maskSensitiveData(data)));

  // 1) Ignore self messages from bot.
  if (data.senderId === data.chatbotUserId || data.senderStaffId === data.chatbotUserId) {
    log?.debug?.("[DingTalk] Ignoring robot self-message");
    return;
  }

  const extractedContent = extractMessageContent(data);
  if (!extractedContent.text) {
    return;
  }

  const isDirect = data.conversationType === "1";
  const senderOriginalId = (data.senderId || "").trim();
  const senderStaffId = (data.senderStaffId || "").trim();
  const senderId = senderStaffId || senderOriginalId;
  const senderName = data.senderNick || "Unknown";
  const groupId = data.conversationId;
  const groupName = data.conversationTitle || "Group";

  // Register original peer IDs to preserve case-sensitive DingTalk conversation IDs.
  if (groupId) {
    registerPeerId(groupId);
  }

  if (
    shouldSendProactivePermissionHint({
      isDirect,
      accountId,
      senderId,
      senderOriginalId,
      senderStaffId,
      config: dingtalkConfig,
      nowMs: Date.now(),
    })
  ) {
    try {
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        "⚠️ 主动推送可能失败\n\n检测到该用户最近一次主动发送调用返回了权限或目标不可达错误。当前会话回复仍可正常使用，但定时/主动发送可能失败。\n\n建议：\n1) 在钉钉开放平台确认应用已申请并获得主动发送相关权限\n2) 确认目标用户属于当前企业并在应用可见范围内\n3) 使用相同账号进行一次主动发送验证并检查错误码详情",
        { log },
      );
    } catch (err: any) {
      log?.debug?.(`[DingTalk] Failed to send proactive permission hint: ${err.message}`);
      if (err?.response?.data !== undefined) {
        log?.debug?.(formatDingTalkErrorPayloadLog("inbound.proactivePermissionHint", err.response.data));
      }
    }
  }
  if (senderId) {
    registerPeerId(senderId);
  }

  // 2) Authorization guard (DM/group policy).
  let commandAuthorized = true;
  if (isDirect) {
    const dmPolicy = dingtalkConfig.dmPolicy || "open";
    const allowFrom = dingtalkConfig.allowFrom || [];

    if (dmPolicy === "allowlist") {
      const normalizedAllowFrom = normalizeAllowFrom(allowFrom);
      const isAllowed = isSenderAllowed({ allow: normalizedAllowFrom, senderId });

      if (!isAllowed) {
        log?.debug?.(
          `[DingTalk] DM blocked: senderId=${senderId} not in allowlist (dmPolicy=allowlist)`,
        );
        try {
          await sendBySession(
            dingtalkConfig,
            sessionWebhook,
            `⛔ 访问受限\n\n您的用户ID：\`${senderId}\`\n\n请联系管理员将此ID添加到允许列表中。`,
            { log },
          );
        } catch (err: any) {
          log?.debug?.(`[DingTalk] Failed to send access denied message: ${err.message}`);
          if (err?.response?.data !== undefined) {
            log?.debug?.(formatDingTalkErrorPayloadLog("inbound.accessDeniedReply", err.response.data));
          }
        }

        return;
      }

      log?.debug?.(`[DingTalk] DM authorized: senderId=${senderId} in allowlist`);
    } else if (dmPolicy === "pairing") {
      // SDK pairing flow performs actual authorization checks.
      commandAuthorized = true;
    } else {
      commandAuthorized = true;
    }
  } else {
    const groupPolicy = dingtalkConfig.groupPolicy || "open";
    const allowFrom = dingtalkConfig.allowFrom || [];

    if (groupPolicy === "allowlist") {
      const normalizedAllowFrom = normalizeAllowFrom(allowFrom);
      const isAllowed = isSenderGroupAllowed({ allow: normalizedAllowFrom, groupId });

      if (!isAllowed) {
        log?.debug?.(
          `[DingTalk] Group blocked: conversationId=${groupId} senderId=${senderId} not in allowlist (groupPolicy=allowlist)`,
        );

        try {
          await sendBySession(
            dingtalkConfig,
            sessionWebhook,
            `⛔ 访问受限\n\n您的群聊ID：\`${groupId}\`\n\n请联系管理员将此ID添加到允许列表中。`,
            { log, atUserId: senderId },
          );
        } catch (err: any) {
          log?.debug?.(`[DingTalk] Failed to send group access denied message: ${err.message}`);
          if (err?.response?.data !== undefined) {
            log?.debug?.(
              formatDingTalkErrorPayloadLog("inbound.groupAccessDeniedReply", err.response.data),
            );
          }
        }

        return;
      }

      log?.debug?.(
        `[DingTalk] Group authorized: conversationId=${groupId} senderId=${senderId} in allowlist`,
      );
    }
  }

  const accountStorePath = rt.channel.session.resolveStorePath(cfg.session?.store, {
    agentId: accountId,
  });
  const currentSessionSourceKind = isDirect ? "direct" : "group";
  const currentSessionSourceId = isDirect ? senderId : groupId;
  const peerIdOverride = getSessionPeerOverride({
    storePath: accountStorePath,
    accountId,
    sourceKind: currentSessionSourceKind,
    sourceId: currentSessionSourceId,
  });
  const sessionPeer = resolveDingTalkSessionPeer({
    isDirect,
    senderId,
    conversationId: groupId,
    peerIdOverride,
    config: dingtalkConfig,
  });
  const route = rt.channel.routing.resolveAgentRoute({
    cfg,
    channel: "dingtalk",
    accountId,
    peer: { kind: sessionPeer.kind, id: sessionPeer.peerId },
  });

  // Route resolved before media download for session context and routing metadata.
  const storePath = rt.channel.session.resolveStorePath(cfg.session?.store, {
    agentId: route.agentId,
  });

  const to = isDirect ? senderId : groupId;
  const parsedLearnCommand = parseLearnCommand(extractedContent.text);
  const parsedSessionCommand = parseSessionCommand(extractedContent.text);
  const isOwner = isLearningOwner({
    cfg,
    config: dingtalkConfig,
    senderId,
    rawSenderId: data.senderId,
  });
  if (isDirect && parsedLearnCommand.scope === "whoami") {
    await sendBySession(
      dingtalkConfig,
      sessionWebhook,
      formatWhoAmIReply({
        senderId,
        rawSenderId: data.senderId,
        senderStaffId: data.senderStaffId,
        isOwner,
      }),
      { log },
    );
    return;
  }
  if (parsedLearnCommand.scope === "whereami") {
    await sendBySession(
      dingtalkConfig,
      sessionWebhook,
      formatWhereAmIReply({
        conversationId: data.conversationId,
        conversationType: isDirect ? "dm" : "group",
        peerId: sessionPeer.peerId,
      }),
      { log },
    );
    return;
  }
  if (isDirect && parsedLearnCommand.scope === "owner-status") {
    await sendBySession(
      dingtalkConfig,
      sessionWebhook,
      formatOwnerStatusReply({
        senderId,
        rawSenderId: data.senderId,
        isOwner,
      }),
      { log },
    );
    return;
  }
  if (parsedLearnCommand.scope === "help") {
    await sendBySession(dingtalkConfig, sessionWebhook, formatLearnCommandHelp(), { log });
    return;
  }
  if (
    (parsedLearnCommand.scope === "global"
      || parsedLearnCommand.scope === "session"
      || parsedLearnCommand.scope === "here"
      || parsedLearnCommand.scope === "target"
      || parsedLearnCommand.scope === "targets"
      || parsedLearnCommand.scope === "list"
      || parsedLearnCommand.scope === "disable"
      || parsedLearnCommand.scope === "delete"
      || parsedLearnCommand.scope === "target-set-create"
      || parsedLearnCommand.scope === "target-set-apply"
      || parsedSessionCommand.scope === "session-alias-show"
      || parsedSessionCommand.scope === "session-alias-set"
      || parsedSessionCommand.scope === "session-alias-clear"
      || parsedSessionCommand.scope === "session-alias-bind"
      || parsedSessionCommand.scope === "session-alias-unbind")
    && !isOwner
  ) {
    await sendBySession(dingtalkConfig, sessionWebhook, formatOwnerOnlyDeniedReply(), { log });
    return;
  }
  if (isOwner) {
    if (parsedSessionCommand.scope === "session-alias-show") {
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        formatSessionAliasReply({
          sourceKind: currentSessionSourceKind,
          sourceId: currentSessionSourceId,
          peerId: sessionPeer.peerId,
          aliasSource: peerIdOverride ? "override" : "default",
        }),
        { log },
      );
      return;
    }
    if (parsedSessionCommand.scope === "session-alias-set" && parsedSessionCommand.peerId) {
      const aliasValidationError = validateSessionAlias(parsedSessionCommand.peerId);
      if (aliasValidationError) {
        await sendBySession(
          dingtalkConfig,
          sessionWebhook,
          formatSessionAliasValidationErrorReply(aliasValidationError),
          { log },
        );
        return;
      }
      setSessionPeerOverride({
        storePath: accountStorePath,
        accountId,
        sourceKind: currentSessionSourceKind,
        sourceId: currentSessionSourceId,
        peerId: parsedSessionCommand.peerId,
      });
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        formatSessionAliasSetReply({
          sourceKind: currentSessionSourceKind,
          sourceId: currentSessionSourceId,
          peerId: parsedSessionCommand.peerId,
        }),
        { log },
      );
      return;
    }
    if (parsedSessionCommand.scope === "session-alias-clear") {
      clearSessionPeerOverride({
        storePath: accountStorePath,
        accountId,
        sourceKind: currentSessionSourceKind,
        sourceId: currentSessionSourceId,
      });
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        formatSessionAliasClearedReply({
          sourceKind: currentSessionSourceKind,
          sourceId: currentSessionSourceId,
        }),
        { log },
      );
      return;
    }
    if (parsedSessionCommand.scope === "session-alias-bind"
      && parsedSessionCommand.sourceKind
      && parsedSessionCommand.sourceId
      && parsedSessionCommand.peerId) {
      const aliasValidationError = validateSessionAlias(parsedSessionCommand.peerId);
      if (aliasValidationError) {
        await sendBySession(
          dingtalkConfig,
          sessionWebhook,
          formatSessionAliasValidationErrorReply(aliasValidationError),
          { log },
        );
        return;
      }
      setSessionPeerOverride({
        storePath: accountStorePath,
        accountId,
        sourceKind: parsedSessionCommand.sourceKind,
        sourceId: parsedSessionCommand.sourceId,
        peerId: parsedSessionCommand.peerId,
      });
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        formatSessionAliasBoundReply({
          sourceKind: parsedSessionCommand.sourceKind,
          sourceId: parsedSessionCommand.sourceId,
          peerId: parsedSessionCommand.peerId,
        }),
        { log },
      );
      return;
    }
    if (parsedSessionCommand.scope === "session-alias-unbind"
      && parsedSessionCommand.sourceKind
      && parsedSessionCommand.sourceId) {
      const existed = clearSessionPeerOverride({
        storePath: accountStorePath,
        accountId,
        sourceKind: parsedSessionCommand.sourceKind,
        sourceId: parsedSessionCommand.sourceId,
      });
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        formatSessionAliasUnboundReply({
          sourceKind: parsedSessionCommand.sourceKind,
          sourceId: parsedSessionCommand.sourceId,
          existed,
        }),
        { log },
      );
      return;
    }
    if (parsedLearnCommand.scope === "global" && parsedLearnCommand.instruction) {
      const applied = applyManualGlobalLearningRule({
        storePath: accountStorePath,
        accountId,
        instruction: parsedLearnCommand.instruction,
      });
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        formatLearnAppliedReply({
          scope: "global",
          instruction: parsedLearnCommand.instruction,
          ruleId: applied?.ruleId,
        }),
        { log },
      );
      return;
    }
    if (parsedLearnCommand.scope === "session" && parsedLearnCommand.instruction) {
      applyManualSessionLearningNote({
        storePath: accountStorePath,
        accountId,
        targetId: data.conversationId,
        instruction: parsedLearnCommand.instruction,
      });
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        formatLearnAppliedReply({
          scope: "session",
          instruction: parsedLearnCommand.instruction,
        }),
        { log },
      );
      return;
    }
    if (parsedLearnCommand.scope === "here" && parsedLearnCommand.instruction) {
      const applied = applyManualTargetLearningRule({
        storePath: accountStorePath,
        accountId,
        targetId: data.conversationId,
        instruction: parsedLearnCommand.instruction,
      });
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        formatLearnAppliedReply({
          scope: "target",
          targetId: data.conversationId,
          instruction: parsedLearnCommand.instruction,
          ruleId: applied?.ruleId,
        }),
        { log },
      );
      return;
    }
    if (parsedLearnCommand.scope === "target" && parsedLearnCommand.targetId && parsedLearnCommand.instruction) {
      const applied = applyManualTargetLearningRule({
        storePath: accountStorePath,
        accountId,
        targetId: parsedLearnCommand.targetId,
        instruction: parsedLearnCommand.instruction,
      });
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        formatLearnAppliedReply({
          scope: "target",
          targetId: parsedLearnCommand.targetId,
          instruction: parsedLearnCommand.instruction,
          ruleId: applied?.ruleId,
        }),
        { log },
      );
      return;
    }
    if (parsedLearnCommand.scope === "targets" && parsedLearnCommand.targetIds?.length && parsedLearnCommand.instruction) {
      const applied = applyManualTargetsLearningRule({
        storePath: accountStorePath,
        accountId,
        targetIds: parsedLearnCommand.targetIds,
        instruction: parsedLearnCommand.instruction,
      });
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        formatLearnAppliedReply({
          scope: "targets",
          targetIds: parsedLearnCommand.targetIds,
          instruction: parsedLearnCommand.instruction,
          ruleId: applied[0]?.ruleId,
        }),
        { log },
      );
      return;
    }
    if (parsedLearnCommand.scope === "target-set-create" && parsedLearnCommand.setName && parsedLearnCommand.targetIds?.length) {
      const saved = createOrUpdateTargetSet({
        storePath: accountStorePath,
        accountId,
        name: parsedLearnCommand.setName,
        targetIds: parsedLearnCommand.targetIds,
      });
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        saved
          ? formatTargetSetSavedReply({
            setName: parsedLearnCommand.setName,
            targetIds: parsedLearnCommand.targetIds,
          })
          : "目标组保存失败，请检查名称和目标列表。",
        { log },
      );
      return;
    }
    if (parsedLearnCommand.scope === "target-set-apply" && parsedLearnCommand.setName && parsedLearnCommand.instruction) {
      const applied = applyTargetSetLearningRule({
        storePath: accountStorePath,
        accountId,
        name: parsedLearnCommand.setName,
        instruction: parsedLearnCommand.instruction,
      });
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        applied.length > 0
          ? formatLearnAppliedReply({
            scope: "target-set",
            setName: parsedLearnCommand.setName,
            targetIds: applied.map((item) => item.targetId),
            instruction: parsedLearnCommand.instruction,
            ruleId: applied[0]?.ruleId,
          })
          : `未找到目标组 \`${parsedLearnCommand.setName}\`，或该目标组为空。`,
        { log },
      );
      return;
    }
    if (parsedLearnCommand.scope === "list") {
      const rules = listScopedLearningRules({ storePath: accountStorePath, accountId })
        .slice(0, 20)
        .map((rule) => {
          const scope = rule.scope === "target" ? `target(${rule.targetId})` : "global";
          const status = rule.enabled ? "enabled" : "disabled";
          return `- [${scope}] ${rule.ruleId} (${status}) => ${rule.instruction}`;
        });
      const targetSets = listLearningTargetSets({ storePath: accountStorePath, accountId })
        .slice(0, 10)
        .map((targetSet) => `- [target-set] ${targetSet.name} => ${targetSet.targetIds.join(", ")}`);
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        formatLearnListReply([...rules, ...targetSets]),
        { log },
      );
      return;
    }
    if (parsedLearnCommand.scope === "disable" && parsedLearnCommand.ruleId) {
      const result = disableManualRule({
        storePath: accountStorePath,
        accountId,
        ruleId: parsedLearnCommand.ruleId,
      });
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        formatLearnDisabledReply({
          ruleId: parsedLearnCommand.ruleId,
          existed: result.existed,
          scope: result.scope,
          targetId: result.targetId,
        }),
        { log },
      );
      return;
    }
    if (parsedLearnCommand.scope === "delete" && parsedLearnCommand.ruleId) {
      const result = deleteManualRule({
        storePath: accountStorePath,
        accountId,
        ruleId: parsedLearnCommand.ruleId,
      });
      await sendBySession(
        dingtalkConfig,
        sessionWebhook,
        formatLearnDeletedReply({
          ruleId: parsedLearnCommand.ruleId,
          existed: result.existed,
          scope: result.scope,
          targetId: result.targetId,
        }),
        { log },
      );
      return;
    }
  }
  const manualForcedReply = resolveManualForcedReply({
    storePath: accountStorePath,
    accountId,
    targetId: data.conversationId,
    content: extractedContent,
  });
  if (manualForcedReply) {
    await sendBySession(dingtalkConfig, sessionWebhook, manualForcedReply, { log });
    return;
  }
  // 3) Select response mode (card vs markdown).
  // Card creation runs BEFORE media download so the user sees immediate visual
  // feedback while large files are still being downloaded.
  let useCardMode = dingtalkConfig.messageType === "card";
  let currentAICard = undefined;

  if (useCardMode) {
    try {
      log?.debug?.(
        `[DingTalk][AICard] conversationType=${data.conversationType}, conversationId=${to}`,
      );
      const aiCard = await createAICard(dingtalkConfig, to, log, {
        accountId,
        storePath: accountStorePath,
      });
      if (aiCard) {
        currentAICard = aiCard;
      } else {
        useCardMode = false;
        log?.warn?.(
          "[DingTalk] Failed to create AI card (returned null), fallback to text/markdown.",
        );
      }
    } catch (err: any) {
      useCardMode = false;
      log?.warn?.(
        `[DingTalk] Failed to create AI card: ${err.message}, fallback to text/markdown.`,
      );
    }
  }

  const hasConcreteQuotedPayload =
    !!extractedContent.quoted?.mediaDownloadCode ||
    !!extractedContent.quoted?.isQuotedFile ||
    !!extractedContent.quoted?.isQuotedCard ||
    extractedContent.quoted?.prefix.startsWith('[引用消息: "') === true;
  const journalTTLDays = dingtalkConfig.journalTTLDays ?? DEFAULT_JOURNAL_TTL_DAYS;
  let content = extractedContent;

  if (data.text?.isReplyMsg && data.originalMsgId && !hasConcreteQuotedPayload) {
    try {
      const quoted = resolveQuotedMessageById({
        storePath,
        accountId,
        conversationId: groupId,
        originalMsgId: data.originalMsgId,
        ttlDays: journalTTLDays,
      });
      if (quoted?.text?.trim()) {
        const cleanedText = extractedContent.text.replace(
          /^\[这是一条引用消息，原消息ID: [^\]]+\]\n\n/,
          "",
        );
        content = {
          ...extractedContent,
          text: `[引用消息: "${quoted.text.trim()}"]\n\n${cleanedText}`,
        };
      }
    } catch (err) {
      log?.debug?.(`[DingTalk] Quote journal lookup failed: ${String(err)}`);
    }
  }

  try {
    appendQuoteJournalEntry({
      storePath,
      accountId,
      conversationId: groupId,
      msgId: data.msgId,
      messageType: content.messageType,
      text: stripQuotedPrefixForJournal(content.text),
      createdAt: data.createAt,
      ttlDays: journalTTLDays,
    });
  } catch (err) {
    log?.warn?.(`[DingTalk] Quote journal append failed: ${String(err)}`);
  }

  let mediaPath: string | undefined;
  let mediaType: string | undefined;
  if (content.mediaPath && dingtalkConfig.robotCode) {
    const media = await downloadMedia(dingtalkConfig, content.mediaPath, log);
    if (media) {
      mediaPath = media.path;
      mediaType = media.mimeType;
    }
  }

  // Cache downloadCode (+ spaceId/fileId) for quoted file lookups (DM + group).
  if (content.mediaPath && data.msgId) {
    cacheInboundDownloadCode(
      accountId,
      data.conversationId,
      data.msgId,
      content.mediaPath,
      content.messageType,
      data.createAt,
      { spaceId: data.content?.spaceId, fileId: data.content?.fileId, storePath },
    );
  }

  // User-sent DingTalk doc / Drive file card: cache msgId -> {spaceId,fileId}
  // during the original message turn, and try downloading immediately in DM.
  if (
    content.messageType === "interactiveCardFile" &&
    data.msgId &&
    content.docSpaceId &&
    content.docFileId
  ) {
    cacheInboundDownloadCode(
      accountId,
      data.conversationId,
      data.msgId,
      undefined,
      content.messageType,
      data.createAt,
      { spaceId: content.docSpaceId, fileId: content.docFileId, storePath },
    );

    if (!mediaPath && isDirect && data.senderStaffId) {
      try {
        const unionId = await getUnionIdByStaffId(dingtalkConfig, data.senderStaffId, log);
        const docMedia = await downloadGroupFile(
          dingtalkConfig,
          content.docSpaceId,
          content.docFileId,
          unionId,
          log,
        );
        if (docMedia) {
          mediaPath = docMedia.path;
          mediaType = docMedia.mimeType;
        }
      } catch (err: any) {
        log?.warn?.(`[DingTalk] Doc card download failed: ${err.message}`);
      }
    }
  }

  // Try downloading a quoted file from cached downloadCode/spaceId+fileId.
  const tryDownloadFromCache = async (
    quotedMsgId: string | undefined,
  ): Promise<MediaFile | null> => {
    if (!quotedMsgId) {
      return null;
    }
    const cached = getCachedDownloadCode(accountId, data.conversationId, quotedMsgId, storePath);
    if (!cached) {
      return null;
    }
    let media: MediaFile | null = null;
    if (cached.downloadCode) {
      media = await downloadMedia(dingtalkConfig, cached.downloadCode, log);
    }
    if (!media && cached.spaceId && cached.fileId && data.senderStaffId) {
      try {
        const unionId = await getUnionIdByStaffId(dingtalkConfig, data.senderStaffId, log);
        media = await downloadGroupFile(
          dingtalkConfig,
          cached.spaceId,
          cached.fileId,
          unionId,
          log,
        );
      } catch (err: any) {
        log?.warn?.(`[DingTalk] spaceId+fileId fallback failed: ${err.message}`);
      }
    }
    return media;
  };

  // Quoted picture: download via existing downloadMedia.
  if (!mediaPath && content.quoted?.mediaDownloadCode && dingtalkConfig.robotCode) {
    const media = await downloadMedia(dingtalkConfig, content.quoted.mediaDownloadCode, log);
    if (media) {
      mediaPath = media.path;
      mediaType = media.mimeType;
    } else {
      content.text = content.text.replace(
        content.quoted.prefix,
        "[引用了一张图片，但下载失败]\n\n",
      );
    }
  }

  // Quoted file/video/audio (unknownMsgType): cache-first, then group file API fallback.
  if (!mediaPath && content.quoted?.isQuotedFile) {
    let fileResolved = false;

    // Step 1: Try msgId-based cache (works for both DM and group if bot saw the original message).
    const cachedMedia = await tryDownloadFromCache(content.quoted.msgId);
    if (cachedMedia) {
      mediaPath = cachedMedia.path;
      mediaType = cachedMedia.mimeType;
      fileResolved = true;
    }

    // Step 2 (group only): Cache miss → fall back to group file API time-based matching.
    if (!fileResolved && !isDirect) {
      const resolved = await resolveQuotedFile(
        dingtalkConfig,
        {
          openConversationId: data.conversationId,
          senderStaffId: data.senderStaffId,
          fileCreatedAt: content.quoted.fileCreatedAt,
        },
        log,
      );
      if (resolved) {
        mediaPath = resolved.media.path;
        mediaType = resolved.media.mimeType;
        fileResolved = true;
        if (content.quoted.msgId) {
          cacheInboundDownloadCode(
            accountId,
            data.conversationId,
            content.quoted.msgId,
            undefined,
            "file",
            content.quoted.fileCreatedAt || Date.now(),
            { storePath, spaceId: resolved.spaceId, fileId: resolved.fileId },
          );
        }
      }
    }

    if (!fileResolved) {
      log?.warn?.(
        `[DingTalk] Quoted file unresolved: conversationType=${data.conversationType} conversationId=${data.conversationId} quotedMsgId=${content.quoted.msgId || "(none)"}`,
      );
      const hint = isDirect
        ? "[引用了一个文件，内容无法自动获取，请直接发送该文件]\n\n"
        : "[引用了一个文件，但无法获取内容]\n\n";
      content.text = content.text.replace(content.quoted.prefix, hint);
    }
  }

  // Quoted DingTalk doc / Drive file card:
  // 1) Prefer msgId-based cached metadata captured when the original doc card
  //    message was seen.
  // 2) In group chats, if the bot never saw the original doc card message,
  //    reuse the same group-file fallback chain as ordinary quoted files.
  if (!mediaPath && content.quoted?.isQuotedDocCard) {
    let docResolved = false;

    const cachedDocMedia = await tryDownloadFromCache(content.quoted.msgId);
    if (cachedDocMedia) {
      mediaPath = cachedDocMedia.path;
      mediaType = cachedDocMedia.mimeType;
      docResolved = true;
      content.text = content.text.replace(content.quoted.prefix, "[引用了钉钉文档]\n\n");
    }

    if (!docResolved && !isDirect && content.quoted.fileCreatedAt) {
      const resolved = await resolveQuotedFile(
        dingtalkConfig,
        {
          openConversationId: data.conversationId,
          senderStaffId: data.senderStaffId,
          fileCreatedAt: content.quoted.fileCreatedAt,
        },
        log,
      );
      if (resolved) {
        mediaPath = resolved.media.path;
        mediaType = resolved.media.mimeType;
        docResolved = true;
        content.text = content.text.replace(content.quoted.prefix, "[引用了钉钉文档]\n\n");
        if (content.quoted.msgId) {
          cacheInboundDownloadCode(
            accountId,
            data.conversationId,
            content.quoted.msgId,
            undefined,
            "interactiveCardFile",
            content.quoted.fileCreatedAt || Date.now(),
            { storePath, spaceId: resolved.spaceId, fileId: resolved.fileId },
          );
        }
      }
    }

    if (!docResolved) {
      log?.warn?.(
        `[DingTalk] Quoted doc card unresolved: conversationType=${data.conversationType} conversationId=${data.conversationId} quotedMsgId=${content.quoted.msgId || "(none)"}`,
      );
      const hint = isDirect
        ? "[引用了钉钉文档，内容无法自动获取，请直接发送该文档]\n\n"
        : "[引用了钉钉文档，但无法获取内容]\n\n";
      content.text = content.text.replace(content.quoted.prefix, hint);
    }
  }

  // Quoted AI card: prefer deterministic processQueryKey lookup, and only
  // fall back to the legacy createdAt matcher when the callback omits that key.
  if (content.quoted?.isQuotedCard) {
    const cardContent = content.quoted.processQueryKey
      ? getCardContentByProcessQueryKey(
          accountId,
          to,
          content.quoted.processQueryKey,
          accountStorePath,
        )
      : content.quoted.cardCreatedAt
        ? findCardContent(accountId, to, content.quoted.cardCreatedAt, accountStorePath)
        : null;
    if (cardContent) {
      const preview = cardContent.length > 50 ? cardContent.slice(0, 50) + "..." : cardContent;
      content.text = content.text.replace(
        content.quoted.prefix,
        `[引用机器人回复: "${preview}"]\n\n`,
      );
    }
    // Card cache miss: prefix already contains "[引用了机器人的回复]", keep as-is.
  }

  let attachmentExtractedText: string | undefined;
  if (mediaPath) {
    try {
      const extracted = await extractAttachmentText({
        path: mediaPath,
        mimeType: mediaType,
        fileName: data.content?.fileName,
      });
      if (extracted?.text) {
        attachmentExtractedText = `${ATTACHMENT_TEXT_PREFIX}\n${extracted.text}`;
      }
    } catch (err: any) {
      log?.warn?.(`[DingTalk] Failed to extract attachment text: ${err.message}`);
    }
  }

  const inboundBody =
    mediaPath && /<media:[^>]+>/.test(content.text)
      ? `${content.text}\n[media_path: ${mediaPath}]\n[media_type: ${mediaType || "unknown"}]`
      : content.text;
  const inboundText = attachmentExtractedText ? `${inboundBody}\n\n${attachmentExtractedText}` : inboundBody;
  const learningEnabled = isFeedbackLearningEnabled(dingtalkConfig);
  const learningContextBlock = buildLearningContextBlock({
    enabled: learningEnabled,
    storePath: accountStorePath,
    accountId,
    targetId: data.conversationId,
    content,
  });
  const envelopeOptions = rt.channel.reply.resolveEnvelopeFormatOptions(cfg);
  const previousTimestamp = rt.channel.session.readSessionUpdatedAt({
    storePath,
    sessionKey: route.sessionKey,
  });

  const groupConfig = !isDirect ? resolveGroupConfig(dingtalkConfig, groupId) : undefined;
  // GroupSystemPrompt is injected every turn (not only first-turn intro).
  const groupSystemPromptParts = !isDirect
    ? [
        buildGroupTurnContextPrompt({
          conversationId: groupId,
          senderDingtalkId: senderId,
          senderName,
        }),
        groupConfig?.systemPrompt?.trim(),
      ]
    : [];
  const extraSystemPrompt =
    [...groupSystemPromptParts, learningContextBlock].filter(Boolean).join("\n\n") || undefined;

  if (!isDirect) {
    noteGroupMember(storePath, groupId, senderId, senderName);
  }
  const groupMembers = !isDirect ? formatGroupMembers(storePath, groupId) : undefined;

  const fromLabel = isDirect ? `${senderName} (${senderId})` : `${groupName} - ${senderName}`;
  const body = rt.channel.reply.formatInboundEnvelope({
    channel: "DingTalk",
    from: fromLabel,
    timestamp: data.createAt,
    body: inboundText,
    chatType: isDirect ? "direct" : "group",
    sender: { name: senderName, id: senderId },
    previousTimestamp,
    envelope: envelopeOptions,
  });

  const ctx = rt.channel.reply.finalizeInboundContext({
    Body: body,
    RawBody: inboundText,
    CommandBody: inboundText,
    From: to,
    To: to,
    SessionKey: route.sessionKey,
    AccountId: accountId,
    ChatType: isDirect ? "direct" : "group",
    ConversationLabel: fromLabel,
    GroupSubject: isDirect ? undefined : groupName,
    SenderName: senderName,
    SenderId: senderId,
    Provider: "dingtalk",
    Surface: "dingtalk",
    MessageSid: data.msgId,
    Timestamp: data.createAt,
    MediaPath: mediaPath,
    MediaType: mediaType,
    MediaUrl: mediaPath,
    GroupMembers: groupMembers,
    GroupSystemPrompt: extraSystemPrompt,
    GroupChannel: isDirect ? undefined : route.sessionKey,
    CommandAuthorized: commandAuthorized,
    OriginatingChannel: "dingtalk",
    OriginatingTo: to,
  });

  await rt.channel.session.recordInboundSession({
    storePath,
    sessionKey: ctx.SessionKey || route.sessionKey,
    ctx,
    updateLastRoute: { sessionKey: route.mainSessionKey, channel: "dingtalk", to, accountId },
    onRecordError: (err: unknown) => {
      log?.error?.(`[DingTalk] Failed to record inbound session: ${String(err)}`);
    },
  });

  log?.info?.(`[DingTalk] Inbound: from=${senderName} text="${content.text.slice(0, 50)}..."`);

  const ackReaction =
    typeof dingtalkConfig.ackReaction === "string"
      ? dingtalkConfig.ackReaction.trim()
      : resolveAckReactionSetting({
          cfg,
          accountId,
          agentId: route.agentId,
        });
  const resolvedAckReaction =
    ackReaction === "emoji" ? classifyAckReactionEmoji(content.text).emoji : ackReaction;
  const shouldAttachAckReaction = Boolean(resolvedAckReaction);
  let ackReactionAttached = false;
  let ackReactionAttachedAt = 0;

  if (shouldAttachAckReaction) {
    ackReactionAttached = await attachNativeAckReaction(
      dingtalkConfig,
      {
        msgId: data.msgId,
        conversationId: groupId,
        reactionName: resolvedAckReaction,
      },
      log,
    );
    if (ackReactionAttached) {
      ackReactionAttachedAt = Date.now();
    }
  }

  // Serialize dispatchReply + card finalize per session to prevent the runtime
  // from receiving concurrent dispatch calls on the same session key, which
  // causes empty replies for all but the first caller.
  const releaseSessionLock = await acquireSessionLock(route.sessionKey);
  try {
    if (!ackReactionAttached && shouldAttachAckReaction) {
      log?.debug?.("[DingTalk] Native ack reaction unavailable; skipping fallback.");
    }

    const controller = useCardMode && currentAICard
      ? createCardDraftController({ card: currentAICard, log })
      : undefined;
    let cardFinalized = false;
    let finalTextForFallback: string | undefined;

    try {
      await rt.channel.reply.dispatchReplyWithBufferedBlockDispatcher({
        ctx,
        cfg,
        dispatcherOptions: {
          responsePrefix: "",
          deliver: async (payload: ReplyStreamPayload, info?: ReplyChunkInfo) => {
            async function deliverMediaAttachments(urls: string[]) {
              for (const rawMediaUrl of urls) {
                const preparedMedia = await prepareMediaInput(
                  rawMediaUrl,
                  log,
                  dingtalkConfig.mediaUrlAllowlist,
                );
                try {
                  const actualMediaPath = preparedMedia.path;
                  const mediaType = resolveOutboundMediaType({
                    mediaPath: actualMediaPath,
                    asVoice: false,
                  });
                  if (sessionWebhook) {
                    await sendBySession(dingtalkConfig, sessionWebhook, "", {
                      mediaPath: actualMediaPath,
                      mediaType,
                      log,
                    });
                  } else {
                    const sendResult = await sendProactiveMedia(
                      dingtalkConfig,
                      to,
                      actualMediaPath,
                      mediaType,
                      {
                        accountId,
                        log,
                      },
                    );
                    if (!sendResult.ok) {
                      throw new Error(sendResult.error || "Media reply send failed");
                    }
                  }
                } finally {
                  await preparedMedia.cleanup?.();
                }
              }
            }

            try {
              const richPayload = payload as typeof payload & {
                mediaUrl?: string;
                mediaUrls?: string[];
              };
              const textToSend = payload.text;
              const mediaUrls = Array.isArray(richPayload.mediaUrls)
                ? richPayload.mediaUrls.filter((entry: unknown) => typeof entry === "string" && entry.trim())
                : richPayload.mediaUrl && typeof richPayload.mediaUrl === "string" && richPayload.mediaUrl.trim()
                  ? [richPayload.mediaUrl]
                  : [];

              // In card mode, deliver(final) must always reach finalize even with empty text
              // (e.g. bot sent a file via tool with no accompanying text).
              if ((typeof textToSend !== "string" || textToSend.length === 0) && mediaUrls.length === 0) {
                if (useCardMode && currentAICard && info?.kind === "final") {
                  // fall through to card finalize below
                } else {
                  return;
                }
              }

              // ---- card mode: final ----
              // Do NOT finalize or stop the controller here — runtime calls
              // deliver(final) once per assistant turn, so there may be more
              // turns coming after tool calls. Finalization is deferred to
              // step 5 (post-dispatch) where the full accumulated content
              // is available.
              if (useCardMode && currentAICard && info?.kind === "final") {
                log?.info?.(
                  `[DingTalk][Finalize] deliver(final) received — cardState=${currentAICard.state} ` +
                  `textLen=${typeof textToSend === "string" ? textToSend.length : "null"} ` +
                  `mediaUrls=${mediaUrls.length} ` +
                  `lastAnswer="${(controller?.getLastAnswerContent() ?? "").slice(0, 80)}" ` +
                  `lastContent="${(controller?.getLastContent() ?? "").slice(0, 80)}"`,
                );
                if (mediaUrls.length > 0) {
                  await deliverMediaAttachments(mediaUrls);
                }
                const rawFinalText = typeof textToSend === "string" ? textToSend : "";
                if (rawFinalText) {
                  finalTextForFallback = rawFinalText;
                }
                return;
              }

              // ---- card mode: tool ----
              if (useCardMode && currentAICard && info?.kind === "tool") {
                if (controller!.isFailed() || isCardInTerminalState(currentAICard.state)) {
                  log?.debug?.("[DingTalk] Card failed, skipping tool result (will send full reply on final)");
                  return;
                }
                await controller!.flush();
                await controller!.waitForInFlight();
                log?.info?.(
                  `[DingTalk] Tool result received, streaming to AI Card: ${(textToSend ?? "").slice(0, 100)}`,
                );
                const toolText = typeof textToSend === "string" ? formatContentForCard(textToSend, "tool") : "";
                if (toolText) {
                  const sendResult = await sendMessage(dingtalkConfig, to, toolText, {
                    sessionWebhook,
                    atUserId: !isDirect ? senderId : null,
                    log,
                    card: currentAICard,
                    accountId,
                    storePath,
                    conversationId: groupId,
                    cardUpdateMode: "append",
                  });
                  if (!sendResult.ok) {
                    throw new Error(sendResult.error || "Tool stream send failed");
                  }
                }
                return;
              }

              // ---- media delivery (all modes) ----
              if (mediaUrls.length > 0) {
                await deliverMediaAttachments(mediaUrls);
              }

              // ---- non-card mode (markdown/text) ----
              if (!useCardMode || !currentAICard) {
                if (typeof textToSend !== "string" || textToSend.length === 0) {
                  return;
                }
                const sendResult = await sendMessage(dingtalkConfig, to, textToSend, {
                  sessionWebhook,
                  atUserId: !isDirect ? senderId : null,
                  log,
                  accountId,
                  storePath,
                  conversationId: groupId,
                });
                if (!sendResult.ok) {
                  throw new Error(sendResult.error || "Reply send failed");
                }
              }
            } catch (err: any) {
              log?.error?.(`[DingTalk] Reply failed: ${err.message}`);
              if (err?.response?.data !== undefined) {
                log?.error?.(formatDingTalkErrorPayloadLog("inbound.replyDeliver", err.response.data));
              }
              throw err;
            }
          },
        },
        replyOptions: {
          disableBlockStreaming: dingtalkConfig.cardRealTimeStream && controller ? true : undefined,

          onAssistantMessageStart: controller
            ? () => { controller.notifyNewAssistantTurn(); }
            : undefined,

          onPartialReply: dingtalkConfig.cardRealTimeStream && controller
            ? (payload: ReplyStreamPayload) => {
                if (payload.text) {
                  controller.updateAnswer(payload.text);
                }
              }
            : undefined,

          onReasoningStream: controller
            ? (payload: ReplyStreamPayload) => {
                if (payload.text) {
                  controller.updateReasoning(payload.text);
                }
              }
            : undefined,
        },
      });
    } catch (dispatchErr: any) {
      if (useCardMode && currentAICard && !isCardInTerminalState(currentAICard.state)) {
        controller!.stop();
        await controller!.waitForInFlight();
        if (!cardFinalized) {
          try {
            await finishAICard(currentAICard, "❌ 处理失败", log);
          } catch (cardCloseErr: any) {
            log?.debug?.(`[DingTalk] Failed to finalize card after dispatch error: ${cardCloseErr.message}`);
            currentAICard.state = AICardStatus.FAILED;
            currentAICard.lastUpdated = Date.now();
          }
        }
      }
      throw dispatchErr;
    }

    // 5) Post-dispatch card finalization.
    // This is the sole finalize path — deliver(final) defers here because
    // runtime may call deliver(final) multiple times (once per assistant turn).
    log?.info?.(
      `[DingTalk][Finalize] Step 5 entry — useCardMode=${useCardMode} ` +
      `hasCard=${!!currentAICard} cardFinalized=${cardFinalized} ` +
      `cardState=${currentAICard?.state ?? "N/A"} ` +
      `controllerFailed=${controller?.isFailed() ?? "N/A"} ` +
      `finalTextForFallback="${(finalTextForFallback ?? "").slice(0, 80)}" ` +
      `lastAnswer="${(controller?.getLastAnswerContent() ?? "").slice(0, 80)}" ` +
      `lastContent="${(controller?.getLastContent() ?? "").slice(0, 80)}"`,
    );
    if (useCardMode && currentAICard && !cardFinalized) {
      try {
        if (currentAICard.state === AICardStatus.FINISHED) {
          log?.info?.(
            `[DingTalk][Finalize] Skipping — card already FINISHED`,
          );
          return;
        }

        if (currentAICard.state === AICardStatus.FAILED || controller!.isFailed()) {
          const fallbackText = finalTextForFallback
            || controller!.getLastAnswerContent()
            || controller!.getLastContent()
            || currentAICard.lastStreamedContent;
          if (fallbackText) {
            log?.debug?.("[DingTalk] Card failed during streaming, sending markdown fallback");
            const sendResult = await sendMessage(dingtalkConfig, to, fallbackText, {
              sessionWebhook,
              atUserId: !isDirect ? senderId : null,
              log,
              accountId,
              storePath,
              conversationId: groupId,
            });
            if (!sendResult.ok) {
              throw new Error(sendResult.error || "Markdown fallback send failed after card failure — user received no reply");
            }
          } else {
            log?.debug?.("[DingTalk] Card failed but no content to fallback with");
          }
          return;
        }

        await controller!.flush();
        await controller!.waitForInFlight();
        controller!.stop();
        const finalText = controller!.getLastAnswerContent()
          || finalTextForFallback
          || "✅ Done";
        log?.info?.(
          `[DingTalk][Finalize] Calling finishAICard — finalTextLen=${finalText.length} ` +
          `source=${controller!.getLastAnswerContent() ? "lastAnswerContent" : finalTextForFallback ? "finalTextForFallback" : "fallbackDone"} ` +
          `preview="${finalText.slice(0, 120)}"`,
        );
        await finishAICard(currentAICard, finalText, log);
      } catch (err: any) {
        log?.debug?.(`[DingTalk] AI Card finalization failed: ${err.message}`);
        if (err?.response?.data !== undefined) {
          log?.debug?.(formatDingTalkErrorPayloadLog("inbound.cardFinalize", err.response.data));
        }
        try {
          if (currentAICard.state !== AICardStatus.FINISHED) {
            currentAICard.state = AICardStatus.FAILED;
            currentAICard.lastUpdated = Date.now();
          }
        } catch (stateErr: any) {
          log?.debug?.(`[DingTalk] Failed to update card state to FAILED: ${stateErr.message}`);
        }
      }
    }
  } finally {
    releaseSessionLock();
    if (ackReactionAttached) {
      void (async () => {
        const elapsedMs = ackReactionAttachedAt > 0 ? Date.now() - ackReactionAttachedAt : 0;
        const remainingVisibleMs = MIN_THINKING_REACTION_VISIBLE_MS - elapsedMs;
        if (remainingVisibleMs > 0) {
          await new Promise(resolve => setTimeout(resolve, remainingVisibleMs));
        }
        await recallNativeAckReactionWithRetry(
          dingtalkConfig,
          {
            msgId: data.msgId,
            conversationId: groupId,
            reactionName: resolvedAckReaction,
          },
          log,
        );
      })();
    }
  }
}
