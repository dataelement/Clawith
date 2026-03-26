// ============ Message Deduplication ============
// Prevent duplicate processing when DingTalk retries delivery.
// In-memory TTL map + lazy cleanup keeps overhead small.
export const DEDUP_NAMESPACE_POLICY = "memory-only" as const;

const processedMessages = new Map<string, number>();
const MESSAGE_DEDUP_TTL = 60000;
const MESSAGE_DEDUP_MAX_SIZE = 1000;
let messageCounter = 0;

// Check whether message is still inside dedup window.
export function isMessageProcessed(dedupKey: string): boolean {
  const now = Date.now();
  const expiresAt = processedMessages.get(dedupKey);

  if (expiresAt === undefined) {
    return false;
  }

  if (now >= expiresAt) {
    processedMessages.delete(dedupKey);
    return false;
  }

  return true;
}

// Mark message as processed and lazily cleanup expired entries.
export function markMessageProcessed(dedupKey: string): void {
  const expiresAt = Date.now() + MESSAGE_DEDUP_TTL;
  processedMessages.set(dedupKey, expiresAt);

  // Hard cap for burst protection.
  if (processedMessages.size > MESSAGE_DEDUP_MAX_SIZE) {
    const now = Date.now();
    for (const [key, expiry] of processedMessages.entries()) {
      if (now >= expiry) {
        processedMessages.delete(key);
      }
    }

    // Safety valve: if still over cap after expired-entry sweep, drop oldest entries.
    if (processedMessages.size > MESSAGE_DEDUP_MAX_SIZE) {
      const removeCount = processedMessages.size - MESSAGE_DEDUP_MAX_SIZE;
      let removed = 0;
      for (const key of processedMessages.keys()) {
        processedMessages.delete(key);
        if (++removed >= removeCount) {
          break;
        }
      }
    }
    return;
  }

  // Deterministic lightweight cleanup every 10 messages.
  messageCounter++;
  if (messageCounter >= 10) {
    messageCounter = 0;
    const now = Date.now();
    for (const [key, expiry] of processedMessages.entries()) {
      if (now >= expiry) {
        processedMessages.delete(key);
      }
    }
  }
}
