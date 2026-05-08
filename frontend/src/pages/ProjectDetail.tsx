import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
    IconArchive, IconArchiveOff, IconFile, IconFolders,
    IconPin, IconPlus, IconTrash, IconUsers, IconMessageCircle,
    IconSettings, IconLayoutDashboard, IconClock, IconClockPause,
    IconCircleDashed, IconCircleDot, IconCircleCheck, IconAlertTriangle,
    IconLink, IconPencil, IconPlayerPlay,
} from '@tabler/icons-react';
import MarkdownRenderer from '../components/MarkdownRenderer';
import Toast from '../components/Toast';
import FileBrowser from '../components/FileBrowser';
import type { FileBrowserApi, ContextAction } from '../components/FileBrowser';
import { useToast } from '../hooks/useToast';
import { projectApi, agentApi, fetchJson } from '../services/api';
import { useAuthStore } from '../stores';
import { parseBrief, serializeBrief } from '../utils/briefMarkdown';
import type { ParsedBrief, BriefSection } from '../utils/briefMarkdown';
import type { Agent, Project, ProjectAgentMember, ProjectAgentSummary, ProjectChatSession, ProjectScheduledTask, ProjectScheduledTaskFrequency, ProjectTask, ProjectTaskDetail, ProjectTaskStatus } from '../types';


const TABS = ['overview', 'chats', 'tasks', 'files', 'settings'] as const;
type TabId = typeof TABS[number];


// ── Avatars (GitHub-style stacked circles) ──────────────────────────────

function AgentAvatar({
    agent,
    size = 22,
    borderColor = 'var(--bg-primary)',
}: {
    agent: { name: string; avatar_url?: string | null };
    size?: number;
    borderColor?: string;
}) {
    const initial = (agent.name || '?').charAt(0).toUpperCase();
    return (
        <div
            title={agent.name}
            style={{
                width: size,
                height: size,
                borderRadius: '50%',
                border: `2px solid ${borderColor}`,
                background: agent.avatar_url
                    ? `url(${agent.avatar_url}) center/cover no-repeat`
                    : 'var(--bg-tertiary)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: Math.round(size * 0.42),
                color: 'var(--text-secondary)',
                overflow: 'hidden',
                flexShrink: 0,
                fontWeight: 500,
            }}
        >
            {!agent.avatar_url && initial}
        </div>
    );
}

function AgentAvatarStack({
    agents,
    max = 5,
    size = 22,
}: {
    agents: ProjectAgentSummary[];
    max?: number;
    size?: number;
}) {
    const shown = agents.slice(0, max);
    const rest = Math.max(0, agents.length - max);
    if (agents.length === 0) return null;
    return (
        <div style={{ display: 'inline-flex', alignItems: 'center' }}>
            {shown.map((a, i) => (
                <div key={a.agent_id} style={{ marginLeft: i === 0 ? 0 : -Math.round(size * 0.35) }}>
                    <AgentAvatar agent={{ name: a.name, avatar_url: a.avatar_url }} size={size} />
                </div>
            ))}
            {rest > 0 && (
                <div
                    style={{
                        marginLeft: -Math.round(size * 0.35),
                        width: size,
                        height: size,
                        borderRadius: '50%',
                        border: '2px solid var(--bg-primary)',
                        background: 'var(--bg-tertiary)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: Math.round(size * 0.35),
                        color: 'var(--text-secondary)',
                        fontWeight: 500,
                    }}
                    title={`+${rest} more`}
                >
                    +{rest}
                </div>
            )}
        </div>
    );
}


function useHashTab(defaultTab: TabId = 'overview'): [TabId, (t: TabId) => void] {
    const getTab = (): TabId => {
        const h = window.location.hash.replace('#', '');
        return (TABS as readonly string[]).includes(h) ? (h as TabId) : defaultTab;
    };
    const [tab, setTabState] = useState<TabId>(getTab);
    useEffect(() => {
        const onHash = () => setTabState(getTab());
        window.addEventListener('hashchange', onHash);
        return () => window.removeEventListener('hashchange', onHash);
    }, []);
    const setTab = (t: TabId) => {
        window.location.hash = t;
        setTabState(t);
    };
    return [tab, setTab];
}


// ── Overview ────────────────────────────────────────────────────────────

// ── Scheduled tasks (on Overview) ────────────────────────────────────────

const FREQUENCY_OPTIONS: ProjectScheduledTaskFrequency[] = ['hourly', 'daily', 'weekdays', 'weekly'];
const HOUR_OPTIONS: number[] = Array.from({ length: 24 }, (_, i) => i);

function pad2(n: number): string {
    return String(n).padStart(2, '0');
}

function freqLabel(f: ProjectScheduledTaskFrequency, hour: number, t: any): string {
    const time = `${pad2(hour)}:00`;
    if (f === 'hourly') return t('projects.scheduled.freq.hourly', 'Every hour');
    if (f === 'daily') return t('projects.scheduled.freq.dailyAt', 'Every day · {{time}}', { time });
    if (f === 'weekdays') return t('projects.scheduled.freq.weekdaysAt', 'Weekdays · {{time}}', { time });
    if (f === 'weekly') return t('projects.scheduled.freq.weeklyAt', 'Every Monday · {{time}}', { time });
    return '';
}

function formatNextFire(iso: string | null | undefined, t: any): string {
    if (!iso) return '';
    try {
        const dt = new Date(iso);
        const now = new Date();
        const diffMs = dt.getTime() - now.getTime();
        const absMin = Math.round(Math.abs(diffMs) / 60000);
        if (diffMs < 0) return t('projects.scheduled.nextDue', 'due now');
        if (absMin < 60) return t('projects.scheduled.nextInMin', 'in {{n}}m', { n: absMin });
        const absH = Math.round(absMin / 60);
        if (absH < 24) return t('projects.scheduled.nextInHour', 'in {{n}}h', { n: absH });
        const absD = Math.round(absH / 24);
        return t('projects.scheduled.nextInDay', 'in {{n}}d', { n: absD });
    } catch {
        return '';
    }
}

