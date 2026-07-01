import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
    IconAlertTriangle,
    IconCheck,
    IconCpu,
    IconPlugConnected,
    IconSettings,
    IconSparkles,
    IconUser,
    IconX,
} from '@tabler/icons-react';
import { agentApi, authApi, enterpriseApi, tenantApi } from '../services/api';
import { useDialog } from './Dialog/DialogProvider';
import LinearCopyButton from './LinearCopyButton';
import { buildOpenClawInstruction } from '../utils/openClawInstruction';

type Mode = 'native' | 'openclaw';
type Visibility = 'company' | 'only_me' | 'custom';

interface Model {
    id: string;
    label?: string;
    enabled?: boolean;
}

interface CreatedAgent {
    id: string;
    name: string;
    api_key?: string;
}

interface Props {
    open: boolean;
    initialMode?: Mode;
    onClose: () => void;
    onDone?: () => void;
}

export default function CustomAgentModal({ open, initialMode = 'native', onClose, onDone }: Props) {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const dialog = useDialog();

    const [mode, setMode] = useState<Mode>(initialMode);
    const [name, setName] = useState('');
    const [roleDescription, setRoleDescription] = useState('');
    const [visibility, setVisibility] = useState<Visibility>('only_me');
    const [modelId, setModelId] = useState('');
    const [createdExternal, setCreatedExternal] = useState<CreatedAgent | null>(null);

    const { data: myTenant } = useQuery({
        queryKey: ['tenant', 'me'],
        queryFn: () => tenantApi.me(),
        enabled: open,
        staleTime: 5 * 60 * 1000,
    });

    const { data: currentUser } = useQuery({
        queryKey: ['auth', 'me'],
        queryFn: authApi.me,
        enabled: open,
        staleTime: 5 * 60 * 1000,
    });

    const { data: models = [] } = useQuery({
        queryKey: ['llm-models'],
        queryFn: enterpriseApi.llmModels,
        enabled: open,
    });

    const enabledModels = useMemo(
        () => (models as Model[]).filter((m) => m.enabled !== false),
        [models],
    );

    const canManageModels = currentUser?.role === 'platform_admin'
        || currentUser?.role === 'org_admin'
        || !!currentUser?.is_platform_admin;
    const nativeHasNoModel = mode === 'native' && enabledModels.length === 0;
    const disabledByNoModel = nativeHasNoModel
        ? t('customAgentModal.noModelButtonHint')
        : undefined;
    const openModelSettings = () => {
        (onDone || onClose)();
        navigate('/enterprise#llm');
    };

    useEffect(() => {
        if (!open) return;
        setMode(initialMode);
    }, [open, initialMode]);

    useEffect(() => {
        if (!open || modelId) return;
        const preferred = myTenant?.default_model_id && enabledModels.find((m) => m.id === myTenant.default_model_id)
            ? myTenant.default_model_id
            : (enabledModels[0]?.id || '');
        if (preferred) setModelId(preferred);
    }, [open, modelId, myTenant?.default_model_id, enabledModels]);

    useEffect(() => {
        if (!open) {
            setMode(initialMode);
            setName('');
            setRoleDescription('');
            setVisibility('only_me');
            setModelId('');
            setCreatedExternal(null);
        }
    }, [open, initialMode]);

    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape' && !createAgent.isPending) onClose();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, onClose]);

    const createAgent = useMutation({
        mutationFn: async ({ chatNow }: { chatNow: boolean }) => {
            const trimmedName = name.trim();
            if (!trimmedName) {
                throw new Error(t('customAgentModal.nameRequired'));
            }
            if (mode === 'native' && enabledModels.length === 0) {
                throw new Error(t('customAgentModal.noModelError'));
            }
            if (mode === 'native' && !modelId) {
                throw new Error(t('customAgentModal.modelRequired'));
            }

            const currentTenant = localStorage.getItem('current_tenant_id');
            const payload: any = {
                name: trimmedName,
                agent_type: mode,
                role_description: roleDescription.trim() || undefined,
                permission_scope_type: visibility === 'company' ? 'company' : visibility === 'custom' ? 'custom' : 'user',
                permission_scope_ids: [],
                permission_access_level: 'use',
                tenant_id: currentTenant || undefined,
                skill_ids: [],
            };

            if (mode === 'native') {
                payload.primary_model_id = modelId || undefined;
            }

            const agent = await agentApi.create(payload);
            return { agent, chatNow };
        },
        onSuccess: ({ agent, chatNow }: { agent: CreatedAgent; chatNow: boolean }) => {
            queryClient.invalidateQueries({ queryKey: ['agents'] });
            if (mode === 'openclaw') {
                setCreatedExternal(agent);
                return;
            }
            (onDone || onClose)();
            if (chatNow) navigate(`/agents/${agent.id}#chat`);
        },
        onError: async (err: any) => {
            await dialog.alert(t('customAgentModal.creationFailed'), {
                type: 'error',
                details: String(err?.message || err),
            });
        },
    });

    if (!open) return null;

    const busy = createAgent.isPending;
    const setupInstruction = createdExternal?.api_key
        ? buildOpenClawInstruction(createdExternal.api_key, !!i18n.language?.startsWith('zh'))
        : '';

    const closeSuccess = () => {
        (onDone || onClose)();
        if (createdExternal) navigate(`/agents/${createdExternal.id}`);
    };

    return (
        <div
            style={{
                position: 'fixed', inset: 0,
                background: 'rgba(0,0,0,0.55)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                zIndex: 10002,
            }}
            onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}
        >
            <div
                role="dialog"
                aria-modal="true"
                style={{
                    background: 'var(--bg-primary)',
                    borderRadius: '12px',
                    width: '520px',
                    maxWidth: '92vw',
                    maxHeight: '86vh',
                    border: '1px solid var(--border-subtle)',
                    boxShadow: '0 22px 70px rgba(0,0,0,0.42)',
                    display: 'flex',
                    flexDirection: 'column',
                    overflow: 'hidden',
                }}
            >
                {createdExternal ? (
                    <ExternalSuccess
                        agent={createdExternal}
                        setupInstruction={setupInstruction}
                        t={t}
                        onClose={onClose}
                        onEnter={closeSuccess}
                    />
                ) : (
                    <>
                        <div style={{ padding: '22px 26px 10px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px' }}>
                            <div>
                                <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 650 }}>
                                    {mode === 'native'
                                        ? t('customAgentModal.nativeTitle')
                                        : t('customAgentModal.externalTitle')}
                                </h3>
                                <p style={{ margin: '5px 0 0', fontSize: '12.5px', color: 'var(--text-secondary)' }}>
                                    {mode === 'native'
                                        ? t('customAgentModal.nativeSubtitle')
                                        : t('customAgentModal.externalSubtitle')}
                                </p>
                            </div>
                            <button onClick={onClose} className="btn btn-ghost" disabled={busy} style={{ padding: '4px', display: 'flex' }}>
                                <IconX size={16} stroke={1.5} />
                            </button>
                        </div>

                        <div style={{ padding: '0 26px 18px', overflowY: 'auto' }}>
                            <div style={{
                                display: 'grid',
                                gridTemplateColumns: '1fr 1fr',
                                gap: '8px',
                                padding: '4px',
                                border: '1px solid var(--border-subtle)',
                                borderRadius: '10px',
                                background: 'var(--bg-secondary)',
                                marginBottom: '18px',
                            }}>
                                <ModeButton
                                    active={mode === 'native'}
                                    icon={<IconSparkles size={15} stroke={1.7} />}
                                    label={t('customAgentModal.nativeMode')}
                                    onClick={() => !busy && setMode('native')}
                                />
                                <ModeButton
                                    active={mode === 'openclaw'}
                                    icon={<IconPlugConnected size={15} stroke={1.7} />}
                                    label={t('customAgentModal.externalMode')}
                                    onClick={() => !busy && setMode('openclaw')}
                                />
                            </div>

                            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                                <Field label={t('customAgentModal.name')} required>
                                    <input
                                        className="form-input"
                                        value={name}
                                        onChange={(e) => setName(e.target.value)}
                                        maxLength={100}
                                        placeholder={mode === 'native'
                                            ? t('customAgentModal.namePlaceholderNative')
                                            : t('customAgentModal.namePlaceholderExternal')}
                                        disabled={busy}
                                        autoFocus
                                        style={{ width: '100%' }}
                                    />
                                </Field>

                                <Field label={t('customAgentModal.role')}>
                                    <textarea
                                        className="form-input"
                                        value={roleDescription}
                                        onChange={(e) => setRoleDescription(e.target.value)}
                                        maxLength={500}
                                        placeholder={t('customAgentModal.rolePlaceholder')}
                                        disabled={busy}
                                        rows={3}
                                        style={{ width: '100%', resize: 'vertical', minHeight: '76px' }}
                                    />
                                </Field>

                                <section>
                                    <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px' }}>
                                        {t('customAgentModal.visibility')}
                                    </div>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                        <RadioRow
                                            selected={visibility === 'company'}
                                            onClick={() => !busy && setVisibility('company')}
                                            title={t('customAgentModal.visibilityCompany')}
                                            hint={t('customAgentModal.visibilityCompanyHint')}
                                        />
                                        <RadioRow
                                            selected={visibility === 'only_me'}
                                            onClick={() => !busy && setVisibility('only_me')}
                                            title={t('customAgentModal.visibilityOnlyMe')}
                                            hint={t('customAgentModal.visibilityOnlyMeHint')}
                                        />
                                        <RadioRow
                                            selected={visibility === 'custom'}
                                            onClick={() => !busy && setVisibility('custom')}
                                            title={t('customAgentModal.visibilityCustom')}
                                            hint={t('customAgentModal.visibilityCustomHint')}
                                        />
                                    </div>
                                </section>

                                {mode === 'native' && (
                                    <Field label={t('customAgentModal.model')} required>
                                        {enabledModels.length === 0 ? (
                                            <NoModelsNotice
                                                canManageModels={canManageModels}
                                                onConfigure={openModelSettings}
                                                t={t}
                                                allowExternalHint
                                            />
                                        ) : (
                                            <select
                                                className="form-input"
                                                value={modelId}
                                                onChange={(e) => setModelId(e.target.value)}
                                                disabled={busy}
                                                style={{ width: '100%' }}
                                            >
                                                {enabledModels.map((m) => (
                                                    <option key={m.id} value={m.id}>
                                                        {m.label || t('customAgentModal.modelFallback')}
                                                        {myTenant?.default_model_id === m.id ? ` · ${t('customAgentModal.defaultModel')}` : ''}
                                                    </option>
                                                ))}
                                            </select>
                                        )}
                                    </Field>
                                )}
                            </div>
                        </div>

                        <div style={{ padding: '16px 26px 20px', display: 'flex', justifyContent: 'flex-end', gap: '8px', borderTop: '1px solid var(--border-subtle)' }}>
                            <button className="btn btn-secondary" disabled={busy} onClick={onClose}>
                                {t('common.cancel')}
                            </button>
                            {mode === 'native' ? (
                                <>
                                    <span
                                        title={disabledByNoModel}
                                        style={{ display: 'inline-flex', cursor: nativeHasNoModel ? 'not-allowed' : undefined }}
                                    >
                                        <button
                                            className="btn btn-secondary"
                                            disabled={busy || nativeHasNoModel}
                                            style={{ pointerEvents: nativeHasNoModel ? 'none' : undefined }}
                                            onClick={() => createAgent.mutate({ chatNow: false })}
                                        >
                                            {t('customAgentModal.createOnly')}
                                        </button>
                                    </span>
                                    <span
                                        title={disabledByNoModel}
                                        style={{ display: 'inline-flex', cursor: nativeHasNoModel ? 'not-allowed' : undefined }}
                                    >
                                        <button
                                            className="btn btn-primary"
                                            disabled={busy || nativeHasNoModel}
                                            style={{ pointerEvents: nativeHasNoModel ? 'none' : undefined }}
                                            onClick={() => createAgent.mutate({ chatNow: true })}
                                        >
                                            {busy ? t('customAgentModal.creating') : t('customAgentModal.chatNow')}
                                        </button>
                                    </span>
                                </>
                            ) : (
                                <button
                                    className="btn btn-primary"
                                    disabled={busy}
                                    onClick={() => createAgent.mutate({ chatNow: false })}
                                >
                                    {busy ? t('customAgentModal.creating') : t('customAgentModal.createConnection')}
                                </button>
                            )}
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}

