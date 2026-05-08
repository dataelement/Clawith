/** Shared TypeScript types */

export interface User {
    id: string;
    username: string;
    email: string;
    display_name: string;
    avatar_url?: string;
    role: 'platform_admin' | 'org_admin' | 'agent_admin' | 'member';
    is_platform_admin?: boolean;
    tenant_id?: string;
    title?: string;
    feishu_open_id?: string;
    is_active: boolean;
    email_verified?: boolean;
    created_at: string;
}

export interface Agent {
    id: string;
    name: string;
    avatar_url?: string;
    role_description: string;
    bio?: string;
    status: 'creating' | 'running' | 'idle' | 'stopped' | 'error';
    creator_id: string;
    primary_model_id?: string;
    fallback_model_id?: string;
    autonomy_policy: Record<string, string>;
    tokens_used_today: number;
    tokens_used_month: number;
    tokens_used_total?: number;
    cache_read_tokens_today?: number;
    cache_read_tokens_month?: number;
    cache_read_tokens_total?: number;
    cache_creation_tokens_today?: number;
    cache_creation_tokens_month?: number;
    cache_creation_tokens_total?: number;
    max_tokens_per_day?: number;
    max_tokens_per_month?: number;
    heartbeat_enabled: boolean;
    heartbeat_interval_minutes: number;
    heartbeat_active_hours: string;
    last_heartbeat_at?: string;
    timezone?: string;
    context_window_size?: number;
    agent_type?: 'native' | 'openclaw';
    openclaw_last_seen?: string;
    unread_count?: number;
    // True when the viewing user has already been onboarded to this agent.
    // Defaults to true on list endpoints that don't compute per-viewer state.
    onboarded_for_me?: boolean;
    created_at: string;
    last_active_at?: string;
}

export interface Task {
    id: string;
    agent_id: string;
    title: string;
    description?: string;
    type: 'todo' | 'supervision';
    status: 'pending' | 'doing' | 'done' | 'paused';
    priority: 'low' | 'medium' | 'high' | 'urgent';
    assignee: string;
    created_by: string;
    creator_username?: string;
    due_date?: string;
    supervision_target_name?: string;
    supervision_channel?: string;
    remind_schedule?: string;
    created_at: string;
    updated_at: string;
    completed_at?: string;
}

export interface ChatMessage {
    id: string;
    agent_id: string;
    user_id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    created_at: string;
}

export interface TokenResponse {
    access_token: string;
    token_type: string;
    user: User;
    needs_company_setup?: boolean;
}

// ─── Projects ──────────────────────────────────────────

export type ProjectScopeType = 'tenant' | 'department' | 'user';
export type ProjectChatVisibility = 'shared' | 'private';
export type ProjectFileCreatorType = 'user' | 'agent';

export interface ProjectAgentSummary {
    agent_id: string;
    name: string;
    avatar_url?: string | null;
}

export interface Project {
    id: string;
    name: string;
    description: string;
    scope_type: ProjectScopeType;
    scope_id: string;
    chat_visibility: ProjectChatVisibility;
    archived_at?: string | null;
    created_by: string;
    created_at: string;
    updated_at: string;
    agent_count: number;
    file_count: number;
    session_count: number;
    last_message_at?: string | null;
    agents: ProjectAgentSummary[];
}

export interface ProjectAgentMember {
    project_id: string;
    agent_id: string;
    agent_name: string;
    avatar_url?: string | null;
    added_by: string;
    added_at: string;
}

export interface ProjectFile {
    id: string;
    project_id: string;
    filename: string;
    /** Path relative to the project workspace root, "/"-separated.
     *  Equals filename for files at root; "posts/draft.md" for nested ones. */
    path: string;
    is_dir: boolean;
    size_bytes: number;
    mime_type: string;
    created_by_type: ProjectFileCreatorType;
    created_by: string;
    created_at: string;
    updated_at: string;
    /** Phase 4 polish: number of project tasks linking to this file. */
    linked_task_count?: number;
    /** Up to 3 task titles, for tooltip display. */
    linked_task_titles?: string[];
}

export interface ProjectFileConflict {
    detail: 'filename_conflict';
    existing: ProjectFile;
    suggested_alt_name: string;
}

export type ProjectScheduledTaskFrequency = 'hourly' | 'daily' | 'weekdays' | 'weekly';

export interface ProjectScheduledTask {
    id: string;
    project_id: string;
    agent_id: string;
    agent_name: string;
    agent_avatar_url?: string | null;
    name: string;
    prompt: string;
    frequency: ProjectScheduledTaskFrequency;
    hour: number;
    is_enabled: boolean;
    last_fired_at?: string | null;
    next_fire_at?: string | null;
    fire_count: number;
    cron_expr: string;
    created_at: string;
}

export interface ProjectChatSession {
    id: string;
    agent_id: string;
    agent_name: string;
    user_id: string;
    user_display_name?: string | null;
    title: string;
    created_at: string;
    last_message_at?: string | null;
    message_count: number;
    owned_by_me: boolean;
}

export type ProjectTaskStatus = 'todo' | 'doing' | 'done' | 'blocked';
export type ProjectTaskCreatedByType = 'user' | 'agent';

export interface ProjectTask {
    id: string;
    project_id: string;
    title: string;
    description: string;
    status: ProjectTaskStatus;
    assigned_agent_id?: string | null;
    assigned_agent_name?: string | null;
    assigned_agent_avatar_url?: string | null;
    assigned_user_id?: string | null;
    assigned_user_display_name?: string | null;
    due_date?: string | null;
    created_by: string;
    created_by_type: ProjectTaskCreatedByType;
    created_at: string;
    updated_at: string;
    completed_at?: string | null;
    linked_file_count: number;
}

export interface ProjectTaskFileLink {
    file_id: string;
    filename: string;
    mime_type: string;
    size_bytes: number;
    linked_at: string;
    linked_by_type: ProjectTaskCreatedByType;
}

export interface ProjectTaskDetail extends ProjectTask {
    linked_files: ProjectTaskFileLink[];
}
