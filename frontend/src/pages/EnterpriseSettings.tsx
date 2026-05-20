import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { saveAccentColor, getSavedAccentColor, resetAccentColor, PRESET_COLORS } from '../utils/theme';
import UserManagement from './UserManagement';
import InvitationCodes from './InvitationCodes';
import { useDialog } from '../components/Dialog/DialogProvider';
import { useToast } from '../components/Toast/ToastProvider';
import { buildCompanyRegions, type CompanyRegion } from '../utils/companyRegions';
import OrgTab from './enterprise-settings/tabs/OrgTab';
import SkillsTab from './enterprise-settings/tabs/SkillsTab';
import OkrTab from './enterprise-settings/tabs/OkrTab';
import LlmTab from './enterprise-settings/tabs/LlmTab';
import EnterpriseKBBrowser from './enterprise-settings/components/EnterpriseKBBrowser';
import { A2AAsyncToggle, CompanyLogoEditor, CompanyNameEditor, CompanyTimezoneEditor } from './enterprise-settings/components/CompanyInfoEditors';
import {
    IconBrowser,
    IconBulb,
    IconChevronDown,
    IconClock,
    IconCheck,
    IconFileText,
    IconMessageCircle,
    IconSearch,
    IconSettings,
    IconTerminal2,
    IconTools,
    IconUser,
} from '@tabler/icons-react';
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
        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
            <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '10px' }}>
                {t('enterprise.config.themeColor')}
            </div>
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





