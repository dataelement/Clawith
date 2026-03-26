export interface ParsedSessionCommand {
  scope:
    | "session-alias-show"
    | "session-alias-set"
    | "session-alias-clear"
    | "session-alias-bind"
    | "session-alias-unbind"
    | "unknown";
  peerId?: string;
  sourceKind?: "direct" | "group";
  sourceId?: string;
}

const SESSION_ALIAS_PATTERN = /^[a-zA-Z0-9_-]{1,64}$/;

export function parseSessionCommand(text: string | undefined): ParsedSessionCommand {
  const raw = String(text || "").trim();
  if (!raw) {
    return { scope: "unknown" };
  }
  const sessionAliasBindMatch = raw.match(/^\/session-alias\s+(bind|unbind)\s+(direct|group)\s+(\S+)(?:\s+(.+))?$/i);
  if (sessionAliasBindMatch) {
    const action = sessionAliasBindMatch[1]?.toLowerCase();
    const sourceKind = sessionAliasBindMatch[2]?.toLowerCase() as "direct" | "group";
    const sourceId = sessionAliasBindMatch[3]?.trim();
    const rawPeerId = sessionAliasBindMatch[4]?.trim();
    if (action === "bind") {
      return sourceId && rawPeerId
        ? { scope: "session-alias-bind", sourceKind, sourceId, peerId: rawPeerId }
        : { scope: "unknown" };
    }
    if (action === "unbind") {
      return sourceId
        ? { scope: "session-alias-unbind", sourceKind, sourceId }
        : { scope: "unknown" };
    }
  }
  const sessionAliasMatch = raw.match(/^\/session-alias\s+(show|clear|set)(?:\s+(.+))?$/i);
  if (!sessionAliasMatch) {
    return { scope: "unknown" };
  }
  const action = sessionAliasMatch[1]?.toLowerCase();
  const rawPeerId = sessionAliasMatch[2]?.trim();
  if (action === "show") {
    return { scope: "session-alias-show" };
  }
  if (action === "clear") {
    return { scope: "session-alias-clear" };
  }
  if (action === "set") {
    return rawPeerId ? { scope: "session-alias-set", peerId: rawPeerId } : { scope: "unknown" };
  }
  return { scope: "unknown" };
}

export function validateSessionAlias(peerId: string | undefined): string | null {
  const value = String(peerId || "").trim();
  if (!value) {
    return "共享会话别名不能为空。";
  }
  if (!SESSION_ALIAS_PATTERN.test(value)) {
    return "共享会话别名仅允许 [a-zA-Z0-9_-]{1,64}。";
  }
  return null;
}

export function formatSessionAliasReply(params: {
  sourceKind: "direct" | "group";
  sourceId: string;
  peerId: string;
  aliasSource: "default" | "override";
}): string {
  return [
    "当前会话别名：",
    "",
    `- source: \`${params.sourceKind}\``,
    `- sourceId: \`${params.sourceId}\``,
    `- peerId: \`${params.peerId}\``,
    `- mode: \`${params.aliasSource}\``,
  ].join("\n");
}

export function formatSessionAliasSetReply(params: {
  sourceKind: "direct" | "group";
  sourceId: string;
  peerId: string;
}): string {
  return [
    "已更新当前会话共享会话别名。",
    "",
    `- source: \`${params.sourceKind}\``,
    `- sourceId: \`${params.sourceId}\``,
    `- peerId: \`${params.peerId}\``,
    "",
    "将其他私聊或群也设置为同一个 peerId 后，这些会话会共用同一条会话。",
  ].join("\n");
}

export function formatSessionAliasValidationErrorReply(error: string): string {
  return [
    "共享会话别名不合法。",
    "",
    `- 原因：${error}`,
    "- 允许规则：`[a-zA-Z0-9_-]{1,64}`",
    "- 示例：`shared-dev`、`ops_shared`",
  ].join("\n");
}

export function formatSessionAliasClearedReply(params: {
  sourceKind: "direct" | "group";
  sourceId: string;
}): string {
  return [
    "已清除当前会话共享会话别名。",
    "",
    `- source: \`${params.sourceKind}\``,
    `- sourceId: \`${params.sourceId}\``,
    `- peerId: 恢复为当前${params.sourceKind === "direct" ? " senderId" : " conversationId"}`,
  ].join("\n");
}

export function formatSessionAliasBoundReply(params: {
  sourceKind: "direct" | "group";
  sourceId: string;
  peerId: string;
}): string {
  return [
    "已绑定共享会话别名。",
    "",
    `- source: \`${params.sourceKind}\``,
    `- sourceId: \`${params.sourceId}\``,
    `- peerId: \`${params.peerId}\``,
  ].join("\n");
}

export function formatSessionAliasUnboundReply(params: {
  sourceKind: "direct" | "group";
  sourceId: string;
  existed: boolean;
}): string {
  return [
    params.existed ? "已解除共享会话别名绑定。" : "未找到对应的共享会话别名绑定。",
    "",
    `- source: \`${params.sourceKind}\``,
    `- sourceId: \`${params.sourceId}\``,
  ].join("\n");
}
