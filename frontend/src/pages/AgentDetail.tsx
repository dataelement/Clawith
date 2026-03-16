import React, { useState, useEffect, useRef, Component, ErrorInfo } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import ConfirmModal from '../components/ConfirmModal';
import type { FileBrowserApi } from '../components/FileBrowser';
import FileBrowser from '../components/FileBrowser';
import MarkdownRenderer from '../components/MarkdownRenderer';
import PromptModal from '../components/PromptModal';
import { activityApi, agentApi, channelApi, enterpriseApi, fileApi, scheduleApi, skillApi, taskApi, triggerApi, uploadFileWithProgress } from '../services/api';
import { useAuthStore } from '../stores';

const TABS = ['status', 'aware', 'mind', 'tools', 'skills', 'relationships', 'workspace', 'chat', 'activityLog', 'approvals', 'settings'] as const;

// Format large token numbers with K/M suffixes
const formatTokens = (n: number) => {
    if (!n) return '0';
    if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
    return String(n);
};

const getCategoryLabels = (t: any): Record<string, string> => ({
    file: t('agent.toolCategories.file'),
    task: t('agent.toolCategories.task'),
    communication: t('agent.toolCategories.communication'),
    search: t('agent.toolCategories.search'),
    pulse: t('agent.toolCategories.pulse', 'Pulse & Triggers'),
    social: t('agent.toolCategories.social', 'Social'),
    code: t('agent.toolCategories.code', 'Code & Execution'),
    discovery: t('agent.toolCategories.discovery', 'Discovery'),
    email: t('agent.toolCategories.email', 'Email'),
    feishu: t('agent.toolCategories.feishu', 'Feishu / Lark'),
    custom: t('agent.toolCategories.custom'),
    general: t('agent.toolCategories.general'),
});

