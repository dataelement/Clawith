import type { DingTalkConfig } from "./types";

export interface ResolveDingTalkSessionPeerParams {
  isDirect: boolean;
  senderId: string;
  conversationId: string;
  peerIdOverride?: string;
  config: DingTalkConfig;
}

export interface ResolvedDingTalkSessionPeer {
  kind: "direct" | "group";
  peerId: string;
}

// Keep DingTalk aligned with Feishu's explicit peerId -> sessionKey model:
// resolve a stable peer identity first, then let OpenClaw build the final session key.
export function resolveDingTalkSessionPeer(
  params: ResolveDingTalkSessionPeerParams,
): ResolvedDingTalkSessionPeer {
  const normalizedPeerIdOverride = params.peerIdOverride?.trim();
  if (params.isDirect) {
    return {
      kind: "direct",
      peerId: normalizedPeerIdOverride || params.senderId,
    };
  }

  return {
    kind: "group",
    peerId: normalizedPeerIdOverride || params.conversationId,
  };
}
