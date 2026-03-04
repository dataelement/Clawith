import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { enterpriseApi, skillApi } from '../services/api';
import PromptModal from '../components/PromptModal';
import FileBrowser from '../components/FileBrowser';
import type { FileBrowserApi } from '../components/FileBrowser';
import { saveAccentColor, getSavedAccentColor, resetAccentColor, PRESET_COLORS } from '../utils/theme';
import UserManagement from './UserManagement';

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
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Error');
    if (res.status === 204) return undefined as T;
    return res.json();
}

interface LLMModel {
    id: string; provider: string; model: string; label: string;
    base_url?: string; max_tokens_per_day?: number; enabled: boolean; created_at: string;
}



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
                            padding: '5px 8px', paddingLeft: `${8 + level * 16}px`, borderRadius: '4px',
                            cursor: 'pointer', fontSize: '13px', marginBottom: '1px',
                            background: selectedDept === d.id ? 'rgba(224,238,238,0.12)' : 'transparent',
                        }}
                        onClick={() => onSelect(d.id)}
                    >
                        <span style={{ color: 'var(--text-tertiary)', marginRight: '4px', fontSize: '11px' }}>
                            {departments.some((c: any) => c.parent_id === d.id) ? '▸' : '·'}
                        </span>
                        {d.name}
                        {d.member_count > 0 && <span style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginLeft: '4px' }}>({d.member_count})</span>}
                    </div>
                    <DeptTree departments={departments} parentId={d.id} selectedDept={selectedDept} onSelect={onSelect} level={level + 1} />
                </div>
            ))}
        </>
    );
}

