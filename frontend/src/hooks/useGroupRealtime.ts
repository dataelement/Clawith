/**
 * Group chat transport. The page never learns how messages arrive.
 *
 * Target design: REST for sending and history, WebSocket for push, cursor backfill on reconnect,
 * polling only while the socket is unavailable. The socket is group-scoped so one connection can
 * refresh unread state for every session, while message catch-up remains session-scoped.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { groupApi } from '../services/groupApi';
import { useAuthStore } from '../stores';
import type { GroupMessage } from '../types/group';

const POLL_INTERVAL_MS = 4000;
const BACKFILL_PAGE_SIZE = 50;
const WS_RETRY_BASE_MS = 2000;
const WS_RETRY_MAX_MS = 30000;
const WS_READY_TIMEOUT_MS = 10000;

/** Socket closed for a reason retrying cannot fix: not a member, group gone, token rejected. */
const NO_RETRY_CLOSE_CODES = new Set([1008, 4001, 4002, 4003]);

export type RealtimeStatus = 'connecting' | 'live' | 'polling' | 'offline';

interface GroupSocketEvent {
    type: string;
    session_id?: string;
    message?: GroupMessage;
}

/**
 * Compare two `<created_at ISO>|<uuid>` cursors by the (created_at, id) contract.
 *
 * The backend stamps microseconds (`2026-07-14T06:16:04.577660+00:00`) but Date only holds
 * milliseconds, so two messages under a millisecond apart would tie and fall through to the id —
 * an order the server does not share, which can drop a message at the backfill boundary. When the
 * millisecond ties, compare the raw timestamps: Python emits a fixed-width prefix and either no
 * fraction or exactly six digits, so lexicographic order matches chronological order.
 */
export function compareCursor(a: string, b: string): number {
    const splitCursor = (value: string): [string, string] => {
        const separator = value.lastIndexOf('|');
        return [value.slice(0, separator), value.slice(separator + 1)];
    };
    const [stampA, idA] = splitCursor(a);
    const [stampB, idB] = splitCursor(b);

    const timeA = Date.parse(stampA);
    const timeB = Date.parse(stampB);
    if (!Number.isNaN(timeA) && !Number.isNaN(timeB) && timeA !== timeB) return timeA - timeB;

    if (stampA !== stampB) return stampA < stampB ? -1 : 1;
    return idA < idB ? -1 : idA > idB ? 1 : 0;
}

/**
 * Every message newer than `cursor`, ascending. With no cursor, begin with the newest page. The
 * walk has no page-count cap: a reconnect is not complete until a short page proves it caught up.
 */
export async function fetchMessagesSince(
    groupId: string,
    sessionId: string,
    cursor?: string,
    signal?: AbortSignal,
): Promise<GroupMessage[]> {
    const collected: GroupMessage[] = [];
    // This cursor is deliberately local to the HTTP walk. A newer WebSocket event may update the
    // visible high-water mark while a page is in flight, but it must never make this walk skip the
    // messages between the frozen starting point and that event.
    let after = cursor;
    while (true) {
        const batch = await groupApi.messages(groupId, sessionId, {
            limit: BACKFILL_PAGE_SIZE,
            after,
            signal,
        });
        if (batch.length === 0) break;

        const next = batch[batch.length - 1].cursor;
        if (after && compareCursor(next, after) <= 0) {
            throw new Error('Group message `after` cursor did not advance.');
        }
        collected.push(...batch);
        if (batch.length < BACKFILL_PAGE_SIZE) break;
        after = next;
    }
    return collected;
}

interface UseGroupRealtimeOptions {
    groupId?: string;
    sessionId?: string;
    /** Ascending, may contain messages the page already has — dedupe by id on the receiving end. */
    onMessages: (sessionId: string, messages: GroupMessage[]) => void;
    /** Something happened somewhere in the group — refresh session list unread badges. */
    onGroupActivity?: () => void;
    enabled?: boolean;
}

