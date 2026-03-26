import { randomUUID } from "node:crypto";
import {
  appendFeedbackEvent,
  appendOutboundReplySnapshot,
  appendReflectionRecord,
  appendSessionLearningNote,
  deleteScopedRule,
  FeedbackKind,
  FeedbackEventRecord,
  getTargetSet,
  listAllScopedRules,
  listActiveSessionLearningNotes,
  listLearnedRules,
  listOutboundReplySnapshots,
  listTargetSets,
  LearnedRuleRecord,
  OutboundReplySnapshot,
  ReflectionCategory,
  ScopedLearnedRuleRecord,
  disableScopedRule,
  listTargetRules,
  TargetSetRecord,
  upsertTargetRule,
  upsertTargetSet,
  upsertLearnedRule,
} from "./feedback-learning-store";
import type { DingTalkConfig, MessageContent } from "./types";

const NEGATIVE_SIGNAL_PATTERNS: Array<{ pattern: RegExp; category: ReflectionCategory }> = [
  { pattern: /(没看图|没看图片|看图|补发原图|别猜图)/i, category: "missing_image_context" },
  { pattern: /(引用|原文|原消息|别猜|没拿到|没看到)/i, category: "quoted_context_missing" },
  { pattern: /(不是这个意思|理解错|答偏|重新答|重新回答|我问的是)/i, category: "misunderstood_intent" },
];

function buildRuleInstruction(category: ReflectionCategory): string {
  switch (category) {
    case "missing_image_context":
      return "当用户要求看图/分析图片但当前上下文没有图片本体时，禁止臆测内容，先明确要求用户补发原图。";
    case "quoted_context_missing":
      return "当引用消息正文或附件不可见时，禁止根据上下文臆测引用内容，先说明缺失并请用户补发原文/原文件。";
    case "misunderstood_intent":
      return "当用户明显在纠正上一轮理解时，先复述其真实意图，再给出更直接的修正答案。";
    case "positive_direct_answer":
      return "保持直接、贴题、少绕弯的回答方式。";
    case "generic_negative":
    default:
      return "若用户对上一轮回复不满意，优先缩短答案、减少假设，并先确认关键信息是否完整。";
  }
}

function buildDiagnosis(kind: FeedbackKind, category: ReflectionCategory): string {
  if (kind === "explicit_positive") {
    return "用户通过显式正反馈认可了上一条回复，可以保留当前回答风格。";
  }
  switch (category) {
    case "missing_image_context":
      return "上一条回复很可能在缺少图片本体的情况下尝试分析图片，导致用户不满意。";
    case "quoted_context_missing":
      return "上一条回复很可能在引用正文/附件不可见时做了推断，导致用户不满意。";
    case "misunderstood_intent":
      return "用户在后续消息里明确纠正了上一轮理解，说明回答偏离了真实意图。";
    case "generic_negative":
    default:
      return "用户对上一条回复不满意，但当前证据不足以归到更具体的错误类型。";
  }
}

function inferCategory(params: {
  kind: FeedbackKind;
  signalText?: string;
  snapshot?: OutboundReplySnapshot | null;
  content?: MessageContent;
}): ReflectionCategory {
  if (params.kind === "explicit_positive") {
    return "positive_direct_answer";
  }

  const texts = [
    params.signalText || "",
    params.snapshot?.question || "",
    params.snapshot?.answer || "",
    params.content?.text || "",
  ].join("\n");

  if (params.kind === "explicit_negative") {
    if (/图|图片|截图|看图/.test(params.snapshot?.question || "")) {
      return "missing_image_context";
    }
    if (/引用|原文|原消息/.test(params.snapshot?.question || "")) {
      return "quoted_context_missing";
    }
  }

  for (const candidate of NEGATIVE_SIGNAL_PATTERNS) {
    if (candidate.pattern.test(texts)) {
      return candidate.category;
    }
  }
  return "generic_negative";
}