// ─── Org Structure Tab ─────────────────────────────
function OrgTab() {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const [syncForm, setSyncForm] = useState({ app_id: '', app_secret: '' });
    const [syncing, setSyncing] = useState(false);
    const [syncResult, setSyncResult] = useState<any>(null);
    const [memberSearch, setMemberSearch] = useState('');
    const [selectedDept, setSelectedDept] = useState<string | null>(null);

    const { data: config } = useQuery({
        queryKey: ['system-settings', 'feishu_org_sync'],
        queryFn: () => fetchJson<any>('/enterprise/system-settings/feishu_org_sync'),
    });

    useEffect(() => {
        if (config?.value?.app_id) {
            setSyncForm({ app_id: config.value.app_id, app_secret: '' });
        }
    }, [config]);

    const currentTenantId = localStorage.getItem('current_tenant_id') || '';
    const { data: departments = [] } = useQuery({
        queryKey: ['org-departments', currentTenantId],
        queryFn: () => fetchJson<any[]>(`/enterprise/org/departments${currentTenantId ? `?tenant_id=${currentTenantId}` : ''}`),
    });
    const { data: members = [] } = useQuery({
        queryKey: ['org-members', selectedDept, memberSearch, currentTenantId],
        queryFn: () => {
            const params = new URLSearchParams();
            if (selectedDept) params.set('department_id', selectedDept);
            if (memberSearch) params.set('search', memberSearch);
            if (currentTenantId) params.set('tenant_id', currentTenantId);
            return fetchJson<any[]>(`/enterprise/org/members?${params}`);
        },
    });

    const saveConfig = async () => {
        await fetchJson('/enterprise/system-settings/feishu_org_sync', {
            method: 'PUT',
            body: JSON.stringify({ value: { app_id: syncForm.app_id, app_secret: syncForm.app_secret } }),
        });
        qc.invalidateQueries({ queryKey: ['system-settings', 'feishu_org_sync'] });
    };

    const triggerSync = async () => {
        setSyncing(true);
        setSyncResult(null);
        try {
            if (syncForm.app_secret) await saveConfig();
            const result = await fetchJson<any>('/enterprise/org/sync', { method: 'POST' });
            setSyncResult(result);
            qc.invalidateQueries({ queryKey: ['org-departments'] });
            qc.invalidateQueries({ queryKey: ['org-members'] });
        } catch (e: any) {
            setSyncResult({ error: e.message });
        }
        setSyncing(false);
    };

    return (
        <div>
            {/* Sync Config */}
            <div className="card" style={{ marginBottom: '16px' }}>
                <h4 style={{ marginBottom: '12px' }}>{t('enterprise.org.feishuSync')}</h4>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                    {t('enterprise.org.feishuSync')}
                </p>
                <div style={{ display: 'flex', gap: '12px', marginBottom: '12px' }}>
                    <div style={{ flex: 1 }}>
                        <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>App ID</label>
                        <input className="input" value={syncForm.app_id} onChange={e => setSyncForm({ ...syncForm, app_id: e.target.value })} placeholder="cli_xxxxxxxx" />
                    </div>
                    <div style={{ flex: 1 }}>
                        <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>App Secret</label>
                        <input className="input" type="password" value={syncForm.app_secret} onChange={e => setSyncForm({ ...syncForm, app_secret: e.target.value })} placeholder={config?.value?.app_id ? '' : ''} />
                    </div>
                </div>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <button className="btn btn-primary" onClick={triggerSync} disabled={syncing || !syncForm.app_id}>
                        {syncing ? t('enterprise.org.syncing') : t('enterprise.org.syncNow')}
                    </button>
                    {config?.value?.last_synced_at && (
                        <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                            Last sync: {new Date(config.value.last_synced_at).toLocaleString()}
                        </span>
                    )}
                </div>
                {syncResult && (
                    <div style={{ marginTop: '12px', padding: '8px 12px', borderRadius: '6px', fontSize: '12px', background: syncResult.error ? 'rgba(255,0,0,0.1)' : 'rgba(0,200,0,0.1)' }}>
                        {syncResult.error ? `${syncResult.error}` : t('enterprise.org.syncComplete', { departments: syncResult.departments, members: syncResult.members })}
                    </div>
                )}
            </div>

            {/* Department & Members Browser */}
            <div className="card">
                <h4 style={{ marginBottom: '12px' }}>{t('enterprise.org.orgBrowser')}</h4>
                <div style={{ display: 'flex', gap: '16px' }}>
                    <div style={{ width: '260px', borderRight: '1px solid var(--border-subtle)', paddingRight: '16px', maxHeight: '500px', overflowY: 'auto' }}>
                        <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-secondary)' }}>{t('enterprise.org.allDepartments')}</div>
                        <div
                            style={{ padding: '6px 8px', borderRadius: '4px', cursor: 'pointer', fontSize: '13px', marginBottom: '2px', background: !selectedDept ? 'rgba(224,238,238,0.1)' : 'transparent' }}
                            onClick={() => setSelectedDept(null)}
                        >
                            {t('common.all')}
                        </div>
                        <DeptTree departments={departments} parentId={null} selectedDept={selectedDept} onSelect={setSelectedDept} level={0} />
                        {departments.length === 0 && <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', padding: '8px' }}>{t('common.noData')}</div>}
                    </div>

                    <div style={{ flex: 1 }}>
                        <input className="input" placeholder={t("enterprise.org.searchMembers")} value={memberSearch} onChange={e => setMemberSearch(e.target.value)} style={{ marginBottom: '12px', fontSize: '13px' }} />
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', maxHeight: '400px', overflowY: 'auto' }}>
                            {members.map((m: any) => (
                                <div key={m.id} style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '8px', borderRadius: '6px', border: '1px solid var(--border-subtle)' }}>
                                    <div style={{ width: '32px', height: '32px', borderRadius: '50%', background: 'rgba(224,238,238,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '14px', fontWeight: 600 }}>
                                        {m.name?.[0] || '?'}
                                    </div>
                                    <div>
                                        <div style={{ fontWeight: 500, fontSize: '13px' }}>{m.name}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                            {m.title || '-'} · {m.department_path || '-'}
                                            {m.email && ` · ${m.email}`}
                                        </div>
                                    </div>
                                </div>
                            ))}
                            {members.length === 0 && <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-tertiary)', fontSize: '13px' }}>{t('enterprise.org.noMembers')}</div>}
                        </div>
                    </div>
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

// ─── Platform Settings ─────────────────────────────
function PlatformSettings() {
    const { t } = useTranslation();
    const [publicBaseUrl, setPublicBaseUrl] = useState('');
    const [maxRounds, setMaxRounds] = useState(5);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        // Load platform settings
        fetchJson<any>('/enterprise/system-settings/platform')
            .then(d => {
                if (d.value?.public_base_url) setPublicBaseUrl(d.value.public_base_url);
            }).catch(() => { });
        fetchJson<any>('/enterprise/system-settings/agent_conversation')
            .then(d => {
                if (d.value?.max_rounds) setMaxRounds(d.value.max_rounds);
            }).catch(() => { });
    }, []);

    const handleSave = async () => {
        setSaving(true);
        try {
            await fetchJson('/enterprise/system-settings/platform', {
                method: 'PUT', body: JSON.stringify({ value: { public_base_url: publicBaseUrl } }),
            });
            await fetchJson('/enterprise/system-settings/agent_conversation', {
                method: 'PUT', body: JSON.stringify({ value: { max_rounds: Number(maxRounds) } }),
            });
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        } catch (e) {
            alert(t('agent.upload.failed'));
        } finally { setSaving(false); }
    };

    return (
        <div className="card" style={{ padding: '16px' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
                <div className="form-group">
                    <label className="form-label">{t('enterprise.config.publicUrl')}</label>
                    <input className="form-input" value={publicBaseUrl} onChange={e => setPublicBaseUrl(e.target.value)}
                        placeholder={t("enterprise.config.publicUrlPlaceholder")} />
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                        {t('enterprise.config.publicUrl')}
                    </div>
                </div>
                <div className="form-group">
                    <label className="form-label">{t('enterprise.config.maxRounds')}</label>
                    <input className="form-input" type="number" min={1} max={20} value={maxRounds}
                        onChange={e => setMaxRounds(Number(e.target.value))} />
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                        {t('enterprise.config.maxRoundsDesc', 'Maximum number of conversation rounds between two agents in a single interaction. Controls how many back-and-forth messages agents can exchange when collaborating.')}
                    </div>
                </div>
            </div>
            <div style={{ marginTop: '12px', display: 'flex', gap: '8px', alignItems: 'center' }}>
                <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                    {saving ? t('common.loading') : t('enterprise.config.save')}
                </button>
                {saved && <span style={{ color: 'var(--success)', fontSize: '12px' }}>{t('enterprise.config.saved')}</span>}
            </div>
        </div>
    );
}


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

    const adapter: FileBrowserApi = {
        list: (path) => skillApi.browse.list(path),
        read: (path) => skillApi.browse.read(path),
        write: (path, content) => skillApi.browse.write(path, content),
        delete: (path) => skillApi.browse.delete(path),
    };

    return (
        <div>
            <div style={{ marginBottom: '12px' }}>
                <h3>{t('enterprise.tabs.skills', 'Skill Registry')}</h3>
                <p style={{ fontSize: '13px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                    Manage global skills. Each skill is a folder with a SKILL.md file. Skills selected during agent creation are copied to the agent's workspace.
                </p>
            </div>
            <FileBrowser
                key={refreshKey}
                api={adapter}
                features={{ newFile: true, newFolder: true, edit: true, delete: true, directoryNavigation: true }}
                title={t('agent.skills.skillFiles', 'Skill Files')}
                onRefresh={() => setRefreshKey(k => k + 1)}
            />
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


export default function EnterpriseSettings() {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const [activeTab, setActiveTab] = useState<'llm' | 'org' | 'info' | 'approvals' | 'audit' | 'tools' | 'skills' | 'quotas' | 'users'>('info');

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

    // Load Company Intro
    useEffect(() => {
        fetchJson<any>('/enterprise/system-settings/company_intro')
            .then(d => { if (d?.value?.content) setCompanyIntro(d.value.content); })
            .catch(() => { });
    }, []);

    const saveCompanyIntro = async () => {
        setCompanyIntroSaving(true);
        try {
            await fetchJson('/enterprise/system-settings/company_intro', {
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

    // ─── Tools
    const [allTools, setAllTools] = useState<any[]>([]);
    const [showAddMCP, setShowAddMCP] = useState(false);
    const [mcpForm, setMcpForm] = useState({ server_url: '', server_name: '' });
    const [mcpTestResult, setMcpTestResult] = useState<any>(null);
    const [mcpTesting, setMcpTesting] = useState(false);
    const [editingToolId, setEditingToolId] = useState<string | null>(null);
    const [editingConfig, setEditingConfig] = useState<Record<string, any>>({});
    const loadAllTools = async () => {
        const data = await fetchJson<any[]>('/tools');
        setAllTools(data);
    };
    useEffect(() => { if (activeTab === 'tools') loadAllTools(); }, [activeTab]);

    // ─── Stats (scoped to selected tenant)
    const { data: stats } = useQuery({
        queryKey: ['enterprise-stats', selectedTenantId],
        queryFn: () => fetchJson<any>(`/enterprise/stats${selectedTenantId ? `?tenant_id=${selectedTenantId}` : ''}`),
    });

    // ─── LLM Models
    const { data: models = [] } = useQuery({
        queryKey: ['llm-models'],
        queryFn: () => fetchJson<LLMModel[]>('/enterprise/llm-models'),
        enabled: activeTab === 'llm',
    });
    const [showAddModel, setShowAddModel] = useState(false);
    const [editingModelId, setEditingModelId] = useState<string | null>(null);
    const [modelForm, setModelForm] = useState({ provider: 'anthropic', model: '', api_key: '', base_url: '', label: '' });
    const addModel = useMutation({
        mutationFn: (data: any) => fetchJson('/enterprise/llm-models', { method: 'POST', body: JSON.stringify(data) }),
        onSuccess: () => { qc.invalidateQueries({ queryKey: ['llm-models'] }); setShowAddModel(false); setEditingModelId(null); },
    });
    const updateModel = useMutation({
        mutationFn: ({ id, data }: { id: string; data: any }) => fetchJson(`/enterprise/llm-models/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
        onSuccess: () => { qc.invalidateQueries({ queryKey: ['llm-models'] }); setShowAddModel(false); setEditingModelId(null); },
    });
    const deleteModel = useMutation({
        mutationFn: (id: string) => fetchJson(`/enterprise/llm-models/${id}`, { method: 'DELETE' }),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['llm-models'] }),
    });

    // ─── Approvals
    const { data: approvals = [] } = useQuery({
        queryKey: ['approvals'],
        queryFn: () => fetchJson<any[]>('/enterprise/approvals'),
        enabled: activeTab === 'approvals',
    });
    const resolveApproval = useMutation({
        mutationFn: ({ id, action }: { id: string; action: string }) =>
            fetchJson(`/enterprise/approvals/${id}/resolve`, { method: 'POST', body: JSON.stringify({ action }) }),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals'] }),
    });

    // ─── Audit Logs
    const BG_ACTIONS = ['supervision_tick', 'supervision_fire', 'supervision_error', 'schedule_tick', 'schedule_fire', 'schedule_error', 'heartbeat_tick', 'heartbeat_fire', 'heartbeat_error', 'server_startup'];
    const { data: auditLogs = [] } = useQuery({
        queryKey: ['audit-logs'],
        queryFn: () => fetchJson<any[]>('/enterprise/audit-logs?limit=200'),
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
                                {stats.pending_approvals > 0 && <span className="badge badge-warning">⏳ {stats.pending_approvals} {t('enterprise.tabs.approval')}</span>}
                            </div>
                        )}
                    </div>
                </div>

                <div className="tabs">
                    {(['info', 'llm', 'tools', 'skills', 'quotas', 'users', 'org', 'approvals', 'audit'] as const).map(tab => (
                        <div key={tab} className={`tab ${activeTab === tab ? 'active' : ''}`} onClick={() => setActiveTab(tab)}>
                            {tab === 'quotas' ? 'Quotas' : tab === 'users' ? 'Users' : t(`enterprise.tabs.${tab}`)}
                        </div>
                    ))}
                </div>

                {/* ── LLM Model Pool ── */}
                {activeTab === 'llm' && (
                    <div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '16px' }}>
                            <button className="btn btn-primary" onClick={() => { setEditingModelId(null); setModelForm({ provider: 'anthropic', model: '', api_key: '', base_url: '', label: '' }); setShowAddModel(true); }}>+ {t('enterprise.llm.addModel')}</button>
                        </div>

                        {showAddModel && (
                            <div className="card" style={{ marginBottom: '16px' }}>
                                <h3 style={{ marginBottom: '16px' }}>{editingModelId ? 'Edit Model' : t('enterprise.llm.addModel')}</h3>
                                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                                    <div className="form-group">
                                        <label className="form-label">Provider</label>
                                        <select className="form-input" value={modelForm.provider} onChange={e => setModelForm({ ...modelForm, provider: e.target.value })}>
                                            <option value="anthropic">Anthropic</option>
                                            <option value="openai">OpenAI</option>
                                            <option value="deepseek">DeepSeek</option>
                                            <option value="qwen">Qwen (DashScope)</option>
                                            <option value="zhipu">Zhipu</option>
                                            <option value="openrouter">OpenRouter</option>
                                            <option value="custom">Custom</option>
                                        </select>
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">Model</label>
                                        <input className="form-input" placeholder="claude-sonnet-4-5" value={modelForm.model} onChange={e => setModelForm({ ...modelForm, model: e.target.value })} />
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">{t('enterprise.llm.label')}</label>
                                        <input className="form-input" placeholder="Claude Sonnet" value={modelForm.label} onChange={e => setModelForm({ ...modelForm, label: e.target.value })} />
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">{t('enterprise.llm.baseUrl')}</label>
                                        <input className="form-input" placeholder="https://api.custom.com/v1" value={modelForm.base_url} onChange={e => setModelForm({ ...modelForm, base_url: e.target.value })} />
                                    </div>
                                    <div className="form-group" style={{ gridColumn: 'span 2' }}>
                                        <label className="form-label">API Key</label>
                                        <input className="form-input" type="password" value={modelForm.api_key} onChange={e => setModelForm({ ...modelForm, api_key: e.target.value })} />
                                    </div>
                                </div>
                                <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                                    <button className="btn btn-secondary" onClick={() => { setShowAddModel(false); setEditingModelId(null); }}>{t('common.cancel')}</button>
                                    <button className="btn btn-primary" onClick={() => {
                                        if (editingModelId) {
                                            updateModel.mutate({ id: editingModelId, data: modelForm });
                                        } else {
                                            addModel.mutate(modelForm);
                                        }
                                    }} disabled={!modelForm.model || (!editingModelId && !modelForm.api_key)}>
                                        {t('common.save')}
                                    </button>
                                </div>
                            </div>
                        )}

                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                            {models.map((m) => (
                                <div key={m.id} className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                    <div>
                                        <div style={{ fontWeight: 500 }}>{m.label}</div>
                                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                                            {m.provider}/{m.model}
                                            {m.base_url && <span> · {m.base_url}</span>}
                                        </div>
                                    </div>
                                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                        <span className={`badge ${m.enabled ? 'badge-success' : 'badge-warning'}`}>
                                            {m.enabled ? t('enterprise.llm.enabled') : t('enterprise.llm.disabled')}
                                        </span>
                                        <button className="btn btn-ghost" onClick={() => {
                                            setEditingModelId(m.id);
                                            setModelForm({ provider: m.provider, model: m.model, label: m.label, base_url: m.base_url || '', api_key: '' });
                                            setShowAddModel(true);
                                        }} style={{ fontSize: '12px' }}>✏️ Edit</button>
                                        <button className="btn btn-ghost" onClick={() => deleteModel.mutate(m.id)} style={{ color: 'var(--error)' }}>{t('common.delete')}</button>
                                    </div>
                                </div>
                            ))}
                            {models.length === 0 && <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>{t('common.noData')}</div>}
                        </div>
                    </div>
                )}

                {/* ── Org Structure ── */}
                {activeTab === 'org' && <OrgTab />}

                {/* ── Approvals ── */}
                {activeTab === 'approvals' && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {approvals.map((a: any) => (
                            <div key={a.id} className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                <div>
                                    <div style={{ fontWeight: 500 }}>{a.action_type}</div>
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                                        Agent: {a.agent_id.slice(0, 8)} · {new Date(a.created_at).toLocaleString()}
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
                            {([['all', '📋 全部'], ['background', '⚙️ 后台服务'], ['actions', '👤 用户操作']] as const).map(([key, label]) => (
                                <button key={key}
                                    onClick={() => setAuditFilter(key as any)}
                                    style={{
                                        padding: '4px 14px', borderRadius: '12px', fontSize: '12px', fontWeight: 500,
                                        border: auditFilter === key ? '1px solid var(--accent-color)' : '1px solid var(--border-color)',
                                        background: auditFilter === key ? 'var(--accent-color)' : 'transparent',
                                        color: auditFilter === key ? '#fff' : 'var(--text-secondary)',
                                        cursor: 'pointer', transition: 'all 0.15s',
                                    }}
                                >{label}</button>
                            ))}
                            <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--text-tertiary)', alignSelf: 'center' }}>
                                {filteredAuditLogs.length} 条记录
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
                        <CompanyNameEditor />

                        {/* ── 1. Company Intro ── */}
                        <h3 style={{ marginBottom: '8px' }}>{t('enterprise.companyIntro.title', 'Company Intro')}</h3>
                        <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                            {t('enterprise.companyIntro.description', 'Describe your company\'s mission, products, and culture. This information is included in every agent conversation as context.')}
                        </p>
                        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
                            <textarea
                                className="form-input"
                                value={companyIntro}
                                onChange={e => setCompanyIntro(e.target.value)}
                                placeholder={`# Company Name\n\n## About Us\nDescribe your company here...\n\n## Products & Services\n- Product A\n- Product B\n\n## Culture & Values\n- Value 1\n- Value 2`}
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

                        {/* ── 3. Platform Configuration ── */}
                        <h3 style={{ marginBottom: '8px' }}>{t('enterprise.config.title')}</h3>
                        <PlatformSettings />

                        {/* ── Theme Color ── */}
                        <ThemeColorPicker />
                    </div>
                )}

                {/* ── Quotas Tab ── */}
                {activeTab === 'quotas' && (
                    <div>
                        <h3 style={{ marginBottom: '4px' }}>Default User Quotas</h3>
                        <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                            These defaults apply to newly registered users. Existing users are not affected.
                        </p>
                        <div className="card" style={{ padding: '16px' }}>
                            {/* ── Conversation Limits ── */}
                            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '10px' }}>Conversation Limits</div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '20px' }}>
                                <div className="form-group">
                                    <label className="form-label">Message Limit</label>
                                    <input className="form-input" type="number" min={0} value={quotaForm.default_message_limit}
                                        onChange={e => setQuotaForm({ ...quotaForm, default_message_limit: Number(e.target.value) })} />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>Max messages per period</div>
                                </div>
                                <div className="form-group">
                                    <label className="form-label">Message Period</label>
                                    <select className="form-input" value={quotaForm.default_message_period}
                                        onChange={e => setQuotaForm({ ...quotaForm, default_message_period: e.target.value })}>
                                        <option value="permanent">Permanent</option>
                                        <option value="daily">Daily</option>
                                        <option value="weekly">Weekly</option>
                                        <option value="monthly">Monthly</option>
                                    </select>
                                </div>
                            </div>

                            {/* ── Agent Limits ── */}
                            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '10px' }}>Agent Limits</div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px', marginBottom: '20px' }}>
                                <div className="form-group">
                                    <label className="form-label">Max Agents</label>
                                    <input className="form-input" type="number" min={0} value={quotaForm.default_max_agents}
                                        onChange={e => setQuotaForm({ ...quotaForm, default_max_agents: Number(e.target.value) })} />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>Agents a user can create</div>
                                </div>
                                <div className="form-group">
                                    <label className="form-label">Agent TTL (hours)</label>
                                    <input className="form-input" type="number" min={1} value={quotaForm.default_agent_ttl_hours}
                                        onChange={e => setQuotaForm({ ...quotaForm, default_agent_ttl_hours: Number(e.target.value) })} />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>Agent auto-expiry time from creation</div>
                                </div>
                                <div className="form-group">
                                    <label className="form-label">Daily LLM Calls / Agent</label>
                                    <input className="form-input" type="number" min={0} value={quotaForm.default_max_llm_calls_per_day}
                                        onChange={e => setQuotaForm({ ...quotaForm, default_max_llm_calls_per_day: Number(e.target.value) })} />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>Max LLM calls per agent per day</div>
                                </div>
                            </div>

                            {/* ── System Limits ── */}
                            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '10px' }}>System</div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px' }}>
                                <div className="form-group">
                                    <label className="form-label">Min Heartbeat Interval (min)</label>
                                    <input className="form-input" type="number" min={1} value={quotaForm.min_heartbeat_interval_minutes}
                                        onChange={e => setQuotaForm({ ...quotaForm, min_heartbeat_interval_minutes: Number(e.target.value) })} />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>Minimum heartbeat interval for all agents</div>
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
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                            <h3>{t('enterprise.tools.title')}</h3>
                            <button className="btn btn-primary" onClick={() => setShowAddMCP(true)}>+ MCP Server</button>
                        </div>

                        {showAddMCP && (
                            <div className="card" style={{ padding: '16px', marginBottom: '16px' }}>
                                <h4 style={{ marginBottom: '12px' }}>MCP Server</h4>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                    <div>
                                        <label style={{ display: 'block', fontSize: '12px', marginBottom: '4px' }}>{t('enterprise.tools.mcpServerName')}</label>
                                        <input className="form-input" value={mcpForm.server_name} onChange={e => setMcpForm(p => ({ ...p, server_name: e.target.value }))} placeholder="My MCP Server" />
                                    </div>
                                    <div>
                                        <label style={{ display: 'block', fontSize: '12px', marginBottom: '4px' }}>MCP Server URL</label>
                                        <input className="form-input" value={mcpForm.server_url} onChange={e => setMcpForm(p => ({ ...p, server_url: e.target.value }))} placeholder="http://localhost:3000/mcp" />
                                    </div>
                                    <div style={{ display: 'flex', gap: '8px' }}>
                                        <button className="btn btn-secondary" disabled={mcpTesting || !mcpForm.server_url} onClick={async () => {
                                            setMcpTesting(true); setMcpTestResult(null);
                                            try {
                                                const r = await fetchJson<any>('/tools/test-mcp', { method: 'POST', body: JSON.stringify({ server_url: mcpForm.server_url }) });
                                                setMcpTestResult(r);
                                            } catch (e: any) { setMcpTestResult({ ok: false, error: e.message }); }
                                            setMcpTesting(false);
                                        }}>{mcpTesting ? t('enterprise.tools.testing') : t('enterprise.tools.testConnection')}</button>
                                        <button className="btn btn-secondary" onClick={() => { setShowAddMCP(false); setMcpTestResult(null); }}>{t('common.cancel')}</button>
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
                                                            <button className="btn btn-primary" style={{ padding: '4px 10px', fontSize: '11px' }} onClick={async () => {
                                                                await fetchJson('/tools', {
                                                                    method: 'POST', body: JSON.stringify({
                                                                        name: `mcp_${tool.name}`,
                                                                        display_name: tool.name,
                                                                        description: tool.description || '',
                                                                        type: 'mcp',
                                                                        category: 'custom',
                                                                        icon: '·',
                                                                        mcp_server_url: mcpForm.server_url,
                                                                        mcp_server_name: mcpForm.server_name || mcpForm.server_url,
                                                                        mcp_tool_name: tool.name,
                                                                        parameters_schema: tool.inputSchema || {},
                                                                        is_default: false,
                                                                    })
                                                                });
                                                                loadAllTools();
                                                            }}>{t('enterprise.tools.importAll')}</button>
                                                        </div>
                                                    ))}
                                                </div>
                                            ) : (
                                                <div style={{ color: 'var(--danger)' }}>{t('enterprise.tools.connectionFailed')}: {mcpTestResult.error}</div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}

                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                            {allTools.map((tool: any) => {
                                const hasConfig = tool.config_schema?.fields?.length > 0;
                                const isEditing = editingToolId === tool.id;
                                return (
                                    <div key={tool.id} className="card" style={{ padding: '0', overflow: 'hidden' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px' }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flex: 1 }}>
                                                <span style={{ fontSize: '20px' }}>{tool.icon}</span>
                                                <div>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                        <span style={{ fontWeight: 500, fontSize: '13px' }}>{tool.display_name}</span>
                                                        <span style={{ fontSize: '10px', background: tool.type === 'mcp' ? 'var(--primary)' : 'var(--bg-tertiary)', color: tool.type === 'mcp' ? '#fff' : 'var(--text-secondary)', borderRadius: '4px', padding: '1px 5px' }}>
                                                            {tool.type === 'mcp' ? 'MCP' : 'Built-in'}
                                                        </span>
                                                        {tool.is_default && <span style={{ fontSize: '10px', background: 'rgba(0,200,100,0.15)', color: 'var(--success)', borderRadius: '4px', padding: '1px 5px' }}>Default</span>}
                                                    </div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                        {tool.description?.slice(0, 60)}
                                                        {tool.mcp_server_name && <span> · {tool.mcp_server_name}</span>}
                                                    </div>
                                                </div>
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                {hasConfig && (
                                                    <button className="btn btn-secondary" style={{ padding: '4px 8px', fontSize: '11px' }} onClick={() => {
                                                        if (isEditing) { setEditingToolId(null); } else { setEditingToolId(tool.id); setEditingConfig({ ...tool.config }); }
                                                    }}>{isEditing ? t('enterprise.tools.collapse') : t('enterprise.tools.configure')}</button>
                                                )}
                                                {tool.type !== 'builtin' && (
                                                    <button className="btn btn-danger" style={{ padding: '4px 8px', fontSize: '11px' }} onClick={async () => {
                                                        if (!confirm(`${t('common.delete')} ${tool.display_name}?`)) return;
                                                        await fetchJson(`/tools/${tool.id}`, { method: 'DELETE' });
                                                        loadAllTools();
                                                    }}>{t('common.delete')}</button>
                                                )}
                                                <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer' }}>
                                                    <input type="checkbox" checked={tool.enabled} onChange={async (e) => {
                                                        await fetchJson(`/tools/${tool.id}`, { method: 'PUT', body: JSON.stringify({ enabled: e.target.checked }) });
                                                        loadAllTools();
                                                    }} style={{ opacity: 0, width: 0, height: 0 }} />
                                                    <span style={{ position: 'absolute', inset: 0, background: tool.enabled ? '#22c55e' : 'var(--bg-tertiary)', borderRadius: '11px', transition: 'background 0.2s' }}>
                                                        <span style={{ position: 'absolute', left: tool.enabled ? '20px' : '2px', top: '2px', width: '18px', height: '18px', background: '#fff', borderRadius: '50%', transition: 'left 0.2s' }} />
                                                    </span>
                                                </label>
                                            </div>
                                        </div>

                                        {/* Config editing form */}
                                        {isEditing && hasConfig && (
                                            <div style={{ borderTop: '1px solid var(--border-color)', padding: '16px', background: 'var(--bg-secondary)' }}>
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
                                                                {field.type === 'select' ? (
                                                                    <select className="form-input" value={editingConfig[field.key] ?? field.default ?? ''} onChange={e => setEditingConfig(p => ({ ...p, [field.key]: e.target.value }))}>
                                                                        {(field.options || []).map((opt: any) => (
                                                                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                                                                        ))}
                                                                    </select>
                                                                ) : field.type === 'number' ? (
                                                                    <input type="number" className="form-input" value={editingConfig[field.key] ?? field.default ?? ''} min={field.min} max={field.max}
                                                                        onChange={e => setEditingConfig(p => ({ ...p, [field.key]: Number(e.target.value) }))} />
                                                                ) : field.type === 'password' ? (
                                                                    <input type="password" className="form-input" value={editingConfig[field.key] ?? ''} placeholder={field.placeholder || ''}
                                                                        onChange={e => setEditingConfig(p => ({ ...p, [field.key]: e.target.value }))} />
                                                                ) : (
                                                                    <input type="text" className="form-input" value={editingConfig[field.key] ?? field.default ?? ''} placeholder={field.placeholder || ''}
                                                                        onChange={e => setEditingConfig(p => ({ ...p, [field.key]: e.target.value }))} />
                                                                )}
                                                            </div>
                                                        );
                                                    })}
                                                    <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                                                        <button className="btn btn-primary" onClick={async () => {
                                                            await fetchJson(`/tools/${tool.id}`, { method: 'PUT', body: JSON.stringify({ config: editingConfig }) });
                                                            setEditingToolId(null);
                                                            loadAllTools();
                                                        }}>{t('enterprise.tools.saveConfig')}</button>
                                                        <button className="btn btn-secondary" onClick={() => setEditingToolId(null)}>{t('common.cancel')}</button>
                                                    </div>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                );
                            })}
                            {allTools.length === 0 && <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>{t('common.noData')}。请先启动后端以初始化内置工具。</div>}
                        </div>
                    </div>
                )}

                {/* ── Skills Tab ── */}
                {activeTab === 'skills' && <SkillsTab />}
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
