/**
 * Experience entry detail drawer — shared by the Plaza page and the chat page.
 *
 * Chat renders it `docked` (no backdrop) so a citation opens the entry in place;
 * the conversation stays put and readable behind it. Plaza renders it modal.
 * Extracted from Plaza.tsx so both surfaces show the same detail, not two copies.
 */
import React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { experienceApi, type ExperienceEntry } from '../services/api';
import { Drawer, EXP_FIELDS, secondaryBtn } from './ExperienceDraftEditor';
import MarkdownRenderer from './MarkdownRenderer';

// 2026年7月9日; empty string for null/invalid.
export function fmtDate(s?: string | null): string {
    if (!s) return '';
    const d = new Date(s);
    if (isNaN(d.getTime())) return '';
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

export function freshness(entry: ExperienceEntry): { label: string; stale: boolean } {
    if (entry.status !== 'published') return { label: '', stale: false };
    if (!entry.last_reviewed_at) return { label: '未复核', stale: true };
    const d = new Date(entry.last_reviewed_at);
    const dateStr = `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
    const age = Date.now() - d.getTime();
    const stale = age > 90 * 86400000;
    return { label: `${stale ? '复核超期' : '已复核'}（${dateStr}）`, stale };
}

const RETIRED_TTL_DAYS = 30;
// Days left before a retired entry is auto-deleted (retired_at + 30d). null when not applicable.
export function retiredDaysLeft(entry: ExperienceEntry): number | null {
    if (entry.status !== 'retired' || !entry.retired_at) return null;
    const d = new Date(entry.retired_at);
    if (isNaN(d.getTime())) return null;
    const deadline = d.getTime() + RETIRED_TTL_DAYS * 86400000;
    return Math.max(0, Math.ceil((deadline - Date.now()) / 86400000));
}

const badgeStyle = (bg: string, fg: string): React.CSSProperties => ({
    display: 'inline-block', padding: '1px 7px', borderRadius: 10, fontSize: 11,
    background: bg, color: fg, whiteSpace: 'nowrap',
});

export function Badge({ children, tone = 'muted' }: { children: React.ReactNode; tone?: 'muted' | 'warn' | 'ok' | 'accent' }) {
    const tones: Record<string, [string, string]> = {
        muted: ['var(--bg-tertiary)', 'var(--text-secondary)'],
        warn: ['var(--error-subtle)', 'var(--error)'],
        ok: ['var(--success-subtle)', 'var(--success)'],
        accent: ['var(--accent-subtle)', 'var(--accent-text)'],
    };
    const [bg, fg] = tones[tone];
    return <span style={badgeStyle(bg, fg)}>{children}</span>;
}

/**
 * Marks a name as an agent rather than a person. Necessary because agents carry
 * ordinary human-looking names ("审核", "回款") — the name alone doesn't disclose it.
 */
export function AiBadge() {
    return (
        <span
            aria-label="AI"
            title="数字员工（AI）"
            style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: 16, height: 16, marginLeft: 4, flexShrink: 0,
                border: '1px solid currentColor', borderRadius: 3,
                fontSize: 9, fontWeight: 600, lineHeight: 1, letterSpacing: '.2px',
            }}
        >AI</span>
    );
}

// PRD v3: every entry shows its two creators — the human who published it + the source agent.
export function CreatorLine({ entry }: { entry: ExperienceEntry }) {
    const created = fmtDate(entry.created_at);
    // Never-modified entries have a null updated_at → fall back to created, so 上次修改 always shows.
    const updated = fmtDate(entry.updated_at) || created;
    const hasCreators = !!(entry.created_by_name || entry.origin_agent_name);
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-tertiary)', flexWrap: 'wrap' }}>
            {hasCreators && (
                <span style={{ display: 'inline-flex', alignItems: 'center' }}>
                    来源：{entry.created_by_name}
                    {entry.created_by_name && entry.origin_agent_name ? '；' : ''}
                    {entry.origin_agent_name}
                    {entry.origin_agent_name && <AiBadge />}
                </span>
            )}
            {hasCreators && created && <span style={{ opacity: .5 }}>·</span>}
            {created && <span>创建日期 {created}</span>}
            {updated && <span>上次修改 {updated}</span>}
        </div>
    );
}

export function EntryDrawer({ entryId, onClose, onEdit, onChanged, docked }: {
    entryId: string; onClose: () => void; onEdit: (e: ExperienceEntry) => void; onChanged: () => void;
    docked?: boolean;
}) {
    const qc = useQueryClient();
    const {
        data: entry,
        isPending: entryPending,
        isError: entryError,
    } = useQuery({ queryKey: ['experience-entry', entryId], queryFn: () => experienceApi.get(entryId), retry: false });
    const { data: refs } = useQuery({
        queryKey: ['experience-refs', entryId],
        queryFn: () => experienceApi.references(entryId),
        enabled: Boolean(entry),
        retry: false,
    });
    const retire = useMutation({ mutationFn: () => experienceApi.retire(entryId), onSuccess: () => { onChanged(); onClose(); } });
    const republish = useMutation({ mutationFn: () => experienceApi.publish(entryId), onSuccess: () => { onChanged(); onClose(); } });
    const review = useMutation({
        mutationFn: () => experienceApi.review(entryId),
        onSuccess: () => { qc.invalidateQueries({ queryKey: ['experience-entry', entryId] }); onChanged(); },
    });

    if (entryPending) return <Drawer onClose={onClose} docked={docked}><div>加载中...</div></Drawer>;
    if (entryError || !entry) {
        return (
            <Drawer onClose={onClose} docked={docked}>
                <div style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>经验已删除或不可访问</div>
                <button onClick={onClose} style={secondaryBtn}>关闭</button>
            </Drawer>
        );
    }
    const f = freshness(entry);
    const daysLeft = retiredDaysLeft(entry);
    return (
        <Drawer onClose={onClose} docked={docked}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                <h2 style={{ margin: 0, fontSize: 18, color: 'var(--text-primary)' }}>{entry.title || '(未命名)'}</h2>
                <button onClick={onClose} style={{ ...secondaryBtn, padding: '4px 10px' }}>✕</button>
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '10px 0 16px' }}>
                {f.label && <Badge tone={f.stale ? 'warn' : 'ok'}>{f.label}</Badge>}
                {(entry.tags || []).map(tg => <Badge key={tg}>#{tg}</Badge>)}
            </div>
            <div style={{ marginBottom: 16 }}><CreatorLine entry={entry} /></div>
            {EXP_FIELDS.map(fl => {
                const value = (entry[fl.key] as string) || '';
                return (
                    <section key={fl.key} style={{ marginBottom: 14 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>{fl.label}</div>
                        {fl.markdown && value ? (
                            <MarkdownRenderer content={value} style={{ fontSize: 14, lineHeight: 1.6 }} />
                        ) : (
                            <div style={{ fontSize: 14, color: 'var(--text-primary)', whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
                                {value || '—'}
                            </div>
                        )}
                    </section>
                );
            })}
            {refs && (
                <div style={{ margin: '8px 0 16px', fontSize: 13, color: 'var(--text-tertiary)' }}>
                    被 AI 阅读 {refs.read_count} 次 · <strong style={{ color: 'var(--text-secondary)' }}>实际采纳（引用） {refs.cited_count} 次</strong>
                </div>
            )}
            {entry.can_manage ? (
                <>
                    <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                        <button onClick={() => onEdit(entry)} style={secondaryBtn}>编辑</button>
                        {entry.status === 'published' && (
                            <button onClick={() => review.mutate()} style={secondaryBtn} disabled={review.isPending}>
                                {entry.last_reviewed_at ? '标记为未复核' : '标记已复核'}
                            </button>
                        )}
                        {entry.status === 'retired' ? (
                            <button onClick={() => republish.mutate()} style={{ ...secondaryBtn, color: 'var(--success)' }} disabled={republish.isPending}>
                                重新发布
                            </button>
                        ) : (
                            <button onClick={() => retire.mutate()} style={{ ...secondaryBtn, color: 'var(--error)' }} disabled={retire.isPending}>
                                下架
                            </button>
                        )}
                    </div>
                    {entry.status === 'retired' && daysLeft !== null && (
                        <p style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 10 }}>
                            已下架，{daysLeft} 天后自动删除。重新发布可恢复到团队经验并清除删除倒计时。
                        </p>
                    )}
                </>
            ) : (
                <p style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 10 }}>
                    {entry.status === 'retired'
                        ? `已下架${daysLeft !== null ? `，${daysLeft} 天后自动删除` : ''}。仅发起人、数字员工创立者或管理员可编辑或重新发布。`
                        : '仅发起人、数字员工创立者或管理员可编辑、复核或下架此经验。'}
                </p>
            )}
        </Drawer>
    );
}
