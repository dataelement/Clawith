import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconEdit } from '@tabler/icons-react';
import { useDialog } from '../../../components/Dialog/DialogProvider';
import { useToast } from '../../../components/Toast/ToastProvider';
import { useAuthStore } from '../../../stores';
import { fetchJson } from '../utils/fetchJson';

interface LLMModel {
    id: string;
    provider: string;
    model: string;
    label: string;
    base_url?: string;
    api_key_masked?: string;
    max_tokens_per_day?: number;
    enabled: boolean;
    supports_vision?: boolean;
    supports_tool_calling?: boolean | null;
    tool_calling_capability_source?: 'probe' | 'builtin_registry' | null;
    tool_calling_checked_at?: string | null;
    tool_calling_error?: string | null;
    max_output_tokens?: number;
    request_timeout?: number;
    temperature?: number;
    created_at: string;
}

interface LLMProviderSpec {
    provider: string;
    display_name: string;
    protocol: string;
    default_base_url?: string | null;
    supports_tool_choice: boolean;
    default_max_tokens: number;
}

interface RuntimeModelSettings {
    tenant_id: string;
    planning_model_id: string | null;
    compact_model_id: string | null;
    planning_source: 'database' | 'environment';
    compact_source: 'database' | 'environment';
    candidates: Array<Pick<LLMModel, 'id' | 'label' | 'provider' | 'model'>>;
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

type LlmTabProps = {
    selectedTenantId: string;
};

export default function LlmTab({ selectedTenantId }: LlmTabProps) {
    const { t } = useTranslation();
    const dialog = useDialog();
    const toast = useToast();
    const qc = useQueryClient();
    const currentUser = useAuthStore((s) => s.user);
    const [showAddModel, setShowAddModel] = useState(false);
    const [editingModelId, setEditingModelId] = useState<string | null>(null);
    const [modelForm, setModelForm] = useState({
        provider: 'anthropic',
        model: '',
        api_key: '',
        base_url: '',
        label: '',
        supports_vision: false,
        max_output_tokens: '' as string,
        request_timeout: '' as string,
        temperature: '' as string,
    });
    const [runtimeModelForm, setRuntimeModelForm] = useState({
        planning_model_id: '',
        compact_model_id: '',
    });

    const invalidateModelCaches = () => {
        qc.invalidateQueries({ queryKey: ['llm-models'] });
        qc.invalidateQueries({ queryKey: ['tenant', 'me'] });
        qc.invalidateQueries({ queryKey: ['tenant-default-model'] });
        qc.invalidateQueries({ queryKey: ['agents'] });
        qc.invalidateQueries({ queryKey: ['agent'] });
    };

    const { data: models = [] } = useQuery({
        queryKey: ['llm-models', selectedTenantId],
        queryFn: () => fetchJson<LLMModel[]>(`/enterprise/llm-models${selectedTenantId ? `?tenant_id=${selectedTenantId}` : ''}`),
    });
    const { data: providerSpecs = [] } = useQuery({
        queryKey: ['llm-provider-specs'],
        queryFn: () => fetchJson<LLMProviderSpec[]>('/enterprise/llm-providers'),
    });
    const providerOptions = providerSpecs.length > 0 ? providerSpecs : FALLBACK_LLM_PROVIDERS;
    const canManageRuntimeModels = currentUser?.role === 'platform_admin'
        || currentUser?.role === 'org_admin'
        || !!currentUser?.is_platform_admin;
    const runtimeModelSettingsUrl = `/enterprise/runtime-model-settings${selectedTenantId ? `?tenant_id=${selectedTenantId}` : ''}`;
    const { data: runtimeModelSettings } = useQuery({
        queryKey: ['runtime-model-settings', selectedTenantId],
        queryFn: () => fetchJson<RuntimeModelSettings>(runtimeModelSettingsUrl),
        enabled: canManageRuntimeModels,
    });
    useEffect(() => {
        if (!runtimeModelSettings) return;
        setRuntimeModelForm({
            planning_model_id: runtimeModelSettings.planning_model_id || '',
            compact_model_id: runtimeModelSettings.compact_model_id || '',
        });
    }, [runtimeModelSettings]);
    const saveRuntimeModelSettings = useMutation({
        mutationFn: () => fetchJson<RuntimeModelSettings>(runtimeModelSettingsUrl, {
            method: 'PUT',
            body: JSON.stringify(runtimeModelForm),
        }),
        onSuccess: (data) => {
            qc.setQueryData(['runtime-model-settings', selectedTenantId], data);
            toast.success(t('enterprise.llm.runtimeModelsSaved', '运行时模型配置已更新'));
        },
        onError: (err: any) => {
            toast.error(t('enterprise.llm.runtimeModelsSaveFailed', '运行时模型配置保存失败'), {
                details: String(err?.message || err),
            });
        },
    });

    const addModel = useMutation({
        mutationFn: (data: any) => fetchJson(`/enterprise/llm-models${selectedTenantId ? `?tenant_id=${selectedTenantId}` : ''}`, { method: 'POST', body: JSON.stringify(data) }),
        onSuccess: () => {
            invalidateModelCaches();
            setShowAddModel(false);
            setEditingModelId(null);
        },
    });
    const updateModel = useMutation({
        mutationFn: ({ id, data }: { id: string; data: any }) => fetchJson(`/enterprise/llm-models/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
        onSuccess: () => {
            invalidateModelCaches();
            setShowAddModel(false);
            setEditingModelId(null);
        },
    });
    const { data: tenantForDefault, refetch: refetchTenantForDefault } = useQuery({
        queryKey: ['tenant-default-model', selectedTenantId],
        queryFn: () => fetchJson<{ default_model_id: string | null }>(
            !selectedTenantId || selectedTenantId === currentUser?.tenant_id
                ? '/tenants/me'
                : `/tenants/${selectedTenantId}`
        ),
    });
    const setDefaultModel = useMutation({
        mutationFn: (modelId: string) => fetchJson(`/enterprise/llm-models/${modelId}/set-default`, { method: 'POST' }),
        onSuccess: (_data, modelId) => {
            qc.setQueryData(['tenant-default-model', selectedTenantId], (old: any) => ({
                ...(old || {}),
                default_model_id: modelId,
            }));
            refetchTenantForDefault();
            qc.invalidateQueries({ queryKey: ['tenant-default-model', selectedTenantId] });
            qc.invalidateQueries({ queryKey: ['tenant', selectedTenantId] });
            qc.invalidateQueries({ queryKey: ['tenant', 'me'] });
            qc.invalidateQueries({ queryKey: ['llm-models', selectedTenantId] });
            invalidateModelCaches();
            qc.invalidateQueries({ queryKey: ['agents'] });
            qc.invalidateQueries({ queryKey: ['agent'] });
            toast.success(t('enterprise.llm.defaultSaved', 'Default model updated'));
        },
        onError: (err: any) => {
            toast.error(t('enterprise.llm.defaultSaveFailed', 'Failed to update default model'), {
                details: String(err?.message || err),
            });
        },
    });
    const deleteModel = useMutation({
        mutationFn: async ({ id }: { id: string; force?: boolean }) => {
            const url = `/enterprise/llm-models/${id}`;
            const res = await fetch(`/api${url}`, {
                method: 'DELETE',
                headers: { Authorization: `Bearer ${localStorage.getItem('token')}` },
            });
            if (res.status === 409) {
                const data = await res.json();
                const agents = data.detail?.agents || [];
                const msg = `该模型正在被 ${agents.length} 个数字员工使用：\n\n${agents.join(', ')}\n\n仍要删除吗？（对应的模型配置会被清空）`;
                if (await dialog.confirm(msg, { title: t('common.dialog.deleteModel'), danger: true, confirmLabel: t('common.confirmActions.forceDelete') })) {
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
        onSuccess: () => invalidateModelCaches(),
    });

    const openCreateForm = () => {
        setEditingModelId(null);
        const defaultSpec = providerOptions[0];
        setModelForm({
            provider: defaultSpec?.provider || 'anthropic',
            model: '',
            api_key: '',
            base_url: defaultSpec?.default_base_url || '',
            label: '',
            supports_vision: false,
            max_output_tokens: defaultSpec ? String(defaultSpec.default_max_tokens) : '4096',
            request_timeout: '',
            temperature: '',
        });
        setShowAddModel(true);
    };

    const runConnectivityTest = async (modelId?: string | null, requireApiKey = false) => {
        const btn = document.activeElement as HTMLButtonElement;
        const origText = btn?.textContent || '';
        if (btn) btn.textContent = t('enterprise.llm.testing');
        try {
            const token = localStorage.getItem('token');
            const testData: any = {
                provider: modelForm.provider,
                model: modelForm.model,
                base_url: modelForm.base_url || undefined,
            };
            if (modelForm.api_key) testData.api_key = modelForm.api_key;
            if (modelId) testData.model_id = modelId;
            if (requireApiKey && !modelForm.api_key) return;
            const res = await fetch('/api/enterprise/llm-test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify(testData),
            });
            const result = await res.json();
            if (result.capability_recorded) invalidateModelCaches();
            if (result.connection_success && result.tool_calling_supported === true) {
                if (btn) {
                    btn.textContent = result.capability_recorded
                        ? t('enterprise.llm.testSuccessWithTools', '连接与原生工具调用均可用 · {{latency}}ms', { latency: result.latency_ms })
                        : t('enterprise.llm.draftTestSuccess', '测试通过；保存模型后需再次测试');
                    btn.style.color = 'var(--success)';
                }
                setTimeout(() => {
                    if (btn) {
                        btn.textContent = origText;
                        btn.style.color = '';
                    }
                }, 3000);
            } else if (result.connection_success) {
                const capabilityUnknown = result.tool_calling_supported == null;
                await dialog.alert(
                    capabilityUnknown
                        ? t('enterprise.llm.toolTestUnknown', '连接成功，但原生工具调用暂未确认')
                        : t('enterprise.llm.toolTestUnsupported', '连接成功，但原生工具调用不可用'),
                    {
                        type: 'error',
                        title: t('enterprise.llm.agentCompatibilityTest', 'Agent 兼容性测试'),
                        details: String(result.tool_calling_error || result.error || 'Model did not return a valid finish tool call.'),
                    },
                );
                if (btn) btn.textContent = origText;
            } else {
                await dialog.alert(t('enterprise.llm.testFailedShort', '连通性测试失败'), {
                    type: 'error',
                    title: t('enterprise.llm.testTitle', '连通性测试'),
                    details: String(result.error || 'Unknown error'),
                });
                if (btn) btn.textContent = origText;
            }
        } catch (e: any) {
            await dialog.alert(t('enterprise.llm.testErrorShort', '连通性测试出错'), {
                type: 'error',
                title: t('enterprise.llm.testTitle', '连通性测试'),
                details: String(e?.message || e),
            });
            if (btn) btn.textContent = origText;
        }
    };

    return (
        <div>
            {canManageRuntimeModels && runtimeModelSettings && (
                <div className="card" style={{ marginBottom: '16px' }}>
                    <div style={{ marginBottom: '14px' }}>
                        <div style={{ fontWeight: 600, fontSize: '15px' }}>
                            {t('enterprise.llm.runtimeModelsTitle', '多智能体运行时模型')}
                        </div>
                        <div style={{ marginTop: '4px', color: 'var(--text-tertiary)', fontSize: '12px' }}>
                            {t('enterprise.llm.runtimeModelsHint', '可使用当前公司的模型或平台模型；候选模型必须已启用并通过原生工具调用测试。保存后立即生效。')}
                        </div>
                    </div>
                    {runtimeModelSettings.candidates.length === 0 ? (
                        <div style={{ color: 'var(--warning)', fontSize: '13px' }}>
                            {t('enterprise.llm.noRuntimeModelCandidates', '暂无可用模型，请先为当前公司或平台创建模型并完成 Agent 兼容性测试。')}
                        </div>
                    ) : (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr)) auto', gap: '12px', alignItems: 'end' }}>
                            <label className="form-group" style={{ margin: 0 }}>
                                <span className="form-label">{t('enterprise.llm.planningModel', '群聊规划模型')}</span>
                                <select
                                    className="form-input"
                                    value={runtimeModelForm.planning_model_id}
                                    onChange={(event) => setRuntimeModelForm((current) => ({ ...current, planning_model_id: event.target.value }))}
                                >
                                    <option value="" disabled>{t('enterprise.llm.selectRuntimeModel', '请选择模型')}</option>
                                    {runtimeModelSettings.candidates.map((model) => (
                                        <option key={model.id} value={model.id}>{model.label} · {model.provider}/{model.model}</option>
                                    ))}
                                </select>
                            </label>
                            <label className="form-group" style={{ margin: 0 }}>
                                <span className="form-label">{t('enterprise.llm.compactModel', '群聊上下文模型')}</span>
                                <select
                                    className="form-input"
                                    value={runtimeModelForm.compact_model_id}
                                    onChange={(event) => setRuntimeModelForm((current) => ({ ...current, compact_model_id: event.target.value }))}
                                >
                                    <option value="" disabled>{t('enterprise.llm.selectRuntimeModel', '请选择模型')}</option>
                                    {runtimeModelSettings.candidates.map((model) => (
                                        <option key={model.id} value={model.id}>{model.label} · {model.provider}/{model.model}</option>
                                    ))}
                                </select>
                            </label>
                            <button
                                className="btn btn-primary"
                                disabled={
                                    saveRuntimeModelSettings.isPending
                                    || !runtimeModelForm.planning_model_id
                                    || !runtimeModelForm.compact_model_id
                                }
                                onClick={() => saveRuntimeModelSettings.mutate()}
                            >
                                {saveRuntimeModelSettings.isPending
                                    ? t('common.saving', '保存中...')
                                    : t('common.save', '保存')}
                            </button>
                        </div>
                    )}
                </div>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '16px' }}>
                <button className="btn btn-primary" onClick={openCreateForm}>+ {t('enterprise.llm.addModel')}</button>
            </div>

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
                                updates.base_url = spec?.default_base_url || '';
                                if (spec) updates.max_output_tokens = String(spec.default_max_tokens);
                                setModelForm(f => ({ ...f, ...updates }));
                            }}>
                                {providerOptions.map((p) => (
                                    <option key={p.provider} value={p.provider}>{p.display_name}</option>
                                ))}
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label">{t('enterprise.llm.model')}</label>
                            <input className="form-input" placeholder={t('enterprise.llm.modelPlaceholder', 'e.g. claude-sonnet-4-20250514')} value={modelForm.model} onChange={e => setModelForm({ ...modelForm, model: e.target.value })} />
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
                        <button className="btn btn-secondary" style={{ display: 'flex', alignItems: 'center', gap: '6px' }} disabled={!modelForm.model || !modelForm.api_key} onClick={() => runConnectivityTest(null, true)}>{t('enterprise.llm.test')}</button>
                        <button className="btn btn-primary" onClick={() => {
                            addModel.mutate({
                                ...modelForm,
                                max_output_tokens: modelForm.max_output_tokens ? Number(modelForm.max_output_tokens) : null,
                                request_timeout: modelForm.request_timeout ? Number(modelForm.request_timeout) : null,
                                temperature: modelForm.temperature !== '' ? Number(modelForm.temperature) : null,
                            });
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
                                        <input className="form-input" placeholder={t('enterprise.llm.modelPlaceholder', 'e.g. claude-sonnet-4-20250514')} value={modelForm.model} onChange={e => setModelForm({ ...modelForm, model: e.target.value })} />
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
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">{t('enterprise.llm.requestTimeout', 'Request Timeout (s)')}</label>
                                        <input className="form-input" type="number" min="1" placeholder={t('enterprise.llm.requestTimeoutPlaceholder', 'e.g. 120 (Leave empty for default)')} value={modelForm.request_timeout} onChange={e => setModelForm({ ...modelForm, request_timeout: e.target.value })} />
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">{t('enterprise.llm.temperature', 'Temperature')}</label>
                                        <input className="form-input" type="number" step="0.1" min="0" max="2" placeholder={t('enterprise.llm.temperaturePlaceholder', 'e.g. 0.7 or 1.0 (Leave empty for default)')} value={modelForm.temperature} onChange={e => setModelForm({ ...modelForm, temperature: e.target.value })} />
                                    </div>
                                </div>
                                <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', alignItems: 'center' }}>
                                    <button className="btn btn-secondary" onClick={() => { setShowAddModel(false); setEditingModelId(null); }}>{t('common.cancel')}</button>
                                    <button className="btn btn-secondary" style={{ display: 'flex', alignItems: 'center', gap: '6px' }} disabled={!modelForm.model} onClick={() => runConnectivityTest(editingModelId, false)}>{t('enterprise.llm.test')}</button>
                                    <button className="btn btn-primary" onClick={() => {
                                        updateModel.mutate({
                                            id: editingModelId!,
                                            data: {
                                                ...modelForm,
                                                max_output_tokens: modelForm.max_output_tokens ? Number(modelForm.max_output_tokens) : null,
                                                request_timeout: modelForm.request_timeout ? Number(modelForm.request_timeout) : null,
                                                temperature: modelForm.temperature !== '' ? Number(modelForm.temperature) : null,
                                            },
                                        });
                                    }} disabled={!modelForm.model}>
                                        {t('common.save')}
                                    </button>
                                </div>
                            </div>
                        ) : (
                            <div className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                <div>
                                    <div style={{ fontWeight: 500 }}>{m.label}</div>
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                                        {m.provider}/{m.model}
                                        {m.base_url && <span> · {m.base_url}</span>}
                                    </div>
                                </div>
                                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                    <button
                                        onClick={async () => {
                                            try {
                                                const token = localStorage.getItem('token');
                                                await fetch(`/api/enterprise/llm-models/${m.id}`, {
                                                    method: 'PUT',
                                                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                                                    body: JSON.stringify({ enabled: !m.enabled }),
                                                });
                                                invalidateModelCaches();
                                            } catch (e) {
                                                console.error(e);
                                            }
                                        }}
                                        title={m.enabled ? t('enterprise.llm.clickToDisable', 'Click to disable') : t('enterprise.llm.clickToEnable', 'Click to enable')}
                                        style={{
                                            position: 'relative',
                                            width: '36px',
                                            height: '20px',
                                            borderRadius: '10px',
                                            border: 'none',
                                            cursor: 'pointer',
                                            transition: 'background 0.2s',
                                            background: m.enabled ? 'var(--accent-primary)' : 'var(--bg-tertiary, #444)',
                                            padding: 0,
                                            flexShrink: 0,
                                        }}
                                    >
                                        <span style={{
                                            position: 'absolute',
                                            left: m.enabled ? '18px' : '2px',
                                            top: '2px',
                                            width: '16px',
                                            height: '16px',
                                            borderRadius: '50%',
                                            background: '#fff',
                                            transition: 'left 0.2s',
                                            boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                                        }} />
                                    </button>
                                    {m.supports_vision && <span className="badge" style={{ background: 'rgba(99,102,241,0.15)', color: 'rgb(99,102,241)', fontSize: '10px' }}>Vision</span>}
                                    {m.supports_tool_calling === true ? (
                                        <span
                                            className="badge"
                                            title={m.tool_calling_capability_source === 'probe'
                                                ? t('enterprise.llm.toolsVerifiedTitle', 'This exact model configuration passed the native finish-tool probe.')
                                                : t('enterprise.llm.toolsRegistryTitle', 'Preserved from the built-in cloud provider registry; rerun the test to verify this exact configuration.')}
                                            style={{ background: 'rgba(34,197,94,0.15)', color: 'rgb(34,197,94)', fontSize: '10px' }}
                                        >
                                            {m.tool_calling_capability_source === 'probe'
                                                ? t('enterprise.llm.toolsVerified', 'Tools verified')
                                                : t('enterprise.llm.toolsRegistry', 'Tools registry')}
                                        </span>
                                    ) : m.supports_tool_calling === false ? (
                                        <span
                                            className="badge"
                                            title={m.tool_calling_error || t('enterprise.llm.toolsUnavailableTitle', 'The model connected but did not return a valid native finish tool call.')}
                                            style={{ background: 'rgba(239,68,68,0.15)', color: 'rgb(239,68,68)', fontSize: '10px' }}
                                        >
                                            {t('enterprise.llm.toolsUnavailable', 'Tools unavailable')}
                                        </span>
                                    ) : (
                                        <span
                                            className="badge"
                                            title={m.tool_calling_error || t('enterprise.llm.toolsUnverifiedTitle', 'Run the model test before using this model in Agent Runtime.')}
                                            style={{ background: 'rgba(245,158,11,0.15)', color: 'rgb(245,158,11)', fontSize: '10px' }}
                                        >
                                            {t('enterprise.llm.toolsUnverified', 'Tools unverified')}
                                        </span>
                                    )}
                                    {tenantForDefault?.default_model_id === m.id ? (
                                        <span className="badge" style={{ background: 'rgba(34,197,94,0.15)', color: 'rgb(34,197,94)', fontSize: '10px' }}>{t('enterprise.llm.defaultBadge', '默认')}</span>
                                    ) : m.enabled ? (
                                        <button className="btn btn-ghost" style={{ fontSize: '12px' }} onClick={() => setDefaultModel.mutate(m.id)} title={t('enterprise.llm.setAsDefaultTitle', 'Set as default for new agents')}>
                                            {t('enterprise.llm.setAsDefault', '设为默认')}
                                        </button>
                                    ) : null}
                                    <button className="btn btn-ghost" onClick={() => {
                                        setEditingModelId(m.id);
                                        setModelForm({
                                            provider: m.provider,
                                            model: m.model,
                                            label: m.label,
                                            base_url: m.base_url || '',
                                            api_key: m.api_key_masked || '',
                                            supports_vision: m.supports_vision || false,
                                            max_output_tokens: m.max_output_tokens ? String(m.max_output_tokens) : '',
                                            request_timeout: m.request_timeout ? String(m.request_timeout) : '',
                                            temperature: m.temperature !== null && m.temperature !== undefined ? String(m.temperature) : '',
                                        });
                                        setShowAddModel(true);
                                    }} style={{ fontSize: '12px', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                                        <IconEdit size={13} stroke={1.8} /> {t('enterprise.tools.edit')}
                                    </button>
                                    <button className="btn btn-ghost" onClick={() => deleteModel.mutate({ id: m.id })} style={{ color: 'var(--error)' }}>{t('common.delete')}</button>
                                </div>
                            </div>
                        )}
                    </div>
                ))}
                {models.length === 0 && <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>{t('common.noData')}</div>}
            </div>
        </div>
    );
}
