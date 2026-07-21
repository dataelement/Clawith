/** Group chat types — mirror of backend/app/api/groups.py schemas. */

export interface Group {
    id: string;
    tenant_id: string;
    name: string;
    description: string | null;
    created_by_participant_id: string;
    created_at: string;
    updated_at: string;
}

export type ParticipantType = 'user' | 'agent';
export type GroupRole = 'manager' | 'member';

export interface GroupMember {
    id: string;
    participant_id: string;
    participant_type: ParticipantType;
    participant_ref_id: string;
    display_name: string;
    avatar_url: string | null;
    role: GroupRole;
    role_description: string | null;
    title: string | null;
    joined_at: string;
}

export interface GroupMemberCandidate {
    participant_id: string;
    participant_type: ParticipantType;
    participant_ref_id: string;
    display_name: string;
    avatar_url: string | null;
    role_description: string | null;
    title: string | null;
}

export interface GroupSession {
    id: string;
    group_id: string;
    title: string;
    is_primary: boolean;
    unread_count: number;
    created_by_participant_id: string | null;
    created_at: string;
    updated_at: string;
    last_message_at: string | null;
}

export interface GroupMention {
    participant_id: string;
    participant_type?: ParticipantType;
    display_name?: string;
}

export interface GroupMessage {
    id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    participant_id: string | null;
    sender_name: string | null;
    mentions: GroupMention[];
    created_at: string;
    /** Message Position `<created_at ISO>|<id>` — the shared (created_at, id) ordering contract. */
    cursor: string;
}

/** `none` = no agent mentioned, `single` = one agent, `planning` = multi-agent task planning. */
export type DispatchKind = 'none' | 'single' | 'planning';

export interface GroupMessageIntake {
    message: GroupMessage;
    dispatch_kind: DispatchKind;
    run_ids: string[];
    created: boolean;
    error_code: string | null;
}

export interface GroupRunState {
    run_id: string;
    status: string;
    can_cancel: boolean;
    agent_id: string | null;
    system_role: string | null;
}

export interface GroupTextFile {
    path: string;
    content: string;
    exists: boolean;
    version_token: string | null;
    modified_at: string | null;
    revision_id: string | null;
}

export interface GroupWorkspaceEntry {
    path: string;
    name: string;
    is_dir: boolean;
    size: number;
    modified_at: string;
    version_token: string | null;
}

export interface GroupSessionSummary {
    version: number;
    summary: string;
    requirements: unknown[];
    decisions: unknown[];
    open_items: unknown[];
    evidence_refs: unknown[];
    workspace_refs: unknown[];
    covered_through_message_id: string | null;
}
