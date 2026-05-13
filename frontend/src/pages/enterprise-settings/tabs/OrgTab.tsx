import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useDialog } from '../../../components/Dialog/DialogProvider';
import { useToast } from '../../../components/Toast/ToastProvider';
import LinearCopyButton from '../../../components/LinearCopyButton';
import { IconSettings } from '@tabler/icons-react';
import { fetchJson } from '../utils/fetchJson';

const FEISHU_SYNC_PERM_JSON = `{
  "scopes": {
    "tenant": [
      "contact:contact.base:readonly",
      "contact:department.base:readonly",
      "contact:user.base:readonly",
      "contact:user.employee_id:readonly"
    ],
    "user": []
  }
}`;


// ─── Department Tree ───────────────────────────────
function DeptTree({ departments, parentId, selectedDept, onSelect, level }: {
    departments: any[]; parentId: string | null; selectedDept: string | null;
    onSelect: (id: string | null) => void; level: number;
}) {
    const children = departments.filter((d: any) =>
        parentId === null ? !d.parent_id : d.parent_id === parentId
    );
    if (children.length === 0) return null;
    return (
        <>
            {children.map((d: any) => (
                <div key={d.id}>
                    <div
                        style={{
                            padding: '5px 8px',
                            paddingLeft: `${8 + level * 16}px`,
                            borderRadius: '4px',
                            cursor: 'pointer',
                            fontSize: '13px',
                            marginBottom: '1px',
                            background: selectedDept === d.id ? 'rgba(224,238,238,0.12)' : 'transparent',
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center'
                        }}
                        onClick={() => onSelect(d.id)}
                    >
                        <div>
                            <span style={{ color: 'var(--text-tertiary)', marginRight: '4px', fontSize: '11px' }}>
                                {departments.some((c: any) => c.parent_id === d.id) ? '▾' : '·'}
                            </span>
                            {d.name}
                        </div>
                        {d.member_count !== undefined && (
                            <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>
                                {d.member_count}
                            </span>
                        )}
                    </div>
                    <DeptTree departments={departments} parentId={d.id} selectedDept={selectedDept} onSelect={onSelect} level={level + 1} />
                </div>
            ))}
        </>
    );
}

