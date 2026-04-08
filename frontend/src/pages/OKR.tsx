/**
 * OKR Page — Objectives & Key Results dashboard.
 *
 * When OKR is disabled in the tenant settings, shows a guide panel
 * directing admins to enable the feature in Company Settings.
 *
 * When enabled, shows:
 *   - Period selector
 *   - Company-level objectives
 *   - Member (user + agent) objectives
 *   - Work report list
 */

import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { fetchJson } from '../services/api';
import { useAuthStore } from '../stores';

// ─── Type Definitions ────────────────────────────────────────────────────────

interface OKRSettings {
    enabled: boolean;
    daily_report_enabled: boolean;
    daily_report_time: string;
    weekly_report_enabled: boolean;
    weekly_report_day: number;
    period_frequency: string;
    period_length_days?: number;
}

interface KeyResult {
    id: string;
    objective_id: string;
    title: string;
    target_value: number;
    current_value: number;
    unit?: string;
    focus_ref?: string;
    status: string; // on_track | at_risk | behind | completed
    last_updated_at: string;
    created_at: string;
}

interface Objective {
    id: string;
    title: string;
    description?: string;
    owner_type: string; // company | user | agent
    owner_id?: string;
    period_start: string;
    period_end: string;
    status: string;
    created_at: string;
    key_results: KeyResult[];
}

interface Period {
    start: string;
    end: string;
    label: string;
    is_current: boolean;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, string> = {
    on_track: '#22c55e',   // green
    at_risk: '#f59e0b',    // amber
    behind: '#ef4444',     // red
    completed: '#6366f1',  // purple
};

const STATUS_LABEL_ZH: Record<string, string> = {
    on_track: '按计划',
    at_risk: '有风险',
    behind: '落后',
    completed: '已完成',
};

function progressPercent(kr: KeyResult): number {
    if (!kr.target_value) return 0;
    return Math.min(100, Math.round((kr.current_value / kr.target_value) * 100));
}

function objectiveProgress(obj: Objective): number {
    if (!obj.key_results.length) return 0;
    const avg = obj.key_results.reduce((s, kr) => s + progressPercent(kr), 0) / obj.key_results.length;
    return Math.round(avg);
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatusBadge({ status, isChinese }: { status: string; isChinese: boolean }) {
    const color = STATUS_COLOR[status] ?? 'var(--text-tertiary)';
    const label = isChinese
        ? (STATUS_LABEL_ZH[status] ?? status)
        : status.replace('_', ' ');
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: '4px',
            padding: '2px 8px', borderRadius: '100px',
            background: `${color}18`,
            border: `1px solid ${color}40`,
            color, fontSize: '11px', fontWeight: 500,
            textTransform: 'capitalize',
        }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: color, flexShrink: 0 }} />
            {label}
        </span>
    );
}

function ProgressBar({ pct, status }: { pct: number; status: string }) {
    const color = STATUS_COLOR[status] ?? 'var(--accent-primary)';
    return (
        <div style={{ height: 4, background: 'var(--bg-tertiary)', borderRadius: 2, overflow: 'hidden', flexGrow: 1 }}>
            <div style={{
                height: '100%', borderRadius: 2,
                background: color,
                width: `${pct}%`,
                transition: 'width 0.6s ease',
            }} />
        </div>
    );
}

function KRCard({ kr, isChinese }: { kr: KeyResult; isChinese: boolean }) {
    const pct = progressPercent(kr);
    return (
        <div style={{
            padding: '10px 14px',
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-subtle)',
            borderRadius: '8px',
            display: 'flex', flexDirection: 'column', gap: '8px',
        }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
                <span style={{ fontSize: '13px', color: 'var(--text-primary)', flex: 1 }}>{kr.title}</span>
                <StatusBadge status={kr.status} isChinese={isChinese} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <ProgressBar pct={pct} status={kr.status} />
                <span style={{ fontSize: '11px', color: 'var(--text-secondary)', whiteSpace: 'nowrap', minWidth: 60, textAlign: 'right' }}>
                    {kr.current_value} / {kr.target_value}
                    {kr.unit ? ` ${kr.unit}` : ''} ({pct}%)
                </span>
            </div>
        </div>
    );
}

