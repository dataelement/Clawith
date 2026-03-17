/** Shared TypeScript types */

export interface User {
    id: string;
    username: string;
    email: string;
    display_name: string;
    avatar_url?: string;
    role: 'platform_admin' | 'org_admin' | 'agent_admin' | 'member';
    tenant_id?: string;
    department_id?: string;
    title?: string;
    feishu_open_id?: string;
    is_active: boolean;
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
