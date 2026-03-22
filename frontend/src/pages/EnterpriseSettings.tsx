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
    base_url?: string; api_key_masked?: string; max_tokens_per_day?: number; enabled: boolean; supports_vision?: boolean; max_output_tokens?: number; created_at: string;
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
                            <circle cx="12" cy="12" r="3"/>
                            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
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
                                {t('enterprise.tools.githubTokenStep1')}<br/>
                                {t('enterprise.tools.githubTokenStep2')}<br/>
                                {t('enterprise.tools.githubTokenStep3')}<br/>
                                {t('enterprise.tools.githubTokenStep4')}<br/>
                                {t('enterprise.tools.githubTokenStep5')}<br/>
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
                                        </div>
                                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: '1.4' }}>
                                            {r.summary?.slice(0, 160)}{r.summary?.length > 160 ? '...' : ''}
                                        </div>
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
    const [sending, setSending] = useState(false);
    const [result, setResult] = useState<{ users: number; agents: number } | null>(null);

    const handleSend = async () => {
        if (!title.trim()) return;
        setSending(true);
        setResult(null);
        try {
            const token = localStorage.getItem('token');
            const res = await fetch('/api/notifications/broadcast', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify({ title: title.trim(), body: body.trim() }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                alert(err.detail || 'Failed to send broadcast');
                setSending(false);
                return;
            }
            const data = await res.json();
            setResult({ users: data.users_notified, agents: data.agents_notified });
            setTitle('');
            setBody('');
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
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <button className="btn btn-primary" onClick={handleSend} disabled={sending || !title.trim()}>
                        {sending ? t('common.loading') : t('enterprise.broadcast.send', 'Send Broadcast')}
                    </button>
                    {result && (
                        <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            {t('enterprise.broadcast.sent', `Sent to ${result.users} users and ${result.agents} agents`, { users: result.users, agents: result.agents })}
                        </span>
                    )}
                </div>
            </div>
        </div>
    );
}


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
    const [mcpForm, setMcpForm] = useState({ server_url: '', server_name: '' });
    const [mcpRawInput, setMcpRawInput] = useState('');
    const [mcpTestResult, setMcpTestResult] = useState<any>(null);
    const [mcpTesting, setMcpTesting] = useState(false);
    const [editingToolId, setEditingToolId] = useState<string | null>(null);
    const [editingConfig, setEditingConfig] = useState<Record<string, any>>({});
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
    const [modelForm, setModelForm] = useState({ provider: 'anthropic', model: '', api_key: '', base_url: '', label: '', supports_vision: false, max_output_tokens: '' as string });
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
                                        <input className="form-input" placeholder={t('enterprise.llm.modelPlaceholder')} value={modelForm.model} onChange={e => setModelForm({ ...modelForm, model: e.target.value })} />
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
                                        <label className="form-label">{t('enterprise.llm.maxOutputTokens')}</label>
                                        <input className="form-input" type="number" placeholder={t('enterprise.llm.maxOutputTokensPlaceholder')} value={modelForm.max_output_tokens} onChange={e => setModelForm({ ...modelForm, max_output_tokens: e.target.value })} />
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('enterprise.llm.maxOutputTokensDesc')}</div>
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
                                        const data = { ...modelForm, max_output_tokens: modelForm.max_output_tokens ? Number(modelForm.max_output_tokens) : null };
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
                                                    <input className="form-input" placeholder={t('enterprise.llm.modelPlaceholder')} value={modelForm.model} onChange={e => setModelForm({ ...modelForm, model: e.target.value })} />
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
                                                    <label className="form-label">{t('enterprise.llm.maxOutputTokens')}</label>
                                                    <input className="form-input" type="number" placeholder={t('enterprise.llm.maxOutputTokensPlaceholder')} value={modelForm.max_output_tokens} onChange={e => setModelForm({ ...modelForm, max_output_tokens: e.target.value })} />
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('enterprise.llm.maxOutputTokensDesc')}</div>
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
                                                    const data = { ...modelForm, max_output_tokens: modelForm.max_output_tokens ? Number(modelForm.max_output_tokens) : null };
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
                                                <span className={`badge ${m.enabled ? 'badge-success' : 'badge-warning'}`}>
                                                    {m.enabled ? t('enterprise.llm.enabled') : t('enterprise.llm.disabled')}
                                                </span>
                                                {m.supports_vision && <span className="badge" style={{ background: 'rgba(99,102,241,0.15)', color: 'rgb(99,102,241)', fontSize: '10px' }}>👁 Vision</span>}
                                                <button className="btn btn-ghost" onClick={() => {
                                                    setEditingModelId(m.id);
                                                    setModelForm({ provider: m.provider, model: m.model, label: m.label, base_url: m.base_url || '', api_key: m.api_key_masked || '', supports_vision: m.supports_vision || false, max_output_tokens: m.max_output_tokens ? String(m.max_output_tokens) : '' });
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
                {activeTab === 'org' && <OrgTab />}

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
                                                        setMcpForm({ server_name: name, server_url: url });
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
                                        <div style={{ display: 'flex', gap: '8px' }}>
                                            <button className="btn btn-secondary" disabled={mcpTesting || !mcpForm.server_url} onClick={async () => {
                                                setMcpTesting(true); setMcpTestResult(null);
                                                try {
                                                    const r = await fetchJson<any>('/tools/test-mcp', { method: 'POST', body: JSON.stringify({ server_url: mcpForm.server_url }) });
                                                    setMcpTestResult(r);
                                                } catch (e: any) { setMcpTestResult({ ok: false, error: e.message }); }
                                                setMcpTesting(false);
                                            }}>{mcpTesting ? t('enterprise.tools.testing') : t('enterprise.tools.testConnection')}</button>
                                            <button className="btn btn-secondary" onClick={() => { setShowAddMCP(false); setMcpTestResult(null); setMcpForm({ server_url: '', server_name: '' }); setMcpRawInput(''); }}>{t('common.cancel')}</button>
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
                                                                    setShowAddMCP(false); setMcpTestResult(null); setMcpForm({ server_url: '', server_name: '' }); setMcpRawInput('');
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
                                                        <button className="btn btn-secondary" style={{ padding: '4px 8px', fontSize: '11px' }} onClick={async () => {
                                                            if (isEditing) {
                                                                setEditingToolId(null);
                                                            } else {
                                                                setEditingToolId(tool.id);
                                                                const cfg = { ...tool.config };
                                                                // Pre-load jina api_key from system_settings
                                                                if (tool.name === 'jina_search' || tool.name === 'jina_read') {
                                                                    try {
                                                                        const token = localStorage.getItem('token');
                                                                        const res = await fetch('/api/enterprise/system-settings/jina_api_key', { headers: { Authorization: `Bearer ${token}` } });
                                                                        const d = await res.json();
                                                                        if (d.value?.api_key) cfg.api_key = d.value.api_key;
                                                                    } catch { }
                                                                }
                                                                setEditingConfig(cfg);
                                                            }
                                                        }}>{isEditing ? t('enterprise.tools.collapse') : t('enterprise.tools.configure')}</button>
                                                    )}
                                                    {tool.type !== 'builtin' && (
                                                        <button className="btn btn-danger" style={{ padding: '4px 8px', fontSize: '11px' }} onClick={async () => {
                                                            if (!confirm(`${t('common.delete')} ${tool.display_name}?`)) return;
                                                            await fetchJson(`/tools/${tool.id}`, { method: 'DELETE' });
                                                            loadAllTools();
                                                            loadAgentInstalledTools(); // cross-refresh in case it was also in agent-installed
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
                                                                if (tool.name === 'jina_search' || tool.name === 'jina_read') {
                                                                    // Save api_key to system_settings (shared by both jina tools)
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
                                                            <button className="btn btn-secondary" onClick={() => setEditingToolId(null)}>{t('common.cancel')}</button>
                                                        </div>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    );
                                })}
                                {allTools.length === 0 && <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>{t('enterprise.tools.emptyState')}</div>}
                            </div>
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