// ─── SSO Channel Section ────────────────────────────────
function SsoChannelSection({ idpType, existingProvider, tenant, t }: {
    idpType: string; existingProvider: any; tenant: any; t: any;
}) {
    const qc = useQueryClient();
    const dialog = useDialog();
    const toast = useToast();
    const [liveDomain, setLiveDomain] = useState<string>(existingProvider?.sso_domain || tenant?.sso_domain || '');
    const [ssoError, setSsoError] = useState<string>('');
    const [toggling, setToggling] = useState(false);

    useEffect(() => {
        setLiveDomain(existingProvider?.sso_domain || tenant?.sso_domain || '');
    }, [existingProvider?.sso_domain, tenant?.sso_domain]);

    const ssoEnabled = existingProvider ? !!existingProvider.sso_login_enabled : false;
    const domain = liveDomain;
    const callbackUrl = domain ? (domain.startsWith('http') ? `${domain}/api/auth/${idpType}/callback` : `https://${domain}/api/auth/${idpType}/callback`) : '';

    const handleSsoToggle = async () => {
        if (!existingProvider) {
            toast.warning(t('enterprise.identity.saveFirst', 'Please save the configuration first to enable SSO.'));
            return;
        }
        const newVal = !ssoEnabled;
        setToggling(true);
        setSsoError('');
        try {
            const result = await fetchJson<any>(`/enterprise/identity-providers/${existingProvider.id}`, {
                method: 'PUT',
                body: JSON.stringify({ sso_login_enabled: newVal }),
            });
            if (result?.sso_domain) setLiveDomain(result.sso_domain);
            qc.invalidateQueries({ queryKey: ['identity-providers'] });
            if (tenant?.id) qc.invalidateQueries({ queryKey: ['tenant', tenant.id] });
        } catch (e: any) {
            const msg = e?.message || '';
            if (msg.includes('IP address') || msg.includes('multi-tenant')) {
                setSsoError(t('enterprise.identity.ssoIpConflict', 'IP 模式下只能有一个企业开启 SSO，当前已有其他企业占用。'));
            } else {
                setSsoError(msg || t('enterprise.identity.ssoToggleFailed', 'Failed to toggle SSO'));
            }
        } finally {
            setToggling(false);
        }
    };

    return (
        <div style={{ marginTop: '20px', paddingTop: '20px', borderTop: '1px dashed var(--border-subtle)' }}>
            {/* SSO Toggle */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: ssoError ? '8px' : '16px' }}>
                <div>
                    <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('enterprise.identity.ssoLoginToggle', 'SSO Login')}</div>
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                        {t('enterprise.identity.ssoLoginToggleHint', 'Allow users to log in via this identity provider.')}
                    </div>
                </div>
                <label style={{ position: 'relative', display: 'inline-block', width: '36px', height: '20px', flexShrink: 0, opacity: (existingProvider && !toggling) ? 1 : 0.5 }}>
                    <input
                        type="checkbox"
                        checked={ssoEnabled}
                        onChange={handleSsoToggle}
                        disabled={!existingProvider || toggling}
                        style={{ opacity: 0, width: 0, height: 0 }}
                    />
                    <span style={{
                        position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
                        borderRadius: '20px', cursor: (existingProvider && !toggling) ? 'pointer' : 'not-allowed',
                        background: ssoEnabled ? 'var(--accent-primary)' : 'var(--border-subtle)',
                        transition: '0.2s',
                    }}>
                        <span style={{
                            position: 'absolute', left: ssoEnabled ? '18px' : '2px', top: '2px',
                            width: '16px', height: '16px', borderRadius: '50%',
                            background: '#fff', transition: '0.2s',
                            boxShadow: '0 1px 2px rgba(0,0,0,0.1)'
                        }} />
                    </span>
                </label>
            </div>
            {ssoError && (
                <div style={{ fontSize: '12px', color: 'var(--error)', marginBottom: '12px', padding: '6px 10px', background: 'rgba(var(--error-rgb,220,38,38),0.08)', borderRadius: '6px' }}>
                    {ssoError}
                </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <div>
                    <label className="form-label" style={{ fontSize: '11px', marginBottom: '4px', color: 'var(--text-secondary)' }}>
                        {t('enterprise.identity.ssoSubdomain', 'SSO Login URL')}
                    </label>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <div style={{
                            flex: 1, maxWidth: '400px',
                            padding: '8px 12px',
                            background: 'var(--bg-elevated)',
                            border: '1px solid var(--border-subtle)',
                            borderRadius: '6px',
                            fontSize: '12px',
                            color: domain ? 'var(--text-primary)' : 'var(--text-tertiary)',
                            fontFamily: 'monospace',
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis'
                        }}>
                            {domain ? (domain.startsWith('http') ? domain : `https://${domain}`) : t('enterprise.identity.ssoUrlEmpty', '请先开启 SSO 以生成地址')}
                        </div>
                        <LinearCopyButton
                            className="btn btn-ghost btn-sm"
                            style={{ fontSize: '11px', width: 'auto', minWidth: '70px', height: '33px' }}
                            disabled={!domain}
                            textToCopy={domain ? (domain.startsWith('http') ? domain : `https://${domain}`) : ''}
                            label={t('common.copy', 'Copy')}
                            copiedLabel="Copied"
                        />
                    </div>
                    <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                        {t('enterprise.identity.ssoSubdomainHint', 'Share this URL with your team. SSO login buttons will appear when they visit this address.')}
                    </div>
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: '11px', marginBottom: '4px', color: 'var(--text-secondary)' }}>
                        {t('enterprise.identity.callbackUrl', 'Redirect URL (paste this in your app settings)')}
                    </label>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <div style={{
                            flex: 1, maxWidth: '400px',
                            padding: '8px 12px',
                            background: 'var(--bg-elevated)',
                            border: '1px solid var(--border-subtle)',
                            borderRadius: '6px',
                            fontSize: '12px',
                            color: callbackUrl ? 'var(--text-primary)' : 'var(--text-tertiary)',
                            fontFamily: 'monospace',
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis'
                        }}>
                            {callbackUrl || t('enterprise.identity.ssoUrlEmpty', '请先开启 SSO 以生成地址')}
                        </div>
                        <LinearCopyButton
                            className="btn btn-ghost btn-sm"
                            style={{ fontSize: '11px', width: 'auto', minWidth: '70px', height: '33px' }}
                            disabled={!callbackUrl}
                            textToCopy={callbackUrl}
                            label={t('common.copy', 'Copy')}
                            copiedLabel="Copied"
                        />
                    </div>
                    <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                        {t('enterprise.identity.callbackUrlHint', "Add this URL as the OAuth redirect URI in your identity provider's app configuration.")}
                    </div>
                </div>
            </div>
        </div>
    );
}


