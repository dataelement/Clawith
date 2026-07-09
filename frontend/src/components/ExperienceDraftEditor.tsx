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

// The library's fixed four-part schema (P0-3). applicability is mandatory to publish.
export const EXP_FIELDS: { key: keyof ExperienceEntry; label: string; hint?: string }[] = [
    { key: 'scenario', label: '场景' },
    { key: 'problem', label: '遇到的问题' },
    { key: 'solution', label: '解决方式' },
    { key: 'applicability', label: '适用条件与失效信号', hint: '必填：此经验何时成立、出现什么信号说明已失效' },
];

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

export function DraftEditor({ draft, onClose, onSaved, docked }: { draft: Draft; onClose: () => void; onSaved: () => void; docked?: boolean }) {
    const [form, setForm] = useState<Draft>({
        title: '', scenario: '', problem: '', solution: '', applicability: '',
        tags: [], visibility_scope: 'company', visibility_scope_id: null, ...draft,
    });
    const [err, setErr] = useState('');
    const isNew = !draft.id;

    const buildPayload = (): Draft => ({
        title: form.title, scenario: form.scenario, problem: form.problem,
        solution: form.solution, applicability: form.applicability, tags: form.tags,
        visibility_scope: form.visibility_scope, visibility_scope_id: form.visibility_scope_id || null,
        // Provenance (chat-sourced drafts): records the source agent + conversation.
        origin_agent_id: form.origin_agent_id, origin_session_id: form.origin_session_id,
    });

    const save = useMutation({
        mutationFn: async () => {
            const payload = buildPayload();
            if (isNew) return experienceApi.create(payload);
            return experienceApi.update(draft.id!, payload);
        },
        onSuccess: onSaved,
        onError: (e: any) => setErr(String(e?.message || e)),
    });

    const publish = useMutation({
        // Calls the API directly (not the `save` mutation) so onSaved fires once, not twice.
        mutationFn: async () => {
            const payload = buildPayload();
            const id = isNew ? (await experienceApi.create(payload)).id : draft.id!;
            if (!isNew) await experienceApi.update(id, payload);
            return experienceApi.publish(id);
        },
        onSuccess: onSaved,
        onError: (e: any) => setErr(String(e?.message || e)),
    });

    const fourFilled = EXP_FIELDS.every(f => ((form[f.key] as string) || '').trim());
    const set = (k: keyof ExperienceEntry, v: any) => setForm(p => ({ ...p, [k]: v }));

    const header = (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h2 style={{ margin: 0, fontSize: 18, color: 'var(--text-primary)' }}>
                {isNew ? '新建经验草稿' : '审核 / 编辑草稿'}
            </h2>
            <button onClick={onClose} style={{ ...secondaryBtn, padding: '4px 10px' }}>✕</button>
        </div>
    );

    const footer = (
        <>
            {err && <div style={{ color: 'var(--error)', fontSize: 13, margin: '0 0 10px' }}>{err}</div>}
            <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={() => save.mutate()} style={secondaryBtn} disabled={save.isPending}>保存草稿</button>
                <button onClick={() => publish.mutate()} style={{ ...primaryBtn, opacity: fourFilled ? 1 : .5 }}
                    disabled={!fourFilled || publish.isPending} title={fourFilled ? '' : '四段缺一不可发布'}>
                    确认入库（发布）
                </button>
            </div>
        </>
    );

    return (
        <Drawer onClose={onClose} docked={docked} header={header} footer={footer}>
            <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '0 0 16px' }}>
                四段齐全（尤其“适用条件与失效信号”）方可发布；发布前均可修改。
            </p>

            <label style={labelStyle}>标题</label>
            <input value={form.title || ''} onChange={e => set('title', e.target.value)} style={inputStyle} maxLength={200} />

            {EXP_FIELDS.map(f => (
                <div key={f.key}>
                    <label style={labelStyle}>{f.label}{f.hint && <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}> — {f.hint}</span>}</label>
                    <textarea value={(form[f.key] as string) || ''} onChange={e => set(f.key, e.target.value)}
                        style={{ ...inputStyle, minHeight: 72, resize: 'vertical' }} />
                </div>
            ))}

            <label style={labelStyle}>标签（逗号分隔）</label>
            <input value={(form.tags || []).join(', ')} onChange={e => set('tags', e.target.value.split(',').map(s => s.trim()).filter(Boolean))} style={inputStyle} />

            <label style={labelStyle}>可见范围</label>
            <div style={{ display: 'flex', gap: 8 }}>
                <select value={form.visibility_scope} onChange={e => set('visibility_scope', e.target.value)} style={{ ...inputStyle, flex: '0 0 140px' }}>
                    <option value="company">全公司</option>
                    <option value="department">指定部门</option>
                    <option value="user">指定用户</option>
                </select>
                {form.visibility_scope !== 'company' && (
                    <input placeholder={form.visibility_scope === 'department' ? '部门 ID' : '用户 ID'}
                        value={form.visibility_scope_id || ''} onChange={e => set('visibility_scope_id', e.target.value)}
                        style={{ ...inputStyle, flex: 1 }} />
                )}
            </div>
            {form.visibility_scope !== 'company' && (
                <p style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                    组织架构未同步时，部门/用户可见性发布后会自动降级为全公司。
                </p>
            )}
        </Drawer>
    );
}