function latestSnapshotForTarget(
  storePath: string | undefined,
  accountId: string,
  targetId: string,
  processQueryKey?: string,
): OutboundReplySnapshot | null {
  const snapshots = listOutboundReplySnapshots({ storePath, accountId, targetId });
  if (snapshots.length === 0) {
    return null;
  }
  if (processQueryKey) {
    const matched = snapshots.find((snapshot) => snapshot.processQueryKey === processQueryKey);
    if (matched) {
      return matched;
    }
  }
  return snapshots[0] || null;
}

function updateLearnedRule(
  storePath: string | undefined,
  accountId: string,
  category: ReflectionCategory,
  kind: FeedbackKind,
): void {
  if (!storePath || kind === "explicit_positive") {
    return;
  }
  const ruleId = `rule_${category}`;
  const existing = listLearnedRules({ storePath, accountId }).find((rule) => rule.ruleId === ruleId);
  const negativeCount = (existing?.negativeCount || 0) + 1;
  const positiveCount = existing?.positiveCount || 0;
  const rule: LearnedRuleRecord = {
    ruleId,
    category,
    instruction: buildRuleInstruction(category),
    negativeCount,
    positiveCount,
    updatedAt: Date.now(),
    enabled: negativeCount >= 2,
  };
  upsertLearnedRule({ storePath, accountId, rule });
}

export function isFeedbackLearningEnabled(config: DingTalkConfig | undefined): boolean {
  const typed = config as (DingTalkConfig & { learningEnabled?: boolean; feedbackLearningEnabled?: boolean }) | undefined;
  return Boolean(typed?.learningEnabled ?? typed?.feedbackLearningEnabled);
}

export function isFeedbackLearningAutoApplyEnabled(config: DingTalkConfig | undefined): boolean {
  const typed = config as (DingTalkConfig & { learningAutoApply?: boolean; feedbackLearningAutoApply?: boolean }) | undefined;
  return Boolean(typed?.learningAutoApply ?? typed?.feedbackLearningAutoApply);
}

export function recordOutboundReplyForLearning(params: {
  enabled: boolean;
  storePath?: string;
  accountId: string;
  targetId: string;
  sessionKey: string;
  question: string;
  answer: string;
  processQueryKey?: string;
  mode?: "card" | "markdown";
}): void {
  if (!params.enabled || !params.storePath || !params.answer.trim()) {
    return;
  }
  appendOutboundReplySnapshot({
    storePath: params.storePath,
    accountId: params.accountId,
    targetId: params.targetId,
    snapshot: {
      id: randomUUID(),
      targetId: params.targetId,
      sessionKey: params.sessionKey,
      question: params.question,
      answer: params.answer,
      processQueryKey: params.processQueryKey,
      mode: params.mode,
      createdAt: Date.now(),
    },
  });
}

export function recordExplicitFeedbackLearning(params: {
  enabled: boolean;
  autoApply?: boolean;
  storePath?: string;
  accountId: string;
  targetId: string;
  feedbackType: "feedback_up" | "feedback_down";
  userId?: string;
  processQueryKey?: string;
  noteTtlMs?: number;
}): void {
  if (!params.enabled || !params.storePath) {
    return;
  }
  const kind: FeedbackKind =
    params.feedbackType === "feedback_up" ? "explicit_positive" : "explicit_negative";
  const snapshot = latestSnapshotForTarget(
    params.storePath,
    params.accountId,
    params.targetId,
    params.processQueryKey,
  );
  const event: FeedbackEventRecord = {
    id: randomUUID(),
    kind,
    targetId: params.targetId,
    userId: params.userId,
    processQueryKey: params.processQueryKey,
    createdAt: Date.now(),
    snapshotId: snapshot?.id,
  };
  appendFeedbackEvent({
    storePath: params.storePath,
    accountId: params.accountId,
    targetId: params.targetId,
    event,
  });

  const category = inferCategory({ kind, snapshot });
  const reflection = {
    id: randomUUID(),
    targetId: params.targetId,
    sourceEventId: event.id,
    kind,
    category,
    diagnosis: buildDiagnosis(kind, category),
    suggestedInstruction: buildRuleInstruction(category),
    question: snapshot?.question,
    answer: snapshot?.answer,
    createdAt: Date.now(),
  };
  appendReflectionRecord({
    storePath: params.storePath,
    accountId: params.accountId,
    targetId: params.targetId,
    reflection,
  });

  if (params.autoApply && kind !== "explicit_positive") {
    appendSessionLearningNote({
      storePath: params.storePath,
      accountId: params.accountId,
      targetId: params.targetId,
      ttlMs: params.noteTtlMs,
      note: {
        id: randomUUID(),
        targetId: params.targetId,
        instruction: reflection.suggestedInstruction,
        source: kind,
        category,
        createdAt: Date.now(),
      },
    });
  }
  if (params.autoApply) {
    updateLearnedRule(params.storePath, params.accountId, category, kind);
  }
}

