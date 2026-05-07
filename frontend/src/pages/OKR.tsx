/**
 * OKR Page — Objectives & Key Results dashboard with full editing support.
 *
 * Features:
 *   - Period selector (computed from OKR settings)
 *   - Company-level and member objectives with progress visualization
 *   - Create Objective (admin: company level; users: own level)
 *   - Add Key Result to an objective
 *   - Inline KR progress editing (current_value update)
 *   - KR status manual override (on_track / at_risk / behind / completed)
 *   - Disabled state: guide panel directing to OKR settings
 */

import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { IconAlertTriangle } from '@tabler/icons-react';
import { fetchJson } from '../services/api';
import { useAuthStore } from '../stores';
import { useDialog } from '../components/Dialog/DialogProvider';

// ─── Type Definitions ────────────────────────────────────────────────────────

interface OKRSettings {
    enabled: boolean;
    first_enabled_at?: string | null;
    daily_report_enabled: boolean;
    daily_report_time: string;
    daily_report_skip_non_workdays?: boolean;
    weekly_report_enabled: boolean;
    weekly_report_day: number;
    period_frequency: string;
    period_length_days?: number;
    period_frequency_locked?: boolean;
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
    owner_name?: string; // resolved display name (agent name or user display_name)
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

interface LegacyWorkReport {
    id: string;
    tenant_id: string;
    okr_agent_id: string;
    report_type: string;
    period_label: string;
    content: string;
    created_at: string;
}

interface CompanyReport {
    id: string;
    report_type: 'daily' | 'weekly' | 'monthly';
    period_start: string;
    period_end: string;
    period_label: string;
    content: string;
    submitted_count: number;
    missing_count: number;
    needs_refresh: boolean;
    generated_at: string;
    updated_at: string;
}

interface MemberDailyReportItem {
    id: string;
    member_type: 'user' | 'agent';
    member_id: string;
    display_name: string;
    avatar_url?: string | null;
    group_label: string;
    report_date: string;
    content: string;
    status: string;
    submitted_at?: string | null;
    updated_at?: string | null;
}

interface MemberWithoutOKR {
    id: string;
    type: 'user' | 'agent';
    display_name: string;
    avatar_url: string;
    channel: string | null;
    channel_user_id: string | null;
    source_label?: string | null;
}

interface ChannelWarning {
    channel_type: string;
    channel_display: string;
    affected_members: string[];
    count: number;
}

interface MembersWithoutOKRData {
    period_start: string;
    period_end: string;
    company_okr_exists: boolean;
    okr_agent_id: string | null;
    members_without_okr: MemberWithoutOKR[];
    tracked_user_ids: string[];
    tracked_agent_ids: string[];
    total: number;
    last_outreach_error?: {
        message: string;
        timestamp: string;
        is_read: boolean;
    } | null;
    channel_warnings?: ChannelWarning[];
}

// ─── Constants ────────────────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, string> = {
    on_track: '#22c55e',
    at_risk: '#f59e0b',
    behind: '#ef4444',
    completed: '#6366f1',
};

const STATUS_LABELS: Record<string, { zh: string; en: string }> = {
    on_track:  { zh: '按计划', en: 'On Track' },
    at_risk:   { zh: '有风险', en: 'At Risk' },
    behind:    { zh: '落后',   en: 'Behind' },
    completed: { zh: '已完成', en: 'Completed' },
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function progressPercent(kr: KeyResult): number {
    if (!kr.target_value) return 0;
    return Math.min(100, Math.round((kr.current_value / kr.target_value) * 100));
}

function objectiveProgress(obj: Objective): number {
    if (!obj.key_results.length) return 0;
    const avg = obj.key_results.reduce((s, kr) => s + progressPercent(kr), 0) / obj.key_results.length;
    return Math.round(avg);
}

function deriveStatus(pct: number, explicit?: string): string {
    if (explicit && explicit !== 'auto') return explicit;
    if (pct >= 100) return 'completed';
    if (pct >= 70)  return 'on_track';
    if (pct >= 40)  return 'at_risk';
    return 'behind';
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatusBadge({ status, isChinese }: { status: string; isChinese: boolean }) {
    const color = STATUS_COLOR[status] ?? 'var(--text-tertiary)';
    const label = isChinese ? (STATUS_LABELS[status]?.zh ?? status) : (STATUS_LABELS[status]?.en ?? status);
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: '4px',
            padding: '2px 8px', borderRadius: '100px',
            background: `${color}18`,
            border: `1px solid ${color}40`,
            color, fontSize: '11px', fontWeight: 500,
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
                background: color, width: `${pct}%`,
                transition: 'width 0.6s ease',
            }} />
        </div>
    );
}