// ─── Org & Identity Tab ─────────────────────────────
export default function OrgTab({ tenant }: { tenant: any }) {
    const { t } = useTranslation();
    const dialog = useDialog();
    const qc = useQueryClient();




    const SsoStatus = () => {
        const [isExpanded, setIsExpanded] = useState(!!tenant?.sso_enabled);
        const [ssoEnabled, setSsoEnabled] = useState(!!tenant?.sso_enabled);
        const [ssoDomain, setSsoDomain] = useState(tenant?.sso_domain || '');
        const [saving, setSaving] = useState(false);
        const [error, setError] = useState('');

        useEffect(() => {
            setSsoEnabled(!!tenant?.sso_enabled);
            setSsoDomain(tenant?.sso_domain || '');
            setIsExpanded(!!tenant?.sso_enabled);
        }, [tenant]);

        const handleSave = async (forceEnabled?: boolean) => {
            if (!tenant?.id) return;
            const targetEnabled = forceEnabled !== undefined ? forceEnabled : ssoEnabled;
            setSaving(true);
            setError('');
            try {
                await fetchJson(`/tenants/${tenant.id}`, {
                    method: 'PUT',
                    body: JSON.stringify({
                        sso_enabled: targetEnabled,
                        sso_domain: targetEnabled ? (ssoDomain.trim() || null) : null,
                    }),
                });
                qc.invalidateQueries({ queryKey: ['tenant', tenant.id] });
            } catch (e: any) {
                setError(e.message || 'Failed to update SSO configuration');
            }
            setSaving(false);
        };

        const handleToggle = (e: React.ChangeEvent<HTMLInputElement>) => {
            const checked = e.target.checked;
            setSsoEnabled(checked);
            setIsExpanded(checked);
            if (!checked) {
                // auto-save when disabling
                handleSave(false);
            }
        };

        return (
            <div className="card" style={{ marginBottom: '24px', overflow: 'hidden' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px' }}>
                    <div>
                        <div style={{ fontWeight: 600, fontSize: '14px', marginBottom: '4px' }}>
                            {t('enterprise.identity.ssoTitle', 'Enterprise SSO')}
                        </div>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            {t('enterprise.identity.ssoDisabledHint', 'Seamless enterprise login via Single Sign-On.')}
                        </div>
                    </div>
                    <div>
                        <label style={{ position: 'relative', display: 'inline-block', width: '36px', height: '20px' }}>
                            <input
                                type="checkbox"
                                checked={ssoEnabled}
                                onChange={handleToggle}
                                style={{ opacity: 0, width: 0, height: 0 }}
                            />
                            <span style={{
                                position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
                                borderRadius: '20px', cursor: 'pointer',
                                background: ssoEnabled ? 'var(--accent-primary)' : 'var(--border-subtle)',
                                transition: '0.2s'
                            }}>
                                <span style={{
                                    position: 'absolute', left: ssoEnabled ? '18px' : '2px', top: '2px',
                                    width: '16px', height: '16px', borderRadius: '50%',
                                    background: '#fff', transition: '0.2s',
                                    boxShadow: '0 1px 2px rgba(0,0,0,0.1)'
                                }} />
                            </span>
                        </label>
                    </div>
                </div>

                {isExpanded && (
                    <div style={{ padding: '0 16px 16px', borderTop: '1px solid var(--border-subtle)', paddingTop: '16px' }}>
                        <div style={{ marginBottom: '16px' }}>
                            <label className="form-label" style={{ fontSize: '12px', marginBottom: '8px' }}>
                                {t('enterprise.identity.ssoDomain', 'Custom Access Domain')}
                            </label>
                            <input
                                className="form-input"
                                value={ssoDomain}
                                onChange={e => setSsoDomain(e.target.value)}
                                placeholder={t('enterprise.identity.ssoDomainPlaceholder', 'e.g. acme.clawith.com')}
                                style={{ fontSize: '13px', width: '100%', maxWidth: '400px' }}
                            />
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '6px' }}>
                                {t('enterprise.identity.ssoDomainDesc', 'The custom domain users will use to log in via SSO.')}
                            </div>
                        </div>

                        {error && <div style={{ color: 'var(--error)', fontSize: '12px', marginBottom: '12px' }}>{error}</div>}

                        <div style={{ display: 'flex', gap: '8px' }}>
                            <button className="btn btn-primary btn-sm" onClick={() => handleSave()} disabled={saving || !ssoDomain.trim()}>
                                {saving ? t('common.loading') : t('common.save', 'Save Configuration')}
                            </button>
                        </div>
                    </div>
                )}
            </div>
        );
    };

    const [syncing, setSyncing] = useState<string | null>(null);
    const [syncResult, setSyncResult] = useState<any>(null);
    const [memberSearch, setMemberSearch] = useState('');
    const [selectedDept, setSelectedDept] = useState<string | null>(null);
    const [expandedType, setExpandedType] = useState<string | null>(null);
    const [savingProvider, setSavingProvider] = useState(false);
    const [saveProviderOk, setSaveProviderOk] = useState(false);

    // Identity Providers state
    const [editingId, setEditingId] = useState<string | null>(null);
    const [useOAuth2Form, setUseOAuth2Form] = useState(false);
    const [form, setForm] = useState({
        provider_type: 'feishu',
        name: '',
        config: {} as any,
        app_id: '',
        app_secret: '',
        authorize_url: '',
        token_url: '',
        user_info_url: '',
        scope: 'openid profile email'
    });

    const currentTenantId = localStorage.getItem('current_tenant_id') || '';

    // Queries
    const { data: providers = [] } = useQuery({
        queryKey: ['identity-providers', currentTenantId],
        queryFn: () => fetchJson<any[]>(`/enterprise/identity-providers${currentTenantId ? `?tenant_id=${currentTenantId}` : ''}`),
    });

    const { data: departmentsData = { items: [], total_member: 0 } } = useQuery({
        queryKey: ['org-departments', currentTenantId, editingId],
        queryFn: () => {
            const params = new URLSearchParams();
            if (currentTenantId) params.set('tenant_id', currentTenantId);
            if (editingId) params.set('provider_id', editingId);
            return fetchJson<{ items: any[]; total_member: number }>(`/enterprise/org/departments?${params}`);
        },
        enabled: !!editingId,
    });

    const { data: members = [] } = useQuery({
        queryKey: ['org-members', selectedDept, memberSearch, currentTenantId, editingId],
        queryFn: () => {
            const params = new URLSearchParams();
            if (selectedDept) params.set('department_id', selectedDept);
            if (memberSearch) params.set('search', memberSearch);
            if (currentTenantId) params.set('tenant_id', currentTenantId);
            if (editingId) params.set('provider_id', editingId);
            return fetchJson<any[]>(`/enterprise/org/members?${params}`);
        },
        enabled: !!editingId,
    });

    // Mutations
    const addProvider = useMutation({
        mutationFn: (data: any) => {
            const payload = { ...data, tenant_id: currentTenantId, is_active: true };
            if (data.provider_type === 'oauth2' && useOAuth2Form) {
                return fetchJson('/enterprise/identity-providers/oauth2', {
                    method: 'POST',
                    body: JSON.stringify(payload)
                });
            }
            return fetchJson('/enterprise/identity-providers', { method: 'POST', body: JSON.stringify(payload) });
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['identity-providers'] });
            setUseOAuth2Form(false);
            setSavingProvider(false);
            setSaveProviderOk(true);
            setTimeout(() => setSaveProviderOk(false), 2500);
        },
        onError: () => setSavingProvider(false),
    });

    const updateProvider = useMutation({
        mutationFn: ({ id, data }: { id: string; data: any }) => {
            if (data.provider_type === 'oauth2' && useOAuth2Form) {
                return fetchJson(`/enterprise/identity-providers/${id}/oauth2`, {
                    method: 'PATCH',
                    body: JSON.stringify(data)
                });
            }
            return fetchJson(`/enterprise/identity-providers/${id}`, { method: 'PUT', body: JSON.stringify(data) });
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['identity-providers'] });
            setUseOAuth2Form(false);
            setSavingProvider(false);
            setSaveProviderOk(true);
            setTimeout(() => setSaveProviderOk(false), 2500);
        },
        onError: () => setSavingProvider(false),
    });

    const deleteProvider = useMutation({
        mutationFn: (id: string) => fetchJson(`/enterprise/identity-providers/${id}`, { method: 'DELETE' }),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['identity-providers'] }),
    });

    const triggerSync = async (providerId: string) => {
        setSyncing(providerId);
        setSyncResult(null);
        try {
            const result = await fetchJson<any>(`/enterprise/org/sync?provider_id=${providerId}`, { method: 'POST' });
            setSyncResult({ ...result, providerId });
            qc.invalidateQueries({ queryKey: ['org-departments'] });
            qc.invalidateQueries({ queryKey: ['org-members'] });
            qc.invalidateQueries({ queryKey: ['identity-providers'] });
        } catch (e: any) {
            setSyncResult({ error: e.message, providerId });
        }
        setSyncing(null);
    };

    const initOAuth2FromConfig = (config: any) => ({
        app_id: config?.app_id || config?.client_id || '',
        app_secret: config?.app_secret || config?.client_secret || '',
        authorize_url: config?.authorize_url || '',
        token_url: config?.token_url || '',
        user_info_url: config?.user_info_url || '',
        scope: config?.scope || 'openid profile email'
    });

    const save = () => {
        setSavingProvider(true);
        setSaveProviderOk(false);
        if (editingId) {
            updateProvider.mutate({ id: editingId, data: form });
        } else {
            addProvider.mutate(form);
        }
    };

    const handleGoogleAdminAuthorize = async (providerId: string) => {
        const res = await fetchJson<{ authorization_url: string }>(`/enterprise/identity-providers/${providerId}/google-workspace-sync/authorize-url`);
        const popup = window.open(res.authorization_url, 'google-workspace-sync', 'width=640,height=760');
        if (!popup) {
            window.location.href = res.authorization_url;
            return;
        }

        const onMessage = (event: MessageEvent) => {
            if (event.data?.type === 'google-workspace-sync-authorized') {
                window.removeEventListener('message', onMessage);
                qc.invalidateQueries({ queryKey: ['identity-providers'] });
            }
        };
        window.addEventListener('message', onMessage);
    };

    const IDP_TYPES = [
        { type: 'feishu', name: 'Feishu', desc: 'Feishu / Lark Integration', icon: <img src="/feishu.png" width="20" height="20" alt="Feishu" /> },
        { type: 'wecom', name: 'WeCom', desc: 'WeChat Work Integration', icon: <img src="/wecom.png" width="20" height="20" style={{ borderRadius: '4px' }} alt="WeCom" /> },
        { type: 'dingtalk', name: 'DingTalk', desc: 'DingTalk App Integration', icon: <img src="/dingtalk.png" width="20" height="20" style={{ borderRadius: '4px' }} alt="DingTalk" /> },
        { type: 'google_workspace', name: 'Google', desc: 'Google Admin Directory Sync', icon: <img src="/google.svg" width="20" height="20" alt="Google" /> },
        { type: 'oauth2', name: 'OAuth2', desc: 'Generic OIDC Provider', icon: <div style={{ width: 20, height: 20, background: 'var(--accent-primary)', borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: 10, fontWeight: 700 }}>O</div> }
    ];

    const handleExpand = (type: string, existingProvider?: any) => {
        if (expandedType === type) {
            setExpandedType(null);
            return;
        }
        setExpandedType(type);
        setEditingId(existingProvider ? existingProvider.id : null);
        setUseOAuth2Form(type === 'oauth2');

        if (existingProvider) {
            setForm({ ...existingProvider, ...(type === 'oauth2' ? initOAuth2FromConfig(existingProvider.config) : {}) });
        } else {
            const defaults: any = {
                feishu: { app_id: '', app_secret: '', corp_id: '' },
                dingtalk: { app_key: '', app_secret: '', corp_id: '' },
                wecom: { corp_id: '', secret: '', agent_id: '', app_secret: '', bot_id: '', bot_secret: '', verify_token: '', verify_aes_key: '' },
                google_workspace: {
                    client_id: '',
                    client_secret: '',
                },
            };
            const nameMap: Record<string, string> = { feishu: 'Feishu', wecom: 'WeCom', dingtalk: 'DingTalk', google_workspace: 'Google', oauth2: 'OAuth2' };
            setForm({
                provider_type: type,
                name: nameMap[type] || type,
                config: defaults[type] || {},
                app_id: '', app_secret: '', authorize_url: '', token_url: '', user_info_url: '',
                scope: 'openid profile email'
            });
        }
        setSelectedDept(null);
        setMemberSearch('');
    };

    const renderForm = (type: string, existingProvider?: any) => {
        const providerBaseUrl = (() => {
            const rawDomain = existingProvider?.sso_domain || tenant?.sso_domain || '';
            if (rawDomain) {
                return rawDomain.startsWith('http') ? rawDomain : `https://${rawDomain}`;
            }
            return window.location.origin;
        })();
        const providerCallbackUrl = `${providerBaseUrl}/api/auth/${type}/callback`;

        return (
            <div style={{ marginTop: '16px', paddingTop: '16px', borderTop: '1px solid var(--border-subtle)' }}>
                {/* Setup Guide moved to the top */}
                {['feishu', 'dingtalk', 'google_workspace'].includes(type) && (
                    <div style={{ background: 'var(--bg-primary)', padding: '16px', borderRadius: '8px', border: '1px solid var(--border-subtle)', marginBottom: '20px', fontSize: '12px' }}>
                        <div style={{ fontWeight: 600, fontSize: '13px', marginBottom: '8px', color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <IconSettings size={15} stroke={1.8} /> {t('enterprise.org.syncSetupGuide', 'Setup Guide & Required Permissions')}
                        </div>
                        <div style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                            {type === 'feishu' && (
                                <>
                                    {Array.from({ length: 7 }).map((_, i) => (
                                        <div key={i} style={{ marginBottom: '6px' }}>
                                            {i + 1}. {t(`enterprise.org.syncGuide.feishu.step${i + 1}`)}
                                        </div>
                                    ))}
                                    <div style={{ marginTop: '16px', marginBottom: '8px' }}>
                                        {t('enterprise.org.feishuGuideText', 'Permission JSON (bulk import)')}
                                    </div>
                                    <div style={{ position: 'relative', background: '#282c34', borderRadius: '6px', padding: '12px', paddingRight: '40px', color: '#abb2bf', fontFamily: 'monospace', fontSize: '11px', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
                                        <LinearCopyButton
                                            className="btn btn-ghost"
                                            style={{ position: 'absolute', top: '8px', right: '8px', fontSize: '10px', color: '#abb2bf', padding: '4px 8px', background: 'rgba(255,255,255,0.1)', cursor: 'pointer', border: 'none', borderRadius: '4px', height: 'fit-content', minWidth: '60px' }}
                                            textToCopy={FEISHU_SYNC_PERM_JSON}
                                            label="Copy"
                                            copiedLabel="Copied✓"
                                        />
                                        {FEISHU_SYNC_PERM_JSON}
                                    </div>
                                    <div style={{ marginTop: '8px', color: 'var(--text-secondary)' }}>
                                        {t('enterprise.org.feishuGuideWarning', 'Note: You must re-publish the app each time you add new permissions.')}
                                    </div>
                                </>
                            )}
                            {type === 'dingtalk' && (
                                <>
                                    {Array.from({ length: 6 }).map((_, i) => (
                                        <div key={i} style={{ marginBottom: '6px' }}>
                                            {i + 1}. {t(`enterprise.org.syncGuide.dingtalk.step${i + 1}`)}
                                        </div>
                                    ))}
                                </>
                            )}
                            {type === 'google_workspace' && (
                                <>
                                    <div style={{ marginBottom: '6px' }}>1. 在 Google Cloud 创建 OAuth Web App，并填入 Client ID 与 Client Secret。</div>
                                    <div style={{ marginBottom: '6px' }}>2. 将下方同一个 Redirect URL 配置到 Google Cloud 的 Authorized redirect URIs。</div>
                                    <div style={{ marginBottom: '6px' }}>3. SSO 登录直接使用这套配置。</div>
                                    <div style={{ marginBottom: '6px' }}>4. 组织同步时，使用有 Directory 读取权限的 Google Workspace 管理员账号点击授权。</div>
                                    <div style={{ marginBottom: '6px' }}>5. 后端会加密保存管理员 refresh token，后续定时同步会自动刷新 access token。</div>
                                </>
                            )}
                            {type === 'wecom' && (
                                <>
                                    {Array.from({ length: 5 }).map((_, i) => (
                                        <div key={i} style={{ marginBottom: '6px' }}>
                                            {i + 1}. {t(`enterprise.org.syncGuide.wecom.step${i + 1}`)}
                                        </div>
                                    ))}
                                </>
                            )}
                        </div>
                    </div>
                )}

                {/* Name field only for oauth2 */}
                {type === 'oauth2' && (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '16px' }}>
                        <div className="form-group">
                            <label className="form-label">{t('enterprise.identity.name')}</label>
                            <input className="form-input" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
                        </div>
                    </div>
                )}

                {type === 'oauth2' ? (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                        <div className="form-group">
                            <label className="form-label">Client ID</label>
                            <input className="form-input" value={form.app_id} onChange={e => setForm({ ...form, app_id: e.target.value })} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Client Secret</label>
                            <input className="form-input" type="password" value={form.app_secret} onChange={e => setForm({ ...form, app_secret: e.target.value })} />
                        </div>
                        <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                            <label className="form-label">Authorize URL</label>
                            <input className="form-input" value={form.authorize_url} onChange={e => setForm({ ...form, authorize_url: e.target.value })} />
                        </div>
                    </div>
                ) : type === 'wecom' ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0' }}>
                        {/* Prerequisites notice — all strings via i18n */}
                        <div style={{
                            padding: '16px',
                            borderRadius: '8px',
                            border: '1px solid var(--border-subtle)',
                            background: 'var(--bg-primary)',
                            fontSize: '13px',
                            lineHeight: 1.7,
                            color: 'var(--text-secondary)',
                        }}>
                            <div style={{ fontWeight: 600, fontSize: '13px', color: 'var(--text-primary)', marginBottom: '10px' }}>
                                {t('enterprise.identity.wecomNotice.title')}
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                <div>
                                    <div style={{ fontWeight: 500, color: 'var(--text-primary)', marginBottom: '3px' }}>
                                        {t('enterprise.identity.wecomNotice.syncTitle')}
                                    </div>
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                                        {t('enterprise.identity.wecomNotice.syncDesc')}
                                    </div>
                                </div>
                                <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '10px' }}>
                                    <div style={{ fontWeight: 500, color: 'var(--text-primary)', marginBottom: '3px' }}>
                                        {t('enterprise.identity.wecomNotice.ssoTitle')}
                                    </div>
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                                        {t('enterprise.identity.wecomNotice.ssoDesc')}
                                    </div>
                                </div>
                                <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '10px' }}>
                                    <div style={{ fontWeight: 500, color: 'var(--text-primary)', marginBottom: '3px' }}>
                                        {t('enterprise.identity.wecomNotice.messagingTitle')}
                                    </div>
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                                        {t('enterprise.identity.wecomNotice.messagingDesc')}
                                    </div>
                                </div>
                            </div>
                            <div style={{ marginTop: '14px', paddingTop: '12px', borderTop: '1px solid var(--border-subtle)', fontSize: '12px', color: 'var(--text-tertiary)', lineHeight: 1.6 }}>
                                {t('enterprise.identity.wecomNotice.footerText')}
                            </div>
                        </div>
                    </div>


                ) : type === 'dingtalk' ? (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                        <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('enterprise.identity.providerHints.dingtalk')}</div>
                        </div>
                        <div className="form-group">
                            <label className="form-label">App Key</label>
                            <input className="form-input" value={form.config.app_key || ''} onChange={e => setForm({ ...form, config: { ...form.config, app_key: e.target.value } })} placeholder="dingxxxxxxxxxxxx" />
                        </div>
                        <div className="form-group">
                            <label className="form-label">App Secret</label>
                            <input className="form-input" type="password" value={form.config.app_secret || ''} onChange={e => setForm({ ...form, config: { ...form.config, app_secret: e.target.value } })} />
                        </div>
                    </div>
                ) : type === 'google_workspace' ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                        <div style={{ padding: '14px', borderRadius: '8px', border: '1px solid var(--border-subtle)', background: 'var(--bg-primary)' }}>
                            <div style={{ fontWeight: 600, fontSize: '13px', marginBottom: '10px' }}>Google OAuth</div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                                <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                        {t('enterprise.identity.providerHints.google_workspace', 'Google Workspace: use one Client ID and Client Secret for both SSO and admin-authorized directory sync.')}
                                    </div>
                                </div>
                                <div className="form-group">
                                    <label className="form-label">Client ID</label>
                                    <input
                                        className="form-input"
                                        value={form.config.client_id || ''}
                                        onChange={e => setForm({ ...form, config: { ...form.config, client_id: e.target.value } })}
                                        placeholder="xxxxxxxx.apps.googleusercontent.com"
                                    />
                                </div>
                                <div className="form-group">
                                    <label className="form-label">Client Secret</label>
                                    <input
                                        className="form-input"
                                        type="password"
                                        value={form.config.client_secret || ''}
                                        onChange={e => setForm({ ...form, config: { ...form.config, client_secret: e.target.value } })}
                                    />
                                </div>
                                <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                                    <label className="form-label">{t('enterprise.identity.callbackUrl', 'Redirect URL (paste this in your app settings)')}</label>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        <div style={{
                                            flex: 1,
                                            padding: '8px 12px',
                                            background: 'var(--bg-elevated)',
                                            border: '1px solid var(--border-subtle)',
                                            borderRadius: '6px',
                                            fontSize: '12px',
                                            color: 'var(--text-primary)',
                                            fontFamily: 'monospace',
                                            whiteSpace: 'nowrap',
                                            overflow: 'hidden',
                                            textOverflow: 'ellipsis'
                                        }}>
                                            {providerCallbackUrl}
                                        </div>
                                        <LinearCopyButton
                                            className="btn btn-ghost btn-sm"
                                            style={{ fontSize: '11px', width: 'auto', minWidth: '70px', height: '33px' }}
                                            textToCopy={providerCallbackUrl}
                                            label={t('common.copy', 'Copy')}
                                            copiedLabel="Copied"
                                        />
                                    </div>
                                    <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                        {t('enterprise.identity.callbackUrlHint', "Add this URL as the OAuth redirect URI in your identity provider's app configuration.")}
                                    </div>
                                </div>
                                <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                                    <label className="form-label">Directory Sync Authorization</label>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                        <button
                                            className="btn btn-secondary btn-sm"
                                            type="button"
                                            onClick={() => existingProvider && handleGoogleAdminAuthorize(existingProvider.id)}
                                            disabled={!existingProvider}
                                        >
                                            {existingProvider?.config?.google_admin_authorized_email ? 'Re-authorize Admin Sync' : 'Authorize Admin Sync'}
                                        </button>
                                        {!existingProvider && (
                                            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                Please save the provider first.
                                            </span>
                                        )}
                                        {existingProvider?.config?.google_admin_authorized_email && (
                                            <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                                                Authorized as {existingProvider.config.google_admin_authorized_email}
                                            </span>
                                        )}
                                    </div>
                                    <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                        Sign in with a Google Workspace admin account to grant directory read access. Clawith will securely store a refresh token and use it for scheduled sync.
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                ) : type === 'feishu' ? (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                        <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('enterprise.identity.providerHints.feishu')}</div>
                        </div>
                        <div className="form-group">
                            <label className="form-label">App ID</label>
                            <input className="form-input" value={form.config.app_id || ''} onChange={e => setForm({ ...form, config: { ...form.config, app_id: e.target.value } })} placeholder="cli_xxxxxxxxxxxx" />
                        </div>
                        <div className="form-group">
                            <label className="form-label">App Secret</label>
                            <input className="form-input" type="password" value={form.config.app_secret || ''} onChange={e => setForm({ ...form, config: { ...form.config, app_secret: e.target.value } })} />
                        </div>
                    </div>
                ) : null}

                {/* Hide save/delete for WeCom while config is disabled */}
                {type !== 'wecom' && (
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '16px' }}>
                        <button className="btn btn-primary btn-sm" onClick={save} disabled={savingProvider}>
                            {savingProvider ? t('common.loading') : t('common.save', 'Save')}
                        </button>
                        {saveProviderOk && (
                            <span style={{ fontSize: '12px', color: 'var(--success)' }}>Saved</span>
                        )}
                        {existingProvider && (
                            <button className="btn btn-ghost btn-sm" style={{ color: 'var(--error)' }} onClick={async () => { const ok = await dialog.confirm('确定要删除此配置吗？', { title: '删除配置', danger: true, confirmLabel: '删除' }); if (ok) deleteProvider.mutate(existingProvider.id); }}>
                                {t('common.delete', 'Delete')}
                            </button>
                        )}
                    </div>
                )}
                {/* WeCom App IP Whitelist verification URL — hidden while WeCom config is disabled */}
                {type === 'wecom' && false && editingId && (existingProvider?.config?.verify_token || form.config?.verify_token) && (() => {
                    const verifyToken = form.config?.verify_token || existingProvider?.config?.verify_token || '';
                    const aesKey = form.config?.verify_aes_key || existingProvider?.config?.verify_aes_key || '';
                    // Use window.location.origin as the base, but if it's a private/non-standard URL let user know
                    const base = window.location.origin;
                    const callbackUrl = aesKey
                        ? `${base}/api/enterprise/org/wecom-callback/${verifyToken}?aes_key=${aesKey}`
                        : `${base}/api/enterprise/org/wecom-callback/${verifyToken}?aes_key=(configure EncodingAESKey above first)`;
                    return (
                        <div style={{ marginTop: '16px', padding: '12px', background: 'var(--bg-primary)', borderRadius: '6px', border: '1px solid var(--border-subtle)' }}>
                            <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '6px' }}>
                                WeCom Receive Message Server URL
                            </div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>
                                Step 1: Go to WeCom App Management (AgentID 1000010) → App Settings → Set Receive Message Server URL.
                                Use this URL. In the Token field, enter your Verify Token. In EncodingAESKey, enter your key below.
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <code style={{ flex: 1, fontSize: '11px', padding: '6px 10px', background: 'var(--bg-secondary)', borderRadius: '4px', wordBreak: 'break-all', color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>
                                    {callbackUrl}
                                </code>
                                {aesKey && (
                                    <LinearCopyButton
                                        className="btn btn-ghost"
                                        style={{ fontSize: '11px', padding: '4px 8px', whiteSpace: 'nowrap', flexShrink: 0 }}
                                        textToCopy={callbackUrl}
                                        label="Copy"
                                        copiedLabel="Copied"
                                    />
                                )}
                            </div>
                            {!aesKey && (
                                <div style={{ marginTop: '6px', fontSize: '11px', color: 'var(--warning, #f59e0b)' }}>
                                    Configure the Verify Token and EncodingAESKey fields above, then Save to generate the final URL.
                                </div>
                            )}
                            <div style={{ marginTop: '10px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                Step 2: After URL verification passes, configure Enterprise Trusted IP with your server IPs in the WeCom console.
                            </div>
                            <div style={{ marginTop: '4px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                Step 3: Paste the App Secret (from that same app page) into the App Secret field above.
                            </div>
                        </div>
                    );
                })()}

            </div>
        );
    };

    const renderOrgBrowser = (p: any) => {
        return (
            <div style={{ marginTop: '24px', paddingTop: '24px', borderTop: '1px dashed var(--border-subtle)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '16px' }}>
                    <div style={{ fontWeight: 500, fontSize: '14px' }}>{t('enterprise.org.orgBrowser', 'Organization Browser')}</div>

                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '8px' }}>
                        {['feishu', 'dingtalk', 'google_workspace'].includes(p.provider_type) && (
                            <button className="btn btn-secondary btn-sm" style={{ fontSize: '12px' }} onClick={() => triggerSync(p.id)} disabled={!!syncing}>
                                {syncing === p.id ? 'Syncing...' : 'Sync Directory'}
                            </button>
                        )}
                        {syncResult && (
                            <div style={{ padding: '6px 10px', borderRadius: '4px', fontSize: '11px', background: syncResult.error || (syncResult.errors && syncResult.errors.length > 0) ? 'rgba(255,100,0,0.1)' : 'rgba(0,200,0,0.1)' }}>
                                {syncResult.error
                                    ? `Error: ${syncResult.error}`
                                    : `Sync complete: ${syncResult.departments || 0} depts, ${syncResult.members || 0} members synced.`}
                                {syncResult.errors && syncResult.errors.length > 0 && (
                                    <div style={{ marginTop: '4px', color: 'var(--color-warning, #f90)' }}>
                                        {/* Show first error to help diagnose permission issues */}
                                        {`Warning: ${syncResult.errors[0]}`}
                                        {syncResult.errors.length > 1 && ` (+${syncResult.errors.length - 1} more)`}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                </div>


                <div style={{ display: 'flex', gap: '16px' }}>
                    <div style={{ width: '260px', borderRight: '1px solid var(--border-subtle)', paddingRight: '16px', maxHeight: '500px', overflowY: 'auto' }}>
                        <div style={{ padding: '6px 8px', borderRadius: '4px', cursor: 'pointer', fontSize: '13px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: !selectedDept ? 'rgba(224,238,238,0.1)' : 'transparent' }} onClick={() => setSelectedDept(null)}>
                            {t('common.all')}
                            {departmentsData.total_member > 0 && <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>({departmentsData.total_member})</span>}
                        </div>
                        <DeptTree departments={departmentsData.items} parentId={null} selectedDept={selectedDept} onSelect={setSelectedDept} level={0} />
                    </div>

                    <div style={{ flex: 1 }}>
                        <input className="form-input" placeholder={t("enterprise.org.searchMembers")} value={memberSearch} onChange={e => setMemberSearch(e.target.value)} style={{ marginBottom: '12px' }} />
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', maxHeight: '400px', overflowY: 'auto' }}>
                            {members.map((m: any) => (
                                <div key={m.id} style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '8px', borderRadius: '6px', border: '1px solid var(--border-subtle)' }}>
                                    <div style={{ width: '32px', height: '32px', borderRadius: '50%', background: 'var(--bg-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '14px', fontWeight: 600 }}>{m.name?.[0]}</div>
                                    <div>
                                        <div style={{ fontWeight: 500, fontSize: '13px' }}>{m.name}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                            {m.provider_type && <span style={{ marginRight: '4px', padding: '1px 4px', borderRadius: '3px', background: 'var(--bg-secondary)', fontSize: '10px' }}>{m.provider_type}</span>}
                                            {m.title || '-'} · {m.department_path || m.department_id || '-'}
                                        </div>
                                    </div>
                                </div>
                            ))}
                            {members.length === 0 && <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-tertiary)' }}>{t('enterprise.org.noMembers')}</div>}
                        </div>
                    </div>
                </div>
            </div>
        );
    };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
            {/* SSO status is now derived from per-channel toggles — no global switch */}

            {/* 1. Identity Providers Section */}
            <div className="card" style={{ padding: '0', overflow: 'hidden' }}>
                <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-subtle)', background: 'var(--bg-secondary)' }}>
                    <h3 style={{ margin: 0, fontSize: '15px', fontWeight: 600 }}>
                        {t('enterprise.identity.title', 'Organization & Directory Sync')}
                    </h3>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                        Configure enterprise directory synchronization and Identity Provider settings.
                    </div>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column' }}>
                    {IDP_TYPES.map((idp, index) => {
                        const existingProvider = providers.find((p: any) => p.provider_type === idp.type);
                        const isExpanded = expandedType === idp.type;

                        return (
                            <div key={idp.type} style={{ borderBottom: index < IDP_TYPES.length - 1 ? '1px solid var(--border-subtle)' : 'none' }}>
                                <div
                                    style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px', cursor: 'pointer', background: isExpanded ? 'var(--bg-secondary)' : 'transparent', transition: 'background 0.2s' }}
                                    onClick={() => handleExpand(idp.type, existingProvider)}
                                >
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                                        {idp.icon}
                                        <div>
                                            <div style={{ fontWeight: 500, fontSize: '14px' }}>{idp.name}</div>
                                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{idp.desc}</div>
                                        </div>
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                                        {existingProvider ? (
                                            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: '8px' }}>
                                                <span className="badge badge-success" style={{ fontSize: '10px' }}>Active</span>
                                                {existingProvider.last_synced_at && (
                                                    <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>
                                                        Synced: {new Date(existingProvider.last_synced_at).toLocaleDateString()}
                                                    </span>
                                                )}
                                            </div>
                                        ) : (
                                            <span className="badge badge-secondary" style={{ fontSize: '10px' }}>Not configured</span>
                                        )}
                                        <div style={{ color: 'var(--text-tertiary)', transform: isExpanded ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s', fontSize: '12px' }}>
                                            ▼
                                        </div>
                                    </div>
                                </div>

                                {isExpanded && (
                                    <div style={{ padding: '0 20px 20px', background: 'var(--bg-secondary)' }}>
                                        {renderForm(idp.type, existingProvider)}

                                        {/* Per-channel SSO Login URLs & Toggle */}
                                        {['feishu', 'dingtalk', 'google_workspace', 'oauth2'].includes(idp.type) && (
                                            <SsoChannelSection
                                                idpType={idp.type}
                                                existingProvider={existingProvider}
                                                tenant={tenant}
                                                t={t}
                                            />
                                        )}
                                        {existingProvider && idp.type !== 'wecom' && renderOrgBrowser(existingProvider)}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>

        </div>
    );
}