function NoModelsNotice({
    canManageModels,
    onConfigure,
    t,
    allowExternalHint,
}: {
    canManageModels: boolean;
    onConfigure: () => void;
    t: (key: string) => string;
    allowExternalHint?: boolean;
}) {
    return (
        <div
            role="status"
            style={{
                display: 'flex',
                gap: '10px',
                alignItems: 'flex-start',
                padding: '10px 12px',
                borderRadius: '9px',
                border: '1px solid rgba(217,119,6,0.28)',
                background: 'rgba(245,158,11,0.08)',
            }}
        >
            <IconAlertTriangle size={17} stroke={1.8} style={{ marginTop: '1px', color: '#b45309', flexShrink: 0 }} />
            <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: '13px', fontWeight: 650, color: 'var(--text-primary)' }}>
                    {t('customAgentModal.noModelsTitle')}
                </div>
                <div style={{ marginTop: '3px', fontSize: '12px', lineHeight: 1.5, color: 'var(--text-secondary)' }}>
                    {canManageModels
                        ? t('customAgentModal.noModelsAdminHint')
                        : t('customAgentModal.noModelsMemberHint')}
                    {!canManageModels && allowExternalHint ? (
                        <span>
                            {' '}
                            {t('customAgentModal.noModelsExternalHint')}
                        </span>
                    ) : null}
                </div>
                {canManageModels ? (
                    <button
                        type="button"
                        className="btn btn-secondary"
                        onClick={onConfigure}
                        style={{ marginTop: '9px', height: '30px', padding: '0 10px', fontSize: '12px', display: 'inline-flex', alignItems: 'center', gap: '6px' }}
                    >
                        <IconSettings size={14} stroke={1.7} />
                        {t('customAgentModal.configureModels')}
                    </button>
                ) : null}
            </div>
        </div>
    );
}

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
    return (
        <label style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>
            <span style={{ fontSize: '13px', fontWeight: 600 }}>
                {label}{required && <span style={{ color: 'var(--error)', marginLeft: '3px' }}>*</span>}
            </span>
            {children}
        </label>
    );
}

