/**
 * Group chat transport. The page never learns how messages arrive.
 *
 * Target design: REST for sending and history, WebSocket for push, cursor backfill on reconnect,
 * polling only as a stopgap. Two pieces are not on the backend yet (see
 * docs/group-chat/frontend-realtime-contract.md):
 *
 *   - `WS /ws/group/{group_id}` does not exist. The socket fails to open, and after a few attempts
 *     this hook settles into polling for the rest of the mount.
 *   - `GET .../messages` has no `after` cursor, so backfill pages *backward* from the newest message
 *     until it reaches the last cursor we hold. Flip USE_AFTER_CURSOR once the backend ships it.
 *
 * Both fallbacks are contained here. When the backend lands, no page code changes.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { groupApi } from '../services/groupApi';
import type { GroupMessage } from '../types/group';

/** Flip to true once `GET .../messages?after=<cursor>` exists. */
const USE_AFTER_CURSOR = false;

const POLL_INTERVAL_MS = 4000;
const BACKFILL_PAGE_SIZE = 50;
/** Bounds the backward walk when we have no forward pager. 10 × 50 = 500 messages of catch-up. */
const MAX_BACKFILL_PAGES = 10;
/** Consecutive socket failures before we stop trying and just poll. */
const WS_FAILURE_THRESHOLD = 3;
const WS_RETRY_BASE_MS = 2000;

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
 * Every message newer than `cursor`, ascending. With no cursor, the newest page.
 *
 * Without a forward pager we walk backward from the head and keep what is newer than the cursor.
 * We can stop as soon as a page contains anything at or older than the cursor — that page closed
 * the gap. A page that is entirely newer means the gap is wider than one page, so keep walking.
 */
export async function fetchMessagesSince(
    groupId: string,
    sessionId: string,
    cursor?: string,
): Promise<GroupMessage[]> {
    if (!cursor) {
        return groupApi.messages(groupId, sessionId, { limit: BACKFILL_PAGE_SIZE });
    }

    if (USE_AFTER_CURSOR) {
        const collected: GroupMessage[] = [];
        let after = cursor;
        for (let page = 0; page < MAX_BACKFILL_PAGES; page += 1) {
            const batch = await groupApi.messages(groupId, sessionId, {
                limit: BACKFILL_PAGE_SIZE,
                after,
            });
            collected.push(...batch);
            if (batch.length < BACKFILL_PAGE_SIZE) break;
            after = batch[batch.length - 1].cursor;
        }
        return collected;
    }

    const collected: GroupMessage[] = [];
    let before: string | undefined;
    for (let page = 0; page < MAX_BACKFILL_PAGES; page += 1) {
        const batch = await groupApi.messages(groupId, sessionId, {
            limit: BACKFILL_PAGE_SIZE,
            before,
        });
        if (batch.length === 0) break;

        // Pages arrive newest-first, each ascending internally, so older pages prepend.
        const newer = batch.filter((message) => compareCursor(message.cursor, cursor) > 0);
        collected.unshift(...newer);

        // This page reached back to the cursor, so nothing newer is left behind it.
        if (newer.length < batch.length) break;
        // A short page is the start of the session — there is nothing older to walk to.
        if (batch.length < BACKFILL_PAGE_SIZE) break;
        before = batch[0].cursor;
    }
    return collected;
}

interface UseGroupRealtimeOptions {
    groupId?: string;
    sessionId?: string;
    /** Newest cursor the page already holds. Read lazily so new messages don't re-subscribe. */
    getLastCursor: () => string | undefined;
    /** Ascending, may contain messages the page already has — dedupe by id on the receiving end. */
    onMessages: (sessionId: string, messages: GroupMessage[]) => void;
    /** Something happened somewhere in the group — refresh session list unread badges. */
    onGroupActivity?: () => void;
    enabled?: boolean;
}

