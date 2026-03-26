import { readNamespaceJson, writeNamespaceJsonAtomic } from "./persistence-store";

const MAX_EVENTS = 200;
const MAX_SNAPSHOTS = 100;
const MAX_REFLECTIONS = 200;
const MAX_SESSION_NOTES = 20;
const MAX_RULES = 50;
const DEFAULT_NOTE_TTL_MS = 6 * 60 * 60 * 1000;

const EVENTS_NAMESPACE = "feedback.events";
const SNAPSHOTS_NAMESPACE = "feedback.snapshots";
const REFLECTIONS_NAMESPACE = "feedback.reflections";
const SESSION_NOTES_NAMESPACE = "feedback.session-notes";
const LEARNED_RULES_NAMESPACE = "feedback.learned-rules";
const TARGET_RULES_NAMESPACE = "feedback.target-rules";
const TARGET_RULE_INDEX_NAMESPACE = "feedback.target-rules-index";
const TARGET_SETS_NAMESPACE = "feedback.target-sets";

export type FeedbackKind = "explicit_positive" | "explicit_negative" | "implicit_negative";
export type ReflectionCategory =
  | "missing_image_context"
  | "quoted_context_missing"
  | "misunderstood_intent"
  | "generic_negative"
  | "positive_direct_answer";

export interface FeedbackEventRecord {
  id: string;
  kind: FeedbackKind;
  targetId: string;
  sessionKey?: string;
  processQueryKey?: string;
  userId?: string;
  createdAt: number;
  signalText?: string;
  snapshotId?: string;
}

export interface OutboundReplySnapshot {
  id: string;
  targetId: string;
  sessionKey: string;
  question: string;
  answer: string;
  createdAt: number;
  processQueryKey?: string;
  mode?: "card" | "markdown";
}

export interface ReflectionRecord {
  id: string;
  targetId: string;
  sourceEventId: string;
  kind: FeedbackKind;
  category: ReflectionCategory;
  diagnosis: string;
  suggestedInstruction: string;
  question?: string;
  answer?: string;
  createdAt: number;
}

export interface SessionLearningNote {
  id: string;
  targetId: string;
  instruction: string;
  source: FeedbackKind;
  category: ReflectionCategory;
  createdAt: number;
  expiresAt: number;
}

export interface LearnedRuleRecord {
  ruleId: string;
  category: ReflectionCategory;
  instruction: string;
  negativeCount: number;
  positiveCount: number;
  updatedAt: number;
  enabled: boolean;
  manual?: boolean;
  triggerText?: string;
  forcedReply?: string;
}

interface ListBucket<T> {
  updatedAt: number;
  entries: T[];
}

interface LearnedRuleBucket {
  updatedAt: number;
  rules: Record<string, LearnedRuleRecord>;
}

interface TargetRuleIndexBucket {
  updatedAt: number;
  targetIds: string[];
}

export interface TargetSetRecord {
  name: string;
  targetIds: string[];
  updatedAt: number;
}

interface TargetSetBucket {
  updatedAt: number;
  sets: Record<string, TargetSetRecord>;
}

export interface ScopedLearnedRuleRecord extends LearnedRuleRecord {
  scope: "global" | "target";
  targetId?: string;
}

function trimNewest<T extends { createdAt: number }>(entries: T[], limit: number): T[] {
  return entries.toSorted((left, right) => right.createdAt - left.createdAt).slice(0, limit);
}

function readListBucket<T>(
  namespace: string,
  params: { storePath?: string; accountId: string; targetId: string },
): ListBucket<T> {
  if (!params.storePath) {
    return { updatedAt: 0, entries: [] };
  }
  return readNamespaceJson<ListBucket<T>>(namespace, {
    storePath: params.storePath,
    scope: { accountId: params.accountId, targetId: params.targetId },
    format: "json",
    fallback: { updatedAt: 0, entries: [] },
  });
}

function writeListBucket<T>(
  namespace: string,
  params: { storePath?: string; accountId: string; targetId: string; entries: T[] },
): void {
  if (!params.storePath) {
    return;
  }
  writeNamespaceJsonAtomic(namespace, {
    storePath: params.storePath,
    scope: { accountId: params.accountId, targetId: params.targetId },
    format: "json",
    data: {
      updatedAt: Date.now(),
      entries: params.entries,
    } satisfies ListBucket<T>,
  });
}

