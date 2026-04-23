import { useCallback, useEffect, useMemo, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
    IconArrowLeft,
    IconCalendar,
    IconCheck,
    IconChevronDown,
    IconChevronUp,
    IconEdit,
    IconFolder,
    IconLock,
    IconLockOpen,
    IconPlus,
    IconTrash,
    IconUsers,
    IconX,
} from '@tabler/icons-react';
import MarkdownRenderer from '../components/MarkdownRenderer';
import { fetchJson } from '../services/api';
import { useAuthStore } from '../stores';

interface Tag {
    id: string;
    name: string;
    color: string | null;
}

interface AgentInProject {
    agent_id: string;
    name: string;
    avatar_url: string | null;
    role: string;
    added_at: string;
}

interface Project {
    id: string;
    name: string;
    description: string | null;
    brief: string | null;
    folder: string | null;
    status: string;
    collab_mode: string;
    target_completion_at: string | null;
    started_at: string | null;
    completed_at: string | null;
    created_by: string;
    created_at: string | null;
    updated_at: string | null;
    tags: Tag[];
    agents: AgentInProject[];
    agent_count: number;
    task_count: number;
    task_completed_count: number;
    task_open_count: number;
    completion_ratio: number;
}

interface A2APair {
    source_agent_id: string;
    target_agent_id: string;
    forward_authorized: boolean;
    reverse_authorized: boolean;
}

interface AvailableAgent {
    id: string;
    name: string;
    avatar_url: string | null;
}

interface TaskAssignee {
    agent_id: string;
    name: string;
    avatar_url: string | null;
}

interface ProjectTask {
    id: string;
    title: string;
    goal: string | null;
    acceptance_criteria: string | null;
    due_at: string | null;
    priority: string;
    status: string;
    assignee_agent_ids: string[];
    assignees: TaskAssignee[];
    created_by: string;
    sort_order: number;
    created_at: string | null;
    updated_at: string | null;
    completed_at: string | null;
}

interface ProjectActivity {
    id: string;
    event: string;
    actor_type: string;
    actor_id: string | null;
    actor_name: string | null;
    payload: Record<string, any>;
    created_at: string | null;
}

interface ProjectDecision {
    id: string;
    title: string;
    content: string | null;
    created_by: string | null;
    created_by_name: string | null;
    created_at: string | null;
    updated_at: string | null;
}

interface ChatSession {
    id: string;
    title: string;
}

const api = {
    getProject: (id: string) => fetchJson<Project>(`/projects/${id}`),
    updateProject: (id: string, body: object) =>
        fetchJson<Project>(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    transition: (id: string, action: string, force = false) =>
        fetchJson<Project>(`/projects/${id}/transition`, { method: 'POST', body: JSON.stringify({ action, force }) }),
    deleteProject: (id: string) => fetchJson(`/projects/${id}`, { method: 'DELETE' }),
    listAgents: (id: string) => fetchJson<AgentInProject[]>(`/projects/${id}/agents`),
    addAgent: (id: string, agent_id: string, role: string) =>
        fetchJson<AgentInProject>(`/projects/${id}/agents`, { method: 'POST', body: JSON.stringify({ agent_id, role }) }),
    updateAgentRole: (id: string, agentId: string, role: string) =>
        fetchJson(`/projects/${id}/agents/${agentId}`, { method: 'PATCH', body: JSON.stringify({ role }) }),
    removeAgent: (id: string, agentId: string) =>
        fetchJson(`/projects/${id}/agents/${agentId}`, { method: 'DELETE' }),
    getA2AMatrix: (id: string) => fetchJson<A2APair[]>(`/projects/${id}/a2a-matrix`),
    grantA2A: (id: string, src: string, dst: string) =>
        fetchJson(`/projects/${id}/a2a-grant`, { method: 'POST', body: JSON.stringify({ source_agent_id: src, target_agent_id: dst }) }),
    listAvailableAgents: (id: string) => fetchJson<AvailableAgent[]>(`/projects/${id}/available-agents`),
    listTasks: (id: string) => fetchJson<ProjectTask[]>(`/projects/${id}/tasks`),
    createTask: (id: string, body: object) =>
        fetchJson<ProjectTask>(`/projects/${id}/tasks`, { method: 'POST', body: JSON.stringify(body) }),
    updateTask: (id: string, taskId: string, body: object) =>
        fetchJson<ProjectTask>(`/projects/${id}/tasks/${taskId}`, { method: 'PATCH', body: JSON.stringify(body) }),
    deleteTask: (id: string, taskId: string) =>
        fetchJson(`/projects/${id}/tasks/${taskId}`, { method: 'DELETE' }),
    reorderTasks: (id: string, ordered_ids: string[]) =>
        fetchJson(`/projects/${id}/tasks/reorder`, { method: 'POST', body: JSON.stringify({ ordered_ids }) }),
    listActivities: (id: string) => fetchJson<ProjectActivity[]>(`/projects/${id}/activities?limit=20`),
    listDecisions: (id: string) => fetchJson<ProjectDecision[]>(`/projects/${id}/decisions`),
    createDecision: (id: string, body: object) =>
        fetchJson<ProjectDecision>(`/projects/${id}/decisions`, { method: 'POST', body: JSON.stringify(body) }),
    updateDecision: (id: string, decisionId: string, body: object) =>
        fetchJson<ProjectDecision>(`/projects/${id}/decisions/${decisionId}`, { method: 'PATCH', body: JSON.stringify(body) }),
    deleteDecision: (id: string, decisionId: string) =>
        fetchJson(`/projects/${id}/decisions/${decisionId}`, { method: 'DELETE' }),
    createChatSession: (agentId: string, body: object) =>
        fetchJson<ChatSession>(`/agents/${agentId}/sessions`, { method: 'POST', body: JSON.stringify(body) }),
};

const STATUS_COLORS: Record<string, string> = {
    draft: 'var(--text-tertiary)',
    active: 'var(--success)',
    on_hold: 'var(--warning)',
    completed: 'var(--primary)',
    archived: 'var(--text-tertiary)',
};

const TASK_STATUS_COLORS: Record<string, string> = {
    todo: 'var(--text-tertiary)',
    doing: 'var(--warning)',
    review: 'var(--primary)',
    done: 'var(--success)',
    cancelled: 'var(--error)',
};

const TASK_PRIORITY_COLORS: Record<string, string> = {
    low: '#6b7280',
    normal: 'var(--text-secondary)',
    high: '#f59e0b',
    urgent: 'var(--error)',
};

const TRANSITION_MAP: Record<string, string[]> = {
    draft: ['start', 'archive'],
    active: ['pause', 'complete', 'archive'],
    on_hold: ['resume', 'archive'],
    completed: ['archive'],
    archived: [],
};

function formatDate(iso: string | null) {
    if (!iso) return '';
    return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function formatDateTime(iso: string | null) {
    if (!iso) return '';
    return new Date(iso).toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function StatusBadge({ status, label }: { status: string; label: string }) {
    const color = STATUS_COLORS[status] || 'var(--text-tertiary)';
    return (
        <span
            style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                padding: '3px 10px',
                borderRadius: 20,
                fontSize: 12,
                fontWeight: 500,
                background: `${color}18`,
                color,
                border: `1px solid ${color}40`,
            }}
        >
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor' }} />
            {label}
        </span>
    );
}

function Pill({ color, label }: { color: string; label: string }) {
    return (
        <span
            style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                padding: '2px 8px',
                borderRadius: 999,
                fontSize: 11,
                fontWeight: 600,
                background: `${color}18`,
                color,
                border: `1px solid ${color}30`,
            }}
        >
            {label}
        </span>
    );
}

function AvatarOrInitial({ name, avatarUrl, size = 28 }: { name: string; avatarUrl: string | null; size?: number }) {
    if (avatarUrl) {
        return (
            <img
                src={avatarUrl}
                alt={name}
                style={{ width: size, height: size, borderRadius: '50%', objectFit: 'cover' }}
            />
        );
    }
    return (
        <div
            style={{
                width: size,
                height: size,
                borderRadius: '50%',
                background: 'var(--primary)18',
                color: 'var(--primary)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: size * 0.4,
                fontWeight: 600,
            }}
        >
            {name[0]?.toUpperCase()}
        </div>
    );
}