// ── KR Card: displays a key result with inline progress editing ──
function KRCard({
    kr,
    isChinese,
    onUpdateProgress,
    onDelete,
    canEdit,
}: {
    kr: KeyResult;
    isChinese: boolean;
    onUpdateProgress: (krId: string, value: number, status: string, note: string) => void;
    onDelete?: (krId: string) => void;
    canEdit: boolean;
}) {
    const dialog = useDialog();
    const pct = progressPercent(kr);
    const [editing, setEditing] = useState(false);
    const [editValue, setEditValue] = useState(String(kr.current_value));
    const [editStatus, setEditStatus] = useState('auto');
    const [editNote, setEditNote] = useState('');
    const [saving, setSaving] = useState(false);

    async function handleSave() {
        const val = parseFloat(editValue);
        if (isNaN(val)) return;
        setSaving(true);
        try {
            await onUpdateProgress(kr.id, val, editStatus, editNote);
            setEditing(false);
            setEditNote('');
        } finally {
            setSaving(false);
        }
    }

    return (
        <div style={{
            padding: editing ? '12px 14px' : '10px 14px',
            background: 'var(--bg-secondary)',
            border: `1px solid ${editing ? 'var(--accent-primary)40' : 'var(--border-subtle)'}`,
            borderRadius: '8px',
            display: 'flex', flexDirection: 'column', gap: '8px',
            transition: 'border-color 0.15s',
        }}>
            {/* Title row */}
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '8px' }}>
                <span style={{ fontSize: '13px', color: 'var(--text-primary)', flex: 1 }}>{kr.title}</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
                    <StatusBadge status={kr.status} isChinese={isChinese} />
                    {canEdit && !editing && (
                        <button
                            id={`kr-edit-${kr.id}`}
                            onClick={() => { setEditing(true); setEditValue(String(kr.current_value)); }}
                            style={{
                                background: 'none', border: '1px solid var(--border-subtle)',
                                borderRadius: '4px', padding: '2px 8px',
                                fontSize: '11px', color: 'var(--text-tertiary)',
                                cursor: 'pointer', transition: 'all 0.15s',
                                whiteSpace: 'nowrap',
                            }}
                            onMouseEnter={e => {
                                (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--accent-primary)';
                                (e.currentTarget as HTMLButtonElement).style.color = 'var(--accent-primary)';
                            }}
                            onMouseLeave={e => {
                                (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border-subtle)';
                                (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-tertiary)';
                            }}
                        >
                            {isChinese ? '更新进度' : 'Update'}
                        </button>
                    )}
                    {canEdit && !editing && onDelete && (
                        <button
                            onClick={async () => {
                                const ok = await dialog.confirm(
                                    isChinese ? '确定要删除这个 Key Result 吗？此操作不可恢复。' : 'Are you sure you want to delete this Key Result?',
                                    { title: isChinese ? '删除 Key Result' : 'Delete Key Result', danger: true, confirmLabel: isChinese ? '删除' : 'Delete' },
                                );
                                if (ok) {
                                    onDelete(kr.id);
                                }
                            }}
                            title={isChinese ? '删除' : 'Delete'}
                            style={{
                                background: 'none', border: '1px solid var(--border-subtle)',
                                borderRadius: '4px', padding: '2px 6px',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                color: 'var(--text-tertiary)', cursor: 'pointer', transition: 'all 0.15s',
                            }}
                            onMouseEnter={e => {
                                (e.currentTarget as HTMLButtonElement).style.borderColor = '#ef4444';
                                (e.currentTarget as HTMLButtonElement).style.color = '#ef4444';
                            }}
                            onMouseLeave={e => {
                                (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border-subtle)';
                                (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-tertiary)';
                            }}
                        >
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                        </button>
                    )}
                </div>
            </div>

            {/* Progress bar */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <ProgressBar pct={pct} status={kr.status} />
                <span style={{ fontSize: '11px', color: 'var(--text-secondary)', whiteSpace: 'nowrap', minWidth: 64, textAlign: 'right' }}>
                    {kr.current_value} / {kr.target_value}
                    {kr.unit ? ` ${kr.unit}` : ''} ({pct}%)
                </span>
            </div>

            {/* Inline editing form */}
            {editing && (
                <div style={{
                    borderTop: '1px solid var(--border-subtle)',
                    paddingTop: '10px',
                    display: 'flex', flexDirection: 'column', gap: '8px',
                }}>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                                {isChinese ? '当前值' : 'Value'}
                            </span>
                            <input
                                type="number"
                                value={editValue}
                                onChange={e => setEditValue(e.target.value)}
                                style={{
                                    width: 80, padding: '4px 8px',
                                    background: 'var(--bg-primary)',
                                    border: '1px solid var(--border-subtle)',
                                    borderRadius: '4px', color: 'var(--text-primary)',
                                    fontSize: '13px',
                                }}
                            />
                            {kr.unit && <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{kr.unit}</span>}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                                {isChinese ? '状态' : 'Status'}
                            </span>
                            <select
                                value={editStatus}
                                onChange={e => setEditStatus(e.target.value)}
                                style={{
                                    padding: '4px 8px',
                                    background: 'var(--bg-primary)',
                                    border: '1px solid var(--border-subtle)',
                                    borderRadius: '4px', color: 'var(--text-primary)',
                                    fontSize: '12px',
                                }}
                            >
                                <option value="auto">{isChinese ? '自动计算' : 'Auto'}</option>
                                <option value="on_track">{isChinese ? '按计划' : 'On Track'}</option>
                                <option value="at_risk">{isChinese ? '有风险' : 'At Risk'}</option>
                                <option value="behind">{isChinese ? '落后' : 'Behind'}</option>
                                <option value="completed">{isChinese ? '已完成' : 'Completed'}</option>
                            </select>
                        </div>
                    </div>
                    <input
                        type="text"
                        value={editNote}
                        onChange={e => setEditNote(e.target.value)}
                        placeholder={isChinese ? '更新说明（可选）' : 'Update note (optional)'}
                        style={{
                            padding: '6px 10px',
                            background: 'var(--bg-primary)',
                            border: '1px solid var(--border-subtle)',
                            borderRadius: '4px', color: 'var(--text-primary)',
                            fontSize: '12px',
                        }}
                    />
                    <div style={{ display: 'flex', gap: '6px', justifyContent: 'flex-end' }}>
                        <button
                            onClick={() => setEditing(false)}
                            style={{
                                padding: '5px 12px', borderRadius: '4px',
                                border: '1px solid var(--border-subtle)',
                                background: 'none', color: 'var(--text-secondary)',
                                fontSize: '12px', cursor: 'pointer',
                            }}
                        >
                            {isChinese ? '取消' : 'Cancel'}
                        </button>
                        <button
                            onClick={handleSave}
                            disabled={saving}
                            style={{
                                padding: '5px 12px', borderRadius: '4px',
                                border: 'none',
                                background: 'var(--accent-primary)', color: '#fff',
                                fontSize: '12px', cursor: saving ? 'wait' : 'pointer',
                                opacity: saving ? 0.7 : 1,
                            }}
                        >
                            {saving ? (isChinese ? '保存中...' : 'Saving...') : (isChinese ? '保存' : 'Save')}
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}

// ── Add KR inline form ──
function AddKRForm({
    objectiveId,
    periodStart,
    periodEnd,
    isChinese,
    onCreated,
    onCancel,
}: {
    objectiveId: string;
    periodStart: string;
    periodEnd: string;
    isChinese: boolean;
    onCreated: () => void;
    onCancel: () => void;
}) {
    const [title, setTitle] = useState('');
    const [targetValue, setTargetValue] = useState('100');
    const [unit, setUnit] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    async function handleSubmit() {
        if (!title.trim()) { setError(isChinese ? '请输入 Key Result 描述' : 'Please enter a description'); return; }
        setSaving(true);
        setError('');
        try {
            await fetchJson(`/okr/objectives/${objectiveId}/key-results`, {
                method: 'POST',
                body: JSON.stringify({
                    title: title.trim(),
                    target_value: parseFloat(targetValue) || 100,
                    unit: unit.trim() || undefined,
                }),
            });
            onCreated();
        } catch (e: any) {
            setError(e.message ?? 'Error');
        } finally {
            setSaving(false);
        }
    }

    return (
        <div style={{
            padding: '12px 14px',
            background: 'var(--bg-tertiary)',
            border: '1px dashed var(--border-subtle)',
            borderRadius: '8px',
            display: 'flex', flexDirection: 'column', gap: '8px',
        }}>
            <input
                type="text"
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder={isChinese ? 'Key Result 描述，例如：用户满意度达到 4.5 分' : 'e.g. Increase NPS to 50'}
                autoFocus
                style={{
                    padding: '6px 10px',
                    background: 'var(--bg-primary)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: '4px', color: 'var(--text-primary)',
                    fontSize: '13px',
                }}
                onKeyDown={e => { if (e.key === 'Enter') handleSubmit(); if (e.key === 'Escape') onCancel(); }}
            />
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                    {isChinese ? '目标值' : 'Target'}
                </span>
                <input
                    type="number"
                    value={targetValue}
                    onChange={e => setTargetValue(e.target.value)}
                    style={{
                        width: 80, padding: '4px 8px',
                        background: 'var(--bg-primary)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: '4px', color: 'var(--text-primary)',
                        fontSize: '13px',
                    }}
                />
                <input
                    type="text"
                    value={unit}
                    onChange={e => setUnit(e.target.value)}
                    placeholder={isChinese ? '单位（可选，如 %、万元）' : 'Unit (e.g. %, pts)'}
                    style={{
                        flex: 1, padding: '4px 8px',
                        background: 'var(--bg-primary)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: '4px', color: 'var(--text-primary)',
                        fontSize: '12px',
                    }}
                />
            </div>
            {error && <div style={{ fontSize: '12px', color: '#ef4444' }}>{error}</div>}
            <div style={{ display: 'flex', gap: '6px', justifyContent: 'flex-end' }}>
                <button onClick={onCancel} style={{ padding: '5px 12px', borderRadius: '4px', border: '1px solid var(--border-subtle)', background: 'none', color: 'var(--text-secondary)', fontSize: '12px', cursor: 'pointer' }}>
                    {isChinese ? '取消' : 'Cancel'}
                </button>
                <button onClick={handleSubmit} disabled={saving} style={{ padding: '5px 12px', borderRadius: '4px', border: 'none', background: 'var(--accent-primary)', color: '#fff', fontSize: '12px', cursor: saving ? 'wait' : 'pointer', opacity: saving ? 0.7 : 1 }}>
                    {saving ? (isChinese ? '创建中...' : 'Creating...') : (isChinese ? '添加 KR' : 'Add KR')}
                </button>
            </div>
        </div>
    );
}

// ── Objective Card ──
function ObjectiveCard({
    obj,
    isChinese,
    canEdit,
    onInvalidate,
    onDelete,
}: {
    obj: Objective;
    isChinese: boolean;
    canEdit: boolean;
    onInvalidate: () => void;
    onDelete?: (objId: string) => void;
}) {
    const dialog = useDialog();
    const [expanded, setExpanded] = useState(true);
    const [addingKR, setAddingKR] = useState(false);
    const pct = objectiveProgress(obj);
    const overallStatus = obj.status === 'completed' ? 'completed' : deriveStatus(pct);

    // Update KR progress
    async function handleKRProgressUpdate(krId: string, value: number, status: string, note: string) {
        await fetchJson(`/okr/key-results/${krId}/progress`, {
            method: 'POST',
            body: JSON.stringify({ value, status: status === 'auto' ? undefined : status, note: note || undefined }),
        });
        onInvalidate();
    }

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
                    display: 'flex', alignItems: 'flex-start', gap: '12px',
                    cursor: 'pointer',
                    borderBottom: expanded ? '1px solid var(--border-subtle)' : 'none',
                }}
                onClick={() => setExpanded(v => !v)}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') setExpanded(v => !v); }}
            >
                {/* Chevron */}
                <svg
                    width="14" height="14" viewBox="0 0 24 24" fill="none"
                    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                    style={{ flexShrink: 0, color: 'var(--text-tertiary)', transform: expanded ? 'rotate(0)' : 'rotate(-90deg)', transition: 'transform 0.2s', marginTop: '4px' }}
                >
                    <polyline points="6 9 12 15 18 9" />
                </svg>

                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ paddingRight: '12px' }}>
                        <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.5, wordBreak: 'break-word', whiteSpace: 'normal' }}>
                            {obj.title}
                        </div>
                    {obj.description && (
                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '4px', lineHeight: 1.5 }}>{obj.description}</div>
                    )}
                    </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 80 }}>
                        <ProgressBar pct={pct} status={overallStatus} />
                        <span style={{ fontSize: '12px', color: 'var(--text-secondary)', fontWeight: 500, minWidth: 30 }}>{pct}%</span>
                    </div>
                    <StatusBadge status={overallStatus} isChinese={isChinese} />
                    {canEdit && onDelete && (
                        <button
                            onClick={async e => {
                                e.stopPropagation();
                                const ok = await dialog.confirm(
                                    isChinese ? '确定要删除这个目标及其所有相关的 Key Results 吗？（此操作实际上是将目标归档）' : 'Are you sure you want to delete this Objective and all its Key Results? (This will archive the objective)',
                                    { title: isChinese ? '删除 / 归档目标' : 'Delete / Archive Objective', danger: true, confirmLabel: isChinese ? '删除 / 归档' : 'Delete / Archive' },
                                );
                                if (ok) {
                                    onDelete(obj.id);
                                }
                            }}
                            title={isChinese ? '删除 / 归档' : 'Delete / Archive'}
                            style={{
                                background: 'none', border: '1px solid transparent',
                                borderRadius: '4px', padding: '4px',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                color: 'var(--text-tertiary)', cursor: 'pointer', transition: 'all 0.15s',
                                marginLeft: '8px'
                            }}
                            onMouseEnter={e => {
                                (e.currentTarget as HTMLButtonElement).style.borderColor = '#ef4444';
                                (e.currentTarget as HTMLButtonElement).style.color = '#ef4444';
                            }}
                            onMouseLeave={e => {
                                (e.currentTarget as HTMLButtonElement).style.borderColor = 'transparent';
                                (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-tertiary)';
                            }}
                        >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                        </button>
                    )}
                </div>
            </div>

            {/* KR list */}
            {expanded && (
                <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {obj.key_results.map(kr => (
                        <KRCard
                            key={kr.id}
                            kr={kr}
                            isChinese={isChinese}
                            onUpdateProgress={handleKRProgressUpdate}
                            onDelete={async (krId) => {
                                await fetchJson(`/okr/key-results/${krId}`, { method: 'DELETE' });
                                onInvalidate();
                            }}
                            canEdit={canEdit}
                        />
                    ))}
                    {obj.key_results.length === 0 && !addingKR && (
                        <div style={{ color: 'var(--text-tertiary)', fontSize: '13px', textAlign: 'center', padding: '8px 0' }}>
                            {isChinese ? '暂无 Key Results' : 'No Key Results yet'}
                        </div>
                    )}
                    {addingKR && (
                        <AddKRForm
                            objectiveId={obj.id}
                            periodStart={obj.period_start}
                            periodEnd={obj.period_end}
                            isChinese={isChinese}
                            onCreated={() => { setAddingKR(false); onInvalidate(); }}
                            onCancel={() => setAddingKR(false)}
                        />
                    )}
                    {canEdit && !addingKR && (
                        <button
                            id={`add-kr-${obj.id}`}
                            onClick={e => { e.stopPropagation(); setAddingKR(true); }}
                            style={{
                                display: 'flex', alignItems: 'center', gap: '6px',
                                padding: '6px 10px', borderRadius: '6px',
                                border: '1px dashed var(--border-subtle)',
                                background: 'none', color: 'var(--text-tertiary)',
                                fontSize: '12px', cursor: 'pointer',
                                transition: 'all 0.15s', alignSelf: 'flex-start',
                            }}
                            onMouseEnter={e => {
                                (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--accent-primary)';
                                (e.currentTarget as HTMLButtonElement).style.color = 'var(--accent-primary)';
                            }}
                            onMouseLeave={e => {
                                (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border-subtle)';
                                (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-tertiary)';
                            }}
                        >
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                            {isChinese ? '添加 Key Result' : 'Add Key Result'}
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

// ── Create Objective Form ──
function CreateObjectiveForm({
    isChinese,
    isAdmin,
    userId,
    selectedPeriod,
    onCreated,
    onCancel,
}: {
    isChinese: boolean;
    isAdmin: boolean;
    userId: string;
    selectedPeriod: Period;
    onCreated: () => void;
    onCancel: () => void;
}) {
    const [title, setTitle] = useState('');
    const [description, setDescription] = useState('');
    const [ownerType, setOwnerType] = useState<'company' | 'user'>(isAdmin ? 'company' : 'user');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    async function handleSubmit() {
        if (!title.trim()) { setError(isChinese ? '请输入目标标题' : 'Please enter a title'); return; }
        setSaving(true);
        setError('');
        try {
            await fetchJson('/okr/objectives', {
                method: 'POST',
                body: JSON.stringify({
                    title: title.trim(),
                    description: description.trim() || undefined,
                    owner_type: ownerType,
                    owner_id: ownerType === 'user' ? userId : undefined,
                    period_start: selectedPeriod.start,
                    period_end: selectedPeriod.end,
                }),
            });
            onCreated();
        } catch (e: any) {
            setError(e.message ?? 'Error');
        } finally {
            setSaving(false);
        }
    }

    return (
        <div style={{
            padding: '16px',
            background: 'var(--bg-primary)',
            border: '1px solid var(--accent-primary)40',
            borderRadius: '10px',
            display: 'flex', flexDirection: 'column', gap: '12px',
        }}>
            <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                {isChinese ? '新建目标' : 'New Objective'}
            </div>
            <input
                type="text"
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder={isChinese ? '目标标题，例如：提升用户体验' : 'e.g. Improve customer experience'}
                autoFocus
                style={{
                    padding: '8px 12px',
                    background: 'var(--bg-secondary)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: '6px', color: 'var(--text-primary)',
                    fontSize: '14px',
                }}
                onKeyDown={e => { if (e.key === 'Enter') handleSubmit(); if (e.key === 'Escape') onCancel(); }}
            />
            <textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder={isChinese ? '说明（可选）' : 'Description (optional)'}
                rows={2}
                style={{
                    padding: '8px 12px',
                    background: 'var(--bg-secondary)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: '6px', color: 'var(--text-primary)',
                    fontSize: '13px', resize: 'vertical',
                    fontFamily: 'inherit',
                }}
            />
            {isAdmin && (
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {isChinese ? '层级' : 'Level'}
                    </span>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer' }}>
                        <input type="radio" name="ownerType" value="company" checked={ownerType === 'company'} onChange={() => setOwnerType('company')} />
                        <span style={{ fontSize: '12px', color: 'var(--text-primary)' }}>{isChinese ? '公司级' : 'Company'}</span>
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer' }}>
                        <input type="radio" name="ownerType" value="user" checked={ownerType === 'user'} onChange={() => setOwnerType('user')} />
                        <span style={{ fontSize: '12px', color: 'var(--text-primary)' }}>{isChinese ? '个人' : 'Personal'}</span>
                    </label>
                </div>
            )}
            {error && <div style={{ fontSize: '12px', color: '#ef4444' }}>{error}</div>}
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                <button onClick={onCancel} style={{ padding: '7px 14px', borderRadius: '6px', border: '1px solid var(--border-subtle)', background: 'none', color: 'var(--text-secondary)', fontSize: '13px', cursor: 'pointer' }}>
                    {isChinese ? '取消' : 'Cancel'}
                </button>
                <button onClick={handleSubmit} disabled={saving} style={{ padding: '7px 14px', borderRadius: '6px', border: 'none', background: 'var(--accent-primary)', color: '#fff', fontSize: '13px', cursor: saving ? 'wait' : 'pointer', opacity: saving ? 0.7 : 1 }}>
                    {saving ? (isChinese ? '创建中...' : 'Creating...') : (isChinese ? '创建目标' : 'Create')}
                </button>
            </div>
        </div>
    );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function OKR() {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const user = useAuthStore(s => s.user);
    const isChinese = i18n.language?.startsWith('zh');
    const isAdmin = user && ['platform_admin', 'org_admin'].includes(user.role);
    const okrRoleMode = isAdmin ? 'admin' : 'member';
    const queryClient = useQueryClient();

    const [selectedPeriod, setSelectedPeriod] = useState<Period | null>(null);
    const [creating, setCreating] = useState(false);
    const [activeTab, setActiveTab] = useState<'dashboards' | 'reports'>('dashboards');
    const [periodMenuOpen, setPeriodMenuOpen] = useState(false);
    const periodMenuRef = useRef<HTMLDivElement | null>(null);

    // Fetch OKR settings — always fresh on mount so toggling the switch is reflected immediately
    const { data: settings, isLoading: settingsLoading } = useQuery<OKRSettings>({
        queryKey: ['okr-settings'],
        queryFn: () => fetchJson<OKRSettings>('/okr/settings'),
        staleTime: 0,
        refetchOnWindowFocus: true,
    });

    // Fetch periods (only when enabled)
    const { data: periods = [] } = useQuery<Period[]>({
        queryKey: ['okr-periods'],
        queryFn: () => fetchJson<Period[]>('/okr/periods'),
        enabled: !!settings?.enabled,
    });

    // Auto-select the current period, and keep the selected object fresh when
    // the period list is reloaded after settings or time-boundary changes.
    useEffect(() => {
        if (periods.length === 0) return;
        const selectedStillExists = selectedPeriod
            ? periods.find(p => p.start === selectedPeriod.start && p.end === selectedPeriod.end)
            : null;
        if (!selectedPeriod || !selectedStillExists) {
            const current = periods.find(p => p.is_current) ?? periods[periods.length - 1];
            setSelectedPeriod(current);
        } else if (selectedStillExists !== selectedPeriod) {
            setSelectedPeriod(selectedStillExists);
        }
    }, [periods, selectedPeriod]);

    useEffect(() => {
        if (!periodMenuOpen) return;
        function handlePointerDown(event: MouseEvent) {
            if (periodMenuRef.current && !periodMenuRef.current.contains(event.target as Node)) {
                setPeriodMenuOpen(false);
            }
        }
        document.addEventListener('mousedown', handlePointerDown);
        return () => document.removeEventListener('mousedown', handlePointerDown);
    }, [periodMenuOpen]);

    // Fetch objectives for selected period — fresh on mount/focus so OKR Agent creation is visible
    const { data: objectives = [], isLoading: objLoading } = useQuery<Objective[]>({
        queryKey: ['okr-objectives', selectedPeriod?.start, selectedPeriod?.end],
        queryFn: () => fetchJson<Objective[]>(
            `/okr/objectives?period_start=${selectedPeriod!.start}&period_end=${selectedPeriod!.end}`
        ),
        enabled: !!settings?.enabled && !!selectedPeriod,
        staleTime: 0,
        refetchOnWindowFocus: true,
    });

    function invalidateObjectives() {
        queryClient.invalidateQueries({ queryKey: ['okr-objectives'] });
    }

    // ── Loading ─────────────────────────────────────────────────────────────
    if (settingsLoading) {
        return (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '50vh', color: 'var(--text-tertiary)' }}>
                {isChinese ? '加载中...' : 'Loading...'}
            </div>
        );
    }

    // ── Disabled guide panel ─────────────────────────────────────────────────
    if (!settings?.enabled) {
        return (
            <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                height: '70vh', gap: '16px', color: 'var(--text-secondary)', textAlign: 'center', padding: '24px',
            }}>
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
                        id="okr-enable-btn"
                        className="btn btn-primary"
                        onClick={() => navigate('/enterprise#okr')}
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

    // ── Enabled OKR dashboard ────────────────────────────────────────────────
    const companyObjs = objectives.filter(o => o.owner_type === 'company');
    const memberObjs = objectives.filter(o => o.owner_type !== 'company');

    // Group member objectives by owner — use owner_name as the display label
    const memberGroups: Record<string, { label: string; objs: Objective[] }> = {};
    for (const obj of memberObjs) {
        const key = `${obj.owner_type}:${obj.owner_id ?? ''}`;
        if (!memberGroups[key]) {
            // Prefer resolved name; fall back to a readable placeholder
            const label = obj.owner_name || '?';
            memberGroups[key] = { label, objs: [] };
        }
        memberGroups[key].objs.push(obj);
    }
    const periodOptions = periods;

    return (
        <div data-okr-role-mode={okrRoleMode} style={{ padding: '24px', maxWidth: 960, margin: '0 auto' }}>
            {/* Page Header */}
            <div style={{
                display: 'grid',
                gridTemplateColumns: 'minmax(0, 1fr) auto minmax(0, 1fr)',
                alignItems: 'center',
                marginBottom: '24px',
                gap: '12px',
            }}>
                <div>
                    <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 700, color: 'var(--text-primary)' }}>
                        {t('okr.title', 'OKR')}
                    </h1>
                    <div style={{ fontSize: '13px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                        {isChinese ? '目标与关键成果' : 'Objectives & Key Results'}
                    </div>
                </div>

                <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    height: 38,
                    background: 'var(--bg-secondary)',
                    padding: '2px',
                    borderRadius: '8px',
                    justifySelf: 'center',
                    alignSelf: 'start',
                    marginTop: '2px',
                }}>
                    <button
                        onClick={() => setActiveTab('dashboards')}
                        style={{
                            padding: '6px 16px', borderRadius: '6px', fontSize: '13px', fontWeight: 500,
                            background: activeTab === 'dashboards' ? 'var(--bg-primary)' : 'transparent',
                            color: activeTab === 'dashboards' ? 'var(--text-primary)' : 'var(--text-secondary)',
                            boxShadow: activeTab === 'dashboards' ? '0 1px 2px rgba(0,0,0,0.05)' : 'none',
                            border: 'none', cursor: 'pointer', transition: 'all 0.15s',
                        }}
                    >
                        {isChinese ? '概览' : 'Dashboard'}
                    </button>
                    <button
                        onClick={() => setActiveTab('reports')}
                        style={{
                            padding: '6px 16px', borderRadius: '6px', fontSize: '13px', fontWeight: 500,
                            background: activeTab === 'reports' ? 'var(--bg-primary)' : 'transparent',
                            color: activeTab === 'reports' ? 'var(--text-primary)' : 'var(--text-secondary)',
                            boxShadow: activeTab === 'reports' ? '0 1px 2px rgba(0,0,0,0.05)' : 'none',
                            border: 'none', cursor: 'pointer', transition: 'all 0.15s',
                        }}
                    >
                        {isChinese ? '工作汇报' : 'Reports'}
                    </button>
                </div>

                <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'flex-end',
                    gap: '12px',
                    flexWrap: 'wrap',
                    minHeight: 38,
                    alignSelf: 'start',
                }}>
                {activeTab === 'dashboards' && (
                    <>
                    {/* Period Selector */}
                    {periods.length > 0 && (
                        <div ref={periodMenuRef} style={{ display: 'flex', alignItems: 'center', gap: '8px', position: 'relative' }}>
                            <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                                {isChinese ? '周期' : 'Period'}
                            </span>
                            <button
                                type="button"
                                onClick={() => setPeriodMenuOpen(v => !v)}
                                style={{
                                    minWidth: 170, height: 34, padding: '5px 10px', borderRadius: '6px',
                                    border: '1px solid var(--border-subtle)', background: 'var(--bg-secondary)',
                                    color: 'var(--text-secondary)', fontSize: '12px', display: 'flex',
                                    alignItems: 'center', justifyContent: 'space-between', gap: '10px', cursor: 'pointer',
                                }}
                            >
                                <span>
                                    {selectedPeriod?.label}
                                    {selectedPeriod?.is_current ? (isChinese ? '（当前）' : ' (now)') : ''}
                                </span>
                                <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>▾</span>
                            </button>
                            {periodMenuOpen && (
                                <div
                                    style={{
                                        position: 'absolute', top: 'calc(100% + 6px)', right: 0, zIndex: 50,
                                        minWidth: 210, maxHeight: 280, overflowY: 'auto', padding: '6px',
                                        borderRadius: '8px', border: '1px solid var(--border-subtle)',
                                        background: 'var(--bg-primary)', boxShadow: '0 12px 32px rgba(0,0,0,0.16)',
                                    }}
                                >
                                    {periodOptions.map(p => {
                                        const active = selectedPeriod?.start === p.start && selectedPeriod?.end === p.end;
                                        return (
                                            <button
                                                key={p.start}
                                                type="button"
                                                onClick={() => {
                                                    setSelectedPeriod(p);
                                                    setPeriodMenuOpen(false);
                                                }}
                                                style={{
                                                    width: '100%', padding: '8px 10px', borderRadius: '6px', border: 'none',
                                                    background: active ? 'var(--accent-primary)' : 'transparent',
                                                    color: active ? '#fff' : 'var(--text-secondary)', fontSize: '12px',
                                                    textAlign: 'left', cursor: 'pointer', display: 'flex',
                                                    justifyContent: 'space-between', alignItems: 'center', gap: '12px',
                                                }}
                                            >
                                                <span>{p.label}{p.is_current ? (isChinese ? '（当前）' : ' (now)') : ''}</span>
                                                {active && <span>✓</span>}
                                            </button>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Create Objective button */}
                    {isAdmin && selectedPeriod && !creating && (
                        <button
                            id="create-objective-btn"
                            onClick={() => setCreating(true)}
                            style={{
                                display: 'flex', alignItems: 'center', gap: '6px',
                                padding: '7px 14px', borderRadius: '6px',
                                border: 'none', background: 'var(--accent-primary)',
                                color: '#fff', fontSize: '13px', cursor: 'pointer',
                                fontWeight: 500, transition: 'opacity 0.15s',
                            }}
                            onMouseEnter={e => (e.currentTarget as HTMLButtonElement).style.opacity = '0.85'}
                            onMouseLeave={e => (e.currentTarget as HTMLButtonElement).style.opacity = '1'}
                        >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                            {isChinese ? '新建目标' : 'New Objective'}
                        </button>
                    )}
                    </>
                )}
                </div>
            </div>

            {activeTab === 'dashboards' && (
                <>
                    {/* Create Objective form */}
                    {creating && selectedPeriod && (
                        <div style={{ marginBottom: '24px' }}>
                            <CreateObjectiveForm
                                isChinese={isChinese}
                                isAdmin={!!isAdmin}
                                userId={user?.id ?? ''}
                                selectedPeriod={selectedPeriod}
                                onCreated={() => { setCreating(false); invalidateObjectives(); }}
                                onCancel={() => setCreating(false)}
                            />
                        </div>
                    )}

                    {/* Loading */}
                    {objLoading && (
                        <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                            {isChinese ? '加载中...' : 'Loading...'}
                        </div>
                    )}

                    {/* Empty state */}
                    {!objLoading && objectives.length === 0 && (
                        <div style={{
                            textAlign: 'center', padding: '60px 24px',
                            border: '1px dashed var(--border-subtle)', borderRadius: '12px',
                            color: 'var(--text-tertiary)', fontSize: '13px',
                        }}>
                            {isChinese
                                ? (isAdmin
                                    ? '当前周期暂无 OKR。点击右上角「新建目标」或联系 OKR Agent 来设定目标。'
                                    : '当前周期暂无 OKR。请联系 OKR Agent 来设定目标。')
                                : (isAdmin
                                    ? 'No OKRs for this period yet. Click "New Objective" or ask the OKR Agent.'
                                    : 'No OKRs for this period yet. Please ask the OKR Agent to set them up.')}
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
                                <span style={{ fontSize: '11px', color: 'var(--text-quaternary)' }}>{companyObjs.length}</span>
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                {companyObjs.map(obj => (
                                    <ObjectiveCard
                                        key={obj.id}
                                        obj={obj}
                                        isChinese={isChinese}
                                        canEdit={!!isAdmin}
                                        onInvalidate={invalidateObjectives}
                                        onDelete={async (id) => {
                                            await fetchJson(`/okr/objectives/${id}`, { method: 'DELETE' });
                                            invalidateObjectives();
                                        }}
                                    />
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
                                <span style={{ fontSize: '11px', color: 'var(--text-quaternary)' }}>{memberObjs.length}</span>
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                                {Object.entries(memberGroups).map(([ownerKey, group]) => (
                                    <div
                                        key={ownerKey}
                                        style={{
                                            border: '1px solid var(--border-subtle)',
                                            borderRadius: '14px',
                                            background: 'var(--bg-primary)',
                                            padding: '14px',
                                        }}
                                    >
                                        <div style={{
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'space-between',
                                            gap: '12px',
                                            marginBottom: '12px',
                                        }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
                                                <div style={{
                                                    width: '28px',
                                                    height: '28px',
                                                    borderRadius: '999px',
                                                    background: 'var(--bg-secondary)',
                                                    border: '1px solid var(--border-subtle)',
                                                    color: 'var(--text-secondary)',
                                                    display: 'flex',
                                                    alignItems: 'center',
                                                    justifyContent: 'center',
                                                    fontSize: '13px',
                                                    fontWeight: 700,
                                                    flexShrink: 0,
                                                }}>
                                                    {group.label.slice(0, 1).toUpperCase()}
                                                </div>
                                                <div style={{
                                                    fontSize: '14px',
                                                    fontWeight: 700,
                                                    color: 'var(--text-primary)',
                                                    minWidth: 0,
                                                }}>
                                                    {group.label}
                                                </div>
                                                <span style={{
                                                    fontSize: '11px',
                                                    color: 'var(--text-tertiary)',
                                                    border: '1px solid var(--border-subtle)',
                                                    borderRadius: '999px',
                                                    padding: '2px 8px',
                                                    whiteSpace: 'nowrap',
                                                }}>
                                                    {group.objs.length} {isChinese ? '个目标' : 'objectives'}
                                                </span>
                                            </div>
                                        </div>

                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                            {group.objs.map(obj => (
                                                <ObjectiveCard
                                                    key={obj.id}
                                                    obj={obj}
                                                    isChinese={isChinese}
                                                    canEdit={!!isAdmin}
                                                    onInvalidate={invalidateObjectives}
                                                    onDelete={async (id) => {
                                                        await fetchJson(`/okr/objectives/${id}`, { method: 'DELETE' });
                                                        invalidateObjectives();
                                                    }}
                                                />
                                            ))}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </section>
                    )}
                    {/* Members Without OKR / Nudge panel (admin-only) */}
                    {isAdmin && selectedPeriod && (
                        <MembersWithoutOKRPanel
                            isChinese={isChinese}
                            periodStart={selectedPeriod.start}
                            periodEnd={selectedPeriod.end}
                        />
                    )}
                </>
            )}

            {activeTab === 'reports' && (
                <ReportsTab isChinese={isChinese} />
            )}
        </div>
    );
}

// ─── Members Without OKR Panel (admin-only) ────────────────────────────────────────────
// Shows admin a list of members who haven’t set OKRs yet, with a nudge button.
function MembersWithoutOKRPanel({
    isChinese,
    periodStart,
    periodEnd,
}: {
    isChinese: boolean;
    periodStart: string;
    periodEnd: string;
}) {
    const queryClient = useQueryClient();
    const [nudging, setNudging] = React.useState(false);
    const [nudgeResult, setNudgeResult] = React.useState<string | null>(null);

    // Always refetch on mount — list must be live after admin adds/removes OKR Agent relationships
    const { data, isLoading } = useQuery<MembersWithoutOKRData>({
        queryKey: ['okr-members-without-okr', periodStart, periodEnd],
        queryFn: () => fetchJson<MembersWithoutOKRData>('/okr/members-without-okr'),
        staleTime: 0,
        refetchOnWindowFocus: true,
    });

    // When a background failure is detected via refetch, clear the stale
    // success message so the error banner becomes visible automatically.
    React.useEffect(() => {
        if (data?.last_outreach_error && nudgeResult) {
            setNudgeResult(null);
        }
    }, [data?.last_outreach_error, nudgeResult]);

    // Don't render when loading or no incomplete members
    if (isLoading || !data || !data.members_without_okr?.length) {
        return null;
    }

    async function handleNudge() {
        setNudging(true);
        setNudgeResult(null);
        try {
            const result = await fetchJson<{ status: string; message: string; members_count: number }>(
                '/okr/trigger-member-outreach',
                { method: 'POST' }
            );
            setNudgeResult(result.message);
            queryClient.invalidateQueries({ queryKey: ['okr-members-without-okr'] });

            // The background task may fail asynchronously (e.g. LLM API key invalid).
            // Schedule delayed re-fetches so the error banner appears automatically
            // without requiring a manual page refresh.
            setTimeout(() => queryClient.invalidateQueries({ queryKey: ['okr-members-without-okr'] }), 5000);
            setTimeout(() => queryClient.invalidateQueries({ queryKey: ['okr-members-without-okr'] }), 12000);
        } catch (e: any) {
            setNudgeResult(e.message ?? (isChinese ? '催促失败，请重试' : 'Failed to trigger outreach'));
        } finally {
            setNudging(false);
        }
    }

    const { members_without_okr: members, company_okr_exists, okr_agent_id, last_outreach_error, channel_warnings } = data;

    // Build a set of unreachable member names for warning icon display
    const unreachableNames = new Set<string>();
    if (channel_warnings?.length) {
        for (const w of channel_warnings) {
            for (const name of w.affected_members) {
                unreachableNames.add(name);
            }
        }
    }

    return (
        <section style={{ marginTop: '32px' }}>
            {/* Section header: title + count + divider only */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.8px' }}>
                    {isChinese ? '未设定 OKR 的成员' : 'Members Without OKR'}
                </span>
                <div style={{ flex: 1, height: '1px', background: 'var(--border-subtle)' }} />
                <span style={{ fontSize: '11px', color: 'var(--text-quaternary)' }}>{members.length}</span>
            </div>

            {/* Channel misconfiguration warning */}
            {channel_warnings && channel_warnings.length > 0 && (
                <div style={{
                    display: 'flex', alignItems: 'flex-start', gap: '8px',
                    padding: '10px 14px',
                    marginBottom: '12px',
                    background: 'rgba(245, 158, 11, 0.06)',
                    border: '1px solid rgba(245, 158, 11, 0.2)',
                    borderRadius: '8px',
                    fontSize: '12px',
                    color: '#92400e',
                    lineHeight: 1.6,
                }}>
                    <IconAlertTriangle size={14} stroke={1.8} style={{ flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                        {channel_warnings.map((w, i) => (
                            <div key={i} style={{ marginBottom: i < channel_warnings.length - 1 ? '4px' : 0 }}>
                                <span style={{ fontWeight: 600 }}>{w.affected_members.join('、')}</span>
                                {' '}
                                {isChinese
                                    ? `为 ${w.channel_display} 用户，但该 Agent 未配置 ${w.channel_display} 机器人，催办消息无法送达。`
                                    : `are on ${w.channel_display} but the Agent has no ${w.channel_display} bot configured. Nudge messages cannot be delivered.`}
                            </div>
                        ))}
                        {okr_agent_id && (
                            <a
                                href={`/agents/${okr_agent_id}#settings`}
                                style={{
                                    fontSize: '11px', color: '#d97706',
                                    textDecoration: 'none', fontWeight: 500,
                                    display: 'inline-block', marginTop: '2px',
                                }}
                            >
                                {isChinese ? '前往 Agent Settings 配置 →' : 'Configure in Agent Settings →'}
                            </a>
                        )}
                    </div>
                </div>
            )}

            {/* Nudge button + description — below title, above member list */}
            <div style={{
                display: 'flex', alignItems: 'center', gap: '16px',
                marginBottom: '12px',
                padding: '10px 14px',
                background: 'var(--bg-secondary)',
                borderRadius: '8px',
                border: '1px solid var(--border-subtle)',
            }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', lineHeight: 1.6 }}>
                        {company_okr_exists
                            ? (isChinese
                                ? 'OKR Agent 将向以上成员发送消息，邀请他们设定个人 OKR。发送后可在 OKR Agent 的会话列表里查看具体对话记录。'
                                : 'OKR Agent will message each member above. You can review the conversations in the OKR Agent\'s chat history.')
                            : (isChinese
                                ? '请先与 OKR Agent 确认公司 OKR，再催促成员。'
                                : 'Please set company OKRs with the OKR Agent before nudging members.')
                        }
                    </div>
                    {nudgeResult && (
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '6px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span>{nudgeResult}</span>
                            {okr_agent_id && (
                                <a
                                    href={`/agents/${okr_agent_id}`}
                                    style={{ fontSize: '11px', color: 'var(--accent-primary)', textDecoration: 'none', whiteSpace: 'nowrap' }}
                                >
                                    {isChinese ? '查看会话 →' : 'View chat →'}
                                </a>
                            )}
                        </div>
                    )}
                    {/* Show error banner if the last background outreach task failed */}
                    {!nudgeResult && last_outreach_error && (
                        <div style={{
                            fontSize: '12px',
                            color: '#b91c1c',
                            background: 'rgba(239,68,68,0.08)',
                            border: '1px solid rgba(239,68,68,0.2)',
                            borderRadius: '6px',
                            padding: '8px 10px',
                            marginTop: '8px',
                            lineHeight: 1.5,
                            display: 'flex',
                            alignItems: 'flex-start',
                            gap: '6px',
                        }}>
                            <IconAlertTriangle size={14} stroke={1.8} style={{ flexShrink: 0 }} />
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontWeight: 600, marginBottom: '2px' }}>
                                    {isChinese ? 'OKR Agent 上次执行失败' : 'OKR Agent task failed'}
                                </div>
                                <div style={{ color: '#991b1b', wordBreak: 'break-word' }}>
                                    {last_outreach_error.message}
                                </div>
                                {last_outreach_error.timestamp && (
                                    <div style={{ fontSize: '11px', color: '#b45309', marginTop: '4px' }}>
                                        {new Date(last_outreach_error.timestamp).toLocaleString()}
                                    </div>
                                )}
                                {okr_agent_id && (
                                    <a
                                        href={`/agents/${okr_agent_id}#settings`}
                                        style={{ fontSize: '11px', color: 'var(--accent-primary)', textDecoration: 'none', marginTop: '4px', display: 'inline-block' }}
                                    >
                                        {isChinese ? '检查 Agent 设置 →' : 'Check Agent Settings →'}
                                    </a>
                                )}
                            </div>
                        </div>
                    )}
                </div>
                <button
                    id="okr-nudge-btn"
                    onClick={handleNudge}
                    disabled={nudging || !company_okr_exists}
                    title={company_okr_exists ? undefined : (isChinese ? '请先设定公司 OKR' : 'Set company OKRs first')}
                    style={{
                        display: 'flex', alignItems: 'center', gap: '6px',
                        padding: '5px 12px', borderRadius: '6px',
                        border: 'none', flexShrink: 0,
                        background: company_okr_exists ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                        color: company_okr_exists ? '#fff' : 'var(--text-quaternary)',
                        fontSize: '12px', fontWeight: 500,
                        cursor: nudging || !company_okr_exists ? 'not-allowed' : 'pointer',
                        opacity: nudging ? 0.7 : 1,
                        transition: 'opacity 0.15s, background 0.15s',
                        whiteSpace: 'nowrap',
                    }}
                >
                    {nudging ? (
                        <>{isChinese ? '发送中...' : 'Sending...'}</>
                    ) : (
                        <>
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M22 2L11 13"/><path d="M22 2L15 22 11 13 2 9l20-7z"/>
                            </svg>
                            {isChinese ? '催促设定 OKR' : 'Nudge Members'}
                        </>
                    )}
                </button>
            </div>

            <div style={{
                border: '1px solid var(--border-subtle)',
                borderRadius: '10px',
                overflow: 'hidden',
                background: 'var(--bg-primary)',
            }}>
                {/* Member list */}
                <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {members.map((member) => (
                        <div key={member.id} style={{
                            display: 'flex', alignItems: 'center', gap: '10px',
                            padding: '8px 10px',
                            background: 'var(--bg-secondary)',
                            borderRadius: '6px',
                            border: '1px solid var(--border-subtle)',
                        }}>
                            <div style={{
                                width: 28, height: 28, borderRadius: '50%', flexShrink: 0,
                                background: member.type === 'agent' ? 'rgba(99,102,241,0.15)' : 'rgba(16,185,129,0.15)',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                fontSize: '11px', fontWeight: 600,
                                color: member.type === 'agent' ? '#6366f1' : '#10b981',
                            }}>
                                {(member.display_name || '?').charAt(0).toUpperCase()}
                            </div>
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
                                    {member.display_name}
                                </div>
                                <div style={{ fontSize: '11px', color: 'var(--text-quaternary)' }}>
                                    {member.type === 'agent'
                                        ? 'AI Agent'
                                        : (member.source_label
                                            ? (member.source_label === 'Platform User'
                                                ? (isChinese ? '平台成员' : 'Platform member')
                                                : member.source_label)
                                            : (isChinese ? '平台成员' : 'Platform member'))}
                                </div>
                            </div>
                            {unreachableNames.has(member.display_name) && (
                                <span title={isChinese ? '渠道未配置，无法推送' : 'Channel not configured, cannot deliver'}
                                    style={{ cursor: 'help', flexShrink: 0, display: 'inline-flex' }}><IconAlertTriangle size={13} stroke={1.8} /></span>
                            )}
                            <span style={{
                                fontSize: '10px', color: 'var(--text-tertiary)',
                                border: '1px dashed var(--border-subtle)',
                                borderRadius: '4px', padding: '1px 6px',
                            }}>
                                {isChinese ? '未设定 OKR' : 'No OKR'}
                            </span>
                        </div>
                    ))}
                </div>
            </div>
        </section>
    );
}
function ReportsTab({ isChinese }: { isChinese: boolean }) {
    const qc = useQueryClient();
    const currentUser = useAuthStore((s) => s.user);
    const [view, setView] = useState<'company' | 'member'>('company');
    const [reportType, setReportType] = useState<'daily' | 'weekly' | 'monthly'>('daily');
    const [selectedDate, setSelectedDate] = useState(() => new Date().toISOString().slice(0, 10));
    const [expandedCompanyReportId, setExpandedCompanyReportId] = useState<string | null>(null);
    const [selectedMemberReportId, setSelectedMemberReportId] = useState<string | null>(null);
    const [memberSearch, setMemberSearch] = useState('');
    const isAdmin = currentUser?.role === 'org_admin' || currentUser?.role === 'platform_admin';

    const { data: companyReports = [], isLoading: companyLoading } = useQuery<CompanyReport[]>({
        queryKey: ['company-reports', reportType],
        queryFn: () => fetchJson<CompanyReport[]>(`/okr/company-reports?report_type=${reportType}`),
        enabled: view === 'company',
    });

    const { data: memberReports = [], isLoading: memberLoading } = useQuery<MemberDailyReportItem[]>({
        queryKey: ['member-daily-reports', selectedDate],
        queryFn: () => fetchJson<MemberDailyReportItem[]>(`/okr/member-daily-reports?report_date=${selectedDate}`),
        enabled: view === 'member',
    });

    const regenerateMutation = useMutation({
        mutationFn: (payload: { report_type: string; period_start: string }) =>
            fetchJson<CompanyReport>('/okr/company-reports/regenerate', {
                method: 'POST',
                body: JSON.stringify(payload),
            }),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['company-reports'] });
        },
    });

    useEffect(() => {
        if (!companyReports.length) {
            setExpandedCompanyReportId(null);
            return;
        }
        if (!expandedCompanyReportId || !companyReports.some(report => report.id === expandedCompanyReportId)) {
            setExpandedCompanyReportId(companyReports[0].id);
        }
    }, [companyReports, expandedCompanyReportId]);

    const filteredMemberReports = useMemo(() => {
        const keyword = memberSearch.trim().toLowerCase();
        if (!keyword) return memberReports;
        return memberReports.filter(report =>
            report.display_name.toLowerCase().includes(keyword)
            || report.group_label.toLowerCase().includes(keyword)
        );
    }, [memberReports, memberSearch]);

    useEffect(() => {
        if (!filteredMemberReports.length) {
            setSelectedMemberReportId(null);
            return;
        }
        if (!selectedMemberReportId || !filteredMemberReports.some(report => report.id === selectedMemberReportId)) {
            setSelectedMemberReportId(filteredMemberReports[0].id);
        }
    }, [filteredMemberReports, selectedMemberReportId]);

    const selectedMemberReport = filteredMemberReports.find(report => report.id === selectedMemberReportId) || filteredMemberReports[0] || null;

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '16px',
                flexWrap: 'wrap',
            }}>
                <div style={{
                    display: 'inline-flex',
                    padding: '4px',
                    borderRadius: '10px',
                    background: 'var(--bg-secondary)',
                    border: '1px solid var(--border-subtle)',
                }}>
                    {[
                        { key: 'company', zh: '公司汇总', en: 'Company Reports' },
                        { key: 'member', zh: '成员日报', en: 'Member Daily Reports' },
                    ].map(item => (
                        <button
                            key={item.key}
                            onClick={() => setView(item.key as 'company' | 'member')}
                            style={{
                                border: 'none',
                                borderRadius: '8px',
                                padding: '8px 14px',
                                background: view === item.key ? 'var(--bg-primary)' : 'transparent',
                                color: view === item.key ? 'var(--text-primary)' : 'var(--text-secondary)',
                                fontSize: '13px',
                                fontWeight: 600,
                                cursor: 'pointer',
                                boxShadow: view === item.key ? '0 1px 4px rgba(0,0,0,0.06)' : 'none',
                            }}
                        >
                            {isChinese ? item.zh : item.en}
                        </button>
                    ))}
                </div>

                {view === 'company' ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                        <div style={{ display: 'inline-flex', gap: '6px' }}>
                            {[
                                { key: 'daily', zh: '日报', en: 'Daily' },
                                { key: 'weekly', zh: '周报', en: 'Weekly' },
                                { key: 'monthly', zh: '月报', en: 'Monthly' },
                            ].map(item => (
                                <button
                                    key={item.key}
                                    onClick={() => setReportType(item.key as 'daily' | 'weekly' | 'monthly')}
                                    style={{
                                        border: `1px solid ${reportType === item.key ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                                        borderRadius: '8px',
                                        padding: '7px 12px',
                                        background: reportType === item.key ? 'rgba(99,102,241,0.08)' : 'var(--bg-primary)',
                                        color: reportType === item.key ? 'var(--accent-primary)' : 'var(--text-secondary)',
                                        fontSize: '12px',
                                        fontWeight: 600,
                                        cursor: 'pointer',
                                    }}
                                >
                                    {isChinese ? item.zh : item.en}
                                </button>
                            ))}
                        </div>
                    </div>
                ) : (
                    <input
                        type="date"
                        className="form-input"
                        value={selectedDate}
                        onChange={e => setSelectedDate(e.target.value)}
                        style={{ width: '180px' }}
                    />
                )}
            </div>

            {view === 'company' ? (
                companyLoading ? (
                    <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                        {isChinese ? '加载中...' : 'Loading...'}
                    </div>
                ) : companyReports.length ? (
                    <div style={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '16px',
                    }}>
                        {companyReports.map(report => {
                            const expanded = expandedCompanyReportId === report.id;
                            const showMissing = report.missing_count > 0;
                            const showRefresh = report.needs_refresh && isAdmin;
                            return (
                                <div
                                    key={report.id}
                                    style={{
                                        background: 'var(--bg-primary)',
                                        border: `1px solid ${expanded ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                                        borderRadius: '12px',
                                        overflow: 'hidden',
                                    }}
                                >
                                    <button
                                        onClick={() => setExpandedCompanyReportId(report.id)}
                                        style={{
                                            width: '100%',
                                            textAlign: 'left',
                                            background: 'transparent',
                                            border: 'none',
                                            padding: '16px 18px',
                                            cursor: 'pointer',
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'space-between',
                                            gap: '16px',
                                        }}
                                    >
                                        <div style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text-primary)' }}>
                                            {report.period_label}
                                        </div>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                                            {showMissing && (
                                                <span style={{
                                                    fontSize: '11px',
                                                    fontWeight: 600,
                                                    color: '#b45309',
                                                    background: 'rgba(245,158,11,0.12)',
                                                    border: '1px solid rgba(245,158,11,0.25)',
                                                    padding: '2px 8px',
                                                    borderRadius: '999px',
                                                }}>
                                                    {isChinese ? `${report.missing_count} 人缺交` : `${report.missing_count} missing`}
                                                </span>
                                            )}
                                            {report.needs_refresh && (
                                                <span style={{
                                                    fontSize: '11px',
                                                    fontWeight: 600,
                                                    color: '#a16207',
                                                    background: 'rgba(245,158,11,0.10)',
                                                    border: '1px solid rgba(245,158,11,0.22)',
                                                    padding: '2px 8px',
                                                    borderRadius: '999px',
                                                }}>
                                                    {isChinese ? '有补交更新' : 'Updated submissions'}
                                                </span>
                                            )}
                                            <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                                                {expanded ? '−' : '+'}
                                            </span>
                                        </div>
                                    </button>

                                    {expanded && (
                                        <div style={{
                                            padding: '0 18px 18px',
                                            borderTop: '1px solid var(--border-subtle)',
                                            display: 'flex',
                                            flexDirection: 'column',
                                            gap: '12px',
                                        }}>
                                            {showRefresh && (
                                                <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: '12px' }}>
                                                    <button
                                                        onClick={(e) => {
                                                            e.stopPropagation();
                                                            regenerateMutation.mutate({
                                                                report_type: report.report_type,
                                                                period_start: report.period_start,
                                                            });
                                                        }}
                                                        disabled={regenerateMutation.isPending}
                                                        className="btn btn-secondary"
                                                        style={{ fontSize: '12px' }}
                                                    >
                                                        {regenerateMutation.isPending
                                                            ? (isChinese ? '重新汇总中...' : 'Regenerating...')
                                                            : (isChinese ? '重新汇总' : 'Regenerate')}
                                                    </button>
                                                </div>
                                            )}

                                            <pre style={{
                                                margin: 0,
                                                whiteSpace: 'pre-wrap',
                                                wordBreak: 'break-word',
                                                fontSize: '13px',
                                                lineHeight: '1.7',
                                                color: 'var(--text-secondary)',
                                                fontFamily: 'inherit',
                                            }}>
                                                {report.content}
                                            </pre>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                ) : (
                    <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: '13px', background: 'var(--bg-secondary)', borderRadius: '12px' }}>
                        {isChinese ? '暂无公司级汇总报告。' : 'No company reports yet.'}
                    </div>
                )
            ) : memberLoading ? (
                <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                    {isChinese ? '加载中...' : 'Loading...'}
                </div>
            ) : (
                <div style={{
                    border: '1px solid var(--border-subtle)',
                    borderRadius: '12px',
                    overflow: 'hidden',
                    background: 'var(--bg-primary)',
                }}>
                    <div style={{
                        display: 'grid',
                        gridTemplateColumns: '220px minmax(0, 1fr)',
                        minHeight: '480px',
                    }}>
                        <div style={{
                            borderRight: '1px solid var(--border-subtle)',
                            background: 'var(--bg-secondary)',
                            padding: '12px',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '8px',
                        }}>
                            <input
                                type="text"
                                className="form-input"
                                value={memberSearch}
                                onChange={e => setMemberSearch(e.target.value)}
                                placeholder={isChinese ? '搜索成员...' : 'Search members...'}
                                style={{ width: '100%' }}
                            />
                            {filteredMemberReports.length ? filteredMemberReports.map(item => (
                                <div
                                    key={item.id}
                                    onClick={() => setSelectedMemberReportId(item.id)}
                                    style={{
                                        padding: '10px 12px',
                                        borderRadius: '8px',
                                        border: `1px solid ${selectedMemberReport?.id === item.id ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                                        background: selectedMemberReport?.id === item.id ? 'rgba(99,102,241,0.06)' : 'var(--bg-primary)',
                                        cursor: 'pointer',
                                    }}
                                >
                                    <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                                        {item.display_name}
                                    </div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                        {item.group_label}
                                    </div>
                                    <div style={{ marginTop: '6px' }}>
                                        <span style={{
                                            fontSize: '10px',
                                            fontWeight: 600,
                                            color: item.status === 'missing' ? '#b45309' : 'var(--accent-primary)',
                                            background: item.status === 'missing' ? 'rgba(245,158,11,0.12)' : 'rgba(99,102,241,0.08)',
                                            borderRadius: '999px',
                                            padding: '2px 6px',
                                        }}>
                                            {item.status === 'missing'
                                                ? (isChinese ? '缺交' : 'Missing')
                                                : item.status === 'late'
                                                    ? (isChinese ? '补交' : 'Late')
                                                    : item.status === 'revised'
                                                        ? (isChinese ? '已修改' : 'Revised')
                                                        : (isChinese ? '已提交' : 'Submitted')}
                                        </span>
                                    </div>
                                </div>
                            )) : (
                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', padding: '8px 4px' }}>
                                    {isChinese ? '没有匹配的成员。' : 'No matching members.'}
                                </div>
                            )}
                        </div>

                        <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px', minWidth: 0 }}>
                            {selectedMemberReport ? (
                                <div
                                    key={`content-${selectedMemberReport.id}`}
                                    style={{
                                        padding: '14px 16px',
                                        border: '1px solid var(--border-subtle)',
                                        borderRadius: '10px',
                                        background: 'var(--bg-secondary)',
                                        minWidth: 0,
                                    }}
                                >
                                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', marginBottom: '8px', flexWrap: 'wrap' }}>
                                        <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                                            {selectedMemberReport.display_name}
                                        </div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                            {selectedMemberReport.updated_at ? new Date(selectedMemberReport.updated_at).toLocaleString() : ''}
                                        </div>
                                    </div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>
                                        {selectedMemberReport.group_label}
                                    </div>
                                    <div style={{
                                        fontSize: '13px',
                                        lineHeight: '1.7',
                                        color: selectedMemberReport.content ? 'var(--text-secondary)' : 'var(--text-tertiary)',
                                        whiteSpace: 'pre-wrap',
                                        wordBreak: 'break-word',
                                    }}>
                                        {selectedMemberReport.content || (isChinese ? '当天暂无日报提交。' : 'No daily report submitted for this day.')}
                                    </div>
                                </div>
                            ) : (
                                <div style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>
                                    {isChinese ? '当天暂无成员日报。' : 'No member daily reports for this day.'}
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
