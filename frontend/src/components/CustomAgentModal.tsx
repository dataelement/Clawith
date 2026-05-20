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
    const isChinese = i18n.language.startsWith('zh');

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
        ? t(
            'customAgentModal.noModelButtonHint',
            isChinese
                ? '需要先在公司设置中启用至少一个模型，才能创建平台托管成员。'
                : 'Enable at least one model in company settings before creating a platform-hosted teammate.',
        )
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
                throw new Error(t('customAgentModal.nameRequired', isChinese ? '请填写名称' : 'Name is required'));
            }
            if (mode === 'native' && enabledModels.length === 0) {
                throw new Error(
                    t(
                        'customAgentModal.noModelError',
                        isChinese
                            ? '公司还没有启用可用模型，请先配置模型或切换为外部 Agent。'
                            : 'No company model is enabled yet. Configure a model or switch to External agent.',
                    ),
                );
            }
            if (mode === 'native' && !modelId) {
                throw new Error(t('customAgentModal.modelRequired', isChinese ? '请选择模型' : 'Choose a model'));
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
            await dialog.alert(isChinese ? '创建失败' : 'Creation failed', {
                type: 'error',
                details: String(err?.message || err),
            });
        },
    });

    if (!open) return null;

    const busy = createAgent.isPending;
    const setupInstruction = createdExternal?.api_key
        ? buildOpenClawInstruction(createdExternal.api_key)
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
                        isChinese={isChinese}
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
                                        ? t('customAgentModal.nativeTitle', isChinese ? '创建自定义成员' : 'Create custom teammate')
                                        : t('customAgentModal.externalTitle', isChinese ? '连接外部 Agent' : 'Link external agent')}
                                </h3>
                                <p style={{ margin: '5px 0 0', fontSize: '12.5px', color: 'var(--text-secondary)' }}>
                                    {mode === 'native'
                                        ? t('customAgentModal.nativeSubtitle', isChinese ? '先完成必要信息，其余能力之后在设置中调整。' : 'Start with the essentials; tune the rest in settings later.')
                                        : t('customAgentModal.externalSubtitle', isChinese ? '为 OpenClaw 或其他外部运行的 Agent 创建连接入口。' : 'Create a Clawith connection for an externally running agent.')}
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
                                    label={t('customAgentModal.nativeMode', isChinese ? '平台托管' : 'Platform hosted')}
                                    onClick={() => !busy && setMode('native')}
                                />
                                <ModeButton
                                    active={mode === 'openclaw'}
                                    icon={<IconPlugConnected size={15} stroke={1.7} />}
                                    label={t('customAgentModal.externalMode', isChinese ? '外部 Agent' : 'External agent')}
                                    onClick={() => !busy && setMode('openclaw')}
                                />
                            </div>

                            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                                <Field label={t('customAgentModal.name', isChinese ? '名称' : 'Name')} required>
                                    <input
                                        className="form-input"
                                        value={name}
                                        onChange={(e) => setName(e.target.value)}
                                        maxLength={100}
                                        placeholder={mode === 'native'
                                            ? t('customAgentModal.namePlaceholderNative', isChinese ? '例如：客户研究员' : 'e.g. Customer researcher')
                                            : t('customAgentModal.namePlaceholderExternal', isChinese ? '例如：OpenClaw 研究助手' : 'e.g. OpenClaw research assistant')}
                                        disabled={busy}
                                        autoFocus
                                        style={{ width: '100%' }}
                                    />
                                </Field>

                                <Field label={t('customAgentModal.role', isChinese ? '角色描述' : 'Role')}>
                                    <textarea
                                        className="form-input"
                                        value={roleDescription}
                                        onChange={(e) => setRoleDescription(e.target.value)}
                                        maxLength={500}
                                        placeholder={t('customAgentModal.rolePlaceholder', isChinese ? '一句话说明它负责什么。' : 'Describe what this teammate is responsible for.')}
                                        disabled={busy}
                                        rows={3}
                                        style={{ width: '100%', resize: 'vertical', minHeight: '76px' }}
                                    />
                                </Field>

                                <section>
                                    <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px' }}>
                                        {t('customAgentModal.visibility', isChinese ? '可见权限' : 'Visibility')}
                                    </div>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                        <RadioRow
                                            selected={visibility === 'company'}
                                            onClick={() => !busy && setVisibility('company')}
                                            title={t('customAgentModal.visibilityCompany', isChinese ? '公司所有人' : 'Everyone at the company')}
                                            hint={t('customAgentModal.visibilityCompanyHint', isChinese ? '全公司都能使用这个数字员工' : 'Everyone in the company can use this agent')}
                                        />
                                        <RadioRow
                                            selected={visibility === 'only_me'}
                                            onClick={() => !busy && setVisibility('only_me')}
                                            title={t('customAgentModal.visibilityOnlyMe', isChinese ? '仅自己' : 'Only me')}
                                            hint={t('customAgentModal.visibilityOnlyMeHint', isChinese ? '只有你能使用，可以之后在设置里分享' : 'Only you can use it; you can share it later')}
                                        />
                                        <RadioRow
                                            selected={visibility === 'custom'}
                                            onClick={() => !busy && setVisibility('custom')}
                                            title={t('customAgentModal.visibilityCustom', isChinese ? '指定成员' : 'Custom')}
                                            hint={t('customAgentModal.visibilityCustomHint', isChinese ? '先仅创建者可管理，之后在设置里指定成员' : 'Start private, then choose members in settings')}
                                        />
                                    </div>
                                </section>

                                {mode === 'native' && (
                                    <Field label={t('customAgentModal.model', isChinese ? '首选模型' : 'Preferred model')} required>
                                        {enabledModels.length === 0 ? (
                                            <NoModelsNotice
                                                isChinese={isChinese}
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
                                                        {m.label || t('customAgentModal.modelFallback', isChinese ? '模型' : 'Model')}
                                                        {myTenant?.default_model_id === m.id ? ` · ${t('customAgentModal.defaultModel', isChinese ? '默认' : 'default')}` : ''}
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
                                {t('common.cancel', isChinese ? '取消' : 'Cancel')}
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
                                            {t('customAgentModal.createOnly', isChinese ? '仅创建' : 'Just create')}
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
                                            {busy ? t('customAgentModal.creating', isChinese ? '创建中...' : 'Creating...') : t('customAgentModal.chatNow', isChinese ? '立即对话' : 'Chat now')}
                                        </button>
                                    </span>
                                </>
                            ) : (
                                <button
                                    className="btn btn-primary"
                                    disabled={busy}
                                    onClick={() => createAgent.mutate({ chatNow: false })}
                                >
                                    {busy ? t('customAgentModal.creating', isChinese ? '创建中...' : 'Creating...') : t('customAgentModal.createConnection', isChinese ? '创建连接' : 'Create connection')}
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
    isChinese,
    canManageModels,
    onConfigure,
    t,
    allowExternalHint,
}: {
    isChinese: boolean;
    canManageModels: boolean;
    onConfigure: () => void;
    t: (key: string, fallback: string) => string;
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
                    {t('customAgentModal.noModelsTitle', isChinese ? '还没有可用模型' : 'No enabled model yet')}
                </div>
                <div style={{ marginTop: '3px', fontSize: '12px', lineHeight: 1.5, color: 'var(--text-secondary)' }}>
                    {canManageModels
                        ? t(
                            'customAgentModal.noModelsAdminHint',
                            isChinese
                                ? '启用至少一个公司模型后，才能创建平台托管的数字员工。'
                                : 'Enable at least one company model before creating a platform-hosted teammate.',
                        )
                        : t(
                            'customAgentModal.noModelsMemberHint',
                            isChinese
                                ? '请联系公司管理员先启用模型。'
                                : 'Ask a company admin to enable a model first.',
                        )}
                    {!canManageModels && allowExternalHint ? (
                        <span>
                            {' '}
                            {t(
                                'customAgentModal.noModelsExternalHint',
                                isChinese
                                    ? '如果你只是接入外部运行的 Agent，可以切换到“外部 Agent”。'
                                    : 'If you are linking an externally running agent, switch to External agent.',
                            )}
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
                        {t('customAgentModal.configureModels', isChinese ? '配置模型' : 'Configure models')}
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
    isChinese,
    t,
    onClose,
    onEnter,
}: {
    agent: CreatedAgent;
    setupInstruction: string;
    isChinese: boolean;
    t: (key: string, fallback: string) => string;
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
                            {t('customAgentModal.externalCreated', isChinese ? '连接已创建' : 'Connection created')}
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
                    {t(
                        'customAgentModal.externalCreatedDesc',
                        isChinese
                            ? '把下面的连接指令交给外部 Agent，它就能通过网关收发 Clawith 消息。'
                            : 'Send the setup instruction below to the external agent so it can sync with Clawith through the gateway.',
                    )}
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
                        {t('customAgentModal.gatewayKeyEmbedded', isChinese ? 'API Key 已包含在连接指令中' : 'The API key is included in the setup instruction')}
                    </span>
                    {agent.api_key && (
                        <LinearCopyButton
                            className="btn btn-secondary"
                            textToCopy={agent.api_key}
                            label={t('customAgentModal.copyKey', isChinese ? '复制 Key' : 'Copy key')}
                            copiedLabel={t('common.copied', isChinese ? '已复制' : 'Copied')}
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
                    }}>{setupInstruction || t('customAgentModal.noKeyReturned', isChinese ? '未返回 API Key，请进入 Agent 页面重新生成。' : 'No API key was returned. Open the agent page and generate one there.')}</pre>
                    {setupInstruction && (
                        <LinearCopyButton
                            className="btn btn-ghost"
                            style={{ position: 'absolute', top: '5px', right: '5px', fontSize: '11px', minWidth: '64px' }}
                            textToCopy={setupInstruction}
                            label={t('common.copy', isChinese ? '复制' : 'Copy')}
                            copiedLabel={t('common.copied', isChinese ? '已复制' : 'Copied')}
                        />
                    )}
                </div>
            </div>

            <div style={{ padding: '16px 26px 20px', display: 'flex', justifyContent: 'flex-end', gap: '8px', borderTop: '1px solid var(--border-subtle)' }}>
                <button className="btn btn-secondary" onClick={onClose}>
                    {t('common.close', isChinese ? '关闭' : 'Close')}
                </button>
                <button className="btn btn-primary" onClick={onEnter}>
                    {t('customAgentModal.enterAgent', isChinese ? '进入 Agent' : 'Open agent')}
                </button>
            </div>
        </>
    );
}

function buildOpenClawInstruction(apiKey: string) {
    const origin = window.location.origin;
    return `Please create a skill file at skills/clawith_sync.md with the following content, then add "Check Clawith inbox using the clawith_sync skill and process any pending messages" to your HEARTBEAT.md:

---
name: clawith_sync
description: Sync with Clawith platform — check inbox, submit results, and send messages.
---

# Clawith Sync

## When to use
Check for new messages from the Clawith platform during every heartbeat cycle.
You can also proactively send messages to people and agents in your relationships.

## Instructions

### 1. Check inbox
Make an HTTP GET request:
- URL: ${origin}/api/gateway/poll
- Header: X-Api-Key: ${apiKey}

The response contains a \`messages\` array. Each message includes:
- \`id\` — unique message ID (use this for reporting)
- \`content\` — the message text
- \`sender_user_name\` — name of the Clawith user who sent it
- \`sender_user_id\` — unique ID of the sender
- \`conversation_id\` — the conversation this message belongs to
- \`history\` — array of previous messages in this conversation for context

The response also contains a \`relationships\` array describing your colleagues:
- \`name\` — the person or agent name
- \`type\` — "human" or "agent"
- \`role\` — relationship type (e.g. collaborator, supervisor)
- \`channels\` — available communication channels (e.g. ["feishu"], ["agent"])

IMPORTANT: Use the \`history\` array to understand conversation context before replying.
Different \`sender_user_name\` values mean different people — address them accordingly.

### 2. Report results
For each completed message, make an HTTP POST request:
- URL: ${origin}/api/gateway/report
- Header: X-Api-Key: ${apiKey}
- Header: Content-Type: application/json
- Body: {"message_id": "<id from the message>", "result": "<your response>"}

### 3. Send a message to someone
To proactively contact a person or agent, make an HTTP POST request:
- URL: ${origin}/api/gateway/send-message
- Header: X-Api-Key: ${apiKey}
- Header: Content-Type: application/json
- Body: {"target": "<name of person or agent>", "content": "<your message>"}

The system auto-detects the best channel. For agents, the reply appears in your next poll.
For humans, the message is delivered via their available channel (e.g. Feishu).`;
}