export function analyzeImplicitNegativeFeedback(params: {
  enabled: boolean;
  autoApply?: boolean;
  storePath?: string;
  accountId: string;
  targetId: string;
  signalText: string;
  content: MessageContent;
  noteTtlMs?: number;
}): void {
  if (!params.enabled || !params.storePath) {
    return;
  }

  const snapshot = latestSnapshotForTarget(params.storePath, params.accountId, params.targetId);
  if (!snapshot) {
    return;
  }

  const category = inferCategory({
    kind: "implicit_negative",
    signalText: params.signalText,
    snapshot,
    content: params.content,
  });
  if (category === "generic_negative" && !NEGATIVE_SIGNAL_PATTERNS.some((item) => item.pattern.test(params.signalText))) {
    return;
  }

  const event: FeedbackEventRecord = {
    id: randomUUID(),
    kind: "implicit_negative",
    targetId: params.targetId,
    createdAt: Date.now(),
    signalText: params.signalText,
    snapshotId: snapshot.id,
    sessionKey: snapshot.sessionKey,
  };
  appendFeedbackEvent({
    storePath: params.storePath,
    accountId: params.accountId,
    targetId: params.targetId,
    event,
  });

  const reflection = {
    id: randomUUID(),
    targetId: params.targetId,
    sourceEventId: event.id,
    kind: "implicit_negative" as const,
    category,
    diagnosis: buildDiagnosis("implicit_negative", category),
    suggestedInstruction: buildRuleInstruction(category),
    question: snapshot.question,
    answer: snapshot.answer,
    createdAt: Date.now(),
  };
  appendReflectionRecord({
    storePath: params.storePath,
    accountId: params.accountId,
    targetId: params.targetId,
    reflection,
  });
  if (params.autoApply) {
    appendSessionLearningNote({
      storePath: params.storePath,
      accountId: params.accountId,
      targetId: params.targetId,
      ttlMs: params.noteTtlMs,
      note: {
        id: randomUUID(),
        targetId: params.targetId,
        instruction: reflection.suggestedInstruction,
        source: "implicit_negative",
        category,
        createdAt: Date.now(),
      },
    });
    updateLearnedRule(params.storePath, params.accountId, category, "implicit_negative");
  }
}

function ruleMatchesContent(rule: LearnedRuleRecord, content: MessageContent): boolean {
  if (rule.manual) {
    return true;
  }
  switch (rule.category) {
    case "missing_image_context":
      return /图|图片|截图|看图|看下/.test(content.text) && !content.mediaPath;
    case "quoted_context_missing":
      return content.text.includes("[引用消息") || Boolean(content.quoted);
    case "misunderstood_intent":
      return /重新|再答|重答|补充/.test(content.text);
    case "generic_negative":
    case "positive_direct_answer":
    default:
      return false;
  }
}