export function useGroupRealtime({
    groupId,
    sessionId,
    getLastCursor,
    onMessages,
    onGroupActivity,
    enabled = true,
}: UseGroupRealtimeOptions): { status: RealtimeStatus } {
    const [status, setStatus] = useState<RealtimeStatus>('connecting');

    // Callbacks live in refs so a re-render never tears down the socket.
    const getLastCursorRef = useRef(getLastCursor);
    const onMessagesRef = useRef(onMessages);
    const onGroupActivityRef = useRef(onGroupActivity);
    getLastCursorRef.current = getLastCursor;
    onMessagesRef.current = onMessages;
    onGroupActivityRef.current = onGroupActivity;

    const sessionIdRef = useRef(sessionId);
    sessionIdRef.current = sessionId;

    const wsFailuresRef = useRef(0);
    const inFlightRef = useRef(false);

    const catchUp = useCallback(async () => {
        const activeSession = sessionIdRef.current;
        if (!groupId || !activeSession || inFlightRef.current) return;
        inFlightRef.current = true;
        try {
            const fresh = await fetchMessagesSince(groupId, activeSession, getLastCursorRef.current());
            if (fresh.length > 0 && sessionIdRef.current === activeSession) {
                onMessagesRef.current(activeSession, fresh);
                onGroupActivityRef.current?.();
            }
        } catch {
            // A failed catch-up is not fatal: the next tick or reconnect tries again.
        } finally {
            inFlightRef.current = false;
        }
    }, [groupId]);

    useEffect(() => {
        if (!enabled || !groupId) return;

        let disposed = false;
        let socket: WebSocket | null = null;
        let retryTimer: ReturnType<typeof setTimeout> | null = null;
        let pollTimer: ReturnType<typeof setInterval> | null = null;

        const startPolling = () => {
            if (disposed || pollTimer) return;
            setStatus('polling');
            void catchUp();
            pollTimer = setInterval(() => void catchUp(), POLL_INTERVAL_MS);
        };

        const connect = () => {
            if (disposed) return;
            const token = localStorage.getItem('token');
            if (!token) {
                setStatus('offline');
                return;
            }

            setStatus((current) => (current === 'polling' ? current : 'connecting'));
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const url = `${protocol}//${window.location.host}/ws/group/${groupId}?token=${token}`;

            try {
                socket = new WebSocket(url);
            } catch {
                startPolling();
                return;
            }

            socket.onopen = () => {
                if (disposed) return;
                wsFailuresRef.current = 0;
                if (pollTimer) {
                    clearInterval(pollTimer);
                    pollTimer = null;
                }
                setStatus('live');
                // Close whatever gap opened while the socket was down.
                void catchUp();
            };

            socket.onmessage = (event) => {
                if (disposed) return;
                let payload: GroupSocketEvent;
                try {
                    payload = JSON.parse(event.data);
                } catch {
                    return;
                }
                if (payload.type !== 'message.created' || !payload.message || !payload.session_id) {
                    return;
                }
                onGroupActivityRef.current?.();
                if (payload.session_id === sessionIdRef.current) {
                    onMessagesRef.current(payload.session_id, [payload.message]);
                }
            };

            socket.onclose = (event) => {
                if (disposed) return;
                socket = null;

                if (NO_RETRY_CLOSE_CODES.has(event.code)) {
                    setStatus('offline');
                    return;
                }

                wsFailuresRef.current += 1;
                if (wsFailuresRef.current >= WS_FAILURE_THRESHOLD) {
                    // The endpoint is very likely not there. Stop knocking; poll instead.
                    startPolling();
                    return;
                }

                startPolling(); // Keep messages flowing while we retry the socket.
                retryTimer = setTimeout(connect, WS_RETRY_BASE_MS * wsFailuresRef.current);
            };
        };

        connect();

        return () => {
            disposed = true;
            if (retryTimer) clearTimeout(retryTimer);
            if (pollTimer) clearInterval(pollTimer);
            if (socket) {
                socket.onclose = null;
                socket.close();
            }
        };
    }, [enabled, groupId, catchUp]);

    // Switching sessions means the cursor we track changed — catch that session up immediately.
    useEffect(() => {
        if (enabled && groupId && sessionId) void catchUp();
    }, [enabled, groupId, sessionId, catchUp]);

    return { status };
}
