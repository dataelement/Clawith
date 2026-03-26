import { readNamespaceJson, writeNamespaceJsonAtomic } from "./persistence-store";

const QUOTE_JOURNAL_NAMESPACE = "quoted.msg-journal";
const QUOTE_JOURNAL_VERSION = 1;
export const DEFAULT_JOURNAL_TTL_DAYS = 7;
const MAX_RECORDS_PER_SCOPE = 1000;

type JournalEntry = {
  msgId: string;
  messageType: string;
  text?: string;
  createdAt: number;
};

type QuoteJournalState = {
  version: number;
  updatedAt: number;
  records: JournalEntry[];
};

const stateCache = new Map<string, QuoteJournalState>();

function getScopeKey(params: {
  storePath: string;
  accountId: string;
  conversationId: string | null;
}): string {
  return JSON.stringify([
    params.storePath,
    params.accountId,
    params.conversationId || null,
  ]);
}

function fallbackState(): QuoteJournalState {
  return {
    version: QUOTE_JOURNAL_VERSION,
    updatedAt: Date.now(),
    records: [],
  };
}

function normalizeEntry(entry: unknown): JournalEntry | null {
  if (!entry || typeof entry !== "object") {
    return null;
  }
  const candidate = entry as Partial<JournalEntry>;
  if (typeof candidate.msgId !== "string" || typeof candidate.createdAt !== "number") {
    return null;
  }
  return {
    msgId: candidate.msgId,
    messageType: typeof candidate.messageType === "string" ? candidate.messageType : "text",
    text: typeof candidate.text === "string" ? candidate.text : undefined,
    createdAt: candidate.createdAt,
  };
}

function normalizeState(parsed: Partial<QuoteJournalState>): QuoteJournalState {
  const records = Array.isArray(parsed.records)
    ? parsed.records.map((entry) => normalizeEntry(entry)).filter((entry): entry is JournalEntry => entry !== null)
    : [];
  return {
    version: typeof parsed.version === "number" ? parsed.version : QUOTE_JOURNAL_VERSION,
    updatedAt: typeof parsed.updatedAt === "number" ? parsed.updatedAt : Date.now(),
    records,
  };
}

function loadState(params: {
  storePath: string;
  accountId: string;
  conversationId: string | null;
}): QuoteJournalState {
  const scopeKey = getScopeKey(params);
  const cached = stateCache.get(scopeKey);
  if (cached) {
    return cached;
  }

  const persisted = readNamespaceJson<Partial<QuoteJournalState>>(QUOTE_JOURNAL_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId, conversationId: params.conversationId || undefined },
    format: "json",
    fallback: fallbackState(),
  });
  const normalized = normalizeState(persisted);
  stateCache.set(scopeKey, normalized);
  return normalized;
}

function writeState(params: {
  storePath: string;
  accountId: string;
  conversationId: string | null;
  state: QuoteJournalState;
}): void {
  stateCache.set(getScopeKey(params), params.state);
  writeNamespaceJsonAtomic(QUOTE_JOURNAL_NAMESPACE, {
    storePath: params.storePath,
    scope: { accountId: params.accountId, conversationId: params.conversationId || undefined },
    format: "json",
    data: params.state,
  });
}

function pruneByTtl(records: JournalEntry[], ttlDays: number, nowMs: number): JournalEntry[] {
  if (!ttlDays || ttlDays <= 0) {
    return records;
  }
  const cutoff = nowMs - ttlDays * 24 * 60 * 60 * 1000;
  return records.filter((entry) => entry.createdAt >= cutoff);
}

function capRecords(records: JournalEntry[]): JournalEntry[] {
  if (records.length <= MAX_RECORDS_PER_SCOPE) {
    return records;
  }
  return records.slice(-MAX_RECORDS_PER_SCOPE);
}

export function appendQuoteJournalEntry(params: {
  storePath: string;
  accountId: string;
  conversationId: string | null;
  msgId: string;
  messageType: string;
  text?: string;
  createdAt: number;
  ttlDays?: number;
  nowMs?: number;
}): void {
  const now = params.nowMs ?? Date.now();
  const ttlDays = params.ttlDays ?? DEFAULT_JOURNAL_TTL_DAYS;
  const state = loadState(params);
  const records = pruneByTtl(state.records, ttlDays, now);
  records.push({
    msgId: params.msgId,
    messageType: params.messageType,
    text: params.text,
    createdAt: params.createdAt,
  });
  const cappedRecords = capRecords(records);
  writeState({
    storePath: params.storePath,
    accountId: params.accountId,
    conversationId: params.conversationId,
    state: {
      version: QUOTE_JOURNAL_VERSION,
      updatedAt: now,
      records: cappedRecords,
    },
  });
}

export function cleanupExpiredQuoteJournalEntries(params: {
  storePath: string;
  accountId: string;
  conversationId: string | null;
  ttlDays: number;
  nowMs?: number;
}): number {
  const now = params.nowMs ?? Date.now();
  const state = loadState(params);
  const kept = pruneByTtl(state.records, params.ttlDays, now);
  const removed = state.records.length - kept.length;
  if (removed > 0) {
    writeState({
      storePath: params.storePath,
      accountId: params.accountId,
      conversationId: params.conversationId,
      state: {
        version: QUOTE_JOURNAL_VERSION,
        updatedAt: now,
        records: kept,
      },
    });
  }
  return removed;
}

export function resolveQuotedMessageById(params: {
  storePath: string;
  accountId: string;
  conversationId: string | null;
  originalMsgId: string;
  ttlDays?: number;
  nowMs?: number;
}): { msgId: string; text?: string; createdAt: number } | null {
  const state = loadState(params);
  const now = params.nowMs ?? Date.now();
  const ttlDays = params.ttlDays ?? DEFAULT_JOURNAL_TTL_DAYS;
  const records = capRecords(pruneByTtl(state.records, ttlDays, now));
  for (let i = records.length - 1; i >= 0; i--) {
    const entry = records[i];
    if (entry.msgId === params.originalMsgId) {
      return { msgId: entry.msgId, text: entry.text, createdAt: entry.createdAt };
    }
  }
  return null;
}

export async function appendOutboundToQuoteJournal(params: {
  storePath: string;
  accountId: string;
  conversationId: string | null;
  messageId?: string;
  text?: string;
  messageType?: string;
  log?: unknown;
}): Promise<void> {
  try {
    if (!params.messageId) {
      return;
    }
    appendQuoteJournalEntry({
      storePath: params.storePath,
      accountId: params.accountId,
      conversationId: params.conversationId || null,
      msgId: params.messageId,
      messageType: params.messageType || "outbound",
      text: params.text,
      createdAt: Date.now(),
    });
  } catch (err) {
    (params.log as { debug?: (message: string) => void } | undefined)?.debug?.(
      `[quote-journal] appendOutbound failed: ${String(err)}`,
    );
  }
}

export async function appendProactiveOutboundJournal(params: {
  storePath: string;
  accountId: string;
  conversationId: string | null;
  messageId?: string;
  text?: string;
  messageType?: string;
  log?: unknown;
}): Promise<void> {
  return appendOutboundToQuoteJournal(params);
}
