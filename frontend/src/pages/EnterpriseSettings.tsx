import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { enterpriseApi, skillApi } from '../services/api';
import PromptModal from '../components/PromptModal';
import FileBrowser from '../components/FileBrowser';
import type { FileBrowserApi } from '../components/FileBrowser';
import { saveAccentColor, getSavedAccentColor, resetAccentColor, PRESET_COLORS } from '../utils/theme';
import UserManagement from './UserManagement';
import InvitationCodes from './InvitationCodes';
import LinearCopyButton from '../components/LinearCopyButton';
// API helpers for enterprise endpoints
async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
    const token = localStorage.getItem('token');
    const res = await fetch(`/api${url}`, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        // Pydantic validation errors return detail as an array of objects,
        // each with {loc, msg, type}. Extract readable messages from the array.
        const detail = body.detail;
        const msg = Array.isArray(detail)
            ? detail.map((e: any) => e.msg || JSON.stringify(e)).join('; ')
            : (typeof detail === 'string' ? detail : 'Error');
        throw new Error(msg);
    }
    if (res.status === 204) return undefined as T;
    return res.json();
}

interface LLMModel {
    id: string; provider: string; model: string; label: string;
    base_url?: string; api_key_masked?: string; max_tokens_per_day?: number; enabled: boolean; supports_vision?: boolean; max_output_tokens?: number; request_timeout?: number; temperature?: number; created_at: string;
}

interface LLMProviderSpec {
    provider: string;
    display_name: string;
    protocol: string;
    default_base_url?: string | null;
    supports_tool_choice: boolean;
    default_max_tokens: number;
}