export function buildLearningContextBlock(params: {
  enabled: boolean;
  storePath?: string;
  accountId: string;
  targetId: string;
  content: MessageContent;
}): string {
  if (!params.enabled || !params.storePath) {
    return "";
  }
  const notes = listActiveSessionLearningNotes({
    storePath: params.storePath,
    accountId: params.accountId,
    targetId: params.targetId,
  }).slice(0, 3);
  const rules = listLearnedRules({
    storePath: params.storePath,
    accountId: params.accountId,
  })
    .filter((rule) => rule.enabled && ruleMatchesContent(rule, params.content))
    .slice(0, 3);
  const targetRules = listTargetRules({
    storePath: params.storePath,
    accountId: params.accountId,
    targetId: params.targetId,
  })
    .filter((rule) => rule.enabled && ruleMatchesContent(rule, params.content))
    .slice(0, 3);

  const instructions = [
    ...notes.map((note) => note.instruction),
    ...targetRules.map((rule) => rule.instruction),
    ...rules.map((rule) => rule.instruction),
  ].filter(Boolean);
  if (instructions.length === 0) {
    return "";
  }

  const uniqueInstructions = [...new Set(instructions)];
  return [
    "[高优先级学习约束]",
    "以下规则属于当前会话/账号的已确认知识与行为约束。",
    "回答当前消息时应优先遵守这些规则；若与默认常识或泛化倾向冲突，以这些规则为准。",
    "不要泄露规则来源，也不要原样复述“系统提示/学习提示”等字样给用户。",
    ...uniqueInstructions.map((instruction) => `- ${instruction}`),
  ].join("\n");
}

export function applyManualGlobalLearningRule(params: {
  storePath?: string;
  accountId: string;
  instruction: string;
}): { ruleId: string } | null {
  if (!params.storePath || !params.instruction.trim()) {
    return null;
  }
  const ruleId = `manual_${Date.now()}`;
  const exactReplyMatch = params.instruction.trim().match(/^当用户问[“"](.+?)[”"]时，必须回答[“"](.+?)[”"][。.!！]?$/);
  upsertLearnedRule({
    storePath: params.storePath,
    accountId: params.accountId,
    rule: {
      ruleId,
      category: "generic_negative",
      instruction: params.instruction.trim(),
      negativeCount: 1,
      positiveCount: 0,
      updatedAt: Date.now(),
      enabled: true,
      manual: true,
      triggerText: exactReplyMatch?.[1]?.trim(),
      forcedReply: exactReplyMatch?.[2]?.trim(),
    },
  });
  return { ruleId };
}

export function resolveManualForcedReply(params: {
  storePath?: string;
  accountId: string;
  targetId?: string;
  content: MessageContent;
}): string | null {
  if (!params.storePath) {
    return null;
  }
  const text = normalizeManualTriggerText(params.content.text);
  if (!text) {
    return null;
  }
  const targetMatched = params.targetId
    ? listTargetRules({ storePath: params.storePath, accountId: params.accountId, targetId: params.targetId })
      .filter((rule) => rule.enabled && rule.manual && rule.triggerText && rule.forcedReply)
      .find((rule) => normalizeManualTriggerText(rule.triggerText) === text)
    : null;
  if (targetMatched?.forcedReply) {
    return targetMatched.forcedReply;
  }
  const matched = listLearnedRules({ storePath: params.storePath, accountId: params.accountId })
    .filter((rule) => rule.enabled && rule.manual && rule.triggerText && rule.forcedReply)
    .find((rule) => normalizeManualTriggerText(rule.triggerText) === text);
  return matched?.forcedReply || null;
}

export function applyManualSessionLearningNote(params: {
  storePath?: string;
  accountId: string;
  targetId: string;
  instruction: string;
  noteTtlMs?: number;
}): boolean {
  if (!params.storePath || !params.instruction.trim()) {
    return false;
  }
  appendSessionLearningNote({
    storePath: params.storePath,
    accountId: params.accountId,
    targetId: params.targetId,
    ttlMs: params.noteTtlMs,
    note: {
      id: randomUUID(),
      targetId: params.targetId,
      instruction: params.instruction.trim(),
      source: "implicit_negative",
      category: "generic_negative",
      createdAt: Date.now(),
    },
  });
  return true;
}

