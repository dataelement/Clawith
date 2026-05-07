import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { agentApi, taskApi, activityApi, fetchJson, tenantApi } from '../services/api';
import type { Agent, Task } from '../types';

/* ────── Inline SVG Icons (monochrome) ────── */

const Icons = {
    users: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="6" cy="5" r="2.5" />
            <path d="M1.5 14v-1a3.5 3.5 0 017 0v1" />
            <circle cx="11.5" cy="5.5" r="2" />
            <path d="M14.5 14v-.5a3 3 0 00-3-3" />
        </svg>
    ),
    tasks: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2" y="2" width="12" height="12" rx="2" />
            <path d="M5.5 8l2 2 3.5-3.5" />
        </svg>
    ),
    zap: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M8.5 1.5L3 9h4.5l-.5 5.5L13 7H8.5l.5-5.5z" />
        </svg>
    ),
    clock: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="8" cy="8" r="6" />
            <path d="M8 4.5V8l2.5 1.5" />
        </svg>
    ),
    activity: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M1 8h3l2-5 3 10 2-5h4" />
        </svg>
    ),
    plus: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <path d="M8 3v10M3 8h10" />
        </svg>
    ),
    bot: (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="5" width="12" height="10" rx="2" />
            <circle cx="7" cy="10" r="1" fill="currentColor" stroke="none" />
            <circle cx="11" cy="10" r="1" fill="currentColor" stroke="none" />
            <path d="M9 2v3M6 2h6" />
        </svg>
    ),
};

/* ────── Helpers ────── */

const timeAgo = (dateStr: string | undefined, t: any) => {
    if (!dateStr) return '-';
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return t('dashboard.justNow');
    if (mins < 60) return t('dashboard.minutesAgo', { count: mins });
    const hours = Math.floor(mins / 60);
    if (hours < 24) return t('dashboard.hoursAgo', { count: hours });
    return t('dashboard.daysAgo', { count: Math.floor(hours / 24) });
};

const priorityColor = (p: string) => {
    switch (p) {
        case 'urgent': return 'var(--error)';
        case 'high': return 'var(--warning)';
        case 'medium': return 'var(--accent-primary)';
        default: return 'var(--text-tertiary)';
    }
};

const statusLabel = (s: string, t: any) => {
    switch (s) {
        case 'running': return t('dashboard.status.running');
        case 'idle': return t('dashboard.status.idle');
        case 'stopped': return t('dashboard.status.stopped');
        case 'error': return t('dashboard.status.error');
        case 'creating': return t('dashboard.status.creating');
        case 'disconnected': return t('dashboard.status.disconnected');
        default: return s;
    }
};

const statusColor = (s: string) => {
    switch (s) {
        case 'running': return 'var(--status-running)';
        case 'idle': return 'var(--status-idle)';
        case 'error': return 'var(--status-error)';
        case 'stopped': return 'var(--status-stopped)';
        default: return 'var(--text-tertiary)';
    }
};

const formatTokens = (n: number) => {
    if (!n) return '0';
    if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
    return String(n);
};

/* ────── OKR Summary Card (P3) ────── */

/**
 * OKRSummaryCard — a compact overview widget for the Dashboard.
 * Fetches the latest OKR settings + current period objectives, shows a
 * mini donut chart and KR status breakdown, links to OKR page.
 * Renders nothing when OKR is disabled or loading.
 */
