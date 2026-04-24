import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconPlus, IconX } from '@tabler/icons-react';
import { agentApi } from '../services/api';
import PostHireSettingsModal from './PostHireSettingsModal';

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

export default function TalentMarketModal({ open, onClose }: Props) {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const isChinese = i18n.language.startsWith('zh');
    // Chosen template → hands off to PostHireSettingsModal. The market modal
    // stays mounted behind so the user can cancel and pick someone else.
    const [pendingTemplate, setPendingTemplate] = useState<Template | null>(null);

    const { data: templates = [], isLoading } = useQuery({
        queryKey: ['agent-templates'],
        queryFn: () => agentApi.templates(),
        enabled: open,
    });

    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape' && !pendingTemplate) onClose();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open, onClose, pendingTemplate]);

    if (!open) return null;

    const builtins = templates.filter((t: Template) => t.is_builtin);

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
                    width: '960px', maxWidth: '95vw', maxHeight: '88vh',
                    border: '1px solid var(--border-subtle)',
                    boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
                    display: 'flex', flexDirection: 'column', overflow: 'hidden',
                }}
            >
                {/* Header */}
                <div style={{
                    padding: '24px 28px 12px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
                }}>
                    <div>
                        <h2 style={{ margin: 0, fontSize: '22px', fontWeight: 600 }}>
                            {t('talentMarket.title', isChinese ? '人才市场' : 'Talent Market')}
                        </h2>
                        <p style={{ margin: '6px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
                            {t('talentMarket.subtitle', isChinese ? '挑选一位专业成员加入你的公司' : 'Pick a professional to join your company')}
                        </p>
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

                {/* Cards */}
                <div style={{
                    padding: '12px 28px 20px', overflowY: 'auto', flex: 1,
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
                    {!isLoading && builtins.map((tpl: Template) => (
                        <TemplateCard
                            key={tpl.id}
                            tpl={tpl}
                            hiring={false}
                            onHire={() => setPendingTemplate(tpl)}
                        />
                    ))}
                    {!isLoading && (
                        <CustomCard
                            onClick={() => { onClose(); navigate('/agents/new'); }}
                        />
                    )}
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
        </div>
    );
}

function TemplateCard({ tpl, hiring, onHire }: { tpl: Template; hiring: boolean; onHire: () => void }) {
    const { t } = useTranslation();
    const bullets = tpl.capability_bullets?.length
        ? tpl.capability_bullets
        : [tpl.description].filter(Boolean);

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
                fontSize: '16px', fontWeight: 600, marginBottom: '14px',
            }}>
                {tpl.icon || '🤖'}
            </div>
            <div style={{ fontSize: '15px', fontWeight: 600, marginBottom: '2px' }}>
                {tpl.name}
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
                {hiring ? t('talentMarket.hiring', 'Hiring...') : t('talentMarket.hire', '聘用')}
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
                cursor: 'pointer', background: 'transparent',
                transition: 'border-color 120ms, background 120ms',
            }}
            onMouseEnter={(e) => {
                (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--accent)';
            }}
            onMouseLeave={(e) => {
                (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border-subtle)';
            }}
        >
            <div style={{
                width: '40px', height: '40px', borderRadius: '8px',
                background: 'var(--bg-secondary)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                marginBottom: '14px', color: 'var(--text-secondary)',
            }}>
                <IconPlus size={20} stroke={1.5} />
            </div>
            <div style={{ fontSize: '15px', fontWeight: 600, marginBottom: '2px' }}>
                {t('talentMarket.customTitle', isChinese ? '自建 Agent' : 'Build custom')}
            </div>
            <div style={{
                fontSize: '10px', fontWeight: 500, letterSpacing: '0.06em',
                color: 'var(--text-tertiary)', textTransform: 'uppercase',
                marginBottom: '12px',
            }}>
                {t('talentMarket.customCategory', 'Custom')}
            </div>
            <p style={{
                margin: 0, flex: 1, fontSize: '12.5px',
                color: 'var(--text-secondary)', lineHeight: 1.6,
            }}>
                {t('talentMarket.customDescription', isChinese
                    ? '从零开始定义身份、性格、权限和工具，完全按你的需求打造。'
                    : 'Define identity, personality, permissions, and tools from scratch.')}
            </p>
            <button
                className="btn btn-secondary"
                onClick={onClick}
                style={{ marginTop: '16px', width: '100%' }}
            >
                {t('talentMarket.customStart', isChinese ? '开始' : 'Start')}
            </button>
        </div>
    );
}