function ModeButton({ active, icon, label, onClick }: { active: boolean; icon: React.ReactNode; label: string; onClick: () => void }) {
    return (
        <button
            type="button"
            onClick={onClick}
            style={{
                height: '34px',
                border: active ? '1px solid var(--border-default)' : '1px solid transparent',
                borderRadius: '7px',
                background: active ? 'var(--bg-primary)' : 'transparent',
                color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                boxShadow: active ? '0 1px 4px rgba(0,0,0,0.06)' : 'none',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '7px',
                fontSize: '13px',
                fontWeight: 600,
                cursor: 'pointer',
            }}
        >
            {icon}
            {label}
        </button>
    );
}

function RadioRow({ selected, onClick, title, hint }: { selected: boolean; onClick: () => void; title: string; hint: string }) {
    return (
        <button
            type="button"
            onClick={onClick}
            style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: '10px',
                padding: '10px 12px',
                textAlign: 'left',
                border: `1px solid ${selected ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                borderRadius: '8px',
                background: selected ? 'var(--accent-subtle, rgba(99,102,241,0.08))' : 'transparent',
                cursor: 'pointer',
                width: '100%',
            }}
        >
            <span style={{
                marginTop: '2px',
                width: '14px',
                height: '14px',
                borderRadius: '50%',
                border: `2px solid ${selected ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
            }}>
                {selected && <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'var(--accent-primary)' }} />}
            </span>
            <span style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                <span style={{ fontSize: '13px', color: 'var(--text-primary)' }}>{title}</span>
                <span style={{ fontSize: '11.5px', color: 'var(--text-tertiary)' }}>{hint}</span>
            </span>
        </button>
    );
}