function OKRSummaryCard() {
    const { i18n } = useTranslation();
    const navigate = useNavigate();
    const isChinese = i18n.language?.startsWith('zh');

    // Load settings first
    const { data: settings } = useQuery({
        queryKey: ['okr-settings-dash'],
        queryFn: () => fetchJson<{ enabled: boolean }>('/okr/settings'),
        staleTime: 60000,
    });

    // Load current-period objectives (only when OKR enabled)
    const { data: objectives = [] } = useQuery<any[]>({
        queryKey: ['okr-objectives-dash'],
        queryFn: async () => {
            // Fetch periods first to get the current period
            const periods = await fetchJson<any[]>('/okr/periods');
            const current = periods.find((p: any) => p.is_current) ?? periods[periods.length - 1];
            if (!current) return [];
            return fetchJson<any[]>(`/okr/objectives?period_start=${current.start}&period_end=${current.end}`);
        },
        enabled: !!settings?.enabled,
        staleTime: 60000,
    });

    // Nothing to show if OKR is off or still loading
    if (!settings?.enabled || objectives.length === 0) return null;

    // Flatten all KRs and count statuses
    const allKRs: any[] = objectives.flatMap((o: any) => o.key_results ?? []);
    const counts = { on_track: 0, at_risk: 0, behind: 0, completed: 0 };
    for (const kr of allKRs) {
        if (kr.status in counts) counts[kr.status as keyof typeof counts]++;
    }
    const total = allKRs.length;

    // Donut chart data
    const COLORS = { on_track: '#3f3f46', at_risk: '#71717a', behind: '#a1a1aa', completed: '#18181b' };
    const LABELS_ZH = { on_track: '按计划', at_risk: '有风险', behind: '落后', completed: '已完成' };
    const LABELS_EN = { on_track: 'On Track', at_risk: 'At Risk', behind: 'Behind', completed: 'Completed' };
    const labels = isChinese ? LABELS_ZH : LABELS_EN;

    // Build SVG donut arcs
    const R = 28, CX = 36, CY = 36, STROKE = 10;
    const circumference = 2 * Math.PI * R;
    let offset = 0;
    const arcs: { key: string; color: string; dash: number; gap: number; rotate: number }[] = [];
    const order: (keyof typeof counts)[] = ['on_track', 'at_risk', 'behind', 'completed'];
    for (const key of order) {
        const pct = total > 0 ? counts[key] / total : 0;
        const dash = pct * circumference;
        const gap = circumference - dash;
        arcs.push({ key, color: COLORS[key], dash, gap, rotate: (offset / total) * 360 });
        offset += counts[key];
    }

    return (
        <div
            style={{
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-lg)',
                padding: '14px 18px',
                marginBottom: '20px',
                display: 'flex',
                alignItems: 'center',
                gap: '20px',
                cursor: 'pointer',
                transition: 'border-color 0.15s',
                background: 'var(--bg-secondary)',
            }}
            onClick={() => navigate('/okr')}
            onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--accent-primary)')}
            onMouseLeave={e => (e.currentTarget.style.borderColor = 'var(--border-subtle)')}
        >
            {/* Donut */}
            <svg width={72} height={72} viewBox="0 0 72 72" style={{ flexShrink: 0 }}>
                {total === 0 ? (
                    <circle cx={CX} cy={CY} r={R} fill="none" stroke="var(--bg-tertiary)" strokeWidth={STROKE} />
                ) : (
                    arcs.map(arc =>
                        arc.dash > 0 ? (
                            <circle
                                key={arc.key}
                                cx={CX} cy={CY} r={R}
                                fill="none"
                                stroke={arc.color}
                                strokeWidth={STROKE}
                                strokeDasharray={`${arc.dash} ${arc.gap}`}
                                strokeDashoffset={0}
                                transform={`rotate(${arc.rotate - 90} ${CX} ${CY})`}
                                opacity={0.9}
                            />
                        ) : null
                    )
                )}
                {/* Center text */}
                <text x={CX} y={CY - 5} textAnchor="middle" fontSize="13" fontWeight="600" fill="var(--text-primary)">{total}</text>
                <text x={CX} y={CY + 9} textAnchor="middle" fontSize="9" fill="var(--text-tertiary)">KRs</text>
            </svg>

            {/* Labels */}
            <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '6px' }}>
                    {isChinese ? '本期 OKR 概览' : 'Current OKR Overview'}
                    <span style={{ fontSize: '11px', fontWeight: 400, color: 'var(--text-tertiary)', marginLeft: '8px' }}>
                        {objectives.length} {isChinese ? '个目标' : objectives.length === 1 ? 'objective' : 'objectives'}
                    </span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                    {order.map(key => counts[key] > 0 && (
                        <span key={key} style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                            <span style={{ width: 8, height: 8, borderRadius: '50%', background: COLORS[key], flexShrink: 0 }} />
                            {counts[key]} {labels[key]}
                        </span>
                    ))}
                </div>
            </div>

            {/* Arrow */}
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-tertiary)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                <polyline points="9 18 15 12 9 6" />
            </svg>
        </div>
    );
}

