// Per-session mutex to serialize dispatchReply calls for the same session.
// Prevents concurrent dispatch from producing empty replies when the runtime
// cannot handle overlapping requests on the same session key.

const locks = new Map<string, Promise<void>>();

export const SESSION_LOCK_NAMESPACE_POLICY = "memory-only" as const;

/**
 * Acquire a per-session lock. Returns a release function that MUST be called
 * (typically in a `finally` block) to unblock the next queued caller.
 *
 * Different session keys run in parallel; same key runs serially.
 */
export async function acquireSessionLock(sessionKey: string): Promise<() => void> {
    let release!: () => void;
    const gate = new Promise<void>((r) => {
        release = r;
    });
    const prev = locks.get(sessionKey) ?? Promise.resolve();
    locks.set(sessionKey, gate);
    await prev;
    return () => {
        if (locks.get(sessionKey) === gate) {
            locks.delete(sessionKey);
        }
        release();
    };
}

/** Visible for testing only. */
export function _getLocksMapForTest(): Map<string, Promise<void>> {
    return locks;
}