function ProgressRing({ ratio, total, completed, label }: { ratio: number; total: number; completed: number; label: string }) {
    const percent = Math.round((ratio || 0) * 100);
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div
                style={{
                    width: 72,
                    height: 72,
                    borderRadius: '50%',
                    background: `conic-gradient(var(--primary) ${percent * 3.6}deg, var(--bg-secondary) 0deg)`,
                    display: 'grid',
                    placeItems: 'center',
                }}
            >
                <div
                    style={{
                        width: 54,
                        height: 54,
                        borderRadius: '50%',
                        background: 'var(--bg-primary)',
                        display: 'grid',
                        placeItems: 'center',
                        border: '1px solid var(--border-subtle)',
                    }}
                >
                    <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 15, fontWeight: 700 }}>{percent}%</div>
                    </div>
                </div>
            </div>
            <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{completed}/{total}</div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{label}</div>
            </div>
        </div>
    );
}

function MetaRow({ label, value }: { label: string; value: ReactNode }) {
    return (
        <div>
            <div
                style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: 'var(--text-tertiary)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                    marginBottom: 4,
                }}
            >
                {label}
            </div>
            <div>{value}</div>
        </div>
    );
}

function activitySummary(activity: ProjectActivity, t: (key: string, options?: Record<string, any>) => string) {
    const actor = activity.actor_name || t('project.activities.system');
    const payload = activity.payload || {};

    switch (activity.event) {
        case 'project.created':
            return t('project.activities.events.projectCreated', { actor });
        case 'project.updated':
            return t('project.activities.events.projectUpdated', { actor });
        case 'project.tags_updated':
            return t('project.activities.events.projectTagsUpdated', { actor });
        case 'project.transitioned':
            return t('project.activities.events.projectTransitioned', {
                actor,
                from: t(`project.status.${payload.from_status}`, payload.from_status || ''),
                to: t(`project.status.${payload.to_status}`, payload.to_status || ''),
            });
        case 'project.overdue':
            return t('project.activities.events.projectOverdue');
        case 'agent.added':
            return t('project.activities.events.agentAdded', {
                actor,
                agent: payload.agent_name || 'Agent',
                role: t(`project.role.${payload.role}`, payload.role || ''),
            });
        case 'agent.role_changed':
            return t('project.activities.events.agentRoleChanged', {
                actor,
                agent: payload.agent_name || 'Agent',
                from: t(`project.role.${payload.from_role}`, payload.from_role || ''),
                to: t(`project.role.${payload.to_role}`, payload.to_role || ''),
            });
        case 'agent.removed':
            return t('project.activities.events.agentRemoved', {
                actor,
                agent: payload.agent_name || 'Agent',
            });
        case 'agent.a2a_granted':
            return t('project.activities.events.agentA2AGranted', {
                actor,
                source: payload.source_agent_name || 'Agent',
                target: payload.target_agent_name || 'Agent',
            });
        case 'task.created':
            return t('project.activities.events.taskCreated', {
                actor,
                title: payload.title || '',
            });
        case 'task.updated':
            return t('project.activities.events.taskUpdated', {
                actor,
                title: payload.title || '',
            });
        case 'task.deleted':
            return t('project.activities.events.taskDeleted', {
                actor,
                title: payload.title || '',
            });
        case 'task.reordered':
            return t('project.activities.events.taskReordered', { actor });
        case 'decision.created':
            return t('project.activities.events.decisionCreated', {
                actor,
                title: payload.title || '',
            });
        case 'decision.updated':
            return t('project.activities.events.decisionUpdated', {
                actor,
                title: payload.title || '',
            });
        case 'decision.deleted':
            return t('project.activities.events.decisionDeleted', {
                actor,
                title: payload.title || '',
            });
        default:
            return activity.event;
    }
}