export function appendFeedbackEvent(
  params: { storePath?: string; accountId: string; targetId: string; event: FeedbackEventRecord },
): void {
  const bucket = readListBucket<FeedbackEventRecord>(EVENTS_NAMESPACE, params);
  bucket.entries = trimNewest([...bucket.entries, params.event], MAX_EVENTS);
  writeListBucket(EVENTS_NAMESPACE, { ...params, entries: bucket.entries });
}

export function listFeedbackEvents(
  params: { storePath?: string; accountId: string; targetId: string },
): FeedbackEventRecord[] {
  return readListBucket<FeedbackEventRecord>(EVENTS_NAMESPACE, params).entries;
}

export function appendOutboundReplySnapshot(
  params: { storePath?: string; accountId: string; targetId: string; snapshot: OutboundReplySnapshot },
): void {
  const bucket = readListBucket<OutboundReplySnapshot>(SNAPSHOTS_NAMESPACE, params);
  bucket.entries = trimNewest([...bucket.entries, params.snapshot], MAX_SNAPSHOTS);
  writeListBucket(SNAPSHOTS_NAMESPACE, { ...params, entries: bucket.entries });
}

export function listOutboundReplySnapshots(
  params: { storePath?: string; accountId: string; targetId: string },
): OutboundReplySnapshot[] {
  return readListBucket<OutboundReplySnapshot>(SNAPSHOTS_NAMESPACE, params).entries;
}

export function appendReflectionRecord(
  params: { storePath?: string; accountId: string; targetId: string; reflection: ReflectionRecord },
): void {
  const bucket = readListBucket<ReflectionRecord>(REFLECTIONS_NAMESPACE, params);
  bucket.entries = trimNewest([...bucket.entries, params.reflection], MAX_REFLECTIONS);
  writeListBucket(REFLECTIONS_NAMESPACE, { ...params, entries: bucket.entries });
}

export function listReflectionRecords(
  params: { storePath?: string; accountId: string; targetId: string },
): ReflectionRecord[] {
  return readListBucket<ReflectionRecord>(REFLECTIONS_NAMESPACE, params).entries;
}

export function appendSessionLearningNote(
  params: {
    storePath?: string;
    accountId: string;
    targetId: string;
    note: Omit<SessionLearningNote, "expiresAt"> & { expiresAt?: number };
    ttlMs?: number;
  },
): void {
  const ttlMs = params.ttlMs && params.ttlMs > 0 ? params.ttlMs : DEFAULT_NOTE_TTL_MS;
  const nowMs = Date.now();
  const bucket = readListBucket<SessionLearningNote>(SESSION_NOTES_NAMESPACE, params);
  const retained = bucket.entries.filter((note) => note.expiresAt > nowMs);
  retained.unshift({
    ...params.note,
    expiresAt: params.note.expiresAt ?? nowMs + ttlMs,
  });
  writeListBucket(SESSION_NOTES_NAMESPACE, {
    ...params,
    entries: trimNewest(retained, MAX_SESSION_NOTES),
  });
}

export function listActiveSessionLearningNotes(
  params: { storePath?: string; accountId: string; targetId: string; nowMs?: number },
): SessionLearningNote[] {
  const nowMs = params.nowMs ?? Date.now();
  return readListBucket<SessionLearningNote>(SESSION_NOTES_NAMESPACE, params).entries.filter(
    (note) => note.expiresAt > nowMs,
  );
}

export function upsertLearnedRule(
  params: { storePath?: string; accountId: string; rule: LearnedRuleRecord },
): void {
  if (!params.storePath) {
    return;
  }
  const bucket = readNamespaceJson<LearnedRuleBucket>(LEARNED_RULES_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId },
    format: "json",
    fallback: { updatedAt: 0, rules: {} },
  });
  bucket.rules[params.rule.ruleId] = params.rule;
  const trimmedRules = Object.values(bucket.rules)
    .toSorted((left, right) => right.updatedAt - left.updatedAt)
    .slice(0, MAX_RULES);
  const rules: Record<string, LearnedRuleRecord> = {};
  for (const rule of trimmedRules) {
    rules[rule.ruleId] = rule;
  }
  writeNamespaceJsonAtomic(LEARNED_RULES_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId },
    format: "json",
    data: { updatedAt: Date.now(), rules } satisfies LearnedRuleBucket,
  });
}