function ScheduledTaskCreateModal({
    project, agents, onClose,
}: {
    project: Project;
    agents: ProjectAgentMember[];
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const queryClient = useQueryClient();
    const [agentId, setAgentId] = useState(agents[0]?.agent_id || '');
    const [name, setName] = useState('');
    const [prompt, setPrompt] = useState('');
    const [frequency, setFrequency] = useState<ProjectScheduledTaskFrequency>('daily');
    const [hour, setHour] = useState<number>(9);
    const [error, setError] = useState('');

    const submit = useMutation({
        mutationFn: () => projectApi.createScheduledTask(project.id, {
            agent_id: agentId, name: name.trim(), prompt: prompt.trim(), frequency, hour,
        }),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['project-scheduled', project.id] });
            onClose();
        },
        onError: (e: any) => setError(e?.message || String(e)),
    });

    const canSubmit = agentId && name.trim() && prompt.trim() && !submit.isPending;

    return (
        <div
            onClick={onClose}
            style={{
                position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
            }}
        >
            <form
                onClick={e => e.stopPropagation()}
                onSubmit={e => { e.preventDefault(); if (canSubmit) submit.mutate(); }}
                className="card"
                style={{ width: 520, maxWidth: '95vw', padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <IconClock size={16} stroke={1.5} />
                    <h3 style={{ margin: 0, fontSize: 15 }}>
                        {t('projects.scheduled.newTask', 'New scheduled task')}
                    </h3>
                </div>

                <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {t('projects.scheduled.agent', 'Which agent runs this task')}
                </label>
                <select className="input" value={agentId} onChange={e => setAgentId(e.target.value)}>
                    {agents.map(a => <option key={a.agent_id} value={a.agent_id}>{a.agent_name}</option>)}
                </select>

                <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {t('projects.scheduled.nameLabel', 'Task name')}
                </label>
                <input
                    className="input" value={name} maxLength={100}
                    onChange={e => setName(e.target.value)}
                    placeholder={t('projects.scheduled.namePlaceholder', 'e.g. Daily standup report')}
                />

                <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {t('projects.scheduled.promptLabel', 'What should the agent do?')}
                </label>
                <textarea
                    className="input" value={prompt} maxLength={4000}
                    onChange={e => setPrompt(e.target.value)}
                    style={{ minHeight: 100, resize: 'vertical' }}
                    placeholder={t('projects.scheduled.promptPlaceholder', 'e.g. Read recent project files and post a one-paragraph progress summary to the project chat.')}
                />

                <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {t('projects.scheduled.freqLabel', 'Frequency')}
                </label>
                <div style={{ display: 'flex', gap: 8 }}>
                    <select
                        className="input" value={frequency} style={{ flex: 1 }}
                        onChange={e => setFrequency(e.target.value as ProjectScheduledTaskFrequency)}
                    >
                        {FREQUENCY_OPTIONS.map(f => (
                            <option key={f} value={f}>{freqLabel(f, hour, t)}</option>
                        ))}
                    </select>
                    {frequency !== 'hourly' && (
                        <select
                            className="input" value={hour} style={{ width: 110 }}
                            onChange={e => setHour(Number(e.target.value))}
                            title={t('projects.scheduled.hourLabel', 'Hour of day')}
                        >
                            {HOUR_OPTIONS.map(h => (
                                <option key={h} value={h}>{pad2(h)}:00</option>
                            ))}
                        </select>
                    )}
                </div>

                {error && <div style={{ color: 'var(--error)', fontSize: 12 }}>{error}</div>}

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
                    <button type="button" className="btn" onClick={onClose}>
                        {t('common.cancel', 'Cancel')}
                    </button>
                    <button type="submit" className="btn btn-primary" disabled={!canSubmit}>
                        {submit.isPending ? t('common.creating', 'Creating...') : t('common.create', 'Create')}
                    </button>
                </div>
            </form>
        </div>
    );
}


function ScheduledTaskEditModal({
    project, task, onClose,
}: {
    project: Project;
    task: ProjectScheduledTask;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const queryClient = useQueryClient();
    const [name, setName] = useState(task.name);
    const [prompt, setPrompt] = useState(task.prompt);
    const [frequency, setFrequency] = useState<ProjectScheduledTaskFrequency>(task.frequency);
    const [hour, setHour] = useState<number>(task.hour);
    const [error, setError] = useState('');

    const submit = useMutation({
        mutationFn: () => projectApi.updateScheduledTask(project.id, task.id, {
            name: name.trim(), prompt: prompt.trim(), frequency, hour,
        }),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['project-scheduled', project.id] });
            onClose();
        },
        onError: (e: any) => setError(e?.message || String(e)),
    });

    const canSubmit = name.trim() && prompt.trim() && !submit.isPending;

    return (
        <div
            onClick={onClose}
            style={{
                position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
            }}
        >
            <form
                onClick={e => e.stopPropagation()}
                onSubmit={e => { e.preventDefault(); if (canSubmit) submit.mutate(); }}
                className="card"
                style={{ width: 520, maxWidth: '95vw', padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <IconClock size={16} stroke={1.5} />
                    <h3 style={{ margin: 0, fontSize: 15 }}>
                        {t('projects.scheduled.editTask', 'Edit scheduled task')}
                    </h3>
                </div>

                <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {t('projects.scheduled.agent', 'Which agent runs this task')}
                </label>
                <div className="input" style={{ background: 'var(--bg-tertiary)', color: 'var(--text-tertiary)' }}>
                    @{task.agent_name}
                </div>

                <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {t('projects.scheduled.nameLabel', 'Task name')}
                </label>
                <input
                    className="input" value={name} maxLength={100}
                    onChange={e => setName(e.target.value)}
                />

                <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {t('projects.scheduled.promptLabel', 'What should the agent do?')}
                </label>
                <textarea
                    className="input" value={prompt} maxLength={4000}
                    onChange={e => setPrompt(e.target.value)}
                    style={{ minHeight: 100, resize: 'vertical' }}
                />

                <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {t('projects.scheduled.freqLabel', 'Frequency')}
                </label>
                <div style={{ display: 'flex', gap: 8 }}>
                    <select
                        className="input" value={frequency} style={{ flex: 1 }}
                        onChange={e => setFrequency(e.target.value as ProjectScheduledTaskFrequency)}
                    >
                        {FREQUENCY_OPTIONS.map(f => (
                            <option key={f} value={f}>{freqLabel(f, hour, t)}</option>
                        ))}
                    </select>
                    {frequency !== 'hourly' && (
                        <select
                            className="input" value={hour} style={{ width: 110 }}
                            onChange={e => setHour(Number(e.target.value))}
                            title={t('projects.scheduled.hourLabel', 'Hour of day')}
                        >
                            {HOUR_OPTIONS.map(h => (
                                <option key={h} value={h}>{pad2(h)}:00</option>
                            ))}
                        </select>
                    )}
                </div>

                {error && <div style={{ color: 'var(--error)', fontSize: 12 }}>{error}</div>}

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
                    <button type="button" className="btn" onClick={onClose}>
                        {t('common.cancel', 'Cancel')}
                    </button>
                    <button type="submit" className="btn btn-primary" disabled={!canSubmit}>
                        {submit.isPending ? t('common.saving', 'Saving...') : t('common.save', 'Save')}
                    </button>
                </div>
            </form>
        </div>
    );
}


function ScheduledTasksSection({
    project, agents,
}: {
    project: Project;
    agents: ProjectAgentMember[];
}) {
    const { t } = useTranslation();
    const queryClient = useQueryClient();
    const { toast, showToast } = useToast();
    const [showCreate, setShowCreate] = useState(false);
    const [editing, setEditing] = useState<ProjectScheduledTask | null>(null);

    const { data: tasks = [], isLoading } = useQuery({
        queryKey: ['project-scheduled', project.id],
        queryFn: () => projectApi.listScheduledTasks(project.id),
    });

    const toggleEnabled = useMutation({
        mutationFn: ({ taskId, enabled }: { taskId: string; enabled: boolean }) =>
            projectApi.updateScheduledTask(project.id, taskId, { is_enabled: enabled }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['project-scheduled', project.id] }),
    });

    const deleteTask = useMutation({
        mutationFn: (taskId: string) => projectApi.deleteScheduledTask(project.id, taskId),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['project-scheduled', project.id] }),
    });

    const runNow = useMutation({
        mutationFn: (taskId: string) => projectApi.runScheduledTaskNow(project.id, taskId),
        onSuccess: () => {
            showToast(
                t('projects.scheduled.runQueued', 'Started — result will appear in the Chats tab once the agent finishes.'),
                'success',
            );
            queryClient.invalidateQueries({ queryKey: ['project-chats', project.id] });
        },
        onError: (e: any) => {
            showToast(t('projects.scheduled.runFailed', 'Failed to run: {{error}}', { error: e?.message || String(e) }), 'error');
        },
    });

    const isArchived = !!project.archived_at;
    const canCreate = !isArchived && agents.length > 0;

    return (
        <div className="card" style={{ padding: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <IconClock size={14} stroke={1.5} style={{ color: 'var(--accent-primary)' }} />
                <h3 style={{ margin: 0, fontSize: 14, flex: 1 }}>
                    {t('projects.scheduled.title', '定时任务')}
                </h3>
                <button
                    className="btn"
                    disabled={!canCreate}
                    onClick={() => setShowCreate(true)}
                    title={
                        isArchived
                            ? t('projects.chats.archivedTooltip', 'This project is archived.')
                            : agents.length === 0
                                ? t('projects.scheduled.needAgent', 'Add an agent first.')
                                : undefined
                    }
                    style={{ fontSize: 12, padding: '3px 10px' }}
                >
                    <IconPlus size={12} stroke={2} style={{ verticalAlign: 'middle', marginRight: 3 }} />
                    {t('projects.scheduled.newBtn', '新建')}
                </button>
            </div>

            {isLoading ? (
                <div style={{ color: 'var(--text-tertiary)', fontSize: 12, padding: 8 }}>
                    {t('common.loading', 'Loading...')}
                </div>
            ) : tasks.length === 0 ? (
                <div style={{ color: 'var(--text-tertiary)', fontSize: 12, padding: '12px 4px' }}>
                    {t('projects.scheduled.empty', '还没有定时任务。点「新建」添加——agent 会按时自动在此 project 里跑一次，结果出现在「对话」标签页。')}
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
                    {tasks.map((task: ProjectScheduledTask, i: number) => (
                        <div
                            key={task.id}
                            style={{
                                padding: '10px 2px',
                                borderBottom: i < tasks.length - 1 ? '1px solid var(--border-secondary)' : 'none',
                                display: 'flex', alignItems: 'center', gap: 10,
                                opacity: task.is_enabled ? 1 : 0.55,
                            }}
                        >
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {task.name}
                                </div>
                                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                                    <span>{freqLabel(task.frequency, task.hour, t)}</span>
                                    <span>·</span>
                                    <span>@{task.agent_name}</span>
                                    {task.is_enabled && task.next_fire_at && (
                                        <>
                                            <span>·</span>
                                            <span title={new Date(task.next_fire_at).toLocaleString()}>
                                                {t('projects.scheduled.nextLabel', 'next')} {formatNextFire(task.next_fire_at, t)}
                                            </span>
                                        </>
                                    )}
                                    {task.fire_count > 0 && (
                                        <>
                                            <span>·</span>
                                            <span>{t('projects.scheduled.firedCount', { count: task.fire_count, defaultValue: 'fired {{count}}x' })}</span>
                                        </>
                                    )}
                                    {!task.is_enabled && (
                                        <>
                                            <span>·</span>
                                            <span style={{ color: 'var(--warning)' }}>{t('projects.scheduled.paused', 'paused')}</span>
                                        </>
                                    )}
                                </div>
                            </div>
                            <button
                                className="btn"
                                onClick={() => runNow.mutate(task.id)}
                                disabled={isArchived || !task.is_enabled || runNow.isPending}
                                title={
                                    !task.is_enabled
                                        ? t('projects.scheduled.runDisabledPaused', 'Resume the task to run it')
                                        : t('projects.scheduled.runNow', 'Run now')
                                }
                                style={{ padding: '4px 6px', background: 'none' }}
                            >
                                <IconPlayerPlay size={14} stroke={1.5} />
                            </button>
                            <button
                                className="btn"
                                onClick={() => toggleEnabled.mutate({ taskId: task.id, enabled: !task.is_enabled })}
                                disabled={isArchived}
                                title={task.is_enabled
                                    ? t('projects.scheduled.pause', 'Pause')
                                    : t('projects.scheduled.resume', 'Resume')}
                                style={{ padding: '4px 6px', background: 'none' }}
                            >
                                {task.is_enabled
                                    ? <IconClockPause size={14} stroke={1.5} />
                                    : <IconClock size={14} stroke={1.5} />}
                            </button>
                            <button
                                className="btn"
                                onClick={() => setEditing(task)}
                                disabled={isArchived}
                                title={t('common.edit', 'Edit')}
                                style={{ padding: '4px 6px', background: 'none' }}
                            >
                                <IconPencil size={13} stroke={1.5} />
                            </button>
                            <button
                                className="btn"
                                onClick={() => {
                                    if (confirm(t('projects.scheduled.deleteConfirm', 'Delete task "{{name}}"?', { name: task.name }))) {
                                        deleteTask.mutate(task.id);
                                    }
                                }}
                                disabled={isArchived}
                                title={t('common.delete', 'Delete')}
                                style={{ padding: '4px 6px', background: 'none' }}
                            >
                                <IconTrash size={13} stroke={1.5} />
                            </button>
                        </div>
                    ))}
                </div>
            )}

            {showCreate && (
                <ScheduledTaskCreateModal
                    project={project}
                    agents={agents}
                    onClose={() => setShowCreate(false)}
                />
            )}
            {editing && (
                <ScheduledTaskEditModal
                    project={project}
                    task={editing}
                    onClose={() => setEditing(null)}
                />
            )}
            <Toast toast={toast} />
        </div>
    );
}


function OverviewTab({ project, brief, agents }: { project: Project; brief: string; agents: ProjectAgentMember[] }) {
    const { t } = useTranslation();
    const [editBrief, setEditBrief] = useState(false);
    const isArchived = !!project.archived_at;
    const needsAgents = project.agents.length === 0 && !isArchived;
    // Brief preview: just the first ~120 chars, single-paragraph compact form.
    // Full brief is one click away via the "Open BRIEF" button or the Files tab.
    const briefSnippet = brief
        .replace(/^\s*#[^\n]*\n+/, '')  // drop leading H1 (project name)
        .trim()
        .slice(0, 240);
    const briefSummary = briefSnippet.length === 240 ? briefSnippet + '…' : briefSnippet;

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {needsAgents && (
                <div
                    className="card"
                    style={{
                        padding: '14px 16px',
                        display: 'flex', alignItems: 'center', gap: 12,
                        borderLeft: '3px solid var(--accent-primary)',
                        background: 'var(--bg-elevated)',
                    }}
                >
                    <IconUsers size={18} stroke={1.5} style={{ color: 'var(--accent-primary)' }} />
                    <div style={{ flex: 1 }}>
                        <div style={{ fontSize: 13, fontWeight: 500 }}>
                            {t('projects.onboarding.addAgentTitle', '先加一个员工，项目就能启动了')}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2 }}>
                            {t('projects.onboarding.addAgentBody', 'Project 里还没有任何员工。加一个之后就可以在 Chats tab 里开始对话。')}
                        </div>
                    </div>
                    <button
                        className="btn btn-primary"
                        onClick={() => { window.location.hash = 'settings'; }}
                    >
                        {t('projects.onboarding.goAddAgent', '去添加员工')}
                    </button>
                </div>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', gap: 24 }}>
                {/* Left column — dashboard primary content */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                    {/* 1. Active tasks — what's pending right now */}
                    <TasksOverviewSection project={project} />

                    {/* 2. Recent activity — what's changed lately */}
                    <RecentFilesSection project={project} />

                    {/* 3. BRIEF preview — compact reference, one click to open */}
                    <div className="card" style={{ padding: 16 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                            <IconPin size={13} stroke={1.5} style={{ color: 'var(--accent-primary)' }} />
                            <h3 style={{ margin: 0, fontSize: 13, flex: 1 }}>BRIEF.md</h3>
                            <button
                                className="btn"
                                style={{ fontSize: 11, padding: '3px 10px' }}
                                onClick={() => setEditBrief(true)}
                            >
                                {isArchived
                                    ? t('projects.brief.openBrief', 'Open BRIEF')
                                    : t('projects.brief.openBrief', 'Open BRIEF')}
                            </button>
                        </div>
                        {briefSummary ? (
                            <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
                                {briefSummary}
                            </div>
                        ) : (
                            <div style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>
                                {t('projects.brief.emptyClickToEdit', 'No brief yet. Click to start writing.')}
                            </div>
                        )}
                    </div>
                    {editBrief && <BriefEditor projectId={project.id} onClose={() => setEditBrief(false)} readonly={isArchived} />}
                </div>

                {/* Right sidebar — at-a-glance metadata */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    <div className="card" style={{ padding: 16 }}>
                        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 10 }}>
                            {t('projects.overview.stats', 'Stats')}
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                                {project.agents.length > 0 ? (
                                    <AgentAvatarStack agents={project.agents} size={22} max={5} />
                                ) : (
                                    <IconUsers size={16} stroke={1.5} style={{ color: 'var(--text-tertiary)' }} />
                                )}
                                <span>
                                    {project.agent_count} {t('projects.overview.agents', 'agents')}
                                </span>
                            </div>
                            <div>
                                <IconFile size={13} stroke={1.5} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                                {project.file_count} {t('projects.overview.files', 'files')}
                            </div>
                            <div>
                                <IconMessageCircle size={13} stroke={1.5} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                                {project.session_count} {t('projects.overview.sessions', 'chat sessions')}
                            </div>
                        </div>
                    </div>

                    <div className="card" style={{ padding: 16 }}>
                        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 10 }}>
                            {t('projects.overview.agentsHeading', '参与的员工：')}
                        </div>
                        {project.agents.length === 0 ? (
                            <div style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>
                                {t('projects.agents.none', 'No agents yet.')}
                            </div>
                        ) : (
                            <AgentAvatarStack agents={project.agents} size={28} max={8} />
                        )}
                    </div>

                    <ScheduledTasksSection project={project} agents={agents} />
                </div>
            </div>
        </div>
    );
}


function RecentFilesSection({ project }: { project: Project }) {
    const { t } = useTranslation();
    const { data: files = [] } = useQuery({
        queryKey: ['project-recent-files', project.id],
        queryFn: () => projectApi.listRecentFiles(project.id, 168, 8),
        // Fresh on Overview entry, plus quietly bg-refresh while user lingers.
        staleTime: 30 * 1000,
    });

    return (
        <div className="card" style={{ padding: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <IconClock size={14} stroke={1.75} style={{ color: 'var(--text-secondary)' }} />
                <h3 style={{ margin: 0, fontSize: 13, flex: 1 }}>
                    {t('projects.overview.recent', '最近活动')}
                </h3>
                <button
                    className="btn"
                    style={{ fontSize: 11, padding: '3px 10px' }}
                    onClick={() => { window.location.hash = 'files'; }}
                >
                    {t('projects.overview.allFiles', '全部文件')}
                </button>
            </div>
            {files.length === 0 ? (
                <div style={{ color: 'var(--text-tertiary)', fontSize: 12, padding: '6px 0' }}>
                    {t('projects.overview.noRecent', '过去 7 天没有文件改动')}
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column' }}>
                    {files.map((f, i) => (
                        <div
                            key={f.path}
                            onClick={() => { window.location.hash = 'files'; }}
                            style={{
                                display: 'flex', alignItems: 'center', gap: 8,
                                padding: '8px 2px',
                                borderBottom: i < files.length - 1 ? '1px solid var(--border-secondary)' : 'none',
                                cursor: 'pointer',
                                fontSize: 12,
                            }}
                        >
                            <IconFile size={14} stroke={1.5} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
                            <span style={{
                                fontFamily: 'var(--font-mono)',
                                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                flex: 1,
                            }}>
                                {f.path}
                            </span>
                            <span style={{ fontSize: 11, color: 'var(--text-tertiary)', flexShrink: 0 }}>
                                {f.created_by_type === 'agent'
                                    ? t('projects.files.writtenByAgent', 'agent-written')
                                    : t('projects.files.uploadedByUser', 'uploaded')}
                            </span>
                            <span
                                title={new Date(f.updated_at).toLocaleString()}
                                style={{ fontSize: 11, color: 'var(--text-tertiary)', flexShrink: 0, whiteSpace: 'nowrap' }}
                            >
                                {relativeTime(f.updated_at, t)}
                            </span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}


function TasksOverviewSection({ project }: { project: Project }) {
    const { t } = useTranslation();
    const { data: tasks = [], isLoading } = useQuery({
        queryKey: ['project-tasks-overview', project.id],
        queryFn: () => projectApi.listTasks(project.id),
    });

    // Show top 3 active tasks (todo / doing / blocked)
    const active = tasks.filter(t => t.status !== 'done').slice(0, 3);
    const totalActive = tasks.filter(t => t.status !== 'done').length;

    if (isLoading) return null;

    return (
        <div className="card" style={{ padding: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <IconCircleDashed size={14} stroke={1.5} style={{ color: 'var(--accent-primary)' }} />
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', flex: 1 }}>
                    {t('projects.tasks.activeHeading', 'Active tasks')}
                    {totalActive > 0 && <span style={{ marginLeft: 6, color: 'var(--text-tertiary)' }}>({totalActive})</span>}
                </div>
                <button
                    onClick={() => { window.location.hash = 'tasks'; }}
                    style={{
                        background: 'transparent', border: 'none', padding: 0,
                        color: 'var(--text-secondary)', fontSize: 11, cursor: 'pointer',
                        textDecoration: 'underline',
                    }}
                >
                    {t('projects.tasks.viewAll', 'View all →')}
                </button>
            </div>
            {active.length === 0 ? (
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: '4px 0' }}>
                    {t('projects.tasks.noActive', 'No active tasks. Open the Tasks tab to add some.')}
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
                    {active.map((task, i) => (
                        <div key={task.id}
                             onClick={() => { window.location.hash = 'tasks'; }}
                             style={{
                                padding: '8px 2px',
                                borderBottom: i < active.length - 1 ? '1px solid var(--border-secondary)' : 'none',
                                display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
                             }}>
                            {statusIcon(task.status, 13)}
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontSize: 12, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {task.title}
                                </div>
                                {(task.assigned_agent_name || task.due_date) && (
                                    <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>
                                        {task.assigned_agent_name && <span>@{task.assigned_agent_name}</span>}
                                        {task.assigned_agent_name && task.due_date && <span> · </span>}
                                        {task.due_date && <span>due {new Date(task.due_date).toLocaleDateString()}</span>}
                                    </div>
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}


// ── Chats ───────────────────────────────────────────────────────────────

function ChatsTab({ project, agents }: { project: Project; agents: ProjectAgentMember[] }) {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [selectedAgent, setSelectedAgent] = useState<string>('');
    const [pickAgentWarning, setPickAgentWarning] = useState(false);

    const { data: sessions = [], isLoading } = useQuery({
        queryKey: ['project-sessions', project.id],
        queryFn: () => projectApi.listSessions(project.id),
    });

    const isArchived = !!project.archived_at;
    const hasAgents = agents.length > 0;

    // Clear the "pick an agent" warning as soon as the user picks one
    useEffect(() => {
        if (selectedAgent) setPickAgentWarning(false);
    }, [selectedAgent]);

    const createSession = useMutation({
        mutationFn: async (agentId: string) => {
            return await fetchJson<{ id: string; agent_id: string }>(
                `/agents/${agentId}/sessions`,
                {
                    method: 'POST',
                    body: JSON.stringify({ project_id: project.id }),
                },
            );
        },
        onSuccess: (session: any) => {
            queryClient.invalidateQueries({ queryKey: ['project-sessions', project.id] });
            // Deep-link: chat tab inside the agent's page; passes session as a query hint
            navigate(`/agents/${session.agent_id}?session=${session.id}#chat`);
        },
    });

    const handleNewChat = () => {
        if (!selectedAgent) {
            setPickAgentWarning(true);
            return;
        }
        createSession.mutate(selectedAgent);
    };

    const [renaming, setRenaming] = useState<{ id: string; agentId: string; draft: string } | null>(null);

    const commitRename = async () => {
        if (!renaming) return;
        const { id: sessionId, agentId, draft } = renaming;
        const trimmed = draft.trim();
        const prev = sessions.find(s => s.id === sessionId);
        setRenaming(null);
        if (!trimmed || (prev && prev.title === trimmed)) return;
        try {
            const tkn = localStorage.getItem('token');
            await fetch(`/api/agents/${agentId}/sessions/${sessionId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tkn}` },
                body: JSON.stringify({ title: trimmed }),
            });
        } finally {
            queryClient.invalidateQueries({ queryKey: ['project-sessions', project.id] });
        }
    };

    return (
        <div className="card" style={{ padding: 16 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: pickAgentWarning ? 6 : 12 }}>
                <select
                    className="input"
                    value={selectedAgent}
                    onChange={e => setSelectedAgent(e.target.value)}
                    disabled={!hasAgents || isArchived}
                    style={{
                        maxWidth: 220,
                        borderColor: pickAgentWarning ? 'var(--error)' : undefined,
                    }}
                >
                    <option value="">
                        {hasAgents
                            ? t('projects.chats.pickAgent', 'Select an agent...')
                            : t('projects.chats.noAgents', 'No agents assigned')}
                    </option>
                    {agents.map(a => (
                        <option key={a.agent_id} value={a.agent_id}>{a.agent_name}</option>
                    ))}
                </select>
                <button
                    className="btn btn-primary"
                    disabled={isArchived || !hasAgents || createSession.isPending}
                    onClick={handleNewChat}
                    title={
                        isArchived
                            ? t('projects.chats.archivedTooltip', 'This project is archived.')
                            : !hasAgents
                                ? t('projects.chats.addAgentFirst', 'Add an agent in Settings first.')
                                : undefined
                    }
                >
                    <IconPlus size={14} stroke={2} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                    {t('projects.chats.newChat', 'New Chat')}
                </button>
            </div>
            {pickAgentWarning && (
                <div style={{ color: 'var(--error)', fontSize: 12, marginBottom: 12 }}>
                    {t('projects.chats.pickAgentWarning', '请先选择一个数字员工')}
                </div>
            )}

            {isLoading ? (
                <div style={{ color: 'var(--text-tertiary)', padding: 16 }}>
                    {t('common.loading', 'Loading...')}
                </div>
            ) : sessions.length === 0 ? (
                <div style={{ color: 'var(--text-tertiary)', padding: 24, textAlign: 'center' }}>
                    {hasAgents
                        ? t('projects.chats.noSessions', 'No chats yet — start one with an agent above.')
                        : t('projects.chats.addAgentToStart', 'Add an agent to start chatting.')}
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
                    {sessions.map((s: ProjectChatSession, i: number) => (
                        <div
                            key={s.id}
                            onClick={() => {
                                if (renaming?.id === s.id) return; // don't navigate while editing
                                navigate(`/agents/${s.agent_id}?session=${s.id}#chat`);
                            }}
                            style={{
                                padding: '10px 12px', cursor: 'pointer',
                                borderBottom: i < sessions.length - 1 ? '1px solid var(--border-secondary)' : 'none',
                                display: 'flex', alignItems: 'center', gap: 12,
                            }}
                        >
                            <div style={{ flex: 1, overflow: 'hidden' }}>
                                {renaming?.id === s.id ? (
                                    <input
                                        autoFocus
                                        value={renaming.draft}
                                        onChange={e => setRenaming({ ...renaming, draft: e.target.value })}
                                        onClick={e => e.stopPropagation()}
                                        onKeyDown={e => {
                                            if (e.key === 'Enter') { e.preventDefault(); commitRename(); }
                                            else if (e.key === 'Escape') { e.preventDefault(); setRenaming(null); }
                                        }}
                                        onBlur={commitRename}
                                        style={{
                                            width: '100%', fontSize: 13, fontWeight: 500,
                                            padding: '2px 6px',
                                            border: '1px solid var(--accent-primary)',
                                            borderRadius: 3,
                                            background: 'var(--bg-primary)',
                                            color: 'var(--text-primary)',
                                            outline: 'none',
                                        }}
                                    />
                                ) : (
                                    <div style={{ fontWeight: 500, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {s.title}
                                    </div>
                                )}
                                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2 }}>
                                    {s.agent_name}
                                    {s.user_display_name && ` · ${s.user_display_name}`}
                                    {!s.owned_by_me && (
                                        <span style={{ marginLeft: 6, color: 'var(--text-tertiary)' }}>
                                            ({t('projects.chats.readonly', 'read-only')})
                                        </span>
                                    )}
                                </div>
                            </div>
                            {s.owned_by_me && (
                                <button
                                    onClick={e => { e.stopPropagation(); setRenaming({ id: s.id, agentId: s.agent_id, draft: s.title }); }}
                                    title={t('projects.chats.rename', 'Rename')}
                                    style={{
                                        background: 'none', border: 'none', padding: 4, cursor: 'pointer',
                                        color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center',
                                    }}
                                >
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 113 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>
                                </button>
                            )}
                            <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                                {s.message_count} {t('projects.chats.messages', 'msgs')}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}


// ── Files ───────────────────────────────────────────────────────────────

interface BriefHistoryEntry {
    ts: string;
    actor_type: string;
    actor_id: string | null;
    bytes: number;
    filename: string;
}

function formatIsoTs(ts: string): string {
    // ts looks like "20260423T094512.123456Z" — turn into "2026-04-23 09:45:12"
    const m = ts.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/);
    if (!m) return ts;
    return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`;
}

function BriefHistoryPanel({
    history,
    onPreview,
    onRestore,
}: {
    projectId: string;
    history: BriefHistoryEntry[];
    onPreview: (filename: string) => void;
    onRestore: (filename: string) => void;
}) {
    const { t } = useTranslation();
    return (
        <div
            className="card"
            style={{
                width: 280, padding: 10, display: 'flex', flexDirection: 'column',
                gap: 4, overflowY: 'auto', flexShrink: 0,
            }}
        >
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', padding: '2px 4px 6px' }}>
                {t('projects.brief.historyTitle', 'Past versions')}
            </div>
            {history.length === 0 ? (
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: '12px 4px' }}>
                    {t('projects.brief.historyEmpty', 'No earlier versions yet. Saves will appear here.')}
                </div>
            ) : (
                history.map(h => (
                    <div
                        key={h.filename}
                        style={{
                            padding: '8px 8px',
                            borderBottom: '1px solid var(--border-secondary)',
                            fontSize: 11,
                            display: 'flex', flexDirection: 'column', gap: 4,
                        }}
                    >
                        <div style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                            {formatIsoTs(h.ts)}
                        </div>
                        <div style={{ color: 'var(--text-tertiary)' }}>
                            {h.actor_type === 'agent'
                                ? t('projects.brief.byAgent', 'by agent')
                                : t('projects.brief.byUser', 'by user')}
                            {' · '}
                            {Math.max(1, Math.round(h.bytes / 1024))} KB
                        </div>
                        <div style={{ display: 'flex', gap: 4, marginTop: 2 }}>
                            <button
                                className="btn"
                                style={{ fontSize: 11, padding: '2px 6px' }}
                                onClick={() => onPreview(h.filename)}
                            >
                                {t('projects.brief.load', 'Load')}
                            </button>
                            <button
                                className="btn"
                                style={{ fontSize: 11, padding: '2px 6px' }}
                                onClick={() => onRestore(h.filename)}
                            >
                                {t('projects.brief.restore', 'Restore')}
                            </button>
                        </div>
                    </div>
                ))
            )}
        </div>
    );
}


type BriefSnapshot = {
    parsed: ParsedBrief | null;
    advancedContent: string;
    viewMode: 'form' | 'advanced';
};

function BriefEditor({ projectId, onClose, readonly = false }: { projectId: string; onClose: () => void; readonly?: boolean }) {
    const { t } = useTranslation();
    const queryClient = useQueryClient();
    const { data, isLoading } = useQuery({
        queryKey: ['project-brief', projectId],
        queryFn: () => projectApi.getBrief(projectId),
    });
    const [parsed, _setParsedRaw] = useState<ParsedBrief | null>(null);
    const [advancedContent, _setAdvancedContentRaw] = useState<string>('');
    const [dirty, setDirty] = useState(false);
    const [showHistory, setShowHistory] = useState(false);
    const [viewMode, _setViewModeRaw] = useState<'form' | 'advanced'>('form');
    const [advancedSubMode, setAdvancedSubMode] = useState<'write' | 'preview'>('write');
    const [unparsableNotice, setUnparsableNotice] = useState(false);

    const pastRef = useRef<BriefSnapshot[]>([]);
    const futureRef = useRef<BriefSnapshot[]>([]);
    const pendingSnapRef = useRef<BriefSnapshot | null>(null);
    const checkpointTimerRef = useRef<number | null>(null);

    const captureSnapshot = (): BriefSnapshot => ({ parsed, advancedContent, viewMode });

    const flushPending = () => {
        if (checkpointTimerRef.current !== null) {
            window.clearTimeout(checkpointTimerRef.current);
            checkpointTimerRef.current = null;
        }
        if (pendingSnapRef.current !== null) {
            pastRef.current.push(pendingSnapRef.current);
            if (pastRef.current.length > 50) pastRef.current.shift();
            pendingSnapRef.current = null;
            futureRef.current = [];
        }
    };

    const scheduleCheckpoint = () => {
        if (pendingSnapRef.current === null) {
            pendingSnapRef.current = captureSnapshot();
        }
        if (checkpointTimerRef.current !== null) {
            window.clearTimeout(checkpointTimerRef.current);
        }
        checkpointTimerRef.current = window.setTimeout(() => {
            if (pendingSnapRef.current !== null) {
                pastRef.current.push(pendingSnapRef.current);
                if (pastRef.current.length > 50) pastRef.current.shift();
                pendingSnapRef.current = null;
                futureRef.current = [];
            }
            checkpointTimerRef.current = null;
        }, 500);
    };

    const pushImmediate = () => {
        flushPending();
        pastRef.current.push(captureSnapshot());
        if (pastRef.current.length > 50) pastRef.current.shift();
        futureRef.current = [];
    };

    const resetHistory = () => {
        if (checkpointTimerRef.current !== null) {
            window.clearTimeout(checkpointTimerRef.current);
            checkpointTimerRef.current = null;
        }
        pendingSnapRef.current = null;
        pastRef.current = [];
        futureRef.current = [];
    };

    const setParsed = (next: ParsedBrief | null) => {
        scheduleCheckpoint();
        _setParsedRaw(next);
        setDirty(true);
    };
    const setAdvancedContent = (next: string) => {
        scheduleCheckpoint();
        _setAdvancedContentRaw(next);
        setDirty(true);
    };
    const setViewMode = (next: 'form' | 'advanced') => {
        pushImmediate();
        _setViewModeRaw(next);
        setDirty(true);
    };

    const undo = () => {
        flushPending();
        if (pastRef.current.length === 0) return;
        const prev = pastRef.current.pop()!;
        futureRef.current.push(captureSnapshot());
        _setParsedRaw(prev.parsed);
        _setAdvancedContentRaw(prev.advancedContent);
        _setViewModeRaw(prev.viewMode);
        setDirty(true);
    };

    const redo = () => {
        flushPending();
        if (futureRef.current.length === 0) return;
        const next = futureRef.current.pop()!;
        pastRef.current.push(captureSnapshot());
        _setParsedRaw(next.parsed);
        _setAdvancedContentRaw(next.advancedContent);
        _setViewModeRaw(next.viewMode);
        setDirty(true);
    };

    const { data: history = [] } = useQuery({
        queryKey: ['project-brief-history', projectId],
        queryFn: () => projectApi.listBriefHistory(projectId),
        enabled: showHistory,
    });

    useEffect(() => {
        if (data?.content !== undefined) {
            const p = parseBrief(data.content);
            _setAdvancedContentRaw(data.content);
            if (p.unparsable) {
                _setParsedRaw(null);
                _setViewModeRaw('advanced');
                setUnparsableNotice(true);
            } else {
                _setParsedRaw(p);
                _setViewModeRaw('form');
                setUnparsableNotice(false);
            }
            if (readonly) setAdvancedSubMode('preview');
            setDirty(false);
            resetHistory();
        }
    }, [data?.content, readonly]);

    const computeContent = useCallback((): string => {
        if (viewMode === 'form' && parsed) {
            return serializeBrief(parsed.title, parsed.sections);
        }
        return advancedContent;
    }, [viewMode, parsed, advancedContent]);

    const save = useMutation({
        mutationFn: () => projectApi.setBrief(projectId, computeContent()),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['project-brief', projectId] });
            queryClient.invalidateQueries({ queryKey: ['project-brief-history', projectId] });
            setDirty(false);
        },
    });

    const switchToAdvanced = () => {
        pushImmediate();
        if (parsed) {
            _setAdvancedContentRaw(serializeBrief(parsed.title, parsed.sections));
        }
        _setViewModeRaw('advanced');
        setDirty(true);
    };

    const switchToForm = () => {
        const p = parseBrief(advancedContent);
        if (p.unparsable) {
            setUnparsableNotice(true);
            alert(t('projects.brief.cannotSwitchToForm', '当前内容不符合表单结构，请保留 ## 目标 / ## 背景 / ## 限制条件 三个章节后再切换。'));
            return;
        }
        pushImmediate();
        _setParsedRaw(p);
        setUnparsableNotice(false);
        _setViewModeRaw('form');
        setDirty(true);
    };

    const loadSnapshot = async (filename: string) => {
        try {
            const snap = await projectApi.getBriefSnapshot(projectId, filename);
            const p = parseBrief(snap.content);
            _setAdvancedContentRaw(snap.content);
            if (p.unparsable) {
                _setParsedRaw(null);
                _setViewModeRaw('advanced');
                setUnparsableNotice(true);
            } else {
                _setParsedRaw(p);
                _setViewModeRaw('form');
                setUnparsableNotice(false);
            }
            setDirty(true);
            resetHistory();
        } catch (e: any) {
            alert(e?.message || String(e));
        }
    };

    const restoreSnapshot = async (filename: string) => {
        if (!confirm(t('projects.brief.restoreConfirm', 'Restore this snapshot as the current BRIEF? The current content will itself be snapshotted first.'))) return;
        try {
            const snap = await projectApi.restoreBriefSnapshot(projectId, filename);
            const p = parseBrief(snap.content);
            _setAdvancedContentRaw(snap.content);
            if (p.unparsable) {
                _setParsedRaw(null);
                _setViewModeRaw('advanced');
                setUnparsableNotice(true);
            } else {
                _setParsedRaw(p);
                _setViewModeRaw('form');
                setUnparsableNotice(false);
            }
            setDirty(false);
            resetHistory();
            queryClient.invalidateQueries({ queryKey: ['project-brief', projectId] });
            queryClient.invalidateQueries({ queryKey: ['project-brief-history', projectId] });
        } catch (e: any) {
            alert(e?.message || String(e));
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
        if (readonly) return;
        const isMod = e.ctrlKey || e.metaKey;
        if (!isMod) return;
        const key = e.key.toLowerCase();
        if (key === 'z' && !e.shiftKey) {
            e.preventDefault();
            e.stopPropagation();
            undo();
        } else if ((key === 'z' && e.shiftKey) || key === 'y') {
            e.preventDefault();
            e.stopPropagation();
            redo();
        }
    };

    return (
        <div
            onClick={onClose}
            onKeyDownCapture={handleKeyDown}
            style={{
                position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
            }}
        >
            <div
                onClick={e => e.stopPropagation()}
                className="card"
                style={{ width: 820, maxWidth: '95vw', height: '85vh', padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <IconPin size={14} stroke={1.5} style={{ color: 'var(--accent-primary)' }} />
                    <h3 style={{ margin: 0, fontSize: 15 }}>{t('projects.brief.heading', '项目简报')}</h3>
                    <div style={{ flex: 1 }} />
                    <button
                        className="btn"
                        onClick={() => setShowHistory(h => !h)}
                        title={t('projects.brief.history', 'History')}
                    >
                        {showHistory ? t('projects.brief.hideHistory', 'Hide history') : t('projects.brief.history', 'History')}
                    </button>
                    <button className="btn" onClick={onClose}>{t('common.close', 'Close')}</button>
                    {!readonly && (
                        <button
                            className="btn btn-primary"
                            onClick={() => save.mutate()}
                            disabled={!dirty || save.isPending}
                        >
                            {save.isPending ? t('common.saving', 'Saving...') : t('common.save', 'Save')}
                        </button>
                    )}
                </div>

                {unparsableNotice && (
                    <div
                        style={{
                            fontSize: 12, color: 'var(--text-secondary)',
                            background: 'var(--bg-elevated)', padding: '8px 12px',
                            borderRadius: 6, borderLeft: '3px solid var(--accent-primary)',
                        }}
                    >
                        {t('projects.brief.unparsableNotice', '当前 BRIEF 包含自定义结构，已切到高级编辑模式。如需回到表单视图，请保留 ## 目标 / ## 背景 / ## 限制条件 三个章节。')}
                    </div>
                )}

                <div style={{ flex: 1, display: 'flex', gap: 10, minHeight: 0 }}>
                    {isLoading ? (
                        <div style={{ color: 'var(--text-tertiary)' }}>{t('common.loading', 'Loading...')}</div>
                    ) : viewMode === 'form' && parsed ? (
                        <BriefFormView
                            parsed={parsed}
                            readonly={readonly}
                            onChange={(updated) => { setParsed(updated); setDirty(true); }}
                        />
                    ) : (
                        <BriefAdvancedView
                            content={advancedContent}
                            onChange={(c) => { setAdvancedContent(c); setDirty(true); }}
                            mode={advancedSubMode}
                            onModeChange={setAdvancedSubMode}
                            readonly={readonly}
                        />
                    )}
                    {showHistory && (
                        <BriefHistoryPanel
                            projectId={projectId}
                            history={history}
                            onPreview={loadSnapshot}
                            onRestore={restoreSnapshot}
                        />
                    )}
                </div>

                {!readonly && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: 'var(--text-tertiary)' }}>
                        <div style={{ flex: 1 }}>
                            {viewMode === 'form'
                                ? t('projects.brief.formFooterHint', '所有改动会在保存后写入项目内每个对话的上下文。')
                                : t('projects.brief.advancedFooterHint', '高级模式：直接编辑 markdown。给会写代码块、表格等格式的人用。')}
                        </div>
                        {viewMode === 'form' ? (
                            <button
                                onClick={switchToAdvanced}
                                style={{
                                    background: 'transparent', border: 'none', padding: 0,
                                    color: 'var(--text-secondary)', fontSize: 11, cursor: 'pointer',
                                    textDecoration: 'underline',
                                }}
                            >
                                {t('projects.brief.switchToAdvanced', '高级模式 ›')}
                            </button>
                        ) : (
                            <button
                                onClick={switchToForm}
                                style={{
                                    background: 'transparent', border: 'none', padding: 0,
                                    color: 'var(--text-secondary)', fontSize: 11, cursor: 'pointer',
                                    textDecoration: 'underline',
                                }}
                            >
                                {t('projects.brief.switchToForm', '‹ 回到表单视图')}
                            </button>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}


function BriefFormView({ parsed, readonly, onChange }: {
    parsed: ParsedBrief;
    readonly: boolean;
    onChange: (updated: ParsedBrief) => void;
}) {
    const { t } = useTranslation();

    const updateTitle = (title: string) => onChange({ ...parsed, title });
    const updateSection = (id: string, patch: Partial<BriefSection>) =>
        onChange({
            ...parsed,
            sections: parsed.sections.map(s => s.id === id ? { ...s, ...patch } : s),
        });
    const removeSection = (id: string) => {
        if (!confirm(t('projects.brief.removeSectionConfirm', '删除此章节？'))) return;
        onChange({ ...parsed, sections: parsed.sections.filter(s => s.id !== id) });
    };
    const addSection = () => {
        const newId = `custom-${Date.now()}`;
        onChange({
            ...parsed,
            sections: [...parsed.sections, { id: newId, heading: '', body: '', isDefault: false }],
        });
    };

    const labelFor = (s: BriefSection): string => {
        if (s.isDefault && s.defaultId) {
            return t(`projects.brief.section.${s.defaultId}.label`, s.heading);
        }
        return s.heading;
    };
    const hintFor = (s: BriefSection): string | null => {
        if (s.isDefault && s.defaultId) {
            return t(`projects.brief.section.${s.defaultId}.hint`, '');
        }
        return null;
    };
    const placeholderFor = (s: BriefSection): string => {
        if (s.isDefault && s.defaultId) {
            return t(`projects.brief.section.${s.defaultId}.placeholder`, '');
        }
        return t('projects.brief.bodyPlaceholder', '在这里写你的内容…');
    };

    return (
        <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 18, paddingRight: 4 }}>
            <div>
                <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>
                    {t('projects.brief.form.titleLabel', '项目名称')}
                </label>
                <input
                    className="input"
                    value={parsed.title}
                    onChange={e => updateTitle(e.target.value)}
                    placeholder={t('projects.brief.form.titlePlaceholder', '给项目起个名字')}
                    readOnly={readonly}
                    style={{ width: '100%', fontSize: 15, fontWeight: 500 }}
                />
            </div>

            {parsed.sections.map(s => (
                <div key={s.id} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                        {s.isDefault ? (
                            <label style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)' }}>
                                {labelFor(s)}
                            </label>
                        ) : (
                            <input
                                value={s.heading}
                                onChange={e => updateSection(s.id, { heading: e.target.value })}
                                placeholder={t('projects.brief.form.customHeadingPlaceholder', '章节标题（如：人员安排）')}
                                readOnly={readonly}
                                style={{
                                    flex: 1,
                                    background: 'transparent',
                                    border: 'none',
                                    borderBottom: '1px solid var(--border-primary)',
                                    fontSize: 14, fontWeight: 500,
                                    color: 'var(--text-primary)',
                                    padding: '2px 0',
                                    outline: 'none',
                                }}
                            />
                        )}
                        {!s.isDefault && !readonly && (
                            <button
                                onClick={() => removeSection(s.id)}
                                title={t('projects.brief.removeSection', '删除章节')}
                                style={{
                                    background: 'transparent', border: 'none', padding: 4,
                                    color: 'var(--text-tertiary)', cursor: 'pointer',
                                    display: 'inline-flex',
                                }}
                            >
                                <IconTrash size={14} stroke={1.5} />
                            </button>
                        )}
                    </div>
                    {hintFor(s) && (
                        <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                            {hintFor(s)}
                        </div>
                    )}
                    <textarea
                        className="input"
                        value={s.body}
                        onChange={e => updateSection(s.id, { body: e.target.value })}
                        placeholder={placeholderFor(s)}
                        readOnly={readonly}
                        rows={4}
                        style={{ resize: 'vertical', fontSize: 13, lineHeight: 1.6, padding: 12 }}
                    />
                </div>
            ))}

            {!readonly && (
                <button
                    onClick={addSection}
                    className="btn"
                    style={{ alignSelf: 'flex-start', display: 'inline-flex', alignItems: 'center', gap: 6 }}
                >
                    <IconPlus size={14} stroke={1.5} />
                    {t('projects.brief.form.addSection', '添加章节')}
                </button>
            )}
        </div>
    );
}


function BriefAdvancedView({ content, onChange, mode, onModeChange, readonly }: {
    content: string;
    onChange: (c: string) => void;
    mode: 'write' | 'preview';
    onModeChange: (m: 'write' | 'preview') => void;
    readonly: boolean;
}) {
    const { t } = useTranslation();

    const segBtn = (active: boolean): React.CSSProperties => ({
        padding: '4px 12px', fontSize: 12, lineHeight: 1.4,
        border: '1px solid var(--border-primary)',
        background: active ? 'var(--bg-elevated)' : 'transparent',
        color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
        cursor: readonly && active ? 'default' : 'pointer',
        fontWeight: active ? 500 : 400,
    });

    return (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0 }}>
            {!readonly && (
                <div style={{ display: 'inline-flex', alignSelf: 'flex-start', borderRadius: 6, overflow: 'hidden' }}>
                    <button
                        onClick={() => onModeChange('write')}
                        style={{ ...segBtn(mode === 'write'), borderRadius: '6px 0 0 6px', borderRight: 'none' }}
                    >
                        {t('projects.brief.tabWrite', 'Write')}
                    </button>
                    <button
                        onClick={() => onModeChange('preview')}
                        style={{ ...segBtn(mode === 'preview'), borderRadius: '0 6px 6px 0' }}
                    >
                        {t('projects.brief.tabPreview', 'Preview')}
                    </button>
                </div>
            )}
            {mode === 'write' && !readonly ? (
                <textarea
                    className="input"
                    value={content}
                    onChange={e => onChange(e.target.value)}
                    spellCheck={false}
                    style={{
                        flex: 1,
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                        fontSize: 14, lineHeight: 1.7, padding: 16, resize: 'none', tabSize: 2,
                    }}
                />
            ) : (
                <div
                    className="card"
                    style={{
                        flex: 1, overflowY: 'auto', padding: '20px 28px',
                        background: 'var(--bg-primary)', fontSize: 14,
                    }}
                >
                    {content.trim() ? (
                        <MarkdownRenderer content={content} />
                    ) : (
                        <div style={{ color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
                            {t('projects.brief.previewEmpty', '还没有内容。')}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}


function FilesTab({ project }: { project: Project }) {
    const { t } = useTranslation();
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const [editBrief, setEditBrief] = useState(false);
    const { toast, showToast } = useToast();

    // Conflict dialog: a Promise-based handshake. The adapter awaits a Promise
    // that this dialog resolves when the user clicks Cancel/Keep both/Replace.
    const [conflictPrompt, setConflictPrompt] = useState<{
        file: File;
        existingName: string;
        suggestedAltName: string;
    } | null>(null);
    const conflictResolverRef = useRef<((mode: 'replace' | 'keep_both' | null) => void) | null>(null);

    const isArchived = !!project.archived_at;
    const refreshProjectMeta = () => {
        queryClient.invalidateQueries({ queryKey: ['project', project.id] });
    };

    // Project agents — used for the "Send to agent" right-click menu.
    const { data: projectAgents = [] } = useQuery({
        queryKey: ['project-agents', project.id],
        queryFn: () => projectApi.listAgents(project.id),
        enabled: !!project.id,
    });

    // Mirror FileBrowser's FileItem shape onto the richer ProjectFile rows.
    const adapter: FileBrowserApi = useMemo(() => ({
        list: async (path: string) => {
            const rows = await projectApi.listFiles(project.id, path);
            return rows.map(r => ({
                name: r.filename,
                path: r.path,
                is_dir: r.is_dir,
                size: r.is_dir ? undefined : r.size_bytes,
                mime_type: r.mime_type,
                created_by_type: r.created_by_type,
                modified: r.updated_at,
                linked_task_count: r.linked_task_count ?? 0,
                linked_task_titles: r.linked_task_titles ?? [],
            }));
        },
        read: (path: string) => projectApi.readFileContent(project.id, path),
        write: async (path: string, content: string) => {
            await projectApi.writeFileContent(project.id, path, content);
            refreshProjectMeta();
        },
        delete: async (path: string) => {
            // FileBrowser shows its own "Deleted" toast on success; adapter only updates server cache.
            await projectApi.deleteFileByPath(project.id, path);
            refreshProjectMeta();
        },
        upload: async (file: File, currentDir: string, onProgress?: (pct: number) => void) => {
            const tryUpload = async (mode?: 'replace' | 'keep_both') => {
                // FileBrowser shows its own "Upload successful" toast on success.
                const { promise } = projectApi.uploadFile(project.id, file, currentDir, mode, onProgress);
                await promise;
                refreshProjectMeta();
            };

            try {
                await tryUpload();
            } catch (e: any) {
                const detail = e?.detail;
                if (e?.status === 409 && detail?.detail === 'filename_conflict') {
                    const choice = await new Promise<'replace' | 'keep_both' | null>(resolve => {
                        conflictResolverRef.current = resolve;
                        setConflictPrompt({
                            file,
                            existingName: detail.existing?.filename || file.name,
                            suggestedAltName: detail.suggested_alt_name || file.name,
                        });
                    });
                    if (!choice) return; // user cancelled
                    await tryUpload(choice);
                    return;
                }
                showToast(t('projects.files.uploadFailed', 'Upload failed: {{error}}', { error: e?.message || String(e) }), 'error');
                throw e;
            }
        },
        downloadUrl: (path: string) => projectApi.downloadFileUrlByPath(project.id, path),
        move: async (src: string, dst: string) => {
            await projectApi.moveFile(project.id, src, dst);
            refreshProjectMeta();
        },
        cleanupEmpty: () => projectApi.cleanupEmptyFolders(project.id),
    }), [project.id, t]); // eslint-disable-line react-hooks/exhaustive-deps

    // Right-click "Send to agent" — creates a new chat session bound to this project,
    // then jumps directly into the chat panel (#chat hash forces AgentDetail's tab there).
    const sendToAgent = async (agentId: string, agentName: string, filePath: string) => {
        try {
            const session = await agentApi.createSession(agentId, {
                project_id: project.id,
                title: `处理 ${filePath.split('/').pop() || filePath}`,
            });
            const prefill = encodeURIComponent('请处理 ./' + filePath);
            navigate(`/agents/${agentId}?session=${session.id}&prefill=${prefill}#chat`);
        } catch (e: any) {
            showToast(`Failed to start chat with ${agentName}: ${e?.message || e}`, 'error');
        }
    };

    const buildContextActions = (file: { path: string; is_dir: boolean }): ContextAction[] => {
        if (file.is_dir || isArchived || projectAgents.length === 0) return [];
        return projectAgents.map((a: ProjectAgentMember) => ({
            label: t('projects.files.sendToAgent', 'Send to {{name}}', { name: a.agent_name }),
            onClick: () => sendToAgent(a.agent_id, a.agent_name, file.path),
            icon: <AgentAvatar agent={{ name: a.agent_name, avatar_url: a.avatar_url }} size={18} borderColor="transparent" />,
        }));
    };

    const resolveConflict = (choice: 'replace' | 'keep_both' | null) => {
        const resolve = conflictResolverRef.current;
        conflictResolverRef.current = null;
        setConflictPrompt(null);
        resolve?.(choice);
    };

    return (
        <>
            {/* BRIEF.md pinned row — kept above FileBrowser; backend filters BRIEF.md from the file list. */}
            <div
                className="card"
                onClick={() => setEditBrief(true)}
                style={{
                    padding: 14, display: 'flex', alignItems: 'center', gap: 12,
                    cursor: 'pointer', marginBottom: 12,
                    border: '1px dashed var(--border-primary)',
                }}
            >
                <IconPin size={16} stroke={1.5} style={{ color: 'var(--accent-primary)' }} />
                <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 500, fontSize: 13 }}>BRIEF.md</div>
                    <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                        {t('projects.brief.pinned', 'Project brief — injected into every chat inside this project.')}
                    </div>
                </div>
                <button className="btn" onClick={(e) => { e.stopPropagation(); setEditBrief(true); }}>
                    {isArchived ? t('common.view', 'View') : t('projects.brief.edit', 'Edit')}
                </button>
            </div>

            <FileBrowser
                api={adapter}
                rootPath=""
                readOnly={isArchived}
                features={{
                    upload: !isArchived,
                    newFile: !isArchived,
                    newFolder: !isArchived,
                    edit: !isArchived,
                    delete: !isArchived,
                    directoryNavigation: true,
                    multiSelect: !isArchived,
                    sort: true,
                }}
                renderFileMeta={(f) => {
                    if (f.is_dir) return null;
                    const linkedCount = (f as any).linked_task_count as number | undefined;
                    const linkedTitles = (f as any).linked_task_titles as string[] | undefined;
                    const mtime = f.modified;
                    return (
                        <>
                            {linkedCount && linkedCount > 0 ? (
                                <span
                                    title={linkedTitles?.join('\n') || ''}
                                    onClick={(e) => { e.stopPropagation(); navigate(`/projects/${project.id}#tasks`); }}
                                    style={{
                                        display: 'inline-flex', alignItems: 'center', gap: 3,
                                        fontSize: 11, padding: '1px 6px', borderRadius: 10,
                                        background: 'var(--accent-primary-soft, var(--bg-elevated))',
                                        color: 'var(--accent-primary)',
                                        cursor: 'pointer',
                                    }}
                                >
                                    <IconLink size={11} stroke={1.75} />
                                    {linkedCount}
                                </span>
                            ) : null}
                            {mtime && (
                                <span
                                    title={new Date(mtime).toLocaleString()}
                                    style={{ fontSize: 11, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}
                                >
                                    {relativeTime(mtime, t)}
                                </span>
                            )}
                            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                                {f.created_by_type === 'agent'
                                    ? t('projects.files.writtenByAgent', 'agent-written')
                                    : t('projects.files.uploadedByUser', 'uploaded')}
                            </span>
                        </>
                    );
                }}
                canDeleteFile={(f) =>
                    !isArchived && (f.is_dir || f.created_by_type === 'user')
                }
                contextActions={(f) => buildContextActions({ path: f.path, is_dir: f.is_dir })}
                onRefresh={refreshProjectMeta}
            />

            {conflictPrompt && (
                <div
                    onClick={() => resolveConflict(null)}
                    style={{
                        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
                    }}
                >
                    <div
                        onClick={e => e.stopPropagation()}
                        className="card"
                        style={{ width: 440, maxWidth: '95vw', padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}
                    >
                        <h3 style={{ margin: 0, fontSize: 15 }}>
                            {t('projects.files.conflict.title', 'File already exists')}
                        </h3>
                        <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                            {t('projects.files.conflict.body', '"{{name}}" already exists in this project. What would you like to do?', { name: conflictPrompt.existingName })}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                            {t('projects.files.conflict.keepBothHint', 'Keep both will save as:')} <code>{conflictPrompt.suggestedAltName}</code>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                            <button className="btn" onClick={() => resolveConflict(null)}>
                                {t('projects.files.conflict.cancel', 'Cancel')}
                            </button>
                            <button className="btn" onClick={() => resolveConflict('keep_both')}>
                                {t('projects.files.conflict.keepBoth', 'Keep both')}
                            </button>
                            <button className="btn btn-primary" onClick={() => resolveConflict('replace')}>
                                {t('projects.files.conflict.replace', 'Replace')}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {editBrief && <BriefEditor projectId={project.id} onClose={() => setEditBrief(false)} readonly={isArchived} />}

            <Toast toast={toast} />
        </>
    );
}


// ── Tasks (Phase 3 deliverables) ────────────────────────────────────────

function statusIcon(status: ProjectTaskStatus, size = 14) {
    const stroke = 1.75;
    switch (status) {
        case 'todo':    return <IconCircleDashed size={size} stroke={stroke} style={{ color: 'var(--text-tertiary)' }} />;
        case 'doing':   return <IconCircleDot size={size} stroke={stroke} style={{ color: 'var(--info)' }} />;
        case 'done':    return <IconCircleCheck size={size} stroke={stroke} style={{ color: 'var(--success)' }} />;
        case 'blocked': return <IconAlertTriangle size={size} stroke={stroke} style={{ color: 'var(--warning)' }} />;
    }
}

function statusColor(status: ProjectTaskStatus): string {
    switch (status) {
        case 'todo':    return 'var(--text-tertiary)';
        case 'doing':   return 'var(--info)';
        case 'done':    return 'var(--success)';
        case 'blocked': return 'var(--warning)';
    }
}

function relativeTime(iso: string | null | undefined, t: any): string {
    if (!iso) return '';
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 0) return new Date(iso).toLocaleDateString();
    const sec = Math.round(ms / 1000);
    if (sec < 60) return t('common.justNow', 'just now');
    if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)} h ago`;
    if (sec < 86400 * 7) return `${Math.floor(sec / 86400)} d ago`;
    return new Date(iso).toLocaleDateString();
}


type TaskFilter = 'all' | ProjectTaskStatus;

function TasksTab({ project, agents }: { project: Project; agents: ProjectAgentMember[] }) {
    const { t } = useTranslation();
    const queryClient = useQueryClient();
    const [filter, setFilter] = useState<TaskFilter>('all');
    const [showCreate, setShowCreate] = useState(false);
    const [detailTaskId, setDetailTaskId] = useState<string | null>(null);
    const { toast, showToast } = useToast();

    const { data: tasks = [], isLoading } = useQuery({
        queryKey: ['project-tasks', project.id, filter],
        queryFn: () => projectApi.listTasks(project.id, filter === 'all' ? undefined : { status: filter }),
    });

    const isArchived = !!project.archived_at;

    const filterChips: { key: TaskFilter; label: string }[] = [
        { key: 'all', label: t('projects.tasks.filter.all', 'All') },
        { key: 'todo', label: t('projects.tasks.status.todo', 'To do') },
        { key: 'doing', label: t('projects.tasks.status.doing', 'Doing') },
        { key: 'blocked', label: t('projects.tasks.status.blocked', 'Blocked') },
        { key: 'done', label: t('projects.tasks.status.done', 'Done') },
    ];

    return (
        <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {/* Header: filter chips on left, create button on right */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <div style={{ display: 'flex', gap: 4 }}>
                        {filterChips.map(({ key, label }) => (
                            <button
                                key={key}
                                onClick={() => setFilter(key)}
                                style={{
                                    fontSize: 12, padding: '4px 10px', borderRadius: 4,
                                    border: '1px solid ' + (filter === key ? 'var(--accent-primary)' : 'var(--border-subtle)'),
                                    background: filter === key ? 'var(--accent-subtle)' : 'transparent',
                                    color: filter === key ? 'var(--accent-text)' : 'var(--text-secondary)',
                                    cursor: 'pointer',
                                }}
                            >
                                {label}
                            </button>
                        ))}
                    </div>
                    <div style={{ flex: 1 }} />
                    {!isArchived && (
                        <button className="btn" onClick={() => setShowCreate(true)}>
                            <IconPlus size={12} stroke={2} style={{ marginRight: 3 }} />
                            {t('projects.tasks.new', 'New task')}
                        </button>
                    )}
                </div>

                {/* List */}
                {isLoading ? (
                    <div style={{ color: 'var(--text-tertiary)', padding: 24, textAlign: 'center' }}>
                        {t('common.loading', 'Loading...')}
                    </div>
                ) : tasks.length === 0 ? (
                    <div className="card" style={{ padding: 32, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                        <IconCircleDashed size={28} stroke={1.5} style={{ marginBottom: 8, color: 'var(--text-tertiary)' }} />
                        <div style={{ fontSize: 13, marginBottom: 4 }}>
                            {filter === 'all'
                                ? t('projects.tasks.empty', 'No tasks yet.')
                                : t('projects.tasks.emptyFiltered', 'No tasks match this filter.')}
                        </div>
                        {filter === 'all' && !isArchived && (
                            <div style={{ fontSize: 11 }}>
                                {t('projects.tasks.emptyHint', 'Click + New task to add the first deliverable.')}
                            </div>
                        )}
                    </div>
                ) : (
                    <div className="card" style={{ padding: 16 }}>
                        {tasks.map((task, i) => (
                            <div
                                key={task.id}
                                onClick={() => setDetailTaskId(task.id)}
                                style={{
                                    padding: '10px 2px',
                                    borderBottom: i < tasks.length - 1 ? '1px solid var(--border-secondary)' : 'none',
                                    display: 'flex', alignItems: 'center', gap: 10,
                                    opacity: task.status === 'done' ? 0.55 : 1,
                                    cursor: 'pointer',
                                }}
                            >
                                {statusIcon(task.status, 14)}
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{
                                        fontSize: 13, fontWeight: 500,
                                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                        textDecoration: task.status === 'done' ? 'line-through' : 'none',
                                    }}>
                                        {task.title}
                                    </div>
                                    <div style={{
                                        fontSize: 11, color: 'var(--text-tertiary)',
                                        display: 'flex', alignItems: 'center', gap: 6, marginTop: 2,
                                    }}>
                                        {task.assigned_agent_name && <span>@{task.assigned_agent_name}</span>}
                                        {task.assigned_user_display_name && <span>@{task.assigned_user_display_name}</span>}
                                        {task.due_date && <span>· {t('projects.tasks.dueShort', 'due')} {new Date(task.due_date).toLocaleDateString()}</span>}
                                        {task.linked_file_count > 0 && (
                                            <span>· <IconLink size={10} stroke={1.75} style={{ verticalAlign: 'middle' }} /> {task.linked_file_count}</span>
                                        )}
                                        {task.created_by_type === 'agent' && (
                                            <span style={{ color: 'var(--accent-text)' }}>· {t('projects.tasks.byAgent', 'agent-created')}</span>
                                        )}
                                        {task.status === 'done' && task.completed_at && (
                                            <span>· {t('projects.tasks.completedAt', 'done')} {relativeTime(task.completed_at, t)}</span>
                                        )}
                                    </div>
                                </div>
                                <span style={{
                                    fontSize: 10, padding: '1px 6px', borderRadius: 4,
                                    color: statusColor(task.status),
                                    border: '1px solid ' + statusColor(task.status),
                                    flexShrink: 0,
                                }}>
                                    {t(`projects.tasks.status.${task.status}`, task.status)}
                                </span>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {showCreate && (
                <TaskCreateModal
                    project={project}
                    agents={agents}
                    onClose={() => setShowCreate(false)}
                    onCreated={(t) => {
                        showToast(`${t.title}`, 'success');
                        queryClient.invalidateQueries({ queryKey: ['project-tasks', project.id] });
                    }}
                />
            )}
            {detailTaskId && (
                <TaskDetailModal
                    project={project}
                    agents={agents}
                    taskId={detailTaskId}
                    onClose={() => setDetailTaskId(null)}
                    onChanged={() => {
                        queryClient.invalidateQueries({ queryKey: ['project-tasks', project.id] });
                    }}
                    onDeleted={(title) => {
                        showToast(t('projects.tasks.deleted', 'Deleted {{name}}', { name: title }), 'success');
                        queryClient.invalidateQueries({ queryKey: ['project-tasks', project.id] });
                        setDetailTaskId(null);
                    }}
                />
            )}

            <Toast toast={toast} />
        </>
    );
}


function TaskCreateModal({
    project, agents, onClose, onCreated,
}: {
    project: Project;
    agents: ProjectAgentMember[];
    onClose: () => void;
    onCreated: (task: ProjectTaskDetail) => void;
}) {
    const { t } = useTranslation();
    const [title, setTitle] = useState('');
    const [description, setDescription] = useState('');
    const [assignedAgentId, setAssignedAgentId] = useState<string>('');
    const [dueDate, setDueDate] = useState('');
    const create = useMutation({
        mutationFn: () => projectApi.createTask(project.id, {
            title: title.trim(),
            description,
            assigned_agent_id: assignedAgentId || null,
            due_date: dueDate ? new Date(dueDate + 'T09:00:00').toISOString() : null,
        }),
        onSuccess: (task) => {
            onCreated(task);
            onClose();
        },
    });

    return (
        <div
            onClick={onClose}
            style={{
                position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
            }}
        >
            <div
                onClick={e => e.stopPropagation()}
                className="card"
                style={{ width: 520, maxWidth: '95vw', padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}
            >
                <h3 style={{ margin: 0, fontSize: 15 }}>{t('projects.tasks.newTitle', 'New task')}</h3>
                <div>
                    <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t('projects.tasks.fields.title', 'Title')}</label>
                    <input
                        autoFocus
                        className="input"
                        value={title}
                        onChange={e => setTitle(e.target.value)}
                        placeholder={t('projects.tasks.fields.titlePlaceholder', 'e.g. 10 Instagram posters')}
                        style={{ width: '100%', marginTop: 4 }}
                    />
                </div>
                <div>
                    <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t('projects.tasks.fields.description', 'Description (optional)')}</label>
                    <textarea
                        className="input"
                        value={description}
                        onChange={e => setDescription(e.target.value)}
                        rows={4}
                        placeholder={t('projects.tasks.fields.descriptionPlaceholder', 'Markdown supported.')}
                        style={{ width: '100%', marginTop: 4, resize: 'vertical', fontSize: 12, lineHeight: 1.6 }}
                    />
                </div>
                <div style={{ display: 'flex', gap: 12 }}>
                    <div style={{ flex: 1 }}>
                        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t('projects.tasks.fields.assignee', 'Assign to')}</label>
                        <select
                            className="input"
                            value={assignedAgentId}
                            onChange={e => setAssignedAgentId(e.target.value)}
                            style={{ width: '100%', marginTop: 4 }}
                        >
                            <option value="">{t('projects.tasks.fields.unassigned', 'Unassigned')}</option>
                            {agents.map(a => (
                                <option key={a.agent_id} value={a.agent_id}>{a.agent_name}</option>
                            ))}
                        </select>
                    </div>
                    <div style={{ flex: 1 }}>
                        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t('projects.tasks.fields.due', 'Due date')}</label>
                        <input
                            type="date"
                            className="input"
                            value={dueDate}
                            onChange={e => setDueDate(e.target.value)}
                            style={{ width: '100%', marginTop: 4 }}
                        />
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 6 }}>
                    <button className="btn" onClick={onClose}>{t('common.cancel', 'Cancel')}</button>
                    <button
                        className="btn btn-primary"
                        onClick={() => create.mutate()}
                        disabled={!title.trim() || create.isPending}
                    >
                        {create.isPending ? t('common.saving', 'Saving...') : t('projects.tasks.create', 'Create')}
                    </button>
                </div>
            </div>
        </div>
    );
}


function TaskDetailModal({
    project, agents, taskId, onClose, onChanged, onDeleted,
}: {
    project: Project;
    agents: ProjectAgentMember[];
    taskId: string;
    onClose: () => void;
    onChanged: () => void;
    onDeleted: (title: string) => void;
}) {
    const { t } = useTranslation();
    const queryClient = useQueryClient();
    const { toast, showToast } = useToast();
    const { data: task, isLoading } = useQuery({
        queryKey: ['project-task', project.id, taskId],
        queryFn: () => projectApi.getTask(project.id, taskId),
    });
    const { data: files = [] } = useQuery({
        queryKey: ['project-files', project.id],
        queryFn: () => projectApi.listFiles(project.id),
    });

    const [title, setTitle] = useState('');
    const [description, setDescription] = useState('');
    const [status, setStatus] = useState<ProjectTaskStatus>('todo');
    const [assignedAgentId, setAssignedAgentId] = useState('');
    const [dueDate, setDueDate] = useState('');
    const [showLinkPicker, setShowLinkPicker] = useState(false);
    const [dirty, setDirty] = useState(false);

    const isArchived = !!project.archived_at;

    useEffect(() => {
        if (task) {
            setTitle(task.title);
            setDescription(task.description);
            setStatus(task.status);
            setAssignedAgentId(task.assigned_agent_id || '');
            setDueDate(task.due_date ? task.due_date.slice(0, 10) : '');
            setDirty(false);
        }
    }, [task?.id]);

    const save = useMutation({
        mutationFn: () => {
            if (!task) throw new Error('no task');
            const body: Record<string, any> = {};
            if (title !== task.title) body.title = title;
            if (description !== task.description) body.description = description;
            if (status !== task.status) body.status = status;
            const newAgentId = assignedAgentId || null;
            const oldAgentId = task.assigned_agent_id || null;
            if (newAgentId !== oldAgentId) {
                if (newAgentId === null) body.clear_assignee = true;
                else body.assigned_agent_id = newAgentId;
            }
            const newDue = dueDate ? new Date(dueDate + 'T09:00:00').toISOString() : null;
            const oldDue = task.due_date || null;
            if (newDue !== oldDue) {
                if (newDue === null) body.clear_due_date = true;
                else body.due_date = newDue;
            }
            return projectApi.updateTask(project.id, taskId, body);
        },
        onSuccess: (saved: any) => {
            queryClient.invalidateQueries({ queryKey: ['project-task', project.id, taskId] });
            onChanged();
            setDirty(false);
            showToast(
                t('projects.tasks.saved', 'Saved {{title}}', { title: saved?.title || title }),
                'success',
            );
            // Close after a short delay so the toast is visible.
            window.setTimeout(() => onClose(), 600);
        },
        onError: (err: any) => {
            showToast(t('projects.tasks.saveFailed', 'Save failed: {{error}}', { error: err?.message || String(err) }), 'error');
        },
    });

    // The save button is *always* clickable (only blocked while a save is in-flight).
    // If no field changed (dirty=false), we still give the user a confirmation
    // toast — the link/unlink/status/etc. operations write straight to the
    // backend on their own, so "no changes here" really means "everything's
    // already saved". This avoids the silent-no-op trap where the user clicks
    // a disabled button and thinks the page is broken.
    const handleSaveClick = () => {
        if (save.isPending) return;
        if (!dirty) {
            showToast(t('projects.tasks.alreadySaved', 'No pending changes — already saved'), 'info');
            window.setTimeout(() => onClose(), 600);
            return;
        }
        save.mutate();
    };

    const linkFile = useMutation({
        mutationFn: (fileId: string) => projectApi.linkFileToTask(project.id, taskId, fileId),
        onSuccess: (_data, fileId) => {
            queryClient.invalidateQueries({ queryKey: ['project-task', project.id, taskId] });
            queryClient.invalidateQueries({ queryKey: ['project-files', project.id] });
            onChanged();
            setShowLinkPicker(false);
            const f = files.find(x => x.id === fileId);
            showToast(t('projects.tasks.fileLinked', 'Linked {{name}}', { name: f?.filename || 'file' }), 'success');
        },
        onError: (err: any) => {
            showToast(t('projects.tasks.fileLinkFailed', 'Link failed: {{error}}', { error: err?.message || String(err) }), 'error');
        },
    });

    const unlinkFile = useMutation({
        mutationFn: (fileId: string) => projectApi.unlinkFileFromTask(project.id, taskId, fileId),
        onSuccess: (_data, fileId) => {
            queryClient.invalidateQueries({ queryKey: ['project-task', project.id, taskId] });
            queryClient.invalidateQueries({ queryKey: ['project-files', project.id] });
            onChanged();
            const f = (task?.linked_files || []).find(x => x.file_id === fileId);
            showToast(t('projects.tasks.fileUnlinked', 'Unlinked {{name}}', { name: f?.filename || 'file' }), 'success');
        },
        onError: (err: any) => {
            showToast(t('projects.tasks.fileUnlinkFailed', 'Unlink failed: {{error}}', { error: err?.message || String(err) }), 'error');
        },
    });

    const deleteTask = useMutation({
        mutationFn: () => projectApi.deleteTask(project.id, taskId),
        onSuccess: () => {
            if (task) onDeleted(task.title);
        },
    });

    const linkedIds = new Set((task?.linked_files || []).map(f => f.file_id));
    const unlinkedFiles = files.filter(f => !linkedIds.has(f.id));

    return (
        <div
            onClick={onClose}
            style={{
                position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
            }}
        >
            <div
                onClick={e => e.stopPropagation()}
                className="card"
                style={{
                    width: 720, maxWidth: '95vw', maxHeight: '90vh',
                    padding: 20, display: 'flex', flexDirection: 'column', gap: 12, overflow: 'auto',
                }}
            >
                {isLoading || !task ? (
                    <div style={{ color: 'var(--text-tertiary)', padding: 24, textAlign: 'center' }}>
                        {t('common.loading', 'Loading...')}
                    </div>
                ) : (
                    <>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            {statusIcon(status, 16)}
                            <input
                                className="input"
                                value={title}
                                onChange={e => { setTitle(e.target.value); setDirty(true); }}
                                disabled={isArchived}
                                style={{ flex: 1, fontSize: 15, fontWeight: 500 }}
                            />
                            <button className="btn" onClick={onClose}>{t('common.close', 'Close')}</button>
                        </div>

                        {/* Status switcher */}
                        <div style={{ display: 'flex', gap: 6 }}>
                            {(['todo', 'doing', 'done', 'blocked'] as ProjectTaskStatus[]).map(s => (
                                <button
                                    key={s}
                                    onClick={() => { setStatus(s); setDirty(true); }}
                                    disabled={isArchived}
                                    style={{
                                        fontSize: 12, padding: '6px 10px', borderRadius: 4,
                                        border: '1px solid ' + (status === s ? statusColor(s) : 'var(--border-subtle)'),
                                        background: status === s ? 'var(--accent-subtle)' : 'transparent',
                                        color: status === s ? statusColor(s) : 'var(--text-secondary)',
                                        cursor: isArchived ? 'not-allowed' : 'pointer',
                                        display: 'inline-flex', alignItems: 'center', gap: 4,
                                    }}
                                >
                                    {statusIcon(s, 12)}
                                    {t(`projects.tasks.status.${s}`, s)}
                                </button>
                            ))}
                        </div>

                        {/* Description */}
                        <div>
                            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                                {t('projects.tasks.fields.description', 'Description')}
                            </label>
                            <textarea
                                className="input"
                                value={description}
                                onChange={e => { setDescription(e.target.value); setDirty(true); }}
                                disabled={isArchived}
                                rows={5}
                                style={{ width: '100%', marginTop: 4, resize: 'vertical', fontSize: 12, lineHeight: 1.6 }}
                            />
                            {description.trim() && (
                                <details style={{ marginTop: 4, fontSize: 11, color: 'var(--text-tertiary)' }}>
                                    <summary style={{ cursor: 'pointer' }}>{t('projects.tasks.preview', 'Preview')}</summary>
                                    <div style={{ padding: 8, background: 'var(--bg-elevated)', borderRadius: 4, marginTop: 4 }}>
                                        <MarkdownRenderer content={description} />
                                    </div>
                                </details>
                            )}
                        </div>

                        {/* Assignee + due */}
                        <div style={{ display: 'flex', gap: 12 }}>
                            <div style={{ flex: 1 }}>
                                <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                                    {t('projects.tasks.fields.assignee', 'Assign to')}
                                </label>
                                <select
                                    className="input"
                                    value={assignedAgentId}
                                    onChange={e => { setAssignedAgentId(e.target.value); setDirty(true); }}
                                    disabled={isArchived}
                                    style={{ width: '100%', marginTop: 4 }}
                                >
                                    <option value="">{t('projects.tasks.fields.unassigned', 'Unassigned')}</option>
                                    {agents.map(a => (
                                        <option key={a.agent_id} value={a.agent_id}>{a.agent_name}</option>
                                    ))}
                                </select>
                            </div>
                            <div style={{ flex: 1 }}>
                                <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                                    {t('projects.tasks.fields.due', 'Due date')}
                                </label>
                                <input
                                    type="date"
                                    className="input"
                                    value={dueDate}
                                    onChange={e => { setDueDate(e.target.value); setDirty(true); }}
                                    disabled={isArchived}
                                    style={{ width: '100%', marginTop: 4 }}
                                />
                            </div>
                        </div>

                        {/* Linked files */}
                        <div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                                    {t('projects.tasks.linkedFiles', 'Linked files')}
                                </label>
                                <div style={{ flex: 1 }} />
                                {!isArchived && (
                                    <button
                                        className="btn"
                                        onClick={() => setShowLinkPicker(v => !v)}
                                        style={{ background: 'none', padding: '4px 6px' }}
                                    >
                                        <IconPlus size={12} stroke={2} />
                                    </button>
                                )}
                            </div>
                            {(task.linked_files || []).length === 0 ? (
                                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', padding: '6px 0' }}>
                                    {t('projects.tasks.noLinkedFiles', 'No files linked yet.')}
                                </div>
                            ) : (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 6 }}>
                                    {task.linked_files.map(f => (
                                        <div key={f.file_id} style={{
                                            display: 'flex', alignItems: 'center', gap: 8,
                                            fontSize: 12, padding: '4px 8px',
                                            background: 'var(--bg-elevated)', borderRadius: 4,
                                        }}>
                                            <IconLink size={12} stroke={1.75} style={{ color: 'var(--text-tertiary)' }} />
                                            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                {f.filename}
                                            </span>
                                            {!isArchived && (
                                                <button
                                                    className="btn"
                                                    onClick={() => unlinkFile.mutate(f.file_id)}
                                                    style={{ background: 'none', padding: '2px 4px' }}
                                                >
                                                    <IconTrash size={11} stroke={1.75} />
                                                </button>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            )}
                            {showLinkPicker && (
                                <div style={{
                                    marginTop: 8, padding: 8,
                                    border: '1px solid var(--border-subtle)', borderRadius: 4,
                                    maxHeight: 180, overflow: 'auto',
                                }}>
                                    {unlinkedFiles.length === 0 ? (
                                        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', padding: 8, textAlign: 'center' }}>
                                            {t('projects.tasks.noFilesToLink', 'No files in this project yet. Upload some in the Files tab first.')}
                                        </div>
                                    ) : unlinkedFiles.map(f => (
                                        <div
                                            key={f.id}
                                            onClick={() => linkFile.mutate(f.id)}
                                            style={{
                                                fontSize: 12, padding: '6px 8px', borderRadius: 4,
                                                cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6,
                                            }}
                                            onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; }}
                                            onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                                        >
                                            <IconLink size={11} stroke={1.75} style={{ color: 'var(--text-tertiary)' }} />
                                            {f.filename}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* Footer */}
                        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                            {!isArchived && (
                                <button
                                    className="btn"
                                    onClick={() => {
                                        if (confirm(t('projects.tasks.deleteConfirm', 'Delete this task?'))) {
                                            deleteTask.mutate();
                                        }
                                    }}
                                    style={{ color: 'var(--error)' }}
                                >
                                    <IconTrash size={12} stroke={1.75} style={{ marginRight: 4 }} />
                                    {t('common.delete', 'Delete')}
                                </button>
                            )}
                            <div style={{ flex: 1 }} />
                            {!isArchived && (
                                <button
                                    className="btn btn-primary"
                                    onClick={handleSaveClick}
                                    disabled={save.isPending}
                                >
                                    {save.isPending ? t('common.saving', 'Saving...') : t('common.save', 'Save')}
                                </button>
                            )}
                        </div>

                        {task.created_by_type === 'agent' && (
                            <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginTop: 4 }}>
                                {t('projects.tasks.byAgentNotice', 'This task was created by an agent.')}
                            </div>
                        )}
                    </>
                )}
            </div>
            <Toast toast={toast} />
        </div>
    );
}


// ── Settings ────────────────────────────────────────────────────────────

function SettingsTab({ project, agents, canArchive }: { project: Project; agents: ProjectAgentMember[]; canArchive: boolean }) {
    const { t } = useTranslation();
    const queryClient = useQueryClient();
    const [name, setName] = useState(project.name);
    const [description, setDescription] = useState(project.description);
    const [visibility, setVisibility] = useState(project.chat_visibility);
    const [addingAgent, setAddingAgent] = useState('');

    const { data: availableAgents = [] } = useQuery({
        queryKey: ['all-agents'],
        queryFn: () => agentApi.list(),
    });

    const isArchived = !!project.archived_at;
    const dirty =
        name !== project.name ||
        description !== project.description ||
        visibility !== project.chat_visibility;

    const availableToAdd = useMemo(
        () => (availableAgents as Agent[]).filter(a => !agents.some(m => m.agent_id === a.id)),
        [availableAgents, agents],
    );

    const saveSettings = useMutation({
        mutationFn: () => projectApi.update(project.id, { name, description, chat_visibility: visibility }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['project', project.id] }),
    });

    const addAgent = useMutation({
        mutationFn: (agent_id: string) => projectApi.addAgent(project.id, agent_id),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['project-agents', project.id] });
            queryClient.invalidateQueries({ queryKey: ['project', project.id] });
            setAddingAgent('');
        },
    });

    const removeAgent = useMutation({
        mutationFn: (agent_id: string) => projectApi.removeAgent(project.id, agent_id),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['project-agents', project.id] });
            queryClient.invalidateQueries({ queryKey: ['project', project.id] });
        },
    });

    const archive = useMutation({
        mutationFn: () => (isArchived ? projectApi.unarchive(project.id) : projectApi.archive(project.id)),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['project', project.id] }),
    });

    const doRemoveAgent = (member: ProjectAgentMember) => {
        if (!confirm(t('projects.agents.removeConfirm', 'Remove {{name}} from this project? History will be preserved.', { name: member.agent_name }))) return;
        removeAgent.mutate(member.agent_id);
    };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 640 }}>
            {/* Basic info */}
            <div className="card" style={{ padding: 16 }}>
                <h3 style={{ margin: '0 0 12px', fontSize: 14 }}>{t('projects.settings.basics', 'Basics')}</h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                        {t('projects.create.nameLabel', 'Name')}
                    </label>
                    <input
                        className="input" value={name} onChange={e => setName(e.target.value)}
                        maxLength={200} disabled={isArchived}
                    />
                    <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                        {t('projects.create.descriptionLabel', 'Short description')}
                    </label>
                    <textarea
                        className="input" value={description}
                        onChange={e => setDescription(e.target.value.slice(0, 500))}
                        style={{ resize: 'vertical', minHeight: 70 }} disabled={isArchived}
                    />
                    <label style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
                        {t('projects.settings.chatVisibility', 'Chat visibility')}
                    </label>
                    <select
                        className="input"
                        value={visibility}
                        onChange={e => setVisibility(e.target.value as 'shared' | 'private')}
                        disabled={isArchived}
                    >
                        <option value="shared">{t('projects.settings.visibility.shared', 'Shared — other members see sessions read-only')}</option>
                        <option value="private">{t('projects.settings.visibility.private', 'Private — only session owner sees their sessions')}</option>
                    </select>
                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
                        <button
                            className="btn btn-primary"
                            disabled={!dirty || saveSettings.isPending || isArchived}
                            onClick={() => saveSettings.mutate()}
                        >
                            {saveSettings.isPending ? t('common.saving', 'Saving...') : t('common.save', 'Save')}
                        </button>
                    </div>
                </div>
            </div>

            {/* Agents */}
            <div className="card" style={{ padding: 16 }}>
                <h3 style={{ margin: '0 0 12px', fontSize: 14 }}>{t('projects.settings.agents', 'Agents')}</h3>
                {agents.length === 0 ? (
                    <div style={{ color: 'var(--text-tertiary)', fontSize: 12, marginBottom: 12 }}>
                        {t('projects.agents.none', 'No agents yet.')}
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12 }}>
                        {agents.map(m => (
                            <div
                                key={m.agent_id}
                                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0' }}
                            >
                                <div style={{ flex: 1, fontSize: 13 }}>{m.agent_name}</div>
                                {!isArchived && (
                                    <button
                                        className="btn"
                                        onClick={() => doRemoveAgent(m)}
                                        title={t('projects.agents.remove', 'Remove')}
                                    >
                                        <IconTrash size={13} stroke={1.5} />
                                    </button>
                                )}
                            </div>
                        ))}
                    </div>
                )}
                {!isArchived && (
                    <div style={{ display: 'flex', gap: 8 }}>
                        <select
                            className="input"
                            value={addingAgent}
                            onChange={e => setAddingAgent(e.target.value)}
                            style={{ flex: 1 }}
                        >
                            <option value="">
                                {availableToAdd.length > 0
                                    ? t('projects.agents.addHint', 'Pick an agent to add')
                                    : t('projects.agents.allAdded', 'All available agents are in the project')}
                            </option>
                            {availableToAdd.map(a => (
                                <option key={a.id} value={a.id}>{a.name}</option>
                            ))}
                        </select>
                        <button
                            className="btn btn-primary"
                            disabled={!addingAgent || addAgent.isPending}
                            onClick={() => addingAgent && addAgent.mutate(addingAgent)}
                        >
                            <IconPlus size={13} stroke={2} style={{ verticalAlign: 'middle' }} />
                        </button>
                    </div>
                )}
            </div>

            {/* Danger zone */}
            {canArchive && (
                <div className="card" style={{ padding: 16 }}>
                    <h3 style={{ margin: '0 0 12px', fontSize: 14 }}>{t('projects.settings.lifecycle', 'Lifecycle')}</h3>
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 10 }}>
                        {isArchived
                            ? t('projects.settings.archiveHelp.archived', 'This project is archived. Unarchive to resume work.')
                            : t('projects.settings.archiveHelp.active', 'Archive makes the project read-only. History and files are preserved.')}
                    </div>
                    <button
                        className="btn"
                        onClick={() => {
                            if (!isArchived && !confirm(t('projects.settings.archiveConfirm', 'Archive this project? It will become read-only.'))) return;
                            archive.mutate();
                        }}
                        disabled={archive.isPending}
                    >
                        {isArchived
                            ? <><IconArchiveOff size={13} stroke={1.5} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                                {t('projects.settings.unarchive', 'Unarchive')}</>
                            : <><IconArchive size={13} stroke={1.5} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                                {t('projects.settings.archive', 'Archive')}</>
                        }
                    </button>
                </div>
            )}
        </div>
    );
}


// ── Page shell ──────────────────────────────────────────────────────────

export default function ProjectDetail() {
    const { id } = useParams<{ id: string }>();
    const { t } = useTranslation();
    const navigate = useNavigate();
    const { user } = useAuthStore();
    const [tab, setTab] = useHashTab('overview');

    const { data: project, isLoading, isError } = useQuery({
        queryKey: ['project', id],
        queryFn: () => projectApi.get(id!),
        enabled: !!id,
    });

    const { data: agents = [] } = useQuery({
        queryKey: ['project-agents', id],
        queryFn: () => projectApi.listAgents(id!),
        enabled: !!id && !!project,
    });

    const { data: briefResp } = useQuery({
        queryKey: ['project-brief', id],
        queryFn: () => projectApi.getBrief(id!),
        enabled: !!id && !!project,
    });

    if (isLoading) {
        return <div style={{ padding: 32, color: 'var(--text-tertiary)' }}>{t('common.loading', 'Loading...')}</div>;
    }
    if (isError || !project) {
        return (
            <div style={{ padding: 32 }}>
                <div style={{ color: 'var(--error)' }}>{t('projects.notFound', 'Project not found or you do not have access.')}</div>
                <button className="btn" style={{ marginTop: 12 }} onClick={() => navigate('/projects')}>
                    {t('common.back', 'Back')}
                </button>
            </div>
        );
    }

    const canArchive = !!user && (
        user.role === 'platform_admin' ||
        user.role === 'org_admin' ||
        user.id === project.created_by
    );
    const isArchived = !!project.archived_at;

    const tabIcons: Record<TabId, React.ReactNode> = {
        overview: <IconLayoutDashboard size={14} stroke={1.5} />,
        chats: <IconMessageCircle size={14} stroke={1.5} />,
        tasks: <IconCircleDashed size={14} stroke={1.5} />,
        files: <IconFile size={14} stroke={1.5} />,
        settings: <IconSettings size={14} stroke={1.5} />,
    };

    const tabLabels: Record<TabId, string> = {
        overview: t('projects.detail.tab.overview', 'Overview'),
        chats: t('projects.detail.tab.chats', 'Chats'),
        tasks: t('projects.detail.tab.tasks', 'Tasks'),
        files: t('projects.detail.tab.files', 'Files'),
        settings: t('projects.detail.tab.settings', 'Settings'),
    };

    return (
        <div style={{ padding: 24, maxWidth: 1200, margin: '0 auto' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
                <IconFolders size={20} stroke={1.5} style={{ color: 'var(--accent-primary)' }} />
                <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>{project.name}</h1>
                {isArchived && (
                    <span
                        className="badge"
                        style={{ fontSize: 11, padding: '2px 8px', background: 'var(--bg-tertiary)', color: 'var(--text-tertiary)' }}
                    >
                        {t('projects.status.archived', 'Archived')}
                    </span>
                )}
                <div style={{ marginLeft: 'auto' }}>
                    <button className="btn" onClick={() => navigate('/projects')}>
                        {t('common.back', 'Back')}
                    </button>
                </div>
            </div>
            {project.description && (
                <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 16 }}>
                    {project.description}
                </div>
            )}

            {/* Tabs */}
            <div
                style={{
                    display: 'flex', gap: 4, borderBottom: '1px solid var(--border-secondary)',
                    marginBottom: 20,
                }}
            >
                {TABS.map(id => (
                    <button
                        key={id}
                        onClick={() => setTab(id)}
                        className="tab-button"
                        style={{
                            padding: '8px 14px', background: 'none', border: 'none',
                            borderBottom: tab === id ? '2px solid var(--accent-primary)' : '2px solid transparent',
                            color: tab === id ? 'var(--text-primary)' : 'var(--text-secondary)',
                            cursor: 'pointer', fontSize: 13, fontWeight: tab === id ? 500 : 400,
                            display: 'inline-flex', alignItems: 'center', gap: 6,
                        }}
                    >
                        {tabIcons[id]}
                        {tabLabels[id]}
                    </button>
                ))}
            </div>

            {/* Tab content */}
            {tab === 'overview' && <OverviewTab project={project} brief={briefResp?.content || ''} agents={agents} />}
            {tab === 'chats' && <ChatsTab project={project} agents={agents} />}
            {tab === 'tasks' && <TasksTab project={project} agents={agents} />}
            {tab === 'files' && <FilesTab project={project} />}
            {tab === 'settings' && <SettingsTab project={project} agents={agents} canArchive={canArchive} />}
        </div>
    );
}
