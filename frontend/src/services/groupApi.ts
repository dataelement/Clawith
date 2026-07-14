/** Group chat API client — /api/groups. */

import { fetchJson, uploadFileWithProgress } from './api';
import type {
    Group,
    GroupMember,
    GroupMessage,
    GroupMessageIntake,
    GroupSession,
    GroupSessionSummary,
    GroupTextFile,
    GroupWorkspaceEntry,
    ParticipantType,
} from '../types/group';

/** Business identity; the backend resolves or lazily creates the Participant row. */
export interface InviteMemberPayload {
    participant_type: ParticipantType;
    ref_id: string;
}

export interface SendMessagePayload {
    content: string;
    mentions: { participant_id: string }[];
    /** Client-generated so a retried send is deduplicated server-side rather than duplicated. */
    message_id: string;
}

export type GroupMessagePageOptions =
    | {
        limit?: number;
        before?: string;
        after?: never;
        signal?: AbortSignal;
    }
    | {
        limit?: number;
        after?: string;
        before?: never;
        signal?: AbortSignal;
    };

const qs = (params: Record<string, string | number | undefined>) => {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== '') search.set(key, String(value));
    }
    const text = search.toString();
    return text ? `?${text}` : '';
};

export const groupApi = {
    list: () => fetchJson<Group[]>('/groups'),

    get: (groupId: string) => fetchJson<Group>(`/groups/${groupId}`),

    create: (data: { name: string; description?: string }) =>
        fetchJson<Group>('/groups', { method: 'POST', body: JSON.stringify(data) }),

    update: (groupId: string, data: { name?: string; description?: string }) =>
        fetchJson<Group>(`/groups/${groupId}`, { method: 'PATCH', body: JSON.stringify(data) }),

    remove: (groupId: string) => fetchJson<void>(`/groups/${groupId}`, { method: 'DELETE' }),

    members: (groupId: string) => fetchJson<GroupMember[]>(`/groups/${groupId}/members`),

    inviteMember: (groupId: string, data: InviteMemberPayload) =>
        fetchJson<GroupMember>(`/groups/${groupId}/members`, {
            method: 'POST',
            body: JSON.stringify(data),
        }),

    removeMember: (groupId: string, memberId: string) =>
        fetchJson<void>(`/groups/${groupId}/members/${memberId}`, { method: 'DELETE' }),

    sessions: (groupId: string) => fetchJson<GroupSession[]>(`/groups/${groupId}/sessions`),

    createSession: (groupId: string, data: { title?: string } = {}) =>
        fetchJson<GroupSession>(`/groups/${groupId}/sessions`, {
            method: 'POST',
            body: JSON.stringify(data),
        }),

    renameSession: (groupId: string, sessionId: string, title: string) =>
        fetchJson<GroupSession>(`/groups/${groupId}/sessions/${sessionId}`, {
            method: 'PATCH',
            body: JSON.stringify({ title }),
        }),

    deleteSession: (groupId: string, sessionId: string) =>
        fetchJson<void>(`/groups/${groupId}/sessions/${sessionId}`, { method: 'DELETE' }),

    markSessionRead: (groupId: string, sessionId: string, messageId: string) =>
        fetchJson<{ session_id: string; last_read_message_id: string; advanced: boolean }>(
            `/groups/${groupId}/sessions/${sessionId}/read`,
            { method: 'POST', body: JSON.stringify({ message_id: messageId }) },
        ),

    /**
     * Backward pager: returns the `limit` messages immediately older than `before`, ascending.
     * Omit `before` for the newest page.
     *
     * `after` is the forward pager used for reconnect and polling catch-up. It is mutually
     * exclusive with `before` so a request has one unambiguous direction.
     */
    messages: (
        groupId: string,
        sessionId: string,
        opts: GroupMessagePageOptions = {},
    ) => {
        if (opts.before && opts.after) {
            throw new Error('Group message pagination cannot use both `before` and `after`.');
        }
        return fetchJson<GroupMessage[]>(
            `/groups/${groupId}/sessions/${sessionId}/messages${qs({
                limit: opts.limit ?? 30,
                before: opts.before,
                after: opts.after,
            })}`,
            { signal: opts.signal },
        );
    },

    sendMessage: (groupId: string, sessionId: string, data: SendMessagePayload) =>
        fetchJson<GroupMessageIntake>(`/groups/${groupId}/sessions/${sessionId}/messages`, {
            method: 'POST',
            body: JSON.stringify(data),
        }),

    sessionSummary: (groupId: string, sessionId: string) =>
        fetchJson<GroupSessionSummary>(`/groups/${groupId}/sessions/${sessionId}/summary`),

    announcement: (groupId: string) =>
        fetchJson<GroupTextFile>(`/groups/${groupId}/announcement`),

    saveAnnouncement: (groupId: string, content: string, expectedVersionToken?: string | null) =>
        fetchJson<GroupTextFile>(`/groups/${groupId}/announcement`, {
            method: 'PUT',
            body: JSON.stringify({
                content,
                expected_version_token: expectedVersionToken ?? null,
            }),
        }),

    agentMemory: (groupId: string, agentId: string) =>
        fetchJson<GroupTextFile>(`/groups/${groupId}/agents/${agentId}/memory`),

    saveAgentMemory: (
        groupId: string,
        agentId: string,
        content: string,
        expectedVersionToken?: string | null,
    ) =>
        fetchJson<GroupTextFile>(`/groups/${groupId}/agents/${agentId}/memory`, {
            method: 'PUT',
            body: JSON.stringify({
                content,
                expected_version_token: expectedVersionToken ?? null,
            }),
        }),

    deleteAgentMemory: (groupId: string, agentId: string) =>
        fetchJson<void>(`/groups/${groupId}/agents/${agentId}/memory`, { method: 'DELETE' }),

    workspace: (groupId: string, path = '') =>
        fetchJson<GroupWorkspaceEntry[]>(`/groups/${groupId}/workspace${qs({ path })}`),

    workspaceFile: (groupId: string, path: string) =>
        fetchJson<GroupTextFile>(`/groups/${groupId}/workspace/file${qs({ path })}`),

    saveWorkspaceFile: (
        groupId: string,
        path: string,
        content: string,
        expectedVersionToken?: string | null,
    ) =>
        fetchJson<GroupTextFile>(`/groups/${groupId}/workspace/file${qs({ path })}`, {
            method: 'PUT',
            body: JSON.stringify({
                content,
                expected_version_token: expectedVersionToken ?? null,
            }),
        }),

    deleteWorkspaceFile: (groupId: string, path: string) =>
        fetchJson<void>(`/groups/${groupId}/workspace/file${qs({ path })}`, { method: 'DELETE' }),

    uploadWorkspaceFile: (
        groupId: string,
        file: File,
        path = '',
        onProgress?: (percent: number) => void,
    ) =>
        uploadFileWithProgress(
            `/groups/${groupId}/workspace/upload${qs({ path })}`,
            file,
            onProgress,
        ).promise,

    workspaceDownloadUrl: (
        groupId: string,
        path: string,
        options?: { inline?: boolean },
    ) => {
        const token = localStorage.getItem('token') || '';
        const params = new URLSearchParams({ path, token });
        if (options?.inline) params.set('inline', '1');
        return `/api/groups/${groupId}/workspace/download?${params.toString()}`;
    },
};
