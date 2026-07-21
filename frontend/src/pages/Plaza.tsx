/**
 * Team Experience Library (route kept as /plaza per PRD "保留外壳").
 *
 * Human-curated, AI-consumed private knowledge. Replaces the old social feed.
 * - 团队经验: published entries visible to me (P0-6 filtered server-side).
 * - 我的经验: entries I can manage (I distilled, or I created the source agent).
 * Draft review drawer sets the four parts + tags + visibility before publish.
 */
import React, { useMemo, useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconBuildingMonument } from '@tabler/icons-react';
import { experienceApi, type ExperienceEntry } from '../services/api';
import { DraftEditor, Drawer, bodyExcerpt, secondaryBtn, type Draft } from '../components/ExperienceDraftEditor';
import { EntryDrawer, Badge, CreatorLine, freshness, retiredDaysLeft } from '../components/ExperienceDetailDrawer';

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

const MINE_ALL = '全部', MINE_DRAFTS = '草稿箱', MINE_RETIRED = '已下架', MINE_UNTAGGED = '未分类';

export default function Plaza() {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const [params, setParams] = useSearchParams();
    const [view, setView] = useState<'team' | 'mine'>('team');
    const [cat, setCat] = useState<string>(MINE_ALL);
    const [teamTag, setTeamTag] = useState<string | null>(null);
    const [openId, setOpenId] = useState<string | null>(null);
    const [editing, setEditing] = useState<Draft | null>(null);

    const teamQ = useQuery({ queryKey: ['experience', 'team'], queryFn: () => experienceApi.list({ view: 'team' }), enabled: view === 'team' });
    const taggedTeamQ = useQuery({
        queryKey: ['experience', 'team', 'tag', teamTag],
        queryFn: () => experienceApi.list({ view: 'team', tag: teamTag || undefined }),
        enabled: view === 'team' && !!teamTag,
    });
    const statsQ = useQuery({ queryKey: ['experience-stats'], queryFn: () => experienceApi.stats(), enabled: view === 'team' });
    const mineQ = useQuery({ queryKey: ['experience', 'mine'], queryFn: () => experienceApi.list({ view: 'mine' }), enabled: view === 'mine' });

    // Deep-links: ?draft=<id> opens the review drawer (from chat 沉淀); ?entry=<id> opens the
    // entry (from a chat citation pill) — published → detail drawer, draft → editor.
    const draftParam = params.get('draft');
    const entryParam = params.get('entry');
    useEffect(() => {
        if (!draftParam && !entryParam) return;
        const id = draftParam || entryParam!;
        experienceApi.get(id).then(e => {
            if (draftParam) setEditing(e);
            else (e.status === 'draft' ? setEditing(e) : setOpenId(e.id));
        }).catch(() => {});
        params.delete('draft'); params.delete('entry');
        setParams(params, { replace: true });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [draftParam, entryParam]);

    const refreshAll = () => { qc.invalidateQueries({ queryKey: ['experience'] }); qc.invalidateQueries({ queryKey: ['experience-stats'] }); };
    // Drafts open the editor; published & retired open the read/action drawer (retired → 重新发布).
    const openEntry = (e: ExperienceEntry) => (e.status === 'draft' ? setEditing(e) : setOpenId(e.id));
    const newEntry = () => setEditing({ tags: [] });

    const unfilteredTeamEntries = teamQ.data ?? [];
    const teamEntries = teamTag ? (taggedTeamQ.data ?? []) : unfilteredTeamEntries;
    const mineEntries = mineQ.data ?? [];

    const trending = useMemo(() => {
        const m = new Map<string, number>();
        unfilteredTeamEntries.forEach(e => (e.tags || []).forEach(tg => m.set(tg, (m.get(tg) || 0) + 1)));
        return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12);
    }, [unfilteredTeamEntries]);

    const mineTags = useMemo(() => {
        const m = new Map<string, number>();
        // Retired entries live only in the 已下架 bin, so their tags don't drive tag chips.
        mineEntries.filter(e => e.status !== 'retired').forEach(e => (e.tags || []).forEach(tg => m.set(tg, (m.get(tg) || 0) + 1)));
        return [...m.entries()].sort((a, b) => b[1] - a[1]).map(x => x[0]);
    }, [mineEntries]);

    // 已下架 is its own bucket; every other category excludes retired entries.
    const mineShown = cat === MINE_DRAFTS ? mineEntries.filter(e => e.status === 'draft')
        : cat === MINE_RETIRED ? mineEntries.filter(e => e.status === 'retired')
        : cat === MINE_UNTAGGED ? mineEntries.filter(e => e.status !== 'retired' && !(e.tags || []).length)
        : cat === MINE_ALL ? mineEntries.filter(e => e.status !== 'retired')
        : mineEntries.filter(e => e.status !== 'retired' && (e.tags || []).includes(cat));

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
                        onChange={(v) => { setView(v); setCat(MINE_ALL); setTeamTag(null); setOpenId(null); }}
                        options={[{ value: 'team', label: t('experience.nav.team', '团队经验') }, { value: 'mine', label: t('experience.nav.mine', '我的经验') }]}
                    />
                    <button className="btn btn-primary" style={{ height: 34, whiteSpace: 'nowrap' }} onClick={newEntry}>
                        + {t('experience.new', '新建经验')}
                    </button>
                </div>
            </div>

            {view === 'team' ? (
                <TeamView loading={teamQ.isLoading || taggedTeamQ.isLoading} entries={teamEntries} stats={statsQ.data} trending={trending}
                    activeTag={teamTag} onOpen={openEntry} onNew={newEntry}
                    onTag={(tg) => setTeamTag(prev => prev === tg ? null : tg)}
                    onClearTag={() => setTeamTag(null)} />
            ) : (
                <MineView loading={mineQ.isLoading}
                    cats={[MINE_ALL, MINE_DRAFTS, MINE_RETIRED, MINE_UNTAGGED, ...mineTags]}
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

function TeamView({ loading, entries, stats, trending, activeTag, onOpen, onNew, onTag, onClearTag }: {
    loading: boolean; entries: ExperienceEntry[];
    stats?: { total: number; today: number; cited: number; top_contributors: { name: string; count: number }[] };
    trending: [string, number][]; activeTag: string | null;
    onOpen: (e: ExperienceEntry) => void; onNew: () => void; onTag: (tg: string) => void; onClearTag: () => void;
}) {
    const { t } = useTranslation();
    return (
        <>
            <StatsBar stats={stats} />
            <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                    {activeTag && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}>
                            <span>{t('experience.filteredByTag', '按标签筛选：')}</span>
                            <button onClick={onClearTag} style={{
                                display: 'inline-flex', alignItems: 'center', gap: 6, padding: '2px 8px', borderRadius: 'var(--radius-sm)',
                                fontSize: 'var(--text-xs)', background: 'var(--accent-soft, var(--bg-tertiary))', color: 'var(--accent, var(--text-primary))',
                                fontWeight: 600, border: 'none', cursor: 'pointer',
                            }}>{activeTag} <span aria-hidden style={{ fontSize: 13, lineHeight: 1 }}>×</span></button>
                            <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--text-xs)' }}>{entries.length}</span>
                        </div>
                    )}
                    {loading ? (
                        <div style={{ color: 'var(--text-tertiary)', padding: '40px 0', textAlign: 'center', fontSize: 'var(--text-sm)' }}>{t('common.loading', '加载中...')}</div>
                    ) : entries.length === 0 ? (
                        activeTag ? (
                            <div style={{ border: '1px dashed var(--border-default)', borderRadius: 'var(--radius-lg)', padding: '40px 24px', textAlign: 'center' }}>
                                <div style={{ fontSize: 'var(--text-sm)', color: 'var(--text-tertiary)', marginBottom: 12 }}>
                                    {t('experience.emptyTag', '该标签下暂无团队经验。')}
                                </div>
                                <button className="btn btn-secondary" onClick={onClearTag}>{t('experience.clearFilter', '清除筛选')}</button>
                            </div>
                        ) : (
                            <div style={{ border: '1px dashed var(--border-default)', borderRadius: 'var(--radius-lg)', padding: '48px 24px', textAlign: 'center' }}>
                                <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 10, color: 'var(--text-tertiary)' }}>
                                    <IconBuildingMonument size={30} stroke={1.5} />
                                </div>
                                <div style={{ fontSize: 'var(--text-base)', color: 'var(--text-primary)', fontWeight: 600, marginBottom: 6 }}>
                                    {t('experience.emptyTitle', '还没有已发布的经验')}
                                </div>
                                <div style={{ fontSize: 'var(--text-sm)', color: 'var(--text-tertiary)', maxWidth: 440, margin: '0 auto 16px', lineHeight: 1.6 }}>
                                    {t('experience.emptyBody', '在与数字员工的对话里点「沉淀为经验」，或直接新建，把团队踩过的坑沉淀下来——AI 会在相关任务中自动检索复用。')}
                                </div>
                                <button className="btn btn-primary" onClick={onNew}>+ {t('experience.new', '新建经验')}</button>
                            </div>
                        )
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
                                {trending.map(([tg, n]) => {
                                    const on = tg === activeTag;
                                    return (
                                        <button key={tg} onClick={() => onTag(tg)} aria-pressed={on} style={{
                                            padding: '2px 8px', borderRadius: 'var(--radius-sm)', fontSize: 'var(--text-xs)',
                                            background: on ? 'var(--accent-soft, var(--accent, var(--bg-tertiary)))' : 'var(--bg-tertiary)',
                                            color: on ? 'var(--accent, var(--text-primary))' : 'var(--text-secondary)',
                                            fontWeight: on ? 600 : 500, border: 'none', cursor: 'pointer',
                                        }}>{tg} <span style={{ color: 'var(--text-tertiary)', fontSize: 10 }}>×{n}</span></button>
                                    );
                                })}
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
                    {t('experience.mineEmpty', '这个分类下还没有经验。')}
                </div>
            ) : (
                <div style={{ display: 'grid', gap: 12 }}>
                    {entries.map(e => <EntryCard key={e.id} entry={e} onOpen={() => onOpen(e)} />)}
                </div>
            )}
        </>
    );
}


function EntryCard({ entry, onOpen }: { entry: ExperienceEntry; onOpen: () => void }) {
    const f = freshness(entry);
    const daysLeft = retiredDaysLeft(entry);
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
                    {daysLeft !== null && <Badge tone="warn">{daysLeft} 天后自动删除</Badge>}
                    {f.label && <Badge tone={f.stale ? 'warn' : 'ok'}>{f.label}</Badge>}
                </div>
            </div>
            {bodyExcerpt(entry.body) && (
                <div style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '8px 0', lineHeight: 1.5,
                    display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                    {bodyExcerpt(entry.body)}
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