/* ────── Summary Stats Bar ────── */

function StatsBar({ agents, allTasks, tokenUsage }: { agents: Agent[]; allTasks: Task[]; tokenUsage?: any }) {
    const { t } = useTranslation();
    const totalAgents = agents.length;
    const activeAgents = agents.filter(a => a.status === 'running' || a.status === 'idle').length;
    const pendingTasks = allTasks.filter(t => t.status === 'pending' || t.status === 'doing').length;
    const completedToday = allTasks.filter(t => {
        if (t.status !== 'done' || !t.completed_at) return false;
        const today = new Date();
        const completed = new Date(t.completed_at);
        return completed.toDateString() === today.toDateString();
    }).length;
    const totalTokensToday = tokenUsage?.today?.total_tokens ?? agents.reduce((sum, a) => sum + (a.tokens_used_today || 0), 0);
    const cacheReadToday = tokenUsage?.today?.cache_read_tokens ?? agents.reduce((sum, a) => sum + (a.cache_read_tokens_today || 0), 0);
    const cacheHitRate = totalTokensToday > 0 ? Math.round((cacheReadToday / totalTokensToday) * 100) : 0;
    const recentlyActive = agents.filter(a => {
        if (!a.last_active_at) return false;
        return Date.now() - new Date(a.last_active_at).getTime() < 3600000;
    }).length;

    const stats = [
        { icon: Icons.users, label: t('dashboard.stats.agents'), value: totalAgents, sub: t('dashboard.stats.online', { count: activeAgents }) },
        { icon: Icons.tasks, label: t('dashboard.stats.activeTasks'), value: pendingTasks, sub: t('dashboard.stats.completedToday', { count: completedToday }) },
        {
            icon: Icons.zap,
            label: t('dashboard.stats.todayTokens'),
            value: formatTokens(totalTokensToday),
            sub: `Cache ${formatTokens(cacheReadToday)} · ${cacheHitRate}%`,
        },
        { icon: Icons.clock, label: t('dashboard.stats.recentlyActive'), value: recentlyActive, sub: t('dashboard.stats.lastHour') },
    ];

    return (
        <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '1px',
            background: 'var(--border-subtle)', borderRadius: 'var(--radius-lg)',
            overflow: 'hidden', marginBottom: '24px',
            border: '1px solid var(--border-subtle)',
        }}>
            {stats.map((s, i) => (
                <div key={i} style={{
                    background: 'var(--bg-secondary)', padding: '16px 20px',
                    display: 'flex', flexDirection: 'column', gap: '2px',
                }}>
                    <div style={{
                        fontSize: '12px', color: 'var(--text-tertiary)',
                        display: 'flex', alignItems: 'center', gap: '6px',
                        marginBottom: '4px',
                    }}>
                        <span style={{ display: 'flex', opacity: 0.7 }}>{s.icon}</span> {s.label}
                    </div>
                    <div style={{ fontSize: '24px', fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
                        {s.value}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{s.sub}</div>
                </div>
            ))}
        </div>
    );
}

/* ────── Agent Row ────── */