const FALLBACK_LLM_PROVIDERS: LLMProviderSpec[] = [
    { provider: 'anthropic', display_name: 'Anthropic', protocol: 'anthropic', default_base_url: 'https://api.anthropic.com', supports_tool_choice: false, default_max_tokens: 8192 },
    { provider: 'openai', display_name: 'OpenAI', protocol: 'openai_compatible', default_base_url: 'https://api.openai.com/v1', supports_tool_choice: true, default_max_tokens: 16384 },
    { provider: 'azure', display_name: 'Azure OpenAI', protocol: 'openai_compatible', default_base_url: '', supports_tool_choice: true, default_max_tokens: 16384 },
    { provider: 'deepseek', display_name: 'DeepSeek', protocol: 'openai_compatible', default_base_url: 'https://api.deepseek.com/v1', supports_tool_choice: true, default_max_tokens: 8192 },
    { provider: 'minimax', display_name: 'MiniMax', protocol: 'openai_compatible', default_base_url: 'https://api.minimaxi.com/v1', supports_tool_choice: true, default_max_tokens: 16384 },
    { provider: 'qwen', display_name: 'Qwen (DashScope)', protocol: 'openai_compatible', default_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', supports_tool_choice: true, default_max_tokens: 8192 },
    { provider: 'zhipu', display_name: 'Zhipu', protocol: 'openai_compatible', default_base_url: 'https://open.bigmodel.cn/api/paas/v4', supports_tool_choice: true, default_max_tokens: 8192 },
    { provider: 'baidu', display_name: 'Baidu (Qianfan)', protocol: 'openai_compatible', default_base_url: 'https://qianfan.baidubce.com/v2', supports_tool_choice: false, default_max_tokens: 4096 },
    { provider: 'gemini', display_name: 'Gemini', protocol: 'gemini', default_base_url: 'https://generativelanguage.googleapis.com/v1beta', supports_tool_choice: true, default_max_tokens: 8192 },
    { provider: 'openrouter', display_name: 'OpenRouter', protocol: 'openai_compatible', default_base_url: 'https://openrouter.ai/api/v1', supports_tool_choice: true, default_max_tokens: 4096 },
    { provider: 'kimi', display_name: 'Kimi (Moonshot)', protocol: 'openai_compatible', default_base_url: 'https://api.moonshot.cn/v1', supports_tool_choice: true, default_max_tokens: 8192 },
    { provider: 'vllm', display_name: 'vLLM', protocol: 'openai_compatible', default_base_url: 'http://localhost:8000/v1', supports_tool_choice: true, default_max_tokens: 4096 },
    { provider: 'ollama', display_name: 'Ollama', protocol: 'openai_compatible', default_base_url: 'http://localhost:11434/v1', supports_tool_choice: true, default_max_tokens: 4096 },
    { provider: 'sglang', display_name: 'SGLang', protocol: 'openai_compatible', default_base_url: 'http://localhost:30000/v1', supports_tool_choice: true, default_max_tokens: 4096 },
    { provider: 'custom', display_name: 'Custom', protocol: 'openai_compatible', default_base_url: '', supports_tool_choice: true, default_max_tokens: 4096 },
];

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
            alert(t('enterprise.identity.saveFirst', 'Please save the configuration first to enable SSO.'));
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
function OrgTab({ tenant }: { tenant: any }) {
    const { t } = useTranslation();
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

    const IDP_TYPES = [
        { type: 'feishu', name: 'Feishu', desc: 'Feishu / Lark Integration', icon: <img src="/feishu.png" width="20" height="20" alt="Feishu" /> },
        { type: 'wecom', name: 'WeCom', desc: 'WeChat Work Integration', icon: <img src="/wecom.png" width="20" height="20" style={{ borderRadius: '4px' }} alt="WeCom" /> },
        { type: 'dingtalk', name: 'DingTalk', desc: 'DingTalk App Integration', icon: <img src="/dingtalk.png" width="20" height="20" style={{ borderRadius: '4px' }} alt="DingTalk" /> },
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
                wecom: { corp_id: '', secret: '', agent_id: '', bot_id: '', bot_secret: '' },
            };
            const nameMap: Record<string, string> = { feishu: 'Feishu', wecom: 'WeCom', dingtalk: 'DingTalk', oauth2: 'OAuth2' };
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
        return (
            <div style={{ marginTop: '16px', paddingTop: '16px', borderTop: '1px solid var(--border-subtle)' }}>
                {/* Setup Guide moved to the top */}
                {['feishu', 'dingtalk', 'wecom'].includes(type) && (
                    <div style={{ background: 'var(--bg-primary)', padding: '16px', borderRadius: '8px', border: '1px solid var(--border-subtle)', marginBottom: '20px', fontSize: '12px' }}>
                        <div style={{ fontWeight: 600, fontSize: '13px', marginBottom: '8px', color: 'var(--text-primary)' }}>
                            👉 {t('enterprise.org.syncSetupGuide', 'Setup Guide & Required Permissions')}
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
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                        <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>
                                {t('enterprise.identity.providerHints.wecom')}
                            </div>
                        </div>
                        <div className="form-group">
                            <label className="form-label">Corp ID</label>
                            <input className="form-input" value={form.config.corp_id || ''} onChange={e => setForm({ ...form, config: { ...form.config, corp_id: e.target.value } })} placeholder="wwxxxxxxxxxxxx" />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Secret</label>
                            <input className="form-input" type="password" value={form.config.secret || ''} onChange={e => setForm({ ...form, config: { ...form.config, secret: e.target.value } })} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Agent ID (Optional)</label>
                            <input className="form-input" value={form.config.agent_id || ''} onChange={e => setForm({ ...form, config: { ...form.config, agent_id: e.target.value } })} />
                        </div>
                        <div style={{ gridColumn: '1 / -1', height: '1px', background: 'var(--border-subtle)', margin: '8px 0' }} />
                        <div className="form-group">
                            <label className="form-label">Bot ID (Intelligent Robot)</label>
                            <input className="form-input" value={form.config.bot_id || ''} onChange={e => setForm({ ...form, config: { ...form.config, bot_id: e.target.value } })} placeholder="aibXXXXXXXXXXXX" />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Bot Secret</label>
                            <input className="form-input" type="password" value={form.config.bot_secret || ''} onChange={e => setForm({ ...form, config: { ...form.config, bot_secret: e.target.value } })} />
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

                <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '16px' }}>
                    <button className="btn btn-primary btn-sm" onClick={save} disabled={savingProvider}>
                        {savingProvider ? t('common.loading') : t('common.save', 'Save')}
                    </button>
                    {saveProviderOk && (
                        <span style={{ fontSize: '12px', color: 'var(--success)' }}>Saved</span>
                    )}
                    {existingProvider && (
                        <button className="btn btn-ghost btn-sm" style={{ color: 'var(--error)' }} onClick={() => confirm('Are you sure you want to delete this configuration?') && deleteProvider.mutate(existingProvider.id)}>
                            {t('common.delete', 'Delete')}
                        </button>
                    )}
                </div>
            </div>
        );
    };

    const renderOrgBrowser = (p: any) => {
        return (
            <div style={{ marginTop: '24px', paddingTop: '24px', borderTop: '1px dashed var(--border-subtle)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '16px' }}>
                    <div style={{ fontWeight: 500, fontSize: '14px' }}>{t('enterprise.org.orgBrowser', 'Organization Browser')}</div>

                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '8px' }}>
                        {['feishu', 'dingtalk', 'wecom'].includes(p.provider_type) && (
                            <button className="btn btn-secondary btn-sm" style={{ fontSize: '12px' }} onClick={() => triggerSync(p.id)} disabled={!!syncing}>
                                {syncing === p.id ? 'Syncing...' : 'Sync Directory'}
                            </button>
                        )}
                        {syncResult && (
                            <div style={{ padding: '6px 10px', borderRadius: '4px', fontSize: '11px', background: syncResult.error ? 'rgba(255,0,0,0.1)' : 'rgba(0,200,0,0.1)' }}>
                                {syncResult.error ? `Error: ${syncResult.error}` : `Sync complete: ${syncResult.users_created || 0} users created, ${syncResult.profiles_synced || 0} profiles synced.`}
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
                                        {['feishu', 'dingtalk', 'wecom', 'oauth2'].includes(idp.type) && (
                                            <SsoChannelSection
                                                idpType={idp.type}
                                                existingProvider={existingProvider}
                                                tenant={tenant}
                                                t={t}
                                            />
                                        )}
                                        {existingProvider && renderOrgBrowser(existingProvider)}
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


// ─── Theme Color Picker ────────────────────────────
function ThemeColorPicker() {
    const { t } = useTranslation();
    const [currentColor, setCurrentColor] = useState(getSavedAccentColor() || '');
    const [customHex, setCustomHex] = useState('');

    const apply = (hex: string) => {
        setCurrentColor(hex);
        saveAccentColor(hex);
    };

    const handleReset = () => {
        setCurrentColor('');
        setCustomHex('');
        resetAccentColor();
    };

    const handleCustom = () => {
        const hex = customHex.trim();
        if (/^#[0-9a-fA-F]{6}$/.test(hex)) {
            apply(hex);
        }
    };

    return (
        <div className="card" style={{ marginTop: '16px', marginBottom: '16px' }}>
            <h4 style={{ marginBottom: '12px' }}>{t('enterprise.config.themeColor')}</h4>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '12px' }}>
                {PRESET_COLORS.map(c => (
                    <div
                        key={c.hex}
                        onClick={() => apply(c.hex)}
                        title={c.name}
                        style={{
                            width: '32px', height: '32px', borderRadius: '8px',
                            background: c.hex, cursor: 'pointer',
                            border: currentColor === c.hex ? '2px solid var(--text-primary)' : '2px solid transparent',
                            outline: currentColor === c.hex ? '2px solid var(--bg-primary)' : 'none',
                            transition: 'all 120ms ease',
                        }}
                    />
                ))}
            </div>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <input
                    className="input"
                    value={customHex}
                    onChange={e => setCustomHex(e.target.value)}
                    placeholder="#hex"
                    style={{ width: '120px', fontSize: '13px', fontFamily: 'var(--font-mono)' }}
                    onKeyDown={e => e.key === 'Enter' && handleCustom()}
                />
                <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={handleCustom}>Apply</button>
                {currentColor && (
                    <button className="btn btn-ghost" style={{ fontSize: '12px', color: 'var(--text-tertiary)' }} onClick={handleReset}>Reset</button>
                )}
                {currentColor && (
                    <div style={{ width: '20px', height: '20px', borderRadius: '4px', background: currentColor, border: '1px solid var(--border-default)' }} />
                )}
            </div>
        </div>
    );
}





// Preset common models per provider
const PRESET_MODELS: Record<string, string[]> = {
    'openai': ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo', 'o1-preview', 'o1-mini'],
    'anthropic': ['claude-3-5-sonnet-20241022', 'claude-3-5-sonnet-20240620', 'claude-3-5-haiku-20241022', 'claude-3-opus-20240229'],
    'google': ['gemini-1.5-pro', 'gemini-1.5-flash', 'gemini-2.0-flash'],
    'deepseek': ['deepseek-chat', 'deepseek-reasoner'],
    'ollama': ['llama3.1', 'llama3.2', 'qwen2.5', 'mistral', 'gemma2'],
    'azure': ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo'],
};

// ─── Main Component ────────────────────────────────
// ─── Enterprise KB Browser ─────────────────────────
function EnterpriseKBBrowser({ onRefresh }: { onRefresh: () => void; refreshKey: number }) {
    const kbAdapter: FileBrowserApi = {
        list: (path) => enterpriseApi.kbFiles(path),
        read: (path) => enterpriseApi.kbRead(path),
        write: (path, content) => enterpriseApi.kbWrite(path, content),
        delete: (path) => enterpriseApi.kbDelete(path),
        upload: (file, path) => enterpriseApi.kbUpload(file, path),
    };
    return <FileBrowser api={kbAdapter} features={{ upload: true, newFolder: true, edit: true, delete: true, directoryNavigation: true }} onRefresh={onRefresh} />;
}

// ─── Skills Tab ────────────────────────────────────
function SkillsTab() {
    const { t } = useTranslation();
    const [refreshKey, setRefreshKey] = useState(0);
    const [showClawhubModal, setShowClawhubModal] = useState(false);
    const [showUrlModal, setShowUrlModal] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<any[]>([]);
    const [searching, setSearching] = useState(false);
    const [hasSearched, setHasSearched] = useState(false);
    const [installing, setInstalling] = useState<string | null>(null);
    const [urlInput, setUrlInput] = useState('');
    const [urlPreview, setUrlPreview] = useState<any | null>(null);
    const [urlPreviewing, setUrlPreviewing] = useState(false);
    const [urlImporting, setUrlImporting] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [showSettings, setShowSettings] = useState(false);
    const [tokenInput, setTokenInput] = useState('');
    const [tokenStatus, setTokenStatus] = useState<{ configured: boolean; source: string; masked: string; clawhub_configured?: boolean; clawhub_masked?: string } | null>(null);
    const [savingToken, setSavingToken] = useState(false);
    const [clawhubKeyInput, setClawhubKeyInput] = useState('');
    const [savingClawhubKey, setSavingClawhubKey] = useState(false);

    const showToast = (message: string, type: 'success' | 'error' = 'success') => {
        setToast({ message, type });
        setTimeout(() => setToast(null), 4000);
    };

    const adapter: FileBrowserApi = useMemo(() => ({
        list: (path: string) => skillApi.browse.list(path),
        read: (path: string) => skillApi.browse.read(path),
        write: (path: string, content: string) => skillApi.browse.write(path, content),
        delete: (path: string) => skillApi.browse.delete(path),
    }), []);

    const handleSearch = async () => {
        if (!searchQuery.trim()) return;
        setSearching(true);
        setSearchResults([]);
        setHasSearched(true);
        try {
            const results = await skillApi.clawhub.search(searchQuery);
            setSearchResults(results);
        } catch (e: any) {
            showToast(e.message || 'Search failed', 'error');
        }
        setSearching(false);
    };

    const handleInstall = async (slug: string) => {
        setInstalling(slug);
        try {
            const result = await skillApi.clawhub.install(slug);
            const tierLabel = result.tier === 1 ? 'Tier 1 (Pure Prompt)' : result.tier === 2 ? 'Tier 2 (CLI/API)' : 'Tier 3 (OpenClaw Native)';
            showToast(`Installed "${result.name}" — ${tierLabel}, ${result.file_count} files`);
            setRefreshKey(k => k + 1);
            // Remove from search results
            setSearchResults(prev => prev.filter(r => r.slug !== slug));
        } catch (e: any) {
            showToast(e.message || 'Install failed', 'error');
        }
        setInstalling(null);
    };

    const handleUrlPreview = async () => {
        if (!urlInput.trim()) return;
        setUrlPreviewing(true);
        setUrlPreview(null);
        try {
            const preview = await skillApi.previewUrl(urlInput);
            setUrlPreview(preview);
        } catch (e: any) {
            showToast(e.message || 'Preview failed', 'error');
        }
        setUrlPreviewing(false);
    };

    const handleUrlImport = async () => {
        if (!urlInput.trim()) return;
        setUrlImporting(true);
        try {
            const result = await skillApi.importFromUrl(urlInput);
            showToast(`Imported "${result.name}" — ${result.file_count} files`);
            setRefreshKey(k => k + 1);
            setShowUrlModal(false);
            setUrlInput('');
            setUrlPreview(null);
        } catch (e: any) {
            showToast(e.message || 'Import failed', 'error');
        }
        setUrlImporting(false);
    };

    const tierBadge = (tier: number) => {
        const styles: Record<number, { bg: string; color: string; label: string }> = {
            1: { bg: 'rgba(52,199,89,0.12)', color: 'var(--success, #34c759)', label: 'Tier 1 · Pure Prompt' },
            2: { bg: 'rgba(255,159,10,0.12)', color: 'var(--warning, #ff9f0a)', label: 'Tier 2 · CLI/API' },
            3: { bg: 'rgba(255,59,48,0.12)', color: 'var(--error, #ff3b30)', label: 'Tier 3 · OpenClaw Native' },
        };
        const s = styles[tier] || styles[1];
        return (
            <span style={{ padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 500, background: s.bg, color: s.color }}>
                {s.label}
            </span>
        );
    };

    return (
        <div>
            <div style={{ marginBottom: '12px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                    <h3>{t('enterprise.tabs.skills', 'Skill Registry')}</h3>
                    <p style={{ fontSize: '13px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                        {t('enterprise.tools.manageGlobalSkills')}
                    </p>
                </div>
                <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
                    <button
                        className="btn btn-secondary"
                        style={{ fontSize: '13px', padding: '6px 10px', minWidth: 'auto' }}
                        onClick={async () => {
                            setShowSettings(s => !s);
                            if (!tokenStatus) {
                                try {
                                    const status = await skillApi.settings.getToken();
                                    setTokenStatus(status);
                                } catch { /* ignore */ }
                            }
                        }}
                        title="Settings"
                    >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <circle cx="12" cy="12" r="3" />
                            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
                        </svg>
                    </button>
                    <button
                        className="btn btn-secondary"
                        style={{ fontSize: '13px' }}
                        onClick={() => { setShowUrlModal(true); setUrlInput(''); setUrlPreview(null); }}
                    >
                        {t('enterprise.tools.importFromUrl')}
                    </button>
                    <button
                        className="btn btn-primary"
                        style={{ fontSize: '13px' }}
                        onClick={() => { setShowClawhubModal(true); setSearchQuery(''); setSearchResults([]); setHasSearched(false); }}
                    >
                        {t('enterprise.tools.browseClawhub')}
                    </button>
                </div>
            </div>

            {/* GitHub Token Settings Panel */}
            {showSettings && (
                <div style={{
                    marginBottom: '16px', padding: '16px', borderRadius: '8px',
                    border: '1px solid var(--border-primary)',
                    background: 'var(--bg-secondary, rgba(255,255,255,0.02))',
                }}>
                    <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        {t('enterprise.tools.githubToken')}
                        <span className="metric-tooltip-trigger" style={{ display: 'inline-flex', alignItems: 'center', cursor: 'help', color: 'var(--text-tertiary)' }}>
                            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="8" cy="8" r="6.5" /><path d="M8 7v4M8 5.5v0" /></svg>
                            <span className="metric-tooltip" style={{ width: '300px', bottom: 'auto', top: 'calc(100% + 6px)', left: '-8px', fontWeight: 400 }}>
                                <div style={{ marginBottom: '6px', fontWeight: 500 }}>{t('enterprise.tools.howToGenerateGithubToken')}</div>
                                {t('enterprise.tools.githubTokenStep1')}<br />
                                {t('enterprise.tools.githubTokenStep2')}<br />
                                {t('enterprise.tools.githubTokenStep3')}<br />
                                {t('enterprise.tools.githubTokenStep4')}<br />
                                {t('enterprise.tools.githubTokenStep5')}<br />
                                <div style={{ marginTop: '6px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                    {t('enterprise.tools.orVisit')}
                                </div>
                            </span>
                        </span>
                    </div>
                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                        {t('enterprise.tools.githubTokenDesc')}
                    </p>
                    {tokenStatus?.configured && (
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                            {t('enterprise.tools.currentToken')} <code style={{ padding: '2px 6px', borderRadius: '4px', background: 'var(--bg-tertiary)', fontSize: '11px' }}>{tokenStatus.masked}</code>
                            <span style={{ marginLeft: '8px', color: 'var(--text-tertiary)' }}>({tokenStatus.source})</span>
                        </div>
                    )}
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        {/* Hidden inputs to absorb browser autofill */}
                        <input type="text" name="prevent_autofill_user" style={{ display: 'none' }} tabIndex={-1} />
                        <input type="password" name="prevent_autofill_pass" style={{ display: 'none' }} tabIndex={-1} />
                        <input
                            type="text"
                            className="input"
                            autoComplete="off"
                            data-form-type="other"
                            placeholder="ghp_xxxxxxxxxxxx"
                            value={tokenInput}
                            onChange={e => setTokenInput(e.target.value)}
                            style={{ flex: 1, fontSize: '13px', fontFamily: 'monospace', WebkitTextSecurity: 'disc' } as React.CSSProperties}
                        />
                        <button
                            className="btn btn-primary"
                            style={{ fontSize: '13px' }}
                            disabled={!tokenInput.trim() || savingToken}
                            onClick={async () => {
                                setSavingToken(true);
                                try {
                                    await skillApi.settings.setToken(tokenInput.trim());
                                    const status = await skillApi.settings.getToken();
                                    setTokenStatus(status);
                                    setTokenInput('');
                                    showToast(t('enterprise.tools.githubTokenSaved'));
                                } catch (e: any) {
                                    showToast(e.message || t('enterprise.tools.failedToSave'), 'error');
                                }
                                setSavingToken(false);
                            }}
                        >
                            {savingToken ? t('enterprise.tools.saving') : t('enterprise.tools.save')}
                        </button>
                        {tokenStatus?.configured && tokenStatus.source === 'tenant' && (
                            <button
                                className="btn btn-secondary"
                                style={{ fontSize: '13px' }}
                                onClick={async () => {
                                    try {
                                        await skillApi.settings.setToken('');
                                        const status = await skillApi.settings.getToken();
                                        setTokenStatus(status);
                                        showToast(t('enterprise.tools.tokenCleared'));
                                    } catch (e: any) {
                                        showToast(e.message || t('enterprise.tools.failed'), 'error');
                                    }
                                }}
                            >
                                {t('enterprise.tools.clear')}
                            </button>
                        )}
                    </div>

                    {/* ClawHub API Key */}
                    <div style={{ marginTop: '16px' }}>
                        <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                            {t('enterprise.tools.clawhubApiKey')}
                            <span className="metric-tooltip-trigger" style={{ display: 'inline-flex', alignItems: 'center', cursor: 'help', color: 'var(--text-tertiary)' }}>
                                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="8" cy="8" r="6.5" /><path d="M8 7v4M8 5.5v0" /></svg>
                                <span className="metric-tooltip" style={{ width: '280px', bottom: 'auto', top: 'calc(100% + 6px)', left: '-8px', fontWeight: 400 }}>
                                    {t('enterprise.tools.clawhubApiKeyDesc')}
                                </span>
                            </span>
                        </div>
                        <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                            {t('enterprise.tools.authenticatedRequestsGetHigherRateLimits')}
                        </p>
                        {tokenStatus?.clawhub_configured && (
                            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                                {t('enterprise.tools.currentKey')} <code style={{ padding: '2px 6px', borderRadius: '4px', background: 'var(--bg-tertiary)', fontSize: '11px' }}>{tokenStatus.clawhub_masked}</code>
                            </div>
                        )}
                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                            <input type="text" name="prevent_autofill_ch_user" style={{ display: 'none' }} tabIndex={-1} />
                            <input type="password" name="prevent_autofill_ch_pass" style={{ display: 'none' }} tabIndex={-1} />
                            <input
                                type="text"
                                className="input"
                                autoComplete="off"
                                data-form-type="other"
                                placeholder="sk-ant-xxxxxxxxxxxx"
                                value={clawhubKeyInput}
                                onChange={e => setClawhubKeyInput(e.target.value)}
                                style={{ flex: 1, fontSize: '13px', fontFamily: 'monospace', WebkitTextSecurity: 'disc' } as React.CSSProperties}
                            />
                            <button
                                className="btn btn-primary"
                                style={{ fontSize: '13px' }}
                                disabled={!clawhubKeyInput.trim() || savingClawhubKey}
                                onClick={async () => {
                                    setSavingClawhubKey(true);
                                    try {
                                        await skillApi.settings.setClawhubKey(clawhubKeyInput.trim());
                                        const status = await skillApi.settings.getToken();
                                        setTokenStatus(status);
                                        setClawhubKeyInput('');
                                        showToast(t('enterprise.tools.clawhubApiKeySaved'));
                                    } catch (e: any) {
                                        showToast(e.message || t('enterprise.tools.failedToSave'), 'error');
                                    }
                                    setSavingClawhubKey(false);
                                }}
                            >
                                {savingClawhubKey ? t('enterprise.tools.saving') : t('enterprise.tools.save')}
                            </button>
                            {tokenStatus?.clawhub_configured && (
                                <button
                                    className="btn btn-secondary"
                                    style={{ fontSize: '13px' }}
                                    onClick={async () => {
                                        try {
                                            await skillApi.settings.setClawhubKey('');
                                            const status = await skillApi.settings.getToken();
                                            setTokenStatus(status);
                                            showToast(t('enterprise.tools.tokenCleared'));
                                        } catch (e: any) {
                                            showToast(e.message || t('enterprise.tools.failed'), 'error');
                                        }
                                    }}
                                >
                                    {t('enterprise.tools.clear')}
                                </button>
                            )}
                        </div>
                    </div>
                </div>
            )}

            <FileBrowser
                key={refreshKey}
                api={adapter}
                features={{ newFile: true, newFolder: true, edit: true, delete: true, directoryNavigation: true }}
                title={t('agent.skills.skillFiles', 'Skill Files')}
                onRefresh={() => setRefreshKey(k => k + 1)}
            />

            {/* Toast */}
            {toast && (
                <div style={{
                    position: 'fixed', bottom: '24px', right: '24px', zIndex: 10000,
                    padding: '12px 20px', borderRadius: '8px', fontSize: '13px', fontWeight: 500,
                    background: toast.type === 'error' ? 'rgba(255,59,48,0.95)' : 'rgba(52,199,89,0.95)',
                    color: '#fff', boxShadow: '0 4px 16px rgba(0,0,0,0.2)', maxWidth: '400px',
                    animation: 'fadeIn 200ms ease',
                }}>
                    {toast.message}
                </div>
            )}

            {/* ClawHub Search Modal */}
            {showClawhubModal && (
                <div style={{
                    position: 'fixed', inset: 0, zIndex: 9999,
                    background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                }} onClick={() => setShowClawhubModal(false)}>
                    <div style={{
                        background: 'var(--bg-primary)', borderRadius: '12px', width: '640px', maxHeight: '80vh',
                        display: 'flex', flexDirection: 'column', border: '1px solid var(--border-default)',
                        boxShadow: '0 16px 48px rgba(0,0,0,0.2)',
                    }} onClick={e => e.stopPropagation()}>
                        {/* Header */}
                        <div style={{ padding: '20px 24px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                <h3 style={{ margin: 0, fontSize: '16px' }}>{t('enterprise.tools.browseClawhub')}</h3>
                                <button className="btn btn-ghost" onClick={() => setShowClawhubModal(false)} style={{ padding: '4px 8px', fontSize: '16px', lineHeight: 1 }}>x</button>
                            </div>
                            <div style={{ display: 'flex', gap: '8px' }}>
                                <input
                                    className="input"
                                    placeholder={t('enterprise.tools.searchSkills')}
                                    value={searchQuery}
                                    onChange={e => setSearchQuery(e.target.value)}
                                    onKeyDown={e => e.key === 'Enter' && handleSearch()}
                                    autoFocus
                                    style={{ flex: 1, fontSize: '13px' }}
                                />
                                <button className="btn btn-primary" onClick={handleSearch} disabled={searching} style={{ fontSize: '13px' }}>
                                    {searching ? t('enterprise.tools.searching') : t('enterprise.tools.search')}
                                </button>
                            </div>
                        </div>
                        {/* Results */}
                        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 24px' }}>
                            {searchResults.length === 0 && !searching && (
                                <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                                    {hasSearched ? t('enterprise.tools.noResultsFound') : t('enterprise.tools.searchForSkills')}
                                </div>
                            )}
                            {searching && (
                                <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                                    {t('enterprise.tools.searchingClawhub')}
                                </div>
                            )}
                            {searchResults.map((r: any) => (
                                <div key={r.slug} style={{
                                    padding: '12px 0', borderBottom: '1px solid var(--border-subtle)',
                                    display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px',
                                }}>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                                            <span style={{ fontWeight: 600, fontSize: '14px' }}>{r.displayName}</span>
                                            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>{r.slug}</span>
                                            {r.version && <span style={{ fontSize: '10px', color: 'var(--accent-text)', background: 'var(--accent-subtle)', padding: '1px 6px', borderRadius: '4px' }}>v{r.version}</span>}
                                        </div>
                                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: '1.4' }}>
                                            {r.summary?.slice(0, 160)}{r.summary?.length > 160 ? '...' : ''}
                                        </div>
                                        {r.updatedAt && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>Updated {new Date(r.updatedAt).toLocaleDateString()}</div>}
                                    </div>
                                    <button
                                        className="btn btn-secondary"
                                        style={{ fontSize: '12px', flexShrink: 0 }}
                                        disabled={installing === r.slug}
                                        onClick={() => handleInstall(r.slug)}
                                    >
                                        {installing === r.slug ? t('enterprise.tools.installing') : t('enterprise.tools.install')}
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            )}

            {/* URL Import Modal */}
            {showUrlModal && (
                <div style={{
                    position: 'fixed', inset: 0, zIndex: 9999,
                    background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                }} onClick={() => setShowUrlModal(false)}>
                    <div style={{
                        background: 'var(--bg-primary)', borderRadius: '12px', width: '560px',
                        border: '1px solid var(--border-default)', boxShadow: '0 16px 48px rgba(0,0,0,0.2)',
                    }} onClick={e => e.stopPropagation()}>
                        <div style={{ padding: '20px 24px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                <h3 style={{ margin: 0, fontSize: '16px' }}>{t('enterprise.tools.importFromUrl')}</h3>
                                <button className="btn btn-ghost" onClick={() => setShowUrlModal(false)} style={{ padding: '4px 8px', fontSize: '16px', lineHeight: 1 }}>x</button>
                            </div>
                            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', margin: '0 0 12px' }}>
                                {t('enterprise.tools.pasteGithubUrl')}
                            </p>
                            <div style={{ display: 'flex', gap: '8px' }}>
                                <input
                                    className="input"
                                    placeholder={t('enterprise.tools.githubUrlPlaceholder')}
                                    value={urlInput}
                                    onChange={e => { setUrlInput(e.target.value); setUrlPreview(null); }}
                                    autoFocus
                                    style={{ flex: 1, fontSize: '13px', fontFamily: 'var(--font-mono)' }}
                                    onKeyDown={e => e.key === 'Enter' && handleUrlPreview()}
                                />
                                <button className="btn btn-secondary" onClick={handleUrlPreview} disabled={urlPreviewing || !urlInput.trim()} style={{ fontSize: '12px' }}>
                                    {urlPreviewing ? t('enterprise.tools.loading') : t('enterprise.tools.preview')}
                                </button>
                            </div>
                        </div>

                        {/* Preview result */}
                        {urlPreview && (
                            <div style={{ padding: '16px 24px' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                                    <span style={{ fontWeight: 600, fontSize: '14px' }}>{urlPreview.name}</span>
                                    {tierBadge(urlPreview.tier)}
                                    {urlPreview.has_scripts && (
                                        <span style={{ padding: '2px 8px', borderRadius: '4px', fontSize: '11px', background: 'rgba(255,59,48,0.1)', color: 'var(--error, #ff3b30)' }}>
                                            Contains scripts
                                        </span>
                                    )}
                                </div>
                                {urlPreview.description && (
                                    <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: '0 0 8px' }}>{urlPreview.description}</p>
                                )}
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                    {urlPreview.files?.length} files, {(urlPreview.total_size / 1024).toFixed(1)} KB
                                </div>
                                <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                                    <button className="btn btn-secondary" onClick={() => setShowUrlModal(false)} style={{ fontSize: '13px' }}>Cancel</button>
                                    <button className="btn btn-primary" onClick={handleUrlImport} disabled={urlImporting} style={{ fontSize: '13px' }}>
                                        {urlImporting ? 'Importing...' : 'Import'}
                                    </button>
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}




// ─── Company Name Editor ───────────────────────────
function CompanyNameEditor() {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const tenantId = localStorage.getItem('current_tenant_id') || '';
    const [name, setName] = useState('');
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        if (!tenantId) return;
        fetchJson<any>(`/tenants/${tenantId}`)
            .then(d => { if (d?.name) setName(d.name); })
            .catch(() => { });
    }, [tenantId]);

    const handleSave = async () => {
        if (!tenantId || !name.trim()) return;
        setSaving(true);
        try {
            await fetchJson(`/tenants/${tenantId}`, {
                method: 'PUT', body: JSON.stringify({ name: name.trim() }),
            });
            qc.invalidateQueries({ queryKey: ['tenants'] });
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        } catch (e) { }
        setSaving(false);
    };

    return (
        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
            <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                <input
                    className="form-input"
                    value={name}
                    onChange={e => setName(e.target.value)}
                    placeholder={t('enterprise.companyName.placeholder', 'Enter company name')}
                    style={{ flex: 1, fontSize: '14px' }}
                    onKeyDown={e => e.key === 'Enter' && handleSave()}
                />
                <button className="btn btn-primary" onClick={handleSave} disabled={saving || !name.trim()}>
                    {saving ? t('common.loading') : t('common.save', 'Save')}
                </button>
                {saved && <span style={{ color: 'var(--success)', fontSize: '12px' }}>✅</span>}
            </div>
        </div>
    );
}


// ─── Company Timezone Editor ───────────────────────
const COMMON_TIMEZONES = [
    'UTC',
    'Asia/Shanghai',
    'Asia/Tokyo',
    'Asia/Seoul',
    'Asia/Singapore',
    'Asia/Kolkata',
    'Asia/Dubai',
    'Europe/London',
    'Europe/Paris',
    'Europe/Berlin',
    'Europe/Moscow',
    'America/New_York',
    'America/Chicago',
    'America/Denver',
    'America/Los_Angeles',
    'America/Sao_Paulo',
    'Australia/Sydney',
    'Pacific/Auckland',
];

function CompanyTimezoneEditor() {
    const { t } = useTranslation();
    const tenantId = localStorage.getItem('current_tenant_id') || '';
    const [timezone, setTimezone] = useState('UTC');
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        if (!tenantId) return;
        fetchJson<any>(`/tenants/${tenantId}`)
            .then(d => { if (d?.timezone) setTimezone(d.timezone); })
            .catch(() => { });
    }, [tenantId]);

    const handleSave = async (tz: string) => {
        if (!tenantId) return;
        setTimezone(tz);
        setSaving(true);
        try {
            await fetchJson(`/tenants/${tenantId}`, {
                method: 'PUT', body: JSON.stringify({ timezone: tz }),
            });
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        } catch (e) { }
        setSaving(false);
    };

    return (
        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
            <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 500, fontSize: '13px', marginBottom: '4px' }}>🌐 {t('enterprise.timezone.title', 'Company Timezone')}</div>
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                        {t('enterprise.timezone.description', 'Default timezone for all agents. Agents can override individually.')}
                    </div>
                </div>
                <select
                    className="form-input"
                    value={timezone}
                    onChange={e => handleSave(e.target.value)}
                    style={{ width: '220px', fontSize: '13px' }}
                    disabled={saving}
                >
                    {COMMON_TIMEZONES.map(tz => (
                        <option key={tz} value={tz}>{tz}</option>
                    ))}
                </select>
                {saved && <span style={{ color: 'var(--success)', fontSize: '12px' }}>✅</span>}
            </div>
        </div>
    );
}


// ── Broadcast Section ──────────────────────────
function BroadcastSection() {
    const { t } = useTranslation();
    const [title, setTitle] = useState('');
    const [body, setBody] = useState('');
    const [sendEmail, setSendEmail] = useState(false);
    const [sending, setSending] = useState(false);
    const [result, setResult] = useState<{ users: number; agents: number; emails: number } | null>(null);

    const handleSend = async () => {
        if (!title.trim()) return;
        setSending(true);
        setResult(null);
        try {
            const token = localStorage.getItem('token');
            const res = await fetch('/api/notifications/broadcast', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify({ title: title.trim(), body: body.trim(), send_email: sendEmail }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                alert(err.detail || 'Failed to send broadcast');
                setSending(false);
                return;
            }
            const data = await res.json();
            setResult({
                users: data.users_notified,
                agents: data.agents_notified,
                emails: data.emails_sent || 0,
            });
            setTitle('');
            setBody('');
            setSendEmail(false);
        } catch (e: any) {
            alert(e.message || 'Failed');
        }
        setSending(false);
    };

    return (
        <div style={{ marginTop: '24px', marginBottom: '24px' }}>
            <h3 style={{ marginBottom: '4px' }}>{t('enterprise.broadcast.title', 'Broadcast Notification')}</h3>
            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                {t('enterprise.broadcast.description', 'Send a notification to all users and agents in this company.')}
            </p>
            <div className="card" style={{ padding: '16px' }}>
                <input
                    className="form-input"
                    placeholder={t('enterprise.broadcast.titlePlaceholder', 'Notification title')}
                    value={title}
                    onChange={e => setTitle(e.target.value)}
                    maxLength={200}
                    style={{ marginBottom: '8px', fontSize: '13px' }}
                />
                <textarea
                    className="form-input"
                    placeholder={t('enterprise.broadcast.bodyPlaceholder', 'Optional details...')}
                    value={body}
                    onChange={e => setBody(e.target.value)}
                    maxLength={1000}
                    rows={3}
                    style={{ resize: 'vertical', fontSize: '13px', marginBottom: '12px' }}
                />
                <label style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px', fontSize: '13px' }}>
                    <input
                        type="checkbox"
                        checked={sendEmail}
                        onChange={e => setSendEmail(e.target.checked)}
                    />
                    <span>{t('enterprise.broadcast.sendEmail', 'Also send email to users with a configured address')}</span>
                </label>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <button className="btn btn-primary" onClick={handleSend} disabled={sending || !title.trim()}>
                        {sending ? t('common.loading') : t('enterprise.broadcast.send', 'Send Broadcast')}
                    </button>
                    {result && (
                        <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            {t(
                                'enterprise.broadcast.sentWithEmail',
                                `Sent to ${result.users} users, ${result.agents} agents, and ${result.emails} email recipients`,
                                { users: result.users, agents: result.agents, emails: result.emails },
                            )}
                        </span>
                    )}
                </div>
            </div>
        </div>
    );
}


// ─── Identity Providers Tab ──────────────────────────

export default function EnterpriseSettings() {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const [activeTab, setActiveTab] = useState<'llm' | 'org' | 'info' | 'approvals' | 'audit' | 'tools' | 'skills' | 'quotas' | 'users' | 'invites'>('info');

    // Track selected tenant as state so page refreshes on company switch
    const [selectedTenantId, setSelectedTenantId] = useState(localStorage.getItem('current_tenant_id') || '');
    useEffect(() => {
        const handler = (e: StorageEvent) => {
            if (e.key === 'current_tenant_id') {
                setSelectedTenantId(e.newValue || '');
            }
        };
        window.addEventListener('storage', handler);
        return () => window.removeEventListener('storage', handler);
    }, []);

    // Tenant quota defaults
    const [quotaForm, setQuotaForm] = useState({
        default_message_limit: 50, default_message_period: 'permanent',
        default_max_agents: 2, default_agent_ttl_hours: 48,
        default_max_llm_calls_per_day: 100, min_heartbeat_interval_minutes: 120,
        default_max_triggers: 20, min_poll_interval_floor: 5, max_webhook_rate_ceiling: 5,
    });
    const [quotaSaving, setQuotaSaving] = useState(false);
    const [quotaSaved, setQuotaSaved] = useState(false);
    useEffect(() => {
        if (activeTab === 'quotas') {
            fetchJson<any>('/enterprise/tenant-quotas').then(d => {
                if (d && Object.keys(d).length) setQuotaForm(f => ({ ...f, ...d }));
            }).catch(() => { });
        }
    }, [activeTab]);
    const saveQuotas = async () => {
        setQuotaSaving(true);
        try {
            await fetchJson('/enterprise/tenant-quotas', { method: 'PATCH', body: JSON.stringify(quotaForm) });
            setQuotaSaved(true); setTimeout(() => setQuotaSaved(false), 2000);
        } catch (e) { alert('Failed to save'); }
        setQuotaSaving(false);
    };
    const [companyIntro, setCompanyIntro] = useState('');
    const [companyIntroSaving, setCompanyIntroSaving] = useState(false);
    const [companyIntroSaved, setCompanyIntroSaved] = useState(false);


    // Company intro key: always per-tenant scoped
    const companyIntroKey = selectedTenantId ? `company_intro_${selectedTenantId}` : 'company_intro';

    // Load Company Intro (tenant-scoped only, no fallback to global)
    useEffect(() => {
        setCompanyIntro('');
        if (!selectedTenantId) return;
        const tenantKey = `company_intro_${selectedTenantId}`;
        fetchJson<any>(`/enterprise/system-settings/${tenantKey}`)
            .then(d => {
                if (d?.value?.content) {
                    setCompanyIntro(d.value.content);
                }
                // No fallback — each company starts empty with placeholder watermark
            })
            .catch(() => { });
    }, [selectedTenantId]);

    const saveCompanyIntro = async () => {
        setCompanyIntroSaving(true);
        try {
            await fetchJson(`/enterprise/system-settings/${companyIntroKey}`, {
                method: 'PUT', body: JSON.stringify({ value: { content: companyIntro } }),
            });
            setCompanyIntroSaved(true);
            setTimeout(() => setCompanyIntroSaved(false), 2000);
        } catch (e) { }
        setCompanyIntroSaving(false);
    };
    const [auditFilter, setAuditFilter] = useState<'all' | 'background' | 'actions'>('all');
    const [infoRefresh, setInfoRefresh] = useState(0);
    const [kbPromptModal, setKbPromptModal] = useState(false);
    const [kbToast, setKbToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const showKbToast = (message: string, type: 'success' | 'error' = 'success') => {
        setKbToast({ message, type });
        setTimeout(() => setKbToast(null), 3000);
    };

    const [allTools, setAllTools] = useState<any[]>([]);
    const [showAddMCP, setShowAddMCP] = useState(false);
    const [mcpForm, setMcpForm] = useState({ server_url: '', server_name: '', api_key: '' });
    const [mcpRawInput, setMcpRawInput] = useState('');
    const [mcpTestResult, setMcpTestResult] = useState<any>(null);
    const [mcpTesting, setMcpTesting] = useState(false);
    // Edit Server modal state — null when closed, otherwise the server to edit
    const [editingMcpServer, setEditingMcpServer] = useState<{
        server_name: string;
        server_url: string;
        api_key: string;
    } | null>(null);
    const [mcpServerSaving, setMcpServerSaving] = useState(false);
    const [editingToolId, setEditingToolId] = useState<string | null>(null);
    const [editingConfig, setEditingConfig] = useState<Record<string, any>>({});

    const [configCategory, setConfigCategory] = useState<string | null>(null);

    // Category-level config schemas: tools sharing the same key have config on category header
    const GLOBAL_CATEGORY_CONFIG_SCHEMAS: Record<string, { title: string; fields: any[] }> = {
        agentbay: {
            title: 'AgentBay Settings',
            fields: [
                { key: 'api_key', label: 'API Key (from AgentBay)', type: 'password', placeholder: 'Enter your AgentBay API key' },
                { key: 'os_type', label: 'Cloud Computer OS', type: 'select', default: 'windows', options: [{ value: 'linux', label: 'Linux' }, { value: 'windows', label: 'Windows' }] },
            ],
        },
    };

    // Labels for tool categories (mirrors AgentDetail getCategoryLabels)
    const categoryLabels: Record<string, string> = {
        file: t('agent.toolCategories.file'),
        task: t('agent.toolCategories.task'),
        communication: t('agent.toolCategories.communication'),
        search: t('agent.toolCategories.search'),
        aware: t('agent.toolCategories.aware', 'Aware & Triggers'),
        social: t('agent.toolCategories.social', 'Social'),
        code: t('agent.toolCategories.code', 'Code & Execution'),
        discovery: t('agent.toolCategories.discovery', 'Discovery'),
        email: t('agent.toolCategories.email', 'Email'),
        feishu: t('agent.toolCategories.feishu', 'Feishu / Lark'),
        custom: t('agent.toolCategories.custom'),
        general: t('agent.toolCategories.general'),
        agentbay: t('agent.toolCategories.agentbay', 'AgentBay'),
    };
    const [toolsView, setToolsView] = useState<'global' | 'agent-installed'>('global');
    const [agentInstalledTools, setAgentInstalledTools] = useState<any[]>([]);
    const loadAllTools = async () => {
        const tid = selectedTenantId;
        const data = await fetchJson<any[]>(`/tools${tid ? `?tenant_id=${tid}` : ''}`);
        setAllTools(data);
    };
    const loadAgentInstalledTools = async () => {
        try {
            const tid = selectedTenantId;
            const data = await fetchJson<any[]>(`/tools/agent-installed${tid ? `?tenant_id=${tid}` : ''}`);
            setAgentInstalledTools(data);
        } catch { }
    };
    useEffect(() => { if (activeTab === 'tools') { loadAllTools(); loadAgentInstalledTools(); } }, [activeTab, selectedTenantId]);

    // ─── Jina API Key
    const [jinaKey, setJinaKey] = useState('');
    const [jinaKeySaved, setJinaKeySaved] = useState(false);
    const [jinaKeySaving, setJinaKeySaving] = useState(false);
    const [jinaKeyMasked, setJinaKeyMasked] = useState('');  // stored key from DB
    useEffect(() => {
        if (activeTab !== 'tools') return;
        const token = localStorage.getItem('token');
        fetch('/api/enterprise/system-settings/jina_api_key', { headers: { Authorization: `Bearer ${token}` } })
            .then(r => r.json())
            .then(d => { if (d.value?.api_key) setJinaKeyMasked(d.value.api_key.slice(0, 8) + '••••••••'); })
            .catch(() => { });
    }, [activeTab]);
    const saveJinaKey = async () => {
        setJinaKeySaving(true);
        const token = localStorage.getItem('token');
        await fetch('/api/enterprise/system-settings/jina_api_key', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
            body: JSON.stringify({ value: { api_key: jinaKey } }),
        });
        setJinaKeyMasked(jinaKey.slice(0, 8) + '••••••••');
        setJinaKey('');
        setJinaKeySaving(false);
        setJinaKeySaved(true);
        setTimeout(() => setJinaKeySaved(false), 2000);
    };
    const clearJinaKey = async () => {
        const token = localStorage.getItem('token');
        await fetch('/api/enterprise/system-settings/jina_api_key', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
            body: JSON.stringify({ value: {} }),
        });
        setJinaKeyMasked('');
        setJinaKey('');
    };


    const { data: currentTenant } = useQuery({
        queryKey: ['tenant', selectedTenantId],
        queryFn: () => fetchJson<any>(`/tenants/${selectedTenantId}`),
        enabled: !!selectedTenantId,
    });

    // ─── Stats (scoped to selected tenant)
    const { data: stats } = useQuery({
        queryKey: ['enterprise-stats', selectedTenantId],
        queryFn: () => fetchJson<any>(`/enterprise/stats${selectedTenantId ? `?tenant_id=${selectedTenantId}` : ''}`),
    });

    // ─── LLM Models
    const { data: models = [] } = useQuery({
        queryKey: ['llm-models', selectedTenantId],
        queryFn: () => fetchJson<LLMModel[]>(`/enterprise/llm-models${selectedTenantId ? `?tenant_id=${selectedTenantId}` : ''}`),
        enabled: activeTab === 'llm',
    });
    const [showAddModel, setShowAddModel] = useState(false);
    const [editingModelId, setEditingModelId] = useState<string | null>(null);
    const [modelForm, setModelForm] = useState({ provider: 'anthropic', model: '', api_key: '', base_url: '', label: '', supports_vision: false, max_output_tokens: '' as string, request_timeout: '' as string, temperature: '' as string });
    const { data: providerSpecs = [] } = useQuery({
        queryKey: ['llm-provider-specs'],
        queryFn: () => fetchJson<LLMProviderSpec[]>('/enterprise/llm-providers'),
        enabled: activeTab === 'llm',
    });
    const providerOptions = providerSpecs.length > 0 ? providerSpecs : FALLBACK_LLM_PROVIDERS;
    const addModel = useMutation({
        mutationFn: (data: any) => fetchJson(`/enterprise/llm-models${selectedTenantId ? `?tenant_id=${selectedTenantId}` : ''}`, { method: 'POST', body: JSON.stringify(data) }),
        onSuccess: () => { qc.invalidateQueries({ queryKey: ['llm-models', selectedTenantId] }); setShowAddModel(false); setEditingModelId(null); },
    });
    const updateModel = useMutation({
        mutationFn: ({ id, data }: { id: string; data: any }) => fetchJson(`/enterprise/llm-models/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
        onSuccess: () => { qc.invalidateQueries({ queryKey: ['llm-models', selectedTenantId] }); setShowAddModel(false); setEditingModelId(null); },
    });
    const deleteModel = useMutation({
        mutationFn: async ({ id, force = false }: { id: string; force?: boolean }) => {
            const url = force ? `/enterprise/llm-models/${id}?force=true` : `/enterprise/llm-models/${id}`;
            const res = await fetch(`/api${url}`, {
                method: 'DELETE',
                headers: { Authorization: `Bearer ${localStorage.getItem('token')}` },
            });
            if (res.status === 409) {
                const data = await res.json();
                const agents = data.detail?.agents || [];
                const msg = `This model is used by ${agents.length} agent(s):\n\n${agents.join(', ')}\n\nDelete anyway? (their model config will be cleared)`;
                if (confirm(msg)) {
                    // Retry with force
                    const r2 = await fetch(`/api/enterprise/llm-models/${id}?force=true`, {
                        method: 'DELETE',
                        headers: { Authorization: `Bearer ${localStorage.getItem('token')}` },
                    });
                    if (!r2.ok && r2.status !== 204) throw new Error('Delete failed');
                }
                return;
            }
            if (!res.ok && res.status !== 204) throw new Error('Delete failed');
        },
        onSuccess: () => qc.invalidateQueries({ queryKey: ['llm-models', selectedTenantId] }),
    });

    // ─── Approvals
    const { data: approvals = [] } = useQuery({
        queryKey: ['approvals', selectedTenantId],
        queryFn: () => fetchJson<any[]>(`/enterprise/approvals${selectedTenantId ? `?tenant_id=${selectedTenantId}` : ''}`),
        enabled: activeTab === 'approvals',
    });
    const resolveApproval = useMutation({
        mutationFn: ({ id, action }: { id: string; action: string }) =>
            fetchJson(`/enterprise/approvals/${id}/resolve`, { method: 'POST', body: JSON.stringify({ action }) }),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals', selectedTenantId] }),
    });

    // ─── Audit Logs
    const BG_ACTIONS = ['supervision_tick', 'supervision_fire', 'supervision_error', 'schedule_tick', 'schedule_fire', 'schedule_error', 'heartbeat_tick', 'heartbeat_fire', 'heartbeat_error', 'server_startup'];
    const { data: auditLogs = [] } = useQuery({
        queryKey: ['audit-logs', selectedTenantId],
        queryFn: () => fetchJson<any[]>(`/enterprise/audit-logs?limit=200${selectedTenantId ? `&tenant_id=${selectedTenantId}` : ''}`),
        enabled: activeTab === 'audit',
    });
    const filteredAuditLogs = auditLogs.filter((log: any) => {
        if (auditFilter === 'background') return BG_ACTIONS.includes(log.action);
        if (auditFilter === 'actions') return !BG_ACTIONS.includes(log.action);
        return true;
    });

    return (
        <>
            <div>
                <div className="page-header">
                    <div>
                        <h1 className="page-title">{t('nav.enterprise')}</h1>
                        {stats && (
                            <div style={{ display: 'flex', gap: '24px', marginTop: '8px' }}>
                                <span className="badge badge-info">{t('enterprise.stats.users', { count: stats.total_users })}</span>
                                <span className="badge badge-success">{t('enterprise.stats.runningAgents', { running: stats.running_agents, total: stats.total_agents })}</span>
                                {stats.pending_approvals > 0 && <span className="badge badge-warning">{stats.pending_approvals} {t('enterprise.tabs.approvals')}</span>}
                            </div>
                        )}
                    </div>
                </div>

                <div className="tabs">
                    {(['info', 'llm', 'tools', 'skills', 'invites', 'quotas', 'users', 'org', 'approvals', 'audit'] as const).map(tab => (
                        <div key={tab} className={`tab ${activeTab === tab ? 'active' : ''}`} onClick={() => setActiveTab(tab)}>
                            {tab === 'quotas' ? t('enterprise.tabs.quotas', 'Quotas') : tab === 'users' ? t('enterprise.tabs.users', 'Users') : tab === 'invites' ? t('enterprise.tabs.invites', 'Invitations') : t(`enterprise.tabs.${tab}`)}
                        </div>
                    ))}
                </div>

                {/* ── LLM Model Pool ── */}
                {activeTab === 'llm' && (
                    <div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '16px' }}>
                            <button className="btn btn-primary" onClick={() => {
                                setEditingModelId(null);
                                const defaultSpec = providerOptions[0];
                                setModelForm({
                                    provider: defaultSpec?.provider || 'anthropic',
                                    model: '', api_key: '',
                                    base_url: defaultSpec?.default_base_url || '',
                                    label: '', supports_vision: false,
                                    max_output_tokens: defaultSpec ? String(defaultSpec.default_max_tokens) : '4096',
                                    request_timeout: '',
                                    temperature: '',
                                });
                                setShowAddModel(true);
                            }}>+ {t('enterprise.llm.addModel')}</button>
                        </div>

                        {/* Add Model form — only shown at top when adding new */}
                        {showAddModel && !editingModelId && (
                            <div className="card" style={{ marginBottom: '16px' }}>
                                <h3 style={{ marginBottom: '16px' }}>{t('enterprise.llm.addModel')}</h3>
                                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                                    <div className="form-group">
                                        <label className="form-label">{t('enterprise.llm.provider')}</label>
                                        <select className="form-input" value={modelForm.provider} onChange={e => {
                                            const newProvider = e.target.value;
                                            const spec = providerOptions.find(p => p.provider === newProvider);
                                            const updates: any = { provider: newProvider };
                                            if (spec?.default_base_url) {
                                                updates.base_url = spec.default_base_url;
                                            } else {
                                                updates.base_url = '';
                                            }
                                            if (spec) {
                                                updates.max_output_tokens = String(spec.default_max_tokens);
                                            }
                                            setModelForm(f => ({ ...f, ...updates }));
                                        }}>
                                            {providerOptions.map((p) => (
                                                <option key={p.provider} value={p.provider}>{p.display_name}</option>
                                            ))}
                                        </select>
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">{t('enterprise.llm.model')}</label>
                                        <input
                                            className="form-input"
                                            placeholder={t('enterprise.llm.modelPlaceholder', 'e.g. claude-sonnet-4-20250514')}
                                            value={modelForm.model}
                                            onChange={e => setModelForm({ ...modelForm, model: e.target.value })}
                                        />
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">{t('enterprise.llm.label')}</label>
                                        <input className="form-input" placeholder={t('enterprise.llm.labelPlaceholder')} value={modelForm.label} onChange={e => setModelForm({ ...modelForm, label: e.target.value })} />
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">{t('enterprise.llm.baseUrl')}</label>
                                        <input className="form-input" placeholder={t('enterprise.llm.baseUrlPlaceholder')} value={modelForm.base_url} onChange={e => setModelForm({ ...modelForm, base_url: e.target.value })} />
                                    </div>
                                    <div className="form-group" style={{ gridColumn: 'span 2' }}>
                                        <label className="form-label">{t('enterprise.llm.apiKey')}</label>
                                        <input className="form-input" type="password" placeholder={t('enterprise.llm.apiKeyPlaceholder')} value={modelForm.api_key} onChange={e => setModelForm({ ...modelForm, api_key: e.target.value })} />
                                    </div>
                                    <div className="form-group" style={{ gridColumn: 'span 2' }}>
                                        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '13px' }}>
                                            <input type="checkbox" checked={modelForm.supports_vision} onChange={e => setModelForm({ ...modelForm, supports_vision: e.target.checked })} />
                                            {t('enterprise.llm.supportsVision')}
                                            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontWeight: 400 }}>{t('enterprise.llm.supportsVisionDesc')}</span>
                                        </label>
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">{t('enterprise.llm.maxOutputTokens', 'Max Output Tokens')}</label>
                                        <input className="form-input" type="number" placeholder={t('enterprise.llm.maxOutputTokensPlaceholder', 'e.g. 4096')} value={modelForm.max_output_tokens} onChange={e => setModelForm({ ...modelForm, max_output_tokens: e.target.value })} />
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('enterprise.llm.maxOutputTokensDesc', 'Limits generation length')}</div>
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">{t('enterprise.llm.requestTimeout', 'Request Timeout (s)')}</label>
                                        <input className="form-input" type="number" min="1" placeholder={t('enterprise.llm.requestTimeoutPlaceholder', 'e.g. 120 (Leave empty for default)')} value={modelForm.request_timeout} onChange={e => setModelForm({ ...modelForm, request_timeout: e.target.value })} />
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('enterprise.llm.requestTimeoutDesc', 'Increase for slow local models.')}</div>
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">{t('enterprise.llm.temperature', 'Temperature')}</label>
                                        <input className="form-input" type="number" step="0.1" min="0" max="2" placeholder={t('enterprise.llm.temperaturePlaceholder', 'e.g. 0.7 or 1.0 (Leave empty for default)')} value={modelForm.temperature} onChange={e => setModelForm({ ...modelForm, temperature: e.target.value })} />
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('enterprise.llm.temperatureDesc', 'Leave empty to use the provider default. o1/o3 reasoning models usually require 1.0')}</div>
                                    </div>
                                </div>
                                <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', alignItems: 'center' }}>
                                    <button className="btn btn-secondary" onClick={() => { setShowAddModel(false); setEditingModelId(null); }}>{t('common.cancel')}</button>
                                    <button className="btn btn-secondary" style={{ display: 'flex', alignItems: 'center', gap: '6px' }} disabled={!modelForm.model || !modelForm.api_key} onClick={async () => {
                                        const btn = document.activeElement as HTMLButtonElement;
                                        const origText = btn?.textContent || '';
                                        if (btn) btn.textContent = t('enterprise.llm.testing');
                                        try {
                                            const token = localStorage.getItem('token');
                                            const testData: any = { provider: modelForm.provider, model: modelForm.model, base_url: modelForm.base_url || undefined };
                                            if (modelForm.api_key) testData.api_key = modelForm.api_key;
                                            const res = await fetch('/api/enterprise/llm-test', {
                                                method: 'POST',
                                                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                                                body: JSON.stringify(testData),
                                            });
                                            const result = await res.json();
                                            if (result.success) {
                                                if (btn) { btn.textContent = t('enterprise.llm.testSuccess', { latency: result.latency_ms }); btn.style.color = 'var(--success)'; }
                                                setTimeout(() => { if (btn) { btn.textContent = origText; btn.style.color = ''; } }, 3000);
                                            } else {
                                                alert(t('enterprise.llm.testFailed', { error: result.error || 'Unknown error', latency: result.latency_ms }));
                                                if (btn) btn.textContent = origText;
                                            }
                                        } catch (e: any) {
                                            alert(t('enterprise.llm.testError', { message: e.message }));
                                            if (btn) btn.textContent = origText;
                                        }
                                    }}>{t('enterprise.llm.test')}</button>
                                    <button className="btn btn-primary" onClick={() => {
                                        const data = {
                                            ...modelForm,
                                            max_output_tokens: modelForm.max_output_tokens ? Number(modelForm.max_output_tokens) : null,
                                            request_timeout: modelForm.request_timeout ? Number(modelForm.request_timeout) : null,
                                            temperature: modelForm.temperature !== '' ? Number(modelForm.temperature) : null
                                        };
                                        addModel.mutate(data);
                                    }} disabled={!modelForm.model || !modelForm.api_key}>
                                        {t('common.save')}
                                    </button>
                                </div>
                            </div>
                        )}

                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                            {models.map((m) => (
                                <div key={m.id}>
                                    {editingModelId === m.id ? (
                                        /* Inline edit form */
                                        <div className="card" style={{ border: '1px solid var(--accent-primary)' }}>
                                            <h3 style={{ marginBottom: '16px' }}>Edit Model</h3>
                                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                                                <div className="form-group">
                                                    <label className="form-label">{t('enterprise.llm.provider')}</label>
                                                    <select className="form-input" value={modelForm.provider} onChange={e => {
                                                        const newProvider = e.target.value;
                                                        setModelForm(f => ({ ...f, provider: newProvider }));
                                                    }}>
                                                        {providerOptions.map((p) => (
                                                            <option key={p.provider} value={p.provider}>{p.display_name}</option>
                                                        ))}
                                                        {!providerOptions.some((p) => p.provider === modelForm.provider) && (
                                                            <option value={modelForm.provider}>{modelForm.provider}</option>
                                                        )}
                                                    </select>
                                                </div>
                                                <div className="form-group">
                                                    <label className="form-label">{t('enterprise.llm.model')}</label>
                                                    <input
                                                        className="form-input"
                                                        placeholder={t('enterprise.llm.modelPlaceholder', 'e.g. claude-sonnet-4-20250514')}
                                                        value={modelForm.model}
                                                        onChange={e => setModelForm({ ...modelForm, model: e.target.value })}
                                                    />
                                                </div>
                                                <div className="form-group">
                                                    <label className="form-label">{t('enterprise.llm.label')}</label>
                                                    <input className="form-input" placeholder={t('enterprise.llm.labelPlaceholder')} value={modelForm.label} onChange={e => setModelForm({ ...modelForm, label: e.target.value })} />
                                                </div>
                                                <div className="form-group">
                                                    <label className="form-label">{t('enterprise.llm.baseUrl')}</label>
                                                    <input className="form-input" placeholder={t('enterprise.llm.baseUrlPlaceholder')} value={modelForm.base_url} onChange={e => setModelForm({ ...modelForm, base_url: e.target.value })} />
                                                </div>
                                                <div className="form-group" style={{ gridColumn: 'span 2' }}>
                                                    <label className="form-label">{t('enterprise.llm.apiKey')}</label>
                                                    <input className="form-input" type="password" placeholder="•••••••• (Leave blank to keep unchanged)" value={modelForm.api_key} onChange={e => setModelForm({ ...modelForm, api_key: e.target.value })} />
                                                </div>
                                                <div className="form-group" style={{ gridColumn: 'span 2' }}>
                                                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '13px' }}>
                                                        <input type="checkbox" checked={modelForm.supports_vision} onChange={e => setModelForm({ ...modelForm, supports_vision: e.target.checked })} />
                                                        {t('enterprise.llm.supportsVision')}
                                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontWeight: 400 }}>{t('enterprise.llm.supportsVisionDesc')}</span>
                                                    </label>
                                                </div>
                                                <div className="form-group">
                                                    <label className="form-label">{t('enterprise.llm.maxOutputTokens', 'Max Output Tokens')}</label>
                                                    <input className="form-input" type="number" placeholder={t('enterprise.llm.maxOutputTokensPlaceholder', 'e.g. 4096')} value={modelForm.max_output_tokens} onChange={e => setModelForm({ ...modelForm, max_output_tokens: e.target.value })} />
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('enterprise.llm.maxOutputTokensDesc', 'Limits generation length')}</div>
                                                </div>
                                                <div className="form-group">
                                                    <label className="form-label">{t('enterprise.llm.requestTimeout', 'Request Timeout (s)')}</label>
                                                    <input className="form-input" type="number" min="1" placeholder={t('enterprise.llm.requestTimeoutPlaceholder', 'e.g. 120 (Leave empty for default)')} value={modelForm.request_timeout} onChange={e => setModelForm({ ...modelForm, request_timeout: e.target.value })} />
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('enterprise.llm.requestTimeoutDesc', 'Increase for slow local models.')}</div>
                                                </div>
                                                <div className="form-group">
                                                    <label className="form-label">{t('enterprise.llm.temperature', 'Temperature')}</label>
                                                    <input className="form-input" type="number" step="0.1" min="0" max="2" placeholder={t('enterprise.llm.temperaturePlaceholder', 'e.g. 0.7 or 1.0 (Leave empty for default)')} value={modelForm.temperature} onChange={e => setModelForm({ ...modelForm, temperature: e.target.value })} />
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('enterprise.llm.temperatureDesc', 'Leave empty to use the provider default. o1/o3 reasoning models usually require 1.0')}</div>
                                                </div>
                                            </div>
                                            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', alignItems: 'center' }}>
                                                <button className="btn btn-secondary" onClick={() => { setShowAddModel(false); setEditingModelId(null); }}>{t('common.cancel')}</button>
                                                <button className="btn btn-secondary" style={{ display: 'flex', alignItems: 'center', gap: '6px' }} disabled={!modelForm.model} onClick={async () => {
                                                    const btn = document.activeElement as HTMLButtonElement;
                                                    const origText = btn?.textContent || '';
                                                    if (btn) btn.textContent = t('enterprise.llm.testing');
                                                    try {
                                                        const token = localStorage.getItem('token');
                                                        const testData: any = { provider: modelForm.provider, model: modelForm.model, base_url: modelForm.base_url || undefined };
                                                        if (modelForm.api_key) testData.api_key = modelForm.api_key;
                                                        testData.model_id = editingModelId;
                                                        const res = await fetch('/api/enterprise/llm-test', {
                                                            method: 'POST',
                                                            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                                                            body: JSON.stringify(testData),
                                                        });
                                                        const result = await res.json();
                                                        if (result.success) {
                                                            if (btn) { btn.textContent = t('enterprise.llm.testSuccess', { latency: result.latency_ms }); btn.style.color = 'var(--success)'; }
                                                            setTimeout(() => { if (btn) { btn.textContent = origText; btn.style.color = ''; } }, 3000);
                                                        } else {
                                                            alert(t('enterprise.llm.testFailed', { error: result.error || 'Unknown error', latency: result.latency_ms }));
                                                            if (btn) btn.textContent = origText;
                                                        }
                                                    } catch (e: any) {
                                                        alert(t('enterprise.llm.testError', { message: e.message }));
                                                        if (btn) btn.textContent = origText;
                                                    }
                                                }}>{t('enterprise.llm.test')}</button>
                                                <button className="btn btn-primary" onClick={() => {
                                                    const data = {
                                                        ...modelForm,
                                                        max_output_tokens: modelForm.max_output_tokens ? Number(modelForm.max_output_tokens) : null,
                                                        request_timeout: modelForm.request_timeout ? Number(modelForm.request_timeout) : null,
                                                        temperature: modelForm.temperature !== '' ? Number(modelForm.temperature) : null
                                                    };
                                                    updateModel.mutate({ id: editingModelId!, data });
                                                }} disabled={!modelForm.model}>
                                                    {t('common.save')}
                                                </button>
                                            </div>
                                        </div>
                                    ) : (
                                        /* Normal model row */
                                        <div className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                            <div>
                                                <div style={{ fontWeight: 500 }}>{m.label}</div>
                                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                                                    {m.provider}/{m.model}
                                                    {m.base_url && <span> · {m.base_url}</span>}
                                                </div>
                                            </div>
                                            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                                {/* Toggle switch for enabled/disabled */}
                                                <button
                                                    onClick={async () => {
                                                        try {
                                                            const token = localStorage.getItem('token');
                                                            await fetch(`/api/enterprise/llm-models/${m.id}`, {
                                                                method: 'PUT',
                                                                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                                                                body: JSON.stringify({ enabled: !m.enabled }),
                                                            });
                                                            qc.invalidateQueries({ queryKey: ['llm-models', selectedTenantId] });
                                                        } catch (e) { console.error(e); }
                                                    }}
                                                    title={m.enabled ? t('enterprise.llm.clickToDisable', 'Click to disable') : t('enterprise.llm.clickToEnable', 'Click to enable')}
                                                    style={{
                                                        position: 'relative', width: '36px', height: '20px', borderRadius: '10px', border: 'none', cursor: 'pointer', transition: 'background 0.2s',
                                                        background: m.enabled ? 'var(--accent-primary)' : 'var(--bg-tertiary, #444)',
                                                        padding: 0, flexShrink: 0,
                                                    }}
                                                >
                                                    <span style={{
                                                        position: 'absolute', left: m.enabled ? '18px' : '2px', top: '2px',
                                                        width: '16px', height: '16px', borderRadius: '50%', background: '#fff',
                                                        transition: 'left 0.2s', boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                                                    }} />
                                                </button>
                                                {m.supports_vision && <span className="badge" style={{ background: 'rgba(99,102,241,0.15)', color: 'rgb(99,102,241)', fontSize: '10px' }}>Vision</span>}
                                                <button className="btn btn-ghost" onClick={() => {
                                                    setEditingModelId(m.id);
                                                    setModelForm({ provider: m.provider, model: m.model, label: m.label, base_url: m.base_url || '', api_key: m.api_key_masked || '', supports_vision: m.supports_vision || false, max_output_tokens: m.max_output_tokens ? String(m.max_output_tokens) : '', request_timeout: m.request_timeout ? String(m.request_timeout) : '', temperature: m.temperature !== null && m.temperature !== undefined ? String(m.temperature) : '' });
                                                    setShowAddModel(true);
                                                }} style={{ fontSize: '12px' }}>✏️ {t('enterprise.tools.edit')}</button>
                                                <button className="btn btn-ghost" onClick={() => deleteModel.mutate({ id: m.id })} style={{ color: 'var(--error)' }}>{t('common.delete')}</button>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            ))}
                            {models.length === 0 && <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>{t('common.noData')}</div>}
                        </div>
                    </div>
                )}

                {/* ── Org Structure ── */}
                {activeTab === 'org' && <OrgTab tenant={currentTenant} />}

                {/* ── Approvals ── */}
                {activeTab === 'approvals' && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {approvals.map((a: any) => (
                            <div key={a.id} className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                <div>
                                    <div style={{ fontWeight: 500 }}>{a.action_type}</div>
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                                        {a.agent_name || `Agent ${a.agent_id.slice(0, 8)}`} · {new Date(a.created_at).toLocaleString()}
                                    </div>
                                </div>
                                {a.status === 'pending' ? (
                                    <div style={{ display: 'flex', gap: '8px' }}>
                                        <button className="btn btn-primary" onClick={() => resolveApproval.mutate({ id: a.id, action: 'approve' })}>{t('common.confirm')}</button>
                                        <button className="btn btn-danger" onClick={() => resolveApproval.mutate({ id: a.id, action: 'reject' })}>Reject</button>
                                    </div>
                                ) : (
                                    <span className={`badge ${a.status === 'approved' ? 'badge-success' : 'badge-error'}`}>
                                        {a.status === 'approved' ? 'Approved' : 'Rejected'}
                                    </span>
                                )}
                            </div>
                        ))}
                        {approvals.length === 0 && <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>{t('common.noData')}</div>}
                    </div>
                )}

                {/* ── Audit Logs ── */}
                {activeTab === 'audit' && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                        {/* Sub-filter pills */}
                        <div style={{ display: 'flex', gap: '8px', padding: '8px 12px', borderBottom: '1px solid var(--border-color)' }}>
                            {([
                                ['all', t('enterprise.audit.filterAll')],
                                ['background', t('enterprise.audit.filterBackground')],
                                ['actions', t('enterprise.audit.filterActions')],
                            ] as const).map(([key, label]) => (
                                <button key={key}
                                    onClick={() => setAuditFilter(key as any)}
                                    style={{
                                        padding: '4px 14px', borderRadius: '12px', fontSize: '12px', fontWeight: 500,
                                        border: auditFilter === key ? '1px solid var(--accent-primary)' : '1px solid var(--border-subtle)',
                                        background: auditFilter === key ? 'var(--accent-primary)' : 'transparent',
                                        color: auditFilter === key ? '#fff' : 'var(--text-secondary)',
                                        cursor: 'pointer', transition: 'all 0.15s',
                                    }}
                                >{label}</button>
                            ))}
                            <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--text-tertiary)', alignSelf: 'center' }}>
                                {t('enterprise.audit.records', { count: filteredAuditLogs.length })}
                            </span>
                        </div>
                        {/* Log entries */}
                        {filteredAuditLogs.map((log: any) => {
                            const isBg = BG_ACTIONS.includes(log.action);
                            const details = log.details && typeof log.details === 'object' && Object.keys(log.details).length > 0 ? log.details : null;
                            return (
                                <div key={log.id} style={{ borderBottom: '1px solid var(--border-subtle)', padding: '6px 12px' }}>
                                    <div style={{ display: 'flex', gap: '12px', fontSize: '13px', alignItems: 'center' }}>
                                        <span style={{ color: 'var(--text-tertiary)', whiteSpace: 'nowrap', fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
                                            {new Date(log.created_at).toLocaleString()}
                                        </span>
                                        <span style={{
                                            padding: '1px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 500,
                                            background: isBg ? 'rgba(99,102,241,0.12)' : 'rgba(34,197,94,0.12)',
                                            color: isBg ? 'var(--accent-color)' : 'rgb(34,197,94)',
                                        }}>{isBg ? '⚙️' : '👤'}</span>
                                        <span style={{ flex: 1, fontWeight: 500 }}>{log.action}</span>
                                        <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>{log.agent_id?.slice(0, 8) || '-'}</span>
                                    </div>
                                    {details && (
                                        <div style={{ marginLeft: '100px', marginTop: '2px', fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                                            {Object.entries(details).map(([k, v]) => (
                                                <span key={k} style={{ marginRight: '12px' }}>{k}={typeof v === 'string' ? v : JSON.stringify(v)}</span>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                        {filteredAuditLogs.length === 0 && <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>{t('common.noData')}</div>}
                    </div>
                )}

                {/* ── Company Management ── */}
                {activeTab === 'info' && (
                    <div>

                        {/* ── 0. Company Name ── */}
                        <h3 style={{ marginBottom: '8px' }}>{t('enterprise.companyName.title', 'Company Name')}</h3>
                        <CompanyNameEditor key={`name-${selectedTenantId}`} />

                        {/* ── 0.5. Company Timezone ── */}
                        <CompanyTimezoneEditor key={`tz-${selectedTenantId}`} />

                        {/* ── 2. Company Intro ── */}
                        <h3 style={{ marginBottom: '8px' }}>{t('enterprise.companyIntro.title', 'Company Intro')}</h3>
                        <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                            {t('enterprise.companyIntro.description', 'Describe your company\'s mission, products, and culture. This information is included in every agent conversation as context.')}
                        </p>
                        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
                            <textarea
                                className="form-input"
                                value={companyIntro}
                                onChange={e => setCompanyIntro(e.target.value)}
                                placeholder={`# Company Name\nClawith\n\n# About\nOpenClaw\uD83E\uDD9E For Teams\nOpen Source \u00B7 Multi-OpenClaw Collaboration\n\nOpenClaw empowers individuals.\nClawith scales it to frontier organizations.`}
                                style={{
                                    minHeight: '200px', resize: 'vertical',
                                    fontFamily: 'var(--font-mono)', fontSize: '13px',
                                    lineHeight: '1.6', whiteSpace: 'pre-wrap',
                                }}
                            />
                            <div style={{ marginTop: '12px', display: 'flex', gap: '8px', alignItems: 'center' }}>
                                <button className="btn btn-primary" onClick={saveCompanyIntro} disabled={companyIntroSaving}>
                                    {companyIntroSaving ? t('common.loading') : t('common.save', 'Save')}
                                </button>
                                {companyIntroSaved && <span style={{ color: 'var(--success)', fontSize: '12px' }}>✅ {t('enterprise.config.saved', 'Saved')}</span>}
                                <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
                                    💡 {t('enterprise.companyIntro.hint', 'This content appears in every agent\'s system prompt')}
                                </span>
                            </div>
                        </div>

                        {/* ── 2. Company Knowledge Base ── */}
                        <h3 style={{ marginBottom: '8px' }}>{t('enterprise.kb.title')}</h3>
                        <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                            {t('enterprise.kb.description', 'Shared files accessible to all agents via enterprise_info/ directory.')}
                        </p>
                        <div className="card" style={{ marginBottom: '24px', padding: '16px' }}>
                            <EnterpriseKBBrowser onRefresh={() => setInfoRefresh((v: number) => v + 1)} refreshKey={infoRefresh} />
                        </div>



                        {/* ── Theme Color ── */}
                        <ThemeColorPicker />

                        {/* ── Broadcast ── */}
                        <BroadcastSection />

                        {/* ── Danger Zone: Delete Company ── */}
                        <div style={{ marginTop: '32px', padding: '16px', border: '1px solid var(--status-error, #e53e3e)', borderRadius: '8px' }}>
                            <h3 style={{ marginBottom: '4px', color: 'var(--status-error, #e53e3e)' }}>{t('enterprise.dangerZone', 'Danger Zone')}</h3>
                            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                {t('enterprise.deleteCompanyDesc', 'Permanently delete this company and all its data including agents, models, tools, and skills. This action cannot be undone.')}
                            </p>
                            <button
                                className="btn"
                                onClick={async () => {
                                    const name = document.querySelector<HTMLInputElement>('.company-name-input')?.value || selectedTenantId;
                                    if (!confirm(t('enterprise.deleteCompanyConfirm', 'Are you sure you want to delete this company and ALL its data? This cannot be undone.'))) return;
                                    try {
                                        const res = await fetchJson<any>(`/tenants/${selectedTenantId}`, { method: 'DELETE' });
                                        // Switch to fallback tenant
                                        const fallbackId = res.fallback_tenant_id;
                                        localStorage.setItem('current_tenant_id', fallbackId);
                                        setSelectedTenantId(fallbackId);
                                        window.dispatchEvent(new StorageEvent('storage', { key: 'current_tenant_id', newValue: fallbackId }));
                                        qc.invalidateQueries({ queryKey: ['tenants'] });
                                    } catch (e: any) {
                                        alert(e.message || 'Delete failed');
                                    }
                                }}
                                style={{
                                    background: 'transparent', color: 'var(--status-error, #e53e3e)',
                                    border: '1px solid var(--status-error, #e53e3e)', borderRadius: '6px',
                                    padding: '6px 16px', fontSize: '13px', cursor: 'pointer',
                                }}
                            >
                                {t('enterprise.deleteCompany', 'Delete This Company')}
                            </button>
                        </div>
                    </div>
                )}

                {/* ── Quotas Tab ── */}
                {activeTab === 'quotas' && (
                    <div>
                        <h3 style={{ marginBottom: '4px' }}>{t('enterprise.quotas.defaultUserQuotas')}</h3>
                        <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                            {t('enterprise.quotas.defaultsApply')}
                        </p>
                        <div className="card" style={{ padding: '16px' }}>
                            {/* ── Conversation Limits ── */}
                            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '10px' }}>{t('enterprise.quotas.conversationLimits')}</div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '20px' }}>
                                <div className="form-group">
                                    <label className="form-label">{t('enterprise.quotas.messageLimit')}</label>
                                    <input className="form-input" type="number" min={0} value={quotaForm.default_message_limit}
                                        onChange={e => setQuotaForm({ ...quotaForm, default_message_limit: Number(e.target.value) })} />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('enterprise.quotas.maxMessagesPerPeriod')}</div>
                                </div>
                                <div className="form-group">
                                    <label className="form-label">{t('enterprise.quotas.messagePeriod')}</label>
                                    <select className="form-input" value={quotaForm.default_message_period}
                                        onChange={e => setQuotaForm({ ...quotaForm, default_message_period: e.target.value })}>
                                        <option value="permanent">{t('enterprise.quotas.permanent')}</option>
                                        <option value="daily">{t('enterprise.quotas.daily')}</option>
                                        <option value="weekly">{t('enterprise.quotas.weekly')}</option>
                                        <option value="monthly">{t('enterprise.quotas.monthly')}</option>
                                    </select>
                                </div>
                            </div>

                            {/* ── Agent Limits ── */}
                            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '10px' }}>{t('enterprise.quotas.agentLimits')}</div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px', marginBottom: '20px' }}>
                                <div className="form-group">
                                    <label className="form-label">{t('enterprise.quotas.maxAgents')}</label>
                                    <input className="form-input" type="number" min={0} value={quotaForm.default_max_agents}
                                        onChange={e => setQuotaForm({ ...quotaForm, default_max_agents: Number(e.target.value) })} />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('enterprise.quotas.agentsUserCanCreate')}</div>
                                </div>
                                <div className="form-group">
                                    <label className="form-label">{t('enterprise.quotas.agentTTL')}</label>
                                    <input className="form-input" type="number" min={1} value={quotaForm.default_agent_ttl_hours}
                                        onChange={e => setQuotaForm({ ...quotaForm, default_agent_ttl_hours: Number(e.target.value) })} />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('enterprise.quotas.agentAutoExpiry')}</div>
                                </div>
                                <div className="form-group">
                                    <label className="form-label">{t('enterprise.quotas.dailyLLMCalls')}</label>
                                    <input className="form-input" type="number" min={0} value={quotaForm.default_max_llm_calls_per_day}
                                        onChange={e => setQuotaForm({ ...quotaForm, default_max_llm_calls_per_day: Number(e.target.value) })} />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('enterprise.quotas.maxLLMCallsPerDay')}</div>
                                </div>
                            </div>

                            {/* ── System Limits ── */}
                            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '10px' }}>{t('enterprise.quotas.system')}</div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px' }}>
                                <div className="form-group">
                                    <label className="form-label">{t('enterprise.quotas.minHeartbeatInterval')}</label>
                                    <input className="form-input" type="number" min={1} value={quotaForm.min_heartbeat_interval_minutes}
                                        onChange={e => setQuotaForm({ ...quotaForm, min_heartbeat_interval_minutes: Number(e.target.value) })} />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('enterprise.quotas.minHeartbeatDesc')}</div>
                                </div>
                            </div>

                            {/* ── Trigger Limits ── */}
                            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '10px' }}>{t('enterprise.quotas.triggerLimits')}</div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px', marginBottom: '20px' }}>
                                <div className="form-group">
                                    <label className="form-label">{t('enterprise.quotas.defaultMaxTriggers', 'Default Max Triggers')}</label>
                                    <input className="form-input" type="number" min={1} max={100} value={quotaForm.default_max_triggers}
                                        onChange={e => setQuotaForm({ ...quotaForm, default_max_triggers: Number(e.target.value) })} />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                        {t('enterprise.quotas.defaultMaxTriggersDesc', 'Default trigger limit for new agents')}
                                    </div>
                                </div>
                                <div className="form-group">
                                    <label className="form-label">{t('enterprise.quotas.minPollInterval', 'Min Poll Interval (min)')}</label>
                                    <input className="form-input" type="number" min={1} max={60} value={quotaForm.min_poll_interval_floor}
                                        onChange={e => setQuotaForm({ ...quotaForm, min_poll_interval_floor: Number(e.target.value) })} />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                        {t('enterprise.quotas.minPollIntervalDesc', 'Company-wide floor: agents cannot poll faster than this')}
                                    </div>
                                </div>
                                <div className="form-group">
                                    <label className="form-label">{t('enterprise.quotas.maxWebhookRate', 'Max Webhook Rate (/min)')}</label>
                                    <input className="form-input" type="number" min={1} max={60} value={quotaForm.max_webhook_rate_ceiling}
                                        onChange={e => setQuotaForm({ ...quotaForm, max_webhook_rate_ceiling: Number(e.target.value) })} />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                        {t('enterprise.quotas.maxWebhookRateDesc', 'Company-wide ceiling: max webhook hits per minute per agent')}
                                    </div>
                                </div>
                            </div>
                            <div style={{ marginTop: '16px', display: 'flex', gap: '8px', alignItems: 'center' }}>
                                <button className="btn btn-primary" onClick={saveQuotas} disabled={quotaSaving}>
                                    {quotaSaving ? t('common.loading') : t('common.save', 'Save')}
                                </button>
                                {quotaSaved && <span style={{ color: 'var(--success)', fontSize: '12px' }}>✅ Saved</span>}
                            </div>
                        </div>
                    </div>
                )}

                {/* ── Users Tab ── */}
                {activeTab === 'users' && (
                    <UserManagement key={selectedTenantId} />
                )}


                {/* ── Tools Tab ── */}
                {activeTab === 'tools' && (
                    <div>
                        {/* Sub-tab pills */}
                        <div style={{ display: 'flex', gap: '8px', marginBottom: '16px', borderBottom: '1px solid var(--border-subtle)', paddingBottom: '8px' }}>
                            {([['global', t('enterprise.tools.globalTools')], ['agent-installed', t('enterprise.tools.agentInstalled')]] as const).map(([key, label]) => (
                                <button key={key} onClick={() => { setToolsView(key as any); if (key === 'agent-installed') loadAgentInstalledTools(); }} style={{
                                    padding: '4px 14px', borderRadius: '12px', fontSize: '12px', fontWeight: 500, cursor: 'pointer', border: 'none',
                                    background: toolsView === key ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                                    color: toolsView === key ? '#fff' : 'var(--text-secondary)', transition: 'all 0.15s',
                                }}>{label}</button>
                            ))}
                        </div>

                        {/* Agent-Installed Tools */}
                        {toolsView === 'agent-installed' && (
                            <div>
                                <p style={{ fontSize: '13px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>{t('enterprise.tools.agentInstalledHint')}</p>
                                {agentInstalledTools.length === 0 ? (
                                    <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>{t('enterprise.tools.noAgentInstalledTools')}</div>
                                ) : (
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                        {agentInstalledTools.map((row: any) => (
                                            <div key={row.agent_tool_id} className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 16px' }}>
                                                <div style={{ flex: 1, minWidth: 0 }}>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                        <span style={{ fontWeight: 500, fontSize: '13px' }}>🔌 {row.tool_display_name}</span>
                                                        {row.mcp_server_name && <span style={{ fontSize: '10px', background: 'var(--primary)', color: '#fff', borderRadius: '4px', padding: '1px 5px' }}>MCP</span>}
                                                    </div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                                        🤖 {row.installed_by_agent_name || 'Unknown Agent'}
                                                        {row.installed_at && <span> · {new Date(row.installed_at).toLocaleString()}</span>}
                                                    </div>
                                                </div>
                                                <button className="btn btn-ghost" style={{ color: 'var(--error)', fontSize: '12px' }} onClick={async () => {
                                                    if (!confirm(t('enterprise.tools.removeFromAgent', { name: row.tool_display_name }))) return;
                                                    try {
                                                        await fetchJson(`/tools/agent-tool/${row.agent_tool_id}`, { method: 'DELETE' });
                                                    } catch {
                                                        // Already deleted (e.g. removed via Global Tools) — just refresh
                                                    }
                                                    loadAgentInstalledTools();
                                                }}>🗑️ {t('enterprise.tools.delete')}</button>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}

                        {toolsView === 'global' && <>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                                <h3>{t('enterprise.tools.title')}</h3>
                                <button className="btn btn-primary" onClick={() => setShowAddMCP(true)}>+ {t('enterprise.tools.addMcpServer')}</button>
                            </div>

                            {showAddMCP && (
                                <div className="card" style={{ padding: '16px', marginBottom: '16px' }}>
                                    <h4 style={{ marginBottom: '12px' }}>{t('enterprise.tools.mcpServer')}</h4>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                        <div>
                                            <label style={{ display: 'block', fontSize: '12px', marginBottom: '4px' }}>{t('enterprise.tools.jsonConfig')}</label>
                                            <textarea className="form-input" value={mcpRawInput} onChange={e => {
                                                const val = e.target.value;
                                                setMcpRawInput(val);
                                                // Auto-parse JSON config format
                                                try {
                                                    const parsed = JSON.parse(val);
                                                    const servers = parsed.mcpServers || parsed;
                                                    const names = Object.keys(servers);
                                                    if (names.length > 0) {
                                                        const name = names[0];
                                                        const cfg = servers[name];
                                                        const url = cfg.url || cfg.uri || '';
                                                        setMcpForm(p => ({ ...p, server_name: name, server_url: url }));
                                                    }
                                                } catch {
                                                    // Not JSON — treat as plain URL
                                                    setMcpForm(p => ({ ...p, server_url: val }));
                                                }
                                            }} placeholder={'{\n  "mcpServers": {\n    "server-name": {\n      "type": "sse",\n      "url": "https://mcp.example.com/sse"\n    }\n  }\n}\n\nor paste a URL directly'} style={{ minHeight: '120px', fontFamily: 'var(--font-mono)', fontSize: '12px', resize: 'vertical' }} />
                                        </div>
                                        {mcpForm.server_name && (
                                            <div style={{ display: 'flex', gap: '12px', fontSize: '12px', color: 'var(--text-secondary)', padding: '8px 12px', background: 'var(--bg-tertiary)', borderRadius: '6px' }}>
                                                <span>Name: <strong>{mcpForm.server_name}</strong></span>
                                                <span>URL: <strong>{mcpForm.server_url}</strong></span>
                                            </div>
                                        )}
                                        {!mcpForm.server_name && (
                                            <div>
                                                <label style={{ display: 'block', fontSize: '12px', marginBottom: '4px' }}>{t('enterprise.tools.mcpServerName')}</label>
                                                <input className="form-input" value={mcpForm.server_name} onChange={e => setMcpForm(p => ({ ...p, server_name: e.target.value }))} placeholder="My MCP Server" />
                                            </div>
                                        )}

                                        {/* Optional standalone API Key — sent as Authorization: Bearer */}
                                        <div>
                                            <label style={{ display: 'block', fontSize: '12px', marginBottom: '4px' }}>
                                                API Key <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(optional)</span>
                                            </label>
                                            <input
                                                type="password"
                                                className="form-input"
                                                value={mcpForm.api_key}
                                                onChange={e => setMcpForm(p => ({ ...p, api_key: e.target.value }))}
                                                placeholder="Leave blank if the key is already embedded in the URL"
                                                autoComplete="new-password"
                                            />
                                        </div>

                                        {/* Auth explanation for non-obvious behavior */}
                                        <div style={{ padding: '10px 12px', background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.18)', borderRadius: '6px', fontSize: '11px', color: 'var(--text-secondary)', lineHeight: '1.65' }}>
                                            <div style={{ fontWeight: 600, marginBottom: '4px', color: 'var(--text-primary)' }}>How authentication works</div>
                                            <div>- If your MCP server embeds the key in the URL (e.g. Tavily uses <code style={{ background: 'rgba(0,0,0,0.06)', padding: '0 3px', borderRadius: '3px' }}>?tavilyApiKey=xxx</code>), leave the field above blank.</div>
                                            <div>- For servers that use <strong>Bearer token</strong> auth, enter the key here. It is sent as <code style={{ background: 'rgba(0,0,0,0.06)', padding: '0 3px', borderRadius: '3px' }}>Authorization: Bearer ...</code> on every request.</div>
                                            <div>- If both are provided, the API Key field takes priority. All keys are stored encrypted.</div>
                                        </div>

                                        <div style={{ display: 'flex', gap: '8px' }}>
                                            <button className="btn btn-secondary" disabled={mcpTesting || !mcpForm.server_url} onClick={async () => {
                                                setMcpTesting(true); setMcpTestResult(null);
                                                try {
                                                    const r = await fetchJson<any>('/tools/test-mcp', { method: 'POST', body: JSON.stringify({ server_url: mcpForm.server_url, api_key: mcpForm.api_key || undefined }) });
                                                    setMcpTestResult(r);
                                                } catch (e: any) { setMcpTestResult({ ok: false, error: e.message }); }
                                                setMcpTesting(false);
                                            }}>{mcpTesting ? t('enterprise.tools.testing') : t('enterprise.tools.testConnection')}</button>
                                            <button className="btn btn-secondary" onClick={() => { setShowAddMCP(false); setMcpTestResult(null); setMcpForm({ server_url: '', server_name: '', api_key: '' }); setMcpRawInput(''); }}>{t('common.cancel')}</button>
                                        </div>
                                        {mcpTestResult && (
                                            <div className="card" style={{ padding: '12px', background: mcpTestResult.ok ? 'rgba(0,200,100,0.1)' : 'rgba(255,0,0,0.1)' }}>
                                                {mcpTestResult.ok ? (
                                                    <div>
                                                        <div style={{ color: 'var(--success)', fontWeight: 600, marginBottom: '8px' }}>{t('enterprise.tools.connectionSuccess', { count: mcpTestResult.tools?.length || 0 })}</div>
                                                        {(mcpTestResult.tools || []).map((tool: any, i: number) => (
                                                            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--border-color)' }}>
                                                                <div>
                                                                    <span style={{ fontWeight: 500, fontSize: '13px' }}>{tool.name}</span>
                                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{tool.description?.slice(0, 80)}</div>
                                                                </div>
                                                                <button className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: '11px' }} onClick={async () => {
                                                                    try {
                                                                        const serverName = mcpForm.server_name || mcpForm.server_url;
                                                                        await fetchJson('/tools', {
                                                                            method: 'POST', body: JSON.stringify({
                                                                                name: `mcp_${tool.name}`,
                                                                                display_name: tool.name,
                                                                                description: tool.description || '',
                                                                                type: 'mcp',
                                                                                category: 'custom',
                                                                                icon: '·',
                                                                                mcp_server_url: mcpForm.server_url,
                                                                                mcp_server_name: serverName,
                                                                                mcp_tool_name: tool.name,
                                                                                parameters_schema: tool.inputSchema || {},
                                                                                is_default: false,
                                                                                tenant_id: selectedTenantId || undefined,
                                                                            })
                                                                        });
                                                                        // Store API key on all tools from this server after creation
                                                                        if (mcpForm.api_key) {
                                                                            await fetchJson('/tools/mcp-server', { method: 'PUT', body: JSON.stringify({ server_name: serverName, server_url: mcpForm.server_url, api_key: mcpForm.api_key, tenant_id: selectedTenantId || undefined }) }).catch(() => {});
                                                                        }
                                                                        await loadAllTools();
                                                                    } catch (e: any) {
                                                                        alert(`${t('enterprise.tools.importFailed') || 'Import failed'}: ${e.message}`);
                                                                    }
                                                                }}>{t('enterprise.tools.import') || 'Import'}</button>
                                                            </div>
                                                        ))}
                                                        <div style={{ marginTop: '10px', display: 'flex', justifyContent: 'flex-end' }}>
                                                            <button className="btn btn-primary" style={{ padding: '6px 14px', fontSize: '12px' }} onClick={async () => {
                                                                const tools = mcpTestResult.tools || [];
                                                                let successCount = 0;
                                                                const errors: string[] = [];
                                                                const serverName = mcpForm.server_name || mcpForm.server_url;
                                                                for (const tool of tools) {
                                                                    try {
                                                                        await fetchJson('/tools', {
                                                                            method: 'POST', body: JSON.stringify({
                                                                                name: `mcp_${tool.name}`,
                                                                                display_name: tool.name,
                                                                                description: tool.description || '',
                                                                                type: 'mcp',
                                                                                category: 'custom',
                                                                                icon: '·',
                                                                                mcp_server_url: mcpForm.server_url,
                                                                                mcp_server_name: serverName,
                                                                                mcp_tool_name: tool.name,
                                                                                parameters_schema: tool.inputSchema || {},
                                                                                is_default: false,
                                                                                tenant_id: selectedTenantId || undefined,
                                                                            })
                                                                        });
                                                                        successCount++;
                                                                    } catch (e: any) {
                                                                        errors.push(`${tool.name}: ${e.message}`);
                                                                    }
                                                                }
                                                                // Store API key on all tools from this server in one request
                                                                if (mcpForm.api_key && successCount > 0) {
                                                                    await fetchJson('/tools/mcp-server', { method: 'PUT', body: JSON.stringify({ server_name: serverName, server_url: mcpForm.server_url, api_key: mcpForm.api_key, tenant_id: selectedTenantId || undefined }) }).catch(() => {});
                                                                }
                                                                await loadAllTools();
                                                                setShowAddMCP(false); setMcpTestResult(null); setMcpForm({ server_url: '', server_name: '', api_key: '' }); setMcpRawInput('');
                                                                if (errors.length > 0) {
                                                                    alert(`Imported ${successCount}/${tools.length} tools.\nFailed:\n${errors.join('\n')}`);
                                                                }
                                                            }}>{t('enterprise.tools.importAll')}</button>
                                                        </div>
                                                    </div>
                                                ) : (
                                                    <div style={{ color: 'var(--danger)' }}>{t('enterprise.tools.connectionFailed')}: {mcpTestResult.error}</div>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )}

                            {/* ─── Category-grouped tool list ─── */}
                            {(() => {
                                // Group tools by category (same pattern as AgentDetail.tsx)
                                const grouped = allTools.reduce((acc: Record<string, any[]>, tool: any) => {
                                    const cat = tool.category || 'general';
                                    (acc[cat] = acc[cat] || []).push(tool);
                                    return acc;
                                }, {} as Record<string, any[]>);

                                if (allTools.length === 0) {
                                    return <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>{t('enterprise.tools.emptyState')}</div>;
                                }

                                return (
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                                        {Object.entries(grouped).map(([category, catTools]) => {
                                            const hasCategoryConfig = !!GLOBAL_CATEGORY_CONFIG_SCHEMAS[category];

                                            // For 'custom' category: sub-group MCP tools by mcp_server_name
                                            // so that Edit Server is presented once per server, not per tool.
                                            if (category === 'custom') {
                                                const mcpByServer: Record<string, any[]> = {};
                                                const nonMcpTools: any[] = [];
                                                (catTools as any[]).forEach((t: any) => {
                                                    if (t.type === 'mcp' && t.mcp_server_name) {
                                                        (mcpByServer[t.mcp_server_name] = mcpByServer[t.mcp_server_name] || []).push(t);
                                                    } else {
                                                        nonMcpTools.push(t);
                                                    }
                                                });

                                                return (
                                                    <div key={category}>
                                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 14px', marginBottom: '8px' }}>
                                                            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                                                                {categoryLabels[category] || category}
                                                            </div>
                                                        </div>
                                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                                                            {/* MCP servers sub-grouped */}
                                                            {Object.entries(mcpByServer).map(([serverName, serverTools]) => (
                                                                <div key={serverName} style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', overflow: 'hidden' }}>
                                                                    {/* Server sub-header */}
                                                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '7px 14px', background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-subtle)' }}>
                                                                        <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
                                                                            <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }} title={serverName}>{(() => { try { if (serverName.startsWith('http')) { return new URL(serverName).hostname; } } catch {} return serverName; })()}</span>
                                                                            <span style={{ fontSize: '10px', background: 'rgba(99,102,241,0.12)', color: 'var(--accent-color)', borderRadius: '4px', padding: '1px 5px' }}>MCP</span>
                                                                            {(serverTools as any[]).some((t: any) => t.config && Object.keys(t.config).length > 0) && (
                                                                                <span style={{ fontSize: '10px', background: 'rgba(0,200,100,0.12)', color: 'var(--success)', borderRadius: '4px', padding: '1px 5px' }}>Configured</span>
                                                                            )}
                                                                        </div>
                                                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                                            <button
                                                                                style={{ background: 'none', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '3px 9px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }}
                                                                                onClick={() => {
                                                                                    // Pre-fill with current server URL from first tool
                                                                                    const firstTool = (serverTools as any[])[0];
                                                                                    setEditingMcpServer({
                                                                                        server_name: serverName,
                                                                                        server_url: firstTool?.mcp_server_url || '',
                                                                                        api_key: '',
                                                                                    });
                                                                                }}
                                                                            >Edit Server</button>
                                                                            {/* Server-level enable/disable all toggle */}
                                                                            <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer', flexShrink: 0 }} title={`Enable/Disable all ${serverName} tools`}>
                                                                                <input type="checkbox"
                                                                                    checked={(serverTools as any[]).every(t => t.enabled)}
                                                                                    onChange={async (e) => {
                                                                                        const payload = (serverTools as any[]).map(t => ({ tool_id: t.id, enabled: e.target.checked }));
                                                                                        await fetchJson('/tools/bulk', { method: 'PUT', body: JSON.stringify(payload) });
                                                                                        loadAllTools();
                                                                                    }}
                                                                                    style={{ opacity: 0, width: 0, height: 0 }} />
                                                                                <span style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, borderRadius: '22px', background: (serverTools as any[]).every(t => t.enabled) ? 'var(--accent-primary)' : 'var(--bg-tertiary)', transition: '0.3s' }}>
                                                                                    <span style={{ position: 'absolute', left: (serverTools as any[]).every(t => t.enabled) ? '20px' : '2px', top: '2px', width: '18px', height: '18px', borderRadius: '50%', background: '#fff', transition: '0.3s' }} />
                                                                                </span>
                                                                            </label>
                                                                        </div>
                                                                    </div>
                                                                    {/* Tools under this server */}
                                                                    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
                                                                        {(serverTools as any[]).map((tool: any, toolIdx: number) => (
                                                                            <div key={tool.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 14px', borderBottom: toolIdx < serverTools.length - 1 ? '1px solid var(--border-subtle)' : 'none' }}>
                                                                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1, minWidth: 0 }}>
                                                                                    <span style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>·</span>
                                                                                    <div style={{ minWidth: 0 }}>
                                                                                        <div style={{ fontWeight: 500, fontSize: '13px' }}>{tool.display_name}</div>
                                                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tool.description?.slice(0, 90)}</div>
                                                                                    </div>
                                                                                </div>
                                                                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                                                                                    <button className="btn btn-danger" style={{ padding: '3px 7px', fontSize: '10px' }} onClick={async () => {
                                                                                        if (!confirm(`${t('common.delete')} ${tool.display_name}?`)) return;
                                                                                        await fetchJson(`/tools/${tool.id}`, { method: 'DELETE' });
                                                                                        await loadAllTools();
                                                                                    }}>{t('common.delete')}</button>
                                                                                    <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer', flexShrink: 0 }}>
                                                                                        <input type="checkbox" checked={tool.enabled} onChange={async (e) => {
                                                                                            await fetchJson(`/tools/${tool.id}`, { method: 'PUT', body: JSON.stringify({ enabled: e.target.checked }) });
                                                                                            loadAllTools();
                                                                                        }} style={{ opacity: 0, width: 0, height: 0 }} />
                                                                                        <span style={{ position: 'absolute', inset: 0, background: tool.enabled ? 'var(--accent-primary)' : 'var(--bg-tertiary)', borderRadius: '11px', transition: 'background 0.2s' }}>
                                                                                            <span style={{ position: 'absolute', left: tool.enabled ? '20px' : '2px', top: '2px', width: '18px', height: '18px', background: '#fff', borderRadius: '50%', transition: 'left 0.2s' }} />
                                                                                        </span>
                                                                                    </label>
                                                                                </div>
                                                                            </div>
                                                                        ))}
                                                                    </div>
                                                                </div>
                                                            ))}
                                                            {/* Non-MCP custom tools shown normally */}
                                                            {nonMcpTools.length > 0 && (
                                                                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                                                    {nonMcpTools.map((tool: any) => {
                                                                        const hasOwnConfig = tool.config_schema?.fields?.length > 0;
                                                                        return (
                                                                            <div key={tool.id} className="card" style={{ padding: '0', overflow: 'hidden' }}>
                                                                                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px' }}>
                                                                                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flex: 1, minWidth: 0 }}>
                                                                                        <span style={{ fontSize: '18px' }}>{tool.icon}</span>
                                                                                        <div style={{ minWidth: 0 }}>
                                                                                            <div style={{ fontWeight: 500, fontSize: '13px' }}>{tool.display_name}</div>
                                                                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tool.description?.slice(0, 80)}</div>
                                                                                        </div>
                                                                                    </div>
                                                                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                                                                                        {hasOwnConfig && (
                                                                                            <button style={{ background: 'none', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }} onClick={() => { setEditingToolId(tool.id); setEditingConfig({ ...tool.config }); }}>Configure</button>
                                                                                        )}
                                                                                        <button className="btn btn-danger" style={{ padding: '4px 8px', fontSize: '11px' }} onClick={async () => {
                                                                                            if (!confirm(`${t('common.delete')} ${tool.display_name}?`)) return;
                                                                                            await fetchJson(`/tools/${tool.id}`, { method: 'DELETE' });
                                                                                            loadAllTools();
                                                                                        }}>{t('common.delete')}</button>
                                                                                        <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer', flexShrink: 0 }}>
                                                                                            <input type="checkbox" checked={tool.enabled} onChange={async (e) => {
                                                                                                await fetchJson(`/tools/${tool.id}`, { method: 'PUT', body: JSON.stringify({ enabled: e.target.checked }) });
                                                                                                loadAllTools();
                                                                                            }} style={{ opacity: 0, width: 0, height: 0 }} />
                                                                                            <span style={{ position: 'absolute', inset: 0, background: tool.enabled ? 'var(--accent-primary)' : 'var(--bg-tertiary)', borderRadius: '11px', transition: 'background 0.2s' }}>
                                                                                                <span style={{ position: 'absolute', left: tool.enabled ? '20px' : '2px', top: '2px', width: '18px', height: '18px', background: '#fff', borderRadius: '50%', transition: 'left 0.2s' }} />
                                                                                            </span>
                                                                                        </label>
                                                                                    </div>
                                                                                </div>
                                                                            </div>
                                                                        );
                                                                    })}
                                                                </div>
                                                            )}
                                                        </div>
                                                    </div>
                                                );
                                            }

                                            return (
                                                <div key={category}>
                                                    {/* Category header */}
                                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 14px', marginBottom: '8px' }}>
                                                        <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                                                            {categoryLabels[category] || category}
                                                        </div>
                                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                            {hasCategoryConfig && (
                                                                <button
                                                                    onClick={() => {
                                                                        setConfigCategory(category);
                                                                        setEditingConfig({});
                                                                        // Load existing global config from the first tool in this category that has a non-empty config.
                                                                        // Do NOT require config_schema — some categories (e.g. AgentBay)
                                                                        // define their schema only in frontend CATEGORY_CONFIG_SCHEMAS.
                                                                        const firstToolWithConfig = (catTools as any[]).find((tl: any) => tl.config && Object.keys(tl.config).length > 0);
                                                                        if (firstToolWithConfig?.config) {
                                                                            setEditingConfig({ ...firstToolWithConfig.config });
                                                                        }
                                                                    }}
                                                                    style={{ background: 'none', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }}
                                                                    title={`Configure ${category}`}
                                                                >
                                                                    ⚙️ {t('enterprise.tools.configure', 'Configure')}
                                                                </button>
                                                            )}
                                                            {/* Category Bulk Toggle */}
                                                            <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer', flexShrink: 0 }} title={`Enable/Disable all ${categoryLabels[category] || category} tools`}>
                                                                <input type="checkbox"
                                                                    checked={(catTools as any[]).every(t => t.enabled)}
                                                                    onChange={async (e) => {
                                                                        const targetEnabled = e.target.checked;
                                                                        try {
                                                                            const payload = (catTools as any[]).map(t => ({ tool_id: t.id, enabled: targetEnabled }));
                                                                            await fetchJson('/tools/bulk', { method: 'PUT', body: JSON.stringify(payload) });
                                                                            loadAllTools();
                                                                        } catch (err: any) {
                                                                            alert('Bulk update failed: ' + err.message);
                                                                        }
                                                                    }}
                                                                    style={{ opacity: 0, width: 0, height: 0 }} />
                                                                <span style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, borderRadius: '22px', background: (catTools as any[]).every(t => t.enabled) ? 'var(--accent-primary)' : 'var(--bg-tertiary)', transition: '0.3s', boxShadow: 'inset 0 1px 3px rgba(0,0,0,0.1)' }}>
                                                                    <span style={{ position: 'absolute', left: (catTools as any[]).every(t => t.enabled) ? '20px' : '2px', top: '2px', width: '18px', height: '18px', borderRadius: '50%', background: '#fff', transition: '0.3s', boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }} />
                                                                </span>
                                                            </label>
                                                        </div>
                                                    </div>

                                                    {/* Tools in this category */}
                                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                                        {(catTools as any[]).map((tool: any) => {
                                                            // If this category has shared config, individual tool config buttons are hidden
                                                            const hasOwnConfig = tool.config_schema?.fields?.length > 0 && !hasCategoryConfig;
                                                            const isEditing = editingToolId === tool.id;

                                                            return (
                                                                <div key={tool.id} className="card" style={{ padding: '0', overflow: 'hidden' }}>
                                                                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px' }}>
                                                                        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flex: 1, minWidth: 0 }}>
                                                                            <span style={{ fontSize: '18px' }}>{tool.icon}</span>
                                                                            <div style={{ minWidth: 0 }}>
                                                                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                                                    <span style={{ fontWeight: 500, fontSize: '13px' }}>{tool.display_name}</span>
                                                                                    <span style={{ fontSize: '10px', background: tool.type === 'mcp' ? 'var(--primary)' : 'var(--bg-tertiary)', color: tool.type === 'mcp' ? '#fff' : 'var(--text-secondary)', borderRadius: '4px', padding: '1px 5px' }}>
                                                                                        {tool.type === 'mcp' ? 'MCP' : 'Built-in'}
                                                                                    </span>
                                                                                    {tool.is_default && <span style={{ fontSize: '10px', background: 'rgba(0,200,100,0.15)', color: 'var(--success)', borderRadius: '4px', padding: '1px 5px' }}>Default</span>}
                                                                                    {tool.config && Object.keys(tool.config).length > 0 && (
                                                                                        <span style={{ fontSize: '10px', background: 'rgba(99,102,241,0.15)', color: 'var(--accent-color)', borderRadius: '4px', padding: '1px 5px' }}>{t('enterprise.tools.configured', 'Configured')}</span>
                                                                                    )}
                                                                                </div>
                                                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                                                    {tool.description?.slice(0, 80)}
                                                                                    {tool.mcp_server_name && <span> · {tool.mcp_server_name}</span>}
                                                                                </div>
                                                                            </div>
                                                                        </div>

                                                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                                                                            {/* Per-tool config button: only if the tool has its own schema AND is NOT part of a category config */}
                                                                            {hasOwnConfig && (
                                                                                <button
                                                                                    style={{ background: 'none', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }}
                                                                                    title={t('enterprise.tools.configureSettings', 'Configure settings')}
                                                                                    onClick={async () => {
                                                                                        setEditingToolId(tool.id);
                                                                                        const cfg = { ...tool.config };
                                                                                        if (tool.name === 'jina_search' || tool.name === 'jina_read') {
                                                                                            try {
                                                                                                const token = localStorage.getItem('token');
                                                                                                const res = await fetch('/api/enterprise/system-settings/jina_api_key', { headers: { Authorization: `Bearer ${token}` } });
                                                                                                const d = await res.json();
                                                                                                if (d.value?.api_key) cfg.api_key = d.value.api_key;
                                                                                            } catch { }
                                                                                        }
                                                                                        setEditingConfig(cfg);
                                                                                    }}
                                                                                >
                                                                                    ⚙️ {t('enterprise.tools.configure')}
                                                                                </button>
                                                                            )}

                                                                            {/* Delete (non-builtin only) */}
                                                                            {tool.type !== 'builtin' && (
                                                                                <button className="btn btn-danger" style={{ padding: '4px 8px', fontSize: '11px' }} onClick={async () => {
                                                                                    if (!confirm(`${t('common.delete')} ${tool.display_name}?`)) return;
                                                                                    await fetchJson(`/tools/${tool.id}`, { method: 'DELETE' });
                                                                                    loadAllTools();
                                                                                    loadAgentInstalledTools();
                                                                                }}>{t('common.delete')}</button>
                                                                            )}

                                                                            {/* Enable toggle */}
                                                                            <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer', flexShrink: 0 }}>
                                                                                <input type="checkbox" checked={tool.enabled} onChange={async (e) => {
                                                                                    await fetchJson(`/tools/${tool.id}`, { method: 'PUT', body: JSON.stringify({ enabled: e.target.checked }) });
                                                                                    loadAllTools();
                                                                                }} style={{ opacity: 0, width: 0, height: 0 }} />
                                                                                <span style={{ position: 'absolute', inset: 0, background: tool.enabled ? 'var(--accent-primary)' : 'var(--bg-tertiary)', borderRadius: '11px', transition: 'background 0.2s' }}>
                                                                                    <span style={{ position: 'absolute', left: tool.enabled ? '20px' : '2px', top: '2px', width: '18px', height: '18px', background: '#fff', borderRadius: '50%', transition: 'left 0.2s' }} />
                                                                                </span>
                                                                            </label>
                                                                        </div>
                                                                    </div>

                                                                    {/* Inline config editing form (per-tool only) */}
                                                                    {/* Inline config editing form replaced by global modal */}
                                                                </div>
                                                            );
                                                        })}
                                                    </div>
                                                </div>
                                            );
                                        })}
                                    </div>
                                );
                            })()}

                            {/* ─── Edit MCP Server Modal ─── */}
                            {editingMcpServer && (
                                <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.55)', zIndex: 2000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                                    onClick={e => { if (e.target === e.currentTarget) setEditingMcpServer(null); }}>
                                    <div className="card" style={{ width: '480px', maxWidth: '95vw', padding: '24px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
                                        <h3 style={{ margin: 0, fontSize: '15px' }}>Edit MCP Server</h3>
                                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', background: 'var(--bg-tertiary)', padding: '6px 10px', borderRadius: '6px' }}>
                                            <strong>{editingMcpServer.server_name}</strong>
                                            <span style={{ marginLeft: '8px', color: 'var(--text-tertiary)' }}>Updates all tools from this server at once</span>
                                        </div>

                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                                            <div>
                                                <label style={{ display: 'block', fontSize: '12px', marginBottom: '4px' }}>Server URL</label>
                                                <input
                                                    type="password"
                                                    className="form-input"
                                                    value={editingMcpServer.server_url}
                                                    onChange={e => setEditingMcpServer(s => s ? { ...s, server_url: e.target.value } : null)}
                                                    placeholder="https://mcp.example.com/sse"
                                                    autoComplete="off"
                                                />
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '3px' }}>Stored encrypted. For URL-embedded keys (e.g. Tavily), include the key directly here.</div>
                                            </div>
                                            <div>
                                                <label style={{ display: 'block', fontSize: '12px', marginBottom: '4px' }}>
                                                    API Key <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(optional)</span>
                                                </label>
                                                <input
                                                    type="password"
                                                    className="form-input"
                                                    value={editingMcpServer.api_key}
                                                    onChange={e => setEditingMcpServer(s => s ? { ...s, api_key: e.target.value } : null)}
                                                    placeholder="Leave blank to keep existing key"
                                                    autoComplete="new-password"
                                                />
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '3px' }}>Sent as <code style={{ background: 'rgba(0,0,0,0.06)', padding: '0 3px', borderRadius: '3px' }}>Authorization: Bearer ...</code> Takes priority over URL-embedded keys.</div>
                                            </div>

                                            {/* Auth explanation */}
                                            <div style={{ padding: '10px 12px', background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.18)', borderRadius: '6px', fontSize: '11px', color: 'var(--text-secondary)', lineHeight: '1.65' }}>
                                                <div style={{ fontWeight: 600, marginBottom: '4px', color: 'var(--text-primary)' }}>How authentication works</div>
                                                <div>- <strong>URL-embedded key</strong> (e.g. Tavily <code style={{ background: 'rgba(0,0,0,0.06)', padding: '0 3px', borderRadius: '3px' }}>?tavilyApiKey=xxx</code>): include in Server URL above, leave API Key blank.</div>
                                                <div>- <strong>Bearer token</strong> auth: enter in the API Key field. It is injected as an HTTP header on every request — the URL stays clean.</div>
                                                <div>- If both are present, the API Key field takes priority over any URL-embedded value.</div>
                                            </div>
                                        </div>

                                        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                                            <button className="btn btn-secondary" onClick={() => setEditingMcpServer(null)} disabled={mcpServerSaving}>Cancel</button>
                                            <button className="btn btn-primary" disabled={mcpServerSaving || !editingMcpServer.server_url} onClick={async () => {
                                                setMcpServerSaving(true);
                                                try {
                                                    await fetchJson('/tools/mcp-server', {
                                                        method: 'PUT',
                                                        body: JSON.stringify({
                                                            server_name: editingMcpServer.server_name,
                                                            server_url: editingMcpServer.server_url,
                                                            // Only send api_key if the user typed something; null = keep existing
                                                            api_key: editingMcpServer.api_key || undefined,
                                                            tenant_id: selectedTenantId || undefined,
                                                        })
                                                    });
                                                    await loadAllTools();
                                                    setEditingMcpServer(null);
                                                } catch (e: any) {
                                                    alert('Failed to update server: ' + e.message);
                                                }
                                                setMcpServerSaving(false);
                                            }}>{mcpServerSaving ? 'Saving...' : 'Save Changes'}</button>
                                        </div>
                                    </div>
                                </div>
                            )}

                            {/* Per-Tool Config Modal */}
                            {editingToolId && (() => {
                                const tool = allTools.find(t => t.id === editingToolId);
                                if (!tool) return null;
                                return (
                                    <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.55)', zIndex: 2000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                                        onClick={() => setEditingToolId(null)}>
                                        <div onClick={e => e.stopPropagation()} style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', width: '480px', maxWidth: '95vw', maxHeight: '80vh', overflow: 'auto', boxShadow: '0 20px 60px rgba(0,0,0,0.4)' }}>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                                                <div>
                                                    <h3 style={{ margin: 0 }}>⚙️ {tool.display_name}</h3>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>Global configuration used by all agents</div>
                                                </div>
                                                <button onClick={() => setEditingToolId(null)} style={{ background: 'none', border: 'none', fontSize: '18px', cursor: 'pointer', color: 'var(--text-secondary)' }}>✕</button>
                                            </div>
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                                                {(tool.config_schema.fields || []).map((field: any) => {
                                                    // Check depends_on
                                                    if (field.depends_on) {
                                                        const visible = Object.entries(field.depends_on).every(([k, vals]: [string, any]) =>
                                                            vals.includes(editingConfig[k])
                                                        );
                                                        if (!visible) return null;
                                                    }
                                                    return (
                                                        <div key={field.key}>
                                                            <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '4px' }}>{field.label}</label>
                                                            {field.type === 'checkbox' ? (
                                                                <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer' }}>
                                                                    <input
                                                                        type="checkbox"
                                                                        checked={editingConfig[field.key] ?? field.default ?? false}
                                                                        onChange={e => setEditingConfig(p => ({ ...p, [field.key]: e.target.checked }))}
                                                                        style={{ opacity: 0, width: 0, height: 0 }}
                                                                    />
                                                                    <span style={{
                                                                        position: 'absolute', inset: 0,
                                                                        background: (editingConfig[field.key] ?? field.default) ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                                                                        borderRadius: '11px', transition: 'background 0.2s',
                                                                    }}>
                                                                        <span style={{
                                                                            position: 'absolute', left: (editingConfig[field.key] ?? field.default) ? '20px' : '2px', top: '2px',
                                                                            width: '18px', height: '18px', background: '#fff',
                                                                            borderRadius: '50%', transition: 'left 0.2s',
                                                                        }} />
                                                                    </span>
                                                                </label>
                                                            ) : field.type === 'select' ? (
                                                                <select className="form-input" value={editingConfig[field.key] ?? field.default ?? ''} onChange={e => setEditingConfig(p => ({ ...p, [field.key]: e.target.value }))}>
                                                                    {(field.options || []).map((opt: any) => (
                                                                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                                                                    ))}
                                                                </select>
                                                            ) : field.type === 'number' ? (
                                                                <input type="number" className="form-input" value={editingConfig[field.key] ?? field.default ?? ''} min={field.min} max={field.max}
                                                                    onChange={e => setEditingConfig(p => ({ ...p, [field.key]: Number(e.target.value) }))} />
                                                            ) : field.type === 'password' ? (
                                                                <input type="password" autoComplete="new-password" className="form-input" value={editingConfig[field.key] ?? ''} placeholder={field.placeholder || ''}
                                                                    onChange={e => setEditingConfig(p => ({ ...p, [field.key]: e.target.value }))} />
                                                            ) : (
                                                                <input type="text" className="form-input" value={editingConfig[field.key] ?? field.default ?? ''} placeholder={field.placeholder || ''}
                                                                    onChange={e => setEditingConfig(p => ({ ...p, [field.key]: e.target.value }))} />
                                                            )}
                                                        </div>
                                                    );
                                                })}
                                                <div style={{ display: 'flex', gap: '8px', marginTop: '12px', justifyContent: 'flex-end', borderTop: '1px solid var(--border-subtle)', paddingTop: '16px' }}>
                                                    <button className="btn btn-secondary" onClick={() => setEditingToolId(null)}>{t('common.cancel')}</button>
                                                    <button className="btn btn-primary" onClick={async () => {
                                                        if (tool.name === 'jina_search' || tool.name === 'jina_read') {
                                                            if (editingConfig.api_key) {
                                                                const token = localStorage.getItem('token');
                                                                await fetch('/api/enterprise/system-settings/jina_api_key', {
                                                                    method: 'PUT',
                                                                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                                                                    body: JSON.stringify({ value: { api_key: editingConfig.api_key } }),
                                                                });
                                                            }
                                                        } else {
                                                            await fetchJson(`/tools/${tool.id}`, { method: 'PUT', body: JSON.stringify({ config: editingConfig }) });
                                                        }
                                                        setEditingToolId(null);
                                                        loadAllTools();
                                                    }}>{t('enterprise.tools.saveConfig')}</button>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                );
                            })()}

                            {/* Category-level config modal */}
                            {configCategory && GLOBAL_CATEGORY_CONFIG_SCHEMAS[configCategory] && (
                                <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.55)', zIndex: 2000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                                    onClick={() => setConfigCategory(null)}>
                                    <div onClick={e => e.stopPropagation()} style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', width: '480px', maxWidth: '95vw', maxHeight: '80vh', overflow: 'auto', boxShadow: '0 20px 60px rgba(0,0,0,0.4)' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                                            <div>
                                                <h3 style={{ margin: 0 }}>{GLOBAL_CATEGORY_CONFIG_SCHEMAS[configCategory].title}</h3>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>Global configuration shared by all tools in this category</div>
                                            </div>
                                            <button onClick={() => setConfigCategory(null)} style={{ background: 'none', border: 'none', fontSize: '18px', cursor: 'pointer', color: 'var(--text-secondary)' }}>x</button>
                                        </div>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                                            {GLOBAL_CATEGORY_CONFIG_SCHEMAS[configCategory].fields.map((field: any) => (
                                                <div key={field.key}>
                                                    <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '4px' }}>{field.label}</label>
                                                    {field.type === 'password' ? (
                                                        <input type="password" autoComplete="new-password" className="form-input" value={editingConfig[field.key] ?? ''} placeholder={field.placeholder || ''}
                                                            onChange={e => setEditingConfig(p => ({ ...p, [field.key]: e.target.value }))} />
                                                    ) : field.type === 'select' ? (
                                                        <select className="form-input" value={editingConfig[field.key] ?? field.default ?? ''} onChange={e => setEditingConfig(p => ({ ...p, [field.key]: e.target.value }))}>
                                                            {(field.options || []).map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
                                                        </select>
                                                    ) : (
                                                        <input type="text" className="form-input" value={editingConfig[field.key] ?? ''} placeholder={field.placeholder || ''}
                                                            onChange={e => setEditingConfig(p => ({ ...p, [field.key]: e.target.value }))} />
                                                    )}
                                                </div>
                                            ))}
                                            <div style={{ display: 'flex', gap: '8px', marginTop: '8px', justifyContent: 'flex-end' }}>
                                                <button className="btn btn-secondary" onClick={() => setConfigCategory(null)}>{t('common.cancel')}</button>
                                                <button className="btn btn-primary" onClick={async () => {
                                                    // Save config to the first tool in this category.
                                                    // We write to one representative tool per category;
                                                    // get_category_config endpoint reads it back.
                                                    const catTools = allTools.filter((tl: any) => (tl.category || 'general') === configCategory);
                                                    if (catTools.length > 0) {
                                                        await fetchJson(`/tools/${catTools[0].id}`, { method: 'PUT', body: JSON.stringify({ config: editingConfig }) });
                                                    }
                                                    setConfigCategory(null);
                                                    loadAllTools();
                                                }}>{t('common.save', 'Save')}</button>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </>}
                    </div>
                )}

                {/* ── Skills Tab ── */}
                {activeTab === 'skills' && <SkillsTab />}

                {/* ── Invitation Codes Tab ── */}
                {activeTab === 'invites' && <InvitationCodes />}
            </div>

            {
                kbToast && (
                    <div style={{
                        position: 'fixed', top: '20px', right: '20px', zIndex: 20000,
                        padding: '12px 20px', borderRadius: '8px',
                        background: kbToast.type === 'success' ? 'rgba(34, 197, 94, 0.9)' : 'rgba(239, 68, 68, 0.9)',
                        color: '#fff', fontSize: '14px', fontWeight: 500,
                        boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
                    }}>
                        {''}{kbToast.message}
                    </div>
                )
            }
        </>
    );
}