function ExternalSuccess({
    agent,
    setupInstruction,
    t,
    onClose,
    onEnter,
}: {
    agent: CreatedAgent;
    setupInstruction: string;
    t: (key: string) => string;
    onClose: () => void;
    onEnter: () => void;
}) {
    return (
        <>
            <div style={{ padding: '24px 26px 10px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px' }}>
                <div style={{ display: 'flex', gap: '12px', alignItems: 'flex-start' }}>
                    <span style={{
                        width: '30px',
                        height: '30px',
                        borderRadius: '50%',
                        background: 'var(--success)',
                        color: '#fff',
                        display: 'inline-flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        flexShrink: 0,
                    }}>
                        <IconCheck size={17} stroke={2.4} />
                    </span>
                    <div>
                        <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 650 }}>
                            {t('customAgentModal.externalCreated')}
                        </h3>
                        <p style={{ margin: '5px 0 0', fontSize: '12.5px', color: 'var(--text-secondary)' }}>
                            {agent.name}
                        </p>
                    </div>
                </div>
                <button onClick={onClose} className="btn btn-ghost" style={{ padding: '4px', display: 'flex' }}>
                    <IconX size={16} stroke={1.5} />
                </button>
            </div>

            <div style={{ padding: '8px 26px 20px', overflowY: 'auto' }}>
                <p style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                    {t('customAgentModal.externalCreatedDesc')}
                </p>

                <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '10px 12px',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: '9px',
                    background: 'var(--bg-secondary)',
                    marginBottom: '12px',
                }}>
                    <IconCpu size={15} stroke={1.7} style={{ color: 'var(--text-secondary)', flexShrink: 0 }} />
                    <span style={{ flex: 1, minWidth: 0, fontSize: '12.5px', color: 'var(--text-secondary)' }}>
                        {t('customAgentModal.gatewayKeyEmbedded')}
                    </span>
                    {agent.api_key && (
                        <LinearCopyButton
                            className="btn btn-secondary"
                            textToCopy={agent.api_key}
                            label={t('customAgentModal.copyKey')}
                            copiedLabel={t('common.copied')}
                            style={{ fontSize: '11px', padding: '4px 10px', minWidth: '76px' }}
                        />
                    )}
                </div>

                <div style={{ position: 'relative' }}>
                    <pre style={{
                        margin: 0,
                        padding: '12px',
                        background: 'var(--bg-secondary)',
                        borderRadius: '8px',
                        fontSize: '11px',
                        lineHeight: 1.6,
                        overflow: 'auto',
                        maxHeight: '260px',
                        border: '1px solid var(--border-subtle)',
                        whiteSpace: 'pre-wrap',
                    }}>{setupInstruction || t('customAgentModal.noKeyReturned')}</pre>
                    {setupInstruction && (
                        <LinearCopyButton
                            className="btn btn-ghost"
                            style={{ position: 'absolute', top: '5px', right: '5px', fontSize: '11px', minWidth: '64px' }}
                            textToCopy={setupInstruction}
                            label={t('common.copy')}
                            copiedLabel={t('common.copied')}
                        />
                    )}
                </div>
            </div>

            <div style={{ padding: '16px 26px 20px', display: 'flex', justifyContent: 'flex-end', gap: '8px', borderTop: '1px solid var(--border-subtle)' }}>
                <button className="btn btn-secondary" onClick={onClose}>
                    {t('common.close')}
                </button>
                <button className="btn btn-primary" onClick={onEnter}>
                    {t('customAgentModal.enterAgent')}
                </button>
            </div>
        </>
    );
}