function ObjectiveCard({ obj, isChinese, ownerLabel }: {
    obj: Objective;
    isChinese: boolean;
    ownerLabel?: string;
}) {
    const [expanded, setExpanded] = useState(true);
    const pct = objectiveProgress(obj);
    const overallStatus = obj.status === 'completed' ? 'completed'
        : pct >= 70 ? 'on_track'
        : pct >= 40 ? 'at_risk'
        : 'behind';

    return (
        <div style={{
            border: '1px solid var(--border-subtle)',
            borderRadius: '10px',
            overflow: 'hidden',
            background: 'var(--bg-primary)',
        }}>
            {/* Header */}
            <div
                role="button"
                tabIndex={0}
                style={{
                    padding: '14px 16px',
                    display: 'flex', alignItems: 'center', gap: '12px',
                    cursor: 'pointer',
                    borderBottom: expanded ? '1px solid var(--border-subtle)' : 'none',
                    transition: 'background 0.15s',
                }}
                onClick={() => setExpanded(v => !v)}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') setExpanded(v => !v); }}
            >
                {/* Expand/collapse chevron */}
                <svg
                    width="14" height="14" viewBox="0 0 24 24" fill="none"
                    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                    style={{ flexShrink: 0, color: 'var(--text-tertiary)', transform: expanded ? 'rotate(0)' : 'rotate(-90deg)', transition: 'transform 0.2s' }}
                >
                    <polyline points="6 9 12 15 18 9" />
                </svg>

                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                        {ownerLabel && (
                            <span style={{
                                fontSize: '11px', color: 'var(--text-tertiary)',
                                border: '1px solid var(--border-subtle)',
                                borderRadius: '4px', padding: '1px 6px', flexShrink: 0,
                            }}>{ownerLabel}</span>
                        )}
                        <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
                            {obj.title}
                        </span>
                    </div>
                    {obj.description && (
                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{obj.description}</div>
                    )}
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 80 }}>
                        <ProgressBar pct={pct} status={overallStatus} />
                        <span style={{ fontSize: '12px', color: 'var(--text-secondary)', fontWeight: 500, minWidth: 30 }}>
                            {pct}%
                        </span>
                    </div>
                    <StatusBadge status={overallStatus} isChinese={isChinese} />
                </div>
            </div>

            {/* KR list */}
            {expanded && obj.key_results.length > 0 && (
                <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {obj.key_results.map(kr => (
                        <KRCard key={kr.id} kr={kr} isChinese={isChinese} />
                    ))}
                </div>
            )}
            {expanded && obj.key_results.length === 0 && (
                <div style={{ padding: '16px', color: 'var(--text-tertiary)', fontSize: '13px', textAlign: 'center' }}>
                    {isChinese ? '暂无 Key Results' : 'No Key Results yet'}
                </div>
            )}
        </div>
    );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function OKR() {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const user = useAuthStore(s => s.user);
    const isChinese = i18n.language?.startsWith('zh');

    const [selectedPeriod, setSelectedPeriod] = useState<Period | null>(null);

    // Fetch OKR settings
    const { data: settings, isLoading: settingsLoading } = useQuery<OKRSettings>({
        queryKey: ['okr-settings'],
        queryFn: () => fetchJson<OKRSettings>('/okr/settings'),
    });

    // Fetch periods (only when enabled)
    const { data: periods = [] } = useQuery<Period[]>({
        queryKey: ['okr-periods'],
        queryFn: () => fetchJson<Period[]>('/okr/periods'),
        enabled: !!settings?.enabled,
    });

    // Auto-select current period on first load
    useEffect(() => {
        if (!selectedPeriod && periods.length > 0) {
            const current = periods.find(p => p.is_current) ?? periods[periods.length - 1];
            setSelectedPeriod(current);
        }
    }, [periods, selectedPeriod]);

    // Fetch objectives for selected period
    const { data: objectives = [], isLoading: objLoading } = useQuery<Objective[]>({
        queryKey: ['okr-objectives', selectedPeriod?.start, selectedPeriod?.end],
        queryFn: () => fetchJson<Objective[]>(
            `/okr/objectives?period_start=${selectedPeriod!.start}&period_end=${selectedPeriod!.end}`
        ),
        enabled: !!settings?.enabled && !!selectedPeriod,
    });

    // ── Loading state ─────────────────────────────────────────────────────────
    if (settingsLoading) {
        return (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '50vh', color: 'var(--text-tertiary)' }}>
                {isChinese ? '加载中...' : 'Loading...'}
            </div>
        );
    }

    // ── OKR Disabled — guide panel ─────────────────────────────────────────────
    if (!settings?.enabled) {
        const isAdmin = user && ['platform_admin', 'org_admin'].includes(user.role);
        return (
            <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                height: '70vh', gap: '16px', color: 'var(--text-secondary)', textAlign: 'center', padding: '24px',
            }}>
                {/* Target icon */}
                <div style={{
                    width: 64, height: 64, borderRadius: '50%',
                    background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: 'var(--text-tertiary)',
                }}>
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                        <circle cx="12" cy="12" r="10" />
                        <circle cx="12" cy="12" r="6" />
                        <circle cx="12" cy="12" r="2" />
                    </svg>
                </div>
                <div>
                    <div style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '8px' }}>
                        {isChinese ? 'OKR 功能尚未开启' : 'OKR is not enabled'}
                    </div>
                    <div style={{ fontSize: '13px', color: 'var(--text-tertiary)', maxWidth: 400 }}>
                        {isChinese
                            ? 'OKR 系统可以帮助团队设定目标、跟踪进度，并通过 OKR Agent 自动收集工作汇报。'
                            : 'The OKR system helps your team set objectives, track progress, and automatically collect work reports via the OKR Agent.'}
                    </div>
                </div>
                {isAdmin && (
                    <button
                        className="btn btn-primary"
                        onClick={() => navigate('/enterprise?tab=okr')}
                        style={{ padding: '8px 20px', fontSize: '13px' }}
                    >
                        {isChinese ? '前往公司设置开启 OKR' : 'Enable OKR in Company Settings'}
                    </button>
                )}
                {!isAdmin && (
                    <div style={{ fontSize: '12px', color: 'var(--text-quaternary)' }}>
                        {isChinese ? '请联系管理员开启 OKR 功能' : 'Please ask an admin to enable OKR'}
                    </div>
                )}
            </div>
        );
    }

    // ── OKR Enabled ────────────────────────────────────────────────────────────
    const companyObjs = objectives.filter(o => o.owner_type === 'company');
    const memberObjs = objectives.filter(o => o.owner_type !== 'company');

    // Group member objectives by owner
    const memberGroups: Record<string, Objective[]> = {};
    for (const obj of memberObjs) {
        const key = `${obj.owner_type}:${obj.owner_id ?? ''}`;
        memberGroups[key] = [...(memberGroups[key] ?? []), obj];
    }

    return (
        <div style={{ padding: '24px', maxWidth: 960, margin: '0 auto' }}>
            {/* Page Header */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '24px', flexWrap: 'wrap', gap: '12px' }}>
                <div>
                    <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 700, color: 'var(--text-primary)' }}>
                        {t('okr.title', 'OKR')}
                    </h1>
                    <div style={{ fontSize: '13px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                        {isChinese ? '目标与关键成果' : 'Objectives & Key Results'}
                    </div>
                </div>

                {/* Period Selector */}
                {periods.length > 0 && (
                    <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                        {periods.map(p => (
                            <button
                                key={p.start}
                                onClick={() => setSelectedPeriod(p)}
                                style={{
                                    padding: '5px 12px', borderRadius: '6px', fontSize: '12px', fontWeight: 500,
                                    border: '1px solid',
                                    borderColor: selectedPeriod?.start === p.start ? 'var(--accent-primary)' : 'var(--border-subtle)',
                                    background: selectedPeriod?.start === p.start ? 'var(--accent-primary)' : 'var(--bg-secondary)',
                                    color: selectedPeriod?.start === p.start ? '#fff' : 'var(--text-secondary)',
                                    cursor: 'pointer', transition: 'all 0.15s',
                                }}
                            >
                                {p.label}
                                {p.is_current && (
                                    <span style={{ marginLeft: '4px', opacity: 0.7, fontSize: '10px' }}>
                                        {isChinese ? '(当前)' : '(now)'}
                                    </span>
                                )}
                            </button>
                        ))}
                    </div>
                )}
            </div>

            {/* Loading objectives */}
            {objLoading && (
                <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                    {isChinese ? '加载中...' : 'Loading...'}
                </div>
            )}

            {!objLoading && objectives.length === 0 && (
                <div style={{
                    textAlign: 'center', padding: '60px 24px',
                    border: '1px dashed var(--border-subtle)', borderRadius: '12px',
                    color: 'var(--text-tertiary)', fontSize: '13px',
                }}>
                    {isChinese
                        ? '当前周期暂无 OKR。请联系 OKR Agent 来设定公司和个人目标。'
                        : 'No OKRs for this period yet. Ask the OKR Agent to set up objectives.'}
                </div>
            )}

            {/* Company Objectives */}
            {!objLoading && companyObjs.length > 0 && (
                <section style={{ marginBottom: '32px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                        <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.8px' }}>
                            {t('okr.companyObjectives', isChinese ? '公司目标' : 'Company Objectives')}
                        </span>
                        <div style={{ flex: 1, height: '1px', background: 'var(--border-subtle)' }} />
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                        {companyObjs.map(obj => (
                            <ObjectiveCard key={obj.id} obj={obj} isChinese={isChinese} />
                        ))}
                    </div>
                </section>
            )}

            {/* Member Objectives */}
            {!objLoading && Object.keys(memberGroups).length > 0 && (
                <section>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                        <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.8px' }}>
                            {t('okr.memberObjectives', isChinese ? '成员目标' : 'Member Objectives')}
                        </span>
                        <div style={{ flex: 1, height: '1px', background: 'var(--border-subtle)' }} />
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                        {Object.entries(memberGroups).map(([ownerKey, objs]) => (
                            <div key={ownerKey}>
                                {objs.map(obj => (
                                    <ObjectiveCard
                                        key={obj.id}
                                        obj={obj}
                                        isChinese={isChinese}
                                        ownerLabel={obj.owner_type}
                                    />
                                ))}
                            </div>
                        ))}
                    </div>
                </section>
            )}
        </div>
    );
}
