import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconPlus, IconSearch, IconWorld, IconX } from '@tabler/icons-react';
import { agentApi } from '../services/api';
import PostHireSettingsModal from './PostHireSettingsModal';
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
    const navigate = useNavigate();
    const isChinese = i18n.language.startsWith('zh');
    // Chosen template → hands off to PostHireSettingsModal. The market modal
    // stays mounted behind so the user can cancel and pick someone else.
    const [pendingTemplate, setPendingTemplate] = useState<Template | null>(null);
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
            if (e.key === 'Escape' && !pendingTemplate) onClose();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open, onClose, pendingTemplate]);

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
            className="talent-market-modal"
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div className="talent-market-content">
                {/* Header */}
                <div className="talent-market-header">
                    <div style={{ flex: 1, minWidth: 0 }}>
                        <h2 className="talent-market-title">
                            {t('talentMarket.title', isChinese ? '人才市场' : 'Talent Market')}
                        </h2>
                        <p className="talent-market-subtitle">
                            {t('talentMarket.subtitle', isChinese ? '挑选一位专业成员加入你的公司' : 'Pick a professional to join your company')}
                        </p>
                    </div>
                    {/* Search box */}
                    <div className="talent-market-search">
                        <IconSearch size={15} stroke={1.6} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            placeholder={t(
                                'talentMarket.searchPlaceholder',
                                isChinese ? '搜索 Agent 名称或能力…' : 'Search agents by name or skill…',
                            )}
                            aria-label={t('talentMarket.searchLabel', isChinese ? '搜索 Agent' : 'Search agents')}
                        />
                        {searchQuery && (
                            <button
                                onClick={() => setSearchQuery('')}
                                title={t('common.clear', isChinese ? '清空' : 'Clear')}
                                className="talent-market-search-clear"
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
                    className="talent-market-tabs"
                >
                    {tabs.map((tab) => {
                        const isActive = !isSearching && activeTab === tab.id;
                        return (
                            <button
                                key={tab.id}
                                role="tab"
                                aria-selected={isActive}
                                onClick={() => { setSearchQuery(''); setActiveTab(tab.id); }}
                                className={`talent-market-tab${isActive ? ' active' : ''}`}
                            >
                                {tab.label}
                            </button>
                        );
                    })}
                </div>

                {/* Cards */}
                <div className="talent-market-cards">
                    {isLoading && (
                        <div style={{ gridColumn: '1 / -1', padding: '60px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                            {t('common.loading', 'Loading...')}
                        </div>
                    )}
                    {!isLoading && (
                        <CustomCard
                            onClick={() => { onClose(); navigate('/agents/new'); }}
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
                <div className="talent-market-footer">
                    {t('talentMarket.footer', isChinese ? '点击聘用·可随时在设置中调整' : 'Hire now · adjust anything in settings later')}
                </div>
            </div>

            <PostHireSettingsModal
                template={pendingTemplate}
                open={!!pendingTemplate}
                onClose={() => setPendingTemplate(null)}
                onDone={() => { setPendingTemplate(null); onClose(); }}
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
        <div className="talent-card">
            <div className="talent-card-icon">
                {tpl.icon || 'AI'}
            </div>
            <div className="talent-card-title">
                {localized.name}
            </div>
            <div className="talent-card-category">
                {tpl.category || 'general'}
            </div>
            <ul className="talent-card-bullets">
                {bullets.slice(0, 4).map((b, i) => (
                    <li key={i}>
                        <span className="talent-card-bullet-dot">•</span>
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
            className="talent-card-custom"
        >
            <div
                aria-hidden="true"
                className="talent-card-custom-bg"
                style={{ backgroundImage: `url(${customAgentBackground})` }}
            />
            <div className="talent-card-custom-icon">
                <IconPlus size={20} stroke={1.5} />
            </div>
            <div className="talent-card-custom-title">
                {t('talentMarket.customTitle', isChinese ? '自建 Agent' : 'Build custom')}
            </div>
            <div className="talent-card-custom-category">
                {t('talentMarket.customCategory', 'Custom')}
            </div>
            <p className="talent-card-custom-description">
                {t('talentMarket.customDescription', isChinese
                    ? '创建本地 Native Agent，按你的需求定义身份、权限和工具。'
                    : 'Create a native agent, then define its identity, permissions, and tools.')}
            </p>
            <div className="talent-card-custom-hint">
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
                style={{ marginTop: '16px', width: '100%' }}
            >
                {t('talentMarket.customStart', isChinese ? '开始' : 'Start')}
            </button>
        </div>
    );
}
