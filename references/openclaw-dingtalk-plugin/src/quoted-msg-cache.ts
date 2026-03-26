import { readNamespaceJson, writeNamespaceJsonAtomic } from "./persistence-store";

const DEFAULT_TTL_MS = 24 * 60 * 60 * 1000;
const MAX_ENTRIES_PER_CONVERSATION = 100;
const MAX_CONVERSATIONS = 1000;
const QUOTED_MSG_NAMESPACE = "quoted.msg-download-code";

export interface DownloadCodeCacheEntry {
  downloadCode?: string;
  msgType: string;
  createdAt: number;
  expiresAt: number;
  spaceId?: string;
  fileId?: string;
}

interface ConversationBucket {
  entries: Map<string, DownloadCodeCacheEntry>;
  lastActiveAt: number;
}

interface PersistedConversationBucket {
  updatedAt: number;
  entries: Record<string, DownloadCodeCacheEntry>;
}

function isValidPersistedEntry(entry: unknown): entry is DownloadCodeCacheEntry {
  if (!entry || typeof entry !== "object") {
    return false;
  }

  const candidate = entry as Partial<DownloadCodeCacheEntry>;
  return (
    typeof candidate.msgType === "string" &&
    candidate.msgType.length > 0 &&
    typeof candidate.createdAt === "number" &&
    Number.isFinite(candidate.createdAt) &&
    typeof candidate.expiresAt === "number" &&
    Number.isFinite(candidate.expiresAt) &&
    ((typeof candidate.downloadCode === "string" && candidate.downloadCode.length > 0) ||
      (typeof candidate.spaceId === "string" &&
        candidate.spaceId.length > 0 &&
        typeof candidate.fileId === "string" &&
        candidate.fileId.length > 0))
  );
}

const store = new Map<string, ConversationBucket>();

function getBucket(conversationId: string): ConversationBucket | undefined {
  return store.get(conversationId);
}

function getOrCreateBucket(conversationId: string): ConversationBucket {
  let bucket = store.get(conversationId);
  if (!bucket) {
    bucket = { entries: new Map(), lastActiveAt: Date.now() };
    store.set(conversationId, bucket);
    evictConversationsIfNeeded();
  }
  bucket.lastActiveAt = Date.now();
  return bucket;
}

function evictConversationsIfNeeded(): void {
  if (store.size <= MAX_CONVERSATIONS) {
    return;
  }
  let oldestKey: string | undefined;
  let oldestTime = Infinity;
  for (const [key, bucket] of store) {
    if (bucket.lastActiveAt < oldestTime) {
      oldestTime = bucket.lastActiveAt;
      oldestKey = key;
    }
  }
  if (oldestKey) {
    store.delete(oldestKey);
  }
}

function evictEntriesIfNeeded(bucket: ConversationBucket): void {
  if (bucket.entries.size <= MAX_ENTRIES_PER_CONVERSATION) {
    return;
  }
  let oldestKey: string | undefined;
  let oldestTime = Infinity;
  for (const [key, entry] of bucket.entries) {
    if (entry.createdAt < oldestTime) {
      oldestTime = entry.createdAt;
      oldestKey = key;
    }
  }
  if (oldestKey) {
    bucket.entries.delete(oldestKey);
  }
}

function purgeExpiredEntries(bucket: ConversationBucket): void {
  const now = Date.now();
  for (const [key, entry] of bucket.entries) {
    if (now >= entry.expiresAt) {
      bucket.entries.delete(key);
    }
  }
}

function loadFromPersistence(
  accountId: string,
  conversationId: string,
  storePath?: string,
): ConversationBucket | null {
  if (!storePath) {
    return null;
  }
  const persisted = readNamespaceJson<PersistedConversationBucket>(QUOTED_MSG_NAMESPACE, {
    storePath,
    scope: { accountId, conversationId },
    format: "json",
    fallback: { updatedAt: 0, entries: {} },
  });
  const keys = Object.keys(persisted.entries || {});
  if (keys.length === 0) {
    return null;
  }
  const bucket: ConversationBucket = {
    entries: new Map<string, DownloadCodeCacheEntry>(),
    lastActiveAt: Date.now(),
  };
  for (const key of keys) {
    const entry = persisted.entries[key];
    if (!isValidPersistedEntry(entry)) {
      continue;
    }
    bucket.entries.set(key, entry);
  }
  purgeExpiredEntries(bucket);
  evictEntriesIfNeeded(bucket);
  return bucket;
}

function persistBucket(
  accountId: string,
  conversationId: string,
  bucket: ConversationBucket,
  storePath?: string,
): void {
  if (!storePath) {
    return;
  }
  const entries: Record<string, DownloadCodeCacheEntry> = {};
  for (const [msgId, entry] of bucket.entries.entries()) {
    entries[msgId] = entry;
  }
  writeNamespaceJsonAtomic(QUOTED_MSG_NAMESPACE, {
    storePath,
    scope: { accountId, conversationId },
    format: "json",
    data: {
      updatedAt: Date.now(),
      entries,
    } satisfies PersistedConversationBucket,
  });
}

export function cacheInboundDownloadCode(
  accountId: string,
  conversationId: string,
  msgId: string,
  downloadCode: string | undefined,
  msgType: string,
  createdAt: number,
  extra?: { spaceId?: string; fileId?: string; storePath?: string },
): void {
  if (!downloadCode && !extra?.spaceId && !extra?.fileId) {
    return;
  }
  const scopedKey = `${accountId}:${conversationId}`;
  const bucket = getOrCreateBucket(scopedKey);
  purgeExpiredEntries(bucket);
  bucket.entries.set(msgId, {
    downloadCode,
    msgType,
    createdAt,
    expiresAt: Date.now() + DEFAULT_TTL_MS,
    spaceId: extra?.spaceId,
    fileId: extra?.fileId,
  });
  evictEntriesIfNeeded(bucket);
  persistBucket(accountId, conversationId, bucket, extra?.storePath);
}

export function getCachedDownloadCode(
  accountId: string,
  conversationId: string,
  msgId: string,
  storePath?: string,
): DownloadCodeCacheEntry | null {
  const scopedKey = `${accountId}:${conversationId}`;
  let bucket = getBucket(scopedKey);
  if (!bucket && storePath) {
    const loaded = loadFromPersistence(accountId, conversationId, storePath);
    if (loaded) {
      store.set(scopedKey, loaded);
      evictConversationsIfNeeded();
      bucket = loaded;
    }
  }
  if (!bucket) {
    return null;
  }
  const entry = bucket.entries.get(msgId);
  if (!entry) {
    return null;
  }
  if (Date.now() >= entry.expiresAt) {
    bucket.entries.delete(msgId);
    return null;
  }
  bucket.lastActiveAt = Date.now();
  return entry;
}

export function clearQuotedMsgCacheForTest(): void {
  store.clear();
}
