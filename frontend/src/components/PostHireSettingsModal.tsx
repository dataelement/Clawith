import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconAlertTriangle, IconSettings, IconX } from '@tabler/icons-react';
import { agentApi, authApi, enterpriseApi, tenantApi } from '../services/api';
import { translateTemplate } from '../i18n/templateTranslations';
import { useDialog } from './Dialog/DialogProvider';

interface Template {
    id: string;
    name: string;
    description?: string;
    icon?: string;
    category?: string;
}

interface Model {
    id: string;
    provider: string;
    model: string;
    label?: string;
    enabled?: boolean;
}

interface Props {
    template: Template | null;
    open: boolean;
    // User cancelled the settings step — close this modal, but keep the caller
    // (e.g. the Talent Market grid) open so they can pick again.
    onClose: () => void;
    // Creation succeeded — caller should close too. Navigation is handled here.
    onDone?: () => void;
}

type Visibility = 'company' | 'only_me' | 'custom';

export default function PostHireSettingsModal({ template, open, onClose, onDone }: Props) {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const dialog = useDialog();
    const isChinese = i18n.language.startsWith('zh');

    const [visibility, setVisibility] = useState<Visibility>('company');
    const [modelId, setModelId] = useState<string>('');

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
        () => (models as Model[]).filter(m => m.enabled !== false),
        [models],
    );

    const canManageModels = currentUser?.role === 'platform_admin'
        || currentUser?.role === 'org_admin'
        || !!currentUser?.is_platform_admin;
    const hasNoModel = enabledModels.length === 0;
    const disabledByNoModel = hasNoModel
        ? t(
            'postHire.noModelButtonHint',
            isChinese
                ? '需要先在公司设置中启用至少一个模型，才能创建模板成员。'
                : 'Enable at least one model in company settings before creating a teammate from a template.',
        )
        : undefined;
    const openModelSettings = () => {
        (onDone || onClose)();
        navigate('/enterprise#llm');
    };

    // Default the model picker to the tenant default (or first enabled)
    // once both are available.
    useEffect(() => {
        if (!open) return;
        if (modelId) return;
        const preferred = myTenant?.default_model_id && enabledModels.find(m => m.id === myTenant.default_model_id)
            ? myTenant.default_model_id
            : (enabledModels[0]?.id || '');
        if (preferred) setModelId(preferred);
    }, [open, myTenant?.default_model_id, enabledModels, modelId]);

    // Reset local form whenever the modal closes so the next open is clean.
    useEffect(() => {
        if (!open) {
            setVisibility('company');
            setModelId('');
        }
    }, [open]);

    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open, onClose]);

    const hire = useMutation({
        mutationFn: (navigateAfter: boolean) => {
            if (!template) return Promise.reject(new Error('No template'));
            if (enabledModels.length === 0) {
                return Promise.reject(new Error(
                    t(
                        'postHire.noModelError',
                        isChinese
                            ? '公司还没有启用可用模型，请先配置模型后再创建模板成员。'
                            : 'No company model is enabled yet. Configure a model before creating a teammate from a template.',
                    ),
                ));
            }
            if (!modelId) {
                return Promise.reject(new Error(t('postHire.modelRequired', isChinese ? '请选择模型' : 'Choose a model')));
            }
            // Localize name + role_description when the UI is in Chinese so
            // the agent persists with the same labels the user saw on the
            // Talent Market card. Without this, the DB stores the English
            // template name and the agent shows "Rapid Prototyper" forever
            // even though the card said "快速原型工程师".
            const localized = translateTemplate(
                { name: template.name, description: template.description || '', capability_bullets: [] },
                isChinese,
            );
            const payload: any = {
                name: localized.name,
                role_description: localized.description,
                template_id: template.id,
                primary_model_id: modelId || undefined,
                permission_access_level: 'manage',
            };
            payload.permission_scope_type = visibility === 'company'
                ? 'company'
                : visibility === 'custom'
                    ? 'custom'
                    : 'user';
            payload.permission_scope_ids = [];
            return agentApi.create(payload).then((agent: any) => ({ agent, navigateAfter }));
        },
        onSuccess: ({ agent, navigateAfter }) => {
            queryClient.invalidateQueries({ queryKey: ['agents'] });
            (onDone || onClose)();
            // "立即对话" → open directly on the chat tab (not the default status
            // tab). AgentDetail picks up the hash on mount.
            if (navigateAfter) navigate(`/agents/${agent.id}#chat`);
        },
        onError: async (err: any) => {
            await dialog.alert(isChinese ? '创建数字员工失败' : 'Failed to create agent', {
                type: 'error',
                details: String(err?.message || err),
            });
        },
    });

    if (!open || !template) return null;

    const labelFor = (m: Model) => m.label || `${m.provider} · ${m.model}`;
    const busy = hire.isPending;

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
                width: '480px', maxWidth: '92vw',
                border: '1px solid var(--border-subtle)',
                boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
                display: 'flex', flexDirection: 'column', overflow: 'hidden',
            }}>
                <div style={{ padding: '22px 26px 8px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                    <div>
                        <h3 style={{ margin: 0, fontSize: '17px', fontWeight: 600 }}>
                            {t('postHire.title', isChinese ? '配置新成员' : 'Configure new teammate')}
                        </h3>
                        <p style={{ margin: '4px 0 0', fontSize: '12.5px', color: 'var(--text-secondary)' }}>
                            {template.name}
                        </p>
                    </div>
                    <button onClick={onClose} className="btn btn-ghost" disabled={busy} style={{ padding: '4px' }}>
                        <IconX size={16} stroke={1.5} />
                    </button>
                </div>

                <div style={{ padding: '8px 26px 8px', display: 'flex', flexDirection: 'column', gap: '18px' }}>
                    {/* Visibility */}
                    <section>
                        <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px' }}>
                            {t('postHire.visibility', isChinese ? '可见权限' : 'Visibility')}
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                            <RadioRow
                                selected={visibility === 'company'}
                                onClick={() => !busy && setVisibility('company')}
                                title={t('postHire.visibilityCompanyTitle', isChinese ? '公司所有人' : 'Everyone at the company')}
                                hint={t('postHire.visibilityCompanyHint', isChinese ? '全公司都能使用这个数字员工' : 'Everyone in the company can use this agent')}
                            />
                            <RadioRow
                                selected={visibility === 'only_me'}
                                onClick={() => !busy && setVisibility('only_me')}
                                title={t('postHire.visibilityOnlyMeTitle', isChinese ? '仅自己' : 'Only me')}
                                hint={t('postHire.visibilityOnlyMeHint', isChinese ? '只有你能使用，可以之后在设置里分享' : 'Only you can use it; you can share later in Settings')}
                            />
                            <RadioRow
                                selected={visibility === 'custom'}
                                onClick={() => !busy && setVisibility('custom')}
                                title={t('postHire.visibilityCustomTitle', isChinese ? '指定成员' : 'Custom')}
                                hint={t('postHire.visibilityCustomHint', isChinese ? '先仅创建者可管理，之后在设置里指定可使用的平台用户' : 'Start with creator access, then choose platform users in Settings')}
                            />
                        </div>
                    </section>

                    {/* Model */}
                    <section>
                        <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px' }}>
                            {t('postHire.model', isChinese ? '首选模型' : 'Preferred model')}
                        </div>
                        {enabledModels.length === 0 ? (
                            <NoModelsNotice
                                isChinese={isChinese}
                                canManageModels={canManageModels}
                                onConfigure={openModelSettings}
                                t={t}
                            />
                        ) : (
                            <select
                                className="form-input"
                                value={modelId}
                                onChange={e => setModelId(e.target.value)}
                                disabled={busy}
                                style={{ width: '100%' }}
                            >
                                {enabledModels.map(m => (
                                    <option key={m.id} value={m.id}>
                                        {labelFor(m)}{myTenant?.default_model_id === m.id ? ` · ${t('postHire.defaultSuffix', isChinese ? '默认' : 'default')}` : ''}
                                    </option>
                                ))}
                            </select>
                        )}
                    </section>
                </div>

                <div style={{ padding: '16px 26px 20px', display: 'flex', justifyContent: 'flex-end', gap: '8px', borderTop: '1px solid var(--border-subtle)', marginTop: '12px' }}>
                    <button
                        className="btn btn-secondary"
                        disabled={busy || hasNoModel}
                        title={disabledByNoModel}
                        onClick={() => hire.mutate(false)}
                    >
                        {busy && !hire.variables ? '...' : t('postHire.createOnly', isChinese ? '仅创建' : 'Just create')}
                    </button>
                    <button
                        className="btn btn-primary"
                        disabled={busy || hasNoModel}
                        title={disabledByNoModel}
                        onClick={() => hire.mutate(true)}
                    >
                        {busy ? (isChinese ? '创建中...' : 'Creating...') : t('postHire.chatNow', isChinese ? '立即对话' : 'Chat now')}
                    </button>
                </div>
            </div>
        </div>
    );
}