function AgentRow({ agent, tasks, recentActivity }: {
    agent: Agent;
    tasks: Task[];
    recentActivity: any[];
}) {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const pendingTasks = tasks.filter(t => t.status === 'pending' || t.status === 'doing');
    const latestActivity = recentActivity[0];

    // Token usage bar
    const maxTokens = agent.max_tokens_per_day || 0;
    const usedTokens = agent.tokens_used_today || 0;
    const tokenPct = maxTokens > 0 ? Math.min(100, (usedTokens / maxTokens) * 100) : 0;

    return (
        <div
            onClick={() => navigate(`/agents/${agent.id}`)}
            style={{
                display: 'grid',
                gridTemplateColumns: '220px 1fr 150px 100px',
                alignItems: 'center', gap: '16px',
                padding: '12px 16px',
                borderRadius: 'var(--radius-md)',
                cursor: 'pointer', transition: 'background 120ms ease',
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'var(--bg-hover)'; }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
        >
            {/* Agent Info */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
                <div style={{
                    width: '32px', height: '32px', borderRadius: 'var(--radius-md)',
                    background: 'var(--bg-tertiary)', border: '1px solid var(--border-subtle)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: 'var(--text-tertiary)', flexShrink: 0,
                }}>
                    {Icons.bot}
                </div>
                <div style={{ minWidth: 0 }}>
                    <div style={{
                        fontWeight: 500, fontSize: '13px', display: 'flex',
                        alignItems: 'center', gap: '8px', color: 'var(--text-primary)',
                    }}>
                        {agent.name}
                        <span style={{
                            display: 'inline-flex', alignItems: 'center', gap: '4px',
                            fontSize: '11px', fontWeight: 400,
                            color: statusColor(agent.status),
                        }}>
                            <span style={{
                                width: '6px', height: '6px', borderRadius: '50%',
                                background: statusColor(agent.status),
                                display: 'inline-block',
                            }} />
                            {statusLabel(agent.status, t)}
                        </span>
                    </div>
                    <div style={{
                        fontSize: '12px', color: 'var(--text-tertiary)',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                        {agent.role_description || '-'}
                    </div>
                </div>
            </div>

            {/* Latest Activity / Tasks */}
            <div style={{ minWidth: 0 }}>
                {latestActivity ? (
                    <div style={{
                        fontSize: '12px', color: 'var(--text-secondary)',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                        <span style={{ color: 'var(--text-tertiary)', marginRight: '6px' }}>
                            {timeAgo(latestActivity.created_at, t)}
                        </span>
                        {latestActivity.summary}
                    </div>
                ) : (
                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('dashboard.noActivity')}</div>
                )}
                {pendingTasks.length > 0 && (
                    <div style={{ display: 'flex', gap: '4px', marginTop: '4px', flexWrap: 'wrap' }}>
                        {pendingTasks.slice(0, 3).map(t => (
                            <span key={t.id} style={{
                                fontSize: '11px', padding: '1px 6px',
                                borderRadius: 'var(--radius-sm)', background: 'var(--bg-tertiary)',
                                color: 'var(--text-secondary)', maxWidth: '140px',
                                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                display: 'inline-flex', alignItems: 'center', gap: '3px',
                            }}>
                                <span style={{ width: '4px', height: '4px', borderRadius: '50%', background: priorityColor(t.priority), flexShrink: 0 }} />
                                {t.title}
                            </span>
                        ))}
                        {pendingTasks.length > 3 && (
                            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', padding: '1px 4px' }}>
                                +{pendingTasks.length - 3}
                            </span>
                        )}
                    </div>
                )}
            </div>

            {/* Token Usage */}
            <div>
                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '3px' }}>
                    {formatTokens(usedTokens)}
                    {maxTokens > 0 && <span style={{ opacity: 0.6 }}> / {formatTokens(maxTokens)}</span>}
                </div>
                {!!agent.cache_read_tokens_today && (
                    <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '3px' }}>
                        Cache {formatTokens(agent.cache_read_tokens_today)} · {usedTokens > 0 ? Math.round((agent.cache_read_tokens_today / usedTokens) * 100) : 0}%
                    </div>
                )}
                {maxTokens > 0 ? (
                    <div style={{
                        height: '3px', background: 'var(--bg-tertiary)',
                        borderRadius: '2px', overflow: 'hidden',
                    }}>
                        <div style={{
                            height: '100%', borderRadius: '2px',
                            width: `${tokenPct}%`,
                            background: tokenPct > 80 ? 'var(--error)' : tokenPct > 50 ? 'var(--warning)' : 'var(--text-tertiary)',
                            transition: 'width 0.3s',
                        }} />
                    </div>
                ) : (
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', opacity: 0.5 }}>{t('dashboard.noLimit')}</div>
                )}
            </div>

            {/* Last Active */}
            <div style={{ textAlign: 'right', fontSize: '12px', color: 'var(--text-tertiary)' }}>
                {timeAgo(agent.last_active_at, t)}
            </div>
        </div>
    );
}

/* ────── Recent Activity Feed ────── */

function ActivityFeed({ activities, agents }: { activities: any[]; agents: Agent[] }) {
    const { t } = useTranslation();
    const agentMap = new Map(agents.map(a => [a.id, a]));

    if (activities.length === 0) {
        return (
            <div style={{ textAlign: 'center', padding: '32px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                {t('dashboard.noActivity')}
            </div>
        );
    }

    return (
        <div style={{ display: 'flex', flexDirection: 'column' }}>
            {activities.map((act, i) => {
                const agent = agentMap.get(act.agent_id);
                return (
                    <div key={act.id || i} style={{
                        display: 'flex', gap: '12px', padding: '7px 12px',
                        fontSize: '13px', alignItems: 'flex-start',
                    }}>
                        <span style={{
                            color: 'var(--text-tertiary)', whiteSpace: 'nowrap',
                            fontFamily: 'var(--font-mono)', fontSize: '11px',
                            minWidth: '52px', paddingTop: '2px',
                        }}>
                            {timeAgo(act.created_at, t)}
                        </span>
                        <span style={{
                            fontSize: '11px', padding: '1px 6px',
                            borderRadius: 'var(--radius-sm)', background: 'var(--bg-tertiary)',
                            color: 'var(--text-secondary)', whiteSpace: 'nowrap', flexShrink: 0,
                            fontWeight: 500,
                        }}>
                            {agent?.name || act.agent_id?.slice(0, 6)}
                        </span>
                        <span style={{
                            color: 'var(--text-secondary)', flex: 1,
                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>
                            {act.summary}
                        </span>
                    </div>
                );
            })}
        </div>
    );
}

/* ────── Main Dashboard ────── */

export default function Dashboard() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const currentTenant = localStorage.getItem('current_tenant_id') || '';

    const { data: agents = [], isLoading } = useQuery({
        queryKey: ['agents', currentTenant],
        queryFn: () => agentApi.list(currentTenant || undefined),
        refetchInterval: 15000,
    });

    const { data: tokenUsage } = useQuery({
        queryKey: ['tenant-token-usage', currentTenant],
        queryFn: () => tenantApi.tokenUsage(),
        refetchInterval: 15000,
    });

    // Fetch tasks & activities for all agents
    const [allTasks, setAllTasks] = useState<Task[]>([]);
    const [allActivities, setAllActivities] = useState<any[]>([]);
    const [agentActivities, setAgentActivities] = useState<Record<string, any[]>>({});


    useEffect(() => {
        if (agents.length === 0) return;
        const fetchData = async () => {
            try {
                const taskResults = await Promise.allSettled(agents.map(a => taskApi.list(a.id)));
                const tasks: Task[] = [];
                taskResults.forEach(r => { if (r.status === 'fulfilled') tasks.push(...r.value); });
                setAllTasks(tasks);
            } catch (e) { console.error('Failed to fetch tasks:', e); }

            try {
                const actResults = await Promise.allSettled(agents.map(a => activityApi.list(a.id, 5)));
                const activities: any[] = [];
                const perAgent: Record<string, any[]> = {};
                actResults.forEach((r, i) => {
                    if (r.status === 'fulfilled') {
                        perAgent[agents[i].id] = r.value;
                        activities.push(...r.value.map((v: any) => ({ ...v, agent_id: agents[i].id })));
                    }
                });
                activities.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
                setAllActivities(activities.slice(0, 20));
                setAgentActivities(perAgent);
            } catch (e) { console.error('Failed to fetch activities:', e); }
        };
        fetchData();
        const interval = setInterval(fetchData, 30000);
        return () => clearInterval(interval);
    }, [agents.map(a => a.id).join(',')]);

    // Group tasks by agent
    const tasksByAgent = new Map<string, Task[]>();
    allTasks.forEach(t => {
        if (!tasksByAgent.has(t.agent_id)) tasksByAgent.set(t.agent_id, []);
        tasksByAgent.get(t.agent_id)!.push(t);
    });

    // Greeting
    const hour = new Date().getHours();
    const greeting = hour < 6 ? '🌙 ' + t('dashboard.greeting.lateNight') : hour < 12 ? '☀️ ' + t('dashboard.greeting.morning') : hour < 18 ? '🌤️ ' + t('dashboard.greeting.afternoon') : '🌙 ' + t('dashboard.greeting.evening');

    return (
        <div>
            {/* Header */}
            <div style={{
                display: 'flex', justifyContent: 'space-between',
                alignItems: 'center', marginBottom: '28px',
            }}>
                <div>
                    <h1 style={{ fontSize: '20px', fontWeight: 600, margin: 0, marginBottom: '2px', letterSpacing: '-0.02em' }}>
                        {greeting}
                    </h1>
                    <p style={{ fontSize: '13px', color: 'var(--text-tertiary)', margin: 0 }}>
                        {t('dashboard.totalAgents', { count: agents.length })}
                    </p>
                </div>
            </div>

            {isLoading ? (
                <div style={{ textAlign: 'center', padding: '60px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                    {t('common.loading')}
                </div>
            ) : agents.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '80px' }}>
                    <div style={{ color: 'var(--text-tertiary)', marginBottom: '4px', fontSize: '32px' }}>
                        {Icons.bot}
                    </div>
                    <div style={{ color: 'var(--text-secondary)', marginBottom: '16px', fontSize: '14px' }}>
                        {t('dashboard.noAgents')}
                    </div>
                    <button className="btn btn-primary" onClick={() => navigate('/agents/new')}>
                        {Icons.plus} {t('nav.newAgent')}
                    </button>
                </div>
            ) : (
                <>
                    {/* Stats Bar */}
                    <StatsBar agents={agents} allTasks={allTasks} tokenUsage={tokenUsage} />

                    {/* OKR Summary (P3) — only shown when OKR is enabled */}
                    <OKRSummaryCard />

                    {/* Agent List Card */}
                    <div style={{
                        border: '1px solid var(--border-subtle)',
                        borderRadius: 'var(--radius-lg)',
                        overflow: 'hidden',
                        marginBottom: '32px',
                    }}>
                        {/* Agent List Header */}
                        <div style={{
                            display: 'grid',
                            gridTemplateColumns: '220px 1fr 150px 100px',
                            padding: '10px 16px',
                            fontSize: '11px', color: 'var(--text-tertiary)', fontWeight: 500,
                            textTransform: 'uppercase' as const, letterSpacing: '0.05em',
                            borderBottom: '1px solid var(--border-subtle)',
                        }}>
                            <span>{t('dashboard.table.agent')}</span>
                            <span>{t('dashboard.table.latestActivity')}</span>
                            <span>Token</span>
                            <span style={{ textAlign: 'right' }}>{t('dashboard.table.active')}</span>
                        </div>

                        {/* Agent Rows (scrollable) */}
                        <div style={{ maxHeight: '350px', overflowY: 'auto' }}>
                            {agents
                                .sort((a, b) => {
                                    const aActive = a.status === 'running' || a.status === 'idle' ? 1 : 0;
                                    const bActive = b.status === 'running' || b.status === 'idle' ? 1 : 0;
                                    if (aActive !== bActive) return bActive - aActive;
                                    const aTime = a.last_active_at ? new Date(a.last_active_at).getTime() : 0;
                                    const bTime = b.last_active_at ? new Date(b.last_active_at).getTime() : 0;
                                    return bTime - aTime;
                                })
                                .map(agent => (
                                    <AgentRow
                                        key={agent.id}
                                        agent={agent}
                                        tasks={tasksByAgent.get(agent.id) || []}
                                        recentActivity={agentActivities[agent.id] || []}
                                    />
                                ))}
                        </div>
                    </div>

                    {/* Recent Activity */}
                    <div style={{
                        border: '1px solid var(--border-subtle)',
                        borderRadius: 'var(--radius-lg)', overflow: 'hidden',
                    }}>
                        <div style={{
                            padding: '12px 16px', borderBottom: '1px solid var(--border-subtle)',
                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                        }}>
                            <h3 style={{
                                margin: 0, fontSize: '13px', fontWeight: 500,
                                display: 'flex', alignItems: 'center', gap: '6px',
                                color: 'var(--text-secondary)',
                            }}>
                                <span style={{ display: 'flex', opacity: 0.6 }}>{Icons.activity}</span>
                                {t('dashboard.globalActivity')}
                            </h3>
                            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('dashboard.recentCount', { count: 20 })}</span>
                        </div>
                        <div style={{ padding: '4px', maxHeight: '320px', overflowY: 'auto' }}>
                            <ActivityFeed activities={allActivities} agents={agents} />
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}