export function listLearnedRules(
  params: { storePath?: string; accountId: string },
): LearnedRuleRecord[] {
  if (!params.storePath) {
    return [];
  }
  const bucket = readNamespaceJson<LearnedRuleBucket>(LEARNED_RULES_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId },
    format: "json",
    fallback: { updatedAt: 0, rules: {} },
  });
  return Object.values(bucket.rules).toSorted((left, right) => right.updatedAt - left.updatedAt);
}

export function disableLearnedRule(
  params: { storePath?: string; accountId: string; ruleId: string },
): boolean {
  if (!params.storePath) {
    return false;
  }
  const bucket = readNamespaceJson<LearnedRuleBucket>(LEARNED_RULES_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId },
    format: "json",
    fallback: { updatedAt: 0, rules: {} },
  });
  const existing = bucket.rules[params.ruleId];
  if (!existing) {
    return false;
  }
  bucket.rules[params.ruleId] = {
    ...existing,
    enabled: false,
    updatedAt: Date.now(),
  };
  writeNamespaceJsonAtomic(LEARNED_RULES_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId },
    format: "json",
    data: { updatedAt: Date.now(), rules: bucket.rules } satisfies LearnedRuleBucket,
  });
  return true;
}

export function deleteLearnedRule(
  params: { storePath?: string; accountId: string; ruleId: string },
): boolean {
  if (!params.storePath) {
    return false;
  }
  const bucket = readNamespaceJson<LearnedRuleBucket>(LEARNED_RULES_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId },
    format: "json",
    fallback: { updatedAt: 0, rules: {} },
  });
  if (!bucket.rules[params.ruleId]) {
    return false;
  }
  delete bucket.rules[params.ruleId];
  writeNamespaceJsonAtomic(LEARNED_RULES_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId },
    format: "json",
    data: { updatedAt: Date.now(), rules: bucket.rules } satisfies LearnedRuleBucket,
  });
  return true;
}

function readTargetRuleIndex(
  params: { storePath?: string; accountId: string },
): TargetRuleIndexBucket {
  if (!params.storePath) {
    return { updatedAt: 0, targetIds: [] };
  }
  return readNamespaceJson<TargetRuleIndexBucket>(TARGET_RULE_INDEX_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId },
    format: "json",
    fallback: { updatedAt: 0, targetIds: [] },
  });
}

function writeTargetRuleIndex(
  params: { storePath?: string; accountId: string; targetIds: string[] },
): void {
  if (!params.storePath) {
    return;
  }
  writeNamespaceJsonAtomic(TARGET_RULE_INDEX_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId },
    format: "json",
    data: {
      updatedAt: Date.now(),
      targetIds: [...new Set(params.targetIds.filter((targetId) => targetId.trim()))],
    } satisfies TargetRuleIndexBucket,
  });
}

function readTargetRuleBucket(
  params: { storePath?: string; accountId: string; targetId: string },
): LearnedRuleBucket {
  if (!params.storePath) {
    return { updatedAt: 0, rules: {} };
  }
  return readNamespaceJson<LearnedRuleBucket>(TARGET_RULES_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId, targetId: params.targetId },
    format: "json",
    fallback: { updatedAt: 0, rules: {} },
  });
}

function writeTargetRuleBucket(
  params: { storePath?: string; accountId: string; targetId: string; bucket: LearnedRuleBucket },
): void {
  if (!params.storePath) {
    return;
  }
  writeNamespaceJsonAtomic(TARGET_RULES_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId, targetId: params.targetId },
    format: "json",
    data: params.bucket,
  });
}

export function upsertTargetRule(
  params: { storePath?: string; accountId: string; targetId: string; rule: LearnedRuleRecord },
): void {
  if (!params.storePath) {
    return;
  }
  const bucket = readTargetRuleBucket(params);
  bucket.rules[params.rule.ruleId] = params.rule;
  const trimmedRules = Object.values(bucket.rules)
    .toSorted((left, right) => right.updatedAt - left.updatedAt)
    .slice(0, MAX_RULES);
  const rules: Record<string, LearnedRuleRecord> = {};
  for (const rule of trimmedRules) {
    rules[rule.ruleId] = rule;
  }
  writeTargetRuleBucket({
    storePath: params.storePath,
    accountId: params.accountId,
    targetId: params.targetId,
    bucket: { updatedAt: Date.now(), rules },
  });
  const index = readTargetRuleIndex({ storePath: params.storePath, accountId: params.accountId });
  writeTargetRuleIndex({
    storePath: params.storePath,
    accountId: params.accountId,
    targetIds: [...index.targetIds, params.targetId],
  });
}

