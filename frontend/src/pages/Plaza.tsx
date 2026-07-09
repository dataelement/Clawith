/**
 * Team Experience Library (route kept as /plaza per PRD "保留外壳").
 *
 * Human-curated, AI-consumed private knowledge. Replaces the old social feed.
 * - 团队经验: published entries visible to me (P0-6 filtered server-side).
 * - 我的经验: entries I can manage (I distilled, or I created the source agent).
 * - 历史沉淀: legacy Plaza imports (drafts, hard-isolated) awaiting triage.
 * Draft review drawer sets the four parts + tags + visibility before publish.
 */
import React, { useMemo, useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { experienceApi, type ExperienceEntry } from '../services/api';
import { DraftEditor, Drawer, EXP_FIELDS, secondaryBtn, type Draft } from '../components/ExperienceDraftEditor';

const SCOPE_LABELS: Record<string, string> = { company: '全公司', department: '本部门', user: '指定人' };

function freshness(entry: ExperienceEntry): { label: string; stale: boolean } {
    if (entry.status !== 'published') return { label: '', stale: false };
    if (!entry.last_reviewed_at) return { label: '未复核', stale: true };
    const d = new Date(entry.last_reviewed_at);
    const dateStr = `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
    const age = Date.now() - d.getTime();
    const stale = age > 90 * 86400000;
    return { label: `${stale ? '复核超期' : '已复核'}（${dateStr}）`, stale };
}

const badgeStyle = (bg: string, fg: string): React.CSSProperties => ({
    display: 'inline-block', padding: '1px 7px', borderRadius: 10, fontSize: 11,
    background: bg, color: fg, whiteSpace: 'nowrap',
});

function Badge({ children, tone = 'muted' }: { children: React.ReactNode; tone?: 'muted' | 'warn' | 'ok' | 'accent' }) {
    const tones: Record<string, [string, string]> = {
        muted: ['var(--bg-tertiary)', 'var(--text-secondary)'],
        warn: ['var(--error-subtle)', 'var(--error)'],
        ok: ['var(--success-subtle)', 'var(--success)'],
        accent: ['var(--accent-subtle)', 'var(--accent-text)'],
    };
    const [bg, fg] = tones[tone];
    return <span style={badgeStyle(bg, fg)}>{children}</span>;
}

const sicon = (d: string) => (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
);
const Icons = {
    book: sicon('M3 2h8a1 1 0 011 1v11l-3-2-3 2V3a1 1 0 00-1-1H3zM3 2v12'),
    fire: sicon('M8.5 1.5S12.5 5 12.5 9a4.5 4.5 0 01-9 0c0-2 1-3.5 2-4.5 0 0 .5 2 2 2.5C8 7 8.5 1.5 8.5 1.5z'),
    check: sicon('M13.5 4.5l-7 7-3-3'),
    trophy: sicon('M5 14h6M8 11v3M4 2h8v3a4 4 0 01-8 0V2zM4 3H2.5a1 1 0 00-1 1v1a2 2 0 002 2H4M12 3h1.5a1 1 0 011 1v1a2 2 0 01-2 2H12'),
    hash: sicon('M3 6h10M3 10h10M6.5 2.5l-1 11M10.5 2.5l-1 11'),
};

const MINE_ALL = '全部', MINE_DRAFTS = '草稿箱', MINE_UNTAGGED = '未分类', MINE_HISTORY = '历史沉淀';

export default function Plaza() {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const [params, setParams] = useSearchParams();
    const [view, setView] = useState<'team' | 'mine'>('team');
    const [cat, setCat] = useState<string>(MINE_ALL);
    const [openId, setOpenId] = useState<string | null>(null);
    const [editing, setEditing] = useState<Draft | null>(null);

    const teamQ = useQuery({ queryKey: ['experience', 'team'], queryFn: () => experienceApi.list({ view: 'team' }), enabled: view === 'team' });
    const statsQ = useQuery({ queryKey: ['experience-stats'], queryFn: () => experienceApi.stats(), enabled: view === 'team' });
    const mineQ = useQuery({ queryKey: ['experience', 'mine'], queryFn: () => experienceApi.list({ view: 'mine' }), enabled: view === 'mine' });
    const historyQ = useQuery({ queryKey: ['experience', 'history'], queryFn: () => experienceApi.list({ view: 'history' }), enabled: view === 'mine' });

    // Deep-links: ?draft=<id> opens the review drawer (from chat 沉淀); ?entry=<id> opens the
    // entry (from a chat citation pill) — published → detail drawer, draft → editor.
    const draftParam = params.get('draft');
    const entryParam = params.get('entry');
    useEffect(() => {
        if (!draftParam && !entryParam) return;
        const id = draftParam || entryParam!;
        experienceApi.get(id).then(e => {
            if (draftParam) setEditing(e);
            else (e.status === 'published' ? setOpenId(e.id) : setEditing(e));
        }).catch(() => {});
        params.delete('draft'); params.delete('entry');
        setParams(params, { replace: true });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [draftParam, entryParam]);

    const refreshAll = () => { qc.invalidateQueries({ queryKey: ['experience'] }); qc.invalidateQueries({ queryKey: ['experience-stats'] }); };
    const openEntry = (e: ExperienceEntry) => (e.status === 'published' ? setOpenId(e.id) : setEditing(e));
    const newEntry = () => setEditing({ visibility_scope: 'company', tags: [] });

    const teamEntries = teamQ.data ?? [];
    const mineEntries = mineQ.data ?? [];
    const historyEntries = historyQ.data ?? [];

    const trending = useMemo(() => {
        const m = new Map<string, number>();
        teamEntries.forEach(e => (e.tags || []).forEach(tg => m.set(tg, (m.get(tg) || 0) + 1)));
        return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12);
    }, [teamEntries]);

    const mineTags = useMemo(() => {
        const m = new Map<string, number>();
        mineEntries.forEach(e => (e.tags || []).forEach(tg => m.set(tg, (m.get(tg) || 0) + 1)));
        return [...m.entries()].sort((a, b) => b[1] - a[1]).map(x => x[0]);
    }, [mineEntries]);

    const mineShown = cat === MINE_HISTORY ? historyEntries
        : cat === MINE_DRAFTS ? mineEntries.filter(e => e.status === 'draft')
        : cat === MINE_UNTAGGED ? mineEntries.filter(e => !(e.tags || []).length)
        : cat === MINE_ALL ? mineEntries
        : mineEntries.filter(e => (e.tags || []).includes(cat));

    return (
        <div style={{ padding: 24, maxWidth: 1120, margin: '0 auto' }}>
            {/* Header: title + right controls (toggle + new). No left sidebar — the global nav already exists. */}
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, marginBottom: 20, flexWrap: 'wrap' }}>
                <div>
                    <h1 style={{ fontSize: 'var(--text-xl)', margin: 0, color: 'var(--text-primary)' }}>
                        {view === 'team' ? t('experience.feedTitle', '经验广场') : t('experience.nav.mine', '我的经验')}
                    </h1>
                    <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-tertiary)', margin: '4px 0 0' }}>
                        {view === 'team'
                            ? t('experience.subtitle', '数字员工与人类分享经验的地方')
                            : t('experience.mineHint', '你发起或可管理的经验；含从旧 Plaza 迁入的历史沉淀。')}
                    </p>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <SegmentedToggle
                        value={view}
                        onChange={(v) => { setView(v); setCat(MINE_ALL); setOpenId(null); }}
                        options={[{ value: 'team', label: t('experience.nav.team', '团队经验') }, { value: 'mine', label: t('experience.nav.mine', '我的经验') }]}
                    />
                    <button className="btn btn-primary" style={{ height: 34, whiteSpace: 'nowrap' }} onClick={newEntry}>
                        + {t('experience.new', '新建经验')}
                    </button>
                </div>
            </div>

            {view === 'team' ? (
                <TeamView loading={teamQ.isLoading} entries={teamEntries} stats={statsQ.data} trending={trending}
                    onOpen={openEntry} onNew={newEntry} onTag={(tg) => { setView('mine'); setCat(tg); }} />
            ) : (
                <MineView loading={mineQ.isLoading || historyQ.isLoading}
                    cats={[MINE_ALL, MINE_DRAFTS, MINE_UNTAGGED, MINE_HISTORY, ...mineTags]}
                    cat={cat} setCat={setCat} entries={mineShown} onOpen={openEntry} />
            )}

            {openId && (
                <EntryDrawer entryId={openId} onClose={() => setOpenId(null)} onEdit={(e) => { setOpenId(null); setEditing(e); }} onChanged={refreshAll} />
            )}
            {editing && (
                <DraftEditor draft={editing} onClose={() => setEditing(null)}
                    onSaved={() => { setEditing(null); refreshAll(); }}
                    onDeleted={() => { setEditing(null); refreshAll(); }} />
            )}
        </div>
    );
}

function SegmentedToggle({ value, onChange, options }: {
    value: 'team' | 'mine'; onChange: (v: 'team' | 'mine') => void; options: { value: 'team' | 'mine'; label: string }[];
}) {
    return (
        <div style={{ display: 'inline-flex', background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-full)', padding: 2 }}>
            {options.map(({ value: v, label }) => (
                <button key={v} onClick={() => onChange(v)} style={{
                    padding: '6px 14px', borderRadius: 'var(--radius-full)', border: 'none', cursor: 'pointer',
                    fontSize: 'var(--text-sm)', whiteSpace: 'nowrap', transition: 'all .15s',
                    background: value === v ? 'var(--segment-active-bg)' : 'transparent',
                    color: value === v ? 'var(--segment-active-text)' : 'var(--text-secondary)',
                    fontWeight: value === v ? 600 : 400,
                }}>{label}</button>
            ))}
        </div>
    );
}

function StatsBar({ stats }: { stats?: { total: number; today: number; cited: number } }) {
    const { t } = useTranslation();
    const items = [
        { icon: Icons.book, label: t('experience.stat.total', '经验数'), value: stats?.total ?? 0 },
        { icon: Icons.fire, label: t('experience.stat.today', '今日新增'), value: stats?.today ?? 0 },
        { icon: Icons.check, label: t('experience.stat.cited', '被采纳'), value: stats?.cited ?? 0 },
    ];
    return (
        <div style={{
            display: 'grid', gridTemplateColumns: `repeat(${items.length}, 1fr)`, gap: 1,
            background: 'var(--border-subtle)', borderRadius: 'var(--radius-lg)', overflow: 'hidden',
            marginBottom: 24, border: '1px solid var(--border-subtle)',
        }}>
            {items.map((s, i) => (
                <div key={i} style={{ background: 'var(--bg-secondary)', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                        <span style={{ display: 'flex', opacity: .7 }}>{s.icon}</span> {s.label}
                    </div>
                    <div style={{ fontSize: 'var(--text-2xl)', fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>{s.value}</div>
                </div>
            ))}
        </div>
    );
}

function SidebarSection({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
    return (
        <div style={{ border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}>
            <div style={{
                padding: '10px 14px', borderBottom: '1px solid var(--border-subtle)', display: 'flex',
                alignItems: 'center', gap: 6, fontSize: 'var(--text-xs)', fontWeight: 500, color: 'var(--text-secondary)',
            }}>
                <span style={{ display: 'flex', opacity: .6 }}>{icon}</span>{title}
            </div>
            <div style={{ padding: '10px 14px' }}>{children}</div>
        </div>
    );
}

function TeamView({ loading, entries, stats, trending, onOpen, onNew, onTag }: {
    loading: boolean; entries: ExperienceEntry[];
    stats?: { total: number; today: number; cited: number; top_contributors: { name: string; count: number }[] };
    trending: [string, number][]; onOpen: (e: ExperienceEntry) => void; onNew: () => void; onTag: (tg: string) => void;
}) {
    const { t } = useTranslation();
    return (
        <>
            <StatsBar stats={stats} />
            <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                    {loading ? (
                        <div style={{ color: 'var(--text-tertiary)', padding: '40px 0', textAlign: 'center', fontSize: 'var(--text-sm)' }}>{t('common.loading', '加载中...')}</div>
                    ) : entries.length === 0 ? (
                        <div style={{ border: '1px dashed var(--border-default)', borderRadius: 'var(--radius-lg)', padding: '48px 24px', textAlign: 'center' }}>
                            <div style={{ fontSize: 30, marginBottom: 10 }}>📚</div>
                            <div style={{ fontSize: 'var(--text-base)', color: 'var(--text-primary)', fontWeight: 600, marginBottom: 6 }}>
                                {t('experience.emptyTitle', '还没有已发布的经验')}
                            </div>
                            <div style={{ fontSize: 'var(--text-sm)', color: 'var(--text-tertiary)', maxWidth: 440, margin: '0 auto 16px', lineHeight: 1.6 }}>
                                {t('experience.emptyBody', '在与数字员工的对话里点「沉淀为经验」，或直接新建，把团队踩过的坑沉淀下来——AI 会在相关任务中自动检索复用。')}
                            </div>
                            <button className="btn btn-primary" onClick={onNew}>+ {t('experience.new', '新建经验')}</button>
                        </div>
                    ) : (
                        <div style={{ display: 'grid', gap: 12 }}>
                            {entries.map(e => <EntryCard key={e.id} entry={e} onOpen={() => onOpen(e)} />)}
                        </div>
                    )}
                </div>

                <aside style={{ width: 260, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {stats && stats.top_contributors.length > 0 && (
                        <SidebarSection icon={Icons.trophy} title={t('experience.topContributors', '热门贡献者')}>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                                {stats.top_contributors.map((c, i) => (
                                    <div key={c.name + i} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                        <span style={{ width: 16, fontSize: 'var(--text-xs)', textAlign: 'center', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>{i + 1}</span>
                                        <span style={{ flex: 1, fontSize: 'var(--text-xs)', color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</span>
                                        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>{c.count}</span>
                                    </div>
                                ))}
                            </div>
                        </SidebarSection>
                    )}
                    {trending.length > 0 && (
                        <SidebarSection icon={Icons.hash} title={t('experience.trendingTags', '热门标签')}>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                {trending.map(([tg, n]) => (
                                    <button key={tg} onClick={() => onTag(tg)} style={{
                                        padding: '2px 8px', borderRadius: 'var(--radius-sm)', fontSize: 'var(--text-xs)',
                                        background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', fontWeight: 500, border: 'none', cursor: 'pointer',
                                    }}>{tg} <span style={{ color: 'var(--text-tertiary)', fontSize: 10 }}>×{n}</span></button>
                                ))}
                            </div>
                        </SidebarSection>
                    )}
                    {(!stats || stats.top_contributors.length === 0) && trending.length === 0 && (
                        <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)', border: '1px dashed var(--border-subtle)', borderRadius: 'var(--radius-lg)', padding: 14, lineHeight: 1.6 }}>
                            {t('experience.sidebarEmpty', '有了已发布经验后，这里会显示热门贡献者与标签。')}
                        </div>
                    )}
                </aside>
            </div>
        </>
    );
}

function MineView({ loading, cats, cat, setCat, entries, onOpen }: {
    loading: boolean; cats: string[]; cat: string; setCat: (c: string) => void;
    entries: ExperienceEntry[]; onOpen: (e: ExperienceEntry) => void;
}) {
    const { t } = useTranslation();
    return (
        <>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
                {cats.map(c => (
                    <button key={c} onClick={() => setCat(c)} style={{
                        padding: '5px 12px', borderRadius: 'var(--radius-full)', fontSize: 'var(--text-xs)', cursor: 'pointer',
                        border: '1px solid ' + (cat === c ? 'transparent' : 'var(--border-default)'),
                        background: cat === c ? 'var(--accent-primary)' : 'transparent',
                        color: cat === c ? 'var(--text-inverse)' : 'var(--text-secondary)',
                    }}>{c}</button>
                ))}
            </div>
            {loading ? (
                <div style={{ color: 'var(--text-tertiary)', padding: '40px 0', textAlign: 'center', fontSize: 'var(--text-sm)' }}>{t('common.loading', '加载中...')}</div>
            ) : entries.length === 0 ? (
                <div style={{ color: 'var(--text-tertiary)', padding: '40px 0', textAlign: 'center', fontSize: 'var(--text-sm)' }}>
                    {cat === MINE_HISTORY ? t('experience.historyEmpty', '没有从旧 Plaza 迁入的历史沉淀。') : t('experience.mineEmpty', '这个分类下还没有经验。')}
                </div>
            ) : (
                <div style={{ display: 'grid', gap: 12 }}>
                    {entries.map(e => <EntryCard key={e.id} entry={e} onOpen={() => onOpen(e)} />)}
                </div>
            )}
        </>
    );
}


// PRD v3: every entry shows its two creators — the human who published it + the source agent.
function CreatorLine({ entry }: { entry: ExperienceEntry }) {
    if (!entry.created_by_name && !entry.origin_agent_name) return null;
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-tertiary)', flexWrap: 'wrap' }}>
            {entry.created_by_name && <span>👤 发布 {entry.created_by_name}</span>}
            {entry.created_by_name && entry.origin_agent_name && <span style={{ opacity: .5 }}>·</span>}
            {entry.origin_agent_name && <span>🤖 来源 {entry.origin_agent_name}</span>}
        </div>
    );
}

function EntryCard({ entry, onOpen }: { entry: ExperienceEntry; onOpen: () => void }) {
    const f = freshness(entry);
    return (
        <div
            onClick={onOpen}
            style={{
                border: '1px solid var(--border-subtle)', borderRadius: 12, padding: 16, cursor: 'pointer',
                background: 'var(--bg-card)', transition: 'border-color .15s',
            }}
        >
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)' }}>
                    {entry.title || '(未命名)'}
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {entry.status !== 'published' && <Badge tone="warn">{entry.status === 'retired' ? '已下架' : '草稿'}</Badge>}
                    <Badge tone="accent">{SCOPE_LABELS[entry.visibility_scope] || entry.visibility_scope}</Badge>
                    {f.label && <Badge tone={f.stale ? 'warn' : 'ok'}>{f.label}</Badge>}
                </div>
            </div>
            {entry.scenario && (
                <div style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '8px 0', lineHeight: 1.5,
                    display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                    {entry.scenario}
                </div>
            )}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                {(entry.tags || []).map(tg => (
                    <span key={tg} style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>#{tg}</span>
                ))}
            </div>
            <CreatorLine entry={entry} />
        </div>
    );
}

function EntryDrawer({ entryId, onClose, onEdit, onChanged }: {
    entryId: string; onClose: () => void; onEdit: (e: ExperienceEntry) => void; onChanged: () => void;
}) {
    const qc = useQueryClient();
    const { data: entry } = useQuery({ queryKey: ['experience-entry', entryId], queryFn: () => experienceApi.get(entryId) });
    const { data: refs } = useQuery({ queryKey: ['experience-refs', entryId], queryFn: () => experienceApi.references(entryId) });
    const retire = useMutation({ mutationFn: () => experienceApi.retire(entryId), onSuccess: () => { onChanged(); onClose(); } });
    const review = useMutation({
        mutationFn: () => experienceApi.review(entryId),
        onSuccess: () => { qc.invalidateQueries({ queryKey: ['experience-entry', entryId] }); onChanged(); },
    });

    if (!entry) return <Drawer onClose={onClose}><div>加载中...</div></Drawer>;
    const f = freshness(entry);
    return (
        <Drawer onClose={onClose}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                <h2 style={{ margin: 0, fontSize: 18, color: 'var(--text-primary)' }}>{entry.title || '(未命名)'}</h2>
                <button onClick={onClose} style={{ ...secondaryBtn, padding: '4px 10px' }}>✕</button>
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '10px 0 16px' }}>
                <Badge tone="accent">{SCOPE_LABELS[entry.visibility_scope]}</Badge>
                {f.label && <Badge tone={f.stale ? 'warn' : 'ok'}>{f.label}</Badge>}
                {(entry.tags || []).map(tg => <Badge key={tg}>#{tg}</Badge>)}
            </div>
            <div style={{ marginBottom: 16 }}><CreatorLine entry={entry} /></div>
            {EXP_FIELDS.map(fl => (
                <section key={fl.key} style={{ marginBottom: 14 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>{fl.label}</div>
                    <div style={{ fontSize: 14, color: 'var(--text-primary)', whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
                        {(entry[fl.key] as string) || '—'}
                    </div>
                </section>
            ))}
            {refs && (
                <div style={{ margin: '8px 0 16px', fontSize: 13, color: 'var(--text-tertiary)' }}>
                    被 AI 阅读 {refs.read_count} 次 · <strong style={{ color: 'var(--text-secondary)' }}>实际采纳（引用） {refs.cited_count} 次</strong>
                </div>
            )}
            <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                <button onClick={() => onEdit(entry)} style={secondaryBtn}>编辑</button>
                {entry.status === 'published' && (
                    <button onClick={() => review.mutate()} style={secondaryBtn} disabled={review.isPending}>
                        {entry.last_reviewed_at ? '标记为未复核' : '标记已复核'}
                    </button>
                )}
                <button onClick={() => retire.mutate()} style={{ ...secondaryBtn, color: 'var(--error)' }} disabled={retire.isPending}>
                    下架
                </button>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 10 }}>
                下架仅数字员工创立者可执行；无权限时操作会被拒绝。
            </p>
        </Drawer>
    );
}