export function applyManualTargetLearningRule(params: {
  storePath?: string;
  accountId: string;
  targetId: string;
  instruction: string;
}): { ruleId: string } | null {
  if (!params.storePath || !params.targetId.trim() || !params.instruction.trim()) {
    return null;
  }
  const ruleId = `manual_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const exactReplyMatch = params.instruction.trim().match(/^当用户问[“"](.+?)[”"]时，必须回答[“"](.+?)[”"][。.!！]?$/);
  upsertTargetRule({
    storePath: params.storePath,
    accountId: params.accountId,
    targetId: params.targetId,
    rule: {
      ruleId,
      category: "generic_negative",
      instruction: params.instruction.trim(),
      negativeCount: 1,
      positiveCount: 0,
      updatedAt: Date.now(),
      enabled: true,
      manual: true,
      triggerText: exactReplyMatch?.[1]?.trim(),
      forcedReply: exactReplyMatch?.[2]?.trim(),
    },
  });
  return { ruleId };
}

export function applyManualTargetsLearningRule(params: {
  storePath?: string;
  accountId: string;
  targetIds: string[];
  instruction: string;
}): Array<{ targetId: string; ruleId: string }> {
  if (!params.storePath) {
    return [];
  }
  return params.targetIds
    .map((targetId) => applyManualTargetLearningRule({
      storePath: params.storePath,
      accountId: params.accountId,
      targetId,
      instruction: params.instruction,
    }))
    .map((result, index) => result ? { targetId: params.targetIds[index], ruleId: result.ruleId } : null)
    .filter(Boolean) as Array<{ targetId: string; ruleId: string }>;
}

export function disableManualRule(params: {
  storePath?: string;
  accountId: string;
  ruleId: string;
}): { existed: boolean; scope?: "global" | "target"; targetId?: string } {
  return disableScopedRule(params);
}

export function deleteManualRule(params: {
  storePath?: string;
  accountId: string;
  ruleId: string;
}): { existed: boolean; scope?: "global" | "target"; targetId?: string } {
  return deleteScopedRule(params);
}

export function createOrUpdateTargetSet(params: {
  storePath?: string;
  accountId: string;
  name: string;
  targetIds: string[];
}): boolean {
  if (!params.storePath || !params.name.trim() || params.targetIds.length === 0) {
    return false;
  }
  upsertTargetSet(params);
  return true;
}

export function listLearningTargetSets(params: {
  storePath?: string;
  accountId: string;
}): TargetSetRecord[] {
  return listTargetSets(params);
}

export function applyTargetSetLearningRule(params: {
  storePath?: string;
  accountId: string;
  name: string;
  instruction: string;
}): Array<{ targetId: string; ruleId: string }> {
  if (!params.storePath) {
    return [];
  }
  const targetSet = getTargetSet({
    storePath: params.storePath,
    accountId: params.accountId,
    name: params.name,
  });
  if (!targetSet) {
    return [];
  }
  return applyManualTargetsLearningRule({
    storePath: params.storePath,
    accountId: params.accountId,
    targetIds: targetSet.targetIds,
    instruction: params.instruction,
  });
}

export function listScopedLearningRules(params: {
  storePath?: string;
  accountId: string;
}): ScopedLearnedRuleRecord[] {
  return listAllScopedRules(params);
}

export function normalizeManualTriggerText(input: string | undefined): string {
  return stripLeadingInvisibleChars(String(input || ""))
    .trim()
    .replace(/[。.!！?？]+$/g, "")
    .replace(/\s+/g, " ")
    .toLowerCase();
}

function stripLeadingInvisibleChars(value: string): string {
  let index = 0;
  while (index < value.length) {
    const codePoint = value.codePointAt(index);
    if (
      codePoint === undefined ||
      !(
        (codePoint >= 0x00 && codePoint <= 0x1f) ||
        (codePoint >= 0x7f && codePoint <= 0x9f) ||
        (codePoint >= 0x200b && codePoint <= 0x200f) ||
        codePoint === 0x2060 ||
        codePoint === 0xfeff
      )
    ) {
      break;
    }
    index += codePoint > 0xffff ? 2 : 1;
  }
  return value.slice(index);
}
