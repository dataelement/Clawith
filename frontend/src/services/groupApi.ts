/** Group chat API client — /api/groups. */

import { fetchJson, fetchVoid, uploadFileWithProgress } from "./api";
import { parseGroupWorkspaceUploadResponse } from "./apiResponseParsers";
import type { GroupWorkspaceUploadResponse } from "./apiContracts";
import {
  parseGroupCandidatesResponse,
  parseGroupMemberResponse,
  parseGroupMembersResponse,
  parseGroupMessageIntakeResponse,
  parseGroupMessagesResponse,
  parseGroupReadReceiptResponse,
  parseGroupResponse,
  parseGroupRunStateResponse,
  parseGroupRunStatesResponse,
  parseGroupsResponse,
  parseGroupSessionResponse,
  parseGroupSessionsResponse,
  parseGroupSessionSummaryResponse,
  parseGroupTextFileResponse,
  parseGroupWorkspaceResponse,
} from "./groupApiResponseParsers";
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
} from "../types/group";

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
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
};

export const groupApi = {
  list: () => fetchJson<Group[]>("/groups", {}, parseGroupsResponse),

  get: (groupId: string) =>
    fetchJson<Group>(`/groups/${groupId}`, {}, parseGroupResponse),

  create: (data: {
    name: string;
    description?: string;
    member_participant_ids?: string[];
  }) =>
    fetchJson<Group>(
      "/groups",
      { method: "POST", body: JSON.stringify(data) },
      parseGroupResponse,
    ),

  update: (groupId: string, data: { name?: string; description?: string }) =>
    fetchJson<Group>(
      `/groups/${groupId}`,
      { method: "PATCH", body: JSON.stringify(data) },
      parseGroupResponse,
    ),

  remove: (groupId: string) =>
    fetchVoid(`/groups/${groupId}`, { method: "DELETE" }),

  members: (groupId: string) =>
    fetchJson<GroupMember[]>(
      `/groups/${groupId}/members`,
      {},
      parseGroupMembersResponse,
    ),

  tenantMemberCandidates: (participantType: ParticipantType) =>
    fetchJson<GroupMemberCandidate[]>(
      `/groups/member-candidates${qs({ participant_type: participantType })}`,
      {},
      parseGroupCandidatesResponse,
    ),

  memberCandidates: (groupId: string, participantType: ParticipantType) =>
    fetchJson<GroupMemberCandidate[]>(
      `/groups/${groupId}/member-candidates${qs({ participant_type: participantType })}`,
      {},
      parseGroupCandidatesResponse,
    ),

  inviteMember: (groupId: string, data: InviteMemberPayload) =>
    fetchJson<GroupMember>(
      `/groups/${groupId}/members`,
      { method: "POST", body: JSON.stringify(data) },
      parseGroupMemberResponse,
    ),

  removeMember: (groupId: string, memberId: string) =>
    fetchVoid(`/groups/${groupId}/members/${memberId}`, {
      method: "DELETE",
    }),

  sessions: (groupId: string) =>
    fetchJson<GroupSession[]>(
      `/groups/${groupId}/sessions`,
      {},
      parseGroupSessionsResponse,
    ),

  createSession: (groupId: string, data: { title?: string } = {}) =>
    fetchJson<GroupSession>(
      `/groups/${groupId}/sessions`,
      { method: "POST", body: JSON.stringify(data) },
      parseGroupSessionResponse,
    ),

  renameSession: (groupId: string, sessionId: string, title: string) =>
    fetchJson<GroupSession>(
      `/groups/${groupId}/sessions/${sessionId}`,
      { method: "PATCH", body: JSON.stringify({ title }) },
      parseGroupSessionResponse,
    ),

  deleteSession: (groupId: string, sessionId: string) =>
    fetchVoid(`/groups/${groupId}/sessions/${sessionId}`, {
      method: "DELETE",
    }),

  markSessionRead: (groupId: string, sessionId: string, messageId: string) =>
    fetchJson<{
      session_id: string;
      last_read_message_id: string;
      advanced: boolean;
    }>(
      `/groups/${groupId}/sessions/${sessionId}/read`,
      {
        method: "POST",
        body: JSON.stringify({ message_id: messageId }),
      },
      parseGroupReadReceiptResponse,
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
      {},
      parseGroupMessagesResponse,
    ),

  sendMessage: (groupId: string, sessionId: string, data: SendMessagePayload) =>
    fetchJson<GroupMessageIntake>(
      `/groups/${groupId}/sessions/${sessionId}/messages`,
      {
        method: "POST",
        body: JSON.stringify(data),
      },
      parseGroupMessageIntakeResponse,
    ),

  runState: (groupId: string, sessionId: string, runId: string) =>
    fetchJson<GroupRunState>(
      `/groups/${groupId}/sessions/${sessionId}/runs/${runId}`,
      {},
      parseGroupRunStateResponse,
    ),

  activeRuns: (groupId: string, sessionId: string) =>
    fetchJson<GroupRunState[]>(
      `/groups/${groupId}/sessions/${sessionId}/runs`,
      {},
      parseGroupRunStatesResponse,
    ),

  cancelRun: (groupId: string, sessionId: string, runId: string) =>
    fetchJson<GroupRunState>(
      `/groups/${groupId}/sessions/${sessionId}/runs/${runId}/cancel`,
      { method: "POST" },
      parseGroupRunStateResponse,
    ),

  sessionSummary: (groupId: string, sessionId: string) =>
    fetchJson<GroupSessionSummary>(
      `/groups/${groupId}/sessions/${sessionId}/summary`,
      {},
      parseGroupSessionSummaryResponse,
    ),

  announcement: (groupId: string) =>
    fetchJson<GroupTextFile>(
      `/groups/${groupId}/announcement`,
      {},
      parseGroupTextFileResponse,
    ),

  saveAnnouncement: (
    groupId: string,
    content: string,
    expectedVersionToken?: string | null,
  ) =>
    fetchJson<GroupTextFile>(
      `/groups/${groupId}/announcement`,
      {
        method: "PUT",
        body: JSON.stringify({
          content,
          expected_version_token: expectedVersionToken ?? null,
        }),
      },
      parseGroupTextFileResponse,
    ),

  agentMemory: (groupId: string, agentId: string) =>
    fetchJson<GroupTextFile>(
      `/groups/${groupId}/agents/${agentId}/memory`,
      {},
      parseGroupTextFileResponse,
    ),

  saveAgentMemory: (
    groupId: string,
    agentId: string,
    content: string,
    expectedVersionToken?: string | null,
  ) =>
    fetchJson<GroupTextFile>(
      `/groups/${groupId}/agents/${agentId}/memory`,
      {
        method: "PUT",
        body: JSON.stringify({
          content,
          expected_version_token: expectedVersionToken ?? null,
        }),
      },
      parseGroupTextFileResponse,
    ),

  deleteAgentMemory: (
    groupId: string,
    agentId: string,
    expectedVersionToken?: string | null,
  ) =>
    fetchVoid(
      `/groups/${groupId}/agents/${agentId}/memory${qs({
        expected_version_token: expectedVersionToken ?? undefined,
      })}`,
      { method: "DELETE" },
    ),

  workspace: (groupId: string, path = "") =>
    fetchJson<GroupWorkspaceEntry[]>(
      `/groups/${groupId}/workspace${qs({ path })}`,
      {},
      parseGroupWorkspaceResponse,
    ),

  workspaceFile: (groupId: string, path: string) =>
    fetchJson<GroupTextFile>(
      `/groups/${groupId}/workspace/file${qs({ path })}`,
      {},
      parseGroupTextFileResponse,
    ),

  saveWorkspaceFile: (
    groupId: string,
    path: string,
    content: string,
    expectedVersionToken?: string | null,
    requireAbsent = false,
  ) =>
    fetchJson<GroupTextFile>(
      `/groups/${groupId}/workspace/file${qs({ path })}`,
      {
        method: "PUT",
        body: JSON.stringify({
          content,
          expected_version_token: expectedVersionToken ?? null,
          require_absent: requireAbsent,
        }),
      },
      parseGroupTextFileResponse,
    ),

  uploadWorkspaceFile: (
    groupId: string,
    path: string,
    file: File,
    expectedVersionToken?: string | null,
    requireAbsent = false,
    onProgress?: (percent: number) => void,
  ) =>
    uploadFileWithProgress<GroupWorkspaceUploadResponse>(
      `/groups/${groupId}/workspace/upload${qs({
        path,
        expected_version_token: expectedVersionToken ?? undefined,
        require_absent: requireAbsent ? "true" : undefined,
      })}`,
      file,
      onProgress,
      undefined,
      undefined,
      parseGroupWorkspaceUploadResponse,
    ).promise,

  downloadWorkspaceUrl: (
    groupId: string,
    path: string,
    options?: { inline?: boolean },
  ) => {
    const token = localStorage.getItem("token");
    const params = new URLSearchParams({ path, token: token || "" });
    if (options?.inline) params.set("inline", "true");
    return `/api/groups/${groupId}/workspace/download?${params.toString()}`;
  },

  deleteWorkspaceFile: (
    groupId: string,
    path: string,
    expectedVersionToken?: string | null,
  ) =>
    fetchVoid(
      `/groups/${groupId}/workspace/file${qs({
        path,
        expected_version_token: expectedVersionToken ?? undefined,
      })}`,
      { method: "DELETE" },
    ),
};
