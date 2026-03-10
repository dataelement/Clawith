import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { agentApi, taskApi, fileApi, channelApi, enterpriseApi, activityApi, scheduleApi, skillApi, triggerApi, uploadFileWithProgress } from '../services/api';
import MarkdownRenderer from '../components/MarkdownRenderer';
import { useAuthStore } from '../stores';
import PromptModal from '../components/PromptModal';
import ConfirmModal from '../components/ConfirmModal';
import FileBrowser from '../components/FileBrowser';
import type { FileBrowserApi } from '../components/FileBrowser';

const TABS = ['status', 'pulse', 'mind', 'tools', 'skills', 'relationships', 'workspace', 'chat', 'activityLog', 'settings'] as const;

const getCategoryLabels = (t: any): Record<string, string> => ({
    file: t('agent.toolCategories.file'),
    task: t('agent.toolCategories.task'),
    communication: t('agent.toolCategories.communication'),
    search: t('agent.toolCategories.search'),
    custom: t('agent.toolCategories.custom'),
    general: t('agent.toolCategories.general'),
});

function ToolsManager({ agentId }: { agentId: string }) {
    const { t } = useTranslation();
    const [tools, setTools] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [configTool, setConfigTool] = useState<any | null>(null);
    const [configData, setConfigData] = useState<Record<string, any>>({});
    const [configJson, setConfigJson] = useState('');
    const [configSaving, setConfigSaving] = useState(false);
    const [toolTab, setToolTab] = useState<'platform' | 'installed'>('platform');

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
                                    {hasConfig && (
                                        <button
                                            onClick={() => openConfig(tool)}
                                            style={{ background: 'none', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }}
                                            title="Configure per-agent settings"
                                        >⚙️ Config</button>
                                    )}
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
                                {configTool.config_schema.fields.map((field: any) => (
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
                                            <input type="password" className="form-input" value={configData[field.key] ?? ''} placeholder={field.placeholder || 'Leave blank to use global default'} onChange={e => setConfigData(p => ({ ...p, [field.key]: e.target.value }))} />
                                        ) : field.type === 'select' ? (
                                            <select className="form-input" value={configData[field.key] ?? field.default ?? ''} onChange={e => setConfigData(p => ({ ...p, [field.key]: e.target.value }))}>
                                                {(field.options || []).map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
                                            </select>
                                        ) : (
                                            <input type="text" className="form-input" value={configData[field.key] ?? ''} placeholder={field.placeholder || 'Leave blank to use global default'} onChange={e => setConfigData(p => ({ ...p, [field.key]: e.target.value }))} />
                                        )}
                                    </div>
                                ))}
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

    return (
        <div>
            {/* ── Human Relationships ── */}
            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '12px' }}>{t('agent.detail.humanRelationships')}</h4>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>{t('agent.detail.humanRelationships')}</p>
                {relationships.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '16px' }}>
                        {relationships.map((r: any) => (
                            <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
                                <div style={{ width: '36px', height: '36px', borderRadius: '50%', background: 'rgba(224,238,238,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px', fontWeight: 600, flexShrink: 0 }}>{r.member?.name?.[0] || '?'}</div>
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ fontWeight: 600, fontSize: '13px' }}>{r.member?.name || '?'} <span className="badge" style={{ fontSize: '10px', marginLeft: '4px' }}>{r.relation_label}</span></div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{r.member?.title || ''} · {r.member?.department_path || ''}</div>
                                    {r.description && <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>{r.description}</div>}
                                </div>
                                {!readOnly && <button className="btn btn-ghost" style={{ color: 'var(--error)', fontSize: '12px', flexShrink: 0 }} onClick={() => removeRelationship(r.id)}>{t('common.delete')}</button>}
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
                            <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px', borderRadius: '8px', border: '1px solid rgba(16,185,129,0.3)', background: 'rgba(16,185,129,0.05)' }}>
                                <div style={{ width: '36px', height: '36px', borderRadius: '50%', background: 'rgba(16,185,129,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px', flexShrink: 0 }}>A</div>
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ fontWeight: 600, fontSize: '13px' }}>{r.target_agent?.name || '?'} <span className="badge" style={{ fontSize: '10px', marginLeft: '4px', background: 'rgba(16,185,129,0.15)', color: 'rgb(16,185,129)' }}>{r.relation_label}</span></div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{r.target_agent?.role_description || 'Agent'}</div>
                                    {r.description && <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>{r.description}</div>}
                                </div>
                                {!readOnly && <button className="btn btn-ghost" style={{ color: 'var(--error)', fontSize: '12px', flexShrink: 0 }} onClick={() => removeAgentRelationship(r.id)}>{t('common.delete')}</button>}
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

