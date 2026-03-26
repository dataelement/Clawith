import { normalizeAllowFrom, isSenderOwner } from "./access-control";
import type { DingTalkConfig } from "./types";

export interface ParsedLearnCommand {
  scope:
    | "help"
    | "global"
    | "session"
    | "here"
    | "target"
    | "targets"
    | "list"
    | "disable"
    | "delete"
    | "whoami"
    | "whereami"
    | "owner-status"
    | "target-set-create"
    | "target-set-apply"
    | "unknown";
  instruction?: string;
  ruleId?: string;
  targetId?: string;
  targetIds?: string[];
  setName?: string;
}

const TARGET_DELIMITER = "#@#";

export function parseLearnCommand(text: string | undefined): ParsedLearnCommand {
  const raw = String(text || "").trim();
  if (!raw) {
    return { scope: "unknown" };
  }
  const normalized = raw.toLowerCase();
  if (
    normalized === "/learn help"
  ) {
    return { scope: "help" };
  }
  if (
    normalized === "/learn whoami"
    || normalized === "/whoami"
    || normalized === "我是谁"
    || normalized === "我的信息"
  ) {
    return { scope: "whoami" };
  }
  if (
    normalized === "/learn whereami"
    || normalized === "这里是谁"
    || normalized === "这个群是谁"
    || normalized === "这个会话是谁"
  ) {
    return { scope: "whereami" };
  }
  if (
    normalized === "/learn owner status"
    || normalized === "/owner status"
    || normalized === "/owner-status"
  ) {
    return { scope: "owner-status" };
  }
  if (normalized === "/learn list") {
    return { scope: "list" };
  }
  if (normalized === "/learn target-set list") {
    return { scope: "list" };
  }
  if (normalized.startsWith("/learn disable ")) {
    const ruleId = raw.slice("/learn disable ".length).trim();
    return ruleId ? { scope: "disable", ruleId } : { scope: "unknown" };
  }
  if (normalized.startsWith("/learn delete ")) {
    const ruleId = raw.slice("/learn delete ".length).trim();
    return ruleId ? { scope: "delete", ruleId } : { scope: "unknown" };
  }
  if (normalized.startsWith("/learn global ")) {
    return { scope: "global", instruction: raw.slice("/learn global ".length).trim() };
  }
  if (normalized.startsWith("/learn session ")) {
    return { scope: "session", instruction: raw.slice("/learn session ".length).trim() };
  }
  if (normalized.startsWith("/learn here ")) {
    const rawInstruction = raw.slice("/learn here ".length).trim();
    const instruction = rawInstruction.startsWith(TARGET_DELIMITER)
      ? rawInstruction.slice(TARGET_DELIMITER.length).trim()
      : rawInstruction;
    return instruction ? { scope: "here", instruction } : { scope: "unknown" };
  }
  if (normalized.startsWith("/learn target-set create ")) {
    const payload = splitDelimitedPayload(raw.slice("/learn target-set create ".length));
    if (!payload) {
      return { scope: "unknown" };
    }
    const targetIds = parseTargetIds(payload.body);
    return payload.head && targetIds.length > 0
      ? { scope: "target-set-create", setName: payload.head, targetIds }
      : { scope: "unknown" };
  }
  if (normalized.startsWith("/learn target-set apply ")) {
    const payload = splitDelimitedPayload(raw.slice("/learn target-set apply ".length));
    return payload?.head && payload.body
      ? { scope: "target-set-apply", setName: payload.head, instruction: payload.body }
      : { scope: "unknown" };
  }
  if (normalized.startsWith("/learn target ")) {
    const payload = splitDelimitedPayload(raw.slice("/learn target ".length));
    return payload?.head && payload.body
      ? { scope: "target", targetId: payload.head, instruction: payload.body }
      : { scope: "unknown" };
  }
  if (normalized.startsWith("/learn targets ")) {
    const payload = splitDelimitedPayload(raw.slice("/learn targets ".length));
    if (!payload) {
      return { scope: "unknown" };
    }
    const targetIds = parseTargetIds(payload.head);
    return targetIds.length > 0 && payload.body
      ? { scope: "targets", targetIds, instruction: payload.body }
      : { scope: "unknown" };
  }
  return { scope: "unknown" };
}

