import axios from "axios";
import { getAccessToken } from "./auth";
import type { DingTalkConfig } from "./types";
import { formatDingTalkErrorPayloadLog, getProxyBypassOption } from "./utils";

// DingTalk currently exposes a dedicated native "thinking" reaction flow rather than
// a generic arbitrary-emoji reaction API for this plugin path.
const DINGTALK_NATIVE_ACK_REACTION = "🤔思考中";
const THINKING_EMOTION_ID = "2659900";
const THINKING_EMOTION_BACKGROUND_ID = "im_bg_1";
const THINKING_REACTION_RECALL_DELAYS_MS = [0, 1500, 5000] as const;

type AckReactionLogger = {
  debug?: (msg: string) => void;
  info?: (msg: string) => void;
  warn?: (msg: string) => void;
};

type AckReactionTarget = {
  msgId: string;
  conversationId: string;
  robotCode?: string;
  reactionName?: string;
};

function resolveAckReactionPayload(config: DingTalkConfig, data: AckReactionTarget): {
  robotCode: string;
  reactionName: string;
} | null {
  const robotCode = (data.robotCode || config.robotCode || config.clientId || "").trim();
  const reactionName =
    (data.reactionName || DINGTALK_NATIVE_ACK_REACTION).trim() || DINGTALK_NATIVE_ACK_REACTION;
  if (!robotCode || !data.msgId || !data.conversationId) {
    return null;
  }
  return { robotCode, reactionName };
}

async function callEmotionApi(
  config: DingTalkConfig,
  data: AckReactionTarget,
  endpoint: "reply" | "recall",
  successLog: string,
  errorLogPrefix: string,
  errorPayloadKey: "inbound.ackReactionAttach" | "inbound.ackReactionRecall",
  log?: AckReactionLogger,
): Promise<boolean> {
  const payload = resolveAckReactionPayload(config, data);
  if (!payload) {
    return false;
  }

  try {
    const token = await getAccessToken(config, log as any);
    await axios.post(
      `https://api.dingtalk.com/v1.0/robot/emotion/${endpoint}`,
      {
        robotCode: payload.robotCode,
        openMsgId: data.msgId,
        openConversationId: data.conversationId,
        emotionType: 2,
        emotionName: payload.reactionName,
        textEmotion: {
          emotionId: THINKING_EMOTION_ID,
          emotionName: payload.reactionName,
          text: payload.reactionName,
          backgroundId: THINKING_EMOTION_BACKGROUND_ID,
        },
      },
      {
        headers: {
          "x-acs-dingtalk-access-token": token,
          "Content-Type": "application/json",
        },
        timeout: 5000,
        ...getProxyBypassOption(config),
      },
    );
    log?.info?.(successLog);
    return true;
  } catch (err: any) {
    log?.warn?.(`${errorLogPrefix}: ${err.message}`);
    if (err?.response?.data !== undefined) {
      log?.warn?.(formatDingTalkErrorPayloadLog(errorPayloadKey, err.response.data));
    }
    return false;
  }
}

export async function attachNativeAckReaction(
  config: DingTalkConfig,
  data: AckReactionTarget,
  log?: AckReactionLogger,
): Promise<boolean> {
  return callEmotionApi(
    config,
    data,
    "reply",
    "[DingTalk] Native ack reaction attach succeeded",
    "[DingTalk] Native ack reaction attach failed",
    "inbound.ackReactionAttach",
    log,
  );
}

async function recallNativeAckReaction(
  config: DingTalkConfig,
  data: AckReactionTarget,
  log?: AckReactionLogger,
): Promise<boolean> {
  return callEmotionApi(
    config,
    data,
    "recall",
    "[DingTalk] Native ack reaction recall succeeded",
    "[DingTalk] Native ack reaction recall failed",
    "inbound.ackReactionRecall",
    log,
  );
}

export async function recallNativeAckReactionWithRetry(
  config: DingTalkConfig,
  data: AckReactionTarget,
  log?: AckReactionLogger,
): Promise<void> {
  for (const delayMs of THINKING_REACTION_RECALL_DELAYS_MS) {
    if (delayMs > 0) {
      await new Promise(resolve => setTimeout(resolve, delayMs));
    }
    if (await recallNativeAckReaction(config, data, log)) {
      return;
    }
  }
}
