import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconPlus, IconSearch, IconWorld, IconX } from '@tabler/icons-react';
import { agentApi } from '../services/api';
import PostHireSettingsModal from './PostHireSettingsModal';
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

type TabId = 'popular' | 'software-development' | 'marketing' | 'office' | 'trading';

export default function TalentMarketModal({ open, onClose }: Props) {
    const { t, i18n } = useTranslation();
    const isChinese = i18n.language.startsWith('zh');
    // Chosen template → hands off to PostHireSettingsModal. The market modal
    // stays mounted behind so the user can cancel and pick someone else.
    const [pendingTemplate, setPendingTemplate] = useState<Template | null>(null);
    const [customModalOpen, setCustomModalOpen] = useState(false);
    const [activeTab, setActiveTab] = useState<TabId>('popular');
    const [searchQuery, setSearchQuery] = useState('');

    const { data: templates = [], isLoading } = useQuery({
        queryKey: ['agent-templates'],
        queryFn: () => agentApi.templates(),
        enabled: open,
    });

    const tabs: Array<{ id: TabId; label: string }> = [
        { id: 'popular', label: t('talentMarket.tabPopular', isChinese ? '热门推荐' : 'Popular') },
        { id: 'software-development', label: t('talentMarket.tabSWE', isChinese ? '软件开发' : 'Software Development') },
        { id: 'marketing', label: t('talentMarket.tabMarketing', isChinese ? '营销' : 'Marketing') },
        { id: 'office', label: t('talentMarket.tabOffice', isChinese ? '办公通用' : 'Office') },
        { id: 'trading', label: t('talentMarket.tabTrading', isChinese ? '交易投资' : 'Trading') },
    ];

    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape' && !pendingTemplate && !customModalOpen) onClose();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open, onClose, pendingTemplate, customModalOpen]);

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
                            {t('talentMarket.title', isChinese ? '人才市场' : 'Talent Market')}
                        </h2>
                        <p style={{ margin: '6px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
                            {t('talentMarket.subtitle', isChinese ? '挑选一位专业成员加入你的公司' : 'Pick a professional to join your company')}
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
                            placeholder={t(
                                'talentMarket.searchPlaceholder',
                                isChinese ? '搜索 Agent 名称或能力…' : 'Search agents by name or skill…',
                            )}
                            style={{
                                flex: 1, minWidth: 0,
                                background: 'transparent', border: 'none', outline: 'none',
                                color: 'var(--text-primary)', fontSize: '13px',
                                height: '100%',
                            }}
                            aria-label={t('talentMarket.searchLabel', isChinese ? '搜索 Agent' : 'Search agents')}
                        />
                        {searchQuery && (
                            <button
                                onClick={() => setSearchQuery('')}
                                title={t('common.clear', isChinese ? '清空' : 'Clear')}
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
                    aria-label={t('talentMarket.tabsAria', isChinese ? '分类筛选' : 'Category filters')}
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
                                ? t('talentMarket.emptySearch', isChinese ? `没有匹配 "${trimmedQuery}" 的 Agent` : `No agents match "${trimmedQuery}"`)
                                : t('talentMarket.empty', isChinese ? '这个分类下还没有模板' : 'No templates in this category yet')}
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
                </div>

                {/* Footer */}
                <div style={{
                    padding: '12px 28px 16px', textAlign: 'center', fontSize: '12px',
                    color: 'var(--text-tertiary)', borderTop: '1px solid var(--border-subtle)',
                }}>
                    {t('talentMarket.footer', isChinese ? '点击聘用·可随时在设置中调整' : 'Hire now · adjust anything in settings later')}
                </div>
            </div>

            <PostHireSettingsModal
                template={pendingTemplate}
                open={!!pendingTemplate}
                onClose={() => setPendingTemplate(null)}
                onDone={() => { setPendingTemplate(null); onClose(); }}
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
                {hiring ? t('talentMarket.hiring', isChinese ? '聘用中…' : 'Hiring...') : t('talentMarket.hire', isChinese ? '聘用' : 'Hire')}
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