function splitDelimitedPayload(raw: string): { head: string; body: string } | null {
  const index = raw.indexOf(TARGET_DELIMITER);
  if (index < 0) {
    return null;
  }
  const head = raw.slice(0, index).trim();
  const body = raw.slice(index + TARGET_DELIMITER.length).trim();
  if (!head || !body) {
    return null;
  }
  return { head, body };
}

function parseTargetIds(raw: string): string[] {
  return raw
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function isLearningOwner(params: {
  cfg?: { commands?: { ownerAllowFrom?: Array<string | number> } };
  config?: DingTalkConfig;
  senderId?: string;
  rawSenderId?: string;
}): boolean {
  const allow = normalizeAllowFrom(params.cfg?.commands?.ownerAllowFrom as string[] | undefined);
  return isSenderOwner({ allow, senderId: params.senderId, rawSenderId: params.rawSenderId });
}

export function formatWhoAmIReply(params: {
  senderId: string;
  rawSenderId?: string;
  senderStaffId?: string;
  isOwner?: boolean;
}): string {
  return [
    "这是你当前消息对应的身份信息：",
    "",
    `- senderId: \`${params.senderId || ""}\``,
    `- rawSenderId: \`${params.rawSenderId || ""}\``,
    `- senderStaffId: \`${params.senderStaffId || ""}\``,
    `- isOwner: \`${params.isOwner ? "true" : "false"}\``,
    "",
    "后续如果要配置 owner 或控制命令权限，就以这里返回的 senderId 为准。",
  ].join("\n");
}

export function formatWhereAmIReply(params: {
  conversationId: string;
  conversationType: "group" | "dm";
  peerId?: string;
}): string {
  return [
    params.conversationType === "group" ? "这是当前群聊信息：" : "这是当前会话信息：",
    "",
    `- conversationId: \`${params.conversationId}\``,
    `- conversationType: \`${params.conversationType}\``,
    params.peerId ? `- peerId: \`${params.peerId}\`` : undefined,
    "",
    "后续如果要定向注入到这里，就使用这个 conversationId。",
  ].filter(Boolean).join("\n");
}

export function formatOwnerStatusReply(params: {
  senderId: string;
  rawSenderId?: string;
  isOwner: boolean;
}): string {
  return [
    "当前 owner 控制状态：",
    "",
    `- senderId: \`${params.senderId || ""}\``,
    `- rawSenderId: \`${params.rawSenderId || ""}\``,
    `- isOwner: \`${params.isOwner ? "true" : "false"}\``,
    "",
    "如果需要变更 owner，请由宿主修改本机运行配置。",
  ].join("\n");
}

export function formatOwnerOnlyDeniedReply(): string {
  return "这条学习/控制命令仅允许 owner 使用。先发送“我是谁”确认你的 senderId，再由宿主将该 senderId 加入 commands.ownerAllowFrom。";
}

export function formatLearnCommandHelp(): string {
  return [
    "可用的 owner 学习命令：",
    "",
    "- /learn whoami：查看当前 senderId",
    "- /learn owner status：查看当前 owner 权限状态",
    "- /learn whereami：查看当前会话/群 ID",
    "- /learn global <规则>：发布到当前钉钉账号下所有会话",
    "- /learn session <规则>：仅发布到当前私聊会话",
    "- /learn here #@# <规则>：发布到当前群/当前私聊",
    "- /learn target <conversationId> #@# <规则>：发布到单个指定群/私聊",
    "- /learn targets <conversationId1,conversationId2> #@# <规则>：一次发布到多个指定群/私聊",
    "- /learn target-set create <名称> #@# <conversationId1,conversationId2>：保存一组固定目标",
    "- /learn target-set apply <名称> #@# <规则>：向一个目标组批量发布规则",
    "- /learn list：查看当前全局规则、目标规则与目标组摘要",
    "- /learn disable <ruleId>：停用一条规则，停止继续命中，但保留记录",
    "- /learn delete <ruleId>：彻底删除一条规则或目标规则",
    "",
    "可用的 owner 会话控制命令：",
    "- /session-alias show：查看当前会话共享会话别名",
    "- /session-alias set <alias>：将当前会话绑定到指定共享会话别名",
    "- /session-alias clear：清除当前会话共享会话别名，恢复为默认 peerId",
    "- /session-alias bind direct <senderId> <alias>：由 owner 远程绑定某个私聊 senderId",
    "- /session-alias bind group <conversationId> <alias>：由 owner 远程绑定某个群 conversationId",
    "- /session-alias unbind direct <senderId>：由 owner 远程解除某个私聊 senderId 绑定",
    "- /session-alias unbind group <conversationId>：由 owner 远程解除某个群 conversationId 绑定",
    "",
    "权限说明：",
    "- 只有 owner 才能真正执行 /learn 的写操作和控制操作。",
    "- 如果你现在不是 owner，也可以先用 `/learn whoami` 查看 senderId，再由宿主把它加入 commands.ownerAllowFrom。",
  ].join("\n");
}
export function formatLearnAppliedReply(params: {
  scope: "global" | "session" | "target" | "targets" | "target-set";
  instruction: string;
  ruleId?: string;
  targetId?: string;
  targetIds?: string[];
  setName?: string;
}): string {
  const scopeLine = params.scope === "global"
    ? "- 生效范围：同一钉钉账号下所有会话，将在下一条消息进入时自动加载"
    : params.scope === "session"
      ? "- 生效范围：当前会话，将在下一条消息进入时自动加载"
      : params.scope === "target"
        ? `- 生效范围：指定目标 \`${params.targetId}\``
        : params.scope === "targets"
          ? `- 生效范围：${params.targetIds?.length || 0} 个目标`
          : `- 生效范围：目标组 \`${params.setName}\``;
  return [
    params.scope === "global"
      ? "已注入全局知识。"
      : params.scope === "session"
        ? "已注入当前会话知识。"
        : params.scope === "target"
          ? "已注入指定目标知识。"
          : params.scope === "targets"
            ? "已批量注入多个目标。"
            : "已向目标组批量注入规则。",
    "",
    params.ruleId ? `- ruleId: \`${params.ruleId}\`` : undefined,
    `- instruction: ${params.instruction}`,
    scopeLine,
  ].filter(Boolean).join("\n");
}

export function formatLearnListReply(lines: string[]): string {
  if (lines.length === 0) {
    return "当前还没有规则或目标组。";
  }
  return ["当前规则与目标组摘要：", "", ...lines].join("\n");
}

export function formatLearnDisabledReply(params: {
  ruleId: string;
  existed: boolean;
  scope?: "global" | "target";
  targetId?: string;
}): string {
  if (!params.existed) {
    return [
      "未找到对应规则。",
      "",
      `- ruleId: \`${params.ruleId}\``,
      "可先使用 `/learn list` 查看当前可停用的规则。",
    ].join("\n");
  }
  return [
    "已停用规则。",
    "",
    `- ruleId: \`${params.ruleId}\``,
    params.scope === "target" && params.targetId ? `- scope: target (\`${params.targetId}\`)` : "- scope: global",
    "- 当前效果：这条规则会停止生效，但记录仍会保留，便于后续排查或恢复。",
  ].join("\n");
}

export function formatLearnDeletedReply(params: {
  ruleId: string;
  existed: boolean;
  scope?: "global" | "target";
  targetId?: string;
}): string {
  if (!params.existed) {
    return [
      "未找到对应规则。",
      "",
      `- ruleId: \`${params.ruleId}\``,
      "可先使用 `/learn list` 查看当前规则。",
    ].join("\n");
  }
  return [
    "已删除规则。",
    "",
    `- ruleId: \`${params.ruleId}\``,
    params.scope === "target" && params.targetId ? `- scope: target (\`${params.targetId}\`)` : "- scope: global",
  ].join("\n");
}

export function formatTargetSetSavedReply(params: {
  setName: string;
  targetIds: string[];
}): string {
  return [
    "已保存目标组。",
    "",
    `- name: \`${params.setName}\``,
    `- targetCount: \`${params.targetIds.length}\``,
    `- targetIds: ${params.targetIds.map((targetId) => `\`${targetId}\``).join(", ")}`,
  ].join("\n");
}
