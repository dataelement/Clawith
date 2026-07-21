/** Group chat API client — /api/groups. */

import { fetchJson } from './api';
import type {
    Group,
    GroupMember,
    GroupMemberCandidate,
    GroupMessage,
    GroupMessageIntake,
    GroupRunState,
    GroupSession,
    GroupSessionSummary,
    GroupTextFile,
    GroupWorkspaceEntry,
    ParticipantType,
} from '../types/group';

export interface InviteMemberPayload {
    participant_id: string;
}

export interface SendMessagePayload {
    content: string;
    mentions: { participant_id: string }[];
    /** Client-generated so a retried send is deduplicated server-side rather than duplicated. */
    message_id: string;
}

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

    memberCandidates: (groupId: string, participantType: ParticipantType) =>
        fetchJson<GroupMemberCandidate[]>(
            `/groups/${groupId}/member-candidates${qs({ participant_type: participantType })}`,
        ),

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
     * Forward pager: `after` returns the next `limit` messages in ascending position order.
     * The two cursors are mutually exclusive.
     */
    messages: (
        groupId: string,
        sessionId: string,
        opts: { limit?: number; before?: string; after?: string } = {},
    ) =>
        fetchJson<GroupMessage[]>(
            `/groups/${groupId}/sessions/${sessionId}/messages${qs({
                limit: opts.limit ?? 30,
                before: opts.before,
                after: opts.after,
            })}`,
        ),

    sendMessage: (groupId: string, sessionId: string, data: SendMessagePayload) =>
        fetchJson<GroupMessageIntake>(`/groups/${groupId}/sessions/${sessionId}/messages`, {
            method: 'POST',
            body: JSON.stringify(data),
        }),

    runState: (groupId: string, sessionId: string, runId: string) =>
        fetchJson<GroupRunState>(`/groups/${groupId}/sessions/${sessionId}/runs/${runId}`),

    activeRuns: (groupId: string, sessionId: string) =>
        fetchJson<GroupRunState[]>(`/groups/${groupId}/sessions/${sessionId}/runs`),

    cancelRun: (groupId: string, sessionId: string, runId: string) =>
        fetchJson<GroupRunState>(
            `/groups/${groupId}/sessions/${sessionId}/runs/${runId}/cancel`,
            { method: 'POST' },
        ),

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

    deleteAgentMemory: (
        groupId: string,
        agentId: string,
        expectedVersionToken?: string | null,
    ) =>
        fetchJson<void>(
            `/groups/${groupId}/agents/${agentId}/memory${qs({
                expected_version_token: expectedVersionToken ?? undefined,
            })}`,
            { method: 'DELETE' },
        ),

    workspace: (groupId: string, path = '') =>
        fetchJson<GroupWorkspaceEntry[]>(`/groups/${groupId}/workspace${qs({ path })}`),

    workspaceFile: (groupId: string, path: string) =>
        fetchJson<GroupTextFile>(`/groups/${groupId}/workspace/file${qs({ path })}`),

    saveWorkspaceFile: (
        groupId: string,
        path: string,
        content: string,
        expectedVersionToken?: string | null,
        requireAbsent = false,
    ) =>
        fetchJson<GroupTextFile>(`/groups/${groupId}/workspace/file${qs({ path })}`, {
            method: 'PUT',
            body: JSON.stringify({
                content,
                expected_version_token: expectedVersionToken ?? null,
                require_absent: requireAbsent,
            }),
        }),

    deleteWorkspaceFile: (
        groupId: string,
        path: string,
        expectedVersionToken?: string | null,
    ) =>
        fetchJson<void>(
            `/groups/${groupId}/workspace/file${qs({
                path,
                expected_version_token: expectedVersionToken ?? undefined,
            })}`,
            { method: 'DELETE' },
        ),
};