export function listTargetRules(
  params: { storePath?: string; accountId: string; targetId: string },
): LearnedRuleRecord[] {
  return Object.values(readTargetRuleBucket(params).rules).toSorted(
    (left, right) => right.updatedAt - left.updatedAt,
  );
}

export function listAllScopedRules(
  params: { storePath?: string; accountId: string },
): ScopedLearnedRuleRecord[] {
  const globalRules = listLearnedRules(params).map((rule) => ({ ...rule, scope: "global" as const }));
  const targetIds = readTargetRuleIndex(params).targetIds;
  const targetRules = targetIds.flatMap((targetId) =>
    listTargetRules({ ...params, targetId }).map((rule) => ({
      ...rule,
      scope: "target" as const,
      targetId,
    })),
  );
  return [...targetRules, ...globalRules].toSorted((left, right) => right.updatedAt - left.updatedAt);
}

export function disableScopedRule(
  params: { storePath?: string; accountId: string; ruleId: string },
): { existed: boolean; scope?: "global" | "target"; targetId?: string } {
  if (disableLearnedRule(params)) {
    return { existed: true, scope: "global" };
  }
  const targetIds = readTargetRuleIndex(params).targetIds;
  for (const targetId of targetIds) {
    const bucket = readTargetRuleBucket({ ...params, targetId });
    const existing = bucket.rules[params.ruleId];
    if (!existing) {
      continue;
    }
    bucket.rules[params.ruleId] = {
      ...existing,
      enabled: false,
      updatedAt: Date.now(),
    };
    writeTargetRuleBucket({
      storePath: params.storePath,
      accountId: params.accountId,
      targetId,
      bucket: { updatedAt: Date.now(), rules: bucket.rules },
    });
    return { existed: true, scope: "target", targetId };
  }
  return { existed: false };
}

export function deleteScopedRule(
  params: { storePath?: string; accountId: string; ruleId: string },
): { existed: boolean; scope?: "global" | "target"; targetId?: string } {
  if (deleteLearnedRule(params)) {
    return { existed: true, scope: "global" };
  }
  const targetIds = readTargetRuleIndex(params).targetIds;
  for (const targetId of targetIds) {
    const bucket = readTargetRuleBucket({ ...params, targetId });
    if (!bucket.rules[params.ruleId]) {
      continue;
    }
    delete bucket.rules[params.ruleId];
    writeTargetRuleBucket({
      storePath: params.storePath,
      accountId: params.accountId,
      targetId,
      bucket: { updatedAt: Date.now(), rules: bucket.rules },
    });
    return { existed: true, scope: "target", targetId };
  }
  return { existed: false };
}

export function upsertTargetSet(
  params: { storePath?: string; accountId: string; name: string; targetIds: string[] },
): void {
  if (!params.storePath) {
    return;
  }
  const bucket = readNamespaceJson<TargetSetBucket>(TARGET_SETS_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId },
    format: "json",
    fallback: { updatedAt: 0, sets: {} },
  });
  bucket.sets[params.name] = {
    name: params.name,
    targetIds: [...new Set(params.targetIds.filter((targetId) => targetId.trim()))],
    updatedAt: Date.now(),
  };
  writeNamespaceJsonAtomic(TARGET_SETS_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId },
    format: "json",
    data: { updatedAt: Date.now(), sets: bucket.sets } satisfies TargetSetBucket,
  });
}

export function getTargetSet(
  params: { storePath?: string; accountId: string; name: string },
): TargetSetRecord | null {
  if (!params.storePath) {
    return null;
  }
  const bucket = readNamespaceJson<TargetSetBucket>(TARGET_SETS_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId },
    format: "json",
    fallback: { updatedAt: 0, sets: {} },
  });
  return bucket.sets[params.name] || null;
}

export function listTargetSets(
  params: { storePath?: string; accountId: string },
): TargetSetRecord[] {
  if (!params.storePath) {
    return [];
  }
  const bucket = readNamespaceJson<TargetSetBucket>(TARGET_SETS_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId },
    format: "json",
    fallback: { updatedAt: 0, sets: {} },
  });
  return Object.values(bucket.sets).toSorted((left, right) => right.updatedAt - left.updatedAt);
}
