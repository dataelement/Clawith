import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { IconChevronDown, IconChevronRight, IconUsers, IconPlug, IconStarFilled, IconX } from '@tabler/icons-react';
import { bundleApi, type BundleSummary } from '../services/api';
import { useDialog } from './Dialog/DialogProvider';

interface Props {
    bundle: BundleSummary | null;
    open: boolean;
    onClose: () => void;
    onDone?: () => void;
}

/**
 * Bundle hire flow. Shows everything that will be created (N agents + R MCPs
 * + K relationships), lets the user pick a visibility scope, then fires the
 * single transactional POST /api/bundles/{slug}/hire.
 *
 * No customisation surface — bundle hire is all-or-nothing per design. The
 * only choice is visibility (only_me / company / custom). The custom option
 * mirrors single-agent hire: creator-only access on hire, with per-agent
 * member-grant via Settings later.
 */
export default function BundleHireModal({ bundle, open, onClose, onDone }: Props) {
    const { t, i18n } = useTranslation();
    const isChinese = i18n.language.startsWith('zh');
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const dialog = useDialog();

    const [visibility, setVisibility] = useState<'only_me' | 'company' | 'custom'>('only_me');
    const [showRelationships, setShowRelationships] = useState(false);

    // Fetch detail when modal opens — list endpoint doesn't include nested
    // children + souls. The detail call is cheap (single bundle, one query).
    const { data: detail, isLoading } = useQuery({
        queryKey: ['agent-bundle', bundle?.slug],
        queryFn: () => bundleApi.get(bundle!.slug),
        enabled: open && !!bundle?.slug,
    });

    useEffect(() => {
        if (!open) {
            setVisibility('only_me');
            setShowRelationships(false);
        }
    }, [open]);

    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !hire.isPending) onClose(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, onClose]);

    const hire = useMutation({
        mutationFn: () => {
            if (!bundle) return Promise.reject(new Error('No bundle'));
            return bundleApi.hire(bundle.slug, { visibility });
        },
        onSuccess: (result) => {
            queryClient.invalidateQueries({ queryKey: ['agents'] });
            (onDone || onClose)();
            // Land the user on the principal (★ point-of-contact, e.g. the
            // Research Manager) — not whichever agent happens to be first in
            // position order. Fall back to the first agent if the bundle didn't
            // designate a principal.
            const target =
                (result.principal_slug
                    && result.agents.find(a => a.slug === result.principal_slug))
                || result.agents[0];
            if (target) navigate(`/agents/${target.agent_id}#chat`);
        },
        onError: async (err: any) => {
            await dialog.alert(
                t('bundleHire.error'),
                { type: 'error', details: String(err?.message || err) },
            );
        },
    });

    if (!open || !bundle) return null;

    const busy = hire.isPending;
    const agents = detail?.agents || [];
    const mcps = detail?.mcp_servers || [];
    const relationships = detail?.relationships || [];

    return (
        <div
            style={{
                position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
                background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                zIndex: 10001,
            }}
            onClick={e => { if (e.target === e.currentTarget && !busy) onClose(); }}
        >
            <div style={{
                background: 'var(--bg-primary)', borderRadius: '12px',
                width: '560px', maxWidth: '94vw',
                maxHeight: '88vh',
                border: '1px solid var(--border-subtle)',
                boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
                display: 'flex', flexDirection: 'column', overflow: 'hidden',
            }}>
                <div style={{ padding: '22px 26px 8px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                    <div style={{ minWidth: 0, flex: 1 }}>
                        <h3 style={{ margin: 0, fontSize: '17px', fontWeight: 600 }}>
                            {t('bundleHire.title')}
                        </h3>
                        <p style={{ margin: '4px 0 0', fontSize: '12.5px', color: 'var(--text-secondary)' }}>
                            {((!isChinese && bundle.name_en) ? bundle.name_en : bundle.name)} · {t('bundleHire.summary', { agentCount: bundle.agent_count, mcpCount: bundle.mcp_count, relCount: bundle.relationship_count })}
                        </p>
                    </div>
                    <button onClick={onClose} className="btn btn-ghost" disabled={busy} style={{ padding: '4px' }} title={t('common.close', 'Close')}>
                        <IconX size={16} stroke={1.5} />
                    </button>
                </div>

                <div style={{ padding: '8px 26px 8px', display: 'flex', flexDirection: 'column', gap: '16px', overflowY: 'auto', flex: 1 }}>
                    {(() => {
                        const desc = (!isChinese && bundle.description_en) ? bundle.description_en : bundle.description;
                        return desc ? (
                            <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                                {desc}
                            </p>
                        ) : null;
                    })()}

                    {/* Agents that will be created */}
                    <section>
                        <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <IconUsers size={14} stroke={1.6} />
                            {t('bundleHire.agentsHeading', { count: bundle.agent_count })}
                        </div>
                        {isLoading ? (
                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('common.loading', 'Loading...')}</div>
                        ) : (
                            <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                {agents.map(a => (
                                    <li key={a.slug} style={{
                                        display: 'flex', alignItems: 'center', gap: '10px',
                                        padding: '8px 10px', background: 'var(--bg-secondary)', borderRadius: '8px',
                                    }}>
                                        <div style={{
                                            width: '28px', height: '28px', borderRadius: '6px',
                                            background: 'var(--bg-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                                            fontSize: '11px', fontWeight: 600, flexShrink: 0,
                                        }}>
                                            {a.position}
                                        </div>
                                        <div style={{ minWidth: 0, flex: 1 }}>
                                            <div style={{ fontSize: '13px', fontWeight: 500, display: 'flex', alignItems: 'center', gap: '5px' }}>
                                                {a.name}
                                                {detail?.principal_slug === a.slug && (
                                                    <IconStarFilled
                                                        size={11}
                                                        style={{ color: '#f59e0b', flexShrink: 0 }}
                                                        aria-label={t('bundleHire.principal')}
                                                        title={t('bundleHire.principal')}
                                                    />
                                                )}
                                            </div>
                                            {a.role_description && (
                                                <div style={{ fontSize: '11.5px', color: 'var(--text-tertiary)', marginTop: '1px' }}>
                                                    {a.role_description}
                                                </div>
                                            )}
                                        </div>
                                        {a.default_mcp_attach && a.default_mcp_attach.length > 0 && (
                                            <span style={{
                                                fontSize: '10.5px', fontWeight: 500, padding: '2px 6px',
                                                background: 'var(--bg-primary)', borderRadius: '4px',
                                                color: 'var(--text-secondary)',
                                            }}>
                                                {t('bundleHire.mcpBadge', { count: a.default_mcp_attach.length })}
                                            </span>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </section>

                    {/* MCPs to register */}
                    {mcps.length > 0 && (
                        <section>
                            <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                <IconPlug size={14} stroke={1.6} />
                                {t('bundleHire.mcpsHeading', { count: mcps.length })}
                            </div>
                            <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                {mcps.map(m => (
                                    <li key={m.local_key} style={{
                                        fontSize: '12px', color: 'var(--text-secondary)',
                                        padding: '4px 0',
                                    }}>
                                        <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{m.server_name}</span>
                                        <span style={{ color: 'var(--text-tertiary)' }}> · {m.url}</span>
                                    </li>
                                ))}
                            </ul>
                        </section>
                    )}

                    {/* Relationships — collapsible (often K can be large) */}
                    {relationships.length > 0 && (
                        <section>
                            <button
                                onClick={() => setShowRelationships(v => !v)}
                                style={{
                                    background: 'transparent', border: 'none', padding: 0, cursor: 'pointer',
                                    display: 'flex', alignItems: 'center', gap: '4px',
                                    fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)',
                                }}
                            >
                                {showRelationships ? <IconChevronDown size={14} stroke={1.6} /> : <IconChevronRight size={14} stroke={1.6} />}
                                {t('bundleHire.relsHeading', { count: relationships.length })}
                            </button>
                            {showRelationships && (
                                <ul style={{ margin: '8px 0 0', padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                    {relationships.map((r, i) => (
                                        <li key={i} style={{
                                            fontSize: '11.5px', color: 'var(--text-secondary)',
                                            padding: '2px 0', fontFamily: 'monospace',
                                        }}>
                                            {r.from_slug} → {r.to_slug} <span style={{ color: 'var(--text-tertiary)' }}>· {r.relation}</span>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </section>
                    )}

                    {/* Visibility */}
                    <section>
                        <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px' }}>
                            {t('bundleHire.visibility')}
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                            <RadioRow
                                selected={visibility === 'only_me'}
                                onClick={() => !busy && setVisibility('only_me')}
                                title={t('bundleHire.visOnlyMeTitle')}
                                hint={t('bundleHire.visOnlyMeHint', { count: bundle.agent_count })}
                            />
                            <RadioRow
                                selected={visibility === 'company'}
                                onClick={() => !busy && setVisibility('company')}
                                title={t('bundleHire.visCompanyTitle')}
                                hint={t('bundleHire.visCompanyHint')}
                            />
                            <RadioRow
                                selected={visibility === 'custom'}
                                onClick={() => !busy && setVisibility('custom')}
                                title={t('bundleHire.visCustomTitle')}
                                hint={t('bundleHire.visCustomHint')}
                            />
                        </div>
                    </section>
                </div>

                <div style={{
                    padding: '16px 26px 18px',
                    borderTop: '1px solid var(--border-subtle)',
                    display: 'flex', justifyContent: 'flex-end', gap: '10px',
                }}>
                    <button className="btn btn-ghost" onClick={onClose} disabled={busy}>
                        {t('common.cancel')}
                    </button>
                    <button
                        className="btn btn-primary"
                        onClick={() => hire.mutate()}
                        disabled={busy || isLoading}
                    >
                        {busy
                            ? t('bundleHire.hiring')
                            : t('bundleHire.confirm', { count: bundle.agent_count })}
                    </button>
                </div>
            </div>
        </div>
    );
}


function RadioRow({ selected, onClick, title, hint }: {
    selected: boolean;
    onClick: () => void;
    title: string;
    hint: string;
}) {
    return (
        <button
            onClick={onClick}
            style={{
                display: 'flex', alignItems: 'flex-start', gap: '10px', textAlign: 'left',
                padding: '10px 12px',
                background: selected ? 'var(--bg-secondary)' : 'transparent',
                border: `1px solid ${selected ? 'var(--accent, var(--text-primary))' : 'var(--border-subtle)'}`,
                borderRadius: '8px',
                cursor: 'pointer',
                width: '100%',
            }}
        >
            <div style={{
                width: '14px', height: '14px', borderRadius: '50%',
                border: `2px solid ${selected ? 'var(--accent, var(--text-primary))' : 'var(--border-strong, var(--text-tertiary))'}`,
                marginTop: '2px', flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
                {selected && <div style={{
                    width: '6px', height: '6px', borderRadius: '50%',
                    background: 'var(--accent, var(--text-primary))',
                }} />}
            </div>
            <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: '13px', fontWeight: 500 }}>{title}</div>
                <div style={{ fontSize: '11.5px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{hint}</div>
            </div>
        </button>
    );
}