export default function AgentDetail() {
    const { t, i18n } = useTranslation();
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [activeTab, setActiveTab] = useState<string>('status');

    const { data: agent, isLoading } = useQuery({
        queryKey: ['agent', id],
        queryFn: () => agentApi.get(id!),
        enabled: !!id,
    });

    // ── Pulse tab data: triggers ──
    const { data: pulseTriggers = [], refetch: refetchTriggers } = useQuery({
        queryKey: ['triggers', id],
        queryFn: () => triggerApi.list(id!),
        enabled: !!id && activeTab === 'pulse',
        refetchInterval: activeTab === 'pulse' ? 5000 : false,
    });

    // ── Pulse tab data: agenda.md ──
    const { data: agendaFile } = useQuery({
        queryKey: ['file', id, 'agenda.md'],
        queryFn: () => fileApi.read(id!, 'agenda.md').catch(() => null),
        enabled: !!id && activeTab === 'pulse',
    });

    // ── Pulse tab data: task_history.md ──
    const { data: taskHistoryFile } = useQuery({
        queryKey: ['file', id, 'task_history.md'],
        queryFn: () => fileApi.read(id!, 'task_history.md').catch(() => null),
        enabled: !!id && activeTab === 'pulse',
    });

    // ── Pulse tab state ──
    const [pulseSection, setPulseSection] = useState<'agenda' | 'triggers' | 'monologue' | 'history'>('agenda');

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
    interface ChatMsg { role: 'user' | 'assistant' | 'tool_call'; content: string; fileName?: string; toolName?: string; toolArgs?: any; toolStatus?: 'running' | 'done'; toolResult?: string; thinking?: string; imageUrl?: string; }
    const [chatMessages, setChatMessages] = useState<ChatMsg[]>([]);
    const [chatInput, setChatInput] = useState('');
    const [wsConnected, setWsConnected] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [uploadProgress, setUploadProgress] = useState(-1);
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
            });
            settingsInitRef.current = true;
        }
    }, [agent]);

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


    // Reset chat state whenever the viewed agent changes
    useEffect(() => {
        setActiveSession(null);
        setChatMessages([]);
        setHistoryMsgs([]);
        setChatScope('mine');
        setAgentExpired(false);
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
                    return;
                }
                if (!cancelled) { setWsConnected(false); setTimeout(connect, 2000); }
            };
            ws.onerror = () => { if (!cancelled) setWsConnected(false); };
            ws.onmessage = (e) => {
                const d = JSON.parse(e.data);
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
                        if (last && last.role === 'assistant' && (last as any)._streaming) return [...prev.slice(0, -1), { role: 'assistant', content: d.content, thinking }];
                        return [...prev, { role: d.role, content: d.content }];
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
        setChatMessages(prev => [...prev, { role: 'user', content: userMsg, fileName: attachedFile?.name, imageUrl: attachedFile?.imageUrl }]);
        wsRef.current.send(JSON.stringify({ content: contentForLLM, display_content: userMsg, file_name: attachedFile?.name || '' }));
        setChatInput(''); setAttachedFile(null);
    };

    const handleChatFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0]; if (!file) return;
        setUploading(true); setUploadProgress(0);
        try {
            const data = await uploadFileWithProgress(
                `/chat/upload`,
                file,
                (pct) => setUploadProgress(pct),
                id ? { agent_id: id } : undefined,
            );
            setAttachedFile({ name: data.filename, text: data.extracted_text, path: data.workspace_path, imageUrl: data.image_data_url || undefined });
        } catch (err) { alert(t('agent.upload.failed')); } finally { setUploading(false); setUploadProgress(-1); if (fileInputRef.current) fileInputRef.current.value = ''; }
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
                    const data = await uploadFileWithProgress(
                        `/chat/upload`,
                        file,
                        (pct) => setUploadProgress(pct),
                        id ? { agent_id: id } : undefined,
                    );
                    setAttachedFile({ name: data.filename, text: data.extracted_text, path: data.workspace_path, imageUrl: data.image_data_url || undefined });
                } catch (err) { alert(t('agent.upload.failed')); } finally { setUploading(false); setUploadProgress(-1); }
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
        queryFn: () => agentApi.metrics(id!),
        enabled: !!id && activeTab === 'status',
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
    const [channelForm, setChannelForm] = useState({ app_id: '', app_secret: '', encrypt_key: '' });
    const [feishuEditing, setFeishuEditing] = useState(false);

    const saveChannel = useMutation({
        mutationFn: () => channelApi.create(id!, {
            channel_type: 'feishu', app_id: channelForm.app_id,
            app_secret: channelForm.app_secret, encrypt_key: channelForm.encrypt_key || undefined,
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
                                    <span style={{ background: 'var(--error)', color: '#fff', padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600 }}>⏰ Expired</span>
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
                        {agent.status === 'stopped' ? (
                            <button className="btn btn-secondary" onClick={async () => { await agentApi.start(id!); queryClient.invalidateQueries({ queryKey: ['agent', id] }); }}>{t('agent.actions.start')}</button>
                        ) : agent.status === 'running' ? (
                            <button className="btn btn-secondary" onClick={async () => { await agentApi.stop(id!); queryClient.invalidateQueries({ queryKey: ['agent', id] }); }}>{t('agent.actions.stop')}</button>
                        ) : null}
                    </div>
                </div>

                {/* Tabs */}
                <div className="tabs">
                    {TABS.filter(tab => {
                        // 'use' access: hide only settings tab
                        if ((agent as any)?.access_level === 'use') {
                            return tab !== 'settings';
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
                                    <div style={{ fontSize: '22px', fontWeight: 600 }}>{agent.tokens_used_today.toLocaleString()}</div>
                                    {agent.max_tokens_per_day && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{t('agent.settings.noLimit')} {agent.max_tokens_per_day.toLocaleString()}</div>}
                                </div>
                                <div className="card">
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.settings.month')} Token</div>
                                    <div style={{ fontSize: '22px', fontWeight: 600 }}>{agent.tokens_used_month.toLocaleString()}</div>
                                    {agent.max_tokens_per_month && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{t('agent.settings.noLimit')} {agent.max_tokens_per_month.toLocaleString()}</div>}
                                </div>
                                <div className="card">
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>LLM Calls Today</div>
                                    <div style={{ fontSize: '22px', fontWeight: 600 }}>{((agent as any).llm_calls_today || 0).toLocaleString()}</div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>Max: {((agent as any).max_llm_calls_per_day || 100).toLocaleString()}</div>
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
                                        <div className="card">
                                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>24h Ops</div>
                                            <div style={{ fontSize: '22px', fontWeight: 600 }}>{metrics.activity?.actions_last_24h || 0}</div>
                                        </div>
                                    </>
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
                                            <span style={{ color: 'var(--text-tertiary)' }}>Created</span>
                                            <span>{agent.created_at ? formatDate(agent.created_at) : '—'}</span>
                                        </div>
                                        {(agent as any).creator_username && (
                                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                                <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.fields.createdBy', 'Created by')}</span>
                                                <span style={{ color: 'var(--text-secondary)' }}>@{(agent as any).creator_username}</span>
                                            </div>
                                        )}
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>Last Active</span>
                                            <span>{agent.last_active_at ? formatDate(agent.last_active_at) : '—'}</span>
                                        </div>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>🌐 Timezone</span>
                                            <span>{(agent as any).timezone || t('agent.settings.heartbeat.timezoneDefault', '↩ Company default')}</span>
                                        </div>
                                    </div>
                                </div>
                                <div className="card">
                                    <h3 style={{ fontSize: '14px', fontWeight: 600, marginBottom: '12px' }}>🤖 Model Config</h3>
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
                                <button className="btn btn-secondary" onClick={() => setActiveTab('chat')}>💬 {t('agent.actions.chat')}</button>
                                <button className="btn btn-secondary" onClick={() => setActiveTab('pulse')}>⚡ Pulse</button>
                                <button className="btn btn-secondary" onClick={() => setActiveTab('settings')}>⚙️ {t('agent.tabs.settings')}</button>
                            </div>
                        </div>
                    );
                })()}

                {/* ── Pulse Tab ── */}
                {activeTab === 'pulse' && (
                    <div>
                        {/* Sub-navigation */}
                        <div style={{ display: 'flex', gap: '2px', marginBottom: '16px', background: 'var(--bg-secondary)', borderRadius: '8px', padding: '3px' }}>
                            {(['agenda', 'triggers', 'monologue', 'history'] as const).map(sec => (
                                <button key={sec} onClick={() => setPulseSection(sec)} style={{
                                    flex: 1, padding: '8px 12px', borderRadius: '6px', border: 'none',
                                    background: pulseSection === sec ? 'var(--accent-primary)' : 'transparent',
                                    color: pulseSection === sec ? '#fff' : 'var(--text-secondary)',
                                    fontSize: '13px', fontWeight: 600, cursor: 'pointer', transition: 'all .2s',
                                }}>
                                    {sec === 'agenda' ? `📋 ${t('agent.pulse.agenda')}` : sec === 'triggers' ? `⚡ ${t('agent.pulse.triggers')}` : sec === 'monologue' ? `🤖 ${t('agent.pulse.monologue')}` : `📜 ${t('agent.pulse.history')}`}
                                </button>
                            ))}
                        </div>

                        {/* AGENDA section */}
                        {pulseSection === 'agenda' && (() => {
                            const raw = agendaFile?.content || '';
                            const lines = raw.split('\n');
                            const items = lines.filter((l: string) => /^\s*-\s*\[/.test(l)).map((l: string, i: number) => {
                                const done = /\[x\]/i.test(l);
                                const inProgress = /\[\//.test(l);
                                const text = l.replace(/^\s*-\s*\[.\]\s*/, '').trim();
                                return { id: i, text, done, inProgress };
                            });
                            // Active triggers as fallback agenda items
                            const activeTriggers = pulseTriggers.filter((trig: any) => trig.is_enabled);
                            const hasAgenda = items.length > 0;
                            const hasActiveTriggers = activeTriggers.length > 0;
                            return (
                                <div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                        <h4 style={{ margin: 0 }}>📋 {t('agent.pulse.agenda')}</h4>
                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.pulse.agendaDesc')}</span>
                                    </div>
                                    {/* Agenda items from agenda.md */}
                                    {hasAgenda && (
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginBottom: hasActiveTriggers ? '16px' : '0' }}>
                                            {items.map((it: any) => (
                                                <div key={it.id} className="card" style={{
                                                    padding: '10px 14px', display: 'flex', alignItems: 'center', gap: '10px',
                                                    opacity: it.done ? 0.5 : 1,
                                                    borderLeft: it.inProgress ? '3px solid var(--accent-primary)' : it.done ? '3px solid var(--success)' : '3px solid var(--border-subtle)',
                                                }}>
                                                    <span style={{ fontSize: '16px' }}>{it.done ? '✅' : it.inProgress ? '🔄' : '⬜'}</span>
                                                    <span style={{ fontSize: '13px', textDecoration: it.done ? 'line-through' : 'none' }}>{it.text}</span>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                    {/* Active triggers shown as scheduled items */}
                                    {hasActiveTriggers && (
                                        <div>
                                            {hasAgenda && (
                                                <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                    <span>⏰</span> {t('agent.pulse.scheduledTriggers', 'Scheduled Triggers')}
                                                </div>
                                            )}
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                                {activeTriggers.map((trig: any) => (
                                                    <div key={trig.id} className="card" style={{
                                                        padding: '10px 14px', display: 'flex', alignItems: 'center', gap: '10px',
                                                        borderLeft: `3px solid ${trig.type === 'cron' ? '#7c3aed' : trig.type === 'once' ? '#059669' : trig.type === 'interval' ? '#0284c7' : trig.type === 'poll' ? '#ea580c' : '#db2777'}`,
                                                    }}>
                                                        <span style={{ fontSize: '16px' }}>⏰</span>
                                                        <div style={{ flex: 1 }}>
                                                            <div style={{ fontSize: '13px', fontWeight: 500 }}>{trig.name}</div>
                                                            {trig.reason && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{trig.reason}</div>}
                                                        </div>
                                                        <span style={{
                                                            fontSize: '10px', padding: '2px 6px', borderRadius: '4px',
                                                            background: trig.type === 'cron' ? '#ede9fe' : trig.type === 'once' ? '#d1fae5' : trig.type === 'interval' ? '#dbeafe' : trig.type === 'poll' ? '#ffedd5' : '#fce7f3',
                                                            color: trig.type === 'cron' ? '#7c3aed' : trig.type === 'once' ? '#059669' : trig.type === 'interval' ? '#0284c7' : trig.type === 'poll' ? '#ea580c' : '#db2777',
                                                            fontWeight: 600,
                                                        }}>{trig.type}</span>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                    {/* Empty state only when BOTH are empty */}
                                    {!hasAgenda && !hasActiveTriggers && (
                                        <div className="card" style={{ padding: '32px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                                            {t('agent.pulse.agendaEmpty')}
                                        </div>
                                    )}
                                    {/* Raw markdown */}
                                    {raw && (
                                        <details style={{ marginTop: '12px' }}>
                                            <summary style={{ fontSize: '11px', color: 'var(--text-tertiary)', cursor: 'pointer' }}>{t('agent.pulse.viewRawMarkdown')}</summary>
                                            <pre style={{ fontSize: '11px', marginTop: '8px', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px', whiteSpace: 'pre-wrap', maxHeight: '300px', overflow: 'auto' }}>{raw}</pre>
                                        </details>
                                    )}
                                </div>
                            );
                        })()}


                        {/* TRIGGERS section */}
                        {pulseSection === 'triggers' && (
                            <div>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                    <h4 style={{ margin: 0 }}>⚡ {t('agent.pulse.triggers')}</h4>
                                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.pulse.triggersDesc')}</span>
                                </div>
                                {pulseTriggers.length === 0 ? (
                                    <div className="card" style={{ padding: '32px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                                        {t('agent.pulse.triggersEmpty')}
                                    </div>
                                ) : (
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                        {pulseTriggers.map((trig: any) => (
                                            <div key={trig.id} className="card" style={{
                                                padding: '12px 16px',
                                                opacity: trig.is_enabled ? 1 : 0.5,
                                                borderLeft: `3px solid ${trig.is_enabled ? (trig.type === 'cron' ? '#7c3aed' : trig.type === 'once' ? '#059669' : trig.type === 'interval' ? '#0284c7' : trig.type === 'poll' ? '#ea580c' : '#db2777') : '#999'}`,
                                            }}>
                                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                                                    <span style={{ fontWeight: 600, fontSize: '13px' }}>{trig.name}</span>
                                                    <span style={{
                                                        fontSize: '10px', padding: '2px 6px', borderRadius: '4px',
                                                        background: trig.type === 'cron' ? '#ede9fe' : trig.type === 'once' ? '#d1fae5' : trig.type === 'interval' ? '#dbeafe' : trig.type === 'poll' ? '#ffedd5' : '#fce7f3',
                                                        color: trig.type === 'cron' ? '#7c3aed' : trig.type === 'once' ? '#059669' : trig.type === 'interval' ? '#0284c7' : trig.type === 'poll' ? '#ea580c' : '#db2777',
                                                        fontWeight: 600,
                                                    }}>{trig.type}</span>
                                                    {!trig.is_enabled && <span style={{ fontSize: '10px', color: '#999' }}>⏸ {t('agent.pulse.disabled')}</span>}
                                                    <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.pulse.fired', { count: trig.fire_count })}</span>
                                                </div>
                                                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>{trig.reason}</div>
                                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '6px' }}>
                                                    <span style={{ fontSize: '10px', color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
                                                        {JSON.stringify(trig.config).substring(0, 80)}
                                                    </span>
                                                    <div style={{ display: 'flex', gap: '4px' }}>
                                                        <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px' }}
                                                            onClick={async () => {
                                                                await triggerApi.update(id!, trig.id, { is_enabled: !trig.is_enabled });
                                                                refetchTriggers();
                                                            }}>
                                                            {trig.is_enabled ? `⏸ ${t('agent.pulse.disable')}` : `▶️ ${t('agent.pulse.enable')}`}
                                                        </button>
                                                        <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px', color: 'var(--error)' }}
                                                            onClick={async () => {
                                                                if (confirm(t('agent.pulse.deleteTriggerConfirm', { name: trig.name }))) {
                                                                    await triggerApi.delete(id!, trig.id);
                                                                    refetchTriggers();
                                                                }
                                                            }}>
                                                            🗑
                                                        </button>
                                                    </div>
                                                </div>
                                            </div>
                                        ))}

                                    </div>
                                )}
                            </div>
                        )}

                        {/* INNER MONOLOGUE section */}
                        {pulseSection === 'monologue' && (() => {
                            // Filter activity logs for trigger-related entries
                            const triggerLogs = activityLogs.filter((log: any) =>
                                log.action_type === 'trigger_fired' || log.action_type === 'trigger_created' ||
                                log.action_type === 'trigger_updated' || log.action_type === 'trigger_cancelled' ||
                                log.summary?.includes('内心独白') || log.summary?.includes('trigger')
                            );
                            return (
                                <div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                        <h4 style={{ margin: 0 }}>🤖 {t('agent.pulse.monologue')}</h4>
                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.pulse.monologueDesc')}</span>
                                    </div>
                                    {triggerLogs.length === 0 ? (
                                        <div className="card" style={{ padding: '32px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                                            {t('agent.pulse.monologueEmpty')}
                                        </div>
                                    ) : (
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                            {triggerLogs.slice(0, 30).map((log: any) => (
                                                <div key={log.id} className="card" style={{ padding: '10px 14px' }}>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                                                        <span style={{
                                                            fontSize: '10px', padding: '2px 6px', borderRadius: '4px',
                                                            background: log.action_type === 'trigger_fired' ? '#fce7f3' : '#e0f2fe',
                                                            color: log.action_type === 'trigger_fired' ? '#db2777' : '#0284c7',
                                                            fontWeight: 600,
                                                        }}>{log.action_type}</span>
                                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                            {new Date(log.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                                                        </span>
                                                    </div>
                                                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>{log.summary}</div>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            );
                        })()}

                        {/* TASK HISTORY section */}
                        {pulseSection === 'history' && (() => {
                            const histRaw = taskHistoryFile?.content || '';
                            return (
                                <div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                        <h4 style={{ margin: 0 }}>📜 {t('agent.pulse.historyTitle')}</h4>
                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.pulse.historyDesc')}</span>
                                    </div>
                                    {!histRaw ? (
                                        <div className="card" style={{ padding: '32px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                                            {t('agent.pulse.historyEmpty')}
                                        </div>
                                    ) : (
                                        <div className="card" style={{ padding: '16px' }}>
                                            <MarkdownRenderer content={histRaw} />
                                        </div>
                                    )}
                                </div>
                            );
                        })()}
                    </div>
                )}


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
                            <ToolsManager agentId={id!} />
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
                                                                } as Record<string, string>)[s.source_channel] && (
                                                                    <span style={{ fontSize: '9px', padding: '1px 4px', borderRadius: '3px', background: 'var(--bg-tertiary)', color: 'var(--text-tertiary)', flexShrink: 0 }}>
                                                                        {({
                                                                            feishu: t('common.channels.feishu'),
                                                                            discord: t('common.channels.discord'),
                                                                            slack: t('common.channels.slack'),
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
                                                    let parsed: any = {}; try { parsed = typeof m.content === 'string' ? JSON.parse(m.content) : m.content; } catch { parsed = { name: 'tool', result: m.content }; }
                                                    return (
                                                        <div key={i} style={{ display: 'flex', gap: '8px', marginBottom: '6px', paddingLeft: '36px' }}>
                                                            <details style={{ flex: 1, borderRadius: '8px', background: 'var(--accent-subtle)', border: '1px solid var(--accent-subtle)', fontSize: '12px' }}>
                                                                <summary style={{ padding: '6px 10px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', userSelect: 'none', listStyle: 'none' }}>
                                                                    <span style={{ fontWeight: 600, color: 'var(--accent-text)' }}>{parsed.name || 'tool'}</span>
                                                                    <span style={{ color: 'var(--text-tertiary)', fontSize: '11px', marginLeft: 'auto' }}>done</span>
                                                                </summary>
                                                                {parsed.result && <div style={{ padding: '4px 10px 8px', color: 'var(--text-secondary)', fontSize: '11px', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '160px', overflow: 'auto' }}>{parsed.result}</div>}
                                                            </details>
                                                        </div>
                                                    );
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
                                                        <div key={i} style={{ display: 'flex', gap: '8px', marginBottom: '6px', paddingLeft: '36px' }}>
                                                            <details style={{ flex: 1, borderRadius: '8px', background: 'var(--accent-subtle)', border: '1px solid var(--accent-subtle)', fontSize: '12px' }}>
                                                                <summary style={{ padding: '6px 10px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', userSelect: 'none', listStyle: 'none' }}>
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
                                                            {msg.role === 'assistant' ? <MarkdownRenderer content={msg.content} /> : msg.content ? <div style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div> : null}
                                                        </div>
                                                    </div>
                                                );
                                            })}
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
                                            <button className="btn btn-secondary" onClick={() => fileInputRef.current?.click()} disabled={!wsConnected || uploading} style={{ padding: '6px 10px', fontSize: '14px', minWidth: 'auto' }}>{uploading ? '⏳' : '⦹'}</button>
                                            {uploading && uploadProgress >= 0 && (
                                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flex: '0 0 120px' }}>
                                                    <div style={{ flex: 1, height: '4px', borderRadius: '2px', background: 'var(--bg-tertiary)', overflow: 'hidden' }}>
                                                        <div style={{ height: '100%', borderRadius: '2px', background: 'var(--accent-primary)', width: `${uploadProgress}%`, transition: 'width 0.15s ease' }} />
                                                    </div>
                                                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{uploadProgress}%</span>
                                                </div>
                                            )}
                                            <input ref={chatInputRef} className="chat-input" value={chatInput} onChange={e => setChatInput(e.target.value)}
                                                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMsg(); } }}
                                                onPaste={handlePaste}
                                                placeholder={!wsConnected && (!activeSession?.user_id || !currentUser || activeSession.user_id === String(currentUser?.id)) ? 'Connecting...' : attachedFile ? t('agent.chat.askAboutFile', { name: attachedFile.name }) : t('chat.placeholder')}
                                                disabled={!wsConnected} style={{ flex: 1 }} autoFocus />
                                            <button className="btn btn-primary" onClick={sendChatMsg} disabled={!wsConnected || (!chatInput.trim() && !attachedFile)} style={{ padding: '6px 16px' }}>{t('chat.send')}</button>
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
                                    {filterBtn('backend', '⚙️ ' + t('agent.activityLog.backendServices', 'Backend Services'))}
                                    {(logFilter === 'backend' || logFilter === 'heartbeat' || logFilter === 'schedule' || logFilter === 'messages') && (
                                        <>
                                            <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>│</span>
                                            {filterBtn('heartbeat', '💓 Heartbeat', true)}
                                            {filterBtn('schedule', '⏰ Schedule/Cron', true)}
                                            {filterBtn('messages', '📨 Messages', true)}
                                        </>
                                    )}
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
                            String(settingsForm.max_tokens_per_month) !== String(agent?.max_tokens_per_month || '')
                        );

                        const handleSaveSettings = async () => {
                            setSettingsSaving(true);
                            setSettingsError('');
                            try {
                                await agentApi.update(id!, {
                                    primary_model_id: settingsForm.primary_model_id || null,
                                    fallback_model_id: settingsForm.fallback_model_id || null,
                                    context_window_size: settingsForm.context_window_size,
                                    max_tool_rounds: settingsForm.max_tool_rounds,
                                    max_tokens_per_day: settingsForm.max_tokens_per_day ? Number(settingsForm.max_tokens_per_day) : null,
                                    max_tokens_per_month: settingsForm.max_tokens_per_month ? Number(settingsForm.max_tokens_per_month) : null,
                                } as any);
                                queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                settingsInitRef.current = false;
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
                                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
                                    <h3 style={{ margin: 0 }}>{t('agent.settings.title')}</h3>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                        {settingsSaved && <span style={{ fontSize: '12px', color: 'var(--success)' }}>✅ {t('agent.settings.saved', 'Saved')}</span>}
                                        {settingsError && <span style={{ fontSize: '12px', color: 'var(--error)' }}>❌ {settingsError}</span>}
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

                                {/* Model Selection */}
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

                                {/* Context Window */}
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
                                                {t('agent.settings.today')}: {(agent?.tokens_used_today || 0).toLocaleString()}
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
                                                {t('agent.settings.month')}: {(agent?.tokens_used_month || 0).toLocaleString()}
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                {/* Autonomy Policy */}
                                <div className="card" style={{ marginBottom: '12px' }}>
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
                                </div>

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

                                        {/* Timezone */}
                                        <div style={{
                                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                            padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px',
                                            border: '1px solid var(--border-subtle)',
                                        }}>
                                            <div>
                                                <div style={{ fontWeight: 500, fontSize: '13px' }}>🌐 {t('agent.settings.heartbeat.timezone', 'Timezone')}</div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                    {agent?.timezone
                                                        ? t('agent.settings.heartbeat.timezoneOverride', 'Agent-level override')
                                                        : t('agent.settings.heartbeat.timezoneInherited', 'Inherited from company')}
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
                                                <option value="">{t('agent.settings.heartbeat.timezoneDefault', '↩ Company default')}</option>
                                                {['UTC', 'Asia/Shanghai', 'Asia/Tokyo', 'Asia/Seoul', 'Asia/Singapore', 'Asia/Kolkata', 'Asia/Dubai',
                                                    'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Moscow',
                                                    'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
                                                    'America/Sao_Paulo', 'Australia/Sydney', 'Pacific/Auckland'].map(tz => (
                                                        <option key={tz} value={tz}>{tz}</option>
                                                    ))}
                                            </select>
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
                                    <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '16px', marginBottom: '12px' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M6.194 14.644a2.194 2.194 0 110 4.388 2.194 2.194 0 010-4.388zm-2.194 0H0v-2.194a2.194 2.194 0 014.388 0v2.194zm16.612 0a2.194 2.194 0 110 4.388 2.194 2.194 0 010-4.388zm0-2.194a2.194 2.194 0 010-4.388 2.194 2.194 0 010 4.388zm0 0v2.194h2.194A2.194 2.194 0 0024 12.45a2.194 2.194 0 00-2.194-2.194h-1.194zm-16.612 0a2.194 2.194 0 010-4.388 2.194 2.194 0 010 4.388zm0 0v2.194H2A2.194 2.194 0 010 12.45a2.194 2.194 0 012.194-2.194h1.806z" fill="#611F69" opacity=".4" /><path d="M9.388 4.388a2.194 2.194 0 110-4.388 2.194 2.194 0 010 4.388zm0 2.194v-2.194H7.194A2.194 2.194 0 005 6.582a2.194 2.194 0 002.194 2.194h2.194zm0 12.612a2.194 2.194 0 110 4.388 2.194 2.194 0 010-4.388zm0-2.194v2.194H7.194A2.194 2.194 0 005 17.418a2.194 2.194 0 002.194 2.194h.194zm4.224-12.612a2.194 2.194 0 110-4.388 2.194 2.194 0 010 4.388zm2.194 0H13.612V2.194a2.194 2.194 0 014.388 0v2.194zm-2.194 14.806a2.194 2.194 0 110 4.388 2.194 2.194 0 010-4.388zm-2.194 0h2.194v2.194a2.194 2.194 0 01-4.388 0v-2.194z" fill="#611F69" /></svg>
                                                <div>
                                                    <div style={{ fontWeight: 600, fontSize: '14px' }}>Slack</div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>Slack Bot</div>
                                                </div>
                                            </div>
                                            {slackConfig && <span className={`badge ${slackConfig.is_configured ? 'badge-success' : 'badge-warning'}`}>{slackConfig.is_configured ? t('agent.settings.channel.configured') : t('agent.settings.channel.notConfigured')}</span>}
                                        </div>
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
                                                    <button className="btn btn-primary" style={{ fontSize: '12px', alignSelf: 'flex-start' }} onClick={() => { saveSlack.mutate(); setSlackEditing(false); }} disabled={!slackForm.bot_token || !slackForm.signing_secret || saveSlack.isPending}>
                                                        {saveSlack.isPending ? t('common.loading') : (slackEditing ? 'Save Changes' : t('agent.settings.channel.saveChannel'))}
                                                    </button>
                                                    {slackEditing && <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setSlackEditing(false)}>Cancel</button>}
                                                </div>
                                            </div>
                                        )}
                                    </div>

                                    {/* Discord */}
                                    <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '16px', marginBottom: '12px' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <svg width="20" height="20" viewBox="0 0 24 24" fill="#5865F2"><path d="M20.317 4.37a19.791 19.791 0 00-4.885-1.515.074.074 0 00-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 00-5.487 0 12.64 12.64 0 00-.617-1.25.077.077 0 00-.079-.037A19.736 19.736 0 003.677 4.37a.07.07 0 00-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 00.031.057 19.9 19.9 0 005.993 3.03.078.078 0 00.084-.028 14.09 14.09 0 001.226-1.994.076.076 0 00-.041-.106 13.107 13.107 0 01-1.872-.892.077.077 0 01-.008-.128 10.2 10.2 0 00.372-.292.074.074 0 01.077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 01.078.01c.12.098.246.198.373.292a.077.077 0 01-.006.127 12.299 12.299 0 01-1.873.892.077.077 0 00-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 00.084.028 19.839 19.839 0 006.002-3.03.077.077 0 00.032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 00-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z" /></svg>
                                                <div>
                                                    <div style={{ fontWeight: 600, fontSize: '14px' }}>Discord</div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>Slash Commands (/ask)</div>
                                                </div>
                                            </div>
                                            {discordConfig && <span className={`badge ${discordConfig.is_configured ? 'badge-success' : 'badge-warning'}`}>{discordConfig.is_configured ? t('agent.settings.channel.configured') : t('agent.settings.channel.notConfigured')}</span>}
                                        </div>
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
                                                    <button className="btn btn-primary" style={{ fontSize: '12px', alignSelf: 'flex-start' }} onClick={() => { saveDiscord.mutate(); setDiscordEditing(false); }} disabled={!discordForm.application_id || !discordForm.bot_token || !discordForm.public_key || saveDiscord.isPending}>
                                                        {saveDiscord.isPending ? t('common.loading') : (discordEditing ? 'Save Changes' : t('agent.settings.channel.saveChannel'))}
                                                    </button>
                                                    {discordEditing && <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setDiscordEditing(false)}>Cancel</button>}
                                                </div>
                                            </div>
                                        )}
                                    </div>

                                    {/* Feishu */}
                                    <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '16px', marginBottom: '12px' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-tertiary)' }}>Feishu</span>
                                                <div>
                                                    <div style={{ fontWeight: 600, fontSize: '14px' }}>{t('agent.settings.channel.feishu')}</div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>Feishu / Lark</div>
                                                </div>
                                            </div>
                                            {channelConfig && (
                                                <span className={`badge ${channelConfig.is_configured ? 'badge-success' : 'badge-warning'}`}>
                                                    {channelConfig.is_configured ? t('agent.settings.channel.configured') : t('agent.settings.channel.notConfigured')}
                                                </span>
                                            )}
                                        </div>

                                        {!canManage ? (
                                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontStyle: 'italic', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
                                                Only the creator or admin can configure communication channels.
                                            </div>
                                        ) : channelConfig && !feishuEditing ? (
                                            <div>
                                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>App ID: <code>{channelConfig.app_id}</code></div>
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
                                                <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                    <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                        <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                                    </summary>
                                                    <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                        <li>{t('channelGuide.feishu.step1')}</li>
                                                        <li>{t('channelGuide.feishu.step2')}</li>
                                                        <li>{t('channelGuide.feishu.step3')}</li>
                                                        <li>{t('channelGuide.feishu.step4')}</li>
                                                        <li>{t('channelGuide.feishu.step5')}</li>
                                                        <li>{t('channelGuide.feishu.step6')}</li>
                                                        <li>{t('channelGuide.feishu.step7')}</li>
                                                        <li>{t('channelGuide.feishu.step8')}</li>
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
                                                    <button className="btn btn-secondary" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => { setChannelForm({ app_id: channelConfig.app_id || '', app_secret: channelConfig.app_secret || '', encrypt_key: channelConfig.encrypt_key || '' }); setFeishuEditing(true); }}>Edit</button>
                                                    <button className="btn btn-danger" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={async () => { await channelApi.delete(id!); queryClient.invalidateQueries({ queryKey: ['channel', id] }); }}>Disconnect</button>
                                                </div>
                                            </div>
                                        ) : (
                                            <div>
                                                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '12px' }}>
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
                                                    <div>
                                                        <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>Encrypt Key</label>
                                                        <div style={{ position: 'relative' }}>
                                                            <input className="input" type={showPwds['feishu_encrypt'] ? 'text' : 'password'} value={channelForm.encrypt_key} onChange={e => setChannelForm({ ...channelForm, encrypt_key: e.target.value })} style={{ fontSize: '12px', paddingRight: '36px', width: '100%' }} />
                                                            <button type="button" onClick={() => togglePwd('feishu_encrypt')} style={{ position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '2px', display: 'flex', alignItems: 'center' }}>{showPwds['feishu_encrypt'] ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19" /><line x1="1" y1="1" x2="23" y2="23" /></svg> : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>}</button>
                                                        </div>
                                                    </div>
                                                </div>
                                                <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                    <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                        <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                                    </summary>
                                                    <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                        <li>{t('channelGuide.feishu.step1')}</li>
                                                        <li>{t('channelGuide.feishu.step2')}</li>
                                                        <li>{t('channelGuide.feishu.step3')}</li>
                                                        <li>{t('channelGuide.feishu.step4')}</li>
                                                        <li>{t('channelGuide.feishu.step5')}</li>
                                                        <li>{t('channelGuide.feishu.step6')}</li>
                                                        <li>{t('channelGuide.feishu.step7')}</li>
                                                        <li>{t('channelGuide.feishu.step8')}</li>
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
                                                    <button className="btn btn-primary" style={{ fontSize: '12px' }} onClick={() => { saveChannel.mutate(); setFeishuEditing(false); }} disabled={!channelForm.app_id || !channelForm.app_secret || saveChannel.isPending}>
                                                        {saveChannel.isPending ? t('common.loading') : (feishuEditing ? 'Save Changes' : t('agent.settings.channel.saveChannel'))}
                                                    </button>
                                                    {feishuEditing && <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setFeishuEditing(false)}>Cancel</button>}
                                                </div>
                                            </div>
                                        )}
                                    </div>

                                    {/* DingTalk — coming soon */}
                                    <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '16px', marginBottom: '12px', opacity: 0.6 }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                            <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-tertiary)' }}>Web</span>
                                            <div>
                                                <div style={{ fontWeight: 600, fontSize: '14px' }}>DingTalk <span className="badge" style={{ fontSize: '10px', marginLeft: '6px' }}>Coming soon</span></div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>DingTalk</div>
                                            </div>
                                        </div>
                                    </div>

                                    {/* WeCom — coming soon */}
                                    <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '16px', opacity: 0.6 }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                            <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-tertiary)' }}>API</span>
                                            <div>
                                                <div style={{ fontWeight: 600, fontSize: '14px' }}>WeCom <span className="badge" style={{ fontSize: '10px', marginLeft: '6px' }}>Coming soon</span></div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>WeCom / WeChat Work</div>
                                            </div>
                                        </div>
                                    </div>
                                </div >

                                {/* Danger Zone */}
                                < div className="card" style={{ borderColor: 'var(--error)' }
                                }>
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
