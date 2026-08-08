/**
 * Shared experience draft editor — the single human-gated review drawer.
 *
 * Used by both the library page (Plaza) and the in-chat experience_draft card.
 * The agent never writes to the library; a row is created only when the human
 * confirms here (save draft or publish).
 */
import React, { useState } from 'react';
import { createPortal } from 'react-dom';
import { useMutation } from '@tanstack/react-query';
import { experienceApi, type ExperienceEntry } from '../services/api';

export type Draft = Partial<ExperienceEntry>;

// The body is free-form markdown; only `applicability` keeps a fixed shape, because it is
// the one field `search_experience` shows the agent as a candidate preview — it must be
// readable on its own for the agent to decide read-or-skip without fetching the full text.
export const EXP_FIELDS: { key: keyof ExperienceEntry; label: string; hint?: string; markdown?: boolean }[] = [
    { key: 'body', label: '正文', markdown: true },
    { key: 'applicability', label: '适用条件与失效信号', hint: '必填：此经验何时成立、出现什么信号说明已失效' },
];

// Seeded into an empty editor: a suggestion, not a schema. Knowledge that isn't a
// problem→solution story (a config reference, a hidden process rule) should overwrite it.
export const BODY_TEMPLATE = '## 场景\n\n## 遇到的问题\n\n## 解决方式\n';

