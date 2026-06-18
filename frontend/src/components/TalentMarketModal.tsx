import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconPlus, IconSearch, IconWorld, IconX } from '@tabler/icons-react';
import { agentApi, bundleApi, type BundleSummary } from '../services/api';
import PostHireSettingsModal from './PostHireSettingsModal';
import BundleHireModal from './BundleHireModal';
import CustomAgentModal from './CustomAgentModal';
import { translateTemplate } from '../i18n/templateTranslations';
import customAgentBackground from '../assets/talent-market/custom-agent-botanical.png';

interface Template {
    id: string;
    name: string;
    description: string;
    icon: string;
    category: string;
    is_builtin: boolean;
    capability_bullets?: string[];
    has_bootstrap?: boolean;
}

interface Props {
    open: boolean;
    onClose: () => void;
}

// Curated list for the "Popular" tab — covers one role from each broad need
// (personal assistant, project management, marketing, engineering, research, trading).
// Matches `AgentTemplate.name` exactly.
const FEATURED_TEMPLATE_NAMES = new Set<string>([
    'Private Assistant',
    'Chief of Staff',
    'Project Manager',
    'Growth Hacker',
    'Content Creator',
    'Frontend Developer',
    'Code Reviewer',
    'Rapid Prototyper',
    'Market Researcher',
    'Watchlist Monitor',
    'Trading Journal Coach',
    'Market Intel Aggregator',
]);

type TabId = 'popular' | 'software-development' | 'marketing' | 'office' | 'trading' | 'bundle';

