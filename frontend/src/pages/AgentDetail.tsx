import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { agentApi, taskApi, fileApi, channelApi, enterpriseApi, activityApi, scheduleApi, skillApi } from '../services/api';
import { useAuthStore } from '../stores';
import PromptModal from '../components/PromptModal';
import ConfirmModal from '../components/ConfirmModal';
import FileBrowser from '../components/FileBrowser';
import type { FileBrowserApi } from '../components/FileBrowser';

const TABS = ['status', 'tasks', 'mind', 'tools', 'skills', 'relationships', 'workspace', 'chat', 'activityLog', 'settings'] as const;

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

    const loadTools = async () => {
        try {
            const token = localStorage.getItem('token');
            const res = await fetch(`/api/tools/agents/${agentId}`, {
                headers: { Authorization: `Bearer ${token}` },
            });
            if (res.ok) setTools(await res.json());
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

    if (loading) return <div style={{ color: 'var(--text-tertiary)', padding: '20px' }}>{t('common.loading')}</div>;

    // Group by category
    const grouped = tools.reduce((acc: Record<string, any[]>, t) => {
        const cat = t.category || 'general';
        (acc[cat] = acc[cat] || []).push(t);
        return acc;
    }, {});

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {Object.entries(grouped).map(([category, catTools]) => (
                <div key={category}>
                    <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                        {getCategoryLabels(t)[category] || category}
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        {(catTools as any[]).map((tool: any) => (
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
                                        </div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {tool.description}
                                            {tool.mcp_server_name && <span> · {tool.mcp_server_name}</span>}
                                        </div>
                                    </div>
                                </div>
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
                        ))}
                    </div>
                </div>
            ))
            }
            {
                tools.length === 0 && (
                    <div className="card" style={{ textAlign: 'center', padding: '30px', color: 'var(--text-tertiary)' }}>
                        {t('common.noData')}
                    </div>
                )
            }
        </div >
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
    const { t } = useTranslation();
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [activeTab, setActiveTab] = useState<string>('status');

    const { data: agent, isLoading } = useQuery({
        queryKey: ['agent', id],
        queryFn: () => agentApi.get(id!),
        enabled: !!id,
    });

    const { data: tasks = [] } = useQuery({
        queryKey: ['tasks', id],
        queryFn: () => taskApi.list(id!),
        enabled: !!id && activeTab === 'tasks',
        refetchInterval: activeTab === 'tasks' ? 3000 : false,
    });

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
    const { data: conversations = [] } = useQuery({
        queryKey: ['chat-conversations', id],
        queryFn: () => fetchAuth<any[]>(`/agents/${id}/chat-history/conversations`),
        enabled: !!id && activeTab === 'chat',
    });
    const [selectedConv, setSelectedConv] = useState<string | null>('me');
    const { data: convMessages = [] } = useQuery({
        queryKey: ['chat-messages', id, selectedConv],
        queryFn: () => fetchAuth<any[]>(`/agents/${id}/chat-history/${selectedConv}`),
        enabled: !!id && !!selectedConv && selectedConv !== 'me',
    });

    // Websocket chat state (for 'me' conversation)
    const token = useAuthStore((s) => s.token);
    interface ChatMsg { role: 'user' | 'assistant' | 'tool_call'; content: string; fileName?: string; toolName?: string; toolArgs?: any; toolStatus?: 'running' | 'done'; toolResult?: string; }
    const [chatMessages, setChatMessages] = useState<ChatMsg[]>([]);
    const [chatInput, setChatInput] = useState('');
    const [wsConnected, setWsConnected] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [attachedFile, setAttachedFile] = useState<{ name: string; text: string; path?: string } | null>(null);
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
    useEffect(() => {
        if (!id || !token || activeTab !== 'chat') return;
        fetch(`/api/chat/${id}/history`, { headers: { Authorization: `Bearer ${token}` } })
            .then(r => r.json())
            .then((h: ChatMsg[]) => { if (h.length > 0) setChatMessages(h); })
            .catch(() => { });
    }, [id, token, activeTab]);

    useEffect(() => {
        if (!id || !token || activeTab !== 'chat') return;
        let cancelled = false;
        const connect = () => {
            if (cancelled) return;
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat/${id}?token=${token}`);
            ws.onopen = () => { if (cancelled) { ws.close(); return; } setWsConnected(true); wsRef.current = ws; };
            ws.onclose = () => { if (!cancelled) { setWsConnected(false); setTimeout(connect, 2000); } };
            ws.onerror = () => { if (!cancelled) setWsConnected(false); };
            ws.onmessage = (e) => {
                const d = JSON.parse(e.data);
                if (d.type === 'tool_call') {
                    // Tool call notification from backend
                    setChatMessages(prev => {
                        const toolMsg: ChatMsg = {
                            role: 'tool_call',
                            content: '',
                            toolName: d.name,
                            toolArgs: d.args,
                            toolStatus: d.status,
                            toolResult: d.result,
                        };
                        // If updating an existing running tool_call for the same tool, replace it
                        if (d.status === 'done') {
                            const lastIdx = prev.length - 1;
                            const last = prev[lastIdx];
                            if (last && last.role === 'tool_call' && last.toolName === d.name && last.toolStatus === 'running') {
                                return [...prev.slice(0, lastIdx), toolMsg];
                            }
                        }
                        return [...prev, toolMsg];
                    });
                } else if (d.type === 'chunk') {
                    // Streaming chunk — append to last message or create new one
                    setChatMessages(prev => {
                        const last = prev[prev.length - 1];
                        if (last && last.role === 'assistant' && (last as any)._streaming) {
                            return [...prev.slice(0, -1), { role: 'assistant', content: last.content + d.content, _streaming: true } as any];
                        }
                        return [...prev, { role: 'assistant', content: d.content, _streaming: true } as any];
                    });
                } else if (d.type === 'done') {
                    // Streaming done — replace with final content
                    setChatMessages(prev => {
                        const last = prev[prev.length - 1];
                        if (last && last.role === 'assistant' && (last as any)._streaming) {
                            return [...prev.slice(0, -1), { role: 'assistant', content: d.content }];
                        }
                        return [...prev, { role: d.role, content: d.content }];
                    });
                } else if (d.type === 'error' || d.type === 'quota_exceeded') {
                    // Quota / expiry / system error — show as styled system message
                    setChatMessages(prev => [...prev, { role: 'assistant', content: `⚠️ ${d.content || d.detail || d.message || 'Request denied'}` }]);
                } else {
                    // Legacy non-streaming message
                    setChatMessages(prev => [...prev, { role: d.role, content: d.content }]);
                }
            };
        };
        connect();
        return () => { cancelled = true; wsRef.current?.close(); wsRef.current = null; setWsConnected(false); };
    }, [id, token, activeTab]);

    // Smart scroll: only auto-scroll if user is at the bottom
    const isNearBottom = useRef(true);
    const isFirstLoad = useRef(true);
    const [showScrollBtn, setShowScrollBtn] = useState(false);
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

    const sendChatMsg = () => {
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
        if (!chatInput.trim() && !attachedFile) return;
        let userMsg = chatInput.trim();
        let contentForLLM = userMsg;
        if (attachedFile) {
            const wsPath = attachedFile.path || '';
            const codePath = wsPath.replace(/^workspace\//, '');
            const fileLoc = wsPath ? `\nFile location: ${wsPath} (for read_file/read_document tools)\nIn execute_code, use relative path: "${codePath}" (working directory is workspace/)` : '';
            const fc = `[File: ${attachedFile.name}]${fileLoc}\n\n${attachedFile.text}`;
            contentForLLM = userMsg ? `${fc}\n\nQuestion: ${userMsg}` : `Please analyze this file:\n\n${fc}`;
            userMsg = userMsg || `⌇ ${attachedFile.name}`;
        }
        setChatMessages(prev => [...prev, { role: 'user', content: userMsg, fileName: attachedFile?.name }]);
        wsRef.current.send(JSON.stringify({ content: contentForLLM }));
        setChatInput(''); setAttachedFile(null);
    };

    const handleChatFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0]; if (!file) return;
        setUploading(true);
        try {
            const fd = new FormData(); fd.append('file', file); if (id) fd.append('agent_id', id);
            const resp = await fetch('/api/chat/upload', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: fd });
            if (!resp.ok) { const err = await resp.json(); alert(err.detail || t('agent.upload.failed')); return; }
            const data = await resp.json(); setAttachedFile({ name: data.filename, text: data.extracted_text, path: data.workspace_path });
        } catch (err) { alert(t('agent.upload.failed')); } finally { setUploading(false); if (fileInputRef.current) fileInputRef.current.value = ''; }
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
        mutationFn: (sid: string) => scheduleApi.trigger(id!, sid),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['schedules', id] }),
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
        enabled: activeTab === 'settings' || activeTab === 'status',
    });

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

    // ─── Channel config ──────────────────────────────────
    const [channelForm, setChannelForm] = useState({ app_id: '', app_secret: '', encrypt_key: '' });

    const saveChannel = useMutation({
        mutationFn: () => channelApi.create(id!, {
            channel_type: 'feishu', app_id: channelForm.app_id,
            app_secret: channelForm.app_secret, encrypt_key: channelForm.encrypt_key || undefined,
        }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['channel', id] }),
    });

    // ─── File viewer ─────────────────────────────────────
    const [viewingFile, setViewingFile] = useState<string | null>(null);
    const [fileEditing, setFileEditing] = useState(false);
    const [fileDraft, setFileDraft] = useState('');
    const [promptModal, setPromptModal] = useState<{ title: string; placeholder: string; action: string } | null>(null);
    const [deleteConfirm, setDeleteConfirm] = useState<{ path: string; name: string; isDir: boolean } | null>(null);
    const [uploadToast, setUploadToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
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

    return (
        <>
            <div>
                {/* Header */}
                <div className="page-header">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                        <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'var(--accent-subtle)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '24px' }}>A</div>
                        <div>
                            <h1 className="page-title">{agent.name}</h1>
                            <p className="page-subtitle" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <span className={`status-dot ${statusKey}`} />
                                {t(`agent.status.${statusKey}`)}
                                {agent.role_description && <span>· {agent.role_description}</span>}
                                {(agent as any).is_expired && (
                                    <span style={{ background: 'var(--error)', color: '#fff', padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600 }}>⏰ Expired</span>
                                )}
                                {!(agent as any).is_expired && (agent as any).expires_at && (
                                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                        Expires: {new Date((agent as any).expires_at).toLocaleString()}
                                    </span>
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
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.fields.role')}</span>
                                            <span>{agent.role_description || '—'}</span>
                                        </div>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>Created</span>
                                            <span>{agent.created_at ? formatDate(agent.created_at) : '—'}</span>
                                        </div>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>Last Active</span>
                                            <span>{agent.last_active_at ? formatDate(agent.last_active_at) : '—'}</span>
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
                                <button className="btn btn-secondary" onClick={() => setActiveTab('tasks')}>📋 {t('agent.tabs.tasks')}</button>
                                <button className="btn btn-secondary" onClick={() => setActiveTab('settings')}>⚙️ {t('agent.tabs.settings')}</button>
                            </div>
                        </div>
                    );
                })()}

                {/* ── Tasks Tab ── */}
                {activeTab === 'tasks' && (
                    <div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginBottom: '12px' }}>
                            <button className="btn btn-secondary" onClick={() => { setShowScheduleForm(!showScheduleForm); setShowTaskForm(false); }} style={{ fontSize: '13px' }}>{t('agent.tasks.addSchedule')}</button>
                            <button className="btn btn-primary" onClick={() => { setShowTaskForm(!showTaskForm); setShowScheduleForm(false); }}>+ {t('agent.tasks.newTask')}</button>
                        </div>
                        {showTaskForm && (
                            <div className="card" style={{ marginBottom: '16px' }}>
                                <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
                                    <button className={`btn ${taskForm.type === 'todo' ? 'btn-primary' : 'btn-secondary'}`} style={{ fontSize: '12px' }} onClick={() => setTaskForm({ ...taskForm, type: 'todo' })}>
                                        📋 {t('agent.tasks.typeTask', 'Task')}
                                    </button>
                                    <button className={`btn ${taskForm.type === 'supervision' ? 'btn-primary' : 'btn-secondary'}`} style={{ fontSize: '12px' }} onClick={() => setTaskForm({ ...taskForm, type: 'supervision', remind_schedule: taskForm.remind_schedule || JSON.stringify({ freq: 'daily', interval: 1, time: '09:00', weekdays: [1, 2, 3, 4, 5] }) })}>
                                        📣 {t('agent.tasks.typeSupervision', 'Supervision')}
                                    </button>
                                </div>
                                <div className="form-group">
                                    <input className="form-input" placeholder={taskForm.type === 'supervision' ? t('agent.tasks.supervisionTitle', 'What to follow up on...') : t("agent.tasks.taskTitle")} value={taskForm.title} onChange={e => setTaskForm({ ...taskForm, title: e.target.value })} />
                                </div>
                                <div className="form-group">
                                    <textarea className="form-textarea" placeholder={t("agent.tasks.taskDesc")} rows={2} value={taskForm.description} onChange={e => setTaskForm({ ...taskForm, description: e.target.value })} />
                                </div>
                                {taskForm.type === 'supervision' && (() => {
                                    const defaults = { freq: 'daily', interval: 1, time: '09:00', weekdays: [1, 2, 3, 4, 5] };
                                    const sched = (() => {
                                        try { const p = JSON.parse(taskForm.remind_schedule || '{}'); return p.freq ? p : defaults; } catch { return defaults; }
                                    })();
                                    const updateSched = (patch: any) => {
                                        const next = { ...defaults, ...sched, ...patch };
                                        setTaskForm(f => ({ ...f, remind_schedule: JSON.stringify(next) }));
                                    };
                                    const freq = sched.freq || 'daily';
                                    const interval = sched.interval || 1;
                                    const time = sched.time || '09:00';
                                    const weekdays: number[] = sched.weekdays || [1, 2, 3, 4, 5];
                                    const DAY_LABELS = [t('agent.tasks.sun', '日'), t('agent.tasks.mon', '一'), t('agent.tasks.tue', '二'), t('agent.tasks.wed', '三'), t('agent.tasks.thu', '四'), t('agent.tasks.fri', '五'), t('agent.tasks.sat', '六')];

                                    return (
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '12px', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '8px' }}>
                                            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                                                📣 {t('agent.tasks.supervisionConfig', 'Supervision Settings')}
                                            </div>
                                            <input className="form-input" placeholder={t('agent.tasks.targetPerson', 'Target person name')} value={taskForm.supervision_target_name} onChange={e => setTaskForm({ ...taskForm, supervision_target_name: e.target.value })} />

                                            {/* Frequency row */}
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                                <span style={{ fontSize: '12px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{t('agent.tasks.repeatFreq', '重复频率')}:</span>
                                                <span style={{ fontSize: '12px' }}>{t('agent.tasks.every', '每')}</span>
                                                <select className="form-input" value={interval} onChange={e => updateSched({ interval: Number(e.target.value) })} style={{ width: '56px', fontSize: '12px', padding: '4px 6px' }}>
                                                    {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}</option>)}
                                                </select>
                                                <select className="form-input" value={freq} onChange={e => updateSched({ freq: e.target.value })} style={{ width: '64px', fontSize: '12px', padding: '4px 6px' }}>
                                                    <option value="daily">{t('agent.tasks.day', '天')}</option>
                                                    <option value="weekly">{t('agent.tasks.week', '周')}</option>
                                                </select>
                                            </div>

                                            {/* Weekday selector — only for weekly */}
                                            {freq === 'weekly' && (
                                                <div style={{ display: 'flex', gap: '4px' }}>
                                                    {DAY_LABELS.map((label, idx) => {
                                                        const active = weekdays.includes(idx);
                                                        return (
                                                            <button key={idx}
                                                                onClick={() => {
                                                                    const next = active ? weekdays.filter((d: number) => d !== idx) : [...weekdays, idx].sort();
                                                                    if (next.length > 0) updateSched({ weekdays: next });
                                                                }}
                                                                style={{
                                                                    width: '32px', height: '32px', borderRadius: '50%',
                                                                    border: active ? '2px solid var(--accent-primary)' : '2px solid #999',
                                                                    fontSize: '12px', fontWeight: 600, cursor: 'pointer',
                                                                    background: active ? 'var(--accent-primary)' : '#fff',
                                                                    color: active ? '#fff' : '#333',
                                                                    lineHeight: '28px', textAlign: 'center' as const, padding: 0,
                                                                }}>
                                                                {label}
                                                            </button>
                                                        );
                                                    })}
                                                </div>
                                            )}

                                            {/* Time picker */}
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <span style={{ fontSize: '12px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{t('agent.tasks.remindTime', '提醒时间')}:</span>
                                                <input type="time" className="form-input" value={time} onChange={e => updateSched({ time: e.target.value })} style={{ width: '110px', fontSize: '12px', padding: '4px 6px' }} />
                                            </div>

                                            {/* Deadline */}
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <span style={{ fontSize: '12px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{t('agent.tasks.deadline', '截止时间')}:</span>
                                                <label style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer' }}>
                                                    <input type="radio" name="sv-deadline" checked={!taskForm.due_date} onChange={() => setTaskForm(f => ({ ...f, due_date: '' }))} />
                                                    {t('agent.tasks.noDeadline', '永不截止')}
                                                </label>
                                                <label style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer' }}>
                                                    <input type="radio" name="sv-deadline" checked={!!taskForm.due_date} onChange={() => setTaskForm(f => ({ ...f, due_date: new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10) }))} />
                                                    {t('agent.tasks.setDeadline', '设置截止')}
                                                </label>
                                                {taskForm.due_date && (
                                                    <input type="date" className="form-input" value={taskForm.due_date} onChange={e => setTaskForm(f => ({ ...f, due_date: e.target.value }))} style={{ width: '150px', fontSize: '12px', padding: '4px 6px' }} />
                                                )}
                                            </div>
                                        </div>
                                    );
                                })()}
                                <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                                    <button className="btn btn-secondary" onClick={() => setShowTaskForm(false)}>{t('common.cancel')}</button>
                                    <button className="btn btn-primary" onClick={() => createTask.mutate(taskForm)} disabled={!taskForm.title || (taskForm.type === 'supervision' && !taskForm.supervision_target_name)}>{t('layout.create')}</button>
                                </div>
                            </div>
                        )}
                        {showScheduleForm && (() => {
                            const sDefs = { freq: 'daily', interval: 1, time: '09:00', weekdays: [1, 2, 3, 4, 5] };
                            const sSched = (() => { try { const p = JSON.parse(schedForm.schedule || '{}'); return p.freq ? p : sDefs; } catch { return sDefs; } })();
                            const sUpdate = (patch: any) => { const next = { ...sDefs, ...sSched, ...patch }; setSchedForm(f => ({ ...f, schedule: JSON.stringify(next) })); };
                            const sFreq = sSched.freq || 'daily';
                            const sInterval = sSched.interval || 1;
                            const sTime = sSched.time || '09:00';
                            const sWeekdays: number[] = sSched.weekdays || [1, 2, 3, 4, 5];
                            const DAY_LABELS_S = [t('agent.tasks.sun', '日'), t('agent.tasks.mon', '一'), t('agent.tasks.tue', '二'), t('agent.tasks.wed', '三'), t('agent.tasks.thu', '四'), t('agent.tasks.fri', '五'), t('agent.tasks.sat', '六')];
                            return (
                                <div className="card" style={{ padding: '16px', marginBottom: '16px' }}>
                                    <h4 style={{ marginBottom: '12px', fontSize: '14px' }}>{t('agent.tasks.addSchedule')}</h4>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                                        <input className="input" placeholder={t("agent.tasks.taskTitle")} value={schedForm.name}
                                            onChange={e => setSchedForm(p => ({ ...p, name: e.target.value }))} />
                                        <textarea className="input" placeholder={t("agent.tasks.taskDesc")} rows={3} value={schedForm.instruction}
                                            onChange={e => setSchedForm(p => ({ ...p, instruction: e.target.value }))} style={{ resize: 'vertical' }} />

                                        {/* Rich schedule picker */}
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '8px' }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                                <span style={{ fontSize: '12px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{t('agent.tasks.repeatFreq', '重复频率')}:</span>
                                                <span style={{ fontSize: '12px' }}>{t('agent.tasks.every', '每')}</span>
                                                <select className="form-input" value={sInterval} onChange={e => sUpdate({ interval: Number(e.target.value) })} style={{ width: '56px', fontSize: '12px', padding: '4px 6px' }}>
                                                    {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}</option>)}
                                                </select>
                                                <select className="form-input" value={sFreq} onChange={e => sUpdate({ freq: e.target.value })} style={{ width: '64px', fontSize: '12px', padding: '4px 6px' }}>
                                                    <option value="daily">{t('agent.tasks.day', '天')}</option>
                                                    <option value="weekly">{t('agent.tasks.week', '周')}</option>
                                                </select>
                                            </div>
                                            {sFreq === 'weekly' && (
                                                <div style={{ display: 'flex', gap: '4px' }}>
                                                    {DAY_LABELS_S.map((label, idx) => {
                                                        const active = sWeekdays.includes(idx);
                                                        return (
                                                            <button key={idx} onClick={() => { const next = active ? sWeekdays.filter((d: number) => d !== idx) : [...sWeekdays, idx].sort(); if (next.length > 0) sUpdate({ weekdays: next }); }}
                                                                style={{ width: '32px', height: '32px', borderRadius: '50%', border: active ? '2px solid var(--accent-primary)' : '2px solid #999', fontSize: '12px', fontWeight: 600, cursor: 'pointer', background: active ? 'var(--accent-primary)' : '#fff', color: active ? '#fff' : '#333', lineHeight: '28px', textAlign: 'center' as const, padding: 0 }}>
                                                                {label}
                                                            </button>
                                                        );
                                                    })}
                                                </div>
                                            )}
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <span style={{ fontSize: '12px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{t('agent.tasks.remindTime', '执行时间')}:</span>
                                                <input type="time" className="form-input" value={sTime} onChange={e => sUpdate({ time: e.target.value })} style={{ width: '110px', fontSize: '12px', padding: '4px 6px' }} />
                                            </div>
                                        </div>

                                        {/* Deadline */}
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                            <span style={{ fontSize: '12px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{t('agent.tasks.deadline', '截止时间')}:</span>
                                            <label style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer' }}>
                                                <input type="radio" name="sched-deadline" checked={!schedForm.due_date} onChange={() => setSchedForm(f => ({ ...f, due_date: '' }))} />
                                                {t('agent.tasks.noDeadline', '永不截止')}
                                            </label>
                                            <label style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer' }}>
                                                <input type="radio" name="sched-deadline" checked={!!schedForm.due_date} onChange={() => setSchedForm(f => ({ ...f, due_date: new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10) }))} />
                                                {t('agent.tasks.setDeadline', '设置截止')}
                                            </label>
                                            {schedForm.due_date && (
                                                <input type="date" className="form-input" value={schedForm.due_date} onChange={e => setSchedForm(f => ({ ...f, due_date: e.target.value }))} style={{ width: '150px', fontSize: '12px', padding: '4px 6px' }} />
                                            )}
                                        </div>

                                        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                                            <button className="btn btn-secondary" onClick={() => setShowScheduleForm(false)}>{t('common.cancel')}</button>
                                            <button className="btn btn-primary" disabled={!schedForm.name || !schedForm.instruction}
                                                onClick={() => createScheduleMut.mutate()}>{t('agent.tasks.addSchedule')}</button>
                                        </div>
                                    </div>
                                </div>
                            );
                        })()}
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px' }}>
                            {['pending', 'doing', 'supervision', 'done'].map((col) => {
                                const colTasks = tasks.filter(t => col === 'supervision' ? t.type === 'supervision' : (t.type !== 'supervision' && t.status === col));
                                const pendingCount = col === 'pending' ? colTasks.length + schedules.length : colTasks.length;
                                return (
                                    <div key={col} style={{ background: 'var(--bg-secondary)', borderRadius: '8px', padding: '10px', minHeight: '200px' }}>
                                        <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: '10px', padding: '4px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                                            {col === 'pending' ? t('agent.tasks.todo') : col === 'doing' ? t('agent.tasks.doing') : col === 'supervision' ? t('agent.tasks.supervision') : t('agent.tasks.done')}
                                            <span style={{ marginLeft: '6px', background: 'var(--bg-elevated)', borderRadius: '8px', padding: '1px 6px', fontSize: '10px' }}>
                                                {pendingCount}
                                            </span>
                                        </div>


                                        {col === 'pending' && (
                                            <>
                                                {/* Tasks */}
                                                {colTasks.length > 0 && (
                                                    <div style={{ marginBottom: '12px' }}>
                                                        <div style={{ fontSize: '10px', fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: '6px', padding: '2px 0' }}>Tasks</div>
                                                        {colTasks.map(task => (
                                                            <div key={task.id} className="card" style={{ marginBottom: '6px', padding: '10px', cursor: 'pointer', border: selectedTaskId === task.id ? '1px solid var(--accent)' : '1px solid transparent' }} onClick={() => setSelectedTaskId(selectedTaskId === task.id ? null : task.id)}>
                                                                <div style={{ fontSize: '12px', fontWeight: 500 }}>{task.title}</div>
                                                                <div style={{ display: 'flex', gap: '4px', marginTop: '6px' }}>
                                                                    <span className={`badge badge-${task.priority === 'high' || task.priority === 'urgent' ? 'error' : 'info'}`} style={{ fontSize: '10px' }}>
                                                                        {task.priority}
                                                                    </span>
                                                                </div>
                                                                {selectedTaskId === task.id && taskLogs.length > 0 && (
                                                                    <div style={{ marginTop: '8px', borderTop: '1px solid var(--border-subtle)', paddingTop: '8px' }}>
                                                                        {taskLogs.map(log => (
                                                                            <div key={log.id} style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
                                                                                <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '2px' }}>
                                                                                    {new Date(log.created_at).toLocaleTimeString('zh-CN')}
                                                                                </div>
                                                                                {log.content}
                                                                            </div>
                                                                        ))}
                                                                    </div>
                                                                )}
                                                                {selectedTaskId === task.id && taskLogs.length === 0 && (
                                                                    <div style={{ marginTop: '8px', fontSize: '11px', color: 'var(--text-tertiary)' }}>No logs</div>
                                                                )}
                                                            </div>
                                                        ))}
                                                    </div>
                                                )}


                                                <div>
                                                    <div style={{ fontSize: '10px', fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: '6px', padding: '2px 0' }}>Scheduled</div>
                                                    {schedules.length > 0 ? schedules.map((s: any) => {
                                                        const formatDt = (iso: string) => !iso ? '-' : new Date(iso).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
                                                        return (
                                                            <div key={s.id} className="card" style={{ marginBottom: '6px', padding: '10px', borderLeft: `3px solid ${s.is_enabled ? 'var(--accent)' : 'var(--text-tertiary)'}` }}>
                                                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
                                                                    <span style={{ fontSize: '12px' }}>{s.is_enabled ? '⏰' : '⏸️'}</span>
                                                                    <span style={{ fontWeight: 500, fontSize: '12px', flex: 1 }}>{s.name}</span>
                                                                </div>
                                                                <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px', lineHeight: 1.3 }}>
                                                                    {s.instruction.length > 50 ? s.instruction.slice(0, 50) + '...' : s.instruction}
                                                                </div>
                                                                <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap' }}>
                                                                    <code style={{ fontSize: '10px', background: 'var(--bg-elevated)', padding: '1px 4px', borderRadius: '3px' }}>{s.cron_expr}</code>
                                                                    <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>Next: {formatDt(s.next_run_at)}</span>
                                                                    {s.run_count > 0 && <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>· {s.run_count}x</span>}
                                                                </div>
                                                                <div style={{ display: 'flex', gap: '4px', marginTop: '6px' }}>
                                                                    <button className="btn btn-ghost" title="Trigger" style={{ fontSize: '11px', padding: '2px 5px' }}
                                                                        onClick={() => triggerScheduleMut.mutate(s.id)}>▶️</button>
                                                                    <button className={`btn ${s.is_enabled ? 'btn-secondary' : 'btn-primary'}`}
                                                                        style={{ fontSize: '10px', padding: '2px 6px' }}
                                                                        onClick={() => toggleScheduleMut.mutate({ sid: s.id, enabled: !s.is_enabled })}>
                                                                        {s.is_enabled ? 'Pause' : 'Resume'}
                                                                    </button>
                                                                    <button className="btn btn-ghost" style={{ fontSize: '10px', padding: '2px 5px', color: 'var(--danger)' }}
                                                                        onClick={() => { if (confirm(`Delete ${s.name}?`)) deleteScheduleMut.mutate(s.id); }}>{t('common.delete')}</button>
                                                                </div>
                                                            </div>
                                                        );
                                                    }) : <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', padding: '8px', textAlign: 'center' }}>No schedules</div>}
                                                </div>
                                            </>
                                        )}


                                        {col !== 'pending' && colTasks.map(task => (
                                            <div key={task.id} className="card" style={{ marginBottom: '6px', padding: '10px', cursor: 'pointer', border: selectedTaskId === task.id ? '1px solid var(--accent)' : '1px solid transparent' }} onClick={() => setSelectedTaskId(selectedTaskId === task.id ? null : task.id)}>
                                                <div style={{ fontSize: '12px', fontWeight: 500 }}>{task.title}</div>
                                                <div style={{ display: 'flex', gap: '4px', marginTop: '6px' }}>
                                                    <span className={`badge badge-${task.priority === 'high' || task.priority === 'urgent' ? 'error' : 'info'}`} style={{ fontSize: '10px' }}>
                                                        {task.priority}
                                                    </span>
                                                    {task.status === 'doing' && <span className="badge" style={{ fontSize: '10px', background: 'var(--warning)' }}>In progress</span>}
                                                    {task.type === 'supervision' && task.supervision_target_name && (
                                                        <span className="badge" style={{ fontSize: '10px', background: '#e8b4f8' }}>→ {task.supervision_target_name}</span>
                                                    )}
                                                    {task.type === 'supervision' && task.remind_schedule && (() => {
                                                        try {
                                                            const s = JSON.parse(task.remind_schedule);
                                                            const dayNames = ['日', '一', '二', '三', '四', '五', '六'];
                                                            let label = s.freq === 'weekly'
                                                                ? `每${s.interval > 1 ? s.interval : ''}周 ${(s.weekdays || []).map((d: number) => dayNames[d]).join('')}`
                                                                : `每${s.interval > 1 ? s.interval : ''}天`;
                                                            return <span className="badge" style={{ fontSize: '10px', background: '#b4d8f8' }}>⏰ {label} {s.time || '09:00'}</span>;
                                                        } catch {
                                                            return <span className="badge" style={{ fontSize: '10px', background: '#b4d8f8' }}>⏰ {task.remind_schedule}</span>;
                                                        }
                                                    })()}
                                                </div>
                                                {selectedTaskId === task.id && taskLogs.length > 0 && (
                                                    <div style={{ marginTop: '8px', borderTop: '1px solid var(--border-subtle)', paddingTop: '8px' }}>
                                                        {taskLogs.map(log => (
                                                            <div key={log.id} style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
                                                                <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '2px' }}>
                                                                    {new Date(log.created_at).toLocaleTimeString('zh-CN')}
                                                                </div>
                                                                {log.content}
                                                            </div>
                                                        ))}
                                                    </div>
                                                )}
                                                {selectedTaskId === task.id && taskLogs.length === 0 && (
                                                    <div style={{ marginTop: '8px', fontSize: '11px', color: 'var(--text-tertiary)' }}>No logs</div>
                                                )}
                                            </div>
                                        ))}
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {/* ── Mind Tab (Soul + Memory + Heartbeat) ── */}
                {activeTab === 'mind' && (() => {
                    const adapter: FileBrowserApi = {
                        list: (p) => fileApi.list(id!, p),
                        read: (p) => fileApi.read(id!, p),
                        write: (p, c) => fileApi.write(id!, p, c),
                        delete: (p) => fileApi.delete(id!, p),
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
                })()}

                {/* ── Tools Tab ── */}
                {activeTab === 'tools' && (
                    <div>
                        <div style={{ marginBottom: '16px' }}>
                            <h3 style={{ marginBottom: '4px' }}>{t('agent.toolMgmt.title')}</h3>
                            <p style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>{t('agent.toolMgmt.description')}</p>
                        </div>
                        <ToolsManager agentId={id!} />
                    </div>
                )}

                {/* ── Skills Tab ── */}
                {activeTab === 'skills' && (() => {
                    const adapter: FileBrowserApi = {
                        list: (p) => fileApi.list(id!, p),
                        read: (p) => fileApi.read(id!, p),
                        write: (p, c) => fileApi.write(id!, p, c),
                        delete: (p) => fileApi.delete(id!, p),
                        upload: (file, path) => fileApi.upload(id!, file, path),
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
                })()}

                {/* ── Relationships Tab ── */}
                {activeTab === 'relationships' && (
                    <RelationshipEditor agentId={id!} readOnly={(agent as any)?.access_level === 'use'} />
                )}

                {/* ── Workspace Tab ── */}
                {activeTab === 'workspace' && (() => {
                    const adapter: FileBrowserApi = {
                        list: (p) => fileApi.list(id!, p),
                        read: (p) => fileApi.read(id!, p),
                        write: (p, c) => fileApi.write(id!, p, c),
                        delete: (p) => fileApi.delete(id!, p),
                        upload: (file, path) => fileApi.upload(id!, file, path + '/'),
                    };
                    return <FileBrowser api={adapter} rootPath="workspace" features={{ upload: true, newFile: true, newFolder: true, edit: true, delete: true, directoryNavigation: true }} />;
                })()}

                {activeTab === 'chat' && (
                    <div>
                        <h3 style={{ marginBottom: '12px' }}>{t('agent.chat.title')}</h3>
                        <div style={{ display: 'flex', gap: '12px', flex: 1, minHeight: 0, height: 'calc(100vh - 280px)' }}>
                            {/* Left: conversation list */}
                            <div style={{ width: '240px', flexShrink: 0, borderRight: '1px solid var(--border-subtle)', paddingRight: '12px', overflowY: 'auto' }}>
                                {/* Pinned: live chat with me */}
                                <div
                                    onClick={() => setSelectedConv('me')}
                                    style={{ padding: '10px', borderRadius: '8px', cursor: 'pointer', marginBottom: '8px', background: selectedConv === 'me' ? 'var(--bg-elevated)' : 'rgba(224,238,238,0.08)', border: selectedConv === 'me' ? '1px solid var(--accent-primary)' : '1px solid rgba(224,238,238,0.2)' }}
                                >
                                    <div style={{ fontWeight: 600, fontSize: '13px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                        <span className={`status-dot ${wsConnected ? 'running' : 'stopped'}`} style={{ width: '6px', height: '6px' }} />
                                        {t('agent.actions.talkToMe')}
                                    </div>
                                    <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{wsConnected ? t('agent.chat.connected') : t('agent.chat.disconnected')} · {chatMessages.length} 条</div>
                                </div>
                                {conversations.length > 0 && <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', padding: '4px 10px', fontWeight: 600 }}>History</div>}
                                {conversations.map((c: any) => (
                                    <div key={c.conv_id}
                                        onClick={() => setSelectedConv(c.conv_id)}
                                        style={{ padding: '8px 10px', borderRadius: '8px', cursor: 'pointer', marginBottom: '2px', background: selectedConv === c.conv_id ? 'var(--bg-elevated)' : 'transparent', border: selectedConv === c.conv_id ? '1px solid var(--accent-primary)' : '1px solid transparent' }}
                                        onMouseEnter={e => { if (selectedConv !== c.conv_id) e.currentTarget.style.background = 'var(--bg-secondary)'; }}
                                        onMouseLeave={e => { if (selectedConv !== c.conv_id) e.currentTarget.style.background = 'transparent'; }}
                                    >
                                        <div style={{ fontWeight: 500, fontSize: '12px', marginBottom: '1px' }}>{c.partner_name}</div>
                                        <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.last_message}</div>
                                        <div style={{ fontSize: '9px', color: 'var(--text-tertiary)', marginTop: '1px' }}>{c.message_count}  · {c.last_at ? new Date(c.last_at).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}</div>
                                    </div>
                                ))}
                            </div>
                            {/* Right: chat area */}
                            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', position: 'relative' }}>
                                {selectedConv === 'me' ? (
                                    /* Live websocket chat */
                                    <>
                                        <div ref={chatContainerRef} onScroll={handleChatScroll} style={{ flex: 1, overflowY: 'auto', padding: '4px' }}>
                                            {chatMessages.length === 0 && (
                                                <div style={{ textAlign: 'center', padding: '60px', color: 'var(--text-tertiary)' }}>
                                                    <div style={{ marginBottom: '8px', color: 'var(--text-tertiary)', fontSize: '13px' }}>{t('agent.chat.startChat')}</div>
                                                    <div style={{ fontSize: '13px' }}>{t('agent.chat.startConversation', { name: agent.name })}</div>
                                                    <div style={{ fontSize: '11px', marginTop: '4px', opacity: 0.7 }}>{t('agent.chat.fileSupport')}</div>
                                                </div>
                                            )}
                                            {chatMessages.map((msg, i) => {
                                                if (msg.role === 'tool_call') {
                                                    return (
                                                        <div key={i} style={{ display: 'flex', gap: '8px', marginBottom: '6px', paddingLeft: '36px' }}>
                                                            <details style={{
                                                                flex: 1, borderRadius: '8px',
                                                                background: 'var(--accent-subtle)',
                                                                border: '1px solid var(--accent-subtle)',
                                                                fontSize: '12px',
                                                            }}>
                                                                <summary style={{
                                                                    padding: '6px 10px', cursor: 'pointer',
                                                                    display: 'flex', alignItems: 'center', gap: '6px',
                                                                    userSelect: 'none', listStyle: 'none',
                                                                }}>
                                                                    <span style={{ fontSize: '13px' }}>{msg.toolStatus === 'running' ? '⏳' : '⚡'}</span>
                                                                    <span style={{ fontWeight: 600, color: 'var(--accent-text)' }}>{msg.toolName}</span>
                                                                    {msg.toolArgs && Object.keys(msg.toolArgs).length > 0 && (
                                                                        <span style={{ color: 'var(--text-tertiary)', fontSize: '11px', fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
                                                                            {`(${Object.entries(msg.toolArgs).map(([k, v]) => `${k}: ${typeof v === 'string' ? v.slice(0, 30) : JSON.stringify(v)}`).join(', ')})`}
                                                                        </span>
                                                                    )}
                                                                    {msg.toolStatus === 'running' && <span style={{ color: 'var(--text-tertiary)', fontSize: '11px', marginLeft: 'auto' }}>{t('common.loading')}</span>}
                                                                </summary>
                                                                {msg.toolResult && (
                                                                    <div style={{ padding: '4px 10px 8px' }}>
                                                                        <div style={{ color: 'var(--text-secondary)', fontSize: '11px', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '120px', overflow: 'auto', background: 'rgba(0,0,0,0.15)', borderRadius: '4px', padding: '4px 6px' }}>
                                                                            {msg.toolResult.slice(0, 500)}
                                                                        </div>
                                                                    </div>
                                                                )}
                                                            </details>
                                                        </div>
                                                    );
                                                }
                                                return (
                                                    <div key={i} style={{ display: 'flex', flexDirection: msg.role === 'assistant' ? 'row' : 'row-reverse', gap: '8px', marginBottom: '8px' }}>
                                                        <div style={{ width: '28px', height: '28px', borderRadius: '50%', background: msg.role === 'assistant' ? 'rgba(224,238,238,0.15)' : 'rgba(16,185,129,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', flexShrink: 0 }}>
                                                            {msg.role === 'user' ? 'U' : 'A'}
                                                        </div>
                                                        <div style={{ maxWidth: '70%', padding: '8px 12px', borderRadius: '12px', background: msg.role === 'assistant' ? 'var(--bg-secondary)' : 'rgba(224,238,238,0.15)', fontSize: '13px', lineHeight: '1.5', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                                                            {msg.fileName && <div style={{ fontSize: '10px', color: 'var(--accent)', marginBottom: '2px' }}>⌇ {msg.fileName}</div>}
                                                            {msg.content}
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                            <div ref={chatEndRef} />
                                        </div>
                                        {showScrollBtn && (
                                            <button
                                                onClick={scrollToBottom}
                                                style={{
                                                    position: 'absolute', bottom: '70px', right: '20px',
                                                    width: '32px', height: '32px', borderRadius: '50%',
                                                    background: 'var(--bg-elevated)', border: '1px solid var(--border-default)',
                                                    color: 'var(--text-secondary)', cursor: 'pointer',
                                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                                    fontSize: '16px', boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
                                                    zIndex: 10,
                                                }}
                                                title="Scroll to bottom"
                                            >↓</button>
                                        )}
                                        {attachedFile && (
                                            <div style={{ padding: '4px 12px', background: 'var(--bg-elevated)', borderTop: '1px solid var(--border-subtle)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '11px' }}>
                                                <span>⌇ {attachedFile.name}</span>
                                                <button onClick={() => setAttachedFile(null)} style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer' }}>✕</button>
                                            </div>
                                        )}
                                        <div style={{ display: 'flex', gap: '8px', padding: '8px 0 0' }}>
                                            <input type="file" ref={fileInputRef} onChange={handleChatFile} style={{ display: 'none' }} />
                                            <button className="btn btn-secondary" onClick={() => fileInputRef.current?.click()} disabled={!wsConnected || uploading} style={{ padding: '6px 10px', fontSize: '14px', minWidth: 'auto' }}>{uploading ? '⏳' : '⌇'}</button>
                                            <input
                                                ref={chatInputRef}
                                                className="chat-input"
                                                value={chatInput}
                                                onChange={e => setChatInput(e.target.value)}
                                                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMsg(); } }}
                                                placeholder={attachedFile ? t('agent.chat.askAboutFile', { name: attachedFile.name }) : t('chat.placeholder')}
                                                disabled={!wsConnected}
                                                style={{ flex: 1 }}
                                                autoFocus
                                            />
                                            <button className="btn btn-primary" onClick={sendChatMsg} disabled={!wsConnected || (!chatInput.trim() && !attachedFile)} style={{ padding: '6px 16px' }}>{t('chat.send')}</button>
                                        </div>
                                    </>
                                ) : !selectedConv ? (
                                    <div style={{ color: 'var(--text-tertiary)', textAlign: 'center', paddingTop: '80px', fontSize: '13px' }}>Select a conversation</div>
                                ) : convMessages.length === 0 ? (
                                    <div style={{ color: 'var(--text-tertiary)', textAlign: 'center', paddingTop: '80px', fontSize: '13px' }}>No messages</div>
                                ) : (
                                    <div style={{ flex: 1, overflowY: 'auto', padding: '4px' }}>
                                        {convMessages.map((m: any) => (
                                            <div key={m.id} style={{ display: 'flex', flexDirection: m.role === 'assistant' ? 'row' : 'row-reverse', gap: '8px', marginBottom: '8px' }}>
                                                <div style={{ width: '28px', height: '28px', borderRadius: '50%', background: m.role === 'assistant' ? 'rgba(224,238,238,0.15)' : 'rgba(16,185,129,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', flexShrink: 0 }}>
                                                    {m.role === 'assistant' ? 'A' : 'U'}
                                                </div>
                                                <div style={{ maxWidth: '70%', padding: '8px 12px', borderRadius: '12px', background: m.role === 'assistant' ? 'var(--bg-secondary)' : 'rgba(224,238,238,0.15)', fontSize: '13px', lineHeight: '1.5', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                                                    {m.sender_name && <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '2px' }}>{m.sender_name}</div>}
                                                    {m.content}
                                                    <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{m.created_at ? new Date(m.created_at).toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''}</div>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                )}

                {activeTab === 'activityLog' && (() => {
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
                })()}

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
                                            <label style={{ position: 'relative', display: 'inline-block', width: '44px', height: '24px', cursor: 'pointer' }}>
                                                <input
                                                    type="checkbox"
                                                    checked={agent?.heartbeat_enabled ?? true}
                                                    onChange={async (e) => {
                                                        await agentApi.update(id!, { heartbeat_enabled: e.target.checked } as any);
                                                        queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                                    }}
                                                    style={{ opacity: 0, width: 0, height: 0 }}
                                                />
                                                <span style={{
                                                    position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
                                                    background: (agent?.heartbeat_enabled ?? true) ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                                                    borderRadius: '12px', transition: 'background 0.2s',
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
                                            <select
                                                className="input"
                                                value={agent?.heartbeat_interval_minutes ?? 30}
                                                onChange={async (e) => {
                                                    await agentApi.update(id!, { heartbeat_interval_minutes: Number(e.target.value) } as any);
                                                    queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                                }}
                                                style={{ width: '140px', fontSize: '12px' }}
                                            >
                                                <option value={15}>15 min</option>
                                                <option value={30}>30 min</option>
                                                <option value={60}>1 hour</option>
                                                <option value={120}>2 hours</option>
                                            </select>
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
                                                value={agent?.heartbeat_active_hours ?? '09:00-18:00'}
                                                onChange={async (e) => {
                                                    await agentApi.update(id!, { heartbeat_active_hours: e.target.value } as any);
                                                    queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                                }}
                                                style={{ width: '140px', fontSize: '12px', textAlign: 'center' }}
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
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>{t('agent.settings.channel.title')}</p>

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
                                                <span className={`badge ${channelConfig.is_connected ? 'badge-success' : 'badge-warning'}`}>
                                                    {channelConfig.is_connected ? t('agent.settings.channel.configured') : t('agent.settings.channel.notConfigured')}
                                                </span>
                                            )}
                                        </div>

                                        {channelConfig ? (
                                            <div>
                                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>App ID: <code>{channelConfig.app_id}</code></div>
                                                <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', fontFamily: 'var(--font-mono)', marginBottom: '12px' }}>
                                                    <div style={{ color: 'var(--text-tertiary)', marginBottom: '4px' }}>Webhook URL</div>
                                                    <div style={{ wordBreak: 'break-all', color: 'var(--accent-primary)' }}>
                                                        {webhookData?.webhook_url || `${window.location.origin}/api/channel/feishu/${id}/webhook`}
                                                    </div>
                                                </div>
                                                <button className="btn btn-danger" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={async () => { await channelApi.delete(id!); queryClient.invalidateQueries({ queryKey: ['channel', id] }); }}>Disconnect</button>
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
                                                        <input className="input" type="password" value={channelForm.app_secret} onChange={e => setChannelForm({ ...channelForm, app_secret: e.target.value })} style={{ fontSize: '12px' }} />
                                                    </div>
                                                    <div>
                                                        <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>Encrypt Key</label>
                                                        <input className="input" value={channelForm.encrypt_key} onChange={e => setChannelForm({ ...channelForm, encrypt_key: e.target.value })} style={{ fontSize: '12px' }} />
                                                    </div>
                                                </div>
                                                <button className="btn btn-primary" style={{ fontSize: '12px' }} onClick={() => saveChannel.mutate()} disabled={!channelForm.app_id || !channelForm.app_secret || saveChannel.isPending}>
                                                    {saveChannel.isPending ? t('common.loading') : t('agent.settings.channel.saveChannel')}
                                                </button>
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
                                </div>

                                {/* Danger Zone */}
                                <div className="card" style={{ borderColor: 'var(--error)' }}>
                                    <h4 style={{ color: 'var(--error)', marginBottom: '12px' }}>{t('agent.settings.danger.title')}</h4>
                                    <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                                        {t('agent.settings.danger.deleteWarning')}
                                    </p>
                                    {!showDeleteConfirm ? (
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
                                    )}
                                </div>
                            </div>
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
        </>
    );
}