function AddAgentModal({
    projectId,
    existing,
    onClose,
    onAdded,
}: {
    projectId: string;
    existing: string[];
    onClose: () => void;
    onAdded: () => void;
}) {
    const { t } = useTranslation();
    const [agents, setAgents] = useState<AvailableAgent[]>([]);
    const [q, setQ] = useState('');
    const [selected, setSelected] = useState<string | null>(null);
    const [role, setRole] = useState('member');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        api.listAvailableAgents(projectId)
            .then((data: any) => setAgents((data?.agents || data || []).filter((a: AvailableAgent) => !existing.includes(a.id))))
            .catch(() => setAgents([]));
    }, [existing, projectId]);

    const filtered = agents.filter((agent) => agent.name.toLowerCase().includes(q.toLowerCase()));

    const submit = async () => {
        if (!selected) return;
        setLoading(true);
        setError('');
        try {
            await api.addAgent(projectId, selected, role);
            onAdded();
        } catch (e: any) {
            setError(e?.message || 'Error');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div style={modalMaskStyle}>
            <div style={{ ...modalCardStyle, width: 420 }}>
                <div style={modalHeaderStyle}>
                    <h3 style={modalTitleStyle}>{t('project.agents.addAgent')}</h3>
                    <button onClick={onClose} style={iconButtonStyle}>
                        <IconX size={18} stroke={2} />
                    </button>
                </div>

                {error && <div style={{ color: 'var(--error)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

                <input
                    value={q}
                    onChange={(event) => setQ(event.target.value)}
                    placeholder={t('project.agents.searchPlaceholder', 'Search agents...')}
                    style={inputStyle}
                />

                <div style={{ flex: 1, overflowY: 'auto', border: '1px solid var(--border-subtle)', borderRadius: 8, marginTop: 8, marginBottom: 12 }}>
                    {filtered.length === 0 ? (
                        <div style={{ padding: 16, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
                            {t('project.agents.noAvailableAgents', 'No agents available')}
                        </div>
                    ) : (
                        filtered.map((agent) => (
                            <div
                                key={agent.id}
                                onClick={() => setSelected(agent.id === selected ? null : agent.id)}
                                style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 10,
                                    padding: '10px 14px',
                                    cursor: 'pointer',
                                    background: selected === agent.id ? 'var(--primary)10' : 'transparent',
                                    borderBottom: '1px solid var(--border-subtle)',
                                }}
                            >
                                <AvatarOrInitial name={agent.name} avatarUrl={agent.avatar_url} size={26} />
                                <span style={{ fontSize: 13, flex: 1 }}>{agent.name}</span>
                                {selected === agent.id && <IconCheck size={14} stroke={2} style={{ color: 'var(--primary)' }} />}
                            </div>
                        ))
                    )}
                </div>

                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
                    <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{t('project.agents.roleLabel', 'Role')}:</label>
                    {['lead', 'member', 'observer'].map((option) => (
                        <button
                            key={option}
                            onClick={() => setRole(option)}
                            style={{
                                padding: '4px 12px',
                                borderRadius: 20,
                                fontSize: 12,
                                cursor: 'pointer',
                                border: `1px solid ${role === option ? 'var(--primary)' : 'var(--border-subtle)'}`,
                                background: role === option ? 'var(--primary)' : 'var(--bg-secondary)',
                                color: role === option ? '#fff' : 'var(--text-secondary)',
                            }}
                        >
                            {t(`project.role.${option}`)}
                        </button>
                    ))}
                </div>

                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                    <button onClick={onClose} className="btn btn-ghost" style={{ fontSize: 13 }}>
                        {t('common.cancel', 'Cancel')}
                    </button>
                    <button onClick={submit} disabled={!selected || loading} className="btn btn-primary" style={{ fontSize: 13 }}>
                        {loading ? '...' : t('project.agents.addAgent')}
                    </button>
                </div>
            </div>
        </div>
    );
}

function TaskEditorModal({
    agents,
    task,
    onClose,
    onSubmit,
}: {
    agents: AgentInProject[];
    task?: ProjectTask | null;
    onClose: () => void;
    onSubmit: (body: any) => Promise<void>;
}) {
    const { t } = useTranslation();
    const [title, setTitle] = useState(task?.title || '');
    const [goal, setGoal] = useState(task?.goal || '');
    const [acceptance, setAcceptance] = useState(task?.acceptance_criteria || '');
    const [dueAt, setDueAt] = useState(task?.due_at ? task.due_at.slice(0, 10) : '');
    const [priority, setPriority] = useState(task?.priority || 'normal');
    const [status, setStatus] = useState(task?.status || 'todo');
    const [assignees, setAssignees] = useState<string[]>(task?.assignee_agent_ids || []);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const toggleAssignee = (agentId: string) => {
        setAssignees((prev) => (prev.includes(agentId) ? prev.filter((id) => id !== agentId) : [...prev, agentId]));
    };

    const submit = async () => {
        if (!title.trim()) {
            setError(t('project.tasks.titleRequired'));
            return;
        }
        setSaving(true);
        setError('');
        try {
            await onSubmit({
                title: title.trim(),
                goal: goal.trim() || null,
                acceptance_criteria: acceptance.trim() || null,
                due_at: dueAt ? new Date(dueAt).toISOString() : '',
                priority,
                status,
                assignee_agent_ids: assignees,
            });
        } catch (e: any) {
            setError(e?.message || 'Error');
            setSaving(false);
        }
    };

    return (
        <div style={modalMaskStyle}>
            <div style={{ ...modalCardStyle, width: 620 }}>
                <div style={modalHeaderStyle}>
                    <h3 style={modalTitleStyle}>
                        {task ? t('project.tasks.editTask') : t('project.tasks.addTask')}
                    </h3>
                    <button onClick={onClose} style={iconButtonStyle}>
                        <IconX size={18} stroke={2} />
                    </button>
                </div>

                {error && <div style={{ color: 'var(--error)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

                <div style={{ display: 'grid', gap: 14 }}>
                    <div>
                        <label style={labelStyle}>{t('project.tasks.taskTitle')}</label>
                        <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder={t('project.tasks.taskTitlePlaceholder')} style={inputStyle} />
                    </div>

                    <div>
                        <label style={labelStyle}>{t('project.tasks.goal')}</label>
                        <textarea value={goal} onChange={(event) => setGoal(event.target.value)} placeholder={t('project.tasks.goalPlaceholder')} rows={4} style={textareaStyle} />
                    </div>

                    <div>
                        <label style={labelStyle}>{t('project.tasks.acceptance')}</label>
                        <textarea value={acceptance} onChange={(event) => setAcceptance(event.target.value)} placeholder={t('project.tasks.acceptancePlaceholder')} rows={5} style={textareaStyle} />
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 12 }}>
                        <div>
                            <label style={labelStyle}>{t('project.tasks.dueDate')}</label>
                            <input type="date" value={dueAt} onChange={(event) => setDueAt(event.target.value)} style={inputStyle} />
                        </div>
                        <div>
                            <label style={labelStyle}>{t('project.tasks.priority')}</label>
                            <select value={priority} onChange={(event) => setPriority(event.target.value)} style={inputStyle}>
                                {['low', 'normal', 'high', 'urgent'].map((option) => (
                                    <option key={option} value={option}>
                                        {t(`project.tasks.priorityOptions.${option}`)}
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label style={labelStyle}>{t('project.tasks.status')}</label>
                            <select value={status} onChange={(event) => setStatus(event.target.value)} style={inputStyle}>
                                {['todo', 'doing', 'review', 'done', 'cancelled'].map((option) => (
                                    <option key={option} value={option}>
                                        {t(`project.tasks.statusOptions.${option}`)}
                                    </option>
                                ))}
                            </select>
                        </div>
                    </div>

                    <div>
                        <label style={labelStyle}>{t('project.tasks.assignees')}</label>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                            {agents.length === 0 ? (
                                <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>{t('project.tasks.noAssignableAgents')}</div>
                            ) : (
                                agents.map((agent) => {
                                    const selected = assignees.includes(agent.agent_id);
                                    return (
                                        <button
                                            key={agent.agent_id}
                                            onClick={() => toggleAssignee(agent.agent_id)}
                                            style={{
                                                display: 'inline-flex',
                                                alignItems: 'center',
                                                gap: 8,
                                                padding: '6px 10px',
                                                borderRadius: 999,
                                                border: `1px solid ${selected ? 'var(--primary)' : 'var(--border-subtle)'}`,
                                                background: selected ? 'var(--primary)12' : 'var(--bg-secondary)',
                                                color: selected ? 'var(--primary)' : 'var(--text-secondary)',
                                                cursor: 'pointer',
                                            }}
                                        >
                                            <AvatarOrInitial name={agent.name} avatarUrl={agent.avatar_url} size={20} />
                                            <span style={{ fontSize: 12 }}>{agent.name}</span>
                                        </button>
                                    );
                                })
                            )}
                        </div>
                    </div>
                </div>

                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 18 }}>
                    <button onClick={onClose} className="btn btn-ghost" style={{ fontSize: 13 }}>
                        {t('common.cancel', 'Cancel')}
                    </button>
                    <button onClick={submit} disabled={saving} className="btn btn-primary" style={{ fontSize: 13 }}>
                        {saving ? '...' : task ? t('project.tasks.saveEdit') : t('project.tasks.saveNew')}
                    </button>
                </div>
            </div>
        </div>
    );
}

function DecisionEditorModal({
    decision,
    onClose,
    onSubmit,
}: {
    decision?: ProjectDecision | null;
    onClose: () => void;
    onSubmit: (body: { title: string; content: string | null }) => Promise<void>;
}) {
    const { t } = useTranslation();
    const [title, setTitle] = useState(decision?.title || '');
    const [content, setContent] = useState(decision?.content || '');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const submit = async () => {
        if (!title.trim()) {
            setError(t('project.decisions.titleRequired'));
            return;
        }
        setSaving(true);
        setError('');
        try {
            await onSubmit({
                title: title.trim(),
                content: content.trim() || null,
            });
        } catch (e: any) {
            setError(e?.message || 'Error');
            setSaving(false);
        }
    };

    return (
        <div style={modalMaskStyle}>
            <div style={{ ...modalCardStyle, width: 620 }}>
                <div style={modalHeaderStyle}>
                    <h3 style={modalTitleStyle}>
                        {decision ? t('project.decisions.editDecision') : t('project.decisions.addDecision')}
                    </h3>
                    <button onClick={onClose} style={iconButtonStyle}>
                        <IconX size={18} stroke={2} />
                    </button>
                </div>

                {error && <div style={{ color: 'var(--error)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

                <div style={{ display: 'grid', gap: 14 }}>
                    <div>
                        <label style={labelStyle}>{t('project.decisions.title')}</label>
                        <input
                            value={title}
                            onChange={(event) => setTitle(event.target.value)}
                            placeholder={t('project.decisions.titlePlaceholder')}
                            style={inputStyle}
                        />
                    </div>
                    <div>
                        <label style={labelStyle}>{t('project.decisions.content')}</label>
                        <textarea
                            value={content}
                            onChange={(event) => setContent(event.target.value)}
                            placeholder={t('project.decisions.contentPlaceholder')}
                            rows={8}
                            style={textareaStyle}
                        />
                    </div>
                </div>

                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 18 }}>
                    <button onClick={onClose} className="btn btn-ghost" style={{ fontSize: 13 }}>
                        {t('common.cancel', 'Cancel')}
                    </button>
                    <button onClick={submit} disabled={saving} className="btn btn-primary" style={{ fontSize: 13 }}>
                        {saving ? '...' : decision ? t('project.decisions.saveEdit') : t('project.decisions.saveNew')}
                    </button>
                </div>
            </div>
        </div>
    );
}

function StartConversationModal({
    project,
    onClose,
}: {
    project: Project;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const [selectedAgentId, setSelectedAgentId] = useState<string | null>(project.agents[0]?.agent_id || null);
    const [creating, setCreating] = useState(false);
    const [error, setError] = useState('');

    const startConversation = async () => {
        if (!selectedAgentId) return;
        setCreating(true);
        setError('');
        try {
            const session = await api.createChatSession(selectedAgentId, { title: project.name });
            navigate(`/agents/${selectedAgentId}/chat?active_project=${project.id}&session_id=${session.id}`);
        } catch (e: any) {
            setError(e?.message || 'Error');
            setCreating(false);
        }
    };

    return (
        <div style={modalMaskStyle}>
            <div style={{ ...modalCardStyle, width: 520 }}>
                <div style={modalHeaderStyle}>
                    <h3 style={modalTitleStyle}>{t('project.conversations.startConversation')}</h3>
                    <button onClick={onClose} style={iconButtonStyle}>
                        <IconX size={18} stroke={2} />
                    </button>
                </div>

                {error && <div style={{ color: 'var(--error)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

                {project.agents.length === 0 ? (
                    <div style={emptyStateStyle}>{t('project.conversations.noAgents')}</div>
                ) : (
                    <>
                        <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>
                            {t('project.conversations.chooseAgent')}
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            {project.agents.map((agent) => {
                                const selected = selectedAgentId === agent.agent_id;
                                return (
                                    <button
                                        key={agent.agent_id}
                                        onClick={() => setSelectedAgentId(agent.agent_id)}
                                        style={{
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: 12,
                                            padding: '12px 14px',
                                            borderRadius: 10,
                                            border: `1px solid ${selected ? 'var(--primary)' : 'var(--border-subtle)'}`,
                                            background: selected ? 'var(--primary)10' : 'var(--bg-primary)',
                                            color: 'var(--text-primary)',
                                            cursor: 'pointer',
                                            textAlign: 'left',
                                        }}
                                    >
                                        <AvatarOrInitial name={agent.name} avatarUrl={agent.avatar_url} size={30} />
                                        <div style={{ flex: 1 }}>
                                            <div style={{ fontSize: 14, fontWeight: 600 }}>{agent.name}</div>
                                            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t(`project.role.${agent.role}`)}</div>
                                        </div>
                                        {selected && <IconCheck size={16} stroke={2} style={{ color: 'var(--primary)' }} />}
                                    </button>
                                );
                            })}
                        </div>
                    </>
                )}

                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 18 }}>
                    <button onClick={onClose} className="btn btn-ghost" style={{ fontSize: 13 }}>
                        {t('common.cancel', 'Cancel')}
                    </button>
                    <button
                        onClick={startConversation}
                        disabled={!selectedAgentId || creating || project.agents.length === 0}
                        className="btn btn-primary"
                        style={{ fontSize: 13 }}
                    >
                        {creating ? '...' : t('project.conversations.startConversation')}
                    </button>
                </div>
            </div>
        </div>
    );
}

function OverviewTab({
    project,
    tasks,
    activities,
    canWrite,
    onRefresh,
}: {
    project: Project;
    tasks: ProjectTask[];
    activities: ProjectActivity[];
    canWrite: boolean;
    onRefresh: () => Promise<void>;
}) {
    const { t } = useTranslation();
    const [editingBrief, setEditingBrief] = useState(false);
    const [editingDesc, setEditingDesc] = useState(false);
    const [briefValue, setBriefValue] = useState(project.brief || '');
    const [descValue, setDescValue] = useState(project.description || '');
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        setBriefValue(project.brief || '');
        setDescValue(project.description || '');
    }, [project.brief, project.description]);

    const openTasks = useMemo(() => tasks.filter((task) => !['done', 'cancelled'].includes(task.status)).slice(0, 5), [tasks]);
    const recentActivities = activities.slice(0, 6);

    const saveField = async (field: string, value: string) => {
        setSaving(true);
        try {
            await api.updateProject(project.id, { [field]: value || null });
            await onRefresh();
            if (field === 'description') setEditingDesc(false);
            if (field === 'brief') setEditingBrief(false);
        } finally {
            setSaving(false);
        }
    };

    return (
        <div style={{ display: 'flex', gap: 24, padding: '24px 0' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
                <section style={{ marginBottom: 28 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{t('project.overview.description')}</h3>
                        {canWrite && !editingDesc && (
                            <button onClick={() => setEditingDesc(true)} style={iconButtonStyle}>
                                <IconEdit size={14} stroke={1.5} />
                            </button>
                        )}
                    </div>
                    {editingDesc ? (
                        <div>
                            <textarea value={descValue} onChange={(event) => setDescValue(event.target.value)} rows={4} autoFocus style={textareaStyle} />
                            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 8 }}>
                                <button onClick={() => setEditingDesc(false)} className="btn btn-ghost" style={{ fontSize: 12 }}>
                                    {t('common.cancel', 'Cancel')}
                                </button>
                                <button onClick={() => saveField('description', descValue)} disabled={saving} className="btn btn-primary" style={{ fontSize: 12 }}>
                                    {t('common.save', 'Save')}
                                </button>
                            </div>
                        </div>
                    ) : (
                        <p style={{ margin: 0, fontSize: 14, lineHeight: 1.7, color: project.description ? 'var(--text-primary)' : 'var(--text-tertiary)' }}>
                            {project.description || t('project.overview.noDescription')}
                        </p>
                    )}
                </section>

                <section style={{ marginBottom: 28 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                        <div>
                            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{t('project.overview.brief')}</h3>
                            <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--text-tertiary)' }}>{t('project.overview.briefPlaceholder')}</p>
                        </div>
                        {canWrite && !editingBrief && (
                            <button onClick={() => setEditingBrief(true)} style={iconButtonStyle}>
                                <IconEdit size={14} stroke={1.5} />
                            </button>
                        )}
                    </div>
                    {editingBrief ? (
                        <div>
                            <textarea value={briefValue} onChange={(event) => setBriefValue(event.target.value)} rows={8} autoFocus placeholder={t('project.overview.briefPlaceholder')} style={textareaStyle} />
                            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 8 }}>
                                <button onClick={() => setEditingBrief(false)} className="btn btn-ghost" style={{ fontSize: 12 }}>
                                    {t('common.cancel', 'Cancel')}
                                </button>
                                <button onClick={() => saveField('brief', briefValue)} disabled={saving} className="btn btn-primary" style={{ fontSize: 12 }}>
                                    {t('common.save', 'Save')}
                                </button>
                            </div>
                        </div>
                    ) : (
                        <div style={previewCardStyle}>
                            {project.brief ? (
                                <MarkdownRenderer content={project.brief} />
                            ) : (
                                <div style={{ color: 'var(--text-tertiary)' }}>{t('project.overview.noBrief')}</div>
                            )}
                        </div>
                    )}
                </section>

                <section style={{ marginBottom: 28 }}>
                    <h3 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 600 }}>{t('project.overview.progress')}</h3>
                    <div style={panelStyle}>
                        <ProgressRing
                            ratio={project.completion_ratio}
                            total={project.task_count}
                            completed={project.task_completed_count}
                            label={t('project.overview.progressLabel')}
                        />
                    </div>
                </section>

                <section style={{ marginBottom: 28 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{t('project.overview.openDeliverables')}</h3>
                        <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{project.task_open_count}</span>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                        {openTasks.length === 0 ? (
                            <div style={emptyStateStyle}>{t('project.overview.noOpenDeliverables')}</div>
                        ) : (
                            openTasks.map((task) => (
                                <div key={task.id} style={panelStyle}>
                                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{ fontSize: 14, fontWeight: 600 }}>{task.title}</div>
                                            {task.goal && (
                                                <div style={{ marginTop: 6, fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                                                    {task.goal}
                                                </div>
                                            )}
                                        </div>
                                        <Pill color={TASK_STATUS_COLORS[task.status] || 'var(--text-tertiary)'} label={t(`project.tasks.statusOptions.${task.status}`)} />
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginTop: 10, fontSize: 12, color: 'var(--text-tertiary)' }}>
                                        {task.due_at && (
                                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                                <IconCalendar size={13} stroke={1.5} />
                                                {formatDate(task.due_at)}
                                            </span>
                                        )}
                                        <span>{t(`project.tasks.priorityOptions.${task.priority}`)}</span>
                                        <span>
                                            {task.assignees.length > 0
                                                ? task.assignees.map((assignee) => assignee.name).join(', ')
                                                : t('project.tasks.noneAssigned')}
                                        </span>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </section>

                <section>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{t('project.overview.recentActivity')}</h3>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                        {recentActivities.length === 0 ? (
                            <div style={emptyStateStyle}>{t('project.activities.empty')}</div>
                        ) : (
                            recentActivities.map((activity) => (
                                <div key={activity.id} style={panelStyle}>
                                    <div style={{ fontSize: 13, lineHeight: 1.6 }}>{activitySummary(activity, t)}</div>
                                    <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text-tertiary)' }}>
                                        {formatDateTime(activity.created_at)}
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </section>
            </div>

            <div style={{ width: 240, flexShrink: 0 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                    <MetaRow label={t('project.overview.status')} value={<StatusBadge status={project.status} label={t(`project.status.${project.status}`)} />} />
                    {project.folder && (
                        <MetaRow
                            label={t('project.overview.folder')}
                            value={<span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13 }}><IconFolder size={13} stroke={1.5} />{project.folder}</span>}
                        />
                    )}
                    {project.target_completion_at && (
                        <MetaRow
                            label={t('project.overview.targetDate')}
                            value={<span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13, color: 'var(--text-secondary)' }}><IconCalendar size={13} stroke={1.5} />{formatDate(project.target_completion_at)}</span>}
                        />
                    )}
                    <MetaRow label={t('project.overview.createdAt')} value={<span style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>{formatDate(project.created_at)}</span>} />
                    <MetaRow label={t('project.overview.collabMode')} value={<span style={{ fontSize: 13 }}>{t(`project.collabMode.${project.collab_mode}`)}</span>} />
                    {project.tags.length > 0 && (
                        <div>
                            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
                                {t('project.overview.tags')}
                            </div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                {project.tags.map((tag) => (
                                    <span
                                        key={tag.id}
                                        style={{
                                            fontSize: 11,
                                            padding: '2px 8px',
                                            borderRadius: 20,
                                            background: tag.color ? `${tag.color}20` : 'var(--bg-secondary)',
                                            color: tag.color || 'var(--text-tertiary)',
                                            border: `1px solid ${tag.color ? `${tag.color}40` : 'var(--border-subtle)'}`,
                                        }}
                                    >
                                        {tag.name}
                                    </span>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function TasksTab({
    project,
    tasks,
    canWrite,
    onRefresh,
}: {
    project: Project;
    tasks: ProjectTask[];
    canWrite: boolean;
    onRefresh: () => Promise<void>;
}) {
    const { t } = useTranslation();
    const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);
    const [filter, setFilter] = useState('all');
    const [editingTask, setEditingTask] = useState<ProjectTask | null>(null);
    const [showCreate, setShowCreate] = useState(false);
    const [busyTaskId, setBusyTaskId] = useState<string | null>(null);

    const filteredTasks = useMemo(
        () => (filter === 'all' ? tasks : tasks.filter((task) => task.status === filter)),
        [filter, tasks],
    );

    const updateStatus = async (taskId: string, nextStatus: string) => {
        setBusyTaskId(taskId);
        try {
            await api.updateTask(project.id, taskId, { status: nextStatus });
            await onRefresh();
        } finally {
            setBusyTaskId(null);
        }
    };

    const removeTask = async (taskId: string) => {
        if (!window.confirm(t('project.tasks.confirmDelete'))) return;
        setBusyTaskId(taskId);
        try {
            await api.deleteTask(project.id, taskId);
            await onRefresh();
        } finally {
            setBusyTaskId(null);
        }
    };

    const reorder = async (taskId: string, direction: 'up' | 'down') => {
        const index = tasks.findIndex((task) => task.id === taskId);
        if (index < 0) return;
        const targetIndex = direction === 'up' ? index - 1 : index + 1;
        if (targetIndex < 0 || targetIndex >= tasks.length) return;

        const reordered = [...tasks];
        const [moved] = reordered.splice(index, 1);
        reordered.splice(targetIndex, 0, moved);
        setBusyTaskId(taskId);
        try {
            await api.reorderTasks(project.id, reordered.map((task) => task.id));
            await onRefresh();
        } finally {
            setBusyTaskId(null);
        }
    };

    const saveTask = async (body: any) => {
        if (editingTask) {
            await api.updateTask(project.id, editingTask.id, body);
            setEditingTask(null);
        } else {
            await api.createTask(project.id, body);
            setShowCreate(false);
        }
        await onRefresh();
    };

    return (
        <div style={{ padding: '24px 0' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {['all', 'todo', 'doing', 'review', 'done', 'cancelled'].map((option) => (
                        <button
                            key={option}
                            onClick={() => setFilter(option)}
                            style={{
                                padding: '5px 12px',
                                borderRadius: 999,
                                border: `1px solid ${filter === option ? 'var(--primary)' : 'var(--border-subtle)'}`,
                                background: filter === option ? 'var(--primary)12' : 'transparent',
                                color: filter === option ? 'var(--primary)' : 'var(--text-secondary)',
                                cursor: 'pointer',
                                fontSize: 12,
                            }}
                        >
                            {option === 'all' ? t('project.tasks.filters.all') : t(`project.tasks.statusOptions.${option}`)}
                        </button>
                    ))}
                </div>
                {canWrite && (
                    <button onClick={() => setShowCreate(true)} className="btn btn-primary" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                        <IconPlus size={14} stroke={2} />
                        {t('project.tasks.addTask')}
                    </button>
                )}
            </div>

            {filteredTasks.length === 0 ? (
                <div style={emptyStateStyle}>{t('project.tasks.empty')}</div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {filteredTasks.map((task, index) => {
                        const expanded = expandedTaskId === task.id;
                        const color = TASK_STATUS_COLORS[task.status] || 'var(--text-tertiary)';
                        return (
                            <div key={task.id} style={panelStyle}>
                                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                            <button onClick={() => setExpandedTaskId(expanded ? null : task.id)} style={{ ...iconButtonStyle, color: 'var(--text-tertiary)' }}>
                                                {expanded ? <IconChevronUp size={16} stroke={1.8} /> : <IconChevronDown size={16} stroke={1.8} />}
                                            </button>
                                            <div style={{ fontSize: 14, fontWeight: 600 }}>{task.title}</div>
                                            <Pill color={color} label={t(`project.tasks.statusOptions.${task.status}`)} />
                                            <Pill color={TASK_PRIORITY_COLORS[task.priority] || 'var(--text-secondary)'} label={t(`project.tasks.priorityOptions.${task.priority}`)} />
                                        </div>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginTop: 10, fontSize: 12, color: 'var(--text-tertiary)' }}>
                                            {task.due_at && (
                                                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                                    <IconCalendar size={13} stroke={1.5} />
                                                    {formatDate(task.due_at)}
                                                </span>
                                            )}
                                            <span>
                                                {task.assignees.length > 0
                                                    ? task.assignees.map((assignee) => assignee.name).join(', ')
                                                    : t('project.tasks.noneAssigned')}
                                            </span>
                                        </div>
                                    </div>

                                    {canWrite && (
                                        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                                            <button
                                                onClick={() => reorder(task.id, 'up')}
                                                disabled={busyTaskId === task.id || filter !== 'all' || index === 0}
                                                style={smallGhostButtonStyle}
                                                title={t('project.tasks.moveUp')}
                                            >
                                                <IconChevronUp size={14} stroke={2} />
                                            </button>
                                            <button
                                                onClick={() => reorder(task.id, 'down')}
                                                disabled={busyTaskId === task.id || filter !== 'all' || tasks.findIndex((item) => item.id === task.id) === tasks.length - 1}
                                                style={smallGhostButtonStyle}
                                                title={t('project.tasks.moveDown')}
                                            >
                                                <IconChevronDown size={14} stroke={2} />
                                            </button>
                                            <button onClick={() => setEditingTask(task)} style={smallGhostButtonStyle} title={t('project.tasks.editTask')}>
                                                <IconEdit size={14} stroke={1.7} />
                                            </button>
                                            <button onClick={() => removeTask(task.id)} disabled={busyTaskId === task.id} style={smallGhostButtonStyle} title={t('project.tasks.deleteTask')}>
                                                <IconTrash size={14} stroke={1.7} />
                                            </button>
                                        </div>
                                    )}
                                </div>

                                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                                    {['todo', 'doing', 'review', 'done', 'cancelled'].map((option) => (
                                        <button
                                            key={option}
                                            onClick={() => canWrite && updateStatus(task.id, option)}
                                            disabled={!canWrite || busyTaskId === task.id}
                                            style={{
                                                padding: '4px 10px',
                                                borderRadius: 999,
                                                border: `1px solid ${task.status === option ? TASK_STATUS_COLORS[option] : 'var(--border-subtle)'}`,
                                                background: task.status === option ? `${TASK_STATUS_COLORS[option]}18` : 'transparent',
                                                color: task.status === option ? TASK_STATUS_COLORS[option] : 'var(--text-secondary)',
                                                cursor: canWrite ? 'pointer' : 'default',
                                                fontSize: 11,
                                            }}
                                        >
                                            {t(`project.tasks.statusOptions.${option}`)}
                                        </button>
                                    ))}
                                </div>

                                {expanded && (
                                    <div style={{ marginTop: 14, display: 'grid', gap: 14 }}>
                                        <div>
                                            <div style={sectionKickerStyle}>{t('project.tasks.goal')}</div>
                                            <div style={previewCardStyle}>
                                                {task.goal ? <MarkdownRenderer content={task.goal} /> : <span style={{ color: 'var(--text-tertiary)' }}>{t('project.tasks.noGoal')}</span>}
                                            </div>
                                        </div>
                                        <div>
                                            <div style={sectionKickerStyle}>{t('project.tasks.acceptance')}</div>
                                            <div style={previewCardStyle}>
                                                {task.acceptance_criteria ? <MarkdownRenderer content={task.acceptance_criteria} /> : <span style={{ color: 'var(--text-tertiary)' }}>{t('project.tasks.noAcceptance')}</span>}
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}

            {showCreate && (
                <TaskEditorModal
                    agents={project.agents}
                    onClose={() => setShowCreate(false)}
                    onSubmit={saveTask}
                />
            )}
            {editingTask && (
                <TaskEditorModal
                    task={editingTask}
                    agents={project.agents}
                    onClose={() => setEditingTask(null)}
                    onSubmit={saveTask}
                />
            )}
        </div>
    );
}

function DecisionsTab({
    project,
    decisions,
    canWrite,
    onRefresh,
}: {
    project: Project;
    decisions: ProjectDecision[];
    canWrite: boolean;
    onRefresh: () => Promise<void>;
}) {
    const { t } = useTranslation();
    const [showCreate, setShowCreate] = useState(false);
    const [editingDecision, setEditingDecision] = useState<ProjectDecision | null>(null);
    const [busyDecisionId, setBusyDecisionId] = useState<string | null>(null);

    const saveDecision = async (body: { title: string; content: string | null }) => {
        if (editingDecision) {
            await api.updateDecision(project.id, editingDecision.id, body);
            setEditingDecision(null);
        } else {
            await api.createDecision(project.id, body);
            setShowCreate(false);
        }
        await onRefresh();
    };

    const removeDecision = async (decisionId: string) => {
        if (!window.confirm(t('project.decisions.confirmDelete'))) return;
        setBusyDecisionId(decisionId);
        try {
            await api.deleteDecision(project.id, decisionId);
            await onRefresh();
        } finally {
            setBusyDecisionId(null);
        }
    };

    return (
        <div style={{ padding: '24px 0' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{t('project.tabs.decisions')} ({decisions.length})</h3>
                {canWrite && (
                    <button onClick={() => setShowCreate(true)} className="btn btn-ghost" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                        <IconPlus size={14} stroke={2} />
                        {t('project.decisions.addDecision')}
                    </button>
                )}
            </div>

            {decisions.length === 0 ? (
                <div style={emptyStateStyle}>{t('project.decisions.empty')}</div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {decisions.map((decision) => (
                        <div key={decision.id} style={panelStyle}>
                            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ fontSize: 15, fontWeight: 600 }}>{decision.title}</div>
                                    <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text-tertiary)' }}>
                                        {decision.created_by_name || t('project.activities.system')}
                                        {decision.created_at ? ` · ${formatDateTime(decision.created_at)}` : ''}
                                    </div>
                                </div>
                                {canWrite && (
                                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                                        <button onClick={() => setEditingDecision(decision)} style={smallGhostButtonStyle} title={t('project.decisions.editDecision')}>
                                            <IconEdit size={14} stroke={1.7} />
                                        </button>
                                        <button onClick={() => removeDecision(decision.id)} disabled={busyDecisionId === decision.id} style={smallGhostButtonStyle} title={t('project.decisions.deleteDecision')}>
                                            <IconTrash size={14} stroke={1.7} />
                                        </button>
                                    </div>
                                )}
                            </div>

                            <div style={{ marginTop: 12 }}>
                                <div style={previewCardStyle}>
                                    {decision.content ? (
                                        <MarkdownRenderer content={decision.content} />
                                    ) : (
                                        <span style={{ color: 'var(--text-tertiary)' }}>{t('project.decisions.noContent')}</span>
                                    )}
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {showCreate && (
                <DecisionEditorModal
                    onClose={() => setShowCreate(false)}
                    onSubmit={saveDecision}
                />
            )}
            {editingDecision && (
                <DecisionEditorModal
                    decision={editingDecision}
                    onClose={() => setEditingDecision(null)}
                    onSubmit={saveDecision}
                />
            )}
        </div>
    );
}

function AgentsTab({
    project,
    canWrite,
    onRefresh,
}: {
    project: Project;
    canWrite: boolean;
    onRefresh: () => Promise<void>;
}) {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const [a2a, setA2A] = useState<A2APair[]>([]);
    const [showAdd, setShowAdd] = useState(false);
    const [a2aLoading, setA2ALoading] = useState<string | null>(null);

    const agents = project.agents;

    useEffect(() => {
        if (agents.length >= 2) {
            api.getA2AMatrix(project.id).then(setA2A).catch(() => setA2A([]));
        } else {
            setA2A([]);
        }
    }, [project.id, agents]);

    const handleRemove = async (agentId: string) => {
        await api.removeAgent(project.id, agentId);
        await onRefresh();
    };

    const handleRoleChange = async (agentId: string, role: string) => {
        await api.updateAgentRole(project.id, agentId, role);
        await onRefresh();
    };

    const handleGrant = async (sourceAgentId: string, targetAgentId: string) => {
        const key = `${sourceAgentId}-${targetAgentId}`;
        setA2ALoading(key);
        try {
            await api.grantA2A(project.id, sourceAgentId, targetAgentId);
            const updated = await api.getA2AMatrix(project.id);
            setA2A(updated);
        } finally {
            setA2ALoading(null);
        }
    };

    const agentName = (id: string) => agents.find((agent) => agent.agent_id === id)?.name || id.slice(0, 8);

    return (
        <div style={{ padding: '24px 0' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{t('project.tabs.agents')} ({agents.length})</h3>
                {canWrite && (
                    <button onClick={() => setShowAdd(true)} className="btn btn-ghost" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                        <IconPlus size={14} stroke={2} />
                        {t('project.agents.addAgent')}
                    </button>
                )}
            </div>

            {agents.length === 0 ? (
                <div style={emptyStateStyle}>{t('project.agents.empty')}</div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 32 }}>
                    {agents.map((agent) => (
                        <div key={agent.agent_id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)', borderRadius: 10 }}>
                            <AvatarOrInitial name={agent.name} avatarUrl={agent.avatar_url} size={32} />
                            <div style={{ flex: 1 }}>
                                <div style={{ fontWeight: 500, fontSize: 14 }}>{agent.name}</div>
                            </div>
                            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                                {['lead', 'member', 'observer'].map((role) => (
                                    <button
                                        key={role}
                                        onClick={() => canWrite && handleRoleChange(agent.agent_id, role)}
                                        disabled={!canWrite}
                                        style={{
                                            padding: '3px 10px',
                                            borderRadius: 20,
                                            fontSize: 11,
                                            cursor: canWrite ? 'pointer' : 'default',
                                            border: `1px solid ${agent.role === role ? 'var(--primary)' : 'var(--border-subtle)'}`,
                                            background: agent.role === role ? 'var(--primary)' : 'transparent',
                                            color: agent.role === role ? '#fff' : 'var(--text-secondary)',
                                        }}
                                    >
                                        {t(`project.role.${role}`)}
                                    </button>
                                ))}
                            </div>
                            <button
                                onClick={() => navigate(`/agents/${agent.agent_id}/chat?active_project=${project.id}`)}
                                className="btn btn-ghost"
                                style={{ fontSize: 12, padding: '5px 12px' }}
                            >
                                {t('project.agents.workWithAgent')}
                            </button>
                            {canWrite && (
                                <button onClick={() => handleRemove(agent.agent_id)} style={iconButtonStyle} title={t('project.agents.removeAgent')}>
                                    <IconTrash size={14} stroke={1.5} />
                                </button>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {agents.length >= 2 && (
                <div>
                    <h3 style={{ margin: '0 0 8px', fontSize: 14, fontWeight: 600 }}>{t('project.agents.a2aMatrix')}</h3>
                    <p style={{ margin: '0 0 14px', fontSize: 13, color: 'var(--text-secondary)' }}>{t('project.agents.a2aMatrixDesc')}</p>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {a2a.map((pair) => {
                            const key = `${pair.source_agent_id}-${pair.target_agent_id}`;
                            const bothAuthorized = pair.forward_authorized && pair.reverse_authorized;
                            return (
                                <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', background: 'var(--bg-secondary)', borderRadius: 8, border: '1px solid var(--border-subtle)' }}>
                                    <span style={{ fontSize: 13, flex: 1 }}>{agentName(pair.source_agent_id)} ↔ {agentName(pair.target_agent_id)}</span>
                                    <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: bothAuthorized ? 'var(--success)' : 'var(--text-tertiary)' }}>
                                        {bothAuthorized ? <IconLockOpen size={13} stroke={1.5} /> : <IconLock size={13} stroke={1.5} />}
                                        {bothAuthorized ? t('project.agents.authorized') : t('project.agents.notAuthorized')}
                                    </span>
                                    {!bothAuthorized && canWrite && (
                                        <button onClick={() => handleGrant(pair.source_agent_id, pair.target_agent_id)} disabled={a2aLoading === key} className="btn btn-ghost" style={{ fontSize: 12, padding: '3px 10px' }}>
                                            {a2aLoading === key ? '...' : t('project.agents.grantBoth')}
                                        </button>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {showAdd && (
                <AddAgentModal
                    projectId={project.id}
                    existing={agents.map((agent) => agent.agent_id)}
                    onClose={() => setShowAdd(false)}
                    onAdded={async () => {
                        setShowAdd(false);
                        await onRefresh();
                    }}
                />
            )}
        </div>
    );
}

function SettingsTab({
    project,
    canWrite,
    onRefresh,
    onDeleted,
}: {
    project: Project;
    canWrite: boolean;
    onRefresh: () => Promise<void>;
    onDeleted: () => void;
}) {
    const { t } = useTranslation();
    const [name, setName] = useState(project.name);
    const [folder, setFolder] = useState(project.folder || '');
    const [targetDate, setTargetDate] = useState(project.target_completion_at ? project.target_completion_at.slice(0, 10) : '');
    const [collabMode, setCollabMode] = useState(project.collab_mode);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        setName(project.name);
        setFolder(project.folder || '');
        setTargetDate(project.target_completion_at ? project.target_completion_at.slice(0, 10) : '');
        setCollabMode(project.collab_mode);
    }, [project]);

    const save = async () => {
        setSaving(true);
        setError('');
        try {
            await api.updateProject(project.id, {
                name,
                folder: folder || null,
                collab_mode: collabMode,
                target_completion_at: targetDate ? new Date(targetDate).toISOString() : '',
            });
            await onRefresh();
        } catch (e: any) {
            setError(e?.message || 'Error');
        } finally {
            setSaving(false);
        }
    };

    const handleTransition = async (action: string, force = false) => {
        const confirmMessages: Record<string, string> = {
            complete: t('project.transition.confirmComplete'),
            archive: t('project.transition.confirmArchive'),
        };
        if (!force && confirmMessages[action] && !window.confirm(confirmMessages[action])) return;
        try {
            await api.transition(project.id, action, force);
            await onRefresh();
        } catch (e: any) {
            if (action === 'complete' && e?.status === 409 && e?.detail?.pending_task_count) {
                if (window.confirm(t('project.transition.confirmForceComplete', { count: e.detail.pending_task_count }))) {
                    await handleTransition(action, true);
                    return;
                }
            }
            alert(e?.message || 'Error');
        }
    };

    const handleDelete = async () => {
        if (!window.confirm(t('project.settings.confirmDelete'))) return;
        await api.deleteProject(project.id);
        onDeleted();
    };

    const transitions = TRANSITION_MAP[project.status] || [];
    const transitionLabels: Record<string, string> = {
        start: t('project.transition.start'),
        pause: t('project.transition.pause'),
        resume: t('project.transition.resume'),
        complete: t('project.transition.complete'),
        archive: t('project.transition.archive'),
    };

    return (
        <div style={{ padding: '24px 0', maxWidth: 560 }}>
            <h3 style={{ margin: '0 0 16px', fontSize: 14, fontWeight: 600 }}>{t('project.settings.basicInfo')}</h3>
            {error && <div style={{ color: 'var(--error)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginBottom: 24 }}>
                <div>
                    <label style={labelStyle}>{t('project.settings.name')}</label>
                    <input value={name} onChange={(event) => setName(event.target.value)} disabled={!canWrite} style={inputStyle} />
                </div>
                <div>
                    <label style={labelStyle}>{t('project.settings.folder')}</label>
                    <input value={folder} onChange={(event) => setFolder(event.target.value)} disabled={!canWrite} placeholder={t('project.settings.folderPlaceholder')} style={inputStyle} />
                </div>
                <div>
                    <label style={labelStyle}>{t('project.settings.targetDate')}</label>
                    <input type="date" value={targetDate} onChange={(event) => setTargetDate(event.target.value)} disabled={!canWrite} style={inputStyle} />
                </div>
                <div>
                    <label style={labelStyle}>{t('project.settings.collabMode')}</label>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {['isolated', 'group_chat', 'lead_helper'].map((mode) => (
                            <button
                                key={mode}
                                onClick={() => canWrite && setCollabMode(mode)}
                                disabled={!canWrite}
                                style={{
                                    padding: '6px 14px',
                                    borderRadius: 8,
                                    fontSize: 12,
                                    cursor: canWrite ? 'pointer' : 'default',
                                    border: `1px solid ${collabMode === mode ? 'var(--primary)' : 'var(--border-subtle)'}`,
                                    background: collabMode === mode ? 'var(--primary)10' : 'transparent',
                                    color: collabMode === mode ? 'var(--primary)' : 'var(--text-secondary)',
                                }}
                            >
                                {t(`project.collabMode.${mode}`)}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            {canWrite && (
                <button onClick={save} disabled={saving || !name.trim()} className="btn btn-primary" style={{ fontSize: 13 }}>
                    {saving ? '...' : t('common.save', 'Save')}
                </button>
            )}

            {transitions.length > 0 && canWrite && (
                <div style={{ marginTop: 32 }}>
                    <h3 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 600 }}>{t('project.settings.statusActions')}</h3>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {transitions.map((action) => (
                            <button
                                key={action}
                                onClick={() => handleTransition(action)}
                                style={{
                                    padding: '7px 16px',
                                    borderRadius: 8,
                                    fontSize: 13,
                                    cursor: 'pointer',
                                    border: `1px solid ${action === 'archive' || action === 'complete' ? 'var(--warning)' : 'var(--border-subtle)'}`,
                                    background: 'transparent',
                                    color: action === 'archive' || action === 'complete' ? 'var(--warning)' : 'var(--text-secondary)',
                                }}
                            >
                                {transitionLabels[action]}
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {canWrite && (
                <div style={{ marginTop: 40, padding: 20, border: '1px solid var(--error)30', borderRadius: 10 }}>
                    <h3 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 600, color: 'var(--error)' }}>{t('project.settings.dangerZone')}</h3>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, gap: 16 }}>
                        <div>
                            <div style={{ fontSize: 13, fontWeight: 500 }}>{t('project.settings.archiveProject')}</div>
                            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t('project.settings.archiveDesc')}</div>
                        </div>
                        {project.status !== 'archived' && (
                            <button onClick={() => handleTransition('archive')} style={{ padding: '6px 14px', borderRadius: 8, fontSize: 13, cursor: 'pointer', border: '1px solid var(--warning)', background: 'transparent', color: 'var(--warning)' }}>
                                {t('project.transition.archive')}
                            </button>
                        )}
                    </div>
                    {project.status === 'archived' && (
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
                            <div>
                                <div style={{ fontSize: 13, fontWeight: 500 }}>{t('project.settings.deleteProject')}</div>
                                <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t('project.settings.deleteDesc')}</div>
                            </div>
                            <button onClick={handleDelete} style={{ padding: '6px 14px', borderRadius: 8, fontSize: 13, cursor: 'pointer', border: '1px solid var(--error)', background: 'transparent', color: 'var(--error)' }}>
                                {t('project.settings.deleteProject')}
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

const modalMaskStyle: CSSProperties = {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.5)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1000,
};

const modalCardStyle: CSSProperties = {
    background: 'var(--bg-primary)',
    borderRadius: 12,
    padding: 24,
    maxHeight: '80vh',
    display: 'flex',
    flexDirection: 'column',
    boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
    overflowY: 'auto',
};

const modalHeaderStyle: CSSProperties = {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
};

const modalTitleStyle: CSSProperties = {
    margin: 0,
    fontSize: 15,
    fontWeight: 600,
};

const iconButtonStyle: CSSProperties = {
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    color: 'var(--text-tertiary)',
    display: 'flex',
    padding: 4,
};

const inputStyle: CSSProperties = {
    width: '100%',
    padding: '8px 12px',
    border: '1px solid var(--border-subtle)',
    borderRadius: 8,
    background: 'var(--bg-secondary)',
    color: 'var(--text-primary)',
    fontSize: 14,
    boxSizing: 'border-box',
    outline: 'none',
};

const textareaStyle: CSSProperties = {
    width: '100%',
    padding: '10px 12px',
    border: '1px solid var(--border-subtle)',
    borderRadius: 8,
    background: 'var(--bg-secondary)',
    color: 'var(--text-primary)',
    fontSize: 13,
    resize: 'vertical',
    outline: 'none',
    fontFamily: 'inherit',
    boxSizing: 'border-box',
    lineHeight: 1.6,
};

const labelStyle: CSSProperties = {
    fontSize: 13,
    fontWeight: 500,
    color: 'var(--text-secondary)',
    display: 'block',
    marginBottom: 6,
};

const panelStyle: CSSProperties = {
    background: 'var(--bg-primary)',
    border: '1px solid var(--border-subtle)',
    borderRadius: 10,
    padding: 16,
};

const previewCardStyle: CSSProperties = {
    background: 'var(--bg-secondary)',
    padding: 14,
    borderRadius: 8,
    border: '1px solid var(--border-subtle)',
};

const emptyStateStyle: CSSProperties = {
    padding: 24,
    textAlign: 'center',
    color: 'var(--text-tertiary)',
    border: '1px dashed var(--border-subtle)',
    borderRadius: 10,
    fontSize: 13,
};

const sectionKickerStyle: CSSProperties = {
    fontSize: 11,
    fontWeight: 600,
    color: 'var(--text-tertiary)',
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    marginBottom: 6,
};

const smallGhostButtonStyle: CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 28,
    height: 28,
    borderRadius: 8,
    border: '1px solid var(--border-subtle)',
    background: 'transparent',
    color: 'var(--text-tertiary)',
    cursor: 'pointer',
};

export default function ProjectDetail() {
    const { id } = useParams<{ id: string }>();
    const { t } = useTranslation();
    const navigate = useNavigate();
    const user = useAuthStore((state) => state.user);

    const [project, setProject] = useState<Project | null>(null);
    const [tasks, setTasks] = useState<ProjectTask[]>([]);
    const [activities, setActivities] = useState<ProjectActivity[]>([]);
    const [decisions, setDecisions] = useState<ProjectDecision[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [activeTab, setActiveTab] = useState<'overview' | 'tasks' | 'decisions' | 'agents' | 'settings'>('overview');
    const [showStartConversation, setShowStartConversation] = useState(false);

    const loadAll = useCallback(
        async (showLoading = true) => {
            if (!id) return;
            if (showLoading) setLoading(true);
            try {
                const [nextProject, nextTasks, nextActivities, nextDecisions] = await Promise.all([
                    api.getProject(id),
                    api.listTasks(id),
                    api.listActivities(id),
                    api.listDecisions(id),
                ]);
                setProject(nextProject);
                setTasks(nextTasks);
                setActivities(nextActivities);
                setDecisions(nextDecisions);
                setError('');
            } catch (e: any) {
                setError(e?.message || 'Error');
            } finally {
                if (showLoading) setLoading(false);
            }
        },
        [id],
    );

    useEffect(() => {
        loadAll(true);
    }, [loadAll]);

    if (loading) {
        return <div style={{ padding: 40, color: 'var(--text-tertiary)' }}>{t('common.loading', 'Loading...')}</div>;
    }

    if (error || !project) {
        return <div style={{ padding: 40, color: 'var(--error)' }}>{error || 'Project not found'}</div>;
    }

    const canWrite = user
        ? String(project.created_by) === String(user.id) || ['platform_admin', 'org_admin'].includes(user.role || '')
        : false;

    const tabs = [
        { key: 'overview', label: t('project.tabs.overview') },
        { key: 'tasks', label: t('project.tabs.tasks') },
        { key: 'decisions', label: t('project.tabs.decisions') },
        { key: 'agents', label: t('project.tabs.agents') },
        { key: 'settings', label: t('project.tabs.settings') },
    ] as const;

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
            <div style={{ padding: '20px 32px 0', borderBottom: '1px solid var(--border-subtle)', flexShrink: 0 }}>
                <button onClick={() => navigate('/projects')} style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 13, padding: 0, marginBottom: 12 }}>
                    <IconArrowLeft size={14} stroke={2} /> {t('project.title')}
                </button>

                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 16 }}>
                    <div style={{ flex: 1 }}>
                        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>{project.name}</h1>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginTop: 6, fontSize: 12, color: 'var(--text-tertiary)' }}>
                            {project.folder && (
                                <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                                    <IconFolder size={12} stroke={1.5} />
                                    {project.folder}
                                </span>
                            )}
                            <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                                <IconUsers size={12} stroke={1.5} />
                                {project.agent_count}
                            </span>
                            <span>{project.task_count} {t('project.tasks.summaryLabel')}</span>
                        </div>
                    </div>
                    <button
                        onClick={() => setShowStartConversation(true)}
                        className="btn btn-primary"
                        style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}
                    >
                        <IconPlus size={14} stroke={2} />
                        {t('project.conversations.newConversation')}
                    </button>
                    <StatusBadge status={project.status} label={t(`project.status.${project.status}`)} />
                </div>

                <div style={{ display: 'flex', gap: 0 }}>
                    {tabs.map((tab) => (
                        <button
                            key={tab.key}
                            onClick={() => setActiveTab(tab.key)}
                            style={{
                                padding: '8px 18px',
                                background: 'none',
                                border: 'none',
                                cursor: 'pointer',
                                fontSize: 14,
                                fontWeight: activeTab === tab.key ? 600 : 400,
                                color: activeTab === tab.key ? 'var(--text-primary)' : 'var(--text-tertiary)',
                                borderBottom: activeTab === tab.key ? '2px solid var(--primary)' : '2px solid transparent',
                                marginBottom: -1,
                            }}
                        >
                            {tab.label}
                        </button>
                    ))}
                </div>
            </div>

            <div style={{ flex: 1, overflowY: 'auto', padding: '0 32px' }}>
                {activeTab === 'overview' && (
                    <OverviewTab
                        project={project}
                        tasks={tasks}
                        activities={activities}
                        canWrite={canWrite}
                        onRefresh={() => loadAll(false)}
                    />
                )}
                {activeTab === 'tasks' && (
                    <TasksTab
                        project={project}
                        tasks={tasks}
                        canWrite={canWrite}
                        onRefresh={() => loadAll(false)}
                    />
                )}
                {activeTab === 'decisions' && (
                    <DecisionsTab
                        project={project}
                        decisions={decisions}
                        canWrite={canWrite}
                        onRefresh={() => loadAll(false)}
                    />
                )}
                {activeTab === 'agents' && (
                    <AgentsTab
                        project={project}
                        canWrite={canWrite}
                        onRefresh={() => loadAll(false)}
                    />
                )}
                {activeTab === 'settings' && (
                    <SettingsTab
                        project={project}
                        canWrite={canWrite}
                        onRefresh={() => loadAll(false)}
                        onDeleted={() => navigate('/projects')}
                    />
                )}
            </div>

            {showStartConversation && (
                <StartConversationModal
                    project={project}
                    onClose={() => setShowStartConversation(false)}
                />
            )}
        </div>
    );
}