export default function TalentMarketModal({ open, onClose }: Props) {
    const { t, i18n } = useTranslation();
    const isChinese = i18n.language.startsWith('zh');
    // Chosen template → hands off to PostHireSettingsModal. The market modal
    // stays mounted behind so the user can cancel and pick someone else.
    const [pendingTemplate, setPendingTemplate] = useState<Template | null>(null);
    // Chosen bundle → hands off to BundleHireModal (parallel to pendingTemplate).
    const [pendingBundle, setPendingBundle] = useState<BundleSummary | null>(null);
    const [customModalOpen, setCustomModalOpen] = useState(false);
    const [activeTab, setActiveTab] = useState<TabId>('popular');
    const [searchQuery, setSearchQuery] = useState('');

    const { data: templates = [], isLoading } = useQuery({
        queryKey: ['agent-templates'],
        queryFn: () => agentApi.templates(),
        enabled: open,
    });

    const { data: bundlesRaw = [], isLoading: bundlesLoading } = useQuery({
        queryKey: ['agent-bundles'],
        queryFn: () => bundleApi.list(),
        enabled: open,
    });

    // Filter bundles by current UI locale — a CN user only sees CN-native
    // bundles (agent souls / names in Chinese), an EN user only sees EN-native
    // ones. This avoids hiring English-speaking agents from a Chinese-looking
    // card or vice versa. Bundles missing `language` (legacy) treated as 'zh'.
    const bundles = bundlesRaw.filter((b: BundleSummary) => {
        const lang = (b.language || 'zh').toLowerCase();
        return isChinese ? lang === 'zh' : lang === 'en';
    });

    const tabs: Array<{ id: TabId; label: string }> = [
        { id: 'popular', label: t('talentMarket.tabPopular') },
        { id: 'software-development', label: t('talentMarket.tabSWE') },
        { id: 'marketing', label: t('talentMarket.tabMarketing') },
        { id: 'office', label: t('talentMarket.tabOffice') },
        { id: 'trading', label: t('talentMarket.tabTrading') },
        { id: 'bundle', label: t('talentMarket.tabBundle') },
    ];

    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape' && !pendingTemplate && !pendingBundle && !customModalOpen) onClose();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open, onClose, pendingTemplate, pendingBundle, customModalOpen]);

    if (!open) return null;

    const builtins: Template[] = templates.filter((t: Template) => t.is_builtin);
    const trimmedQuery = searchQuery.trim().toLowerCase();
    const isSearching = trimmedQuery.length > 0;

    // When searching, ignore the active tab and show matches across all
    // categories. Otherwise filter by the selected tab. Search matches against
    // both the canonical (English) name + description AND the localized
    // versions returned by translateTemplate, so a Chinese keyword like
    // "前端" finds the Frontend Developer card.
    const visibleTemplates: Template[] = isSearching
        ? builtins.filter((tpl) => {
            const localized = translateTemplate(tpl, isChinese);
            const haystack = [
                tpl.name,
                tpl.description,
                ...(tpl.capability_bullets || []),
                localized.name,
                localized.description,
                ...localized.bullets,
                tpl.category,
            ].join(' ').toLowerCase();
            return haystack.includes(trimmedQuery);
        })
        : activeTab === 'popular'
            ? builtins.filter((tpl) => FEATURED_TEMPLATE_NAMES.has(tpl.name))
            : builtins.filter((tpl) => tpl.category === activeTab);

    return (
        <div
            style={{
                position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
                background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                zIndex: 10000,
            }}
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div
                style={{
                    background: 'var(--bg-primary)', borderRadius: '12px',
                    width: '960px', maxWidth: '95vw',
                    height: 'min(88vh, 720px)',
                    border: '1px solid var(--border-subtle)',
                    boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
                    display: 'flex', flexDirection: 'column', overflow: 'hidden',
                }}
            >
                {/* Header */}
                <div style={{
                    padding: '24px 28px 12px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px',
                }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                        <h2 style={{ margin: 0, fontSize: '22px', fontWeight: 600 }}>
                            {t('talentMarket.title')}
                        </h2>
                        <p style={{ margin: '6px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
                            {t('talentMarket.subtitle')}
                        </p>
                    </div>
                    {/* Search box */}
                    <div style={{
                        display: 'flex', alignItems: 'center', gap: '8px',
                        height: '40px',
                        padding: '0 12px',
                        background: 'var(--bg-secondary)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: '8px',
                        width: '260px', maxWidth: '40vw',
                    }}>
                        <IconSearch size={15} stroke={1.6} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            placeholder={t('talentMarket.searchPlaceholder')}
                            style={{
                                flex: 1, minWidth: 0,
                                background: 'transparent', border: 'none', outline: 'none',
                                color: 'var(--text-primary)', fontSize: '13px',
                                height: '100%',
                            }}
                            aria-label={t('talentMarket.searchLabel')}
                        />
                        {searchQuery && (
                            <button
                                onClick={() => setSearchQuery('')}
                                title={t('common.clear')}
                                style={{
                                    background: 'transparent', border: 'none', cursor: 'pointer',
                                    color: 'var(--text-tertiary)', padding: '0', display: 'flex',
                                }}
                            >
                                <IconX size={14} stroke={1.6} />
                            </button>
                        )}
                    </div>
                    <button
                        onClick={onClose}
                        className="btn btn-ghost"
                        style={{ padding: '4px', display: 'flex', alignItems: 'center' }}
                        title={t('common.close', 'Close')}
                    >
                        <IconX size={18} stroke={1.5} />
                    </button>
                </div>

                {/* Category tabs */}
                <div
                    role="tablist"
                    aria-label={t('talentMarket.tabsAria')}
                    style={{
                        display: 'flex',
                        padding: '0 28px',
                        borderBottom: '1px solid var(--border-subtle)',
                        overflowX: 'auto',
                        flexShrink: 0,
                    }}
                >
                    {tabs.map((tab) => {
                        const isActive = !isSearching && activeTab === tab.id;
                        return (
                            <button
                                key={tab.id}
                                role="tab"
                                aria-selected={isActive}
                                onClick={() => { setSearchQuery(''); setActiveTab(tab.id); }}
                                onMouseEnter={(e) => {
                                    if (!isActive) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)';
                                }}
                                onMouseLeave={(e) => {
                                    if (!isActive) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)';
                                }}
                                style={{
                                    padding: '14px 18px',
                                    marginBottom: '-1px',
                                    marginRight: '8px',
                                    background: 'transparent',
                                    border: 'none',
                                    borderBottom: `2px solid ${isActive ? 'var(--text-primary)' : 'transparent'}`,
                                    color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                                    fontSize: '13px',
                                    fontWeight: 500,
                                    cursor: 'pointer',
                                    whiteSpace: 'nowrap',
                                    transition: 'color 120ms, border-color 120ms',
                                    outline: 'none',
                                }}
                            >
                                {tab.label}
                            </button>
                        );
                    })}
                </div>

                {/* Cards */}
                <div style={{
                    padding: '18px 28px 20px', overflowY: 'auto', flex: 1,
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))',
                    gap: '16px',
                    alignContent: 'start',
                }}>
                    {/* Bundle tab — show bundle cards, no CustomCard / TemplateCard */}
                    {!isSearching && activeTab === 'bundle' && (
                        <>
                            {bundlesLoading && (
                                <div style={{ gridColumn: '1 / -1', padding: '60px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                                    {t('common.loading', 'Loading...')}
                                </div>
                            )}
                            {!bundlesLoading && bundles.length === 0 && (
                                <div style={{ gridColumn: '1 / -1', padding: '40px', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                                    {t('talentMarket.emptyBundles')}
                                </div>
                            )}
                            {!bundlesLoading && bundles.map((b: BundleSummary) => (
                                <BundleCard
                                    key={b.id}
                                    bundle={b}
                                    isChinese={isChinese}
                                    onHire={() => setPendingBundle(b)}
                                />
                            ))}
                        </>
                    )}

                    {/* Non-bundle tabs (popular + categories) and search — render templates */}
                    {(isSearching || activeTab !== 'bundle') && (
                        <>
                            {isLoading && (
                                <div style={{ gridColumn: '1 / -1', padding: '60px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                                    {t('common.loading', 'Loading...')}
                                </div>
                            )}
                            {!isLoading && (
                                <CustomCard
                                    onClick={() => setCustomModalOpen(true)}
                                />
                            )}
                            {!isLoading && visibleTemplates.length === 0 && (
                                <div style={{ gridColumn: '1 / -1', padding: '40px', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                                    {isSearching
                                        ? t('talentMarket.emptySearch', { query: trimmedQuery })
                                        : t('talentMarket.empty')}
                                </div>
                            )}
                            {!isLoading && visibleTemplates.map((tpl: Template) => (
                                <TemplateCard
                                    key={tpl.id}
                                    tpl={tpl}
                                    hiring={false}
                                    isChinese={isChinese}
                                    onHire={() => setPendingTemplate(tpl)}
                                />
                            ))}
                        </>
                    )}
                </div>

                {/* Footer */}
                <div style={{
                    padding: '12px 28px 16px', textAlign: 'center', fontSize: '12px',
                    color: 'var(--text-tertiary)', borderTop: '1px solid var(--border-subtle)',
                }}>
                    {t('talentMarket.footer')}
                </div>
            </div>

            <PostHireSettingsModal
                template={pendingTemplate}
                open={!!pendingTemplate}
                onClose={() => setPendingTemplate(null)}
                onDone={() => { setPendingTemplate(null); onClose(); }}
            />
            <BundleHireModal
                bundle={pendingBundle}
                open={!!pendingBundle}
                onClose={() => setPendingBundle(null)}
                onDone={() => { setPendingBundle(null); onClose(); }}
            />
            <CustomAgentModal
                open={customModalOpen}
                initialMode="native"
                onClose={() => setCustomModalOpen(false)}
                onDone={() => { setCustomModalOpen(false); onClose(); }}
            />
        </div>
    );
}

function BundleCard({ bundle, isChinese, onHire }: {
    bundle: BundleSummary;
    isChinese: boolean;
    onHire: () => void;
}) {
    const { t } = useTranslation();
    // Pick lang-appropriate metadata. Bundle author can ship name_en /
    // description_en / capability_bullets_en for full EN coverage; when
    // absent (zh-only bundle), fall through to the primary CN field so the
    // card still renders rather than going blank.
    const displayName = (!isChinese && bundle.name_en) ? bundle.name_en : bundle.name;
    const displayDescription = (!isChinese && bundle.description_en) ? bundle.description_en : bundle.description;
    const displayBullets = (!isChinese && bundle.capability_bullets_en?.length)
        ? bundle.capability_bullets_en
        : bundle.capability_bullets;
    const bullets = displayBullets?.length
        ? displayBullets
        : [displayDescription].filter(Boolean);

    return (
        <div style={{
            border: '1px solid var(--border-subtle)', borderRadius: '10px',
            padding: '18px', display: 'flex', flexDirection: 'column',
            background: 'var(--bg-primary)',
            transition: 'border-color 120ms',
            position: 'relative',
        }}>
            {/* "Team" / "套装" badge in corner */}
            <div style={{
                position: 'absolute', top: '10px', right: '10px',
                fontSize: '10px', fontWeight: 600, letterSpacing: '0.04em',
                padding: '2px 8px', borderRadius: '10px',
                background: 'var(--bg-secondary)', color: 'var(--text-secondary)',
                textTransform: 'uppercase',
            }}>
                {t('talentMarket.bundleBadge')}
            </div>

            <div style={{
                width: '40px', height: '40px', borderRadius: '8px',
                background: 'var(--bg-secondary)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '13px', fontWeight: 600, marginBottom: '14px',
                letterSpacing: '0.04em',
            }}>
                {bundle.icon || 'TM'}
            </div>
            <div style={{ fontSize: '15px', fontWeight: 600, marginBottom: '2px' }}>
                {displayName}
            </div>
            <div style={{
                fontSize: '10.5px', fontWeight: 500, letterSpacing: '0.04em',
                color: 'var(--text-tertiary)',
                marginBottom: '12px',
            }}>
                {t('bundleHire.summary', { agentCount: bundle.agent_count, mcpCount: bundle.mcp_count, relCount: bundle.relationship_count })}
            </div>
            <ul style={{
                margin: 0, padding: 0, listStyle: 'none', flex: 1,
                fontSize: '12.5px', color: 'var(--text-secondary)', lineHeight: 1.7,
            }}>
                {bullets.slice(0, 4).map((b, i) => (
                    <li key={i} style={{ display: 'flex', gap: '6px', alignItems: 'flex-start' }}>
                        <span style={{ color: 'var(--text-tertiary)', flexShrink: 0 }}>•</span>
                        <span>{b}</span>
                    </li>
                ))}
            </ul>
            <button
                className="btn btn-primary"
                onClick={onHire}
                style={{ marginTop: '16px', width: '100%' }}
            >
                {t('talentMarket.hireBundle')}
            </button>
        </div>
    );
}

function TemplateCard({ tpl, hiring, isChinese, onHire }: {
    tpl: Template;
    hiring: boolean;
    isChinese: boolean;
    onHire: () => void;
}) {
    const { t } = useTranslation();
    const localized = translateTemplate(tpl, isChinese);
    const bullets = localized.bullets.length
        ? localized.bullets
        : [localized.description].filter(Boolean);

    return (
        <div style={{
            border: '1px solid var(--border-subtle)', borderRadius: '10px',
            padding: '18px', display: 'flex', flexDirection: 'column',
            background: 'var(--bg-primary)',
            transition: 'border-color 120ms',
        }}>
            <div style={{
                width: '40px', height: '40px', borderRadius: '8px',
                background: 'var(--bg-secondary)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '13px', fontWeight: 600, marginBottom: '14px',
                letterSpacing: '0.04em',
            }}>
                {tpl.icon || 'AI'}
            </div>
            <div style={{ fontSize: '15px', fontWeight: 600, marginBottom: '2px' }}>
                {localized.name}
            </div>
            <div style={{
                fontSize: '10px', fontWeight: 500, letterSpacing: '0.06em',
                color: 'var(--text-tertiary)', textTransform: 'uppercase',
                marginBottom: '12px',
            }}>
                {tpl.category || 'general'}
            </div>
            <ul style={{
                margin: 0, padding: 0, listStyle: 'none', flex: 1,
                fontSize: '12.5px', color: 'var(--text-secondary)', lineHeight: 1.7,
            }}>
                {bullets.slice(0, 4).map((b, i) => (
                    <li key={i} style={{ display: 'flex', gap: '6px', alignItems: 'flex-start' }}>
                        <span style={{ color: 'var(--text-tertiary)', flexShrink: 0 }}>•</span>
                        <span>{b}</span>
                    </li>
                ))}
            </ul>
            <button
                className="btn btn-primary"
                onClick={onHire}
                disabled={hiring}
                style={{ marginTop: '16px', width: '100%' }}
            >
                {hiring ? t('talentMarket.hiring') : t('talentMarket.hire')}
            </button>
        </div>
    );
}

function CustomCard({ onClick }: { onClick: () => void }) {
    const { t, i18n } = useTranslation();
    const isChinese = i18n.language.startsWith('zh');
    return (
        <div
            onClick={onClick}
            style={{
                border: '1.5px dashed var(--border-subtle)', borderRadius: '10px',
                padding: '18px', display: 'flex', flexDirection: 'column',
                cursor: 'pointer',
                background: 'linear-gradient(135deg, rgba(255,255,255,0.97) 0%, rgba(255,255,255,0.92) 54%, rgba(249,246,238,0.82) 100%)',
                transition: 'border-color 120ms, background 120ms',
                position: 'relative',
                overflow: 'hidden',
            }}
            onMouseEnter={(e) => {
                (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--accent)';
            }}
            onMouseLeave={(e) => {
                (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border-subtle)';
            }}
        >
            <div
                aria-hidden="true"
                style={{
                    position: 'absolute',
                    inset: 0,
                    backgroundImage: `linear-gradient(90deg, rgba(255,255,255,0.97) 0%, rgba(255,255,255,0.84) 48%, rgba(255,255,255,0.18) 100%), url(${customAgentBackground})`,
                    backgroundRepeat: 'no-repeat',
                    backgroundPosition: 'right -44px center',
                    backgroundSize: '260px auto',
                    filter: 'grayscale(18%) saturate(76%) sepia(8%)',
                    opacity: 0.68,
                    pointerEvents: 'none',
                }}
            />
            <div style={{
                width: '40px', height: '40px', borderRadius: '8px',
                background: 'var(--bg-secondary)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                marginBottom: '14px', color: 'var(--text-secondary)',
                position: 'relative', zIndex: 1,
            }}>
                <IconPlus size={20} stroke={1.5} />
            </div>
            <div style={{ fontSize: '15px', fontWeight: 600, marginBottom: '2px', position: 'relative', zIndex: 1 }}>
                {t('talentMarket.customTitle', isChinese ? '自建 Agent' : 'Build custom')}
            </div>
            <div style={{
                fontSize: '10px', fontWeight: 500, letterSpacing: '0.06em',
                color: 'var(--text-tertiary)', textTransform: 'uppercase',
                marginBottom: '12px',
                position: 'relative', zIndex: 1,
            }}>
                {t('talentMarket.customCategory', 'Custom')}
            </div>
            <p style={{
                margin: 0, flex: 1, fontSize: '12.5px',
                color: 'var(--text-secondary)', lineHeight: 1.6,
                position: 'relative', zIndex: 1,
            }}>
                {t('talentMarket.customDescription', isChinese
                    ? '创建本地 Native Agent，按你的需求定义身份、权限和工具。'
                    : 'Create a native agent, then define its identity, permissions, and tools.')}
            </p>
            <div style={{
                marginTop: '14px',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                color: 'var(--text-tertiary)',
                fontSize: '11.5px',
                lineHeight: 1.2,
                position: 'relative',
                zIndex: 1,
            }}>
                <IconWorld size={13} stroke={1.5} style={{ flexShrink: 0 }} />
                <span>
                    {t('talentMarket.externalAgentHint', isChinese
                        ? '支持 Native、OpenClaw 等外部 Agent'
                        : 'Supports native, OpenClaw, and external agents')}
                </span>
            </div>
            <button
                className="btn btn-secondary"
                onClick={(e) => {
                    e.stopPropagation();
                    onClick();
                }}
                style={{ marginTop: '16px', width: '100%', position: 'relative', zIndex: 1 }}
            >
                {t('talentMarket.customStart', isChinese ? '开始' : 'Start')}
            </button>
        </div>
    );
}