function ToolsManager({ agentId, canManage = false }: { agentId: string; canManage?: boolean }) {
    const { t } = useTranslation();
    const [tools, setTools] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [configTool, setConfigTool] = useState<any | null>(null);
    const [configData, setConfigData] = useState<Record<string, any>>({});
    const [configJson, setConfigJson] = useState('');
    const [configSaving, setConfigSaving] = useState(false);
    const [toolTab, setToolTab] = useState<'platform' | 'installed'>('platform');
    const [deletingToolId, setDeletingToolId] = useState<string | null>(null);

    const loadTools = async () => {
        try {
            const token = localStorage.getItem('token');
            const res = await fetch(`/api/tools/agents/${agentId}/with-config`, {
                headers: { Authorization: `Bearer ${token}` },
            });
            if (res.ok) setTools(await res.json());
            else {
                // Fallback to old endpoint
                const res2 = await fetch(`/api/tools/agents/${agentId}`, { headers: { Authorization: `Bearer ${token}` } });
                if (res2.ok) setTools(await res2.json());
            }
        } catch (e) { console.error(e); }
        setLoading(false);
    };

    useEffect(() => { loadTools(); }, [agentId]);

    const toggleTool = async (toolId: string, enabled: boolean) => {
        setTools(prev => prev.map(t => t.id === toolId ? { ...t, enabled } : t));
        try {
            const token = localStorage.getItem('token');
            await fetch(`/api/tools/agents/${agentId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify([{ tool_id: toolId, enabled }]),
            });
        } catch (e) { console.error(e); }
    };

    const openConfig = (tool: any) => {
        setConfigTool(tool);
        const merged = { ...(tool.global_config || {}), ...(tool.agent_config || {}) };
        setConfigData(merged);
        setConfigJson(JSON.stringify(tool.agent_config || {}, null, 2));
    };

    const saveConfig = async () => {
        if (!configTool) return;
        setConfigSaving(true);
        try {
            const token = localStorage.getItem('token');
            const hasSchema = configTool.config_schema?.fields?.length > 0;
            const payload = hasSchema ? configData : JSON.parse(configJson || '{}');
            await fetch(`/api/tools/agents/${agentId}/tool-config/${configTool.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify({ config: payload }),
            });
            setConfigTool(null);
            loadTools();
        } catch (e) { alert('Save failed: ' + e); }
        setConfigSaving(false);
    };

    if (loading) return <div style={{ color: 'var(--text-tertiary)', padding: '20px' }}>{t('common.loading')}</div>;

    // Split by source first, then group by category
    const systemTools = tools.filter(t => t.source !== 'user_installed');
    const agentInstalledTools = tools.filter(t => t.source === 'user_installed');

    const groupByCategory = (toolList: any[]) =>
        toolList.reduce((acc: Record<string, any[]>, t) => {
            const cat = t.category || 'general';
            (acc[cat] = acc[cat] || []).push(t);
            return acc;
        }, {});

    const renderToolGroup = (groupedTools: Record<string, any[]>) =>
        Object.entries(groupedTools).map(([category, catTools]) => (
            <div key={category}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                    {getCategoryLabels(t)[category] || category}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {(catTools as any[]).map((tool: any) => {
                        const hasConfig = tool.config_schema?.fields?.length > 0 || tool.type === 'mcp';
                        const hasAgentOverride = tool.agent_config && Object.keys(tool.agent_config).length > 0;
                        return (
                            <div key={tool.id} className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flex: 1, minWidth: 0 }}>
                                    <span style={{ fontSize: '18px' }}>{tool.icon}</span>
                                    <div style={{ minWidth: 0 }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                            <span style={{ fontWeight: 500, fontSize: '13px' }}>{tool.display_name}</span>
                                            {tool.type === 'mcp' && (
                                                <span style={{ fontSize: '10px', background: 'var(--primary)', color: '#fff', borderRadius: '4px', padding: '1px 5px' }}>MCP</span>
                                            )}
                                            {tool.type === 'builtin' && (
                                                <span style={{ fontSize: '10px', background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', borderRadius: '4px', padding: '1px 5px' }}>Built-in</span>
                                            )}
                                            {hasAgentOverride && (
                                                <span style={{ fontSize: '10px', background: 'rgba(99,102,241,0.15)', color: 'var(--accent-color)', borderRadius: '4px', padding: '1px 5px' }}>Configured</span>
                                            )}
                                        </div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {tool.description}
                                            {tool.mcp_server_name && <span> · {tool.mcp_server_name}</span>}
                                        </div>
                                    </div>
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                                    {canManage && hasConfig && (
                                        <button
                                            onClick={() => openConfig(tool)}
                                            style={{ background: 'none', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }}
                                            title="Configure per-agent settings"
                                        >⚙️ Config</button>
                                    )}
                                    {canManage && tool.source === 'user_installed' && tool.agent_tool_id && (
                                        <button
                                            onClick={async () => {
                                                if (!confirm(t('agent.tools.confirmDelete', `Remove "${tool.display_name}" from this agent?`))) return;
                                                setDeletingToolId(tool.id);
                                                try {
                                                    const token = localStorage.getItem('token');
                                                    const res = await fetch(`/api/tools/agent-tool/${tool.agent_tool_id}`, {
                                                        method: 'DELETE',
                                                        headers: { Authorization: `Bearer ${token}` },
                                                    });
                                                    if (res.ok) await loadTools();
                                                    else alert('Delete failed');
                                                } catch (e) { alert('Delete failed: ' + e); }
                                                setDeletingToolId(null);
                                            }}
                                            disabled={deletingToolId === tool.id}
                                            style={{ background: 'none', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-tertiary)', opacity: deletingToolId === tool.id ? 0.5 : 1 }}
                                            title={t('agent.tools.removeTool', 'Remove from agent')}
                                        >{deletingToolId === tool.id ? '...' : '✕'}</button>
                                    )}
                                    {canManage ? (
                                        <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer', flexShrink: 0 }}>
                                            <input
                                                type="checkbox"
                                                checked={tool.enabled}
                                                onChange={e => toggleTool(tool.id, e.target.checked)}
                                                style={{ opacity: 0, width: 0, height: 0 }}
                                            />
                                            <span style={{
                                                position: 'absolute', inset: 0,
                                                background: tool.enabled ? '#22c55e' : 'var(--bg-tertiary)',
                                                borderRadius: '11px', transition: 'background 0.2s',
                                            }}>
                                                <span style={{
                                                    position: 'absolute', left: tool.enabled ? '20px' : '2px', top: '2px',
                                                    width: '18px', height: '18px', background: '#fff',
                                                    borderRadius: '50%', transition: 'left 0.2s',
                                                }} />
                                            </span>
                                        </label>
                                    ) : (
                                        <span style={{ fontSize: '11px', color: tool.enabled ? '#22c55e' : 'var(--text-tertiary)', fontWeight: 500 }}>
                                            {tool.enabled ? t('common.enabled', 'On') : t('common.disabled', 'Off')}
                                        </span>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>
        ));

    const activeTools = toolTab === 'platform' ? systemTools : agentInstalledTools;

    return (
        <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                {/* Tab Bar */}
                <div style={{ display: 'flex', gap: '2px', background: 'var(--bg-tertiary)', borderRadius: '8px', padding: '3px' }}>
                    <button
                        onClick={() => setToolTab('platform')}
                        style={{
                            flex: 1, padding: '7px 12px', border: 'none', borderRadius: '6px', cursor: 'pointer',
                            fontSize: '12px', fontWeight: 600, transition: 'all 0.2s',
                            background: toolTab === 'platform' ? 'var(--bg-primary)' : 'transparent',
                            color: toolTab === 'platform' ? 'var(--text-primary)' : 'var(--text-tertiary)',
                            boxShadow: toolTab === 'platform' ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
                        }}
                    >
                        🔧 {t('agent.tools.platformTools', 'Platform Tools')} ({systemTools.length})
                    </button>
                    <button
                        onClick={() => setToolTab('installed')}
                        style={{
                            flex: 1, padding: '7px 12px', border: 'none', borderRadius: '6px', cursor: 'pointer',
                            fontSize: '12px', fontWeight: 600, transition: 'all 0.2s',
                            background: toolTab === 'installed' ? 'var(--bg-primary)' : 'transparent',
                            color: toolTab === 'installed' ? 'var(--text-primary)' : 'var(--text-tertiary)',
                            boxShadow: toolTab === 'installed' ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
                        }}
                    >
                        🤖 {t('agent.tools.agentInstalled', 'Agent-Installed Tools')} ({agentInstalledTools.length})
                    </button>
                </div>

                {/* Tool List */}
                {activeTools.length > 0 ? (
                    renderToolGroup(groupByCategory(activeTools))
                ) : (
                    <div className="card" style={{ textAlign: 'center', padding: '30px', color: 'var(--text-tertiary)' }}>
                        {toolTab === 'installed' ? t('agent.tools.noInstalled', 'No agent-installed tools yet') : t('common.noData')}
                    </div>
                )}
            </div>
            {tools.length === 0 && (
                <div className="card" style={{ textAlign: 'center', padding: '30px', color: 'var(--text-tertiary)' }}>
                    {t('common.noData')}
                </div>
            )}

            {/* Tool Config Modal */}
            {configTool && (
                <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.55)', zIndex: 2000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                    onClick={() => setConfigTool(null)}>
                    <div onClick={e => e.stopPropagation()} style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', width: '480px', maxWidth: '95vw', maxHeight: '80vh', overflow: 'auto', boxShadow: '0 20px 60px rgba(0,0,0,0.4)' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                            <div>
                                <h3 style={{ margin: 0 }}>⚙️ {configTool.display_name}</h3>
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>Per-agent configuration (overrides global defaults)</div>
                            </div>
                            <button onClick={() => setConfigTool(null)} style={{ background: 'none', border: 'none', fontSize: '18px', cursor: 'pointer', color: 'var(--text-secondary)' }}>✕</button>
                        </div>

                        {configTool.config_schema?.fields?.length > 0 ? (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                                {configTool.config_schema.fields
                                    .filter((field: any) => {
                                        // Handle depends_on: hide fields unless dependency is met
                                        if (!field.depends_on) return true;
                                        return Object.entries(field.depends_on).every(([depKey, depVals]: [string, any]) =>
                                            (depVals as string[]).includes(configData[depKey] ?? '')
                                        );
                                    })
                                    .map((field: any) => (
                                        <div key={field.key}>
                                            <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '4px' }}>
                                                {field.label}
                                                {configTool.global_config?.[field.key] && (
                                                    <span style={{ fontWeight: 400, color: 'var(--text-tertiary)', marginLeft: '4px' }}>
                                                        (global: {String(configTool.global_config[field.key]).slice(0, 20)}{String(configTool.global_config[field.key]).length > 20 ? '…' : ''})
                                                    </span>
                                                )}
                                            </label>
                                            {field.type === 'password' ? (
                                                <>
                                                <input type="password" className="form-input" value={configData[field.key] ?? ''} placeholder={field.placeholder || 'Leave blank to use global default'} onChange={e => setConfigData(p => ({ ...p, [field.key]: e.target.value }))} />
                                                {/* Per-provider help text for auth_code */}
                                                {field.key === 'auth_code' && (() => {
                                                    const providerField = configTool.config_schema?.fields?.find((f: any) => f.key === 'email_provider');
                                                    const selectedProvider = configData['email_provider'] || providerField?.default || '';
                                                    const providerOption = providerField?.options?.find((o: any) => o.value === selectedProvider);
                                                    if (!providerOption?.help_text) return null;
                                                    return (
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px', lineHeight: '1.5' }}>
                                                            {providerOption.help_text}
                                                            {providerOption.help_url && (
                                                                <> &middot; <a href={providerOption.help_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-primary)', textDecoration: 'none' }}>Setup guide</a></>
                                                            )}
                                                        </div>
                                                    );
                                                })()}
                                                </>
                                            ) : field.type === 'select' ? (
                                                <select className="form-input" value={configData[field.key] ?? field.default ?? ''} onChange={e => setConfigData(p => ({ ...p, [field.key]: e.target.value }))}>
                                                    {(field.options || []).map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
                                                </select>
                                            ) : field.type === 'number' ? (
                                                <input type="number" className="form-input" value={configData[field.key] ?? field.default ?? ''} placeholder={field.placeholder || ''} min={field.min} max={field.max} onChange={e => setConfigData(p => ({ ...p, [field.key]: e.target.value ? Number(e.target.value) : '' }))} />
                                            ) : (
                                                <input type="text" className="form-input" value={configData[field.key] ?? ''} placeholder={field.placeholder || 'Leave blank to use global default'} onChange={e => setConfigData(p => ({ ...p, [field.key]: e.target.value }))} />
                                            )}
                                        </div>
                                    ))}
                                {/* Email tool: test connection button + help text */}
                                {configTool.category === 'email' && (
                                    <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                        <button
                                            className="btn btn-secondary"
                                            style={{ alignSelf: 'flex-start' }}
                                            onClick={async () => {
                                                const btn = document.getElementById('email-test-btn');
                                                const status = document.getElementById('email-test-status');
                                                if (btn) btn.textContent = 'Testing...';
                                                if (btn) (btn as HTMLButtonElement).disabled = true;
                                                try {
                                                    const token = localStorage.getItem('token');
                                                    const res = await fetch('/api/tools/test-email', {
                                                        method: 'POST',
                                                        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                                                        body: JSON.stringify({ config: configData }),
                                                    });
                                                    const data = await res.json();
                                                    if (status) {
                                                        status.textContent = data.ok
                                                            ? `${data.imap}\n${data.smtp}`
                                                            : `${data.imap || ''}\n${data.smtp || ''}\n${data.error || ''}`;
                                                        status.style.color = data.ok ? 'var(--success)' : 'var(--error)';
                                                    }
                                                } catch (e: any) {
                                                    if (status) { status.textContent = `Error: ${e.message}`; status.style.color = 'var(--error)'; }
                                                } finally {
                                                    if (btn) { btn.textContent = 'Test Connection'; (btn as HTMLButtonElement).disabled = false; }
                                                }
                                            }}
                                            id="email-test-btn"
                                        >Test Connection</button>
                                        <div id="email-test-status" style={{ fontSize: '11px', whiteSpace: 'pre-line', minHeight: '16px' }}></div>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div>
                                <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '4px' }}>Config JSON (Agent Override)</label>
                                <textarea
                                    className="form-input"
                                    value={configJson}
                                    onChange={e => setConfigJson(e.target.value)}
                                    style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', minHeight: '120px', resize: 'vertical' }}
                                    placeholder='{}'
                                />
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                    Global default: <code style={{ fontSize: '10px' }}>{JSON.stringify(configTool.global_config || {}).slice(0, 80)}</code>
                                </div>
                            </div>
                        )}

                        <div style={{ display: 'flex', gap: '8px', marginTop: '16px', justifyContent: 'flex-end' }}>
                            {Object.keys(configTool.agent_config || {}).length > 0 && (
                                <button className="btn btn-ghost" style={{ color: 'var(--error)', marginRight: 'auto' }} onClick={async () => {
                                    const token = localStorage.getItem('token');
                                    await fetch(`/api/tools/agents/${agentId}/tool-config/${configTool.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify({ config: {} }) });
                                    setConfigTool(null); loadTools();
                                }}>Reset to Global</button>
                            )}
                            <button className="btn btn-secondary" onClick={() => setConfigTool(null)}>Cancel</button>
                            <button className="btn btn-primary" onClick={saveConfig} disabled={configSaving}>{configSaving ? 'Saving…' : 'Save'}</button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
}

/** Convert rich schedule JSON to cron expression */
function schedToCron(sched: { freq: string; interval: number; time: string; weekdays?: number[] }): string {
    const [h, m] = (sched.time || '09:00').split(':').map(Number);
    if (sched.freq === 'weekly') {
        const days = (sched.weekdays || [1, 2, 3, 4, 5]).join(',');
        return sched.interval > 1 ? `${m} ${h} * * ${days}` : `${m} ${h} * * ${days}`;
    }
    // daily
    if (sched.interval === 1) return `${m} ${h} * * *`;
    return `${m} ${h} */${sched.interval} * *`;
}

const getRelationOptions = (t: any) => [
    { value: 'direct_leader', label: t('agent.detail.supervisor') },
    { value: 'collaborator', label: t('agent.detail.collaborator') },
    { value: 'stakeholder', label: 'Stakeholder' },
    { value: 'team_member', label: 'Team Member' },
    { value: 'subordinate', label: t('agent.detail.subordinate') },
    { value: 'mentor', label: 'Mentor' },
    { value: 'other', label: 'Other' },
];

const getAgentRelationOptions = (t: any) => [
    { value: 'peer', label: t('agent.detail.colleague') },
    { value: 'supervisor', label: t('agent.detail.supervisor') },
    { value: 'assistant', label: 'Assistant' },
    { value: 'collaborator', label: t('agent.detail.collaborator') },
    { value: 'other', label: 'Other' },
];

function fetchAuth<T>(url: string, options?: RequestInit): Promise<T> {
    const token = localStorage.getItem('token');
    return fetch(`/api${url}`, {
        ...options,
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    }).then(r => r.json());
}

function RelationshipEditor({ agentId, readOnly = false }: { agentId: string; readOnly?: boolean }) {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const [search, setSearch] = useState('');
    const [searchResults, setSearchResults] = useState<any[]>([]);
    const [adding, setAdding] = useState<any>(null);
    const [relation, setRelation] = useState('collaborator');
    const [description, setDescription] = useState('');
    // Agent relationships state
    const [addingAgent, setAddingAgent] = useState(false);
    const [agentRelation, setAgentRelation] = useState('collaborator');
    const [agentDescription, setAgentDescription] = useState('');
    const [selectedAgentId, setSelectedAgentId] = useState('');
    // Editing state
    const [editingId, setEditingId] = useState<string | null>(null);
    const [editRelation, setEditRelation] = useState('');
    const [editDescription, setEditDescription] = useState('');
    const [editingAgentId, setEditingAgentId] = useState<string | null>(null);
    const [editAgentRelation, setEditAgentRelation] = useState('');
    const [editAgentDescription, setEditAgentDescription] = useState('');

    const { data: relationships = [], refetch } = useQuery({
        queryKey: ['relationships', agentId],
        queryFn: () => fetchAuth<any[]>(`/agents/${agentId}/relationships/`),
    });
    const { data: agentRelationships = [], refetch: refetchAgentRels } = useQuery({
        queryKey: ['agent-relationships', agentId],
        queryFn: () => fetchAuth<any[]>(`/agents/${agentId}/relationships/agents`),
    });
    const { data: allAgents = [] } = useQuery({
        queryKey: ['agents-for-rel'],
        queryFn: () => fetchAuth<any[]>(`/agents/`),
    });
    const availableAgents = allAgents.filter((a: any) => a.id !== agentId);

    useEffect(() => {
        if (!search || search.length < 1) { setSearchResults([]); return; }
        const t = setTimeout(() => {
            fetchAuth<any[]>(`/enterprise/org/members?search=${encodeURIComponent(search)}`).then(setSearchResults);
        }, 300);
        return () => clearTimeout(t);
    }, [search]);

    const addRelationship = async () => {
        if (!adding) return;
        const existing = relationships.map((r: any) => ({ member_id: r.member_id, relation: r.relation, description: r.description }));
        existing.push({ member_id: adding.id, relation, description });
        await fetchAuth(`/agents/${agentId}/relationships/`, { method: 'PUT', body: JSON.stringify({ relationships: existing }) });
        setAdding(null); setSearch(''); setRelation('collaborator'); setDescription('');
        refetch();
    };
    const removeRelationship = async (relId: string) => {
        await fetchAuth(`/agents/${agentId}/relationships/${relId}`, { method: 'DELETE' });
        refetch();
    };
    const startEditRelationship = (r: any) => {
        setEditingId(r.id);
        setEditRelation(r.relation || 'collaborator');
        setEditDescription(r.description || '');
    };
    const saveEditRelationship = async (targetId: string) => {
        const updated = relationships.map((r: any) => ({
            member_id: r.member_id,
            relation: r.id === targetId ? editRelation : r.relation,
            description: r.id === targetId ? editDescription : r.description,
        }));
        await fetchAuth(`/agents/${agentId}/relationships/`, { method: 'PUT', body: JSON.stringify({ relationships: updated }) });
        setEditingId(null);
        refetch();
    };
    const addAgentRelationship = async () => {
        if (!selectedAgentId) return;
        const existing = agentRelationships.map((r: any) => ({ target_agent_id: r.target_agent_id, relation: r.relation, description: r.description }));
        existing.push({ target_agent_id: selectedAgentId, relation: agentRelation, description: agentDescription });
        await fetchAuth(`/agents/${agentId}/relationships/agents`, { method: 'PUT', body: JSON.stringify({ relationships: existing }) });
        setAddingAgent(false); setSelectedAgentId(''); setAgentRelation('collaborator'); setAgentDescription('');
        refetchAgentRels();
    };
    const removeAgentRelationship = async (relId: string) => {
        await fetchAuth(`/agents/${agentId}/relationships/agents/${relId}`, { method: 'DELETE' });
        refetchAgentRels();
    };
    const startEditAgentRelationship = (r: any) => {
        setEditingAgentId(r.id);
        setEditAgentRelation(r.relation || 'collaborator');
        setEditAgentDescription(r.description || '');
    };
    const saveEditAgentRelationship = async (targetId: string) => {
        const updated = agentRelationships.map((r: any) => ({
            target_agent_id: r.target_agent_id,
            relation: r.id === targetId ? editAgentRelation : r.relation,
            description: r.id === targetId ? editAgentDescription : r.description,
        }));
        await fetchAuth(`/agents/${agentId}/relationships/agents`, { method: 'PUT', body: JSON.stringify({ relationships: updated }) });
        setEditingAgentId(null);
        refetchAgentRels();
    };

    return (
        <div>
            {/* ── Human Relationships ── */}
            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '12px' }}>{t('agent.detail.humanRelationships')}</h4>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>{t('agent.detail.humanRelationships')}</p>
                {relationships.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '16px' }}>
                        {relationships.map((r: any) => (
                            <div key={r.id} style={{ borderRadius: '8px', border: '1px solid var(--border-subtle)', overflow: 'hidden' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px' }}>
                                    <div style={{ width: '36px', height: '36px', borderRadius: '50%', background: 'rgba(224,238,238,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px', fontWeight: 600, flexShrink: 0 }}>{r.member?.name?.[0] || '?'}</div>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ fontWeight: 600, fontSize: '13px' }}>{r.member?.name || '?'} <span className="badge" style={{ fontSize: '10px', marginLeft: '4px' }}>{r.relation_label}</span></div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{r.member?.title || ''} · {r.member?.department_path || ''}</div>
                                        {r.description && editingId !== r.id && <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>{r.description}</div>}
                                    </div>
                                    {!readOnly && editingId !== r.id && (
                                        <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                                            <button className="btn btn-ghost" style={{ fontSize: '12px' }} onClick={() => startEditRelationship(r)}>{t('common.edit', 'Edit')}</button>
                                            <button className="btn btn-ghost" style={{ color: 'var(--error)', fontSize: '12px' }} onClick={() => removeRelationship(r.id)}>{t('common.delete')}</button>
                                        </div>
                                    )}
                                </div>
                                {editingId === r.id && (
                                    <div style={{ padding: '0 10px 10px', borderTop: '1px solid var(--border-subtle)', background: 'var(--bg-elevated)' }}>
                                        <div style={{ display: 'flex', gap: '8px', marginTop: '8px', marginBottom: '8px' }}>
                                            <select className="input" value={editRelation} onChange={e => setEditRelation(e.target.value)} style={{ width: '140px', fontSize: '12px' }}>
                                                {getRelationOptions(t).map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
                                            </select>
                                        </div>
                                        <textarea className="input" value={editDescription} onChange={e => setEditDescription(e.target.value)} rows={2} style={{ fontSize: '12px', resize: 'vertical', marginBottom: '8px', width: '100%' }} placeholder={t('agent.detail.descriptionPlaceholder', 'Description...')} />
                                        <div style={{ display: 'flex', gap: '8px' }}>
                                            <button className="btn btn-primary" style={{ fontSize: '12px' }} onClick={() => saveEditRelationship(r.id)}>{t('common.save', 'Save')}</button>
                                            <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setEditingId(null)}>{t('common.cancel')}</button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                )}
                {!readOnly && !adding && (
                    <div style={{ position: 'relative' }}>
                        <input className="input" placeholder={t("agent.detail.searchMembers")} value={search} onChange={e => setSearch(e.target.value)} style={{ fontSize: '13px' }} />
                        {searchResults.length > 0 && (
                            <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)', borderRadius: '6px', marginTop: '4px', maxHeight: '200px', overflowY: 'auto', zIndex: 10, boxShadow: '0 4px 12px rgba(0,0,0,0.15)' }}>
                                {searchResults.map((m: any) => (
                                    <div key={m.id} style={{ padding: '8px 12px', cursor: 'pointer', fontSize: '13px', borderBottom: '1px solid var(--border-subtle)' }}
                                        onClick={() => { setAdding(m); setSearch(''); setSearchResults([]); }}
                                        onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-elevated)')}
                                        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                                        <div style={{ fontWeight: 500 }}>{m.name}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{m.title} · {m.department_path}</div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}
                {!readOnly && adding && (
                    <div style={{ border: '1px solid var(--accent-primary)', borderRadius: '8px', padding: '12px', background: 'var(--bg-elevated)' }}>
                        <div style={{ fontWeight: 600, fontSize: '14px', marginBottom: '8px' }}>{t('agent.detail.addRelationship')}: {adding.name} <span style={{ fontSize: '12px', fontWeight: 400, color: 'var(--text-tertiary)' }}>({adding.title} · {adding.department_path})</span></div>
                        <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
                            <select className="input" value={relation} onChange={e => setRelation(e.target.value)} style={{ width: '140px', fontSize: '12px' }}>
                                {getRelationOptions(t).map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
                            </select>
                        </div>
                        <textarea className="input" placeholder="" value={description} onChange={e => setDescription(e.target.value)} rows={2} style={{ fontSize: '12px', resize: 'vertical', marginBottom: '8px' }} />
                        <div style={{ display: 'flex', gap: '8px' }}>
                            <button className="btn btn-primary" style={{ fontSize: '12px' }} onClick={addRelationship}>{t('common.confirm')}</button>
                            <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => { setAdding(null); setDescription(''); }}>{t('common.cancel')}</button>
                        </div>
                    </div>
                )}
            </div>
            {/* ── Agent-to-Agent Relationships ── */}
            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '12px' }}>{t('agent.detail.agentRelationships')}</h4>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>{t('agent.detail.agentRelationships')}</p>
                {agentRelationships.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '16px' }}>
                        {agentRelationships.map((r: any) => (
                            <div key={r.id} style={{ borderRadius: '8px', border: '1px solid rgba(16,185,129,0.3)', background: 'rgba(16,185,129,0.05)', overflow: 'hidden' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px' }}>
                                    <div style={{ width: '36px', height: '36px', borderRadius: '50%', background: 'rgba(16,185,129,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px', flexShrink: 0 }}>A</div>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ fontWeight: 600, fontSize: '13px' }}>{r.target_agent?.name || '?'} <span className="badge" style={{ fontSize: '10px', marginLeft: '4px', background: 'rgba(16,185,129,0.15)', color: 'rgb(16,185,129)' }}>{r.relation_label}</span></div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{r.target_agent?.role_description || 'Agent'}</div>
                                        {r.description && editingAgentId !== r.id && <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>{r.description}</div>}
                                    </div>
                                    {!readOnly && editingAgentId !== r.id && (
                                        <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                                            <button className="btn btn-ghost" style={{ fontSize: '12px' }} onClick={() => startEditAgentRelationship(r)}>{t('common.edit', 'Edit')}</button>
                                            <button className="btn btn-ghost" style={{ color: 'var(--error)', fontSize: '12px' }} onClick={() => removeAgentRelationship(r.id)}>{t('common.delete')}</button>
                                        </div>
                                    )}
                                </div>
                                {editingAgentId === r.id && (
                                    <div style={{ padding: '0 10px 10px', borderTop: '1px solid rgba(16,185,129,0.2)', background: 'var(--bg-elevated)' }}>
                                        <div style={{ display: 'flex', gap: '8px', marginTop: '8px', marginBottom: '8px' }}>
                                            <select className="input" value={editAgentRelation} onChange={e => setEditAgentRelation(e.target.value)} style={{ width: '140px', fontSize: '12px' }}>
                                                {getAgentRelationOptions(t).map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
                                            </select>
                                        </div>
                                        <textarea className="input" value={editAgentDescription} onChange={e => setEditAgentDescription(e.target.value)} rows={2} style={{ fontSize: '12px', resize: 'vertical', marginBottom: '8px', width: '100%' }} placeholder={t('agent.detail.descriptionPlaceholder', 'Description...')} />
                                        <div style={{ display: 'flex', gap: '8px' }}>
                                            <button className="btn btn-primary" style={{ fontSize: '12px' }} onClick={() => saveEditAgentRelationship(r.id)}>{t('common.save', 'Save')}</button>
                                            <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setEditingAgentId(null)}>{t('common.cancel')}</button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                )}
                {!readOnly && !addingAgent && (
                    <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setAddingAgent(true)}>+ {t('agent.detail.addRelationship')}</button>
                )}
                {!readOnly && addingAgent && (
                    <div style={{ border: '1px solid rgba(16,185,129,0.5)', borderRadius: '8px', padding: '12px', background: 'var(--bg-elevated)' }}>
                        <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
                            <select className="input" value={selectedAgentId} onChange={e => setSelectedAgentId(e.target.value)} style={{ flex: 1, fontSize: '12px' }}>
                                <option value="">— Select —</option>
                                {availableAgents.map((a: any) => <option key={a.id} value={a.id}>{a.name} — {a.role_description || 'Agent'}</option>)}
                            </select>
                            <select className="input" value={agentRelation} onChange={e => setAgentRelation(e.target.value)} style={{ width: '140px', fontSize: '12px' }}>
                                {getAgentRelationOptions(t).map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
                            </select>
                        </div>
                        <textarea className="input" placeholder="" value={agentDescription} onChange={e => setAgentDescription(e.target.value)} rows={2} style={{ fontSize: '12px', resize: 'vertical', marginBottom: '8px' }} />
                        <div style={{ display: 'flex', gap: '8px' }}>
                            <button className="btn btn-primary" style={{ fontSize: '12px' }} onClick={addAgentRelationship} disabled={!selectedAgentId}>{t('common.confirm')}</button>
                            <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => { setAddingAgent(false); setAgentDescription(''); setSelectedAgentId(''); }}>{t('common.cancel')}</button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

function AgentDetailInner() {
    const { t, i18n } = useTranslation();
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const location = useLocation();
    const validTabs = ['status', 'aware', 'mind', 'tools', 'skills', 'relationships', 'workspace', 'chat', 'activityLog', 'approvals', 'settings'];
    const hashTab = location.hash?.replace('#', '');
    const [activeTab, setActiveTabRaw] = useState<string>(hashTab && validTabs.includes(hashTab) ? hashTab : 'status');

    // Sync URL hash when tab changes
    const setActiveTab = (tab: string) => {
        setActiveTabRaw(tab);
        window.history.replaceState(null, '', `#${tab}`);
    };

    const { data: agent, isLoading } = useQuery({
        queryKey: ['agent', id],
        queryFn: () => agentApi.get(id!),
        enabled: !!id,
    });

    // ── Aware tab data: triggers ──
    const { data: awareTriggers = [], refetch: refetchTriggers } = useQuery({
        queryKey: ['triggers', id],
        queryFn: () => triggerApi.list(id!),
        enabled: !!id && activeTab === 'aware',
        refetchInterval: activeTab === 'aware' ? 5000 : false,
    });

    // ── Aware tab data: focus.md ──
    const { data: focusFile } = useQuery({
        queryKey: ['file', id, 'focus.md'],
        queryFn: () => fileApi.read(id!, 'focus.md').catch(() => null),
        enabled: !!id && activeTab === 'aware',
    });

    // ── Aware tab data: task_history.md ──
    const { data: taskHistoryFile } = useQuery({
        queryKey: ['file', id, 'task_history.md'],
        queryFn: () => fileApi.read(id!, 'task_history.md').catch(() => null),
        enabled: !!id && activeTab === 'aware',
    });

    // ── Aware tab data: pulse sessions (trigger monologues) ──
    const { data: pulseSessions = [] } = useQuery({
        queryKey: ['pulse-sessions', id],
        queryFn: async () => {
            const tkn = localStorage.getItem('token');
            const res = await fetch(`/api/agents/${id}/sessions?scope=all`, { headers: { Authorization: `Bearer ${tkn}` } });
            if (!res.ok) return [];
            const all = await res.json();
            return all.filter((s: any) => s.source_channel === 'trigger').slice(0, 20);
        },
        enabled: !!id && activeTab === 'aware',
        refetchInterval: activeTab === 'aware' ? 10000 : false,
    });

    // ── Aware tab state ──
    const [expandedFocus, setExpandedFocus] = useState<string | null>(null);
    const [expandedReflection, setExpandedReflection] = useState<string | null>(null);
    const [reflectionMessages, setReflectionMessages] = useState<Record<string, any[]>>({});
    const [showAllFocus, setShowAllFocus] = useState(false);
    const [showCompletedFocus, setShowCompletedFocus] = useState(false);
    const [showAllTriggers, setShowAllTriggers] = useState(false);
    const [showAllReflections, setShowAllReflections] = useState(false);
    const SECTION_PAGE_SIZE = 5;

    const { data: soulContent } = useQuery({
        queryKey: ['file', id, 'soul.md'],
        queryFn: () => fileApi.read(id!, 'soul.md'),
        enabled: !!id && activeTab === 'mind',
    });

    const { data: memoryFiles = [] } = useQuery({
        queryKey: ['files', id, 'memory'],
        queryFn: () => fileApi.list(id!, 'memory'),
        enabled: !!id && activeTab === 'mind',
    });
    const [expandedMemory, setExpandedMemory] = useState<string | null>(null);
    const { data: memoryFileContent } = useQuery({
        queryKey: ['file', id, expandedMemory],
        queryFn: () => fileApi.read(id!, expandedMemory!),
        enabled: !!id && !!expandedMemory,
    });

    const { data: skillFiles = [] } = useQuery({
        queryKey: ['files', id, 'skills'],
        queryFn: () => fileApi.list(id!, 'skills'),
        enabled: !!id && activeTab === 'skills',
    });

    const [workspacePath, setWorkspacePath] = useState('workspace');
    const { data: workspaceFiles = [] } = useQuery({
        queryKey: ['files', id, workspacePath],
        queryFn: () => fileApi.list(id!, workspacePath),
        enabled: !!id && activeTab === 'workspace',
    });

    const { data: activityLogs = [] } = useQuery({
        queryKey: ['activity', id],
        queryFn: () => activityApi.list(id!, 100),
        enabled: !!id && (activeTab === 'activityLog' || activeTab === 'status'),
        refetchInterval: activeTab === 'activityLog' ? 10000 : false,
    });

    // Chat history
    // ── Session state (replaces old conversations query) ──────────────────
    const [sessions, setSessions] = useState<any[]>([]);
    const [allSessions, setAllSessions] = useState<any[]>([]);
    const [activeSession, setActiveSession] = useState<any | null>(null);
    const [chatScope, setChatScope] = useState<'mine' | 'all'>('mine');
    const [allUserFilter, setAllUserFilter] = useState<string>('');  // filter by username in All Users
    const [historyMsgs, setHistoryMsgs] = useState<any[]>([]);
    const [sessionsLoading, setSessionsLoading] = useState(false);
    const [agentExpired, setAgentExpired] = useState(false);

    const fetchMySessions = async (silent = false) => {
        if (!id) return;
        if (!silent) setSessionsLoading(true);
        try {
            const tkn = localStorage.getItem('token');
            const res = await fetch(`/api/agents/${id}/sessions?scope=mine`, { headers: { Authorization: `Bearer ${tkn}` } });
            if (res.ok) { const data = await res.json(); setSessions(data); return data; }
        } catch { }
        if (!silent) setSessionsLoading(false);
        return [];
    };

    const fetchAllSessions = async () => {
        if (!id) return;
        try {
            const tkn = localStorage.getItem('token');
            const res = await fetch(`/api/agents/${id}/sessions?scope=all`, { headers: { Authorization: `Bearer ${tkn}` } });
            if (res.ok) setAllSessions(await res.json());
        } catch { }
    };

    const createNewSession = async () => {
        try {
            const tkn = localStorage.getItem('token');
            const res = await fetch(`/api/agents/${id}/sessions`, {
                method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tkn}` },
                body: JSON.stringify({}),
            });
            if (res.ok) {
                const newSess = await res.json();
                setSessions(prev => [newSess, ...prev]);
                setChatMessages([]);
                setHistoryMsgs([]);
                setActiveSession(newSess);
            } else {
                const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
                console.error('Failed to create session:', err);
                alert(`Failed to create session: ${err.detail || res.status}`);
            }
        } catch (err: any) {
            console.error('Failed to create session:', err);
            alert(`Failed to create session: ${err.message || err}`);
        }
    };

    const selectSession = async (sess: any) => {
        setChatMessages([]);
        setHistoryMsgs([]);
        setActiveSession(sess);
        // Always load stored messages for the selected session
        const tkn = localStorage.getItem('token');
        const res = await fetch(`/api/agents/${id}/sessions/${sess.id}/messages`, { headers: { Authorization: `Bearer ${tkn}` } });
        if (res.ok) {
            const msgs = await res.json();
            // Agent-to-agent sessions are always read-only
            const isAgentSession = sess.source_channel === 'agent' || sess.participant_type === 'agent';
            if (!isAgentSession && sess.user_id === String(currentUser?.id)) {
                // Own session: load into chatMessages so WS can append new replies seamlessly
                setChatMessages(msgs.map((m: any) => parseChatMsg({
                    role: m.role, content: m.content,
                    ...(m.toolName && { toolName: m.toolName, toolArgs: m.toolArgs, toolStatus: m.toolStatus, toolResult: m.toolResult }),
                    ...(m.thinking && { thinking: m.thinking }),
                    ...(m.created_at && { timestamp: m.created_at }),
                })));
            } else {
                // Other user's session or agent-to-agent: read-only view
                setHistoryMsgs(msgs);
            }
        }
    };

    // Websocket chat state (for 'me' conversation)
    const token = useAuthStore((s) => s.token);
    const currentUser = useAuthStore((s) => s.user);
    const isAdmin = currentUser?.role === 'platform_admin' || currentUser?.role === 'org_admin';

    // Expiry editor modal state
    const [showExpiryModal, setShowExpiryModal] = useState(false);
    const [expiryValue, setExpiryValue] = useState('');       // datetime-local string or ''
    const [expirySaving, setExpirySaving] = useState(false);

    const openExpiryModal = () => {
        const cur = (agent as any)?.expires_at;
        // Convert ISO to datetime-local format (YYYY-MM-DDTHH:MM)
        setExpiryValue(cur ? new Date(cur).toISOString().slice(0, 16) : '');
        setShowExpiryModal(true);
    };

    const addHours = (h: number) => {
        const base = (agent as any)?.expires_at ? new Date((agent as any).expires_at) : new Date();
        const next = new Date(base.getTime() + h * 3600_000);
        setExpiryValue(next.toISOString().slice(0, 16));
    };

    const saveExpiry = async (permanent = false) => {
        setExpirySaving(true);
        try {
            const token = localStorage.getItem('token');
            const body = permanent ? { expires_at: null } : { expires_at: expiryValue ? new Date(expiryValue).toISOString() : null };
            await fetch(`/api/agents/${id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify(body),
            });
            queryClient.invalidateQueries({ queryKey: ['agent', id] });
            setShowExpiryModal(false);
        } catch (e) { alert('Failed: ' + e); }
        setExpirySaving(false);
    };
    interface ChatMsg { role: 'user' | 'assistant' | 'tool_call'; content: string; fileName?: string; toolName?: string; toolArgs?: any; toolStatus?: 'running' | 'done'; toolResult?: string; thinking?: string; imageUrl?: string; timestamp?: string; }
    const [chatMessages, setChatMessages] = useState<ChatMsg[]>([]);
    const [chatInput, setChatInput] = useState('');
    const [wsConnected, setWsConnected] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [isWaiting, setIsWaiting] = useState(false);
    const [isStreaming, setIsStreaming] = useState(false);
    const [uploadProgress, setUploadProgress] = useState(-1);
    const uploadAbortRef = useRef<(() => void) | null>(null);
    const [attachedFile, setAttachedFile] = useState<{ name: string; text: string; path?: string; imageUrl?: string } | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const chatEndRef = useRef<HTMLDivElement>(null);
    const chatContainerRef = useRef<HTMLDivElement>(null);
    const chatInputRef = useRef<HTMLInputElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    // Settings form local state
    const [settingsForm, setSettingsForm] = useState({
        primary_model_id: '',
        fallback_model_id: '',
        context_window_size: 100,
        max_tool_rounds: 50,
        max_tokens_per_day: '' as string | number,
        max_tokens_per_month: '' as string | number,
        max_triggers: 20,
        min_poll_interval_min: 5,
        webhook_rate_limit: 5,
    });
    const [settingsSaving, setSettingsSaving] = useState(false);
    const [settingsSaved, setSettingsSaved] = useState(false);
    const [settingsError, setSettingsError] = useState('');
    const settingsInitRef = useRef(false);

    // Sync settings form from server data on load
    useEffect(() => {
        if (agent && !settingsInitRef.current) {
            setSettingsForm({
                primary_model_id: agent.primary_model_id || '',
                fallback_model_id: agent.fallback_model_id || '',
                context_window_size: agent.context_window_size ?? 100,
                max_tool_rounds: (agent as any).max_tool_rounds ?? 50,
                max_tokens_per_day: agent.max_tokens_per_day || '',
                max_tokens_per_month: agent.max_tokens_per_month || '',
                max_triggers: (agent as any).max_triggers ?? 20,
                min_poll_interval_min: (agent as any).min_poll_interval_min ?? 5,
                webhook_rate_limit: (agent as any).webhook_rate_limit ?? 5,
            });
            settingsInitRef.current = true;
        }
    }, [agent]);

    // Welcome message editor state (must be at top level -- not inside IIFE)
    const [wmDraft, setWmDraft] = useState('');
    const [wmSaved, setWmSaved] = useState(false);
    useEffect(() => { setWmDraft((agent as any)?.welcome_message || ''); }, [(agent as any)?.welcome_message]);

    // Reset cached state when switching to a different agent
    const prevIdRef = useRef(id);
    useEffect(() => {
        if (id && id !== prevIdRef.current) {
            prevIdRef.current = id;
            settingsInitRef.current = false;
            setSettingsSaved(false);
            setSettingsError('');
            setWmDraft('');
            setWmSaved(false);
            // Invalidate all queries for the old agent to force fresh data
            queryClient.invalidateQueries({ queryKey: ['agent', id] });
            // Re-apply hash so refresh preserves the current tab
            window.history.replaceState(null, '', `#${activeTab}`);
        }
    }, [id]);

    // Load chat history + connect websocket when chat tab is active
    const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'];
    const parseChatMsg = (msg: ChatMsg): ChatMsg => {
        if (msg.role !== 'user') return msg;
        let parsed = { ...msg };
        // Standard web chat format: [file:name.pdf]\ncontent
        const newFmt = msg.content.match(/^\[file:([^\]]+)\]\n?/);
        if (newFmt) { parsed = { ...msg, fileName: newFmt[1], content: msg.content.slice(newFmt[0].length).trim() }; }
        // Feishu/Slack channel format: [文件已上传: workspace/uploads/name]
        const chanFmt = !newFmt && msg.content.match(/^\[\u6587\u4ef6\u5df2\u4e0a\u4f20: (?:workspace\/uploads\/)?([^\]\n]+)\]/);
        if (chanFmt) {
            const raw = chanFmt[1]; const fileName = raw.split('/').pop() || raw;
            parsed = { ...msg, fileName, content: msg.content.slice(chanFmt[0].length).trim() };
        }
        // Old format: [File: name.pdf]\nFile location:...\nQuestion: user_msg
        const oldFmt = !newFmt && !chanFmt && msg.content.match(/^\[File: ([^\]]+)\]/);
        if (oldFmt) {
            const fileName = oldFmt[1];
            const qMatch = msg.content.match(/\nQuestion: ([\s\S]+)$/);
            parsed = { ...msg, fileName, content: qMatch ? qMatch[1].trim() : '' };
        }
        // If file is an image and no imageUrl yet, build download URL for preview
        if (parsed.fileName && !parsed.imageUrl && id) {
            const ext = parsed.fileName.split('.').pop()?.toLowerCase() || '';
            if (IMAGE_EXTS.includes(ext)) {
                parsed.imageUrl = `/api/agents/${id}/files/download?path=workspace/uploads/${encodeURIComponent(parsed.fileName)}&token=${token}`;
            }
        }
        return parsed;
    };


    // Reset state whenever the viewed agent changes
    useEffect(() => {
        setActiveSession(null);
        setChatMessages([]);
        setHistoryMsgs([]);
        setChatScope('mine');
        setAgentExpired(false);
        settingsInitRef.current = false;
    }, [id]);

    useEffect(() => {
        if (!id || !token || activeTab !== 'chat') return;
        // Load sessions when entering chat tab; auto-select first and load its history
        fetchMySessions().then((data: any) => {
            setSessionsLoading(false);
            if (data && data.length > 0) selectSession(data[0]);
        });
    }, [id, activeTab]);

    useEffect(() => {
        if (!id || !token || activeTab !== 'chat') return;
        if (!activeSession) return;  // wait for session to be set
        // Only connect WS for own sessions (not other users' and not agent-to-agent)
        const isAgentSession = activeSession.source_channel === 'agent' || activeSession.participant_type === 'agent';
        if (isAgentSession) return;
        if (activeSession.user_id && currentUser && activeSession.user_id !== String(currentUser.id)) return;
        let cancelled = false;
        const sessionParam = activeSession?.id ? `&session_id=${activeSession.id}` : '';
        const connect = () => {
            if (cancelled) return;
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat/${id}?token=${token}${sessionParam}`);
            ws.onopen = () => { if (cancelled) { ws.close(); return; } setWsConnected(true); wsRef.current = ws; };
            ws.onclose = (e) => {
                if (e.code === 4003 || e.code === 4002) {
                    // 4003 = Agent expired, 4002 = Config error (no model, setup failed)
                    if (e.code === 4003) setAgentExpired(true);
                    setWsConnected(false);
                    setIsWaiting(false);
                    setIsStreaming(false);
                    return;
                }
                if (!cancelled) { setWsConnected(false); setIsWaiting(false); setIsStreaming(false); setTimeout(connect, 2000); }
            };
            ws.onerror = () => { if (!cancelled) setWsConnected(false); };
            ws.onmessage = (e) => {
                const d = JSON.parse(e.data);
                if (['thinking', 'chunk', 'tool_call', 'done', 'error', 'quota_exceeded'].includes(d.type)) {
                    setIsWaiting(false);
                    if (['thinking', 'chunk', 'tool_call'].includes(d.type)) setIsStreaming(true);
                    if (['done', 'error', 'quota_exceeded'].includes(d.type)) setIsStreaming(false);
                }

                if (d.type === 'thinking') {
                    setChatMessages(prev => {
                        const last = prev[prev.length - 1];
                        if (last && last.role === 'assistant' && (last as any)._streaming) {
                            return [...prev.slice(0, -1), { ...last, thinking: (last.thinking || '') + d.content } as any];
                        }
                        return [...prev, { role: 'assistant', content: '', thinking: d.content, _streaming: true } as any];
                    });
                } else if (d.type === 'tool_call') {
                    setChatMessages(prev => {
                        const toolMsg: ChatMsg = { role: 'tool_call', content: '', toolName: d.name, toolArgs: d.args, toolStatus: d.status, toolResult: d.result };
                        if (d.status === 'done') {
                            const lastIdx = prev.length - 1;
                            const last = prev[lastIdx];
                            if (last && last.role === 'tool_call' && last.toolName === d.name && last.toolStatus === 'running') return [...prev.slice(0, lastIdx), toolMsg];
                        }
                        return [...prev, toolMsg];
                    });
                } else if (d.type === 'chunk') {
                    setChatMessages(prev => {
                        const last = prev[prev.length - 1];
                        if (last && last.role === 'assistant' && (last as any)._streaming) return [...prev.slice(0, -1), { ...last, content: last.content + d.content } as any];
                        return [...prev, { role: 'assistant', content: d.content, _streaming: true } as any];
                    });
                } else if (d.type === 'done') {
                    setChatMessages(prev => {
                        const last = prev[prev.length - 1];
                        const thinking = (last && last.role === 'assistant' && (last as any)._streaming) ? last.thinking : undefined;
                        if (last && last.role === 'assistant' && (last as any)._streaming) return [...prev.slice(0, -1), { role: 'assistant', content: d.content, thinking, timestamp: new Date().toISOString() }];
                        return [...prev, { role: d.role, content: d.content, timestamp: new Date().toISOString() }];
                    });
                    // Silently refresh session list to update last_message_at (no loading spinner)
                    fetchMySessions(true);
                } else if (d.type === 'error' || d.type === 'quota_exceeded') {
                    const msg = d.content || d.detail || d.message || 'Request denied';
                    // Only add message if not a duplicate of the last one
                    setChatMessages(prev => {
                        const last = prev[prev.length - 1];
                        if (last && last.role === 'assistant' && last.content === `⚠️ ${msg}`) return prev;
                        return [...prev, { role: 'assistant', content: `⚠️ ${msg}` }];
                    });
                    // Permanent errors — stop reconnecting
                    if (msg.includes('expired') || msg.includes('Setup failed') || msg.includes('no LLM model') || msg.includes('No model')) {
                        cancelled = true;
                        if (msg.includes('expired')) setAgentExpired(true);
                    }
                } else if (d.type === 'trigger_notification') {
                    // Trigger fired — show the result as a new assistant message
                    setChatMessages(prev => [...prev, { role: 'assistant', content: d.content }]);
                    fetchMySessions(true);
                } else {
                    setChatMessages(prev => [...prev, { role: d.role, content: d.content }]);
                }
            };
        };
        connect();
        return () => { cancelled = true; wsRef.current?.close(); wsRef.current = null; setWsConnected(false); };
    }, [id, token, activeTab, activeSession?.id]);

    // Smart scroll: only auto-scroll if user is at the bottom
    const isNearBottom = useRef(true);
    const isFirstLoad = useRef(true);
    const [showScrollBtn, setShowScrollBtn] = useState(false);
    // Read-only history scroll-to-bottom
    const historyContainerRef = useRef<HTMLDivElement>(null);
    const [showHistoryScrollBtn, setShowHistoryScrollBtn] = useState(false);
    const handleHistoryScroll = () => {
        const el = historyContainerRef.current;
        if (!el) return;
        const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        setShowHistoryScrollBtn(distFromBottom > 200);
    };
    const scrollHistoryToBottom = () => {
        const el = historyContainerRef.current;
        if (el) el.scrollTop = el.scrollHeight;
        setShowHistoryScrollBtn(false);
    };
    // Auto-show button when history messages overflow the container
    useEffect(() => {
        const el = historyContainerRef.current;
        if (!el) return;
        // Use a small timeout to let the DOM render the messages first
        const timer = setTimeout(() => {
            const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
            setShowHistoryScrollBtn(distFromBottom > 200);
        }, 100);
        return () => clearTimeout(timer);
    }, [historyMsgs, activeSession?.id]);
    const handleChatScroll = () => {
        const el = chatContainerRef.current;
        if (!el) return;
        const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        isNearBottom.current = distFromBottom < 5;
        setShowScrollBtn(distFromBottom > 200);
    };
    const scrollToBottom = () => {
        chatEndRef.current?.scrollIntoView({ behavior: 'instant' as ScrollBehavior });
        setShowScrollBtn(false);
    };
    useEffect(() => {
        if (!chatEndRef.current) return;
        if (isFirstLoad.current && chatMessages.length > 0) {
            // First load: instant jump to bottom, no animation
            chatEndRef.current.scrollIntoView({ behavior: 'instant' as ScrollBehavior });
            isFirstLoad.current = false;
            // Auto-focus the input
            setTimeout(() => chatInputRef.current?.focus(), 100);
            return;
        }
        if (isNearBottom.current) {
            chatEndRef.current.scrollIntoView({ behavior: 'instant' as ScrollBehavior });
        }
    }, [chatMessages]);

    // Auto-focus input when switching sessions
    useEffect(() => {
        if (activeSession && activeTab === 'chat') {
            setTimeout(() => chatInputRef.current?.focus(), 150);
        }
    }, [activeSession?.id, activeTab]);

    const sendChatMsg = () => {
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
        if (!chatInput.trim() && !attachedFile) return;
        let userMsg = chatInput.trim();
        let contentForLLM = userMsg;
        if (attachedFile) {
            if (attachedFile.imageUrl && supportsVision) {
                // Vision model — embed image data for direct analysis
                const imageMarker = `[image_data:${attachedFile.imageUrl}]`;
                contentForLLM = userMsg ? `${imageMarker}\n${userMsg}` : `${imageMarker}\n请分析这张图片`;
                userMsg = userMsg || `[图片] ${attachedFile.name}`;
            } else if (attachedFile.imageUrl) {
                // Non-vision model — just reference the file path
                const wsPath = attachedFile.path || '';
                contentForLLM = userMsg
                    ? `[图片文件已上传: ${attachedFile.name}，保存在 ${wsPath}]\n\n${userMsg}`
                    : `[图片文件已上传: ${attachedFile.name}，保存在 ${wsPath}]\n请描述或处理这个图片文件。你可以使用 read_document 工具读取它。`;
                userMsg = userMsg || `[图片] ${attachedFile.name}`;
            } else {
                const wsPath = attachedFile.path || '';
                const codePath = wsPath.replace(/^workspace\//, '');
                const fileLoc = wsPath ? `\nFile location: ${wsPath} (for read_file/read_document tools)\nIn execute_code, use relative path: "${codePath}" (working directory is workspace/)` : '';
                const fc = `[File: ${attachedFile.name}]${fileLoc}\n\n${attachedFile.text}`;
                contentForLLM = userMsg ? `${fc}\n\nQuestion: ${userMsg}` : `Please analyze this file:\n\n${fc}`;
                userMsg = userMsg || `⌆ ${attachedFile.name}`;
            }
        }

        setIsWaiting(true);
        setIsStreaming(false);
        setChatMessages(prev => [...prev, { role: 'user', content: userMsg, fileName: attachedFile?.name, imageUrl: attachedFile?.imageUrl, timestamp: new Date().toISOString() }]);
        wsRef.current.send(JSON.stringify({ content: contentForLLM, display_content: userMsg, file_name: attachedFile?.name || '' }));
        setChatInput(''); setAttachedFile(null);
    };

    const handleChatFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0]; if (!file) return;
        setUploading(true); setUploadProgress(0);
        try {
            const { promise, abort } = uploadFileWithProgress(
                `/chat/upload`,
                file,
                (pct) => setUploadProgress(pct),
                id ? { agent_id: id } : undefined,
            );
            uploadAbortRef.current = abort;
            const data = await promise;
            setAttachedFile({ name: data.filename, text: data.extracted_text, path: data.workspace_path, imageUrl: data.image_data_url || undefined });
        } catch (err: any) {
            if (err?.message !== 'Upload cancelled') alert(t('agent.upload.failed'));
        } finally { setUploading(false); setUploadProgress(-1); uploadAbortRef.current = null; if (fileInputRef.current) fileInputRef.current.value = ''; }
    };

    // Clipboard paste handler — auto-upload pasted images
    const handlePaste = async (e: React.ClipboardEvent) => {
        const items = e.clipboardData?.items;
        if (!items) return;
        for (let i = 0; i < items.length; i++) {
            if (items[i].type.startsWith('image/')) {
                e.preventDefault();
                const blob = items[i].getAsFile();
                if (!blob) return;
                // Generate a filename from timestamp
                const ext = blob.type.split('/')[1] || 'png';
                const fileName = `paste-${Date.now()}.${ext}`;
                const file = new File([blob], fileName, { type: blob.type });
                setUploading(true); setUploadProgress(0);
                try {
                    const { promise, abort } = uploadFileWithProgress(
                        `/chat/upload`,
                        file,
                        (pct) => setUploadProgress(pct),
                        id ? { agent_id: id } : undefined,
                    );
                    uploadAbortRef.current = abort;
                    const data = await promise;
                    setAttachedFile({ name: data.filename, text: data.extracted_text, path: data.workspace_path, imageUrl: data.image_data_url || undefined });
                } catch (err: any) {
                    if (err?.message !== 'Upload cancelled') alert(t('agent.upload.failed'));
                } finally { setUploading(false); setUploadProgress(-1); uploadAbortRef.current = null; }
                return; // Only handle the first image
            }
        }
    };

    // Expandable activity log
    const [expandedLogId, setExpandedLogId] = useState<string | null>(null);
    const [logFilter, setLogFilter] = useState<string>('user'); // 'user' | 'backend' | 'heartbeat' | 'schedule' | 'messages'

    // Import skill from presets
    const [showImportSkillModal, setShowImportSkillModal] = useState(false);
    const [importingSkillId, setImportingSkillId] = useState<string | null>(null);
    const { data: globalSkillsForImport } = useQuery({
        queryKey: ['global-skills-for-import'],
        queryFn: () => skillApi.list(),
        enabled: showImportSkillModal,
    });

    const { data: schedules = [] } = useQuery({
        queryKey: ['schedules', id],
        queryFn: () => scheduleApi.list(id!),
        enabled: !!id && activeTab === 'tasks',
    });

    // Schedule form state
    const [showScheduleForm, setShowScheduleForm] = useState(false);
    const schedDefaults = { freq: 'daily', interval: 1, time: '09:00', weekdays: [1, 2, 3, 4, 5] };
    const [schedForm, setSchedForm] = useState({ name: '', instruction: '', schedule: JSON.stringify(schedDefaults), due_date: '' });

    const createScheduleMut = useMutation({
        mutationFn: () => {
            let sched: any;
            try { sched = JSON.parse(schedForm.schedule); } catch { sched = schedDefaults; }
            return scheduleApi.create(id!, { name: schedForm.name, instruction: schedForm.instruction, cron_expr: schedToCron(sched) });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['schedules', id] });
            setShowScheduleForm(false);
            setSchedForm({ name: '', instruction: '', schedule: JSON.stringify(schedDefaults), due_date: '' });
        },
        onError: (err: any) => {
            const msg = err?.detail || err?.message || String(err);
            alert(`Failed to create schedule: ${msg}`);
        },
    });

    const toggleScheduleMut = useMutation({
        mutationFn: ({ sid, enabled }: { sid: string; enabled: boolean }) =>
            scheduleApi.update(id!, sid, { is_enabled: enabled }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['schedules', id] }),
    });

    const deleteScheduleMut = useMutation({
        mutationFn: (sid: string) => scheduleApi.delete(id!, sid),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['schedules', id] }),
    });

    const triggerScheduleMut = useMutation({
        mutationFn: async (sid: string) => {
            const res = await scheduleApi.trigger(id!, sid);
            return res;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['schedules', id] });
            showToast('✅ Schedule triggered — executing in background', 'success');
        },
        onError: (err: any) => {
            const msg = err?.response?.data?.detail || err?.message || 'Failed to trigger schedule';
            showToast(msg, 'error');
        },
    });


    const { data: metrics } = useQuery({
        queryKey: ['metrics', id],
        queryFn: () => agentApi.metrics(id!).catch(() => null),
        enabled: !!id && activeTab === 'status',
        retry: false,
    });

    const { data: channelConfig } = useQuery({
        queryKey: ['channel', id],
        queryFn: () => channelApi.get(id!),
        enabled: !!id && activeTab === 'settings',
    });

    const { data: webhookData } = useQuery({
        queryKey: ['webhook-url', id],
        queryFn: () => channelApi.webhookUrl(id!),
        enabled: !!id && activeTab === 'settings',
    });

    const { data: llmModels = [] } = useQuery({
        queryKey: ['llm-models'],
        queryFn: () => enterpriseApi.llmModels(),
        enabled: activeTab === 'settings' || activeTab === 'status' || activeTab === 'chat',
    });

    const supportsVision = !!agent?.primary_model_id && llmModels.some(
        (m: any) => m.id === agent.primary_model_id && m.supports_vision
    );

    const { data: permData } = useQuery({
        queryKey: ['agent-permissions', id],
        queryFn: () => fetchAuth<any>(`/agents/${id}/permissions`),
        enabled: !!id && activeTab === 'settings',
    });

    // ─── Soul editor ─────────────────────────────────────
    const [soulEditing, setSoulEditing] = useState(false);
    const [soulDraft, setSoulDraft] = useState('');

    const saveSoul = useMutation({
        mutationFn: () => fileApi.write(id!, 'soul.md', soulDraft),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['file', id, 'soul.md'] });
            setSoulEditing(false);
        },
    });

    // ─── Channel config — Feishu ────────────────────────
    const [channelForm, setChannelForm] = useState({ app_id: '', app_secret: '', encrypt_key: '', connection_mode: 'webhook' });
    const [feishuEditing, setFeishuEditing] = useState(false);

    const saveChannel = useMutation({
        mutationFn: () => channelApi.create(id!, {
            channel_type: 'feishu', app_id: channelForm.app_id,
            app_secret: channelForm.app_secret, encrypt_key: channelForm.encrypt_key || undefined,
            extra_config: { connection_mode: channelForm.connection_mode }
        }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['channel', id] }),
    });

    // ─── Channel config — Slack ──────────────────────────
    const [slackForm, setSlackForm] = useState({ bot_token: '', signing_secret: '' });
    const [slackEditing, setSlackEditing] = useState(false);
    const { data: slackConfig } = useQuery({
        queryKey: ['slack-channel', id],
        queryFn: () => fetchAuth<any>(`/agents/${id}/slack-channel`).catch(() => null),
        enabled: !!id && activeTab === 'settings',
    });
    const { data: slackWebhookData } = useQuery({
        queryKey: ['slack-webhook-url', id],
        queryFn: () => fetchAuth<any>(`/agents/${id}/slack-channel/webhook-url`),
        enabled: !!id && activeTab === 'settings',
    });
    const saveSlack = useMutation({
        mutationFn: () => fetchAuth(`/agents/${id}/slack-channel`, { method: 'POST', body: JSON.stringify(slackForm) }),
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['slack-channel', id] }); setSlackForm({ bot_token: '', signing_secret: '' }); },
    });
    const deleteSlack = useMutation({
        mutationFn: () => fetchAuth(`/agents/${id}/slack-channel`, { method: 'DELETE' }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['slack-channel', id] }),
    });

    // ─── Channel config — Discord ────────────────────────
    const [discordForm, setDiscordForm] = useState({ application_id: '', bot_token: '', public_key: '' });
    const [discordEditing, setDiscordEditing] = useState(false);

    // ─── Channel config — Microsoft Teams ────────────────────────
    const [teamsForm, setTeamsForm] = useState({ app_id: '', app_secret: '', tenant_id: '' });
    const [teamsEditing, setTeamsEditing] = useState(false);
    const { data: teamsConfig } = useQuery({
        queryKey: ['teams-channel', id],
        queryFn: () => fetchAuth<any>(`/agents/${id}/teams-channel`).catch(() => null),
        enabled: !!id && activeTab === 'settings',
    });
    const { data: teamsWebhookData } = useQuery({
        queryKey: ['teams-webhook-url', id],
        queryFn: () => fetchAuth<any>(`/agents/${id}/teams-channel/webhook-url`).catch(() => null),
        enabled: !!id && activeTab === 'settings',
    });
    const saveTeams = useMutation({
        mutationFn: () => fetchAuth(`/agents/${id}/teams-channel`, { method: 'POST', body: JSON.stringify(teamsForm) }),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['teams-channel', id] });
            queryClient.invalidateQueries({ queryKey: ['teams-webhook-url', id] });
            setTeamsForm({ app_id: '', app_secret: '', tenant_id: '' });
        },
    });
    const deleteTeams = useMutation({
        mutationFn: () => fetchAuth(`/agents/${id}/teams-channel`, { method: 'DELETE' }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['teams-channel', id] }),
    });

    // Collapsible state for channel settings sections
    const [slackOpen, setSlackOpen] = useState(false);
    const [discordOpen, setDiscordOpen] = useState(false);
    const [teamsOpen, setTeamsOpen] = useState(false);
    const [feishuOpen, setFeishuOpen] = useState(false);
    const [atlassianOpen, setAtlassianOpen] = useState(false);
    const [dingtalkOpen, setDingtalkOpen] = useState(false);
    const [wecomOpen, setWecomOpen] = useState(false);

    // ─── Channel config — DingTalk ───────────────────────
    const [dingtalkForm, setDingtalkForm] = useState({ app_key: '', app_secret: '' });
    const [dingtalkEditing, setDingtalkEditing] = useState(false);
    const { data: dingtalkConfig } = useQuery({
        queryKey: ['dingtalk-channel', id],
        queryFn: () => fetchAuth<any>(`/agents/${id}/dingtalk-channel`).catch(() => null),
        enabled: !!id && activeTab === 'settings',
    });
    const saveDingtalk = useMutation({
        mutationFn: () => fetchAuth(`/agents/${id}/dingtalk-channel`, { method: 'POST', body: JSON.stringify(dingtalkForm) }),
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['dingtalk-channel', id] }); setDingtalkForm({ app_key: '', app_secret: '' }); },
    });
    const deleteDingtalk = useMutation({
        mutationFn: () => fetchAuth(`/agents/${id}/dingtalk-channel`, { method: 'DELETE' }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dingtalk-channel', id] }),
    });

    // ─── Channel config — WeCom ──────────────────────────
    const [wecomForm, setWecomForm] = useState({ connection_mode: 'websocket' as 'websocket' | 'webhook', bot_id: '', bot_secret: '', corp_id: '', wecom_agent_id: '', secret: '', token: '', encoding_aes_key: '' });
    const [wecomEditing, setWecomEditing] = useState(false);
    const { data: wecomConfig } = useQuery({
        queryKey: ['wecom-channel', id],
        queryFn: () => fetchAuth<any>(`/agents/${id}/wecom-channel`).catch(() => null),
        enabled: !!id && activeTab === 'settings',
    });
    const { data: wecomWebhookData } = useQuery({
        queryKey: ['wecom-webhook-url', id],
        queryFn: () => fetchAuth<any>(`/agents/${id}/wecom-channel/webhook-url`),
        enabled: !!id && activeTab === 'settings',
    });
    const saveWecom = useMutation({
        mutationFn: () => fetchAuth(`/agents/${id}/wecom-channel`, { method: 'POST', body: JSON.stringify(wecomForm) }),
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['wecom-channel', id] }); setWecomForm({ connection_mode: 'websocket', bot_id: '', bot_secret: '', corp_id: '', wecom_agent_id: '', secret: '', token: '', encoding_aes_key: '' }); },
    });
    const deleteWecom = useMutation({
        mutationFn: () => fetchAuth(`/agents/${id}/wecom-channel`, { method: 'DELETE' }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['wecom-channel', id] }),
    });
    // Shared password-field visibility map: key = field id, value = shown/hidden
    const [showPwds, setShowPwds] = useState<Record<string, boolean>>({});
    const togglePwd = (fieldId: string) => setShowPwds(p => ({ ...p, [fieldId]: !p[fieldId] }));
    const { data: discordConfig } = useQuery({
        queryKey: ['discord-channel', id],
        queryFn: () => fetchAuth<any>(`/agents/${id}/discord-channel`).catch(() => null),
        enabled: !!id && activeTab === 'settings',
    });
    const { data: discordWebhookData } = useQuery({
        queryKey: ['discord-webhook-url', id],
        queryFn: () => fetchAuth<any>(`/agents/${id}/discord-channel/webhook-url`),
        enabled: !!id && activeTab === 'settings',
    });
    const saveDiscord = useMutation({
        mutationFn: () => fetchAuth(`/agents/${id}/discord-channel`, { method: 'POST', body: JSON.stringify(discordForm) }),
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['discord-channel', id] }); setDiscordForm({ application_id: '', bot_token: '', public_key: '' }); },
    });
    const deleteDiscord = useMutation({
        mutationFn: () => fetchAuth(`/agents/${id}/discord-channel`, { method: 'DELETE' }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['discord-channel', id] }),
    });

    // ─── Channel config — Atlassian ──────────────────────
    const [atlassianForm, setAtlassianForm] = useState({ api_key: '', cloud_id: '' });
    const [atlassianEditing, setAtlassianEditing] = useState(false);
    const [atlassianTestResult, setAtlassianTestResult] = useState<{ ok: boolean; message?: string; tool_count?: number; error?: string } | null>(null);
    const [atlassianTesting, setAtlassianTesting] = useState(false);
    const { data: atlassianConfig } = useQuery({
        queryKey: ['atlassian-channel', id],
        queryFn: () => fetchAuth<any>(`/agents/${id}/atlassian-channel`).catch(() => null),
        enabled: !!id && activeTab === 'settings',
    });
    const saveAtlassian = useMutation({
        mutationFn: () => fetchAuth(`/agents/${id}/atlassian-channel`, { method: 'POST', body: JSON.stringify(atlassianForm) }),
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['atlassian-channel', id] }); setAtlassianForm({ api_key: '', cloud_id: '' }); setAtlassianEditing(false); setAtlassianTestResult(null); },
    });
    const deleteAtlassian = useMutation({
        mutationFn: () => fetchAuth(`/agents/${id}/atlassian-channel`, { method: 'DELETE' }),
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['atlassian-channel', id] }); setAtlassianTestResult(null); },
    });
    const testAtlassian = async () => {
        setAtlassianTesting(true);
        setAtlassianTestResult(null);
        try {
            const res = await fetchAuth<any>(`/agents/${id}/atlassian-channel/test`, { method: 'POST' });
            setAtlassianTestResult(res);
        } catch (e: any) {
            setAtlassianTestResult({ ok: false, error: String(e) });
        }
        setAtlassianTesting(false);
    };

    const CopyBtn = ({ url }: { url: string }) => (
        <button title="Copy" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', marginLeft: '6px', padding: '1px 4px', cursor: 'pointer', borderRadius: '3px', border: '1px solid var(--border-color)', background: 'var(--bg-primary)', color: 'var(--text-secondary)', verticalAlign: 'middle', lineHeight: 1 }}
            onClick={() => navigator.clipboard.writeText(url).then(() => { })}>
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <rect x="4" y="4" width="9" height="11" rx="1.5" /><path d="M3 11H2a1 1 0 01-1-1V2a1 1 0 011-1h8a1 1 0 011 1v1" />
            </svg>
        </button>
    );

    // ─── File viewer ─────────────────────────────────────
    const [viewingFile, setViewingFile] = useState<string | null>(null);
    const [fileEditing, setFileEditing] = useState(false);
    const [fileDraft, setFileDraft] = useState('');
    const [promptModal, setPromptModal] = useState<{ title: string; placeholder: string; action: string } | null>(null);
    const [deleteConfirm, setDeleteConfirm] = useState<{ path: string; name: string; isDir: boolean } | null>(null);
    const [uploadToast, setUploadToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [editingRole, setEditingRole] = useState(false);
    const [roleInput, setRoleInput] = useState('');
    const [editingName, setEditingName] = useState(false);
    const [nameInput, setNameInput] = useState('');
    const showToast = (message: string, type: 'success' | 'error' = 'success') => {
        setUploadToast({ message, type });
        setTimeout(() => setUploadToast(null), 3000);
    };
    const { data: fileContent } = useQuery({
        queryKey: ['file-content', id, viewingFile],
        queryFn: () => fileApi.read(id!, viewingFile!),
        enabled: !!viewingFile,
    });

    // ─── Task creation & detail ───────────────────────────────────
    const [showTaskForm, setShowTaskForm] = useState(false);
    const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
    const [taskForm, setTaskForm] = useState({ title: '', description: '', priority: 'medium', type: 'todo' as 'todo' | 'supervision', supervision_target_name: '', remind_schedule: '', due_date: '' });
    const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
    const { data: taskLogs = [] } = useQuery({
        queryKey: ['task-logs', id, selectedTaskId],
        queryFn: () => taskApi.getLogs(id!, selectedTaskId!),
        enabled: !!id && !!selectedTaskId,
        refetchInterval: selectedTaskId ? 3000 : false,
    });

    // Schedule execution history (selectedTaskId format: 'sched-{uuid}')
    const expandedScheduleId = selectedTaskId?.startsWith('sched-') ? selectedTaskId.slice(6) : null;
    const { data: scheduleHistoryData } = useQuery({
        queryKey: ['schedule-history', id, expandedScheduleId],
        queryFn: () => scheduleApi.history(id!, expandedScheduleId!),
        enabled: !!id && !!expandedScheduleId,
    });
    const createTask = useMutation({
        mutationFn: (data: any) => {
            const cleaned = { ...data };
            if (!cleaned.due_date) delete cleaned.due_date;
            return taskApi.create(id!, cleaned);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['tasks', id] });
            setShowTaskForm(false);
            setTaskForm({ title: '', description: '', priority: 'medium', type: 'todo', supervision_target_name: '', remind_schedule: '', due_date: '' });
        },
    });

    if (isLoading || !agent) {
        return <div style={{ padding: '40px', color: 'var(--text-tertiary)' }}>{t('common.loading')}</div>;
    }

    const statusKey = agent.status === 'running' ? 'running' : agent.status === 'stopped' ? 'stopped' : agent.status === 'creating' ? 'creating' : 'idle';
    const canManage = (agent as any).access_level === 'manage' || isAdmin;

    return (
        <>
            <div>
                {/* Header */}
                <div className="page-header">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                        <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'var(--accent-subtle)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '24px' }}>{agent.name?.charAt(0).toUpperCase() || 'A'}</div>
                        <div style={{ flex: 1, minWidth: 0, overflow: 'hidden' }}>
                            {canManage && editingName ? (
                                <input
                                    className="page-title"
                                    autoFocus
                                    value={nameInput}
                                    onChange={e => setNameInput(e.target.value)}
                                    onBlur={async () => {
                                        setEditingName(false);
                                        if (nameInput.trim() && nameInput !== agent.name) {
                                            await agentApi.update(id!, { name: nameInput.trim() } as any);
                                            queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                        } else {
                                            setNameInput(agent.name);
                                        }
                                    }}
                                    onKeyDown={async e => {
                                        if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                                        if (e.key === 'Escape') { setEditingName(false); setNameInput(agent.name); }
                                    }}
                                    style={{
                                        background: 'var(--bg-elevated)', border: '1px solid var(--accent-primary)',
                                        borderRadius: '6px', color: 'var(--text-primary)',
                                        padding: '4px 10px', minWidth: '320px', width: 'auto', outline: 'none',
                                        marginBottom: '0', display: 'block',
                                    }}
                                />
                            ) : (
                                <h1 className="page-title"
                                    title={canManage ? "Click to edit name" : undefined}
                                    onClick={() => { if (canManage) { setNameInput(agent.name); setEditingName(true); } }}
                                    style={{ cursor: canManage ? 'text' : 'default', borderBottom: canManage ? '1px dashed transparent' : 'none', display: 'inline-block', marginBottom: '0' }}
                                    onMouseEnter={e => { if (canManage) e.currentTarget.style.borderBottomColor = 'var(--text-tertiary)'; }}
                                    onMouseLeave={e => { if (canManage) e.currentTarget.style.borderBottomColor = 'transparent'; }}
                                >
                                    {agent.name}
                                </h1>
                            )}
                            <p className="page-subtitle" style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '4px' }}>
                                <span className={`status-dot ${statusKey}`} />
                                {t(`agent.status.${statusKey}`)}
                                {canManage && editingRole ? (
                                    <textarea
                                        autoFocus
                                        value={roleInput}
                                        onChange={e => setRoleInput(e.target.value)}
                                        onBlur={async () => {
                                            setEditingRole(false);
                                            if (roleInput !== agent.role_description) {
                                                await agentApi.update(id!, { role_description: roleInput } as any);
                                                queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                            }
                                        }}
                                        onKeyDown={async e => {
                                            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); (e.target as HTMLTextAreaElement).blur(); }
                                            if (e.key === 'Escape') { setEditingRole(false); setRoleInput(agent.role_description || ''); }
                                        }}
                                        rows={2}
                                        style={{
                                            background: 'var(--bg-elevated)', border: '1px solid var(--accent-primary)',
                                            borderRadius: '6px', color: 'var(--text-primary)', fontSize: '13px',
                                            padding: '6px 10px', width: 'min(500px, 50vw)', outline: 'none',
                                            resize: 'vertical', lineHeight: '1.5', fontFamily: 'inherit',
                                        }}
                                    />
                                ) : (
                                    <span
                                        title={canManage ? (agent.role_description || 'Click to edit') : (agent.role_description || '')}
                                        onClick={() => { if (canManage) { setRoleInput(agent.role_description || ''); setEditingRole(true); } }}
                                        style={{ cursor: canManage ? 'text' : 'default', borderBottom: canManage ? '1px dashed transparent' : 'none', maxWidth: '38vw', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block', verticalAlign: 'middle' }}
                                        onMouseEnter={e => { if (canManage) e.currentTarget.style.borderBottomColor = 'var(--text-tertiary)'; }}
                                        onMouseLeave={e => { if (canManage) e.currentTarget.style.borderBottomColor = 'transparent'; }}
                                    >
                                        {agent.role_description ? `· ${agent.role_description}` : (canManage ? <span style={{ color: 'var(--text-tertiary)', fontSize: '12px' }}>· {t('agent.fields.role', 'Click to add a description...')}</span> : null)}
                                    </span>
                                )}
                                {(agent as any).is_expired && (
                                    <span style={{ background: 'var(--error)', color: '#fff', padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600 }}>Expired</span>
                                )}
                                {(agent as any).agent_type === 'openclaw' && (
                                    <span style={{
                                        fontSize: '10px', padding: '2px 6px', borderRadius: '4px',
                                        background: 'linear-gradient(135deg, #6366f1, #8b5cf6)', color: '#fff', fontWeight: 600,
                                        letterSpacing: '0.5px',
                                    }}>OpenClaw · Lab</span>
                                )}
                                {!(agent as any).is_expired && (agent as any).expires_at && (
                                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                        Expires: {new Date((agent as any).expires_at).toLocaleString()}
                                    </span>
                                )}
                                {isAdmin && (
                                    <button
                                        onClick={openExpiryModal}
                                        title="Edit expiry time"
                                        style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '11px', color: 'var(--text-tertiary)', padding: '1px 4px', borderRadius: '4px', lineHeight: 1 }}
                                        onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-secondary)')}
                                        onMouseLeave={e => (e.currentTarget.style.background = 'none')}
                                    >✏️ {t((agent as any).expires_at || (agent as any).is_expired ? 'agent.settings.expiry.renew' : 'agent.settings.expiry.setExpiry')}</button>
                                )}
                            </p>
                        </div>
                    </div>
                    <div style={{ display: 'flex', gap: '8px' }}>
                        <button className="btn btn-primary" onClick={() => setActiveTab('chat')}>{t('agent.actions.chat')}</button>
                        {(agent as any)?.agent_type !== 'openclaw' && (
                            <>
                                {agent.status === 'stopped' ? (
                                    <button className="btn btn-secondary" onClick={async () => { await agentApi.start(id!); queryClient.invalidateQueries({ queryKey: ['agent', id] }); }}>{t('agent.actions.start')}</button>
                                ) : agent.status === 'running' ? (
                                    <button className="btn btn-secondary" onClick={async () => { await agentApi.stop(id!); queryClient.invalidateQueries({ queryKey: ['agent', id] }); }}>{t('agent.actions.stop')}</button>
                                ) : null}
                            </>
                        )}
                    </div>
                </div>

                {/* Tabs */}
                <div className="tabs">
                    {TABS.filter(tab => {
                        // 'use' access: hide settings and approvals tabs
                        if ((agent as any)?.access_level === 'use') {
                            if (tab === 'settings' || tab === 'approvals') return false;
                        }
                        // OpenClaw agents: only show status, chat, activityLog, settings
                        if ((agent as any)?.agent_type === 'openclaw') {
                            return ['status', 'relationships', 'chat', 'activityLog', 'settings'].includes(tab);
                        }
                        return true;
                    }).map((tab) => (
                        <div key={tab} className={`tab ${activeTab === tab ? 'active' : ''}`} onClick={() => setActiveTab(tab)}>
                            {t(`agent.tabs.${tab}`)}
                        </div>
                    ))}
                </div>

                {/* ── Enhanced Status Tab ── */}
                {activeTab === 'status' && (() => {
                    // Format date helper
                    const formatDate = (d: string) => {
                        try { return new Date(d).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); } catch { return d; }
                    };
                    // Get model label
                    const primaryModel = llmModels.find((m: any) => m.id === agent.primary_model_id);
                    const modelLabel = primaryModel ? (primaryModel.label || primaryModel.model) : '—';
                    const modelProvider = primaryModel ? primaryModel.provider : '—';

                    return (
                        <div>
                            {/* Metric cards */}
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px', marginBottom: '24px' }}>
                                <div className="card">
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.tabs.status')}</div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        <span className={`status-dot ${statusKey}`} />
                                        <span style={{ fontSize: '16px', fontWeight: 500 }}>{t(`agent.status.${statusKey}`)}</span>
                                    </div>
                                </div>
                                <div className="card">
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.settings.today')} Token</div>
                                    <div style={{ fontSize: '22px', fontWeight: 600 }}>{formatTokens(agent.tokens_used_today)}</div>
                                    {agent.max_tokens_per_day && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{t('agent.settings.noLimit')} {formatTokens(agent.max_tokens_per_day)}</div>}
                                </div>
                                <div className="card">
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.settings.month')} Token</div>
                                    <div style={{ fontSize: '22px', fontWeight: 600 }}>{formatTokens(agent.tokens_used_month)}</div>
                                    {agent.max_tokens_per_month && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{t('agent.settings.noLimit')} {formatTokens(agent.max_tokens_per_month)}</div>}
                                </div>
                                {/* Native agent metrics */}
                                {(agent as any)?.agent_type !== 'openclaw' && (<>
                                <div className="card">
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>LLM Calls Today</div>
                                    <div style={{ fontSize: '22px', fontWeight: 600 }}>{((agent as any).llm_calls_today || 0).toLocaleString()}</div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>Max: {((agent as any).max_llm_calls_per_day || 100).toLocaleString()}</div>
                                </div>
                                <div className="card">
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>Total Token</div>
                                    <div style={{ fontSize: '22px', fontWeight: 600 }}>{formatTokens((agent as any).tokens_used_total || 0)}</div>
                                </div>
                                {metrics && (
                                    <>
                                        <div className="card">
                                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.tasks.done')}</div>
                                            <div style={{ fontSize: '22px', fontWeight: 600 }}>{metrics.tasks?.done || 0}/{metrics.tasks?.total || 0}</div>
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}> {metrics.tasks?.completion_rate || 0}%</div>
                                        </div>
                                        <div className="card">
                                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>Pending</div>
                                            <div style={{ fontSize: '22px', fontWeight: 600, color: metrics.approvals?.pending > 0 ? 'var(--warning)' : 'inherit' }}>{metrics.approvals?.pending || 0}</div>
                                        </div>
                                        <div className="card" style={{ position: 'relative' }}>
                                            <div className="metric-tooltip-trigger" style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px', cursor: 'help', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                                                {t('agent.status.24hActions')}
                                                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="8" cy="8" r="6.5" /><path d="M8 7v4M8 5.5v0" /></svg>
                                                <span className="metric-tooltip">{t('agent.status.24hActionsTooltip')}</span>
                                            </div>
                                            <div style={{ fontSize: '22px', fontWeight: 600 }}>{metrics.activity?.actions_last_24h || 0}</div>
                                        </div>
                                    </>
                                )}
                                </>)}
                                {/* OpenClaw-specific metrics */}
                                {(agent as any)?.agent_type === 'openclaw' && (
                                    <div className="card">
                                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>
                                            {t('agent.openclaw.lastSeen')}
                                        </div>
                                        <div style={{ fontSize: '16px', fontWeight: 500 }}>
                                            {(agent as any).openclaw_last_seen
                                                ? new Date((agent as any).openclaw_last_seen).toLocaleString()
                                                : t('agent.openclaw.notConnected')}
                                        </div>
                                    </div>
                                )}
                            </div>

                            {/* Agent Profile & Model Info */}
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '24px' }}>
                                <div className="card">
                                    <h3 style={{ fontSize: '14px', fontWeight: 600, marginBottom: '12px' }}>📋 Agent Profile</h3>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', gap: '12px' }}>
                                            <span style={{ color: 'var(--text-tertiary)', flexShrink: 0 }}>{t('agent.fields.role')}</span>
                                            <span title={agent.role_description || ''} style={{ textAlign: 'right', overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as any }}>{agent.role_description || '—'}</span>
                                        </div>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>{t('common.createdBy', 'Created')}</span>
                                            <span>{agent.created_at ? formatDate(agent.created_at) : '—'}</span>
                                        </div>
                                        {(agent as any).creator_username && (
                                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                                <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.status.createdBy', 'Created by')}</span>
                                                <span style={{ color: 'var(--text-secondary)' }}>@{(agent as any).creator_username}</span>
                                            </div>
                                        )}
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.status.lastActive', 'Last Active')}</span>
                                            <span>{agent.last_active_at ? formatDate(agent.last_active_at) : '—'}</span>
                                        </div>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>🌐 Timezone</span>
                                            <span>{(agent as any).effective_timezone || agent.timezone || 'UTC'}</span>
                                        </div>
                                    </div>
                                </div>
                                {(agent as any)?.agent_type !== 'openclaw' ? (
                                <div className="card">
                                    <h3 style={{ fontSize: '14px', fontWeight: 600, marginBottom: '12px' }}>Model Config</h3>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>Model</span>
                                            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px' }}>{modelLabel}</span>
                                        </div>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>Provider</span>
                                            <span style={{ textTransform: 'capitalize' }}>{modelProvider}</span>
                                        </div>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>Context Rounds</span>
                                            <span>{(agent as any).context_window_size || 100}</span>
                                        </div>
                                    </div>
                                </div>
                                ) : (
                                <div className="card">
                                    <h3 style={{ fontSize: '14px', fontWeight: 600, marginBottom: '12px' }}>
                                        {t('agent.openclaw.connection')}
                                    </h3>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.openclaw.type')}</span>
                                            <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                <span style={{
                                                    fontSize: '10px', padding: '2px 6px', borderRadius: '4px',
                                                    background: 'linear-gradient(135deg, #6366f1, #8b5cf6)', color: '#fff', fontWeight: 600,
                                                }}>OpenClaw</span>
                                                Lab
                                            </span>
                                        </div>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.openclaw.lastSeen')}</span>
                                            <span>{(agent as any).openclaw_last_seen
                                                ? new Date((agent as any).openclaw_last_seen).toLocaleString()
                                                : t('agent.openclaw.neverConnected')}
                                            </span>
                                        </div>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.openclaw.model')}</span>
                                            <span style={{ color: 'var(--text-secondary)' }}>{t('agent.openclaw.managedBy')}</span>
                                        </div>
                                    </div>
                                </div>
                                )}
                            </div>

                            {/* Recent Activity */}
                            {activityLogs && activityLogs.length > 0 && (
                                <div className="card">
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                        <h3 style={{ fontSize: '14px', fontWeight: 600 }}>📊 Recent Activity</h3>
                                        <button className="btn btn-ghost" style={{ fontSize: '12px' }} onClick={() => setActiveTab('activityLog')}>View All →</button>
                                    </div>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                        {activityLogs.slice(0, 5).map((log: any, i: number) => (
                                            <div key={i} style={{ display: 'flex', gap: '12px', alignItems: 'flex-start', padding: '6px 0', borderBottom: i < 4 ? '1px solid var(--border-subtle)' : 'none' }}>
                                                <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', minWidth: '60px', flexShrink: 0 }}>
                                                    {new Date(log.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                                </span>
                                                <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>{log.summary || log.action_type}</span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Quick Actions */}
                            <div style={{ display: 'flex', gap: '10px', marginTop: '20px' }}>
                                <button className="btn btn-secondary" onClick={() => setActiveTab('chat')}>{t('agent.actions.chat')}</button>
                                {(agent as any)?.agent_type !== 'openclaw' && <button className="btn btn-secondary" onClick={() => setActiveTab('aware')}>Aware</button>}
                                <button className="btn btn-secondary" onClick={() => setActiveTab('settings')}>{t('agent.tabs.settings')}</button>
                            </div>
                        </div>
                    );
                })()}

                {/* ── Aware Tab ── */}
                {activeTab === 'aware' && (() => {
                    // Parse focus.md into focus items with multi-line descriptions
                    const raw = focusFile?.content || '';
                    const lines = raw.split('\n');
                    const focusItems: { id: string; name: string; description: string; done: boolean; inProgress: boolean }[] = [];
                    let currentItem: any = null;
                    for (const line of lines) {
                        const match = line.match(/^\s*-\s*\[([ x/])\]\s*(.+)/i);
                        if (match) {
                            if (currentItem) focusItems.push(currentItem);
                            const marker = match[1];
                            const fullText = match[2].trim();
                            // Split on first colon: "identifier: description"
                            const colonIdx = fullText.indexOf(':');
                            const itemName = colonIdx > 0 ? fullText.substring(0, colonIdx).trim() : fullText;
                            const itemDesc = colonIdx > 0 ? fullText.substring(colonIdx + 1).trim() : '';
                            currentItem = {
                                id: itemName,
                                name: itemName,
                                description: itemDesc,
                                done: marker.toLowerCase() === 'x',
                                inProgress: marker === '/',
                            };
                        } else if (currentItem && line.trim() && /^\s{2,}/.test(line)) {
                            // Indented continuation line = description
                            currentItem.description = currentItem.description
                                ? currentItem.description + ' ' + line.trim()
                                : line.trim();
                        }
                    }
                    if (currentItem) focusItems.push(currentItem);

                    // Helper: convert trigger config to natural language
                    const triggerToHuman = (trig: any): string => {
                        if (trig.type === 'cron' && trig.config?.expr) {
                            const expr = trig.config.expr;
                            const parts = expr.split(' ');
                            if (parts.length >= 5) {
                                const [min, hour, , , dow] = parts;
                                const timeStr = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`;
                                if (dow === '*' && min !== '*' && hour !== '*') return `Every day at ${timeStr}`;
                                if (dow === '1-5' && min !== '*' && hour !== '*') return `Weekdays at ${timeStr}`;
                                if (dow === '0' || dow === '7') return `Sundays at ${timeStr}`;
                                if (hour === '*' && min === '0') {
                                    if (dow === '1-5') return 'Every hour on weekdays';
                                    return 'Every hour';
                                }
                                if (hour === '*' && min !== '*') return `Every hour at :${min.padStart(2, '0')}`;
                            }
                            return `Cron: ${expr}`;
                        }
                        if (trig.type === 'once' && trig.config?.at) {
                            try {
                                return `Once at ${new Date(trig.config.at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}`;
                            } catch { return `Once at ${trig.config.at}`; }
                        }
                        if (trig.type === 'interval' && trig.config?.minutes) {
                            const m = trig.config.minutes;
                            return m >= 60 ? `Every ${m / 60}h` : `Every ${m} min`;
                        }
                        if (trig.type === 'poll') return `Poll: ${trig.config?.url?.substring(0, 40) || 'URL'}`;
                        if (trig.type === 'on_message') {
                            return `On message from ${trig.config?.from_agent_name || trig.config?.from_user_name || 'unknown'}`;
                        }
                        if (trig.type === 'webhook') {
                            return `Webhook${trig.config?.token ? ` (${trig.config.token.substring(0, 6)}...)` : ''}`;
                        }
                        return trig.type;
                    };

                    // Group triggers by focus_ref
                    const triggersByFocus: Record<string, any[]> = {};
                    const standaloneTriggers: any[] = [];
                    for (const trig of awareTriggers) {
                        if (trig.focus_ref && focusItems.some(f => f.name === trig.focus_ref)) {
                            if (!triggersByFocus[trig.focus_ref]) triggersByFocus[trig.focus_ref] = [];
                            triggersByFocus[trig.focus_ref].push(trig);
                        } else {
                            standaloneTriggers.push(trig);
                        }
                    }

                    // Group activity logs by trigger name -> focus_ref
                    const triggerLogsByFocus: Record<string, any[]> = {};
                    const triggerNameToFocus: Record<string, string> = {};
                    for (const trig of awareTriggers) {
                        if (trig.focus_ref) triggerNameToFocus[trig.name] = trig.focus_ref;
                    }
                    const triggerRelatedLogs = activityLogs.filter((log: any) =>
                        log.action_type === 'trigger_fired' || log.action_type === 'trigger_created' ||
                        log.action_type === 'trigger_updated' || log.action_type === 'trigger_cancelled' ||
                        log.summary?.includes('trigger')
                    );
                    for (const log of triggerRelatedLogs) {
                        // Try to match log to a focus item via trigger name in the summary
                        let matched = false;
                        for (const [trigName, focusName] of Object.entries(triggerNameToFocus)) {
                            if (log.summary?.includes(trigName) || log.detail?.tool === trigName) {
                                if (!triggerLogsByFocus[focusName]) triggerLogsByFocus[focusName] = [];
                                triggerLogsByFocus[focusName].push(log);
                                matched = true;
                                break;
                            }
                        }
                        if (!matched) {
                            if (!triggerLogsByFocus['__unmatched__']) triggerLogsByFocus['__unmatched__'] = [];
                            triggerLogsByFocus['__unmatched__'].push(log);
                        }
                    }

                    const hasFocusItems = focusItems.length > 0;
                    const hasStandalone = standaloneTriggers.length > 0;

                    // Split focus items: active first, completed separately
                    const activeFocusItems = focusItems.filter(f => !f.done);
                    const completedFocusItems = focusItems.filter(f => f.done);
                    const visibleActiveFocus = showAllFocus ? activeFocusItems : activeFocusItems.slice(0, SECTION_PAGE_SIZE);
                    const hiddenActiveCount = activeFocusItems.length - visibleActiveFocus.length;

                    // Render a focus item row
                    const renderFocusItem = (item: typeof focusItems[0]) => {
                        const isExpanded = expandedFocus === item.id;
                        const itemTriggers = triggersByFocus[item.name] || [];
                        const itemLogs = triggerLogsByFocus[item.name] || [];
                        const displayTitle = item.description || item.name;
                        const displaySubtitle = item.description ? item.name : null;

                        return (
                            <div key={item.id} style={{
                                borderRadius: '8px',
                                border: '1px solid var(--border-subtle)',
                                overflow: 'hidden',
                                marginBottom: '6px',
                                background: 'var(--bg-primary)',
                            }}>
                                {/* Focus Item Header */}
                                <div
                                    onClick={() => setExpandedFocus(isExpanded ? null : item.id)}
                                    style={{
                                        padding: '12px 16px',
                                        display: 'flex',
                                        alignItems: 'flex-start',
                                        gap: '12px',
                                        cursor: 'pointer',
                                        transition: 'background 0.15s',
                                    }}
                                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-secondary)')}
                                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                                >
                                    {/* Status indicator */}
                                    <div style={{
                                        width: '8px', height: '8px', borderRadius: '50%', marginTop: '5px', flexShrink: 0,
                                        background: item.done ? 'var(--success, #10b981)' : item.inProgress ? 'var(--accent-primary)' : 'var(--border-subtle)',
                                    }} />
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{
                                            fontSize: '13px', fontWeight: 500, lineHeight: '20px',
                                            textDecoration: item.done ? 'line-through' : 'none',
                                            color: item.done ? 'var(--text-tertiary)' : 'var(--text-primary)',
                                        }}>{displayTitle}</div>
                                        {displaySubtitle && (
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'monospace', marginTop: '2px' }}>
                                                {displaySubtitle}
                                            </div>
                                        )}
                                    </div>
                                    {/* Trigger count badge */}
                                    {itemTriggers.length > 0 && (
                                        <span style={{
                                            fontSize: '11px', color: 'var(--text-tertiary)',
                                            padding: '2px 8px', borderRadius: '10px',
                                            background: 'var(--bg-secondary)',
                                            whiteSpace: 'nowrap',
                                        }}>
                                            {itemTriggers.length} trigger{itemTriggers.length > 1 ? 's' : ''}
                                        </span>
                                    )}
                                    {/* Expand arrow */}
                                    <span style={{
                                        fontSize: '11px', color: 'var(--text-tertiary)',
                                        transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
                                        transition: 'transform 0.15s',
                                        marginTop: '4px',
                                    }}>&#9654;</span>
                                </div>

                                {/* Expanded content */}
                                {isExpanded && (
                                    <div style={{ padding: '0 16px 12px 36px', borderTop: '1px solid var(--border-subtle)' }}>
                                        {/* Nested Triggers */}
                                        {itemTriggers.length > 0 && (
                                            <div style={{ marginTop: '12px' }}>
                                                {itemTriggers.map((trig: any) => (
                                                    <div key={trig.id} style={{
                                                        display: 'flex', alignItems: 'center', gap: '10px',
                                                        padding: '8px 12px', marginBottom: '4px',
                                                        borderRadius: '6px', background: 'var(--bg-secondary)',
                                                        opacity: trig.is_enabled ? 1 : 0.5,
                                                    }}>
                                                        <div style={{ flex: 1 }}>
                                                            <div style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-primary)' }}>
                                                                {triggerToHuman(trig)}
                                                            </div>
                                                            {trig.reason && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{trig.reason}</div>}
                                                            <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '2px', fontFamily: 'monospace' }}>
                                                                {trig.type === 'cron' ? trig.config?.expr : ''}{' '}
                                                            </div>
                                                        </div>
                                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                                                            {t('agent.aware.fired', { count: trig.fire_count })}
                                                        </span>
                                                        {!trig.is_enabled && (
                                                            <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>{t('agent.aware.disabled')}</span>
                                                        )}
                                                        <div style={{ display: 'flex', gap: '4px' }}>
                                                            <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px' }}
                                                                onClick={async (e) => {
                                                                    e.stopPropagation();
                                                                    await triggerApi.update(id!, trig.id, { is_enabled: !trig.is_enabled });
                                                                    refetchTriggers();
                                                                }}>
                                                                {trig.is_enabled ? t('agent.aware.disable') : t('agent.aware.enable')}
                                                            </button>
                                                            <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px', color: 'var(--error)' }}
                                                                onClick={async (e) => {
                                                                    e.stopPropagation();
                                                                    if (confirm(t('agent.aware.deleteTriggerConfirm', { name: trig.name }))) {
                                                                        await triggerApi.delete(id!, trig.id);
                                                                        refetchTriggers();
                                                                    }
                                                                }}>
                                                                {t('common.delete', 'Delete')}
                                                            </button>
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        )}

                                        {/* Activity Logs for this focus */}
                                        {itemLogs.length > 0 && (
                                            <div style={{ marginTop: '12px' }}>
                                                <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: '6px' }}>
                                                    {t('agent.aware.reflections')}
                                                </div>
                                                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                                    {itemLogs.slice(0, 10).map((log: any) => (
                                                        <div key={log.id} style={{
                                                            padding: '6px 12px', borderRadius: '6px',
                                                            background: 'var(--bg-secondary)',
                                                            borderLeft: '2px solid var(--border-subtle)',
                                                        }}>
                                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '2px' }}>
                                                                <span style={{
                                                                    fontSize: '10px', padding: '1px 5px', borderRadius: '3px',
                                                                    background: log.action_type === 'trigger_fired' ? 'rgba(var(--accent-primary-rgb, 99,102,241), 0.1)' : 'var(--bg-tertiary, #e5e7eb)',
                                                                    color: log.action_type === 'trigger_fired' ? 'var(--accent-primary)' : 'var(--text-tertiary)',
                                                                    fontWeight: 500,
                                                                }}>{log.action_type?.replace('trigger_', '')}</span>
                                                                <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>
                                                                    {new Date(log.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                                                                </span>
                                                            </div>
                                                            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>{log.summary}</div>
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        )}

                                        {itemTriggers.length === 0 && itemLogs.length === 0 && (
                                            <div style={{ padding: '12px 0', fontSize: '12px', color: 'var(--text-tertiary)' }}>
                                                {t('agent.aware.noTriggers')}
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    };

                    return (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                            {/* ── Focus Section ── */}
                            <div className="card" style={{ marginBottom: '16px', padding: '16px' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                    <div>
                                        <h4 style={{ margin: 0, fontSize: '14px', fontWeight: 600 }}>{t('agent.aware.focus')}</h4>
                                        <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('agent.aware.focusDesc')}</span>
                                    </div>
                                    {hasFocusItems && (
                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                            {activeFocusItems.length} active{completedFocusItems.length > 0 ? ` · ${completedFocusItems.length} done` : ''}
                                        </span>
                                    )}
                                </div>

                                {/* Active Focus Items */}
                                {visibleActiveFocus.map(renderFocusItem)}

                                {/* Show more active items */}
                                {hiddenActiveCount > 0 && (
                                    <button
                                        onClick={() => setShowAllFocus(true)}
                                        className="btn btn-ghost"
                                        style={{ width: '100%', fontSize: '12px', color: 'var(--text-tertiary)', padding: '8px', marginTop: '4px' }}
                                    >
                                        {t('agent.aware.showMore', { count: hiddenActiveCount })}
                                    </button>
                                )}
                                {showAllFocus && activeFocusItems.length > SECTION_PAGE_SIZE && (
                                    <button
                                        onClick={(e) => { setShowAllFocus(false); e.currentTarget.closest('.card')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}
                                        className="btn btn-ghost"
                                        style={{ width: '100%', fontSize: '12px', color: 'var(--text-tertiary)', padding: '8px', marginTop: '4px' }}
                                    >
                                        {t('agent.aware.showLess')}
                                    </button>
                                )}

                                {/* Completed Focus Items — auto-collapsed */}
                                {completedFocusItems.length > 0 && (
                                    <>
                                        <button
                                            onClick={() => setShowCompletedFocus(!showCompletedFocus)}
                                            className="btn btn-ghost"
                                            style={{
                                                width: '100%', fontSize: '12px', color: 'var(--text-tertiary)',
                                                padding: '8px', marginTop: '8px',
                                                borderTop: '1px solid var(--border-subtle)',
                                                borderRadius: 0,
                                            }}
                                        >
                                            {showCompletedFocus
                                                ? t('agent.aware.hideCompleted')
                                                : t('agent.aware.showCompleted', { count: completedFocusItems.length })
                                            }
                                        </button>
                                        {showCompletedFocus && completedFocusItems.map(renderFocusItem)}
                                    </>
                                )}

                                {/* Empty state */}
                                {!hasFocusItems && (
                                    <div style={{
                                        padding: '24px', textAlign: 'center', color: 'var(--text-tertiary)',
                                        border: '1px dashed var(--border-subtle)', borderRadius: '8px',
                                    }}>
                                        {t('agent.aware.focusEmpty')}
                                    </div>
                                )}
                            </div>
                            {/* ── Standalone Triggers Card ── */}
                            {hasStandalone && (
                                <div className="card" style={{ marginBottom: '16px', padding: '16px' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                        <div>
                                            <h4 style={{ margin: 0, fontSize: '14px', fontWeight: 600 }}>{t('agent.aware.standaloneTriggers')}</h4>
                                        </div>
                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                            {standaloneTriggers.length} trigger{standaloneTriggers.length > 1 ? 's' : ''}
                                        </span>
                                    </div>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                        {[...standaloneTriggers].sort((a: any, b: any) => (b.is_enabled ? 1 : 0) - (a.is_enabled ? 1 : 0)).slice(0, showAllTriggers ? undefined : SECTION_PAGE_SIZE).map((trig: any) => (
                                            <div key={trig.id} style={{
                                                padding: '10px 14px', borderRadius: '8px',
                                                border: '1px solid var(--border-subtle)',
                                                display: 'flex', alignItems: 'center', gap: '10px',
                                                opacity: trig.is_enabled ? 1 : 0.5,
                                                background: 'var(--bg-primary)',
                                            }}>
                                                <div style={{ flex: 1 }}>
                                                    <div style={{ fontSize: '13px', fontWeight: 500 }}>{triggerToHuman(trig)}</div>
                                                    {trig.reason && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{trig.reason}</div>}
                                                    <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', fontFamily: 'monospace', marginTop: '2px' }}>
                                                        {trig.name}{trig.type === 'cron' ? ` · ${trig.config?.expr}` : ''}
                                                    </div>
                                                </div>
                                                <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                                                    {t('agent.aware.fired', { count: trig.fire_count })}
                                                </span>
                                                {!trig.is_enabled && (
                                                    <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>{t('agent.aware.disabled')}</span>
                                                )}
                                                <div style={{ display: 'flex', gap: '4px' }}>
                                                    <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px' }}
                                                        onClick={async () => {
                                                            await triggerApi.update(id!, trig.id, { is_enabled: !trig.is_enabled });
                                                            refetchTriggers();
                                                        }}>
                                                        {trig.is_enabled ? t('agent.aware.disable') : t('agent.aware.enable')}
                                                    </button>
                                                    <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px', color: 'var(--error)' }}
                                                        onClick={async () => {
                                                            if (confirm(t('agent.aware.deleteTriggerConfirm', { name: trig.name }))) {
                                                                await triggerApi.delete(id!, trig.id);
                                                                refetchTriggers();
                                                            }
                                                        }}>
                                                        {t('common.delete', 'Delete')}
                                                    </button>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                    {standaloneTriggers.length > SECTION_PAGE_SIZE && (
                                        <button
                                            onClick={(e) => { const collapse = showAllTriggers; setShowAllTriggers(!showAllTriggers); if (collapse) e.currentTarget.closest('.card')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}
                                            className="btn btn-ghost"
                                            style={{ width: '100%', fontSize: '12px', color: 'var(--text-tertiary)', padding: '8px', marginTop: '4px' }}
                                        >
                                            {showAllTriggers
                                                ? t('agent.aware.showLess')
                                                : t('agent.aware.showMore', { count: standaloneTriggers.length - SECTION_PAGE_SIZE })
                                            }
                                        </button>
                                    )}
                                </div>
                            )}

                            {/* Raw markdown toggle */}
                            {raw && (
                                <details style={{ marginTop: '4px', marginBottom: '16px' }}>
                                    <summary style={{ fontSize: '11px', color: 'var(--text-tertiary)', cursor: 'pointer' }}>{t('agent.aware.viewRawMarkdown')}</summary>
                                    <pre style={{ fontSize: '11px', marginTop: '8px', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px', whiteSpace: 'pre-wrap', maxHeight: '300px', overflow: 'auto' }}>{raw}</pre>
                                </details>
                            )}

                            {/* ── Reflections Card ── */}
                            {pulseSessions.length > 0 && (() => {
                                const visibleSessions = showAllReflections ? pulseSessions : pulseSessions.slice(0, SECTION_PAGE_SIZE);
                                const hiddenCount = pulseSessions.length - visibleSessions.length;
                                return (
                                    <div className="card" style={{ padding: '16px' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                            <div>
                                                <h4 style={{ margin: 0, fontSize: '14px', fontWeight: 600 }}>{t('agent.aware.reflections')}</h4>
                                                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('agent.aware.reflectionsDesc')}</span>
                                            </div>
                                            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                {pulseSessions.length} session{pulseSessions.length > 1 ? 's' : ''}
                                            </span>
                                        </div>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                            {visibleSessions.map((session: any) => {
                                                const isExpanded = expandedReflection === session.id;
                                                const msgs = reflectionMessages[session.id] || [];
                                                return (
                                                    <div key={session.id} style={{
                                                        borderRadius: '8px',
                                                        border: '1px solid var(--border-subtle)',
                                                        overflow: 'hidden',
                                                        background: 'var(--bg-primary)',
                                                    }}>
                                                        <div
                                                            onClick={async () => {
                                                                if (isExpanded) {
                                                                    setExpandedReflection(null);
                                                                    return;
                                                                }
                                                                setExpandedReflection(session.id);
                                                                // Load messages if not cached
                                                                if (!reflectionMessages[session.id]) {
                                                                    try {
                                                                        const tkn = localStorage.getItem('token');
                                                                        const res = await fetch(`/api/agents/${id}/sessions/${session.id}/messages`, {
                                                                            headers: { Authorization: `Bearer ${tkn}` },
                                                                        });
                                                                        if (res.ok) {
                                                                            const data = await res.json();
                                                                            setReflectionMessages(prev => ({ ...prev, [session.id]: data }));
                                                                        }
                                                                    } catch { /* ignore */ }
                                                                }
                                                            }}
                                                            style={{
                                                                padding: '10px 16px',
                                                                display: 'flex', alignItems: 'center', gap: '10px',
                                                                cursor: 'pointer', transition: 'background 0.15s',
                                                            }}
                                                            onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-secondary)')}
                                                            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                                                        >
                                                            <div style={{
                                                                width: '6px', height: '6px', borderRadius: '50%',
                                                                background: 'var(--accent-primary)', flexShrink: 0,
                                                            }} />
                                                            <div style={{ flex: 1, minWidth: 0 }}>
                                                                <div style={{ fontSize: '12px', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                                    {(session.title || 'Trigger execution').replace(/^🤖\s*/, '')}
                                                                </div>
                                                                <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '1px' }}>
                                                                    {new Date(session.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                                                                    {session.message_count > 0 && ` · ${session.message_count} msg`}
                                                                </div>
                                                            </div>
                                                            <span style={{
                                                                fontSize: '11px', color: 'var(--text-tertiary)',
                                                                transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
                                                                transition: 'transform 0.15s',
                                                            }}>&#9654;</span>
                                                        </div>
                                                        {isExpanded && (
                                                            <div style={{ padding: '0 16px 12px', borderTop: '1px solid var(--border-subtle)' }}>
                                                                {msgs.length === 0 ? (
                                                                    <div style={{ padding: '12px 0', fontSize: '12px', color: 'var(--text-tertiary)' }}>Loading...</div>
                                                                ) : (
                                                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '8px' }}>
                                                                        {msgs.map((msg: any, mi: number) => {
                                                                            if (msg.role === 'tool_call') {
                                                                                const tName = msg.toolName || (() => { try { return JSON.parse(msg.content || '{}').name; } catch { return ''; } })() || 'tool';
                                                                                const tArgs = msg.toolArgs || (() => { try { return JSON.parse(msg.content || '{}').args; } catch { return {}; } })();
                                                                                const tResult = msg.toolResult || '';
                                                                                const argsStr = typeof tArgs === 'string' ? tArgs : JSON.stringify(tArgs || {}, null, 2);
                                                                                const resultStr = typeof tResult === 'string' ? tResult : JSON.stringify(tResult, null, 2);
                                                                                const hasDetail = argsStr.length > 60 || resultStr;
                                                                                const Tag = hasDetail ? 'details' : 'div';
                                                                                const HeaderTag = hasDetail ? 'summary' : 'div';
                                                                                return (
                                                                                    <Tag key={mi} style={{ borderRadius: '6px', background: 'var(--bg-secondary)', overflow: 'hidden' }}>
                                                                                        <HeaderTag style={{
                                                                                            padding: '5px 10px',
                                                                                            fontSize: '11px', cursor: hasDetail ? 'pointer' : 'default',
                                                                                            display: 'flex', alignItems: 'center', gap: '8px',
                                                                                            listStyle: 'none',
                                                                                            WebkitAppearance: 'none',
                                                                                        } as any}>
                                                                                            {hasDetail && <span style={{ fontSize: '8px', color: 'var(--text-tertiary)', flexShrink: 0 }}>&#9654;</span>}
                                                                                            <span style={{
                                                                                                fontWeight: 600, fontSize: '10px', color: 'var(--text-primary)',
                                                                                                padding: '1px 6px', borderRadius: '3px',
                                                                                                background: 'var(--bg-tertiary, rgba(0,0,0,0.06))',
                                                                                                flexShrink: 0, fontFamily: 'monospace',
                                                                                            }}>{tName}</span>
                                                                                            <span style={{
                                                                                                color: 'var(--text-tertiary)', fontFamily: 'monospace', fontSize: '10px',
                                                                                                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                                                                            }}>
                                                                                                {argsStr.replace(/\n/g, ' ').substring(0, 60)}{argsStr.length > 60 ? '...' : ''}
                                                                                            </span>
                                                                                        </HeaderTag>
                                                                                        {hasDetail && (
                                                                                            <div style={{
                                                                                                padding: '8px 10px', borderTop: '1px solid var(--border-subtle)',
                                                                                                fontFamily: 'monospace', fontSize: '10px', lineHeight: 1.5,
                                                                                                whiteSpace: 'pre-wrap', maxHeight: '200px', overflow: 'auto',
                                                                                                color: 'var(--text-secondary)',
                                                                                            }}>
                                                                                                {argsStr}
                                                                                                {resultStr && (
                                                                                                    <>
                                                                                                        <div style={{ borderTop: '1px dashed var(--border-subtle)', margin: '6px 0', opacity: 0.5 }} />
                                                                                                        <span style={{ color: 'var(--text-tertiary)' }}>→ </span>{resultStr.substring(0, 500)}
                                                                                                    </>
                                                                                                )}
                                                                                            </div>
                                                                                        )}
                                                                                    </Tag>
                                                                                );
                                                                            }
                                                                            if (msg.role === 'tool_result') {
                                                                                const tName = msg.toolName || (() => { try { return JSON.parse(msg.content || '{}').name; } catch { return ''; } })() || 'result';
                                                                                const tResult = msg.toolResult || msg.content || '';
                                                                                const resultStr = typeof tResult === 'string' ? tResult : JSON.stringify(tResult, null, 2);
                                                                                if (!resultStr) return null;
                                                                                return (
                                                                                    <details key={mi} style={{ borderRadius: '6px', background: 'var(--bg-secondary)', overflow: 'hidden' }}>
                                                                                        <summary style={{
                                                                                            padding: '5px 10px',
                                                                                            fontSize: '11px', cursor: 'pointer',
                                                                                            display: 'flex', alignItems: 'center', gap: '8px',
                                                                                            listStyle: 'none',
                                                                                            WebkitAppearance: 'none',
                                                                                        } as any}>
                                                                                            <span style={{ fontSize: '8px', color: 'var(--text-tertiary)', flexShrink: 0 }}>&#9654;</span>
                                                                                            <span style={{
                                                                                                fontWeight: 600, fontSize: '10px', color: 'var(--text-primary)',
                                                                                                padding: '1px 6px', borderRadius: '3px',
                                                                                                background: 'var(--bg-tertiary, rgba(0,0,0,0.06))',
                                                                                                flexShrink: 0, fontFamily: 'monospace',
                                                                                            }}>{tName}</span>
                                                                                            <span style={{
                                                                                                color: 'var(--text-tertiary)', fontFamily: 'monospace', fontSize: '10px',
                                                                                                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                                                                            }}>
                                                                                                → {resultStr.replace(/\n/g, ' ').substring(0, 80)}
                                                                                            </span>
                                                                                        </summary>
                                                                                        <div style={{
                                                                                            padding: '8px 10px', borderTop: '1px solid var(--border-subtle)',
                                                                                            fontFamily: 'monospace', fontSize: '10px', lineHeight: 1.5,
                                                                                            whiteSpace: 'pre-wrap', maxHeight: '200px', overflow: 'auto',
                                                                                            color: 'var(--text-secondary)',
                                                                                        }}>
                                                                                            {resultStr.substring(0, 1000)}
                                                                                        </div>
                                                                                    </details>
                                                                                );
                                                                            }
                                                                            if (msg.role === 'assistant') {
                                                                                return (
                                                                                    <div key={mi} style={{
                                                                                        padding: '8px 10px', borderRadius: '6px',
                                                                                        background: 'var(--bg-secondary)',
                                                                                        fontSize: '12px', color: 'var(--text-primary)',
                                                                                        whiteSpace: 'pre-wrap', lineHeight: '1.5',
                                                                                        maxHeight: '200px', overflow: 'auto',
                                                                                    }}>
                                                                                        {msg.content}
                                                                                    </div>
                                                                                );
                                                                            }
                                                                            if (msg.role === 'user') {
                                                                                return (
                                                                                    <div key={mi} style={{
                                                                                        padding: '6px 10px', borderRadius: '6px',
                                                                                        background: 'var(--bg-secondary)',
                                                                                        borderLeft: '2px solid var(--border-subtle)',
                                                                                        fontSize: '11px', color: 'var(--text-secondary)',
                                                                                        whiteSpace: 'pre-wrap', maxHeight: '100px', overflow: 'auto',
                                                                                    }}>
                                                                                        {(msg.content || '').substring(0, 300)}
                                                                                    </div>
                                                                                );
                                                                            }
                                                                            return null;
                                                                        })}
                                                                    </div>
                                                                )}
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                        {pulseSessions.length > SECTION_PAGE_SIZE && (
                                            <button
                                                onClick={(e) => { const collapse = showAllReflections; setShowAllReflections(!showAllReflections); if (collapse) e.currentTarget.closest('.card')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}
                                                className="btn btn-ghost"
                                                style={{ width: '100%', fontSize: '12px', color: 'var(--text-tertiary)', padding: '8px', marginTop: '4px' }}
                                            >
                                                {showAllReflections
                                                    ? t('agent.aware.showLess')
                                                    : t('agent.aware.showMore', { count: hiddenCount })
                                                }
                                            </button>
                                        )}
                                    </div>
                                );
                            })()}
                        </div>
                    );
                })()}


                {/* ── Mind Tab (Soul + Memory + Heartbeat) ── */}
                {
                    activeTab === 'mind' && (() => {
                        const adapter: FileBrowserApi = {
                            list: (p) => fileApi.list(id!, p),
                            read: (p) => fileApi.read(id!, p),
                            write: (p, c) => fileApi.write(id!, p, c),
                            delete: (p) => fileApi.delete(id!, p),
                            downloadUrl: (p) => fileApi.downloadUrl(id!, p),
                        };
                        return (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
                                {/* Soul Section */}
                                <div>
                                    <h3 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        🧬 {t('agent.soul.title')}
                                    </h3>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                        {t('agent.mind.soulDesc', 'Core identity, personality, and behavior boundaries.')}
                                    </p>
                                    <FileBrowser api={adapter} singleFile="soul.md" title="" features={{ edit: (agent as any)?.access_level !== 'use' }} />
                                </div>

                                {/* Memory Section */}
                                <div>
                                    <h3 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        🧠 {t('agent.memory.title')}
                                    </h3>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                        {t('agent.mind.memoryDesc', 'Persistent memory accumulated through conversations and experiences.')}
                                    </p>
                                    <FileBrowser api={adapter} rootPath="memory" readOnly features={{}} />
                                </div>

                                {/* Heartbeat Section */}
                                <div>
                                    <h3 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        💓 {t('agent.mind.heartbeatTitle', 'Heartbeat')}
                                    </h3>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                        {t('agent.mind.heartbeatDesc', 'Instructions for periodic awareness checks. The agent reads this file during each heartbeat.')}
                                    </p>
                                    <FileBrowser api={adapter} singleFile="HEARTBEAT.md" title="" features={{ edit: (agent as any)?.access_level !== 'use' }} />
                                </div>
                            </div>
                        );
                    })()
                }

                {/* ── Tools Tab ── */}
                {
                    activeTab === 'tools' && (
                        <div>
                            <div style={{ marginBottom: '16px' }}>
                                <h3 style={{ marginBottom: '4px' }}>{t('agent.toolMgmt.title')}</h3>
                                <p style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>{t('agent.toolMgmt.description')}</p>
                            </div>
                            <ToolsManager agentId={id!} canManage={canManage} />
                        </div>
                    )
                }

                {/* ── Skills Tab ── */}
                {
                    activeTab === 'skills' && (() => {
                        const adapter: FileBrowserApi = {
                            list: (p) => fileApi.list(id!, p),
                            read: (p) => fileApi.read(id!, p),
                            write: (p, c) => fileApi.write(id!, p, c),
                            delete: (p) => fileApi.delete(id!, p),
                            upload: (file, path, onProgress) => fileApi.upload(id!, file, path, onProgress),
                            downloadUrl: (p) => fileApi.downloadUrl(id!, p),
                        };
                        return (
                            <div>
                                <div style={{ marginBottom: '16px' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                        <div>
                                            <h3 style={{ marginBottom: '4px' }}>{t('agent.skills.title')}</h3>
                                            <p style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>{t('agent.skills.description')}</p>
                                        </div>
                                        <button
                                            className="btn btn-primary"
                                            style={{ display: 'flex', alignItems: 'center', gap: '6px', whiteSpace: 'nowrap' }}
                                            onClick={() => setShowImportSkillModal(true)}
                                        >
                                            📦 {t('agent.skills.importPreset', 'Import from Presets')}
                                        </button>
                                    </div>
                                    <div style={{ marginTop: '8px', padding: '10px 14px', background: 'var(--bg-secondary)', borderRadius: '8px', fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                                        <strong>📁 Skill Format:</strong><br />
                                        • <code>skills/my-skill/SKILL.md</code> — {t('agent.skills.folderFormat', 'Each skill is a folder with a SKILL.md file and optional auxiliary files (scripts/, examples/)')}
                                    </div>
                                </div>
                                <FileBrowser api={adapter} rootPath="skills" features={{ newFile: true, edit: true, delete: true, newFolder: true, upload: true, directoryNavigation: true }} title={t('agent.skills.skillFiles')} />

                                {/* Import from Presets Modal */}
                                {showImportSkillModal && (
                                    <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowImportSkillModal(false)}>
                                        <div onClick={e => e.stopPropagation()} style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', maxWidth: '600px', width: '90%', maxHeight: '70vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                                <h3>📦 {t('agent.skills.importPreset', 'Import from Presets')}</h3>
                                                <button onClick={() => setShowImportSkillModal(false)} style={{ background: 'none', border: 'none', fontSize: '18px', cursor: 'pointer', color: 'var(--text-secondary)', padding: '4px 8px' }}>✕</button>
                                            </div>
                                            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: '0 0 16px' }}>
                                                {t('agent.skills.importDesc', 'Select a preset skill to import into this agent. All skill files will be copied to the agent\'s skills folder.')}
                                            </p>
                                            <div style={{ flex: 1, overflowY: 'auto' }}>
                                                {!globalSkillsForImport ? (
                                                    <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-tertiary)' }}>Loading...</div>
                                                ) : globalSkillsForImport.length === 0 ? (
                                                    <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-tertiary)' }}>No preset skills available</div>
                                                ) : (
                                                    globalSkillsForImport.map((skill: any) => (
                                                        <div
                                                            key={skill.id}
                                                            style={{
                                                                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                                                padding: '12px 14px', borderRadius: '8px', marginBottom: '8px',
                                                                border: '1px solid var(--border-subtle)', background: 'var(--bg-secondary)',
                                                                transition: 'border-color 0.15s',
                                                            }}
                                                            onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--accent-primary)')}
                                                            onMouseLeave={e => (e.currentTarget.style.borderColor = 'var(--border-subtle)')}
                                                        >
                                                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flex: 1 }}>
                                                                <span style={{ fontSize: '20px' }}>{skill.icon || '📋'}</span>
                                                                <div>
                                                                    <div style={{ fontWeight: 600, fontSize: '14px' }}>{skill.name}</div>
                                                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                                                        {skill.description?.substring(0, 100)}{skill.description?.length > 100 ? '...' : ''}
                                                                    </div>
                                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                                                        📁 {skill.folder_name}
                                                                        {skill.is_default && <span style={{ marginLeft: '8px', color: 'var(--accent-primary)', fontWeight: 600 }}>✓ Default</span>}
                                                                    </div>
                                                                </div>
                                                            </div>
                                                            <button
                                                                className="btn btn-secondary"
                                                                style={{ whiteSpace: 'nowrap', fontSize: '12px', padding: '6px 14px' }}
                                                                disabled={importingSkillId === skill.id}
                                                                onClick={async () => {
                                                                    setImportingSkillId(skill.id);
                                                                    try {
                                                                        const res = await fileApi.importSkill(id!, skill.id);
                                                                        alert(`✅ Imported "${skill.name}" (${res.files_written} files)`);
                                                                        queryClient.invalidateQueries({ queryKey: ['files', id, 'skills'] });
                                                                        setShowImportSkillModal(false);
                                                                    } catch (err: any) {
                                                                        alert(`❌ Import failed: ${err?.message || err}`);
                                                                    } finally {
                                                                        setImportingSkillId(null);
                                                                    }
                                                                }}
                                                            >
                                                                {importingSkillId === skill.id ? '⏳ ...' : '⬇️ Import'}
                                                            </button>
                                                        </div>
                                                    ))
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>
                        );
                    })()
                }

                {/* ── Relationships Tab ── */}
                {
                    activeTab === 'relationships' && (
                        <RelationshipEditor agentId={id!} readOnly={(agent as any)?.access_level === 'use'} />
                    )
                }

                {/* ── Workspace Tab ── */}
                {
                    activeTab === 'workspace' && (() => {
                        const adapter: FileBrowserApi = {
                            list: (p) => fileApi.list(id!, p),
                            read: (p) => fileApi.read(id!, p),
                            write: (p, c) => fileApi.write(id!, p, c),
                            delete: (p) => fileApi.delete(id!, p),
                            upload: (file, path, onProgress) => fileApi.upload(id!, file, path + '/', onProgress),
                            downloadUrl: (p) => fileApi.downloadUrl(id!, p),
                        };
                        return <FileBrowser api={adapter} rootPath="workspace" features={{ upload: true, newFile: true, newFolder: true, edit: true, delete: true, directoryNavigation: true }} />;
                    })()
                }

                {
                    activeTab === 'chat' && (
                        <div style={{ display: 'flex', gap: '0', flex: 1, minHeight: 0, height: 'calc(100vh - 206px)' }}>
                            {/* ── Left: session sidebar ── */}
                            <div style={{ width: '220px', flexShrink: 0, borderRight: '1px solid var(--border-subtle)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                                {/* Tab row */}
                                <div style={{ display: 'flex', alignItems: 'center', padding: '10px 12px 0', gap: '4px', borderBottom: '1px solid var(--border-subtle)' }}>
                                    <button onClick={() => setChatScope('mine')}
                                        style={{ flex: 1, padding: '5px 0', background: 'none', border: 'none', cursor: 'pointer', fontSize: '12px', fontWeight: chatScope === 'mine' ? 600 : 400, color: chatScope === 'mine' ? 'var(--text-primary)' : 'var(--text-tertiary)', borderBottom: chatScope === 'mine' ? '2px solid var(--accent-primary)' : '2px solid transparent', paddingBottom: '8px' }}>
                                        My Sessions
                                    </button>
                                    {isAdmin && (
                                        <button onClick={() => { setChatScope('all'); fetchAllSessions(); }}
                                            style={{ flex: 1, padding: '5px 0', background: 'none', border: 'none', cursor: 'pointer', fontSize: '12px', fontWeight: chatScope === 'all' ? 600 : 400, color: chatScope === 'all' ? 'var(--text-primary)' : 'var(--text-tertiary)', borderBottom: chatScope === 'all' ? '2px solid var(--accent-primary)' : '2px solid transparent', paddingBottom: '8px' }}>
                                            All Users
                                        </button>
                                    )}
                                </div>

                                {/* Actions row */}
                                {chatScope === 'mine' && (
                                    <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--border-subtle)' }}>
                                        <button onClick={createNewSession}
                                            style={{ width: '100%', padding: '5px 8px', background: 'none', border: '1px solid var(--border-subtle)', borderRadius: '6px', cursor: 'pointer', fontSize: '12px', color: 'var(--text-secondary)', textAlign: 'left', display: 'flex', alignItems: 'center', gap: '6px' }}
                                            onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-secondary)'; e.currentTarget.style.color = 'var(--text-primary)'; }}
                                            onMouseLeave={e => { e.currentTarget.style.background = 'none'; e.currentTarget.style.color = 'var(--text-secondary)'; }}>
                                            + New Session
                                        </button>
                                    </div>
                                )}

                                {/* Session list */}
                                <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0' }}>
                                    {chatScope === 'mine' ? (
                                        sessionsLoading ? (
                                            <div style={{ padding: '20px 12px', fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('common.loading')}</div>
                                        ) : sessions.length === 0 ? (
                                            <div style={{ padding: '20px 12px', fontSize: '12px', color: 'var(--text-tertiary)' }}>No sessions yet.<br />Click "+ New Session" to start.</div>
                                        ) : sessions.map((s: any) => {
                                            const isActive = activeSession?.id === s.id;
                                            const isOwn = s.user_id === String(currentUser?.id);
                                            const channelLabel: Record<string, string> = {
                                                feishu: t('common.channels.feishu'),
                                                discord: t('common.channels.discord'),
                                                slack: t('common.channels.slack'),
                                                dingtalk: t('common.channels.dingtalk'),
                                                wecom: t('common.channels.wecom'),
                                            };
                                            const chLabel = channelLabel[s.source_channel];
                                            return (
                                                <div key={s.id} onClick={() => selectSession(s)}
                                                    style={{ padding: '8px 12px', cursor: 'pointer', borderLeft: isActive ? '2px solid var(--accent-primary)' : '2px solid transparent', background: isActive ? 'var(--bg-secondary)' : 'transparent', marginBottom: '1px' }}
                                                    onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--bg-secondary)'; }}
                                                    onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px', marginBottom: '2px' }}>
                                                        <div style={{ fontSize: '12px', fontWeight: isActive ? 600 : 400, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{s.title}</div>
                                                        {chLabel && <span style={{ fontSize: '9px', padding: '1px 4px', borderRadius: '3px', background: 'var(--bg-tertiary)', color: 'var(--text-tertiary)', flexShrink: 0 }}>{chLabel}</span>}
                                                    </div>
                                                    <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                        {isOwn && isActive && wsConnected && <span className="status-dot running" style={{ width: '5px', height: '5px', flexShrink: 0 }} />}
                                                        {s.last_message_at
                                                            ? new Date(s.last_message_at).toLocaleString(i18n.language === 'zh' ? 'zh-CN' : 'en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                                                            : new Date(s.created_at).toLocaleString(i18n.language === 'zh' ? 'zh-CN' : 'en-US', { month: 'short', day: 'numeric' })}
                                                        {s.message_count > 0 && <span style={{ marginLeft: 'auto' }}>{s.message_count}</span>}
                                                    </div>
                                                </div>
                                            );
                                        })
                                    ) : (
                                        /* All Users tab — user filter dropdown + flat list */
                                        <>
                                            {/* User filter dropdown */}
                                            <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border-subtle)' }}>
                                                <select
                                                    value={allUserFilter}
                                                    onChange={e => setAllUserFilter(e.target.value)}
                                                    style={{ width: '100%', padding: '4px 6px', fontSize: '11px', background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)', borderRadius: '5px', color: 'var(--text-primary)', cursor: 'pointer' }}
                                                >
                                                    <option value="">All Users</option>
                                                    {Array.from(new Set(allSessions.map((s: any) => s.username || s.user_id))).filter(Boolean).map((u: any) => (
                                                        <option key={u} value={u}>{u}</option>
                                                    ))}
                                                </select>
                                            </div>
                                            {/* Filtered session list */}
                                            {allSessions
                                                .filter((s: any) => !allUserFilter || (s.username || s.user_id) === allUserFilter)
                                                .map((s: any) => {
                                                    const isActive = activeSession?.id === s.id;
                                                    return (
                                                        <div key={s.id} onClick={() => selectSession(s)}
                                                            style={{ padding: '6px 12px', cursor: 'pointer', borderLeft: isActive ? '2px solid var(--accent-primary)' : '2px solid transparent', background: isActive ? 'var(--bg-secondary)' : 'transparent' }}
                                                            onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--bg-secondary)'; }}
                                                            onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}>
                                                            <div style={{ display: 'flex', alignItems: 'center', gap: '5px', marginBottom: '1px' }}>
                                                                <div style={{ fontSize: '12px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-primary)', flex: 1 }}>{s.title}</div>
                                                                {({
                                                                    feishu: t('common.channels.feishu'),
                                                                    discord: t('common.channels.discord'),
                                                                    slack: t('common.channels.slack'),
                                                                    dingtalk: t('common.channels.dingtalk'),
                                                                    wecom: t('common.channels.wecom'),
                                                                } as Record<string, string>)[s.source_channel] && (
                                                                        <span style={{ fontSize: '9px', padding: '1px 4px', borderRadius: '3px', background: 'var(--bg-tertiary)', color: 'var(--text-tertiary)', flexShrink: 0 }}>
                                                                            {({
                                                                                feishu: t('common.channels.feishu'),
                                                                                discord: t('common.channels.discord'),
                                                                                slack: t('common.channels.slack'),
                                                                                dingtalk: t('common.channels.dingtalk'),
                                                                                wecom: t('common.channels.wecom'),
                                                                            } as Record<string, string>)[s.source_channel]}
                                                                        </span>
                                                                    )}
                                                            </div>
                                                            <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', display: 'flex', gap: '4px' }}>
                                                                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{s.username || ''}</span>
                                                                <span style={{ flexShrink: 0 }}>{s.last_message_at ? new Date(s.last_message_at).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}{s.message_count > 0 ? ` · ${s.message_count}` : ''}</span>
                                                            </div>
                                                        </div>
                                                    );
                                                })}
                                        </>
                                    )}
                                </div>
                            </div>

                            {/* ── Right: chat/message area ── */}
                            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', position: 'relative', minWidth: 0, overflow: 'hidden' }}>
                                {!activeSession ? (
                                    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', fontSize: '13px', flexDirection: 'column', gap: '8px' }}>
                                        <div>No session selected</div>
                                        <button className="btn btn-secondary" onClick={createNewSession} style={{ fontSize: '12px' }}>Start a new session</button>
                                    </div>
                                ) : (activeSession.user_id && currentUser && activeSession.user_id !== String(currentUser.id)) || activeSession.source_channel === 'agent' || activeSession.participant_type === 'agent' ? (
                                    /* ── Read-only history view (other user's session or agent-to-agent) ── */
                                    <>
                                        <div ref={historyContainerRef} onScroll={handleHistoryScroll} style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '12px', padding: '4px 8px', background: 'var(--bg-secondary)', borderRadius: '4px', display: 'inline-block' }}>
                                                {activeSession.source_channel === 'agent' ? `🤖 Agent Conversation · ${activeSession.username || 'Agents'}` : `Read-only · ${activeSession.username || 'User'}`}
                                            </div>
                                            {historyMsgs.map((m: any, i: number) => {
                                                if (m.role === 'tool_call') {
                                                    const tName = m.toolName || (() => { try { return JSON.parse(m.content || '{}').name; } catch { return 'tool'; } })();
                                                    const tArgs = m.toolArgs || (() => { try { return JSON.parse(m.content || '{}').args; } catch { return {}; } })();
                                                    const tResult = m.toolResult ?? (() => { try { return JSON.parse(m.content || '{}').result; } catch { return ''; } })();
                                                    return (
                                                        <div key={i} style={{ display: 'flex', gap: '8px', marginBottom: '6px', paddingLeft: '36px', minWidth: 0 }}>
                                                            <details style={{ flex: 1, minWidth: 0, borderRadius: '8px', background: 'var(--accent-subtle)', border: '1px solid var(--accent-subtle)', fontSize: '12px', overflow: 'hidden' }}>
                                                                <summary style={{ padding: '6px 10px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', userSelect: 'none', listStyle: 'none', overflow: 'hidden' }}>
                                                                    <span style={{ fontSize: '13px' }}>⚡</span>
                                                                    <span style={{ fontWeight: 600, color: 'var(--accent-text)' }}>{tName}</span>
                                                                    {tArgs && typeof tArgs === 'object' && Object.keys(tArgs).length > 0 && <span style={{ color: 'var(--text-tertiary)', fontSize: '11px', fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{`(${Object.entries(tArgs).map(([k, v]) => `${k}: ${typeof v === 'string' ? v.slice(0, 30) : JSON.stringify(v)}`).join(', ')})`}</span>}
                                                                </summary>
                                                                {tResult && <div style={{ padding: '4px 10px 8px' }}><div style={{ color: 'var(--text-secondary)', fontSize: '11px', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '240px', overflow: 'auto', background: 'rgba(0,0,0,0.15)', borderRadius: '4px', padding: '4px 6px' }}>{tResult}</div></div>}
                                                            </details>
                                                        </div>
                                                    );
                                                }

                                                {/* Assistant message with no content: show inline thinking or skip */}
                                                if (m.role === 'assistant' && !m.content?.trim()) {
                                                    if (m.thinking) {
                                                        return (
                                                            <div key={i} style={{ paddingLeft: '36px', marginBottom: '6px' }}>
                                                                <details style={{
                                                                    fontSize: '12px',
                                                                    background: 'rgba(147, 130, 220, 0.08)', borderRadius: '6px',
                                                                    border: '1px solid rgba(147, 130, 220, 0.15)',
                                                                }}>
                                                                    <summary style={{
                                                                        padding: '6px 10px', cursor: 'pointer',
                                                                        color: 'rgba(147, 130, 220, 0.9)', fontWeight: 500,
                                                                        userSelect: 'none', display: 'flex', alignItems: 'center', gap: '4px',
                                                                    }}>Thinking</summary>
                                                                    <div style={{
                                                                        padding: '4px 10px 8px',
                                                                        fontSize: '12px', lineHeight: '1.6',
                                                                        color: 'var(--text-secondary)',
                                                                        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                                                        maxHeight: '300px', overflow: 'auto',
                                                                    }}>{m.thinking}</div>
                                                                </details>
                                                            </div>
                                                        );
                                                    }
                                                    return null;
                                                }
                                                return (
                                                    <div key={i} style={{ display: 'flex', flexDirection: m.role === 'assistant' ? 'row' : 'row-reverse', gap: '8px', marginBottom: '8px' }}>
                                                        <div style={{ width: '28px', height: '28px', borderRadius: '50%', background: m.role === 'assistant' ? 'var(--bg-elevated)' : 'rgba(16,185,129,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', flexShrink: 0, color: 'var(--text-secondary)', fontWeight: 600 }}>{m.sender_name ? m.sender_name[0] : (m.role === 'assistant' ? 'A' : 'U')}</div>
                                                        <div style={{ maxWidth: '70%', padding: '8px 12px', borderRadius: '12px', background: m.role === 'assistant' ? 'var(--bg-secondary)' : 'rgba(16,185,129,0.1)', fontSize: '13px', lineHeight: '1.5', wordBreak: 'break-word' }}>
                                                            {m.sender_name && <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '2px', fontWeight: 600 }}>🤖 {m.sender_name}</div>}
                                                            {(() => {
                                                                const pm = parseChatMsg({ role: m.role as ChatMsg['role'], content: m.content || '' });
                                                                const fe = pm.fileName?.split('.').pop()?.toLowerCase() ?? '';
                                                                const fi = fe === 'pdf' ? '📄' : (fe === 'csv' || fe === 'xlsx' || fe === 'xls') ? '📊' : (fe === 'docx' || fe === 'doc') ? '📝' : '📎';
                                                                return (
                                                                    <>
                                                                        {m.thinking && (
                                                                            <details style={{
                                                                                marginBottom: '8px', fontSize: '12px',
                                                                                background: 'rgba(147, 130, 220, 0.08)', borderRadius: '6px',
                                                                                border: '1px solid rgba(147, 130, 220, 0.15)',
                                                                            }}>
                                                                                <summary style={{
                                                                                    padding: '6px 10px', cursor: 'pointer',
                                                                                    color: 'rgba(147, 130, 220, 0.9)', fontWeight: 500,
                                                                                    userSelect: 'none', display: 'flex', alignItems: 'center', gap: '4px',
                                                                                }}>
                                                                                    💭 Thinking
                                                                                </summary>
                                                                                <div style={{
                                                                                    padding: '4px 10px 8px',
                                                                                    fontSize: '12px', lineHeight: '1.6',
                                                                                    color: 'var(--text-secondary)',
                                                                                    whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                                                                    maxHeight: '300px', overflow: 'auto',
                                                                                }}>
                                                                                    {m.thinking}
                                                                                </div>
                                                                            </details>
                                                                        )}
                                                                        {pm.fileName && (
                                                                            <div style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', background: 'var(--bg-elevated)', borderRadius: '6px', padding: '4px 8px', marginBottom: pm.content ? '4px' : '0', fontSize: '11px', border: '1px solid var(--border-subtle)', color: 'var(--text-secondary)' }}>
                                                                                <span>{fi}</span>
                                                                                <span style={{ fontWeight: 500, color: 'var(--text-primary)', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pm.fileName}</span>
                                                                            </div>
                                                                        )}
                                                                        {pm.content ? (m.role === 'assistant' ? <MarkdownRenderer content={pm.content} /> : <div style={{ whiteSpace: 'pre-wrap' }}>{pm.content}</div>) : null}
                                                                    </>
                                                                );
                                                            })()}
                                                            {m.created_at && <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '4px', opacity: 0.6 }}>{(() => { const d = new Date(m.created_at); const now = new Date(); const diffMs = now.getTime() - d.getTime(); const isToday = d.toDateString() === now.toDateString(); if (isToday) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); if (diffMs < 7 * 86400000) return d.toLocaleDateString([], { weekday: 'short' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); })()}</div>}
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                        {showHistoryScrollBtn && (
                                            <button onClick={scrollHistoryToBottom} style={{ position: 'absolute', bottom: '20px', right: '20px', width: '32px', height: '32px', borderRadius: '50%', background: 'var(--bg-elevated)', border: '1px solid var(--border-default)', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px', boxShadow: '0 2px 8px rgba(0,0,0,0.3)', zIndex: 10 }} title="Scroll to bottom">↓</button>
                                        )}
                                    </>
                                ) : (
                                    /* ── Live WebSocket chat (own session) ── */
                                    <>
                                        <div ref={chatContainerRef} onScroll={handleChatScroll} style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
                                            {chatMessages.length === 0 && (
                                                <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--text-tertiary)' }}>
                                                    <div style={{ fontSize: '13px', marginBottom: '4px' }}>{activeSession?.title || t('agent.chat.startChat')}</div>
                                                    <div style={{ fontSize: '12px' }}>{t('agent.chat.startConversation', { name: agent.name })}</div>
                                                    <div style={{ fontSize: '11px', marginTop: '4px', opacity: 0.7 }}>{t('agent.chat.fileSupport')}</div>
                                                </div>
                                            )}
                                            {chatMessages.map((msg, i) => {
                                                if (msg.role === 'tool_call') {
                                                    return (
                                                        <div key={i} style={{ display: 'flex', gap: '8px', marginBottom: '6px', paddingLeft: '36px', minWidth: 0 }}>
                                                            <details style={{ flex: 1, minWidth: 0, borderRadius: '8px', background: 'var(--accent-subtle)', border: '1px solid var(--accent-subtle)', fontSize: '12px', overflow: 'hidden' }}>
                                                                <summary style={{ padding: '6px 10px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', userSelect: 'none', listStyle: 'none', overflow: 'hidden' }}>
                                                                    <span style={{ fontSize: '13px' }}>{msg.toolStatus === 'running' ? '⏳' : '⚡'}</span>
                                                                    <span style={{ fontWeight: 600, color: 'var(--accent-text)' }}>{msg.toolName}</span>
                                                                    {msg.toolArgs && Object.keys(msg.toolArgs).length > 0 && <span style={{ color: 'var(--text-tertiary)', fontSize: '11px', fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{`(${Object.entries(msg.toolArgs).map(([k, v]) => `${k}: ${typeof v === 'string' ? v.slice(0, 30) : JSON.stringify(v)}`).join(', ')})`}</span>}
                                                                    {msg.toolStatus === 'running' && <span style={{ color: 'var(--text-tertiary)', fontSize: '11px', marginLeft: 'auto' }}>{t('common.loading')}</span>}
                                                                </summary>
                                                                {msg.toolResult && <div style={{ padding: '4px 10px 8px' }}><div style={{ color: 'var(--text-secondary)', fontSize: '11px', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '240px', overflow: 'auto', background: 'rgba(0,0,0,0.15)', borderRadius: '4px', padding: '4px 6px' }}>{msg.toolResult}</div></div>}
                                                            </details>
                                                        </div>
                                                    );
                                                }
                                                {/* Assistant message with no text content: show inline thinking or skip */}
                                                if (msg.role === 'assistant' && !msg.content?.trim()) {
                                                    if (msg.thinking) {
                                                        return (
                                                            <div key={i} style={{ paddingLeft: '36px', marginBottom: '6px' }}>
                                                                <details style={{
                                                                    fontSize: '12px',
                                                                    background: 'rgba(147, 130, 220, 0.08)', borderRadius: '6px',
                                                                    border: '1px solid rgba(147, 130, 220, 0.15)',
                                                                }}>
                                                                    <summary style={{
                                                                        padding: '6px 10px', cursor: 'pointer',
                                                                        color: 'rgba(147, 130, 220, 0.9)', fontWeight: 500,
                                                                        userSelect: 'none', display: 'flex', alignItems: 'center', gap: '4px',
                                                                    }}>Thinking</summary>
                                                                    <div style={{
                                                                        padding: '4px 10px 8px',
                                                                        fontSize: '12px', lineHeight: '1.6',
                                                                        color: 'var(--text-secondary)',
                                                                        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                                                        maxHeight: '300px', overflow: 'auto',
                                                                    }}>{msg.thinking}</div>
                                                                </details>
                                                            </div>
                                                        );
                                                    }
                                                    return null;
                                                }
                                                return (
                                                    <div key={i} style={{ display: 'flex', flexDirection: msg.role === 'assistant' ? 'row' : 'row-reverse', gap: '8px', marginBottom: '8px' }}>
                                                        <div style={{ width: '28px', height: '28px', borderRadius: '50%', background: msg.role === 'assistant' ? 'var(--bg-elevated)' : 'rgba(16,185,129,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', flexShrink: 0, color: 'var(--text-secondary)', fontWeight: 600 }}>{msg.role === 'user' ? 'U' : 'A'}</div>
                                                        <div style={{ maxWidth: '70%', padding: '8px 12px', borderRadius: '12px', background: msg.role === 'assistant' ? 'var(--bg-secondary)' : 'rgba(16,185,129,0.1)', fontSize: '13px', lineHeight: '1.5', wordBreak: 'break-word' }}>
                                                            {msg.fileName && (() => {
                                                                const fe = msg.fileName!.split('.').pop()?.toLowerCase() ?? '';
                                                                const isImage = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'].includes(fe);
                                                                if (isImage && msg.imageUrl) {
                                                                    return (<div style={{ marginBottom: '4px' }}>
                                                                        <img src={msg.imageUrl} alt={msg.fileName} style={{ maxWidth: '200px', maxHeight: '150px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }} />
                                                                    </div>);
                                                                }
                                                                const fi = fe === 'pdf' ? '📄' : (fe === 'csv' || fe === 'xlsx' || fe === 'xls') ? '📊' : (fe === 'docx' || fe === 'doc') ? '📝' : '📎';
                                                                return (<div style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', background: 'rgba(0,0,0,0.08)', borderRadius: '6px', padding: '4px 8px', marginBottom: msg.content ? '4px' : '0', fontSize: '11px', border: '1px solid var(--border-subtle)', color: 'var(--text-secondary)' }}><span>{fi}</span><span style={{ fontWeight: 500, color: 'var(--text-primary)', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{msg.fileName}</span></div>);
                                                            })()}
                                                            {msg.thinking && (
                                                                <details style={{
                                                                    marginBottom: '8px', fontSize: '12px',
                                                                    background: 'rgba(147, 130, 220, 0.08)', borderRadius: '6px',
                                                                    border: '1px solid rgba(147, 130, 220, 0.15)',
                                                                }}>
                                                                    <summary style={{
                                                                        padding: '6px 10px', cursor: 'pointer',
                                                                        color: 'rgba(147, 130, 220, 0.9)', fontWeight: 500,
                                                                        userSelect: 'none', display: 'flex', alignItems: 'center', gap: '4px',
                                                                    }}>
                                                                        💭 Thinking
                                                                    </summary>
                                                                    <div style={{
                                                                        padding: '4px 10px 8px',
                                                                        fontSize: '12px', lineHeight: '1.6',
                                                                        color: 'var(--text-secondary)',
                                                                        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                                                        maxHeight: '300px', overflow: 'auto',
                                                                    }}>
                                                                        {msg.thinking}
                                                                    </div>
                                                                </details>
                                                            )}
                                                            {msg.role === 'assistant' ? (
                                                                (msg as any)._streaming && !msg.content ? (
                                                                    <div className="thinking-indicator">
                                                                        <div className="thinking-dots">
                                                                            <span /><span /><span />
                                                                        </div>
                                                                        <span style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>{t('agent.chat.thinking', 'Thinking...')}</span>
                                                                    </div>
                                                                ) : <MarkdownRenderer content={msg.content} />
                                                            ) : msg.content ? <div style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div> : null}
                                                            {msg.timestamp && <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '4px', opacity: 0.6, textAlign: msg.role === 'user' ? 'right' : 'left' }}>{(() => { const d = new Date(msg.timestamp); const now = new Date(); const diffMs = now.getTime() - d.getTime(); const isToday = d.toDateString() === now.toDateString(); if (isToday) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); if (diffMs < 7 * 86400000) return d.toLocaleDateString([], { weekday: 'short' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); })()}</div>}
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                            {isWaiting && (
                                                <div style={{ display: 'flex', gap: '8px', marginBottom: '8px', animation: 'fadeIn .2s ease' }}>
                                                    <div style={{ width: '28px', height: '28px', borderRadius: '50%', background: 'var(--bg-elevated)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', flexShrink: 0, color: 'var(--text-secondary)', fontWeight: 600 }}>A</div>
                                                    <div style={{ padding: '8px 12px', borderRadius: '12px', background: 'var(--bg-secondary)', fontSize: '13px' }}>
                                                        <div className="thinking-indicator">
                                                            <div className="thinking-dots">
                                                                <span /><span /><span />
                                                            </div>
                                                            <span style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>{t('agent.chat.thinking', 'Thinking...')}</span>
                                                        </div>
                                                    </div>
                                                </div>
                                            )}
                                            <div ref={chatEndRef} />
                                        </div>
                                        {showScrollBtn && (
                                            <button onClick={scrollToBottom} style={{ position: 'absolute', bottom: '70px', right: '20px', width: '32px', height: '32px', borderRadius: '50%', background: 'var(--bg-elevated)', border: '1px solid var(--border-default)', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px', boxShadow: '0 2px 8px rgba(0,0,0,0.3)', zIndex: 10 }} title="Scroll to bottom">↓</button>
                                        )}
                                        {agentExpired ? (
                                            <div style={{ padding: '7px 16px', borderTop: '1px solid rgba(245,158,11,0.3)', background: 'rgba(245,158,11,0.08)', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: 'rgb(180,100,0)' }}>
                                                <span>u23f8</span>
                                                <span>This Agent has <strong>expired</strong> and is off duty. Contact your admin to extend its service.</span>
                                            </div>
                                        ) : !wsConnected && (!activeSession?.user_id || !currentUser || activeSession.user_id === String(currentUser?.id)) ? (
                                            <div style={{ padding: '3px 16px', display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                <span style={{ display: 'inline-block', width: '5px', height: '5px', borderRadius: '50%', background: 'var(--accent-primary)', opacity: 0.8, animation: 'pulse 1.2s ease-in-out infinite' }} />
                                                Connecting...
                                            </div>
                                        ) : null}
                                        {attachedFile && (
                                            <div style={{ padding: '6px 16px', background: 'var(--bg-elevated)', borderTop: '1px solid var(--border-subtle)', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '11px' }}>
                                                {attachedFile.imageUrl ? (
                                                    <img src={attachedFile.imageUrl} alt={attachedFile.name} style={{ width: '40px', height: '40px', borderRadius: '6px', objectFit: 'cover', border: '1px solid var(--border-subtle)' }} />
                                                ) : (
                                                    <span>📎</span>
                                                )}
                                                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{attachedFile.name}</span>
                                                <button onClick={() => setAttachedFile(null)} style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer', fontSize: '14px', padding: '2px 4px' }}>✕</button>
                                            </div>
                                        )}
                                        <div style={{ display: 'flex', gap: '8px', padding: '6px 12px', borderTop: '1px solid var(--border-subtle)' }}>
                                            <input type="file" ref={fileInputRef} onChange={handleChatFile} style={{ display: 'none' }} />
                                            <button className="btn btn-secondary" onClick={() => fileInputRef.current?.click()} disabled={!wsConnected || uploading || isWaiting || isStreaming} style={{ padding: '6px 10px', fontSize: '14px', minWidth: 'auto', ...( (!wsConnected || uploading || isWaiting || isStreaming) ? { cursor: 'not-allowed', opacity: 0.4 } : {}) }}>{uploading ? '⏳' : '⦹'}</button>
                                            {uploading && uploadProgress >= 0 && (
                                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flex: '0 0 140px' }}>
                                                    {uploadProgress <= 100 ? (
                                                        /* Upload phase: show progress bar */
                                                        <>
                                                            <div style={{ flex: 1, height: '4px', borderRadius: '2px', background: 'var(--bg-tertiary)', overflow: 'hidden' }}>
                                                                <div style={{ height: '100%', borderRadius: '2px', background: 'var(--accent-primary)', width: `${uploadProgress}%`, transition: 'width 0.15s ease' }} />
                                                            </div>
                                                            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{uploadProgress}%</span>
                                                        </>
                                                    ) : (
                                                        /* Processing phase (progress = 101): server is parsing the file */
                                                        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                                            <span style={{ display: 'inline-block', width: '5px', height: '5px', borderRadius: '50%', background: 'var(--accent-primary)', animation: 'pulse 1.2s ease-in-out infinite' }} />
                                                            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>Processing...</span>
                                                        </div>
                                                    )}
                                                    <button onClick={() => { uploadAbortRef.current?.(); }} style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer', fontSize: '12px', padding: '0 2px', lineHeight: 1 }} title="Cancel upload">✕</button>
                                                </div>
                                            )}
                                            <input ref={chatInputRef} className="chat-input" value={chatInput} onChange={e => setChatInput(e.target.value)}
                                                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMsg(); } }}
                                                onPaste={handlePaste}
                                                placeholder={!wsConnected && (!activeSession?.user_id || !currentUser || activeSession.user_id === String(currentUser?.id)) ? 'Connecting...' : attachedFile ? t('agent.chat.askAboutFile', { name: attachedFile.name }) : t('chat.placeholder')}
                                                disabled={!wsConnected || isWaiting || isStreaming} style={{ flex: 1 }} autoFocus />
                                            {(isStreaming || isWaiting) ? (
                                                <button className="btn btn-stop-generation" onClick={() => { if (wsRef.current?.readyState === WebSocket.OPEN) { wsRef.current.send(JSON.stringify({ type: 'abort' })); setIsStreaming(false); setIsWaiting(false); } }} style={{ padding: '6px 16px' }} title={t('chat.stop', 'Stop')}>
                                                    <span className="stop-icon" />
                                                </button>
                                            ) : (
                                                <button className="btn btn-primary" onClick={sendChatMsg} disabled={!wsConnected || (!chatInput.trim() && !attachedFile)} style={{ padding: '6px 16px' }}>{t('chat.send')}</button>
                                            )}
                                        </div>
                                    </>
                                )}
                            </div>
                        </div>
                    )
                }

                {
                    activeTab === 'activityLog' && (() => {
                        // Category definitions
                        const userActionTypes = ['chat_reply', 'tool_call', 'task_created', 'task_updated', 'file_written', 'error'];
                        const heartbeatTypes = ['heartbeat', 'plaza_post'];
                        const scheduleTypes = ['schedule_run'];
                        const messageTypes = ['feishu_msg_sent', 'agent_msg_sent', 'web_msg_sent'];

                        let filteredLogs = activityLogs;
                        if (logFilter === 'user') {
                            filteredLogs = activityLogs.filter((l: any) => userActionTypes.includes(l.action_type));
                        } else if (logFilter === 'backend') {
                            filteredLogs = activityLogs.filter((l: any) => !userActionTypes.includes(l.action_type));
                        } else if (logFilter === 'heartbeat') {
                            filteredLogs = activityLogs.filter((l: any) => heartbeatTypes.includes(l.action_type));
                        } else if (logFilter === 'schedule') {
                            filteredLogs = activityLogs.filter((l: any) => scheduleTypes.includes(l.action_type));
                        } else if (logFilter === 'messages') {
                            filteredLogs = activityLogs.filter((l: any) => messageTypes.includes(l.action_type));
                        }

                        const filterBtn = (key: string, label: string, indent = false) => (
                            <button
                                key={key}
                                onClick={() => setLogFilter(key)}
                                style={{
                                    padding: indent ? '4px 10px 4px 20px' : '6px 14px',
                                    fontSize: indent ? '11px' : '12px',
                                    fontWeight: logFilter === key ? 600 : 400,
                                    color: logFilter === key ? 'var(--accent-primary)' : 'var(--text-secondary)',
                                    background: logFilter === key ? 'rgba(99,102,241,0.1)' : 'transparent',
                                    border: logFilter === key ? '1px solid var(--accent-primary)' : '1px solid var(--border-subtle)',
                                    borderRadius: '6px',
                                    cursor: 'pointer',
                                    transition: 'all 0.15s',
                                    whiteSpace: 'nowrap' as const,
                                }}
                            >
                                {label}
                            </button>
                        );

                        return (
                            <div>
                                <h3 style={{ marginBottom: '12px' }}>{t('agent.activityLog.title')}</h3>

                                {/* Filter tabs */}
                                <div style={{ display: 'flex', gap: '6px', marginBottom: '16px', flexWrap: 'wrap', alignItems: 'center' }}>
                                    {filterBtn('user', '👤 ' + t('agent.activityLog.userActions', 'User Actions'))}
                                    {(agent as any)?.agent_type !== 'openclaw' && (<>
                                    {filterBtn('backend', '⚙️ ' + t('agent.activityLog.backendServices', 'Backend Services'))}
                                    {(logFilter === 'backend' || logFilter === 'heartbeat' || logFilter === 'schedule' || logFilter === 'messages') && (
                                        <>
                                            <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>│</span>
                                            {filterBtn('heartbeat', '💓 Heartbeat', true)}
                                            {filterBtn('schedule', '⏰ Schedule/Cron', true)}
                                            {filterBtn('messages', '📨 Messages', true)}
                                        </>
                                    )}
                                    </>)}
                                </div>

                                {filteredLogs.length > 0 ? (
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                        {filteredLogs.map((log: any) => {
                                            const icons: Record<string, string> = {
                                                chat_reply: '💬', tool_call: '⚡', feishu_msg_sent: '📤',
                                                agent_msg_sent: '🤖', web_msg_sent: '🌐', task_created: '📋',
                                                task_updated: '✅', file_written: '📝', error: '❌',
                                                schedule_run: '⏰', heartbeat: '💓', plaza_post: '🏛️',
                                            };
                                            const time = log.created_at ? new Date(log.created_at).toLocaleString('zh-CN', {
                                                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
                                            }) : '';
                                            const isExpanded = expandedLogId === log.id;
                                            return (
                                                <div key={log.id}
                                                    onClick={() => setExpandedLogId(isExpanded ? null : log.id)}
                                                    style={{
                                                        padding: '10px 14px', borderRadius: '8px', cursor: 'pointer',
                                                        background: isExpanded ? 'var(--bg-elevated)' : 'var(--bg-secondary)', fontSize: '13px',
                                                        border: isExpanded ? '1px solid var(--accent-primary)' : '1px solid transparent',
                                                        transition: 'all 0.15s ease',
                                                    }}
                                                >
                                                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
                                                        <span style={{ fontSize: '16px', flexShrink: 0, marginTop: '1px' }}>
                                                            {icons[log.action_type] || '·'}
                                                        </span>
                                                        <div style={{ flex: 1, minWidth: 0 }}>
                                                            <div style={{ fontWeight: 500, marginBottom: '2px' }}>{log.summary}</div>
                                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                                {time} · {log.action_type}
                                                                {log.detail && !isExpanded && <span style={{ marginLeft: '8px', color: 'var(--accent-primary)' }}>▸ Details</span>}
                                                            </div>
                                                        </div>
                                                    </div>
                                                    {isExpanded && log.detail && (
                                                        <div style={{ marginTop: '8px', padding: '10px', borderRadius: '6px', background: 'var(--bg-primary)', fontSize: '12px', fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all', lineHeight: '1.6', color: 'var(--text-secondary)', maxHeight: '300px', overflowY: 'auto' }}>
                                                            {Object.entries(log.detail).map(([k, v]: [string, any]) => (
                                                                <div key={k} style={{ marginBottom: '6px' }}>
                                                                    <span style={{ color: 'var(--accent-primary)', fontWeight: 600 }}>{k}:</span>{' '}
                                                                    <span>{typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v)}</span>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        })}
                                    </div>
                                ) : (
                                    <div className="card" style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>
                                        {t('agent.activityLog.noRecords')}
                                    </div>
                                )}
                            </div>
                        );
                    })()
                }

                {/* ── Feishu Channel Tab ── */}

                {/* ── Approvals Tab ── */}
                {
                    activeTab === 'approvals' && (() => {
                        const ApprovalsTab = () => {
                            const { data: approvals = [], refetch: refetchApprovals } = useQuery({
                                queryKey: ['agent-approvals', id],
                                queryFn: () => fetchAuth<any[]>(`/agents/${id}/approvals`),
                                enabled: !!id,
                                refetchInterval: 15000,
                            });
                            const resolveMut = useMutation({
                                mutationFn: async ({ approvalId, action }: { approvalId: string; action: string }) => {
                                    const token = localStorage.getItem('token');
                                    return fetch(`/api/agents/${id}/approvals/${approvalId}/resolve`, {
                                        method: 'POST',
                                        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
                                        body: JSON.stringify({ action }),
                                    });
                                },
                                onSuccess: () => {
                                    refetchApprovals();
                                    queryClient.invalidateQueries({ queryKey: ['notifications-unread'] });
                                },
                            });
                            const pending = (approvals as any[]).filter((a: any) => a.status === 'pending');
                            const resolved = (approvals as any[]).filter((a: any) => a.status !== 'pending');
                            const statusStyle = (s: string) => ({
                                padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600,
                                background: s === 'approved' ? 'rgba(0,180,120,0.12)' : s === 'rejected' ? 'rgba(255,80,80,0.12)' : 'rgba(255,180,0,0.12)',
                                color: s === 'approved' ? 'var(--success)' : s === 'rejected' ? 'var(--error)' : 'var(--warning)',
                            });
                            return (
                                <div style={{ padding: '20px 24px' }}>
                                    {/* Pending */}
                                    {pending.length > 0 && (
                                        <>
                                            <h4 style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--warning)' }}>
                                                {t('agent.settings.approvals.pendingCount', { count: pending.length })}
                                            </h4>
                                            {pending.map((a: any) => (
                                                <div key={a.id} style={{
                                                    padding: '14px 16px', marginBottom: '8px', borderRadius: '8px',
                                                    background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)',
                                                }}>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                                                        <span style={statusStyle(a.status)}>{a.status}</span>
                                                        <span style={{ fontSize: '13px', fontWeight: 500 }}>{a.action_type}</span>
                                                        <span style={{ flex: 1 }} />
                                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                            {a.created_at ? new Date(a.created_at).toLocaleString() : ''}
                                                        </span>
                                                    </div>
                                                    {a.details && (
                                                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '10px', lineHeight: '1.5', maxHeight: '80px', overflow: 'hidden' }}>
                                                            {typeof a.details === 'string' ? a.details : JSON.stringify(a.details, null, 2)}
                                                        </div>
                                                    )}
                                                    <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                                                        <button
                                                            className="btn btn-primary"
                                                            style={{ padding: '6px 16px', fontSize: '12px' }}
                                                            onClick={() => resolveMut.mutate({ approvalId: a.id, action: 'approve' })}
                                                            disabled={resolveMut.isPending}
                                                        >
                                                            {t('agent.settings.approvals.approve')}
                                                        </button>
                                                        <button
                                                            className="btn btn-danger"
                                                            style={{ padding: '6px 16px', fontSize: '12px' }}
                                                            onClick={() => resolveMut.mutate({ approvalId: a.id, action: 'reject' })}
                                                            disabled={resolveMut.isPending}
                                                        >
                                                            {t('agent.settings.approvals.reject')}
                                                        </button>
                                                    </div>
                                                </div>
                                            ))}
                                            <div style={{ borderTop: '1px solid var(--border-subtle)', margin: '16px 0' }} />
                                        </>
                                    )}
                                    {/* History */}
                                    <h4 style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                                        {t('agent.settings.approvals.history')}
                                    </h4>
                                    {resolved.length === 0 && pending.length === 0 && (
                                        <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                                            {t('agent.settings.approvals.noRecords')}
                                        </div>
                                    )}
                                    {resolved.map((a: any) => (
                                        <div key={a.id} style={{
                                            padding: '12px 16px', marginBottom: '6px', borderRadius: '8px',
                                            background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)',
                                            opacity: 0.7,
                                        }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <span style={statusStyle(a.status)}>{a.status}</span>
                                                <span style={{ fontSize: '12px' }}>{a.action_type}</span>
                                                <span style={{ flex: 1 }} />
                                                <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>
                                                    {a.resolved_at ? new Date(a.resolved_at).toLocaleString() : ''}
                                                </span>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            );
                        };
                        return <ApprovalsTab />;
                    })()}

                {/* ── Settings Tab ── */}
                {
                    activeTab === 'settings' && (() => {
                        // Check if form has unsaved changes
                        const hasChanges = (
                            settingsForm.primary_model_id !== (agent?.primary_model_id || '') ||
                            settingsForm.fallback_model_id !== (agent?.fallback_model_id || '') ||
                            settingsForm.context_window_size !== (agent?.context_window_size ?? 100) ||
                            settingsForm.max_tool_rounds !== ((agent as any)?.max_tool_rounds ?? 50) ||
                            String(settingsForm.max_tokens_per_day) !== String(agent?.max_tokens_per_day || '') ||
                            String(settingsForm.max_tokens_per_month) !== String(agent?.max_tokens_per_month || '') ||
                            settingsForm.max_triggers !== ((agent as any)?.max_triggers ?? 20) ||
                            settingsForm.min_poll_interval_min !== ((agent as any)?.min_poll_interval_min ?? 5) ||
                            settingsForm.webhook_rate_limit !== ((agent as any)?.webhook_rate_limit ?? 5)
                        );

                        const handleSaveSettings = async () => {
                            setSettingsSaving(true);
                            setSettingsError('');
                            try {
                                const result: any = await agentApi.update(id!, {
                                    primary_model_id: settingsForm.primary_model_id || null,
                                    fallback_model_id: settingsForm.fallback_model_id || null,
                                    context_window_size: settingsForm.context_window_size,
                                    max_tool_rounds: settingsForm.max_tool_rounds,
                                    max_tokens_per_day: settingsForm.max_tokens_per_day ? Number(settingsForm.max_tokens_per_day) : null,
                                    max_tokens_per_month: settingsForm.max_tokens_per_month ? Number(settingsForm.max_tokens_per_month) : null,
                                    max_triggers: settingsForm.max_triggers,
                                    min_poll_interval_min: settingsForm.min_poll_interval_min,
                                    webhook_rate_limit: settingsForm.webhook_rate_limit,
                                } as any);
                                queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                settingsInitRef.current = false;

                                // Check if any values were clamped by company policy
                                const clamped = result?._clamped_fields;
                                if (clamped && clamped.length > 0) {
                                    const fieldNames: Record<string, string> = {
                                        min_poll_interval_min: t('agent.settings.fieldNames.min_poll_interval_min'),
                                        webhook_rate_limit: t('agent.settings.fieldNames.webhook_rate_limit'),
                                        heartbeat_interval_minutes: t('agent.settings.fieldNames.heartbeat_interval_minutes')
                                    };
                                    const msgs = clamped.map((c: any) => {
                                        const name = fieldNames[c.field] || c.field;
                                        return `${name}: ${c.requested} -> ${c.applied} (${t('agent.settings.fieldNames.companyPolicy')})`;
                                    });
                                    setSettingsError(t('agent.settings.settingsAdjusted') + '\n' + msgs.join('\n'));
                                    setTimeout(() => setSettingsError(''), 5000);
                                }

                                setSettingsSaved(true);
                                setTimeout(() => setSettingsSaved(false), 2000);
                            } catch (e: any) {
                                setSettingsError(e?.message || 'Failed to save');
                            } finally {
                                setSettingsSaving(false);
                            }
                        };

                        return (
                            <div>
                                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px', position: 'sticky', top: 0, zIndex: 10, background: 'var(--bg-primary)', paddingTop: '4px', paddingBottom: '12px', borderBottom: '1px solid var(--border-subtle)' }}>
                                    <h3 style={{ margin: 0 }}>{t('agent.settings.title')}</h3>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                        {settingsSaved && <span style={{ fontSize: '12px', color: 'var(--success)' }}>{t('agent.settings.saved', 'Saved')}</span>}
                                        {settingsError && <span style={{ fontSize: '12px', color: settingsError.includes('adjusted') ? 'var(--warning)' : 'var(--error)', whiteSpace: 'pre-line' }}>{settingsError}</span>}
                                        <button
                                            className="btn btn-primary"
                                            disabled={!hasChanges || settingsSaving}
                                            onClick={handleSaveSettings}
                                            style={{
                                                opacity: hasChanges ? 1 : 0.5,
                                                cursor: hasChanges ? 'pointer' : 'default',
                                                padding: '6px 20px',
                                                fontSize: '13px',
                                            }}
                                        >
                                            {settingsSaving ? t('agent.settings.saving', 'Saving...') : t('agent.settings.save', 'Save')}
                                        </button>
                                    </div>
                                </div>

                                {/* Model Selection — native agents only */}
                                {(agent as any)?.agent_type !== 'openclaw' && (
                                <div className="card" style={{ marginBottom: '12px' }}>
                                    <h4 style={{ marginBottom: '12px' }}>{t('agent.settings.modelConfig')}</h4>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                                        <div>
                                            <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.primaryModel')}</label>
                                            <select
                                                className="input"
                                                value={settingsForm.primary_model_id}
                                                onChange={(e) => setSettingsForm(f => ({ ...f, primary_model_id: e.target.value }))}
                                            >
                                                <option value="">--</option>
                                                {llmModels.map((m: any) => (
                                                    <option key={m.id} value={m.id}>{m.label} ({m.provider}/{m.model})</option>
                                                ))}
                                            </select>
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.primaryModel')}</div>
                                        </div>
                                        <div>
                                            <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.fallbackModel')}</label>
                                            <select
                                                className="input"
                                                value={settingsForm.fallback_model_id}
                                                onChange={(e) => setSettingsForm(f => ({ ...f, fallback_model_id: e.target.value }))}
                                            >
                                                <option value="">--</option>
                                                {llmModels.map((m: any) => (
                                                    <option key={m.id} value={m.id}>{m.label} ({m.provider}/{m.model})</option>
                                                ))}
                                            </select>
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.fallbackModel')}</div>
                                        </div>
                                    </div>
                                </div>
                                )}

                                {/* Context Window — native agents only */}
                                {(agent as any)?.agent_type !== 'openclaw' && (<>
                                <div className="card" style={{ marginBottom: '12px' }}>
                                    <h4 style={{ marginBottom: '12px' }}>{t('agent.settings.conversationContext')}</h4>
                                    <div>
                                        <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.maxRounds')}</label>
                                        <input
                                            className="input"
                                            type="number"
                                            min={10}
                                            max={500}
                                            value={settingsForm.context_window_size}
                                            onChange={(e) => setSettingsForm(f => ({ ...f, context_window_size: Math.max(10, Math.min(500, parseInt(e.target.value) || 100)) }))}
                                            style={{ width: '120px' }}
                                        />
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.roundsDesc')}</div>
                                    </div>
                                </div>

                                {/* Max Tool Call Rounds */}
                                <div className="card" style={{ marginBottom: '12px' }}>
                                    <h4 style={{ marginBottom: '12px' }}>🔧 {t('agent.settings.maxToolRounds', 'Max Tool Call Rounds')}</h4>
                                    <div>
                                        <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.maxToolRoundsLabel', 'Maximum rounds per message')}</label>
                                        <input
                                            className="input"
                                            type="number"
                                            min={5}
                                            max={200}
                                            value={settingsForm.max_tool_rounds}
                                            onChange={(e) => setSettingsForm(f => ({ ...f, max_tool_rounds: Math.max(5, Math.min(200, parseInt(e.target.value) || 50)) }))}
                                            style={{ width: '120px' }}
                                        />
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.maxToolRoundsDesc', 'How many tool-calling rounds the agent can perform per message (search, write, etc). Default: 50')}</div>
                                    </div>
                                </div>
                                </>)}

                                {/* Token Limits */}
                                <div className="card" style={{ marginBottom: '12px' }}>
                                    <h4 style={{ marginBottom: '12px' }}>{t('agent.settings.tokenLimits')}</h4>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                                        <div>
                                            <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.dailyLimit')}</label>
                                            <input
                                                className="input"
                                                type="number"
                                                value={settingsForm.max_tokens_per_day}
                                                onChange={(e) => setSettingsForm(f => ({ ...f, max_tokens_per_day: e.target.value }))}
                                                placeholder={t("agent.settings.noLimit")}
                                            />
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                                {t('agent.settings.today')}: {formatTokens(agent?.tokens_used_today || 0)}
                                            </div>
                                        </div>
                                        <div>
                                            <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.monthlyLimit')}</label>
                                            <input
                                                className="input"
                                                type="number"
                                                value={settingsForm.max_tokens_per_month}
                                                onChange={(e) => setSettingsForm(f => ({ ...f, max_tokens_per_month: e.target.value }))}
                                                placeholder={t("agent.settings.noLimit")}
                                            />
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                                {t('agent.settings.month')}: {formatTokens(agent?.tokens_used_month || 0)}
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                {/* Trigger Limits — native agents only */}
                                {(agent as any)?.agent_type !== 'openclaw' && (() => {
                                    return (
                                        <div className="card" style={{ marginBottom: '12px' }}>
                                            <h4 style={{ marginBottom: '4px' }}>{t('agent.settings.triggerLimits.title')}</h4>
                                            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                                {t('agent.settings.triggerLimits.description')}
                                            </p>
                                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px' }}>
                                                <div>
                                                    <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>
                                                        {t('agent.settings.triggerLimits.maxTriggers')}
                                                    </label>
                                                    <input
                                                        className="input"
                                                        type="number"
                                                        min={1}
                                                        max={100}
                                                        value={settingsForm.max_triggers}
                                                        onChange={(e) => setSettingsForm(f => ({ ...f, max_triggers: Math.max(1, Math.min(100, parseInt(e.target.value) || 20)) }))}
                                                        style={{ width: '100%' }}
                                                    />
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                                        {t('agent.settings.triggerLimits.maxTriggersDesc')}
                                                    </div>
                                                </div>
                                                <div>
                                                    <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>
                                                        {t('agent.settings.triggerLimits.minPollInterval')}
                                                    </label>
                                                    <input
                                                        className="input"
                                                        type="number"
                                                        min={1}
                                                        max={60}
                                                        value={settingsForm.min_poll_interval_min}
                                                        onChange={(e) => setSettingsForm(f => ({ ...f, min_poll_interval_min: Math.max(1, Math.min(60, parseInt(e.target.value) || 5)) }))}
                                                        style={{ width: '100%' }}
                                                    />
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                                        {t('agent.settings.triggerLimits.minPollIntervalDesc')}
                                                    </div>
                                                </div>
                                                <div>
                                                    <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>
                                                        {t('agent.settings.triggerLimits.webhookRateLimit')}
                                                    </label>
                                                    <input
                                                        className="input"
                                                        type="number"
                                                        min={1}
                                                        max={60}
                                                        value={settingsForm.webhook_rate_limit}
                                                        onChange={(e) => setSettingsForm(f => ({ ...f, webhook_rate_limit: Math.max(1, Math.min(60, parseInt(e.target.value) || 5)) }))}
                                                        style={{ width: '100%' }}
                                                    />
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                                        {t('agent.settings.triggerLimits.webhookRateLimitDesc')}
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    );
                                })()}

                                {/* Welcome Message */}
                                {(() => {
                                    const saveWm = async () => {
                                        try {
                                            await agentApi.update(id!, { welcome_message: wmDraft } as any);
                                            queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                            setWmSaved(true);
                                            setTimeout(() => setWmSaved(false), 2000);
                                        } catch { }
                                    };
                                    return (
                                        <div className="card" style={{ marginBottom: '12px' }}>
                                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' }}>
                                                <h4 style={{ margin: 0 }}>{t('agent.settings.welcomeMessage.title')}</h4>
                                                {wmSaved && <span style={{ fontSize: '12px', color: 'var(--success)' }}>✓ {t('agent.settings.welcomeMessage.saved')}</span>}
                                            </div>
                                            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                                {t('agent.settings.welcomeMessage.description')}
                                            </p>
                                            <textarea
                                                className="input"
                                                rows={4}
                                                value={wmDraft}
                                                onChange={e => setWmDraft(e.target.value)}
                                                onBlur={saveWm}
                                                placeholder={t('agent.settings.welcomeMessage.placeholder')}
                                                style={{
                                                    width: '100%', minHeight: '80px', resize: 'vertical',
                                                    fontFamily: 'inherit', fontSize: '13px',
                                                }}
                                            />
                                        </div>
                                    );
                                })()}

                                {/* Autonomy Policy — native agents only */}
                                {(agent as any)?.agent_type !== 'openclaw' && <div className="card" style={{ marginBottom: '12px' }}>
                                    <h4 style={{ marginBottom: '4px' }}>{t('agent.settings.autonomy.title')}</h4>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                                        {t('agent.settings.autonomy.description')}
                                    </p>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                        {[
                                            { key: 'read_files', label: t('agent.settings.autonomy.readFiles'), desc: t('agent.settings.autonomy.readFilesDesc') },
                                            { key: 'write_workspace_files', label: t('agent.settings.autonomy.writeFiles'), desc: t('agent.settings.autonomy.writeFilesDesc') },
                                            { key: 'delete_files', label: t('agent.settings.autonomy.deleteFiles'), desc: t('agent.settings.autonomy.deleteFilesDesc') },
                                            { key: 'send_feishu_message', label: t('agent.settings.autonomy.sendFeishu'), desc: t('agent.settings.autonomy.sendFeishuDesc') },
                                            { key: 'web_search', label: t('agent.settings.autonomy.webSearch'), desc: t('agent.settings.autonomy.webSearchDesc') },
                                            { key: 'manage_tasks', label: t('agent.settings.autonomy.manageTasks'), desc: t('agent.settings.autonomy.manageTasksDesc') },
                                        ].map((action) => {
                                            const currentLevel = (agent?.autonomy_policy as any)?.[action.key] || 'L1';
                                            return (
                                                <div key={action.key} style={{
                                                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                                    padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px',
                                                    border: '1px solid var(--border-subtle)',
                                                }}>
                                                    <div style={{ flex: 1 }}>
                                                        <div style={{ fontWeight: 500, fontSize: '13px' }}>{action.label}</div>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{action.desc}</div>
                                                    </div>
                                                    <select
                                                        className="input"
                                                        value={currentLevel}
                                                        onChange={async (e) => {
                                                            const newPolicy = { ...(agent?.autonomy_policy as any || {}), [action.key]: e.target.value };
                                                            await agentApi.update(id!, { autonomy_policy: newPolicy } as any);
                                                            queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                                        }}
                                                        style={{
                                                            width: '140px', fontSize: '12px',
                                                            color: currentLevel === 'L1' ? 'var(--success)' : currentLevel === 'L2' ? 'var(--warning)' : 'var(--error)',
                                                            fontWeight: 600,
                                                        }}
                                                    >
                                                        <option value="L1">{t('agent.settings.autonomy.l1Auto')}</option>
                                                        <option value="L2">{t('agent.settings.autonomy.l2Notify')}</option>
                                                        <option value="L3">{t('agent.settings.autonomy.l3Approve')}</option>
                                                    </select>
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>}

                                {/* Permission Management */}
                                {(() => {
                                    const scopeLabels: Record<string, string> = {
                                        company: '🏢 ' + t('agent.settings.perm.company', 'Company-wide'),
                                        user: '👤 ' + t('agent.settings.perm.selfOnly', 'Only Me'),
                                    };

                                    const handleScopeChange = async (newScope: string) => {
                                        try {
                                            await fetchAuth(`/agents/${id}/permissions`, {
                                                method: 'PUT',
                                                headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify({ scope_type: newScope, scope_ids: [], access_level: permData?.access_level || 'use' }),
                                            });
                                            queryClient.invalidateQueries({ queryKey: ['agent-permissions', id] });
                                            queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                        } catch (e) {
                                            console.error('Failed to update permissions', e);
                                        }
                                    };

                                    const handleAccessLevelChange = async (newLevel: string) => {
                                        try {
                                            await fetchAuth(`/agents/${id}/permissions`, {
                                                method: 'PUT',
                                                headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify({ scope_type: permData?.scope_type || 'company', scope_ids: permData?.scope_ids || [], access_level: newLevel }),
                                            });
                                            queryClient.invalidateQueries({ queryKey: ['agent-permissions', id] });
                                            queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                        } catch (e) {
                                            console.error('Failed to update access level', e);
                                        }
                                    };

                                    const isOwner = permData?.is_owner ?? false;
                                    const currentScope = permData?.scope_type || 'company';
                                    const currentAccessLevel = permData?.access_level || 'use';

                                    return (
                                        <div className="card" style={{ marginBottom: '12px' }}>
                                            <h4 style={{ marginBottom: '12px' }}>🔒 {t('agent.settings.perm.title', 'Access Permissions')}</h4>
                                            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                                                {t('agent.settings.perm.description', 'Control who can see and interact with this agent. Only the creator or admin can change this.')}
                                            </p>

                                            {/* Scope Selection */}
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '16px' }}>
                                                {(['company', 'user'] as const).map((scope) => (
                                                    <label
                                                        key={scope}
                                                        style={{
                                                            display: 'flex',
                                                            alignItems: 'center',
                                                            gap: '10px',
                                                            padding: '12px 14px',
                                                            borderRadius: '8px',
                                                            cursor: isOwner ? 'pointer' : 'default',
                                                            border: currentScope === scope
                                                                ? '1px solid var(--accent-primary)'
                                                                : '1px solid var(--border-subtle)',
                                                            background: currentScope === scope
                                                                ? 'rgba(99,102,241,0.06)'
                                                                : 'transparent',
                                                            opacity: isOwner ? 1 : 0.7,
                                                            transition: 'all 0.15s',
                                                        }}
                                                    >
                                                        <input
                                                            type="radio"
                                                            name="perm_scope"
                                                            checked={currentScope === scope}
                                                            disabled={!isOwner}
                                                            onChange={() => handleScopeChange(scope)}
                                                            style={{ accentColor: 'var(--accent-primary)' }}
                                                        />
                                                        <div>
                                                            <div style={{ fontWeight: 500, fontSize: '13px' }}>{scopeLabels[scope]}</div>
                                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                                                {scope === 'company' && t('agent.settings.perm.companyDesc', 'All users in the organization can use this agent')}
                                                                {scope === 'user' && t('agent.settings.perm.selfDesc', 'Only the creator can use this agent')}
                                                            </div>
                                                        </div>
                                                    </label>
                                                ))}
                                            </div>

                                            {/* Access Level for company scope */}
                                            {currentScope === 'company' && isOwner && (
                                                <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '12px' }}>
                                                    <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '8px' }}>
                                                        {t('agent.settings.perm.accessLevel', 'Default Access Level')}
                                                    </label>
                                                    <div style={{ display: 'flex', gap: '8px' }}>
                                                        {[{ val: 'use', label: '👁️ ' + t('agent.settings.perm.useLevel', 'Use'), desc: t('agent.settings.perm.useDesc', 'Task, Chat, Tools, Skills, Workspace') },
                                                        { val: 'manage', label: '⚙️ ' + t('agent.settings.perm.manageLevel', 'Manage'), desc: t('agent.settings.perm.manageDesc', 'Full access including Settings, Mind, Relationships') }].map(opt => (
                                                            <label key={opt.val}
                                                                style={{
                                                                    flex: 1,
                                                                    padding: '10px 12px',
                                                                    borderRadius: '8px',
                                                                    cursor: 'pointer',
                                                                    border: currentAccessLevel === opt.val
                                                                        ? '1px solid var(--accent-primary)'
                                                                        : '1px solid var(--border-subtle)',
                                                                    background: currentAccessLevel === opt.val
                                                                        ? 'rgba(99,102,241,0.06)'
                                                                        : 'transparent',
                                                                    transition: 'all 0.15s',
                                                                }}
                                                            >
                                                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                                    <input type="radio" name="access_level" checked={currentAccessLevel === opt.val}
                                                                        onChange={() => handleAccessLevelChange(opt.val)}
                                                                        style={{ accentColor: 'var(--accent-primary)' }} />
                                                                    <span style={{ fontWeight: 500, fontSize: '13px' }}>{opt.label}</span>
                                                                </div>
                                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px', marginLeft: '20px' }}>{opt.desc}</div>
                                                            </label>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}

                                            {currentScope !== 'company' && permData?.scope_names?.length > 0 && (
                                                <div style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                    <span style={{ fontWeight: 500 }}>{t('agent.settings.perm.currentAccess', 'Current access')}:</span>{' '}
                                                    {permData.scope_names.map((s: any) => s.name).join(', ')}
                                                </div>
                                            )}

                                            {!isOwner && (
                                                <div style={{ marginTop: '12px', fontSize: '11px', color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
                                                    {t('agent.settings.perm.readOnly', 'Only the creator or admin can change permissions')}
                                                </div>
                                            )}
                                        </div>
                                    );
                                })()}

                                {/* Timezone */}
                                <div className="card" style={{ marginBottom: '12px' }}>
                                    <h4 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        {t('agent.settings.timezone.title', '🌐 Timezone')}
                                    </h4>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                                        {t('agent.settings.timezone.description', 'The timezone used for this agent\'s scheduling, active hours, and time awareness. Defaults to the company timezone if not set.')}
                                    </p>
                                    <div style={{
                                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                        padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px',
                                        border: '1px solid var(--border-subtle)',
                                    }}>
                                        <div>
                                            <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('agent.settings.timezone.current', 'Agent Timezone')}</div>
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                {agent?.timezone
                                                    ? t('agent.settings.timezone.override', 'Custom timezone for this agent')
                                                    : t('agent.settings.timezone.inherited', 'Using company default timezone')}
                                            </div>
                                        </div>
                                        <select
                                            className="input"
                                            disabled={!canManage}
                                            value={agent?.timezone || ''}
                                            onChange={async (e) => {
                                                if (!canManage) return;
                                                const val = e.target.value || null;
                                                await agentApi.update(id!, { timezone: val } as any);
                                                queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                            }}
                                            style={{ width: '200px', fontSize: '12px', opacity: canManage ? 1 : 0.6 }}
                                        >
                                            <option value="">{t('agent.settings.timezone.default', '↩ Company default')}</option>
                                            {['UTC', 'Asia/Shanghai', 'Asia/Tokyo', 'Asia/Seoul', 'Asia/Singapore', 'Asia/Kolkata', 'Asia/Dubai',
                                                'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Moscow',
                                                'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
                                                'America/Sao_Paulo', 'Australia/Sydney', 'Pacific/Auckland'].map(tz => (
                                                    <option key={tz} value={tz}>{tz}</option>
                                                ))}
                                        </select>
                                    </div>
                                </div>

                                {/* Heartbeat */}
                                <div className="card" style={{ marginBottom: '12px' }}>
                                    <h4 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        {t('agent.settings.heartbeat.title', 'Heartbeat')}
                                    </h4>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                                        {t('agent.settings.heartbeat.description', 'Periodic awareness check — agent proactively monitors the plaza and work environment.')}
                                    </p>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                                        {/* Enable toggle */}
                                        <div style={{
                                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                            padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px',
                                            border: '1px solid var(--border-subtle)',
                                        }}>
                                            <div>
                                                <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('agent.settings.heartbeat.enabled', 'Enable Heartbeat')}</div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.settings.heartbeat.enabledDesc', 'Agent will periodically check plaza and work status')}</div>
                                            </div>
                                            <label style={{ position: 'relative', display: 'inline-block', width: '44px', height: '24px', cursor: canManage ? 'pointer' : 'default' }}>
                                                <input
                                                    type="checkbox"
                                                    checked={agent?.heartbeat_enabled ?? true}
                                                    disabled={!canManage}
                                                    onChange={async (e) => {
                                                        if (!canManage) return;
                                                        await agentApi.update(id!, { heartbeat_enabled: e.target.checked } as any);
                                                        queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                                    }}
                                                    style={{ opacity: 0, width: 0, height: 0 }}
                                                />
                                                <span style={{
                                                    position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
                                                    background: (agent?.heartbeat_enabled ?? true) ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                                                    borderRadius: '12px', transition: 'background 0.2s',
                                                    opacity: canManage ? 1 : 0.6
                                                }}>
                                                    <span style={{
                                                        position: 'absolute', top: '3px',
                                                        left: (agent?.heartbeat_enabled ?? true) ? '23px' : '3px',
                                                        width: '18px', height: '18px', background: 'white',
                                                        borderRadius: '50%', transition: 'left 0.2s',
                                                    }} />
                                                </span>
                                            </label>
                                        </div>

                                        {/* Interval */}
                                        <div style={{
                                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                            padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px',
                                            border: '1px solid var(--border-subtle)',
                                        }}>
                                            <div>
                                                <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('agent.settings.heartbeat.interval', 'Check Interval')}</div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.settings.heartbeat.intervalDesc', 'How often the agent checks for updates')}</div>
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                <input
                                                    type="number"
                                                    className="input"
                                                    disabled={!canManage}
                                                    min={1}
                                                    defaultValue={agent?.heartbeat_interval_minutes ?? 120}
                                                    key={agent?.heartbeat_interval_minutes}
                                                    onBlur={async (e) => {
                                                        if (!canManage) return;
                                                        const val = Math.max(1, Number(e.target.value) || 120);
                                                        e.target.value = String(val);
                                                        await agentApi.update(id!, { heartbeat_interval_minutes: val } as any);
                                                        queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                                    }}
                                                    style={{ width: '80px', fontSize: '12px', opacity: canManage ? 1 : 0.6 }}
                                                />
                                                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('common.minutes', 'min')}</span>
                                            </div>
                                        </div>

                                        {/* Active Hours */}
                                        <div style={{
                                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                            padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px',
                                            border: '1px solid var(--border-subtle)',
                                        }}>
                                            <div>
                                                <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('agent.settings.heartbeat.activeHours', 'Active Hours')}</div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.settings.heartbeat.activeHoursDesc', 'Only trigger heartbeat during these hours (HH:MM-HH:MM)')}</div>
                                            </div>
                                            <input
                                                className="input"
                                                disabled={!canManage}
                                                value={agent?.heartbeat_active_hours ?? '09:00-18:00'}
                                                onChange={async (e) => {
                                                    if (!canManage) return;
                                                    await agentApi.update(id!, { heartbeat_active_hours: e.target.value } as any);
                                                    queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                                }}
                                                style={{ width: '140px', fontSize: '12px', textAlign: 'center', opacity: canManage ? 1 : 0.6 }}
                                                placeholder="09:00-18:00"
                                            />
                                        </div>



                                        {/* Last Heartbeat */}
                                        {agent?.last_heartbeat_at && (
                                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', paddingLeft: '4px' }}>
                                                {t('agent.settings.heartbeat.lastRun', 'Last heartbeat')}: {new Date(agent.last_heartbeat_at).toLocaleString()}
                                            </div>
                                        )}
                                    </div>
                                </div>

                                {/* Channel Config — multi-channel */}
                                <div className="card" style={{ marginBottom: '12px' }}>
                                    <h4 style={{ marginBottom: '12px' }}>{t('agent.settings.channel.title')}</h4>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>{t('agent.settings.channel.title')}</p>
                                    <div style={{
                                        padding: '10px 14px', borderRadius: '8px', marginBottom: '16px',
                                        background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.2)',
                                        fontSize: '12px', color: 'var(--text-secondary)', lineHeight: '1.6',
                                    }}>
                                        💡 {t('agent.settings.channel.syncHint', 'Before configuring the Feishu bot, please sync your organization structure in Enterprise Settings → Org Structure first. This ensures the bot can identify message senders.')}
                                    </div>

                                    {/* Slack */}
                                    <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', overflow: 'hidden', marginBottom: '12px' }}>
                                        <div onClick={() => setSlackOpen(!slackOpen)} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', cursor: 'pointer', transition: 'background 0.15s' }} onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-hover)')} onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M6.194 14.644a2.194 2.194 0 110 4.388 2.194 2.194 0 010-4.388zm-2.194 0H0v-2.194a2.194 2.194 0 014.388 0v2.194zm16.612 0a2.194 2.194 0 110 4.388 2.194 2.194 0 010-4.388zm0-2.194a2.194 2.194 0 010-4.388 2.194 2.194 0 010 4.388zm0 0v2.194h2.194A2.194 2.194 0 0024 12.45a2.194 2.194 0 00-2.194-2.194h-1.194zm-16.612 0a2.194 2.194 0 010-4.388 2.194 2.194 0 010 4.388zm0 0v2.194H2A2.194 2.194 0 010 12.45a2.194 2.194 0 012.194-2.194h1.806z" fill="#611F69" opacity=".4" /><path d="M9.388 4.388a2.194 2.194 0 110-4.388 2.194 2.194 0 010 4.388zm0 2.194v-2.194H7.194A2.194 2.194 0 005 6.582a2.194 2.194 0 002.194 2.194h2.194zm0 12.612a2.194 2.194 0 110 4.388 2.194 2.194 0 010-4.388zm0-2.194v2.194H7.194A2.194 2.194 0 005 17.418a2.194 2.194 0 002.194 2.194h.194zm4.224-12.612a2.194 2.194 0 110-4.388 2.194 2.194 0 010 4.388zm2.194 0H13.612V2.194a2.194 2.194 0 014.388 0v2.194zm-2.194 14.806a2.194 2.194 0 110 4.388 2.194 2.194 0 010-4.388zm-2.194 0h2.194v2.194a2.194 2.194 0 01-4.388 0v-2.194z" fill="#611F69" /></svg>
                                                <div>
                                                    <div style={{ fontWeight: 600, fontSize: '14px' }}>Slack</div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>Slack Bot</div>
                                                </div>
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                {slackConfig && <span className={`badge ${slackConfig.is_configured ? 'badge-success' : 'badge-warning'}`}>{slackConfig.is_configured ? t('agent.settings.channel.configured') : t('agent.settings.channel.notConfigured')}</span>}
                                                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', transition: 'transform 0.2s', transform: slackOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>▼</span>
                                            </div>
                                        </div>
                                        {slackOpen && (<div style={{ padding: '0 16px 16px', borderTop: '1px solid var(--border-subtle)' }}>
                                            {!canManage ? (
                                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontStyle: 'italic', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
                                                    Only the creator or admin can configure communication channels.
                                                </div>
                                            ) : slackConfig?.is_configured && !slackEditing ? (
                                                <div>
                                                    <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', fontFamily: 'var(--font-mono)', marginBottom: '12px' }}>
                                                        <div style={{ color: 'var(--text-tertiary)', marginBottom: '6px' }}>Webhook URL (Event Subscriptions URL)</div>
                                                        <div style={{ lineHeight: 1.6, wordBreak: 'break-all' }}>
                                                            <span style={{ color: 'var(--accent-primary)' }}>{slackWebhookData?.webhook_url || `${window.location.origin}/api/channel/slack/${id}/webhook`}</span>
                                                            <CopyBtn url={slackWebhookData?.webhook_url || `${window.location.origin}/api/channel/slack/${id}/webhook`} />
                                                        </div>
                                                    </div>
                                                    <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                        <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                            <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                                        </summary>
                                                        <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                            <li>{t('channelGuide.slack.step1')}</li>
                                                            <li>{t('channelGuide.slack.step2')}</li>
                                                            <li>{t('channelGuide.slack.step3')}</li>
                                                            <li>{t('channelGuide.slack.step4')}</li>
                                                            <li>{t('channelGuide.slack.step5')}</li>
                                                            <li>{t('channelGuide.slack.step6')}</li>
                                                            <li>{t('channelGuide.slack.step7')}</li>
                                                            <li>{t('channelGuide.slack.step8')}</li>
                                                        </ol>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>💡 {t('channelGuide.slack.note')}</div>
                                                    </details>
                                                    <div style={{ display: 'flex', gap: '8px' }}>
                                                        <button className="btn btn-secondary" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => { setSlackForm({ bot_token: slackConfig?.app_secret || '', signing_secret: slackConfig?.encrypt_key || '' }); setSlackEditing(true); }}>Edit</button>
                                                        <button className="btn btn-danger" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => deleteSlack.mutate()}>Disconnect</button>
                                                    </div>
                                                </div>
                                            ) : (
                                                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                                    <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                        <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                            <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                                        </summary>
                                                        <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                            <li>{t('channelGuide.slack.step1')}</li>
                                                            <li>{t('channelGuide.slack.step2')}</li>
                                                            <li>{t('channelGuide.slack.step3')}</li>
                                                            <li>{t('channelGuide.slack.step4')}</li>
                                                            <li>{t('channelGuide.slack.step5')}</li>
                                                            <li>{t('channelGuide.slack.step6')}</li>
                                                            <li>{t('channelGuide.slack.step7')}</li>
                                                            <li>{t('channelGuide.slack.step8')}</li>
                                                        </ol>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>💡 {t('channelGuide.slack.note')}</div>
                                                    </details>
                                                    <div>
                                                        <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>Bot Token *</label>
                                                        <div style={{ position: 'relative' }}>
                                                            <input className="input" type={showPwds['slack_token'] ? 'text' : 'password'} value={slackForm.bot_token} onChange={e => setSlackForm({ ...slackForm, bot_token: e.target.value })} placeholder="xoxb-..." style={{ fontSize: '12px', paddingRight: '36px', width: '100%' }} />
                                                            <button type="button" onClick={() => togglePwd('slack_token')} style={{ position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '2px', display: 'flex', alignItems: 'center' }}>{showPwds['slack_token'] ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19" /><line x1="1" y1="1" x2="23" y2="23" /></svg> : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>}</button>
                                                        </div>
                                                    </div>
                                                    <div>
                                                        <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>Signing Secret *</label>
                                                        <div style={{ position: 'relative' }}>
                                                            <input className="input" type={showPwds['slack_secret'] ? 'text' : 'password'} value={slackForm.signing_secret} onChange={e => setSlackForm({ ...slackForm, signing_secret: e.target.value })} style={{ fontSize: '12px', paddingRight: '36px', width: '100%' }} />
                                                            <button type="button" onClick={() => togglePwd('slack_secret')} style={{ position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '2px', display: 'flex', alignItems: 'center' }}>{showPwds['slack_secret'] ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19" /><line x1="1" y1="1" x2="23" y2="23" /></svg> : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>}</button>
                                                        </div>
                                                    </div>
                                                    <div style={{ display: 'flex', gap: '8px' }}>
                                                        <button className="btn btn-primary" style={{ fontSize: '12px', alignSelf: 'flex-start' }} onClick={() => { saveSlack.mutate(); setSlackEditing(false); }} disabled={!slackForm.bot_token || !slackForm.signing_secret || saveSlack.isPending}>
                                                            {saveSlack.isPending ? t('common.loading') : (slackEditing ? 'Save Changes' : t('agent.settings.channel.saveChannel'))}
                                                        </button>
                                                        {slackEditing && <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setSlackEditing(false)}>Cancel</button>}
                                                    </div>
                                                </div>
                                            )}
                                        </div>)}
                                    </div>

                                    {/* Discord */}
                                    <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', overflow: 'hidden', marginBottom: '12px' }}>
                                        <div onClick={() => setDiscordOpen(!discordOpen)} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', cursor: 'pointer', transition: 'background 0.15s' }} onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-hover)')} onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <svg width="20" height="20" viewBox="0 0 24 24" fill="#5865F2"><path d="M20.317 4.37a19.791 19.791 0 00-4.885-1.515.074.074 0 00-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 00-5.487 0 12.64 12.64 0 00-.617-1.25.077.077 0 00-.079-.037A19.736 19.736 0 003.677 4.37a.07.07 0 00-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 00.031.057 19.9 19.9 0 005.993 3.03.078.078 0 00.084-.028 14.09 14.09 0 001.226-1.994.076.076 0 00-.041-.106 13.107 13.107 0 01-1.872-.892.077.077 0 01-.008-.128 10.2 10.2 0 00.372-.292.074.074 0 01.077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 01.078.01c.12.098.246.198.373.292a.077.077 0 01-.006.127 12.299 12.299 0 01-1.873.892.077.077 0 00-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 00.084.028 19.839 19.839 0 006.002-3.03.077.077 0 00.032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 00-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z" /></svg>
                                                <div>
                                                    <div style={{ fontWeight: 600, fontSize: '14px' }}>Discord</div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>Slash Commands (/ask)</div>
                                                </div>
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                {discordConfig && <span className={`badge ${discordConfig.is_configured ? 'badge-success' : 'badge-warning'}`}>{discordConfig.is_configured ? t('agent.settings.channel.configured') : t('agent.settings.channel.notConfigured')}</span>}
                                                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', transition: 'transform 0.2s', transform: discordOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>▼</span>
                                            </div>
                                        </div>
                                        {discordOpen && (<div style={{ padding: '0 16px 16px', borderTop: '1px solid var(--border-subtle)' }}>
                                            {!canManage ? (
                                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontStyle: 'italic', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
                                                    Only the creator or admin can configure communication channels.
                                                </div>
                                            ) : discordConfig?.is_configured && !discordEditing ? (
                                                <div>
                                                    <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', fontFamily: 'var(--font-mono)', marginBottom: '12px' }}>
                                                        <div style={{ color: 'var(--text-tertiary)', marginBottom: '6px' }}>Interactions Endpoint URL</div>
                                                        <div style={{ lineHeight: 1.6, wordBreak: 'break-all' }}>
                                                            <span style={{ color: 'var(--accent-primary)' }}>{discordWebhookData?.webhook_url || `${window.location.origin}/api/channel/discord/${id}/webhook`}</span>
                                                            <CopyBtn url={discordWebhookData?.webhook_url || `${window.location.origin}/api/channel/discord/${id}/webhook`} />
                                                        </div>
                                                    </div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>Use <code>/ask message:&lt;your question&gt;</code> to talk to this agent</div>
                                                    <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                        <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                            <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                                        </summary>
                                                        <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                            <li>{t('channelGuide.discord.step1')}</li>
                                                            <li>{t('channelGuide.discord.step2')}</li>
                                                            <li>{t('channelGuide.discord.step3')}</li>
                                                            <li>{t('channelGuide.discord.step4')}</li>
                                                            <li>{t('channelGuide.discord.step5')}</li>
                                                            <li>{t('channelGuide.discord.step6')}</li>
                                                            <li>{t('channelGuide.discord.step7')}</li>
                                                        </ol>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>💡 {t('channelGuide.discord.note')}</div>
                                                    </details>
                                                    <div style={{ display: 'flex', gap: '8px' }}>
                                                        <button className="btn btn-secondary" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => { setDiscordForm({ application_id: discordConfig?.app_id || '', bot_token: discordConfig?.app_secret || '', public_key: discordConfig?.encrypt_key || '' }); setDiscordEditing(true); }}>Edit</button>
                                                        <button className="btn btn-danger" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => deleteDiscord.mutate()}>Disconnect</button>
                                                    </div>
                                                </div>
                                            ) : (
                                                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                                    <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                        <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                            <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                                        </summary>
                                                        <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                            <li>{t('channelGuide.discord.step1')}</li>
                                                            <li>{t('channelGuide.discord.step2')}</li>
                                                            <li>{t('channelGuide.discord.step3')}</li>
                                                            <li>{t('channelGuide.discord.step4')}</li>
                                                            <li>{t('channelGuide.discord.step5')}</li>
                                                            <li>{t('channelGuide.discord.step6')}</li>
                                                            <li>{t('channelGuide.discord.step7')}</li>
                                                        </ol>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>💡 {t('channelGuide.discord.note')}</div>
                                                    </details>
                                                    <div>
                                                        <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>Application ID *</label>
                                                        <input className="input" value={discordForm.application_id} onChange={e => setDiscordForm({ ...discordForm, application_id: e.target.value })} placeholder="1234567890" style={{ fontSize: '12px' }} />
                                                    </div>
                                                    <div>
                                                        <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>Bot Token *</label>
                                                        <div style={{ position: 'relative' }}>
                                                            <input className="input" type={showPwds['disc_token'] ? 'text' : 'password'} value={discordForm.bot_token} onChange={e => setDiscordForm({ ...discordForm, bot_token: e.target.value })} style={{ fontSize: '12px', paddingRight: '36px', width: '100%' }} />
                                                            <button type="button" onClick={() => togglePwd('disc_token')} style={{ position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '2px', display: 'flex', alignItems: 'center' }}>{showPwds['disc_token'] ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19" /><line x1="1" y1="1" x2="23" y2="23" /></svg> : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>}</button>
                                                        </div>
                                                    </div>
                                                    <div>
                                                        <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>Public Key *</label>
                                                        <input className="input" value={discordForm.public_key} onChange={e => setDiscordForm({ ...discordForm, public_key: e.target.value })} style={{ fontSize: '12px' }} />
                                                    </div>
                                                    <div style={{ display: 'flex', gap: '8px' }}>
                                                        <button className="btn btn-primary" style={{ fontSize: '12px', alignSelf: 'flex-start' }} onClick={() => { saveDiscord.mutate(); setDiscordEditing(false); }} disabled={!discordForm.application_id || !discordForm.bot_token || !discordForm.public_key || saveDiscord.isPending}>
                                                            {saveDiscord.isPending ? t('common.loading') : (discordEditing ? 'Save Changes' : t('agent.settings.channel.saveChannel'))}
                                                        </button>
                                                        {discordEditing && <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setDiscordEditing(false)}>Cancel</button>}
                                                    </div>
                                                </div>
                                            )}
                                        </div>)}
                                    </div>


                                    {/* Microsoft Teams */}
                                    <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', overflow: 'hidden', marginBottom: '12px' }}>
                                        <div onClick={() => setTeamsOpen(!teamsOpen)} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', cursor: 'pointer', transition: 'background 0.15s' }} onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-hover)')} onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                {/* Microsoft Teams icon (larger, similar visual weight to Discord/Slack) */}
                                                <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true">
                                                    {/* Main Teams tile */}
                                                    <rect x="3" y="4" width="13" height="13" rx="3" fill="#6264A7" />
                                                    {/* T letter */}
                                                    <path
                                                        d="M7.2 9h5.6c.3 0 .5.2.5.5v1.3c0 .3-.2.5-.5.5H11v5c0 .3-.2.5-.5.5H9.3c-.3 0-.5-.2-.5-.5v-5H7.2c-.3 0-.5-.2-.5-.5V9.5c0-.3.2-.5.5-.5Z"
                                                        fill="#FFFFFF"
                                                    />
                                                    {/* Right-side presence shapes */}
                                                    <circle cx="18.5" cy="7.5" r="2.3" fill="#7B83EB" />
                                                    <rect x="16.2" y="10.5" width="5" height="6.2" rx="2" fill="#7B83EB" />
                                                </svg>
                                                <div>
                                                    <div style={{ fontWeight: 600, fontSize: '14px' }}>Microsoft Teams</div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>Teams Bot</div>
                                                </div>
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                {teamsConfig && <span className={`badge ${teamsConfig.is_configured ? 'badge-success' : 'badge-warning'}`}>{teamsConfig.is_configured ? t('agent.settings.channel.configured') : t('agent.settings.channel.notConfigured')}</span>}
                                                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', transition: 'transform 0.2s', transform: teamsOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>▼</span>
                                            </div>
                                        </div>
                                        {teamsOpen && (<div style={{ padding: '0 16px 16px', borderTop: '1px solid var(--border-subtle)' }}>
                                            {!canManage ? (
                                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontStyle: 'italic', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
                                                    Only the creator or admin can configure communication channels.
                                                </div>
                                            ) : teamsConfig?.is_configured && !teamsEditing ? (
                                                <div>
                                                    <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', fontFamily: 'var(--font-mono)', marginBottom: '12px' }}>
                                                        <div style={{ color: 'var(--text-tertiary)', marginBottom: '6px' }}>Messaging Endpoint URL</div>
                                                        <div style={{ lineHeight: 1.6, wordBreak: 'break-all' }}>
                                                            <span style={{ color: 'var(--accent-primary)' }}>{teamsWebhookData?.webhook_url || `${window.location.origin}/api/channel/teams/${id}/webhook`}</span>
                                                            <CopyBtn url={teamsWebhookData?.webhook_url || `${window.location.origin}/api/channel/teams/${id}/webhook`} />
                                                        </div>
                                                    </div>
                                                    <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                        <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                            <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                                        </summary>
                                                        <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                            <li>{t('channelGuide.teams.step1')}</li>
                                                            <li>{t('channelGuide.teams.step2')}</li>
                                                            <li>{t('channelGuide.teams.step3')}</li>
                                                            <li>{t('channelGuide.teams.step4')}</li>
                                                            <li>{t('channelGuide.teams.step5')}</li>
                                                        </ol>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>💡 {t('channelGuide.teams.note')}</div>
                                                    </details>
                                                    <div style={{ display: 'flex', gap: '8px' }}>
                                                        <button className="btn btn-secondary" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => { setTeamsForm({ app_id: teamsConfig?.app_id || '', app_secret: teamsConfig?.app_secret || '', tenant_id: teamsConfig?.extra_config?.tenant_id || '' }); setTeamsEditing(true); }}>Edit</button>
                                                        <button className="btn btn-danger" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => deleteTeams.mutate()}>Disconnect</button>
                                                    </div>
                                                </div>
                                            ) : (
                                                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                                    <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                        <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                            <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                                        </summary>
                                                        <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                            <li>{t('channelGuide.teams.step1')}</li>
                                                            <li>{t('channelGuide.teams.step2')}</li>
                                                            <li>{t('channelGuide.teams.step3')}</li>
                                                            <li>{t('channelGuide.teams.step4')}</li>
                                                            <li>{t('channelGuide.teams.step5')}</li>
                                                        </ol>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>💡 {t('channelGuide.teams.note')}</div>
                                                    </details>
                                                    <div>
                                                        <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>App ID (Client ID) *</label>
                                                        <input className="input" value={teamsForm.app_id} onChange={e => setTeamsForm({ ...teamsForm, app_id: e.target.value })} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" style={{ fontSize: '12px' }} />
                                                    </div>
                                                    <div>
                                                        <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>App Secret (Client Secret) *</label>
                                                        <div style={{ position: 'relative' }}>
                                                            <input className="input" type={showPwds['teams_secret'] ? 'text' : 'password'} value={teamsForm.app_secret} onChange={e => setTeamsForm({ ...teamsForm, app_secret: e.target.value })} style={{ fontSize: '12px', paddingRight: '36px', width: '100%' }} />
                                                            <button type="button" onClick={() => togglePwd('teams_secret')} style={{ position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '2px', display: 'flex', alignItems: 'center' }}>{showPwds['teams_secret'] ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19" /><line x1="1" y1="1" x2="23" y2="23" /></svg> : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>}</button>
                                                        </div>
                                                    </div>
                                                    <div>
                                                        <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>{t('channelGuide.teams.tenantId')} <span style={{ color: 'var(--text-tertiary)', fontWeight: 'normal' }}>(Optional)</span></label>
                                                        <input className="input" value={teamsForm.tenant_id} onChange={e => setTeamsForm({ ...teamsForm, tenant_id: e.target.value })} placeholder={t('channelGuide.teams.tenantIdPlaceholder')} style={{ fontSize: '12px' }} />
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('channelGuide.teams.tenantIdHint')}</div>
                                                    </div>
                                                    <div style={{ display: 'flex', gap: '8px' }}>
                                                        <button className="btn btn-primary" style={{ fontSize: '12px', alignSelf: 'flex-start' }} onClick={() => { saveTeams.mutate(); setTeamsEditing(false); }} disabled={(!teamsForm.app_id || !teamsForm.app_secret) || saveTeams.isPending}>
                                                            {saveTeams.isPending ? t('common.loading') : (teamsEditing ? 'Save Changes' : t('agent.settings.channel.saveChannel'))}
                                                        </button>
                                                        {teamsEditing && <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setTeamsEditing(false)}>Cancel</button>}
                                                    </div>
                                                </div>
                                            )}
                                        </div>)}
                                    </div>

                                    {/* Feishu */}
                                    <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', overflow: 'hidden', marginBottom: '12px' }}>
                                        <div onClick={() => setFeishuOpen(!feishuOpen)} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', cursor: 'pointer', transition: 'background 0.15s' }} onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-hover)')} onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M3.64 7.2c.83 2.33 2.52 4.36 4.76 5.53L19.2 3.2c-.32-.09-.67-.11-1.03-.04L3.64 7.2z" fill="#00D6B9"/><path d="M8.4 12.73c.68.35 1.41.6 2.16.73l10.2-7.52c-.26-.56-.72-1.02-1.36-1.24L8.4 12.73z" fill="#3370FF"/><path d="M10.56 13.46c1.18.19 2.39.09 3.5-.3l6.86-5.06-.12-.14L10.56 13.46z" fill="#3370FF"/><path d="M14.06 13.16a8.1 8.1 0 002.62-1.67l4.24-3.13-.12-.42L14.06 13.16z" fill="#3370FF"/><path d="M16.68 11.49a8 8 0 001.7-2.15l2.54-1.87-.12-.53-4.12 4.55z" fill="#3370FF"/><path d="M3.64 7.2l-.24.7c-.94 2.82-.37 5.6 1.36 7.7L16.68 3.96 3.64 7.2z" fill="#00D6B9"/><path d="M4.76 15.6a8.02 8.02 0 003.64 3.94V12.73l-3.64 2.87z" fill="#3370FF"/><path d="M8.4 19.54c.68.35 1.41.56 2.16.64v-6.72l-2.16 6.08z" fill="#3370FF"/></svg>
                                                <div>
                                                    <div style={{ fontWeight: 600, fontSize: '14px' }}>{t('agent.settings.channel.feishu')}</div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{channelConfig?.extra_config?.connection_mode === 'websocket' ? 'WebSocket Mode' : 'Feishu / Lark'}</div>
                                                </div>
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                {channelConfig && (
                                                    <span className={`badge ${channelConfig.is_configured ? 'badge-success' : 'badge-warning'}`}>
                                                        {channelConfig.is_configured ? t('agent.settings.channel.configured') : t('agent.settings.channel.notConfigured')}
                                                    </span>
                                                )}
                                                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', transition: 'transform 0.2s', transform: feishuOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>▼</span>
                                            </div>
                                        </div>
                                        {feishuOpen && (<div style={{ padding: '0 16px 16px', borderTop: '1px solid var(--border-subtle)' }}>

                                            {!canManage ? (
                                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontStyle: 'italic', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
                                                    Only the creator or admin can configure communication channels.
                                                </div>
                                            ) : channelConfig && !feishuEditing ? (
                                                <div>
                                                                                                        {channelConfig.extra_config?.connection_mode === 'websocket' ? (
                                                        <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', marginBottom: '12px' }}>
                                                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
                                                                <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#00D6B9', display: 'inline-block' }}></span>
                                                                <span style={{ color: 'var(--text-secondary)' }}>Connected via WebSocket (No callback URL needed)</span>
                                                            </div>
                                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>App ID: <code>{channelConfig.app_id}</code></div>
                                                        </div>
                                                    ) : (
                                                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>
                                                            <div style={{ marginBottom: '4px' }}>Mode: <strong>Webhook</strong></div>
                                                            <div>App ID: <code>{channelConfig.app_id}</code></div>
                                                        </div>
                                                    )}
                                                    {channelConfig.extra_config?.connection_mode !== 'websocket' && (
                                                        <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', fontFamily: 'var(--font-mono)', marginBottom: '12px' }}>
                                                            <div style={{ color: 'var(--text-tertiary)', marginBottom: '6px' }}>Webhook URL</div>
                                                            <div style={{ lineHeight: 1.6, wordBreak: 'break-all' }}>
                                                                <span style={{ color: 'var(--accent-primary)' }}>
                                                                    {webhookData?.webhook_url || `${window.location.origin}/api/channel/feishu/${id}/webhook`}
                                                                </span>
                                                                <button
                                                                    title="Copy"
                                                                    style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', marginLeft: '6px', padding: '1px 4px', cursor: 'pointer', borderRadius: '3px', border: '1px solid var(--border-color)', background: 'var(--bg-primary)', color: 'var(--text-secondary)', verticalAlign: 'middle', lineHeight: 1 }}
                                                                    onClick={(e) => {
                                                                        const url = webhookData?.webhook_url || `${window.location.origin}/api/channel/feishu/${id}/webhook`;
                                                                        navigator.clipboard.writeText(url).then(() => {
                                                                            const btn = e.currentTarget as HTMLButtonElement;
                                                                            const origHtml = btn.innerHTML;
                                                                            btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="13 2 5 10 2 7"/></svg>';
                                                                            btn.style.color = 'rgb(16,185,129)';
                                                                            setTimeout(() => { btn.innerHTML = origHtml; btn.style.color = ''; }, 1500);
                                                                        });
                                                                    }}
                                                                >
                                                                    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                                                        <rect x="4" y="4" width="9" height="11" rx="1.5" />
                                                                        <path d="M3 11H2a1 1 0 01-1-1V2a1 1 0 011-1h8a1 1 0 011 1v1" />
                                                                    </svg>
                                                                </button>
                                                            </div>
                                                        </div>
                                                    )}
                                                    <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                        <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                            <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                                        </summary>
                                                        <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                            {channelConfig?.extra_config?.connection_mode === 'websocket' ? (<>
                                                            <li>{t('channelGuide.feishu.ws_step1')}</li>
                                                            <li>{t('channelGuide.feishu.ws_step2')}</li>
                                                            <li>{t('channelGuide.feishu.ws_step3')}</li>
                                                            <li>{t('channelGuide.feishu.ws_step4')}</li>
                                                            <li>{t('channelGuide.feishu.ws_step5')}</li>
                                                            <li>{t('channelGuide.feishu.ws_step6')}</li>
                                                            <li>{t('channelGuide.feishu.ws_step7')}</li>
                                                            </>) : (<>
                                                            <li>{t('channelGuide.feishu.step1')}</li>
                                                            <li>{t('channelGuide.feishu.step2')}</li>
                                                            <li>{t('channelGuide.feishu.step3')}</li>
                                                            <li>{t('channelGuide.feishu.step4')}</li>
                                                            <li>{t('channelGuide.feishu.step5')}</li>
                                                            <li>{t('channelGuide.feishu.step6')}</li>
                                                            <li>{t('channelGuide.feishu.step7')}</li>
                                                            <li>{t('channelGuide.feishu.step8')}</li>
                                                            </>)}
                                                        </ol>
                                                        <div style={{ margin: '8px 0', borderRadius: '6px', border: '1px solid var(--border-color)', overflow: 'hidden' }}>
                                                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 10px', background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-color)' }}>
                                                                <span style={{ fontSize: '10px', color: 'var(--text-secondary)', fontWeight: 500 }}>{t('channelGuide.feishuPermJson')}</span>
                                                                <button type="button" style={{ fontSize: '10px', padding: '1px 7px', cursor: 'pointer', borderRadius: '3px', border: '1px solid var(--border-color)', background: 'var(--bg-primary)', color: 'var(--text-secondary)' }} onClick={(e) => { const btn = e.currentTarget as HTMLButtonElement; const json = '{"scopes":{"tenant":["contact:contact.base:readonly","contact:user.base:readonly","contact:user.id:readonly","im:chat","im:message","im:message.group_at_msg:readonly","im:message.p2p_msg:readonly","im:message:send_as_bot","im:resource"],"user":[]}}'; navigator.clipboard.writeText(json).then(() => { const o = btn.textContent; btn.textContent = t('channelGuide.feishuPermCopied'); btn.style.color = 'rgb(16,185,129)'; setTimeout(() => { btn.textContent = o; btn.style.color = ''; }, 1500); }); }}>{t('channelGuide.feishuPermCopy')}</button>
                                                            </div>
                                                            <pre style={{ margin: 0, padding: '6px 10px', fontSize: '10px', fontFamily: 'var(--font-mono)', lineHeight: 1.5, background: 'var(--bg-primary)', color: 'var(--text-secondary)', overflowX: 'auto', userSelect: 'all' }}>{`{
  "scopes": {
    "tenant": [
      "contact:contact.base:readonly",
      "contact:user.base:readonly",
      "contact:user.id:readonly",
      "im:chat",
      "im:message",
      "im:message.group_at_msg:readonly",
      "im:message.p2p_msg:readonly",
      "im:message:send_as_bot",
      "im:resource"
    ],
    "user": []
  }
}`}</pre>
                                                        </div>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>💡 {t('channelGuide.feishu.note')}</div>
                                                    </details>
                                                    <div style={{ display: 'flex', gap: '8px' }}>
                                                        <button className="btn btn-secondary" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => { setChannelForm({ app_id: channelConfig.app_id || '', app_secret: channelConfig.app_secret || '', encrypt_key: channelConfig.encrypt_key || '', connection_mode: channelConfig.extra_config?.connection_mode || 'webhook' }); setFeishuEditing(true); }}>Edit</button>
                                                        <button className="btn btn-danger" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={async () => { await channelApi.delete(id!); queryClient.invalidateQueries({ queryKey: ['channel', id] }); }}>Disconnect</button>
                                                    </div>
                                                </div>
                                            ) : (
                                                <div>
                                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '8px' }}>
                                                        <div>
                                                            <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '8px' }}>Connection Mode</label>
                                                            <div style={{ display: 'flex', gap: '16px', marginBottom: '8px' }}>
                                                                <label style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                                                                    <input type="radio" name="connection_mode" value="websocket" checked={channelForm.connection_mode === 'websocket'} onChange={() => setChannelForm({ ...channelForm, connection_mode: 'websocket' })} />
                                                                    WebSocket (Recommended)
                                                                </label>
                                                                <label style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                                                                    <input type="radio" name="connection_mode" value="webhook" checked={channelForm.connection_mode === 'webhook'} onChange={() => setChannelForm({ ...channelForm, connection_mode: 'webhook' })} />
                                                                    Webhook
                                                                </label>
                                                            </div>
                                                        </div>
                                                    </div>
                                                    <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                        <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                            <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                                        </summary>
                                                        <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                            {channelForm.connection_mode === 'websocket' ? (<>
                                                            <li>{t('channelGuide.feishu.ws_step1')}</li>
                                                            <li>{t('channelGuide.feishu.ws_step2')}</li>
                                                            <li>{t('channelGuide.feishu.ws_step3')}</li>
                                                            <li>{t('channelGuide.feishu.ws_step4')}</li>
                                                            <li>{t('channelGuide.feishu.ws_step5')}</li>
                                                            <li>{t('channelGuide.feishu.ws_step6')}</li>
                                                            <li>{t('channelGuide.feishu.ws_step7')}</li>
                                                            </>) : (<>
                                                            <li>{t('channelGuide.feishu.step1')}</li>
                                                            <li>{t('channelGuide.feishu.step2')}</li>
                                                            <li>{t('channelGuide.feishu.step3')}</li>
                                                            <li>{t('channelGuide.feishu.step4')}</li>
                                                            <li>{t('channelGuide.feishu.step5')}</li>
                                                            <li>{t('channelGuide.feishu.step6')}</li>
                                                            <li>{t('channelGuide.feishu.step7')}</li>
                                                            <li>{t('channelGuide.feishu.step8')}</li>
                                                            </>)}
                                                        </ol>
                                                        <div style={{ margin: '8px 0', borderRadius: '6px', border: '1px solid var(--border-color)', overflow: 'hidden' }}>
                                                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 10px', background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-color)' }}>
                                                                <span style={{ fontSize: '10px', color: 'var(--text-secondary)', fontWeight: 500 }}>{t('channelGuide.feishuPermJson')}</span>
                                                                <button type="button" style={{ fontSize: '10px', padding: '1px 7px', cursor: 'pointer', borderRadius: '3px', border: '1px solid var(--border-color)', background: 'var(--bg-primary)', color: 'var(--text-secondary)' }} onClick={(e) => { const btn = e.currentTarget as HTMLButtonElement; const json = '{"scopes":{"tenant":["contact:contact.base:readonly","contact:user.base:readonly","contact:user.id:readonly","im:chat","im:message","im:message.group_at_msg:readonly","im:message.p2p_msg:readonly","im:message:send_as_bot","im:resource"],"user":[]}}'; navigator.clipboard.writeText(json).then(() => { const o = btn.textContent; btn.textContent = t('channelGuide.feishuPermCopied'); btn.style.color = 'rgb(16,185,129)'; setTimeout(() => { btn.textContent = o; btn.style.color = ''; }, 1500); }); }}>{t('channelGuide.feishuPermCopy')}</button>
                                                            </div>
                                                            <pre style={{ margin: 0, padding: '6px 10px', fontSize: '10px', fontFamily: 'var(--font-mono)', lineHeight: 1.5, background: 'var(--bg-primary)', color: 'var(--text-secondary)', overflowX: 'auto', userSelect: 'all' }}>{`{
  "scopes": {
    "tenant": [
      "contact:contact.base:readonly",
      "contact:user.base:readonly",
      "contact:user.id:readonly",
      "im:chat",
      "im:message",
      "im:message.group_at_msg:readonly",
      "im:message.p2p_msg:readonly",
      "im:message:send_as_bot",
      "im:resource"
    ],
    "user": []
  }
}`}</pre>
                                                        </div>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>💡 {t('channelGuide.feishu.note')}</div>
                                                    </details>
                                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
                                                        <div>
                                                            <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>App ID *</label>
                                                            <input className="input" value={channelForm.app_id} onChange={e => setChannelForm({ ...channelForm, app_id: e.target.value })} placeholder="cli_xxxxxxxxxxxxxxxx" style={{ fontSize: '12px' }} />
                                                        </div>
                                                        <div>
                                                            <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>App Secret *</label>
                                                            <div style={{ position: 'relative' }}>
                                                                <input className="input" type={showPwds['feishu_secret'] ? 'text' : 'password'} value={channelForm.app_secret} onChange={e => setChannelForm({ ...channelForm, app_secret: e.target.value })} style={{ fontSize: '12px', paddingRight: '36px', width: '100%' }} />
                                                                <button type="button" onClick={() => togglePwd('feishu_secret')} style={{ position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '2px', display: 'flex', alignItems: 'center' }}>{showPwds['feishu_secret'] ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19" /><line x1="1" y1="1" x2="23" y2="23" /></svg> : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>}</button>
                                                            </div>
                                                        </div>
                                                        {channelForm.connection_mode === 'webhook' && (
                                                            <div>
                                                                <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>Encrypt Key</label>
                                                                <div style={{ position: 'relative' }}>
                                                                    <input className="input" type={showPwds['feishu_encrypt'] ? 'text' : 'password'} value={channelForm.encrypt_key} onChange={e => setChannelForm({ ...channelForm, encrypt_key: e.target.value })} style={{ fontSize: '12px', paddingRight: '36px', width: '100%' }} />
                                                                    <button type="button" onClick={() => togglePwd('feishu_encrypt')} style={{ position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '2px', display: 'flex', alignItems: 'center' }}>{showPwds['feishu_encrypt'] ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19" /><line x1="1" y1="1" x2="23" y2="23" /></svg> : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>}</button>
                                                                </div>
                                                            </div>
                                                        )}
                                                    </div>
                                                    <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                                                        <button className="btn btn-primary" style={{ fontSize: '12px' }} onClick={() => { saveChannel.mutate(); setFeishuEditing(false); }} disabled={!channelForm.app_id || !channelForm.app_secret || saveChannel.isPending}>
                                                            {saveChannel.isPending ? t('common.loading') : (feishuEditing ? 'Save Changes' : t('agent.settings.channel.saveChannel'))}
                                                        </button>
                                                        {feishuEditing && <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setFeishuEditing(false)}>Cancel</button>}
                                                    </div>
                                                </div>
                                            )}
                                        </div>)}
                                    </div>

                                    {/* WeCom */}
                                    <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', overflow: 'hidden', marginBottom: '12px' }}>
                                        <div onClick={() => setWecomOpen(!wecomOpen)} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', cursor: 'pointer', transition: 'background 0.15s' }} onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-hover)')} onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" fill="#07C160"/><path d="M7.5 9.5a1 1 0 110-2 1 1 0 010 2zm4 0a1 1 0 110-2 1 1 0 010 2zm4 0a1 1 0 110-2 1 1 0 010 2zM6 13h5l2 3h-3l-1 2-1-2H6v-3z" fill="white"/></svg>
                                                <div>
                                                    <div style={{ fontWeight: 600, fontSize: '14px' }}>{t('common.channels.wecom')}</div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{wecomConfig?.extra_config?.connection_mode === 'websocket' ? 'WebSocket Mode' : 'Webhook'}</div>
                                                </div>
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                {wecomConfig && <span className={`badge ${wecomConfig.is_configured ? 'badge-success' : 'badge-warning'}`}>{wecomConfig.is_configured ? t('agent.settings.channel.configured') : t('agent.settings.channel.notConfigured')}</span>}
                                                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', transition: 'transform 0.2s', transform: wecomOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>&#9660;</span>
                                            </div>
                                        </div>
                                        {wecomOpen && (<div style={{ padding: '0 16px 16px', borderTop: '1px solid var(--border-subtle)' }}>
                                            {!canManage ? (
                                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontStyle: 'italic', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
                                                    Only the creator or admin can configure communication channels.
                                                </div>
                                            ) : wecomConfig?.is_configured && !wecomEditing ? (
                                                <div>
                                                    {wecomConfig?.extra_config?.connection_mode === 'websocket' ? (
                                                        <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', marginBottom: '12px' }}>
                                                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                                <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#07C160', display: 'inline-block' }}></span>
                                                                <span style={{ color: 'var(--text-secondary)' }}>Connected via WebSocket (No callback URL needed)</span>
                                                            </div>
                                                        </div>
                                                    ) : (
                                                        <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', fontFamily: 'var(--font-mono)', marginBottom: '12px' }}>
                                                            <div style={{ color: 'var(--text-tertiary)', marginBottom: '6px' }}>Webhook URL</div>
                                                            <div style={{ lineHeight: 1.6, wordBreak: 'break-all' }}>
                                                                <span style={{ color: 'var(--accent-primary)' }}>{wecomWebhookData?.webhook_url || `${window.location.origin}/api/channel/wecom/${id}/webhook`}</span>
                                                                <CopyBtn url={wecomWebhookData?.webhook_url || `${window.location.origin}/api/channel/wecom/${id}/webhook`} />
                                                            </div>
                                                        </div>
                                                    )}
                                                    <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                        <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                            <span style={{ fontSize: '10px' }}>&#9654;</span> {t('channelGuide.setupGuide')}
                                                        </summary>
                                                        <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                            {wecomConfig?.extra_config?.connection_mode === 'websocket' ? (<>
                                                            <li>{t('channelGuide.wecom.ws_step1')}</li>
                                                            <li>{t('channelGuide.wecom.ws_step2')}</li>
                                                            <li>{t('channelGuide.wecom.ws_step3')}</li>
                                                            <li>{t('channelGuide.wecom.ws_step4')}</li>
                                                            <li>{t('channelGuide.wecom.ws_step5')}</li>
                                                            <li>{t('channelGuide.wecom.ws_step6')}</li>
                                                            </>) : (<>
                                                            <li>{t('channelGuide.wecom.step1')}</li>
                                                            <li>{t('channelGuide.wecom.step2')}</li>
                                                            <li>{t('channelGuide.wecom.step3')}</li>
                                                            <li>{t('channelGuide.wecom.step4')}</li>
                                                            <li>{t('channelGuide.wecom.step5')}</li>
                                                            <li>{t('channelGuide.wecom.step6')}</li>
                                                            </>)}
                                                        </ol>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>{wecomConfig?.extra_config?.connection_mode === 'websocket' ? t('channelGuide.wecom.ws_note') : t('channelGuide.wecom.note')}</div>
                                                    </details>
                                                    <div style={{ display: 'flex', gap: '8px' }}>
                                                        <button className="btn btn-secondary" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => { setWecomForm({ connection_mode: wecomConfig?.extra_config?.connection_mode === 'websocket' ? 'websocket' : 'webhook', bot_id: wecomConfig?.extra_config?.bot_id || '', bot_secret: wecomConfig?.extra_config?.bot_secret || '', corp_id: wecomConfig?.app_id || '', wecom_agent_id: wecomConfig?.extra_config?.wecom_agent_id || '', secret: wecomConfig?.app_secret || '', token: wecomConfig?.verification_token || '', encoding_aes_key: wecomConfig?.encrypt_key || '' }); setWecomEditing(true); }}>Edit</button>
                                                        <button className="btn btn-danger" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => deleteWecom.mutate()}>Disconnect</button>
                                                    </div>
                                                </div>
                                            ) : (
                                                <div>
                                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
                                                        <div>
                                                            <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '8px' }}>Connection Mode</label>
                                                            <div style={{ display: 'flex', gap: '16px', marginBottom: '8px' }}>
                                                                <label style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                                                                    <input type="radio" name="wecom_connection_mode" value="websocket" checked={wecomForm.connection_mode === 'websocket'} onChange={() => setWecomForm(f => ({ ...f, connection_mode: 'websocket' }))} />
                                                                    WebSocket (Recommended)
                                                                </label>
                                                                <label style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                                                                    <input type="radio" name="wecom_connection_mode" value="webhook" checked={wecomForm.connection_mode === 'webhook'} onChange={() => setWecomForm(f => ({ ...f, connection_mode: 'webhook' }))} />
                                                                    Webhook
                                                                </label>
                                                            </div>
                                                        </div>
                                                    <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                        <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                            <span style={{ fontSize: '10px' }}>&#9654;</span> {t('channelGuide.setupGuide')}
                                                        </summary>
                                                        <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                            {wecomForm.connection_mode === 'websocket' ? (<>
                                                            <li>{t('channelGuide.wecom.ws_step1')}</li>
                                                            <li>{t('channelGuide.wecom.ws_step2')}</li>
                                                            <li>{t('channelGuide.wecom.ws_step3')}</li>
                                                            <li>{t('channelGuide.wecom.ws_step4')}</li>
                                                            <li>{t('channelGuide.wecom.ws_step5')}</li>
                                                            <li>{t('channelGuide.wecom.ws_step6')}</li>
                                                            </>) : (<>
                                                            <li>{t('channelGuide.wecom.step1')}</li>
                                                            <li>{t('channelGuide.wecom.step2')}</li>
                                                            <li>{t('channelGuide.wecom.step3')}</li>
                                                            <li>{t('channelGuide.wecom.step4')}</li>
                                                            <li>{t('channelGuide.wecom.step5')}</li>
                                                            <li>{t('channelGuide.wecom.step6')}</li>
                                                            </>)}
                                                        </ol>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>{wecomForm.connection_mode === 'websocket' ? t('channelGuide.wecom.ws_note') : t('channelGuide.wecom.note')}</div>
                                                    </details>
                                                        {wecomForm.connection_mode === 'websocket' ? (<>
                                                        <div>
                                                            <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>Bot ID *</label>
                                                            <input className="input" placeholder="aibXXXXXXXXXXXX" value={wecomForm.bot_id} onChange={e => setWecomForm(f => ({ ...f, bot_id: e.target.value }))} style={{ fontSize: '12px' }} />
                                                        </div>
                                                        <div>
                                                            <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>Bot Secret *</label>
                                                            <div style={{ position: 'relative' }}>
                                                                <input className="input" type={showPwds['wc_bot_secret'] ? 'text' : 'password'} value={wecomForm.bot_secret} onChange={e => setWecomForm(f => ({ ...f, bot_secret: e.target.value }))} style={{ fontSize: '12px', paddingRight: '36px', width: '100%' }} />
                                                                <button type="button" onClick={() => togglePwd('wc_bot_secret')} style={{ position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '2px', display: 'flex', alignItems: 'center' }}>{showPwds['wc_bot_secret'] ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19" /><line x1="1" y1="1" x2="23" y2="23" /></svg> : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>}</button>
                                                            </div>
                                                        </div>
                                                        </>) : (<>
                                                        <div>
                                                            <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>CorpID *</label>
                                                            <input className="input" placeholder="CorpID" value={wecomForm.corp_id} onChange={e => setWecomForm(f => ({ ...f, corp_id: e.target.value }))} style={{ fontSize: '12px' }} />
                                                        </div>
                                                        <div>
                                                            <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>AgentID *</label>
                                                            <input className="input" placeholder="AgentID" value={wecomForm.wecom_agent_id} onChange={e => setWecomForm(f => ({ ...f, wecom_agent_id: e.target.value }))} style={{ fontSize: '12px' }} />
                                                        </div>
                                                        <div>
                                                            <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>Secret *</label>
                                                            <div style={{ position: 'relative' }}>
                                                                <input className="input" type={showPwds['wc_secret'] ? 'text' : 'password'} value={wecomForm.secret} onChange={e => setWecomForm(f => ({ ...f, secret: e.target.value }))} style={{ fontSize: '12px', paddingRight: '36px', width: '100%' }} />
                                                                <button type="button" onClick={() => togglePwd('wc_secret')} style={{ position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '2px', display: 'flex', alignItems: 'center' }}>{showPwds['wc_secret'] ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19" /><line x1="1" y1="1" x2="23" y2="23" /></svg> : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>}</button>
                                                            </div>
                                                        </div>
                                                        <div>
                                                            <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>Token *</label>
                                                            <input className="input" placeholder="Token" value={wecomForm.token} onChange={e => setWecomForm(f => ({ ...f, token: e.target.value }))} style={{ fontSize: '12px' }} />
                                                        </div>
                                                        <div>
                                                            <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>EncodingAESKey *</label>
                                                            <input className="input" placeholder="EncodingAESKey" value={wecomForm.encoding_aes_key} onChange={e => setWecomForm(f => ({ ...f, encoding_aes_key: e.target.value }))} style={{ fontSize: '12px' }} />
                                                        </div>
                                                        </>)}
                                                    </div>
                                                    <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                                                        <button className="btn btn-primary" style={{ fontSize: '12px', alignSelf: 'flex-start' }} onClick={() => { saveWecom.mutate(); setWecomEditing(false); }} disabled={(wecomForm.connection_mode === 'websocket' ? (!wecomForm.bot_id || !wecomForm.bot_secret) : (!wecomForm.corp_id || !wecomForm.secret || !wecomForm.token || !wecomForm.encoding_aes_key)) || saveWecom.isPending}>
                                                            {saveWecom.isPending ? t('common.loading') : (wecomEditing ? 'Save Changes' : t('agent.settings.channel.saveChannel'))}
                                                        </button>
                                                        {wecomEditing && <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setWecomEditing(false)}>Cancel</button>}
                                                    </div>
                                                </div>
                                            )}
                                        </div>)}
                                    </div>

                                    {/* DingTalk */}
                                    <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', overflow: 'hidden', marginBottom: '12px' }}>
                                        <div onClick={() => setDingtalkOpen(!dingtalkOpen)} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', cursor: 'pointer', transition: 'background 0.15s' }} onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-hover)')} onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" fill="#0089FF"/><path d="M16.7 10.3c-.2-.7-1.1-1.2-1.7-.8l-2.5 1.8c-.3.2-.1.7.3.6l1.2-.2s-2 2.5-4.3 3.5c0 0 1.3.4 2.8-.1 1.5-.5 3.1-1.8 3.1-1.8l-.3 1.1c-.1.3.2.5.4.3l2-2c.5-.5.3-1.3-.1-1.7l-1-.7z" fill="white"/></svg>
                                                <div>
                                                    <div style={{ fontWeight: 600, fontSize: '14px' }}>{t('common.channels.dingtalk')}</div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>Stream Mode</div>
                                                </div>
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                {dingtalkConfig && <span className={`badge ${dingtalkConfig.is_configured ? 'badge-success' : 'badge-warning'}`}>{dingtalkConfig.is_configured ? t('agent.settings.channel.configured') : t('agent.settings.channel.notConfigured')}</span>}
                                                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', transition: 'transform 0.2s', transform: dingtalkOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>&#9660;</span>
                                            </div>
                                        </div>
                                        {dingtalkOpen && (<div style={{ padding: '0 16px 16px', borderTop: '1px solid var(--border-subtle)' }}>
                                            {!canManage ? (
                                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontStyle: 'italic', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
                                                    Only the creator or admin can configure communication channels.
                                                </div>
                                            ) : dingtalkConfig?.is_configured && !dingtalkEditing ? (
                                                <div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '8px', padding: '8px 10px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
                                                        Stream mode active. No webhook URL needed.
                                                    </div>
                                                    <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                        <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                            <span style={{ fontSize: '10px' }}>&#9654;</span> {t('channelGuide.setupGuide')}
                                                        </summary>
                                                        <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                            <li>{t('channelGuide.dingtalk.step1')}</li>
                                                            <li>{t('channelGuide.dingtalk.step2')}</li>
                                                            <li>{t('channelGuide.dingtalk.step3')}</li>
                                                            <li>{t('channelGuide.dingtalk.step4')}</li>
                                                            <li>{t('channelGuide.dingtalk.step5')}</li>
                                                            <li>{t('channelGuide.dingtalk.step6')}</li>
                                                        </ol>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>{t('channelGuide.dingtalk.note')}</div>
                                                    </details>
                                                    <div style={{ display: 'flex', gap: '8px' }}>
                                                        <button className="btn btn-secondary" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => { setDingtalkForm({ app_key: dingtalkConfig?.app_id || '', app_secret: dingtalkConfig?.app_secret || '' }); setDingtalkEditing(true); }}>Edit</button>
                                                        <button className="btn btn-danger" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => deleteDingtalk.mutate()}>Disconnect</button>
                                                    </div>
                                                </div>
                                            ) : (
                                                <div>
                                                    <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                        <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                            <span style={{ fontSize: '10px' }}>&#9654;</span> {t('channelGuide.setupGuide')}
                                                        </summary>
                                                        <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                            <li>{t('channelGuide.dingtalk.step1')}</li>
                                                            <li>{t('channelGuide.dingtalk.step2')}</li>
                                                            <li>{t('channelGuide.dingtalk.step3')}</li>
                                                            <li>{t('channelGuide.dingtalk.step4')}</li>
                                                            <li>{t('channelGuide.dingtalk.step5')}</li>
                                                            <li>{t('channelGuide.dingtalk.step6')}</li>
                                                        </ol>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>{t('channelGuide.dingtalk.note')}</div>
                                                    </details>
                                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
                                                        <input className="input" placeholder="AppKey" type={showPwds['dt_key'] ? 'text' : 'password'} value={dingtalkForm.app_key} onChange={e => setDingtalkForm(f => ({ ...f, app_key: e.target.value }))} style={{ fontSize: '12px' }} />
                                                        <input className="input" placeholder="AppSecret" type={showPwds['dt_secret'] ? 'text' : 'password'} value={dingtalkForm.app_secret} onChange={e => setDingtalkForm(f => ({ ...f, app_secret: e.target.value }))} style={{ fontSize: '12px' }} />
                                                    </div>
                                                    <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                                                        <button className="btn btn-primary" style={{ fontSize: '12px', alignSelf: 'flex-start' }} onClick={() => { saveDingtalk.mutate(); setDingtalkEditing(false); }} disabled={!dingtalkForm.app_key || !dingtalkForm.app_secret || saveDingtalk.isPending}>
                                                            {saveDingtalk.isPending ? t('common.loading') : (dingtalkEditing ? 'Save Changes' : t('agent.settings.channel.saveChannel'))}
                                                        </button>
                                                        {dingtalkEditing && <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setDingtalkEditing(false)}>Cancel</button>}
                                                    </div>
                                                </div>
                                            )}
                                        </div>)}
                                    </div>

                                    {/* Atlassian */}
                                    <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', marginBottom: '12px', overflow: 'hidden' }}>
                                        <div onClick={() => setAtlassianOpen(!atlassianOpen)} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', cursor: 'pointer', transition: 'background 0.15s' }} onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-hover)')} onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                                                    <path d="M11.53 1.1a.59.59 0 00-.96.06L5.81 9.08a.59.59 0 00.52.87h3.89l-2.7 4.73a.59.59 0 00.52.87h7.42a.59.59 0 00.52-.87l-5.24-9.17 1.09-1.91a.59.59 0 000-.59L11.53 1.1z" fill="#0052CC" />
                                                    <path d="M12.47 22.9a.59.59 0 00.96-.06l4.76-7.92a.59.59 0 00-.52-.87h-3.89l2.7-4.73a.59.59 0 00-.52-.87H8.54a.59.59 0 00-.52.87l5.24 9.17-1.09 1.91a.59.59 0 000 .59l.3.51z" fill="#2684FF" />
                                                </svg>
                                                <div>
                                                    <div style={{ fontWeight: 600, fontSize: '14px' }}>Atlassian</div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>Jira · Confluence · Compass (Rovo MCP)</div>
                                                </div>
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                                {atlassianConfig && <span className={`badge ${atlassianConfig.is_configured ? 'badge-success' : 'badge-warning'}`} style={{ fontSize: '10px' }}>{atlassianConfig.is_configured ? t('agent.settings.channel.configured') : t('agent.settings.channel.notConfigured')}</span>}
                                                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', transition: 'transform 0.2s', transform: atlassianOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>▼</span>
                                            </div>
                                        </div>

                                        {atlassianOpen && (
                                            <div style={{ padding: '0 16px 16px', borderTop: '1px solid var(--border-subtle)' }}>
                                                <div style={{ paddingTop: '16px' }}>
                                                    {!canManage ? (
                                                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontStyle: 'italic', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
                                                            Only the creator or admin can configure integrations.
                                                        </div>
                                                    ) : atlassianConfig?.is_configured && !atlassianEditing ? (
                                                        <div>
                                                            <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', marginBottom: '12px' }}>
                                                                <div style={{ color: 'var(--text-tertiary)', marginBottom: '4px' }}>Status</div>
                                                                <div style={{ color: 'var(--text-primary)', fontWeight: 500 }}>✅ API Key configured — Jira / Confluence / Compass tools available</div>
                                                                {atlassianConfig.cloud_id && <div style={{ color: 'var(--text-tertiary)', marginTop: '4px', fontSize: '11px' }}>Cloud ID: <code>{atlassianConfig.cloud_id}</code></div>}
                                                            </div>
                                                            {atlassianTestResult && (
                                                                <div style={{ padding: '8px 12px', borderRadius: '6px', fontSize: '12px', marginBottom: '10px', background: atlassianTestResult.ok ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)', border: `1px solid ${atlassianTestResult.ok ? 'rgba(16,185,129,0.25)' : 'rgba(239,68,68,0.25)'}`, color: atlassianTestResult.ok ? 'rgb(5,150,105)' : 'rgb(220,38,38)' }}>
                                                                    {atlassianTestResult.ok
                                                                        ? `✅ ${atlassianTestResult.message || `Connected — ${atlassianTestResult.tool_count} tools available`}`
                                                                        : `❌ ${atlassianTestResult.error}`}
                                                                </div>
                                                            )}
                                                            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                                                                <button className="btn btn-secondary" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={testAtlassian} disabled={atlassianTesting}>
                                                                    {atlassianTesting ? 'Testing...' : '🔌 Test Connection'}
                                                                </button>
                                                                <button className="btn btn-secondary" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => { setAtlassianForm({ api_key: '', cloud_id: atlassianConfig?.cloud_id || '' }); setAtlassianEditing(true); }}>Edit</button>
                                                                <button className="btn btn-danger" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => deleteAtlassian.mutate()}>Disconnect</button>
                                                            </div>
                                                        </div>
                                                    ) : (
                                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                                            <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                                <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                                    <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                                                </summary>
                                                                <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                                    <li>Go to <a href="https://id.atlassian.com/manage-profile/security/api-tokens" target="_blank" rel="noreferrer" style={{ color: 'var(--accent-primary)' }}>id.atlassian.com → Security → API tokens</a></li>
                                                                    <li>Create a new API token and copy it</li>
                                                                    <li>Paste the token below (service account key starts with <code>ATSTT</code>, or base64-encode <code>email:token</code> and prefix with <code>Basic </code>)</li>
                                                                    <li>(Optional) Enter your Cloud ID for multi-site setups</li>
                                                                    <li>Click Connect to enable Jira, Confluence, and Compass tools via Rovo MCP</li>
                                                                </ol>
                                                            </details>
                                                            <div>
                                                                <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>API Key *</label>
                                                                <div style={{ position: 'relative' }}>
                                                                    <input className="input" type={showPwds['atl_key'] ? 'text' : 'password'} value={atlassianForm.api_key} onChange={e => setAtlassianForm({ ...atlassianForm, api_key: e.target.value })} placeholder="ATSTT3x... (service account) or Basic base64(email:token)" style={{ fontSize: '12px', paddingRight: '36px', width: '100%' }} />
                                                                    <button type="button" onClick={() => togglePwd('atl_key')} style={{ position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '2px', display: 'flex', alignItems: 'center' }}>{showPwds['atl_key'] ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19" /><line x1="1" y1="1" x2="23" y2="23" /></svg> : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>}</button>
                                                                </div>
                                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                                                    Service account key starts with <code>ATSTT</code>. Personal API token: base64-encode <code>email:token</code> and prefix with <code>Basic </code>
                                                                </div>
                                                            </div>
                                                            <div>
                                                                <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>Cloud ID <span style={{ fontWeight: 400, color: 'var(--text-tertiary)' }}>(optional)</span></label>
                                                                <input className="input" value={atlassianForm.cloud_id} onChange={e => setAtlassianForm({ ...atlassianForm, cloud_id: e.target.value })} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" style={{ fontSize: '12px' }} />
                                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>Required for multi-site setups. Find it at <code>your-site.atlassian.net/_edge/tenant_info</code></div>
                                                            </div>
                                                            <div style={{ display: 'flex', gap: '8px' }}>
                                                                <button className="btn btn-primary" style={{ fontSize: '12px', alignSelf: 'flex-start' }} onClick={() => saveAtlassian.mutate()} disabled={!atlassianForm.api_key || saveAtlassian.isPending}>
                                                                    {saveAtlassian.isPending ? 'Connecting...' : (atlassianEditing ? 'Save Changes' : 'Connect Atlassian')}
                                                                </button>
                                                                {atlassianEditing && <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => { setAtlassianEditing(false); setAtlassianForm({ api_key: '', cloud_id: '' }); }}>Cancel</button>}
                                                            </div>
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        )}
                                    </div>

                                </div>

                                {/* Danger Zone */}
                                <div className="card" style={{ borderColor: 'var(--error)' }}>
                                    <h4 style={{ color: 'var(--error)', marginBottom: '12px' }}>{t('agent.settings.danger.title')}</h4>
                                    <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                                        {t('agent.settings.danger.deleteWarning')}
                                    </p>
                                    {
                                        !showDeleteConfirm ? (
                                            <button className="btn btn-danger" onClick={() => setShowDeleteConfirm(true)}>× {t('agent.settings.danger.deleteAgent')}</button>
                                        ) : (
                                            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                                <span style={{ fontSize: '13px', color: 'var(--error)', fontWeight: 600 }}>{t('agent.settings.danger.deleteWarning')}</span>
                                                <button className="btn btn-danger" onClick={async () => {
                                                    try {
                                                        await agentApi.delete(id!);
                                                        queryClient.invalidateQueries({ queryKey: ['agents'] });
                                                        navigate('/');
                                                    } catch (err: any) {
                                                        alert(err?.message || 'Failed to delete agent');
                                                    }
                                                }}>{t('agent.settings.danger.confirmDelete')}</button>
                                                <button className="btn btn-secondary" onClick={() => setShowDeleteConfirm(false)}>{t('common.cancel')}</button>
                                            </div>
                                        )
                                    }
                                </div >
                            </div >
                        )
                    })()
                }
            </div >

            <PromptModal
                open={!!promptModal}
                title={promptModal?.title || ''}
                placeholder={promptModal?.placeholder || ''}
                onCancel={() => setPromptModal(null)}
                onConfirm={async (value) => {
                    const action = promptModal?.action;
                    setPromptModal(null);
                    if (action === 'newFolder') {
                        await fileApi.write(id!, `${workspacePath}/${value}/.gitkeep`, '');
                        queryClient.invalidateQueries({ queryKey: ['files', id, workspacePath] });
                    } else if (action === 'newFile') {
                        await fileApi.write(id!, `${workspacePath}/${value}`, '');
                        queryClient.invalidateQueries({ queryKey: ['files', id, workspacePath] });
                        setViewingFile(`${workspacePath}/${value}`);
                        setFileEditing(true);
                        setFileDraft('');
                    } else if (action === 'newSkill') {
                        const template = `---\nname: ${value}\ndescription: Describe what this skill does\n---\n\n# ${value}\n\n## Overview\nDescribe the purpose and when to use this skill.\n\n## Process\n1. Step one\n2. Step two\n\n## Output Format\nDescribe the expected output format.\n`;
                        await fileApi.write(id!, `skills/${value}/SKILL.md`, template);
                        queryClient.invalidateQueries({ queryKey: ['files', id, 'skills'] });
                        setViewingFile(`skills/${value}/SKILL.md`);
                        setFileEditing(true);
                        setFileDraft(template);
                    }
                }}
            />

            <ConfirmModal
                open={!!deleteConfirm}
                title={t('common.delete')}
                message={`${t('common.delete')}: ${deleteConfirm?.name}?`}
                confirmLabel={t('common.delete')}
                danger
                onCancel={() => setDeleteConfirm(null)}
                onConfirm={async () => {
                    const path = deleteConfirm?.path;
                    setDeleteConfirm(null);
                    if (path) {
                        try {
                            await fileApi.delete(id!, path);
                            setViewingFile(null);
                            setFileEditing(false);
                            queryClient.invalidateQueries({ queryKey: ['files', id, workspacePath] });
                            showToast(t('common.delete'));
                        } catch (err: any) {
                            showToast(t('agent.upload.failed'), 'error');
                        }
                    }
                }}
            />

            {
                uploadToast && (
                    <div style={{
                        position: 'fixed', top: '20px', right: '20px', zIndex: 20000,
                        padding: '12px 20px', borderRadius: '8px',
                        background: uploadToast.type === 'success' ? 'rgba(34, 197, 94, 0.9)' : 'rgba(239, 68, 68, 0.9)',
                        color: '#fff', fontSize: '14px', fontWeight: 500,
                        boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
                    }}>
                        {''}{uploadToast.message}
                    </div>
                )
            }

            {/* ── Expiry Editor Modal (admin only) ── */}
            {
                showExpiryModal && (
                    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 9000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                        onClick={() => setShowExpiryModal(false)}>
                        <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)', borderRadius: '12px', padding: '24px', width: '360px', maxWidth: '90vw' }}
                            onClick={e => e.stopPropagation()}>
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
                                <h3 style={{ margin: 0, fontSize: '15px', fontWeight: 600 }}>⏰ {t('agent.settings.expiry.title')}</h3>
                                <button onClick={() => setShowExpiryModal(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: '18px', lineHeight: 1 }}>×</button>
                            </div>
                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                                {(agent as any).is_expired
                                    ? <span style={{ color: 'var(--error)', fontWeight: 600 }}>⏰ {t('agent.settings.expiry.expired')}</span>
                                    : (agent as any).expires_at
                                        ? <>{t('agent.settings.expiry.currentExpiry')} <strong>{new Date((agent as any).expires_at).toLocaleString(i18n.language === 'zh' ? 'zh-CN' : 'en-US')}</strong></>
                                        : <span style={{ color: 'var(--success)' }}>{t('agent.settings.expiry.neverExpires')}</span>
                                }
                            </div>
                            <div style={{ marginBottom: '16px' }}>
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>{t('agent.settings.expiry.quickRenew')}</div>
                                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                                    {([
                                        ['+ 24h', 24],
                                        [`+ ${t('agent.settings.expiry.days', { count: 7 })}`, 168],
                                        [`+ ${t('agent.settings.expiry.days', { count: 30 })}`, 720],
                                        [`+ ${t('agent.settings.expiry.days', { count: 90 })}`, 2160],
                                    ] as [string, number][]).map(([label, h]) => (
                                        <button key={h} onClick={() => addHours(h)}
                                            style={{ padding: '4px 10px', borderRadius: '6px', border: '1px solid var(--border-subtle)', background: 'var(--bg-primary)', cursor: 'pointer', fontSize: '12px', color: 'var(--text-primary)' }}>
                                            {label}
                                        </button>
                                    ))}
                                </div>
                            </div>
                            <div style={{ marginBottom: '20px' }}>
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.settings.expiry.customDeadline')}</div>
                                <input type="datetime-local" value={expiryValue} onChange={e => setExpiryValue(e.target.value)}
                                    style={{ width: '100%', padding: '8px 10px', borderRadius: '8px', border: '1px solid var(--border-subtle)', background: 'var(--bg-primary)', color: 'var(--text-primary)', fontSize: '13px', boxSizing: 'border-box' }} />
                            </div>
                            <div style={{ display: 'flex', gap: '8px', justifyContent: 'space-between', alignItems: 'center' }}>
                                <button onClick={() => saveExpiry(true)} disabled={expirySaving}
                                    style={{ padding: '7px 12px', borderRadius: '8px', border: '1px solid var(--border-subtle)', background: 'none', cursor: 'pointer', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                    🔓 {t('agent.settings.expiry.neverExpires')}
                                </button>
                                <div style={{ display: 'flex', gap: '8px' }}>
                                    <button onClick={() => setShowExpiryModal(false)} disabled={expirySaving}
                                        style={{ padding: '7px 14px', borderRadius: '8px', border: '1px solid var(--border-subtle)', background: 'none', cursor: 'pointer', fontSize: '13px', color: 'var(--text-secondary)' }}>
                                        {t('common.cancel')}
                                    </button>
                                    <button onClick={() => saveExpiry(false)} disabled={expirySaving || !expiryValue}
                                        className="btn btn-primary"
                                        style={{ opacity: !expiryValue ? 0.5 : 1 }}>
                                        {expirySaving ? t('agent.settings.expiry.saving') : t('common.save')}
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                )
            }

        </>
    );
}

// Error boundary to catch unhandled React errors and prevent white screen
class AgentDetailErrorBoundary extends Component<{ children: React.ReactNode }, { hasError: boolean; error: Error | null }> {
    constructor(props: { children: React.ReactNode }) {
        super(props);
        this.state = { hasError: false, error: null };
    }
    static getDerivedStateFromError(error: Error) {
        return { hasError: true, error };
    }
    componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error('AgentDetail crash caught by error boundary:', error, errorInfo);
    }
    render() {
        if (this.state.hasError) {
            return (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '60vh', gap: '16px' }}>
                    <div style={{ fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)' }}>Something went wrong</div>
                    <div style={{ fontSize: '13px', color: 'var(--text-tertiary)', maxWidth: '400px', textAlign: 'center' }}>
                        {this.state.error?.message || 'An unexpected error occurred while loading this page.'}
                    </div>
                    <button
                        className="btn btn-primary"
                        onClick={() => { this.setState({ hasError: false, error: null }); window.location.reload(); }}
                        style={{ marginTop: '8px' }}
                    >
                        Reload Page
                    </button>
                </div>
            );
        }
        return this.props.children;
    }
}

// Wrap the AgentDetail component with error boundary
export default function AgentDetailWithErrorBoundary() {
    return (
        <AgentDetailErrorBoundary>
            <AgentDetailInner />
        </AgentDetailErrorBoundary>
    );
}