export default function EnterpriseSettings() {
    const { t } = useTranslation();
    const dialog = useDialog();
    const toast = useToast();
    const qc = useQueryClient();
    type TabKey = 'llm' | 'org' | 'info' | 'approvals' | 'audit' | 'tools' | 'skills' | 'quotas' | 'users' | 'invites' | 'okr';
    const VALID_TABS: TabKey[] = ['info', 'llm', 'tools', 'skills', 'okr', 'invites', 'quotas', 'users', 'org', 'approvals', 'audit'];
    const getTabFromHash = (): TabKey => {
        const hash = window.location.hash.replace('#', '') as TabKey;
        return VALID_TABS.includes(hash) ? hash : 'info';
    };
    const [activeTab, setActiveTab] = useState<TabKey>(getTabFromHash);
    // Sync hash ↔ activeTab: hashchange navigation (back/forward) updates state
    useEffect(() => {
        const handler = () => setActiveTab(getTabFromHash());
        window.addEventListener('hashchange', handler);
        return () => window.removeEventListener('hashchange', handler);
    }, []);

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
        default_max_agents: 2, default_agent_ttl_hours: 0,
        default_max_llm_calls_per_day: 1000, min_heartbeat_interval_minutes: 120,
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
        } catch (e: any) { toast.error('保存失败', { details: String(e?.message || e) }); }
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
    const [kbToast, setKbToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

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
    const [showAdvancedToolConfig, setShowAdvancedToolConfig] = useState(false);

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
    const GLOBAL_CATEGORY_CONFIG_PRIMARY_TOOL: Record<string, string> = {
        agentbay: 'agentbay_browser_navigate',
    };

    const applyConfigDefaults = (fields: any[] = [], config: Record<string, any> = {}) => {
        const next = { ...config };
        for (const field of fields) {
            if (field.default !== undefined && (next[field.key] === undefined || next[field.key] === null || next[field.key] === '')) {
                next[field.key] = field.default;
            }
        }
        return next;
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
    const categoryDescriptions: Record<string, string> = {
        agentbay: 'Browser and cloud computer automation',
        file: 'Read, write, convert, and manage workspace files',
        communication: 'Messages and cross-channel collaboration',
        search: 'Web and knowledge search tools',
        code: 'Code execution and development utilities',
        aware: 'Triggers, reminders, and awareness workflows',
        email: 'Email reading and sending tools',
        feishu: 'Feishu / Lark messaging and collaboration',
        okr: 'Objectives, key results, and progress reporting',
        social: 'Social publishing and community workflows',
        discovery: 'Tool and capability discovery',
        custom: 'Company-added or MCP tools',
        general: 'General purpose tools',
    };
    const renderCategoryIcon = (category: string, size = 15) => {
        const style = { color: 'var(--text-tertiary)' };
        switch (category) {
            case 'agentbay': return <IconBrowser size={size} stroke={1.8} style={style} />;
            case 'file': return <IconFileText size={size} stroke={1.8} style={style} />;
            case 'communication':
            case 'feishu':
            case 'email':
            case 'social':
                return <IconMessageCircle size={size} stroke={1.8} style={style} />;
            case 'search':
            case 'discovery':
                return <IconSearch size={size} stroke={1.8} style={style} />;
            case 'code': return <IconTerminal2 size={size} stroke={1.8} style={style} />;
            case 'aware': return <IconClock size={size} stroke={1.8} style={style} />;
            case 'custom': return <IconSettings size={size} stroke={1.8} style={style} />;
            default: return <IconTools size={size} stroke={1.8} style={style} />;
        }
    };
    const mcpToolGroupKey = (tool: any) => {
        const serverName = String(tool.mcp_server_name || '').trim();
        return tool.type === 'mcp' && serverName
            ? `mcp:${serverName.toLowerCase()}`
            : (tool.category || 'general');
    };
    const getToolGroupMeta = (groupKey: string, toolsInGroup: any[]) => {
        const first = toolsInGroup.find((tool: any) => tool.type === 'mcp' && tool.mcp_server_name) || toolsInGroup[0];
        if (groupKey.startsWith('mcp:') && first?.mcp_server_name) {
            return {
                label: first.mcp_server_name,
                description: t('agent.tools.mcpGroupDescription', 'Tools from {{name}}', { name: first.mcp_server_name }),
                iconCategory: 'custom',
                configCategory: first.category || 'custom',
            };
        }
        return {
            label: categoryLabels[groupKey] || groupKey,
            description: categoryDescriptions[groupKey] || 'Tools in this category',
            iconCategory: groupKey,
            configCategory: groupKey,
        };
    };
    const switchTrack = (enabled: boolean, mixed = false) => ({
        position: 'absolute' as const,
        inset: 0,
        background: enabled ? 'var(--accent-primary)' : mixed ? 'var(--border-default)' : 'var(--bg-tertiary)',
        borderRadius: '11px',
        transition: 'background 0.2s',
    });
    const switchKnob = (enabled: boolean) => ({
        position: 'absolute' as const,
        left: enabled ? '20px' : '2px',
        top: '2px',
        width: '18px',
        height: '18px',
        background: '#fff',
        borderRadius: '50%',
        transition: 'left 0.2s',
        boxShadow: '0 1px 3px rgba(0,0,0,0.12)',
    });
    const [toolsView, setToolsView] = useState<'global' | 'agent-installed'>('global');
    const [agentInstalledTools, setAgentInstalledTools] = useState<any[]>([]);
    const [toolSearch, setToolSearch] = useState('');
    const [toolStatusFilter, setToolStatusFilter] = useState<'all' | 'enabled' | 'disabled' | 'default' | 'configured'>('all');
    const [expandedToolCategories, setExpandedToolCategories] = useState<Set<string>>(() => new Set());
    const [expandedAgentInstalledGroups, setExpandedAgentInstalledGroups] = useState<Set<string>>(() => new Set());
    const hasMeaningfulConfigValue = (value: any): boolean => {
        if (value == null) return false;
        if (typeof value === 'string') return value.trim().length > 0;
        if (typeof value === 'number') return Number.isFinite(value);
        if (typeof value === 'boolean') return value;
        if (Array.isArray(value)) return value.some(hasMeaningfulConfigValue);
        if (typeof value === 'object') return Object.values(value).some(hasMeaningfulConfigValue);
        return false;
    };
    const hasMeaningfulConfig = (config?: Record<string, any> | null): boolean => {
        if (!config) return false;
        return Object.values(config).some(hasMeaningfulConfigValue);
    };
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
        } catch (error) {
            console.warn('[EnterpriseTools] Failed to load agent-installed tools', error);
            setAgentInstalledTools([]);
        }
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
                    {(['info', 'llm', 'tools', 'skills', 'okr', 'invites', 'quotas', 'users', 'org', 'approvals', 'audit'] as const).map(tab => (
                        <div
                            key={tab}
                            className={`tab ${activeTab === tab ? 'active' : ''}`}
                            onClick={() => {
                                // Update URL hash so each tab has a bookmarkable address
                                window.location.hash = tab;
                                setActiveTab(tab);
                            }}
                        >
                            {tab === 'quotas' ? t('enterprise.tabs.quotas', 'Quotas') : tab === 'users' ? t('enterprise.tabs.users', 'Users') : tab === 'invites' ? t('enterprise.tabs.invites', 'Invitations') : tab === 'okr' ? t('nav.okr', 'OKR') : t(`enterprise.tabs.${tab}`)}
                        </div>
                    ))}
                </div>

                {activeTab === 'okr' && <OkrTab tenantId={selectedTenantId} t={t} />}

                {/* ── LLM Model Pool ── */}
                {activeTab === 'llm' && <LlmTab selectedTenantId={selectedTenantId} />}

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
                                            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                            background: isBg ? 'rgba(99,102,241,0.12)' : 'rgba(34,197,94,0.12)',
                                            color: isBg ? 'var(--accent-color)' : 'rgb(34,197,94)',
                                        }}>{isBg ? <IconSettings size={12} stroke={1.8} /> : <IconUser size={12} stroke={1.8} />}</span>
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
                        <CompanyLogoEditor key={`logo-${selectedTenantId}`} />
                        <CompanyNameEditor key={`name-${selectedTenantId}`} />
                        <CompanyTimezoneEditor key={`tz-${selectedTenantId}`} />
                        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
                            <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '4px' }}>
                                {t('enterprise.companyIntro.title', 'Company Intro')}
                            </div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                {t('enterprise.companyIntro.description', 'Describe your company\'s mission, products, and culture. This information is included in every agent conversation as context.')}
                            </div>
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
                                {companyIntroSaved && <span style={{ color: 'var(--success)', fontSize: '12px', display: 'inline-flex', alignItems: 'center', gap: '4px' }}><IconCheck size={13} stroke={2} /> {t('enterprise.config.saved', 'Saved')}</span>}
                                <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                                    <IconBulb size={13} stroke={1.8} /> {t('enterprise.companyIntro.hint', 'This content appears in every agent\'s system prompt')}
                                </span>
                            </div>
                        </div>
                        <div className="card" style={{ marginBottom: '24px', padding: '16px' }}>
                            <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '4px' }}>
                                {t('enterprise.kb.title')}
                            </div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                {t('enterprise.kb.description', 'Shared files accessible to all agents via enterprise_info/ directory.')}
                            </div>
                            <EnterpriseKBBrowser onRefresh={() => setInfoRefresh((v: number) => v + 1)} refreshKey={infoRefresh} />
                        </div>
                        <ThemeColorPicker />
                        <A2AAsyncToggle key={`a2a-${selectedTenantId}`} />

                        {/* ── Danger Zone: Delete Company ── */}
                        <div style={{ marginTop: '32px', padding: '16px', border: '1px solid var(--status-error, #e53e3e)', borderRadius: '8px' }}>
                            <h3 style={{ marginBottom: '4px', color: 'var(--status-error, #e53e3e)' }}>{t('enterprise.dangerZone', 'Danger Zone')}</h3>
                            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                {t('enterprise.deleteCompanyDesc', 'Permanently delete this company and all its data including agents, models, tools, and skills. This action cannot be undone.')}
                            </p>
                            <button
                                className="btn"
                                onClick={async () => {
                                    const ok = await dialog.confirm(
                                        t('enterprise.deleteCompanyConfirm', 'Are you sure you want to delete this company and ALL its data? This cannot be undone.'),
                                        {
                                            title: t('enterprise.deleteCompanyTitle', 'Delete company'),
                                            danger: true,
                                            confirmLabel: t('enterprise.deleteCompanyConfirmButton', 'Permanently delete'),
                                        },
                                    );
                                    if (!ok) return;
                                    try {
                                        const res = await fetchJson<any>(`/tenants/${selectedTenantId}`, { method: 'DELETE' });
                                        // Switch to fallback tenant
                                        const fallbackId = res.fallback_tenant_id;
                                        localStorage.setItem('current_tenant_id', fallbackId);
                                        setSelectedTenantId(fallbackId);
                                        window.dispatchEvent(new StorageEvent('storage', { key: 'current_tenant_id', newValue: fallbackId }));
                                        qc.invalidateQueries({ queryKey: ['tenants'] });
                                    } catch (e: any) {
                                        await dialog.alert(t('enterprise.deleteCompanyFailed', 'Failed to delete company'), { type: 'error', details: String(e?.message || e) });
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
                                    <select
                                        className="form-input"
                                        value={quotaForm.default_agent_ttl_hours > 0 ? 'custom' : 'permanent'}
                                        onChange={e => setQuotaForm({
                                            ...quotaForm,
                                            default_agent_ttl_hours: e.target.value === 'permanent' ? 0 : 48,
                                        })}
                                    >
                                        <option value="permanent">{t('enterprise.quotas.permanent')}</option>
                                        <option value="custom">{t('enterprise.quotas.customHours', 'Custom hours')}</option>
                                    </select>
                                    {quotaForm.default_agent_ttl_hours > 0 && (
                                        <input
                                            className="form-input"
                                            type="number"
                                            min={1}
                                            value={quotaForm.default_agent_ttl_hours}
                                            onChange={e => setQuotaForm({ ...quotaForm, default_agent_ttl_hours: Number(e.target.value) })}
                                            style={{ marginTop: '8px' }}
                                        />
                                    )}
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
                                {quotaSaved && <span style={{ color: 'var(--success)', fontSize: '12px', display: 'inline-flex', alignItems: 'center', gap: '4px' }}><IconCheck size={13} stroke={2} /> Saved</span>}
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
                        <div className="tool-source-tabs enterprise-tool-source-tabs" role="tablist" aria-label={t('enterprise.tools.sourceTabs', 'Tool sources')}>
                            {([['global', t('enterprise.tools.globalTools')], ['agent-installed', t('enterprise.tools.agentInstalled')]] as const).map(([key, label]) => (
                                <button
                                    key={key}
                                    type="button"
                                    role="tab"
                                    aria-selected={toolsView === key}
                                    className={toolsView === key ? 'active' : ''}
                                    onClick={() => { setToolsView(key as any); if (key === 'agent-installed') loadAgentInstalledTools(); }}
                                >
                                    <span>{label}</span>
                                    <span className="tool-source-tab-count">{key === 'global' ? allTools.length : agentInstalledTools.length}</span>
                                </button>
                            ))}
                        </div>

                        {/* Agent-Installed Tools */}
                        {toolsView === 'agent-installed' && (
                            <div>
                                <p style={{ fontSize: '13px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>{t('enterprise.tools.agentInstalledHint')}</p>
                                {agentInstalledTools.length === 0 ? (
                                    <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>{t('enterprise.tools.noAgentInstalledTools')}</div>
                                ) : (
                                    (() => {
                                        const grouped = agentInstalledTools.reduce((acc: Record<string, any[]>, row: any) => {
                                            const groupKey = mcpToolGroupKey(row);
                                            (acc[groupKey] = acc[groupKey] || []).push(row);
                                            return acc;
                                        }, {});
                                        const toggleAgentInstalledGroup = (groupKey: string) => {
                                            setExpandedAgentInstalledGroups(prev => {
                                                const next = new Set(prev);
                                                if (next.has(groupKey)) next.delete(groupKey);
                                                else next.add(groupKey);
                                                return next;
                                            });
                                        };
                                        return (
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                                {Object.entries(grouped)
                                                    .sort(([a, aRows], [b, bRows]) => {
                                                        const aMeta = getToolGroupMeta(a, aRows as any[]);
                                                        const bMeta = getToolGroupMeta(b, bRows as any[]);
                                                        return aMeta.label.localeCompare(bMeta.label);
                                                    })
                                                    .map(([groupKey, rows]) => {
                                                        const groupRows = rows as any[];
                                                        const meta = getToolGroupMeta(groupKey, groupRows);
                                                        const expanded = expandedAgentInstalledGroups.has(groupKey);
                                                        return (
                                                            <div key={groupKey} style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', overflow: 'hidden', background: 'var(--bg-primary)' }}>
                                                                <div
                                                                    role="button"
                                                                    tabIndex={0}
                                                                    onClick={() => toggleAgentInstalledGroup(groupKey)}
                                                                    onKeyDown={(e) => {
                                                                        if (e.key === 'Enter' || e.key === ' ') {
                                                                            e.preventDefault();
                                                                            toggleAgentInstalledGroup(groupKey);
                                                                        }
                                                                    }}
                                                                    style={{ background: 'var(--bg-secondary)', padding: '12px 14px', display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', userSelect: 'none' }}
                                                                >
                                                                    <IconChevronDown size={14} stroke={1.8} style={{ color: 'var(--text-tertiary)', transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)', transition: 'transform 0.15s ease', flexShrink: 0 }} />
                                                                    <span style={{ width: '26px', height: '26px', borderRadius: '7px', border: '1px solid var(--border-subtle)', background: 'var(--bg-primary)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>{renderCategoryIcon(meta.iconCategory, 15)}</span>
                                                                    <div style={{ minWidth: 0 }}>
                                                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                                                            <span style={{ fontSize: '13px', fontWeight: 650, color: 'var(--text-primary)' }}>{meta.label}</span>
                                                                            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                                                {groupRows.length} {groupRows.length === 1 ? 'tool' : 'tools'}
                                                                            </span>
                                                                        </div>
                                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{meta.description}</div>
                                                                    </div>
                                                                </div>
                                                                {expanded && groupRows.map((row: any, idx: number) => (
                                                                    <div key={row.agent_tool_id} style={{
                                                                        display: 'grid',
                                                                        gridTemplateColumns: 'minmax(0, 1fr) auto',
                                                                        gap: '12px',
                                                                        alignItems: 'center',
                                                                        padding: '10px 14px',
                                                                        borderTop: idx === 0 ? '1px solid var(--border-subtle)' : 'none',
                                                                        borderBottom: idx < groupRows.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                                                                    }}>
                                                                        <div style={{ minWidth: 0 }}>
                                                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0, flexWrap: 'wrap' }}>
                                                                                <span style={{ fontWeight: 500, fontSize: '13px', color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.tool_display_name}</span>
                                                                                {row.type === 'mcp' && <span style={{ fontSize: '10px', background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', borderRadius: '4px', padding: '1px 5px' }}>MCP</span>}
                                                                                {row.configured && <span style={{ fontSize: '10px', background: 'rgba(99,102,241,0.15)', color: 'var(--accent-color)', borderRadius: '4px', padding: '1px 5px' }}>{t('enterprise.tools.configured', 'Configured')}</span>}
                                                                            </div>
                                                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                                                {row.installed_by_agent_name || 'Unknown Agent'}
                                                                                {row.installed_at && <span> · {new Date(row.installed_at).toLocaleString()}</span>}
                                                                            </div>
                                                                        </div>
                                                                        <button className="btn btn-ghost" style={{ color: 'var(--error)', fontSize: '12px' }} onClick={async () => {
                                                                            const ok = await dialog.confirm(t('enterprise.tools.removeFromAgent', { name: row.tool_display_name }), { title: '移除工具', danger: true, confirmLabel: '移除' });
                                                                            if (!ok) return;
                                                                            try {
                                                                                await fetchJson(`/tools/agent-tool/${row.agent_tool_id}`, { method: 'DELETE' });
                                                                            } catch {
                                                                                // Already deleted (e.g. removed via Global Tools) — just refresh
                                                                            }
                                                                            loadAgentInstalledTools();
                                                                        }}>{t('enterprise.tools.delete')}</button>
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        );
                                                    })}
                                            </div>
                                        );
                                    })()
                                )}
                            </div>
                        )}

                        {toolsView === 'global' && <>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                                <div />
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
                                                                        await dialog.alert(t('enterprise.tools.importFailed') || '导入失败', { type: 'error', details: String(e?.message || e) });
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
                                                                    await dialog.alert(`已导入 ${successCount}/${tools.length} 个工具`, { type: 'warning', title: '部分导入失败', details: errors.join('\n') });
                                                                } else if (successCount > 0) {
                                                                    toast.success(`已导入 ${successCount} 个工具`);
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
                                const normalizedSearch = toolSearch.trim().toLowerCase();
                                const matchesSearch = (tool: any) => {
                                    if (!normalizedSearch) return true;
                                    const category = tool.category || 'general';
                                    const haystack = [
                                        tool.name,
                                        tool.display_name,
                                        tool.description,
                                        tool.mcp_server_name,
                                        category,
                                        categoryLabels[category],
                                    ].filter(Boolean).join(' ').toLowerCase();
                                    return haystack.includes(normalizedSearch);
                                };
                                const matchesStatus = (tool: any) => {
                                    if (toolStatusFilter === 'enabled') return !!tool.enabled;
                                    if (toolStatusFilter === 'disabled') return !tool.enabled;
                                    if (toolStatusFilter === 'default') return !!tool.is_default;
                                    if (toolStatusFilter === 'configured') return hasMeaningfulConfig(tool.config);
                                    return true;
                                };
                                const filteredTools = allTools.filter(tool => matchesSearch(tool) && matchesStatus(tool));
                                const groupTools = (toolList: any[]) => toolList.reduce((acc: Record<string, any[]>, tool: any) => {
                                    const cat = mcpToolGroupKey(tool);
                                    (acc[cat] = acc[cat] || []).push(tool);
                                    return acc;
                                }, {} as Record<string, any[]>);
                                const grouped = groupTools(filteredTools);
                                const allGrouped = groupTools(allTools);
                                const hasFilters = !!normalizedSearch || toolStatusFilter !== 'all';

                                const toggleCategoryExpanded = (category: string) => {
                                    setExpandedToolCategories(prev => {
                                        const next = new Set(prev);
                                        if (next.has(category)) next.delete(category);
                                        else next.add(category);
                                        return next;
                                    });
                                };

                                const bulkToggle = async (tools: any[], enabled: boolean) => {
                                    try {
                                        const payload = tools.map(t => ({ tool_id: t.id, enabled }));
                                        await fetchJson('/tools/bulk', { method: 'PUT', body: JSON.stringify(payload) });
                                        loadAllTools();
                                    } catch (err: any) {
                                        toast.error('批量更新失败', { details: String(err?.message || err) });
                                    }
                                };

                                const renderToolRow = (tool: any, category: string, idx: number, total: number) => {
                                    const hasCategoryConfig = !!GLOBAL_CATEGORY_CONFIG_SCHEMAS[category];
                                    const hasOwnConfig = tool.config_schema?.fields?.length > 0 && !hasCategoryConfig;
                                    const isConfigured = hasMeaningfulConfig(tool.config);
                                    return (
                                        <div key={tool.id} style={{
                                            display: 'grid',
                                            gridTemplateColumns: 'minmax(0, 1fr) auto',
                                            alignItems: 'center',
                                            gap: '12px',
                                            padding: '10px 14px',
                                            borderTop: idx === 0 ? '1px solid var(--border-subtle)' : 'none',
                                            borderBottom: idx < total - 1 ? '1px solid var(--border-subtle)' : 'none',
                                            background: 'var(--bg-primary)',
                                        }}>
                                            <div style={{ minWidth: 0 }}>
                                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', minWidth: 0, flexWrap: 'wrap' }}>
                                                    <span style={{ fontWeight: 500, fontSize: '13px', color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tool.display_name}</span>
                                                    <span style={{ fontSize: '10px', background: tool.type === 'mcp' ? 'var(--primary)' : 'var(--bg-tertiary)', color: tool.type === 'mcp' ? '#fff' : 'var(--text-secondary)', borderRadius: '4px', padding: '1px 5px', flexShrink: 0 }}>
                                                        {tool.type === 'mcp' ? 'MCP' : 'Built-in'}
                                                    </span>
                                                    {tool.is_default && <span style={{ fontSize: '10px', background: 'rgba(0,200,100,0.15)', color: 'var(--success)', borderRadius: '4px', padding: '1px 5px', flexShrink: 0 }}>Default</span>}
                                                    {isConfigured && <span style={{ fontSize: '10px', background: 'rgba(99,102,241,0.15)', color: 'var(--accent-color)', borderRadius: '4px', padding: '1px 5px', flexShrink: 0 }}>{t('enterprise.tools.configured', 'Configured')}</span>}
                                                </div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                    {tool.description}
                                                    {tool.mcp_server_name && <span> · {tool.mcp_server_name}</span>}
                                                </div>
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                                                {tool.type === 'mcp' && tool.mcp_server_name && (
                                                    <button
                                                        style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }}
                                                        onClick={() => setEditingMcpServer({
                                                            server_name: tool.mcp_server_name,
                                                            server_url: tool.mcp_server_url || '',
                                                            api_key: '',
                                                        })}
                                                    >
                                                        Edit Server
                                                    </button>
                                                )}
                                                {hasOwnConfig && (
                                                    <button
                                                        style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }}
                                                        title={t('enterprise.tools.configureSettings', 'Configure settings')}
                                                        onClick={async () => {
                                                            setEditingToolId(tool.id);
                                                            setShowAdvancedToolConfig(false);
                                                            let cfg = applyConfigDefaults(tool.config_schema?.fields || [], tool.config || {});
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
                                                        {t('enterprise.tools.configure')}
                                                    </button>
                                                )}
                                                {tool.type !== 'builtin' && (
                                                    <button className="btn btn-danger" style={{ padding: '4px 8px', fontSize: '11px' }} onClick={async () => {
                                                        const ok = await dialog.confirm(`确定删除 ${tool.display_name}？`, { title: '删除工具', danger: true, confirmLabel: '删除' });
                                                        if (!ok) return;
                                                        await fetchJson(`/tools/${tool.id}`, { method: 'DELETE' });
                                                        loadAllTools();
                                                        loadAgentInstalledTools();
                                                    }}>{t('common.delete')}</button>
                                                )}
                                                <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer', flexShrink: 0 }}>
                                                    <input type="checkbox" checked={tool.enabled} onChange={async (e) => {
                                                        await fetchJson(`/tools/${tool.id}`, { method: 'PUT', body: JSON.stringify({ enabled: e.target.checked }) });
                                                        loadAllTools();
                                                    }} style={{ opacity: 0, width: 0, height: 0 }} />
                                                    <span style={switchTrack(tool.enabled)}>
                                                        <span style={switchKnob(tool.enabled)} />
                                                    </span>
                                                </label>
                                            </div>
                                        </div>
                                    );
                                };

                                if (allTools.length === 0) {
                                    return <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>{t('enterprise.tools.emptyState')}</div>;
                                }
                                if (filteredTools.length === 0) {
                                    return (
                                        <>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', marginBottom: '16px' }}>
                                                <div style={{ position: 'relative', flex: '1 1 260px', minWidth: '220px' }}>
                                                    <IconSearch size={15} style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)' }} />
                                                    <input value={toolSearch} onChange={(e) => setToolSearch(e.target.value)} placeholder={t('agent.tools.searchTools', 'Search tools...')} style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--border-subtle)', borderRadius: '8px', background: 'var(--bg-primary)', color: 'var(--text-primary)', padding: '8px 10px 8px 32px', fontSize: '13px', outline: 'none' }} />
                                                </div>
                                            </div>
                                            <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>{hasFilters ? t('agent.tools.noMatchingTools', 'No matching tools') : t('enterprise.tools.emptyState')}</div>
                                        </>
                                    );
                                }

                                return (
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                            <div style={{ position: 'relative', flex: '1 1 260px', minWidth: '220px' }}>
                                                <IconSearch size={15} style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)' }} />
                                                <input value={toolSearch} onChange={(e) => setToolSearch(e.target.value)} placeholder={t('agent.tools.searchTools', 'Search tools...')} style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--border-subtle)', borderRadius: '8px', background: 'var(--bg-primary)', color: 'var(--text-primary)', padding: '8px 10px 8px 32px', fontSize: '13px', outline: 'none' }} />
                                            </div>
                                            {(['all', 'enabled', 'disabled', 'default', 'configured'] as const).map(filter => (
                                                <button key={filter} type="button" onClick={() => setToolStatusFilter(filter)} style={{ border: '1px solid var(--border-subtle)', borderRadius: '999px', background: toolStatusFilter === filter ? 'var(--text-primary)' : 'var(--bg-primary)', color: toolStatusFilter === filter ? 'var(--bg-primary)' : 'var(--text-secondary)', padding: '6px 10px', fontSize: '11px', cursor: 'pointer' }}>
                                                    {filter === 'all' ? t('common.all', 'All')
                                                        : filter === 'enabled' ? t('common.enabled', 'Enabled')
                                                            : filter === 'disabled' ? t('common.disabled', 'Disabled')
                                                                : filter === 'default' ? 'Default'
                                                                    : t('agent.tools.configured', 'Configured')}
                                                </button>
                                            ))}
                                            <button type="button" onClick={() => {
                                                const categories = Object.keys(allGrouped);
                                                setExpandedToolCategories(prev => prev.size >= categories.length ? new Set() : new Set(categories));
                                            }} style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', background: 'var(--bg-primary)', color: 'var(--text-secondary)', padding: '6px 10px', fontSize: '11px', cursor: 'pointer' }}>
                                                {expandedToolCategories.size >= Object.keys(allGrouped).length ? t('agent.tools.collapseAll', 'Collapse all') : t('agent.tools.expandAll', 'Expand all')}
                                            </button>
                                        </div>

                                        {Object.entries(grouped)
                                            .sort(([a, aTools], [b, bTools]) => {
                                                const aMeta = getToolGroupMeta(a, allGrouped[a] || aTools as any[]);
                                                const bMeta = getToolGroupMeta(b, allGrouped[b] || bTools as any[]);
                                                return aMeta.label.localeCompare(bMeta.label);
                                            })
                                            .map(([category, catTools]) => {
                                            const allCatTools = allGrouped[category] || catTools;
                                            const meta = getToolGroupMeta(category, allCatTools);
                                            const hasCategoryConfig = !!GLOBAL_CATEGORY_CONFIG_SCHEMAS[meta.configCategory];
                                            const label = meta.label;
                                            const enabledCount = allCatTools.filter((tool: any) => tool.enabled).length;
                                            const defaultCount = allCatTools.filter((tool: any) => tool.is_default).length;
                                            const configuredCount = allCatTools.filter((tool: any) => hasMeaningfulConfig(tool.config)).length;
                                            const allEnabled = allCatTools.length > 0 && enabledCount === allCatTools.length;
                                            const mixed = enabledCount > 0 && enabledCount < allCatTools.length;
                                            const expanded = expandedToolCategories.has(category) || !!toolSearch.trim();
                                            const visibleCount = (catTools as any[]).length;

                                            return (
                                                <div key={category} style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', overflow: 'hidden', background: 'var(--bg-primary)' }}>
                                                    <div role="button" tabIndex={0} onClick={() => toggleCategoryExpanded(category)} onKeyDown={(e) => {
                                                        if (e.key === 'Enter' || e.key === ' ') {
                                                            e.preventDefault();
                                                            toggleCategoryExpanded(category);
                                                        }
                                                    }} style={{ width: '100%', background: 'var(--bg-secondary)', padding: '13px 16px', display: 'grid', gridTemplateColumns: '1fr auto', gap: '14px', alignItems: 'center', cursor: 'pointer', textAlign: 'left', boxSizing: 'border-box' }}>
                                                        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', minWidth: 0 }}>
                                                            <IconChevronDown size={16} style={{ transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)', transition: 'transform 120ms ease', color: 'var(--text-tertiary)', flexShrink: 0 }} />
                                                            <span style={{ width: '28px', height: '28px', borderRadius: '7px', border: '1px solid var(--border-subtle)', background: 'var(--bg-primary)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>{renderCategoryIcon(meta.iconCategory, 16)}</span>
                                                            <div style={{ minWidth: 0 }}>
                                                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                                                    <span style={{ fontSize: '13px', fontWeight: 650, color: 'var(--text-primary)' }}>{label}</span>
                                                                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                                        {allCatTools.length} tools · {enabledCount} enabled
                                                                        {defaultCount > 0 ? ` · ${defaultCount} default` : ''}
                                                                        {visibleCount !== allCatTools.length ? ` · ${visibleCount} shown` : ''}
                                                                    </span>
                                                                    {configuredCount > 0 && <span style={{ fontSize: '10px', background: 'rgba(99,102,241,0.15)', color: 'var(--accent-color)', borderRadius: '4px', padding: '1px 5px' }}>{configuredCount} configured</span>}
                                                                </div>
                                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{meta.description}</div>
                                                            </div>
                                                        </div>
                                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }} onClick={(e) => e.stopPropagation()}>
                                                            {hasCategoryConfig && (
                                                                <button onClick={() => {
                                                                    setConfigCategory(meta.configCategory);
                                                                    setEditingConfig({});
                                                                    const firstToolWithConfig = (allCatTools as any[]).find((tl: any) => tl.category === meta.configCategory && hasMeaningfulConfig(tl.config));
                                                                    if (firstToolWithConfig?.config) setEditingConfig({ ...firstToolWithConfig.config });
                                                                }} style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '4px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }} title={`Configure ${label}`}>
                                                                    {t('enterprise.tools.configure', 'Configure')}
                                                                </button>
                                                            )}
                                                            <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer', flexShrink: 0 }} title={`Enable/Disable all ${label} tools`}>
                                                                <input type="checkbox" checked={allEnabled} onChange={(e) => void bulkToggle(allCatTools, e.target.checked)} style={{ opacity: 0, width: 0, height: 0 }} />
                                                                <span style={switchTrack(allEnabled, mixed)}>
                                                                    <span style={switchKnob(allEnabled)} />
                                                                </span>
                                                            </label>
                                                        </div>
                                                    </div>
                                                    {expanded && (
                                                        <div>
                                                            {(catTools as any[]).map((tool: any, idx: number) => renderToolRow(tool, category, idx, (catTools as any[]).length))}
                                                        </div>
                                                    )}
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
                                                    toast.error('更新服务器失败', { details: String(e?.message || e) });
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
                                const visibleFields = (tool.config_schema.fields || []).filter((field: any) => {
                                    if (field.depends_on) {
                                        return Object.entries(field.depends_on).every(([k, vals]: [string, any]) =>
                                            vals.includes(editingConfig[k])
                                        );
                                    }
                                    return true;
                                });
                                const primaryFields = visibleFields.filter((field: any) => !field.advanced);
                                const advancedFields = visibleFields.filter((field: any) => field.advanced);
                                const renderField = (field: any) => (
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
                                        ) : field.type === 'textarea' ? (
                                            <textarea
                                                className="form-input"
                                                value={editingConfig[field.key] ?? field.default ?? ''}
                                                placeholder={field.placeholder || ''}
                                                rows={Math.max(3, Math.min(10, String(editingConfig[field.key] ?? field.default ?? field.placeholder ?? '').split('\n').length))}
                                                style={{ minHeight: '88px', fontFamily: 'var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)', resize: 'vertical' }}
                                                onChange={e => setEditingConfig(p => ({ ...p, [field.key]: e.target.value }))}
                                            />
                                        ) : field.type === 'password' ? (
                                            <input type="password" autoComplete="new-password" className="form-input" value={editingConfig[field.key] ?? ''} placeholder={field.placeholder || ''}
                                                onChange={e => setEditingConfig(p => ({ ...p, [field.key]: e.target.value }))} />
                                        ) : (
                                            <input type="text" className="form-input" value={editingConfig[field.key] ?? field.default ?? ''} placeholder={field.placeholder || ''}
                                                onChange={e => setEditingConfig(p => ({ ...p, [field.key]: e.target.value }))} />
                                        )}
                                    </div>
                                );
                                return (
                                    <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.55)', zIndex: 2000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                                        onClick={() => setEditingToolId(null)}>
                                        <div onClick={e => e.stopPropagation()} style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', width: '480px', maxWidth: '95vw', maxHeight: '80vh', overflow: 'auto', boxShadow: '0 20px 60px rgba(0,0,0,0.4)' }}>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                                                <div>
                                                    <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}><IconSettings size={20} stroke={1.8} /> {tool.display_name}</h3>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>Global configuration used by all agents</div>
                                                </div>
                                                <button onClick={() => setEditingToolId(null)} style={{ background: 'none', border: 'none', fontSize: '18px', cursor: 'pointer', color: 'var(--text-secondary)' }}>✕</button>
                                            </div>
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                                                {primaryFields.map(renderField)}
                                                {advancedFields.length > 0 && (
                                                    <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '10px', marginTop: '2px' }}>
                                                        <button
                                                            type="button"
                                                            className="btn btn-ghost"
                                                            onClick={() => setShowAdvancedToolConfig(v => !v)}
                                                            style={{ padding: 0, minWidth: 'auto', fontSize: '12px', color: 'var(--text-secondary)' }}
                                                        >
                                                            {showAdvancedToolConfig ? 'Hide advanced settings' : 'Advanced settings'}
                                                        </button>
                                                        {showAdvancedToolConfig && (
                                                            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '12px' }}>
                                                                {advancedFields.map(renderField)}
                                                            </div>
                                                        )}
                                                    </div>
                                                )}
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
                                                            await fetchJson(`/tools/${tool.id}`, { method: 'PUT', body: JSON.stringify({ config: editingConfig, tenant_id: selectedTenantId || undefined }) });
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
                                                    // Save config to the category's runtime representative tool.
                                                    const catTools = allTools.filter((tl: any) => (tl.category || 'general') === configCategory);
                                                    const primaryToolName = GLOBAL_CATEGORY_CONFIG_PRIMARY_TOOL[configCategory];
                                                    const representativeTool = catTools.find((tl: any) => tl.name === primaryToolName) || catTools[0];
                                                    if (representativeTool) {
                                                        await fetchJson(`/tools/${representativeTool.id}`, { method: 'PUT', body: JSON.stringify({ config: editingConfig, tenant_id: selectedTenantId || undefined }) });
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