// The seeded template is non-empty, so a plain trim() check would green-light publishing an
// empty scaffold. Strip heading lines and see whether any prose actually remains.
const hasProse = (md?: string | null): boolean =>
    (md || '').replace(/^\s*#{1,6}\s.*$/gm, '').trim().length > 0;

// Past this the entry starts costing real tokens on every read_experience call. Advisory only.
const BODY_SOFT_LIMIT = 2000;

// Flatten the markdown body to plain text for compact previews (cards, summary rows),
// where raw markers would otherwise show up literally as "## 场景".
export function bodyExcerpt(md?: string | null): string {
    return (md || '')
        .replace(/```[\s\S]*?```/g, ' ')   // code blocks read as noise at this size
        .replace(/^\s*#{1,6}\s+/gm, '')    // heading markers
        .replace(/^\s*[-*+]\s+/gm, '')     // list bullets
        .replace(/[*_`]/g, '')             // inline emphasis
        .replace(/\s+/g, ' ')
        .trim();
}

export const primaryBtn: React.CSSProperties = {
    padding: '8px 14px', borderRadius: 8, border: 'none', cursor: 'pointer', fontSize: 14,
    background: 'var(--accent-primary)', color: 'var(--text-inverse)', fontWeight: 500, flexShrink: 0,
};
export const secondaryBtn: React.CSSProperties = {
    padding: '7px 12px', borderRadius: 8, border: '1px solid var(--border-default)', cursor: 'pointer',
    fontSize: 13, background: 'var(--bg-card)', color: 'var(--text-primary)',
};
const labelStyle: React.CSSProperties = {
    display: 'block', fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', margin: '12px 0 4px',
};
const inputStyle: React.CSSProperties = {
    width: '100%', padding: '9px 11px', borderRadius: 8, border: '1px solid var(--border-strong, var(--border-default))',
    fontSize: 14, background: 'var(--bg-primary)', color: 'var(--text-primary)', boxSizing: 'border-box',
};

// 2026年7月9日; empty string for null/invalid so callers can fall back.
const fmtDate = (s?: string | null): string => {
    if (!s) return '';
    const d = new Date(s);
    if (isNaN(d.getTime())) return '';
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
};

export function Drawer({ header, children, footer, onClose, docked = false }: {
    header?: React.ReactNode; children: React.ReactNode; footer?: React.ReactNode;
    onClose: () => void; docked?: boolean;
}) {
    // Flex column: fixed header / scrollable body / pinned footer. Solid opaque surface.
    const panel = (
        <div onClick={e => e.stopPropagation()} style={{
            position: 'fixed', top: 0, right: 0, height: '100vh',
            width: docked ? 'min(460px, 46vw)' : 'min(560px, 92vw)',
            background: 'var(--bg-elevated)', borderLeft: '1px solid var(--border-strong, var(--border-default))',
            boxShadow: '-8px 0 32px rgba(0,0,0,.28)', zIndex: 1001, boxSizing: 'border-box',
            display: 'flex', flexDirection: 'column',
        }}>
            {header && (
                <div style={{ flex: 'none', padding: '20px 24px 12px', borderBottom: '1px solid var(--border-default)' }}>
                    {header}
                </div>
            )}
            <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', WebkitOverflowScrolling: 'touch', padding: 24 }}>
                {children}
            </div>
            {footer && (
                <div style={{ flex: 'none', padding: '14px 24px', borderTop: '1px solid var(--border-default)', background: 'var(--bg-elevated)' }}>
                    {footer}
                </div>
            )}
        </div>
    );
    // Docked: no backdrop — the rest of the page stays fully bright and interactive.
    const content = docked ? panel : (
        <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.35)', zIndex: 1000 }}>
            {panel}
        </div>
    );
    // Portal to <body> so the drawer escapes any opacity/transform ancestor in the chat DOM
    // (which would otherwise make it translucent and break position:fixed sizing).
    return createPortal(content, document.body);
}

export function DraftEditor({ draft, onClose, onSaved, onDeleted, docked, autoExtractFailed }: {
    draft: Draft; onClose: () => void; onSaved: () => void; onDeleted?: () => void; docked?: boolean;
    // Set when a chat distill produced nothing usable — shows a manual-fill hint.
    autoExtractFailed?: boolean;
}) {
    const [form, setForm] = useState<Draft>({
        title: '', applicability: '',
        tags: [], ...draft,
        // Seed the section scaffold only when there's nothing to show yet.
        body: hasProse(draft.body) ? draft.body : BODY_TEMPLATE,
    });
    const [err, setErr] = useState('');
    const isNew = !draft.id;
    const isRevisionSource = draft.status === 'published' || draft.status === 'retired';
    const canDelete = draft.status === 'draft' || draft.status === 'retired';

    const buildPayload = (): Draft => ({
        title: form.title, body: form.body, applicability: form.applicability, tags: form.tags,
        // Provenance (chat-sourced drafts): records the source agent + conversation.
        origin_agent_id: form.origin_agent_id, origin_session_id: form.origin_session_id,
    });

    const save = useMutation({
        mutationFn: async () => {
            const payload = buildPayload();
            if (isNew) return experienceApi.create(payload);
            if (isRevisionSource) return experienceApi.createRevision(draft.id!, payload);
            return experienceApi.update(draft.id!, payload);
        },
        onSuccess: onSaved,
        onError: (e: any) => setErr(String(e?.message || e)),
    });

    const publish = useMutation({
        // Calls the API directly (not the `save` mutation) so onSaved fires once, not twice.
        mutationFn: async () => {
            const payload = buildPayload();
            let id: string;
            if (isNew) {
                id = (await experienceApi.create(payload)).id;
            } else if (isRevisionSource) {
                id = (await experienceApi.createRevision(draft.id!, payload)).id;
            } else {
                id = draft.id!;
                await experienceApi.update(id, payload);
            }
            return experienceApi.publish(id);
        },
        onSuccess: onSaved,
        onError: (e: any) => setErr(String(e?.message || e)),
    });

    const del = useMutation({
        mutationFn: () => experienceApi.remove(draft.id!),
        onSuccess: () => onDeleted && onDeleted(),
        onError: (e: any) => setErr(String(e?.message || e)),
    });
    const handleDelete = () => {
        const label = draft.status === 'retired' ? '这条已下架经验' : '这条草稿';
        if (window.confirm(`确定删除${label}？此操作不可撤销。`)) del.mutate();
    };

    // Publish gate (P0-3): a title, a body with actual prose, and applicability filled in.
    const canPublish = !!(form.title || '').trim() && hasProse(form.body) && !!(form.applicability || '').trim();
    const bodyLen = (form.body || '').length;
    const set = (k: keyof ExperienceEntry, v: any) => setForm(p => ({ ...p, [k]: v }));

    const header = (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h2 style={{ margin: 0, fontSize: 18, color: 'var(--text-primary)' }}>
                {isNew ? '新建经验草稿'
                    : draft.status === 'published' ? '编辑经验'
                    : draft.status === 'retired' ? '编辑已下架经验'
                    : '审核 / 编辑草稿'}
            </h2>
            <button onClick={onClose} style={{ ...secondaryBtn, padding: '4px 10px' }}>✕</button>
        </div>
    );

    const footer = (
        <>
            {err && <div style={{ color: 'var(--error)', fontSize: 13, margin: '0 0 10px' }}>{err}</div>}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button onClick={() => save.mutate()} style={secondaryBtn} disabled={save.isPending}>保存草稿</button>
                <button onClick={() => publish.mutate()} style={{ ...primaryBtn, opacity: canPublish ? 1 : .5 }}
                    disabled={!canPublish || publish.isPending}
                    title={canPublish ? '' : '标题、正文、适用条件与失效信号均须填写'}>
                    确认入库（发布）
                </button>
                {canDelete && onDeleted && (
                    <button onClick={handleDelete} disabled={del.isPending}
                        style={{ ...secondaryBtn, color: 'var(--error)', borderColor: 'var(--error)', marginLeft: 'auto' }}>
                        {draft.status === 'retired' ? '永久删除' : '删除草稿'}
                    </button>
                )}
            </div>
        </>
    );

    return (
        <Drawer onClose={onClose} docked={docked} header={header} footer={footer}>
            <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '0 0 16px' }}>
                标题、正文、“适用条件与失效信号”齐全方可发布；发布前均可修改。
            </p>

            {autoExtractFailed && (
                <div style={{
                    fontSize: 13, color: 'var(--error)', background: 'var(--error-subtle)',
                    border: '1px solid var(--error)', borderRadius: 8, padding: '8px 11px', margin: '0 0 16px',
                }}>
                    未能自动抽取，请手动填写。
                </div>
            )}

            {draft.created_at && (
                <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: 12, color: 'var(--text-tertiary)', margin: '0 0 16px' }}>
                    <span>添加日期：{fmtDate(draft.created_at) || '—'}</span>
                    <span>修改日期：{fmtDate(draft.updated_at) || '—'}</span>
                    <span>复核日期：{fmtDate(draft.last_reviewed_at) || '未复核'}</span>
                </div>
            )}

            <label style={labelStyle}>标题</label>
            <input value={form.title || ''} onChange={e => set('title', e.target.value)} style={inputStyle} maxLength={200} />

            <label style={labelStyle}>
                正文<span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}> — markdown；小节标题是建议，不是必须</span>
            </label>
            <textarea value={form.body || ''} onChange={e => set('body', e.target.value)}
                style={{
                    ...inputStyle, minHeight: 240, resize: 'vertical', lineHeight: 1.6,
                    fontFamily: 'var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)', fontSize: 13,
                }} />
            <div style={{
                fontSize: 12, textAlign: 'right', marginTop: 4,
                color: bodyLen > BODY_SOFT_LIMIT ? 'var(--warning, #b45309)' : 'var(--text-tertiary)',
            }}>
                {bodyLen} 字
                {bodyLen > BODY_SOFT_LIMIT && ` · 偏长，AI 每次读取都要吃掉全文，建议精简到 ${BODY_SOFT_LIMIT} 字内`}
            </div>

            <label style={labelStyle}>
                适用条件与失效信号
                <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}> — 必填：此经验何时成立、出现什么信号说明已失效</span>
            </label>
            <textarea value={form.applicability || ''} onChange={e => set('applicability', e.target.value)}
                style={{ ...inputStyle, minHeight: 72, resize: 'vertical' }} />
            <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '4px 0 0' }}>
                AI 检索时只先看到标题和这一栏，据此判断要不要读全文——请写成脱离正文也能读懂的一两句话。
            </p>

            <label style={labelStyle}>标签（逗号分隔）</label>
            <input value={(form.tags || []).join(', ')} onChange={e => set('tags', e.target.value.split(',').map(s => s.trim()).filter(Boolean))} style={inputStyle} />
        </Drawer>
    );
}