export function useGroupRealtime({
    groupId,
    sessionId,
    onMessages,
    onGroupActivity,
    enabled = true,
}: UseGroupRealtimeOptions): { status: RealtimeStatus } {
    const [status, setStatus] = useState<RealtimeStatus>('connecting');
    const token = useAuthStore((state) => state.token);

    // Callbacks live in refs so a re-render never tears down the socket.
    const onMessagesRef = useRef(onMessages);
    const onGroupActivityRef = useRef(onGroupActivity);
    onMessagesRef.current = onMessages;
    onGroupActivityRef.current = onGroupActivity;

    const sessionIdRef = useRef(sessionId);
    sessionIdRef.current = sessionId;

    // Only a completed REST walk may advance the safe backfill cursor. WebSocket events are
    // best-effort and can arrive out of Message Position order, so their high-water mark is kept
    // separately and is never used as an `after` starting point.
    const restCursorByScopeRef = useRef(new Map<string, string>());
    const wsObservedCursorByScopeRef = useRef(new Map<string, string>());
    const inFlightByScopeRef = useRef(new Map<string, Promise<boolean>>());
    const abortByScopeRef = useRef(new Map<string, AbortController>());
    const generationByScopeRef = useRef(new Map<string, number>());
    const ensurePollingRef = useRef<(() => void) | null>(null);

    const advanceCursor = (target: Map<string, string>, scope: string, cursor: string) => {
        const current = target.get(scope);
        if (!current || compareCursor(cursor, current) > 0) {
            target.set(scope, cursor);
        }
    };

    const cancelScope = (scope: string) => {
        abortByScopeRef.current.get(scope)?.abort();
        abortByScopeRef.current.delete(scope);
        inFlightByScopeRef.current.delete(scope);
        generationByScopeRef.current.set(
            scope,
            (generationByScopeRef.current.get(scope) ?? 0) + 1,
        );
    };

    const cancelAllScopes = () => {
        for (const scope of abortByScopeRef.current.keys()) cancelScope(scope);
    };

    const catchUp = useCallback((targetSession = sessionIdRef.current): Promise<boolean> => {
        if (!groupId || !targetSession) return Promise.resolve(true);
        const scope = `${groupId}:${targetSession}`;
        const existing = inFlightByScopeRef.current.get(scope);
        if (existing) return existing;

        const generation = (generationByScopeRef.current.get(scope) ?? 0) + 1;
        generationByScopeRef.current.set(scope, generation);
        const controller = new AbortController();
        abortByScopeRef.current.set(scope, controller);

        const knownCursor = restCursorByScopeRef.current.get(scope);

        const run = (async () => {
            try {
                const fresh = await fetchMessagesSince(
                    groupId,
                    targetSession,
                    knownCursor,
                    controller.signal,
                );
                if (
                    controller.signal.aborted
                    || generationByScopeRef.current.get(scope) !== generation
                    || sessionIdRef.current !== targetSession
                ) {
                    return false;
                }
                if (fresh.length > 0) {
                    onMessagesRef.current(targetSession, fresh);
                    advanceCursor(
                        restCursorByScopeRef.current,
                        scope,
                        fresh[fresh.length - 1].cursor,
                    );
                }
                return true;
            } catch (error: any) {
                if (error?.name !== 'AbortError') {
                    console.warn(`[groups] Message catch-up failed for session ${targetSession}:`, error);
                }
                return false;
            } finally {
                if (generationByScopeRef.current.get(scope) === generation) {
                    abortByScopeRef.current.delete(scope);
                    inFlightByScopeRef.current.delete(scope);
                }
            }
        })();
        inFlightByScopeRef.current.set(scope, run);
        return run;
    }, [groupId]);

    const catchUpThroughObserved = useCallback(async (
        targetSession = sessionIdRef.current,
    ): Promise<boolean> => {
        const synchronized = await catchUp(targetSession);
        if (!synchronized || !groupId || !targetSession || sessionIdRef.current !== targetSession) {
            return synchronized;
        }

        const scope = `${groupId}:${targetSession}`;
        const observed = wsObservedCursorByScopeRef.current.get(scope);
        const confirmed = restCursorByScopeRef.current.get(scope);
        if (observed && (!confirmed || compareCursor(confirmed, observed) < 0)) {
            // An event arrived after the REST walk's last query. Repeat from the REST-confirmed
            // cursor; concurrent callers coalesce through inFlightByScopeRef.
            const followedUp = await catchUp(targetSession);
            if (!followedUp || sessionIdRef.current !== targetSession) return false;

            // A WS event can race the follow-up too. Do not declare the transport synchronized
            // until REST has actually confirmed the latest observed position; polling will retry.
            const latestObserved = wsObservedCursorByScopeRef.current.get(scope);
            const latestConfirmed = restCursorByScopeRef.current.get(scope);
            return !latestObserved
                || Boolean(
                    latestConfirmed
                    && compareCursor(latestConfirmed, latestObserved) >= 0,
                );
        }
        return true;
    }, [groupId, catchUp]);

    // Authentication and group changes are hard scope boundaries. Session changes retain their
    // cursor for a later return, but cancel any request that could still deliver into the old pane.
    useEffect(() => {
        cancelAllScopes();
        restCursorByScopeRef.current.clear();
        wsObservedCursorByScopeRef.current.clear();
        generationByScopeRef.current.clear();
        return cancelAllScopes;
    }, [groupId, token]);

    useEffect(() => {
        const activeScope = groupId && sessionId ? `${groupId}:${sessionId}` : null;
        for (const scope of abortByScopeRef.current.keys()) {
            if (scope !== activeScope) cancelScope(scope);
        }
    }, [groupId, sessionId]);

    useEffect(() => {
        if (!enabled || !groupId || !token) {
            ensurePollingRef.current = null;
            setStatus('offline');
            return;
        }
        const authToken = token;

        let disposed = false;
        let socket: WebSocket | null = null;
        let retryTimer: ReturnType<typeof setTimeout> | null = null;
        let pollTimer: ReturnType<typeof setInterval> | null = null;
        let readyTimer: ReturnType<typeof setTimeout> | null = null;
        let retryAttempt = 0;
        let connected = false;

        const stopPolling = () => {
            if (!pollTimer) return;
            clearInterval(pollTimer);
            pollTimer = null;
        };

        const clearReadyTimer = () => {
            if (!readyTimer) return;
            clearTimeout(readyTimer);
            readyTimer = null;
        };

        const promoteIfSynchronized = async () => {
            onGroupActivityRef.current?.();
            const synchronized = await catchUpThroughObserved();
            if (disposed) return;
            if (synchronized && connected && socket?.readyState === WebSocket.OPEN) {
                retryAttempt = 0;
                stopPolling();
                setStatus('live');
            } else if (!synchronized) {
                startPolling();
            }
        };

        const poll = () => {
            if (disposed) return;
            // Session badges are part of the fallback contract even if the active session has no
            // new messages. React Query coalesces overlapping invalidations for this query.
            onGroupActivityRef.current?.();
            void catchUpThroughObserved().then((synchronized) => {
                if (
                    !disposed
                    && synchronized
                    && connected
                    && socket?.readyState === WebSocket.OPEN
                ) {
                    retryAttempt = 0;
                    stopPolling();
                    setStatus('live');
                }
            });
        };

        function startPolling() {
            if (disposed || pollTimer) return;
            setStatus('polling');
            poll();
            pollTimer = setInterval(poll, POLL_INTERVAL_MS);
        }
        ensurePollingRef.current = startPolling;

        const scheduleReconnect = () => {
            if (disposed || retryTimer) return;
            const delay = Math.min(
                WS_RETRY_MAX_MS,
                WS_RETRY_BASE_MS * (2 ** Math.min(retryAttempt, 4)),
            );
            retryAttempt += 1;
            retryTimer = setTimeout(() => {
                retryTimer = null;
                connect();
            }, delay);
        };

        function connect() {
            if (disposed) return;
            if (
                socket
                && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)
            ) {
                return;
            }

            setStatus((current) => (current === 'polling' ? current : 'connecting'));
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const url = `${protocol}//${window.location.host}/ws/group/${groupId}?token=${encodeURIComponent(authToken)}`;

            let candidate: WebSocket;
            try {
                candidate = new WebSocket(url);
                socket = candidate;
            } catch {
                startPolling();
                scheduleReconnect();
                return;
            }

            clearReadyTimer();
            readyTimer = setTimeout(() => {
                if (disposed || socket !== candidate || connected) return;
                // A TCP/WebSocket open without the application-level `connected` event is not
                // ready. Keep data moving over REST and force a fresh socket attempt.
                startPolling();
                try {
                    candidate.close();
                } catch {
                    if (socket === candidate) socket = null;
                }
                scheduleReconnect();
            }, WS_READY_TIMEOUT_MS);

            candidate.onopen = () => {
                // The server's `connected` event is the readiness boundary. Keep polling until it
                // arrives and its cursor catch-up completes.
            };

            candidate.onmessage = (event) => {
                if (disposed || socket !== candidate) return;
                let payload: GroupSocketEvent;
                try {
                    payload = JSON.parse(event.data);
                } catch {
                    return;
                }
                if (payload.type === 'connected') {
                    connected = true;
                    clearReadyTimer();
                    void promoteIfSynchronized();
                    return;
                }
                if (payload.type !== 'message.created' || !payload.message || !payload.session_id) {
                    return;
                }
                onGroupActivityRef.current?.();
                const scope = `${groupId}:${payload.session_id}`;
                advanceCursor(
                    wsObservedCursorByScopeRef.current,
                    scope,
                    payload.message.cursor,
                );
                if (payload.session_id === sessionIdRef.current) {
                    onMessagesRef.current(payload.session_id, [payload.message]);
                    void catchUpThroughObserved(payload.session_id).then((synchronized) => {
                        if (!disposed && !synchronized) startPolling();
                    });
                }
            };

            candidate.onclose = (event) => {
                if (disposed) return;
                clearReadyTimer();
                if (socket !== candidate) return;
                socket = null;
                connected = false;

                if (NO_RETRY_CLOSE_CODES.has(event.code)) {
                    stopPolling();
                    setStatus('offline');
                    return;
                }

                // REST keeps the page current while the socket retries in the background.
                startPolling();
                scheduleReconnect();
            };

            candidate.onerror = () => {
                // Browsers follow WebSocket errors with `close`; that path owns fallback/retry.
            };
        }

        connect();

        return () => {
            disposed = true;
            if (ensurePollingRef.current === startPolling) ensurePollingRef.current = null;
            if (retryTimer) clearTimeout(retryTimer);
            clearReadyTimer();
            stopPolling();
            if (socket) {
                socket.onclose = null;
                socket.close();
            }
        };
    }, [enabled, groupId, token, catchUpThroughObserved]);

    // Switching sessions means the cursor we track changed — catch that session up immediately.
    useEffect(() => {
        if (!enabled || !groupId || !sessionId) return;
        const targetSession = sessionId;
        void catchUpThroughObserved(targetSession).then((synchronized) => {
            if (!synchronized && sessionIdRef.current === targetSession) {
                // Keep the existing group socket alive. REST polling retries the failed session
                // catch-up and will promote the transport back to live once it succeeds.
                ensurePollingRef.current?.();
            }
        });
    }, [enabled, groupId, sessionId, catchUpThroughObserved]);

    return { status };
}