function NoModelsNotice({
    isChinese,
    canManageModels,
    onConfigure,
    t,
}: {
    isChinese: boolean;
    canManageModels: boolean;
    onConfigure: () => void;
    t: (key: string, fallback: string) => string;
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
                    {t('postHire.noModelsTitle', isChinese ? '还没有可用模型' : 'No enabled model yet')}
                </div>
                <div style={{ marginTop: '3px', fontSize: '12px', lineHeight: 1.5, color: 'var(--text-secondary)' }}>
                    {canManageModels
                        ? t(
                            'postHire.noModelsAdminHint',
                            isChinese
                                ? '启用至少一个公司模型后，才能基于模板创建数字员工。'
                                : 'Enable at least one company model before creating a teammate from a template.',
                        )
                        : t(
                            'postHire.noModelsMemberHint',
                            isChinese
                                ? '请联系公司管理员先启用模型。'
                                : 'Ask a company admin to enable a model first.',
                        )}
                </div>
                {canManageModels ? (
                    <button
                        type="button"
                        className="btn btn-secondary"
                        onClick={onConfigure}
                        style={{ marginTop: '9px', height: '30px', padding: '0 10px', fontSize: '12px', display: 'inline-flex', alignItems: 'center', gap: '6px' }}
                    >
                        <IconSettings size={14} stroke={1.7} />
                        {t('postHire.configureModels', isChinese ? '配置模型' : 'Configure models')}
                    </button>
                ) : null}
            </div>
        </div>
    );
}

function RadioRow({ selected, onClick, title, hint }: { selected: boolean; onClick: () => void; title: string; hint: string }) {
    return (
        <button
            type="button"
            onClick={onClick}
            style={{
                display: 'flex', alignItems: 'flex-start', gap: '10px',
                padding: '10px 12px', textAlign: 'left',
                border: `1px solid ${selected ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                borderRadius: '8px', background: selected ? 'var(--accent-subtle, rgba(99,102,241,0.08))' : 'transparent',
                cursor: 'pointer', width: '100%',
            }}
        >
            <span style={{
                marginTop: '2px', width: '14px', height: '14px', borderRadius: '50%',
                border: `2px solid ${selected ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
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
