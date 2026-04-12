import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
    IconArrowLeft, IconPlus, IconTrash, IconCheck, IconX,
    IconUsers, IconCalendar, IconFolder, IconTag, IconSettings,
    IconEdit, IconChevronDown, IconLock, IconLockOpen,
} from '@tabler/icons-react';
import { fetchJson } from '../services/api';
import { useAuthStore } from '../stores';

// ─── Types ───────────────────────────────────────────────────────────────────

interface Tag { id: string; name: string; color: string | null; }
interface AgentInProject { agent_id: string; name: string; avatar_url: string | null; role: string; added_at: string; }
interface Project {
    id: string; name: string; description: string | null; brief: string | null;
    folder: string | null; status: string; collab_mode: string;
    target_completion_at: string | null; started_at: string | null; completed_at: string | null;
    created_by: string; created_at: string; updated_at: string;
    tags: Tag[]; agents: AgentInProject[]; agent_count: number;
}
interface A2APair { source_agent_id: string; target_agent_id: string; forward_authorized: boolean; reverse_authorized: boolean; }
interface AvailableAgent { id: string; name: string; avatar_url: string | null; }

// ─── API ─────────────────────────────────────────────────────────────────────

const api = {
    getProject: (id: string) => fetchJson<Project>(`/projects/${id}`),
    updateProject: (id: string, body: object) => fetchJson<Project>(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    transition: (id: string, action: string, force = false) => fetchJson<Project>(`/projects/${id}/transition`, { method: 'POST', body: JSON.stringify({ action, force }) }),
    deleteProject: (id: string) => fetchJson(`/projects/${id}`, { method: 'DELETE' }),
    listAgents: (id: string) => fetchJson<AgentInProject[]>(`/projects/${id}/agents`),
    addAgent: (id: string, agent_id: string, role: string) => fetchJson<AgentInProject>(`/projects/${id}/agents`, { method: 'POST', body: JSON.stringify({ agent_id, role }) }),
    updateAgentRole: (id: string, agentId: string, role: string) => fetchJson(`/projects/${id}/agents/${agentId}`, { method: 'PATCH', body: JSON.stringify({ role }) }),
    removeAgent: (id: string, agentId: string) => fetchJson(`/projects/${id}/agents/${agentId}`, { method: 'DELETE' }),
    getA2AMatrix: (id: string) => fetchJson<A2APair[]>(`/projects/${id}/a2a-matrix`),
    grantA2A: (id: string, src: string, dst: string) => fetchJson(`/projects/${id}/a2a-grant`, { method: 'POST', body: JSON.stringify({ source_agent_id: src, target_agent_id: dst }) }),
    listTags: () => fetchJson<Tag[]>('/project-tags'),
    setTags: (id: string, tag_ids: string[]) => fetchJson(`/projects/${id}/tags`, { method: 'POST', body: JSON.stringify({ tag_ids }) }),
    listAvailableAgents: () => fetchJson<any>('/agents'),
};

// ─── Helpers ─────────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
    draft: 'var(--text-tertiary)', active: 'var(--success)', on_hold: 'var(--warning)',
    completed: 'var(--primary)', archived: 'var(--text-tertiary)',
};

const TRANSITION_MAP: Record<string, string[]> = {
    draft: ['start', 'archive'],
    active: ['pause', 'complete', 'archive'],
    on_hold: ['resume', 'archive'],
    completed: ['archive'],
    archived: [],
};

function StatusBadge({ status, label }: { status: string; label: string }) {
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 10px', borderRadius: 20,
            fontSize: 12, fontWeight: 500,
            background: `${STATUS_COLORS[status] || 'var(--text-tertiary)'}18`,
            color: STATUS_COLORS[status] || 'var(--text-tertiary)',
            border: `1px solid ${STATUS_COLORS[status] || 'var(--text-tertiary)'}40`,
        }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor' }} />
            {label}
        </span>
    );
}

function AvatarOrInitial({ name, avatarUrl, size = 28 }: { name: string; avatarUrl: string | null; size?: number }) {
    if (avatarUrl) return <img src={avatarUrl} alt={name} style={{ width: size, height: size, borderRadius: '50%', objectFit: 'cover' }} />;
    return (
        <div style={{ width: size, height: size, borderRadius: '50%', background: 'var(--primary)18', color: 'var(--primary)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: size * 0.4, fontWeight: 600 }}>
            {name[0]?.toUpperCase()}
        </div>
    );
}

// ─── Add Agent Modal ─────────────────────────────────────────────────────────

function AddAgentModal({ projectId, existing, onClose, onAdded }: {
    projectId: string; existing: string[]; onClose: () => void; onAdded: (a: AgentInProject) => void;
}) {
    const { t } = useTranslation();
    const [agents, setAgents] = useState<AvailableAgent[]>([]);
    const [q, setQ] = useState('');
    const [selected, setSelected] = useState<string | null>(null);
    const [role, setRole] = useState('member');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        api.listAvailableAgents()
            .then((data: any) => setAgents((data?.agents || data || []).filter((a: AvailableAgent) => !existing.includes(a.id))))
            .catch(() => { });
    }, []);

    const filtered = agents.filter(a => a.name.toLowerCase().includes(q.toLowerCase()));

    const submit = async () => {
        if (!selected) return;
        setLoading(true);
        try {
            const added = await api.addAgent(projectId, selected, role);
            onAdded(added);
        } catch (e: any) {
            setError(e?.message || 'Error');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
            <div style={{ background: 'var(--bg-primary)', borderRadius: 12, padding: 24, width: 420, maxHeight: '80vh', display: 'flex', flexDirection: 'column', boxShadow: '0 8px 32px rgba(0,0,0,0.2)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>{t('project.agents.addAgent')}</h3>
                    <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', display: 'flex' }}><IconX size={18} stroke={2} /></button>
                </div>

                {error && <div style={{ color: 'var(--error)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

                <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search agents..."
                    style={{ padding: '7px 12px', border: '1px solid var(--border-subtle)', borderRadius: 8, background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 13, outline: 'none', marginBottom: 8 }} />

                <div style={{ flex: 1, overflowY: 'auto', border: '1px solid var(--border-subtle)', borderRadius: 8, marginBottom: 12 }}>
                    {filtered.length === 0
                        ? <div style={{ padding: 16, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>No agents available</div>
                        : filtered.map(a => (
                            <div key={a.id} onClick={() => setSelected(a.id === selected ? null : a.id)}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', cursor: 'pointer',
                                    background: selected === a.id ? 'var(--primary)10' : 'none',
                                    borderBottom: '1px solid var(--border-subtle)',
                                }}>
                                <AvatarOrInitial name={a.name} avatarUrl={a.avatar_url} size={26} />
                                <span style={{ fontSize: 13, flex: 1 }}>{a.name}</span>
                                {selected === a.id && <IconCheck size={14} stroke={2} style={{ color: 'var(--primary)' }} />}
                            </div>
                        ))
                    }
                </div>

                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16 }}>
                    <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Role:</label>
                    {['lead', 'member', 'observer'].map(r => (
                        <button key={r} onClick={() => setRole(r)} style={{
                            padding: '4px 12px', borderRadius: 20, fontSize: 12, cursor: 'pointer',
                            border: `1px solid ${role === r ? 'var(--primary)' : 'var(--border-subtle)'}`,
                            background: role === r ? 'var(--primary)' : 'var(--bg-secondary)',
                            color: role === r ? '#fff' : 'var(--text-secondary)',
                        }}>
                            {t(`project.role.${r}`)}
                        </button>
                    ))}
                </div>

                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                    <button onClick={onClose} className="btn btn-ghost" style={{ fontSize: 13 }}>{t('common.cancel', 'Cancel')}</button>
                    <button onClick={submit} disabled={!selected || loading} className="btn btn-primary" style={{ fontSize: 13 }}>
                        {loading ? '...' : t('project.agents.addAgent')}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─── Overview Tab ─────────────────────────────────────────────────────────────

function OverviewTab({ project, canWrite, onUpdate }: { project: Project; canWrite: boolean; onUpdate: (p: Project) => void }) {
    const { t } = useTranslation();
    const [editingBrief, setEditingBrief] = useState(false);
    const [briefValue, setBriefValue] = useState(project.brief || '');
    const [editingDesc, setEditingDesc] = useState(false);
    const [descValue, setDescValue] = useState(project.description || '');
    const [saving, setSaving] = useState(false);

    const saveField = async (field: string, value: string) => {
        setSaving(true);
        try {
            const updated = await api.updateProject(project.id, { [field]: value || null });
            onUpdate(updated);
        } catch (e) { console.error(e); }
        finally { setSaving(false); }
    };

    const fmtDate = (iso: string | null) => iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' }) : null;

    return (
        <div style={{ display: 'flex', gap: 24, padding: '24px 0' }}>
            {/* Left col — main content */}
            <div style={{ flex: 1, minWidth: 0 }}>
                {/* Description */}
                <section style={{ marginBottom: 28 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{t('project.overview.description')}</h3>
                        {canWrite && !editingDesc && (
                            <button onClick={() => { setEditingDesc(true); setDescValue(project.description || ''); }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', display: 'flex' }}>
                                <IconEdit size={14} stroke={1.5} />
                            </button>
                        )}
                    </div>
                    {editingDesc ? (
                        <div>
                            <textarea value={descValue} onChange={e => setDescValue(e.target.value)} rows={4} autoFocus
                                style={{ width: '100%', padding: '10px 12px', border: '1px solid var(--primary)', borderRadius: 8, background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 14, resize: 'vertical', outline: 'none', fontFamily: 'inherit', boxSizing: 'border-box' }} />
                            <div style={{ display: 'flex', gap: 8, marginTop: 8, justifyContent: 'flex-end' }}>
                                <button onClick={() => setEditingDesc(false)} className="btn btn-ghost" style={{ fontSize: 12 }}>{t('common.cancel', 'Cancel')}</button>
                                <button onClick={() => { saveField('description', descValue); setEditingDesc(false); }} disabled={saving} className="btn btn-primary" style={{ fontSize: 12 }}>Save</button>
                            </div>
                        </div>
                    ) : (
                        <p style={{ margin: 0, fontSize: 14, color: project.description ? 'var(--text-primary)' : 'var(--text-tertiary)', lineHeight: 1.6 }}>
                            {project.description || t('project.overview.noDescription')}
                        </p>
                    )}
                </section>

                {/* Brief */}
                <section>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                        <div>
                            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{t('project.overview.brief')}</h3>
                            <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--text-tertiary)' }}>{t('project.overview.briefPlaceholder')}</p>
                        </div>
                        {canWrite && !editingBrief && (
                            <button onClick={() => { setEditingBrief(true); setBriefValue(project.brief || ''); }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', display: 'flex', flexShrink: 0 }}>
                                <IconEdit size={14} stroke={1.5} />
                            </button>
                        )}
                    </div>
                    {editingBrief ? (
                        <div>
                            <textarea value={briefValue} onChange={e => setBriefValue(e.target.value)} rows={8} autoFocus
                                placeholder={t('project.overview.briefPlaceholder')}
                                style={{ width: '100%', padding: '10px 12px', border: '1px solid var(--primary)', borderRadius: 8, background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 13, resize: 'vertical', outline: 'none', fontFamily: 'monospace', boxSizing: 'border-box', lineHeight: 1.6 }} />
                            <div style={{ display: 'flex', gap: 8, marginTop: 8, justifyContent: 'flex-end' }}>
                                <button onClick={() => setEditingBrief(false)} className="btn btn-ghost" style={{ fontSize: 12 }}>{t('common.cancel', 'Cancel')}</button>
                                <button onClick={() => { saveField('brief', briefValue); setEditingBrief(false); }} disabled={saving} className="btn btn-primary" style={{ fontSize: 12 }}>Save</button>
                            </div>
                        </div>
                    ) : (
                        <pre style={{ margin: 0, fontSize: 13, color: project.brief ? 'var(--text-primary)' : 'var(--text-tertiary)', lineHeight: 1.7, whiteSpace: 'pre-wrap', fontFamily: 'inherit', background: 'var(--bg-secondary)', padding: 14, borderRadius: 8, border: '1px solid var(--border-subtle)' }}>
                            {project.brief || t('project.overview.noBrief')}
                        </pre>
                    )}
                </section>
            </div>

            {/* Right col — meta */}
            <div style={{ width: 220, flexShrink: 0 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                    <MetaRow label={t('project.overview.status')} value={<StatusBadge status={project.status} label={t(`project.status.${project.status}`)} />} />
                    {project.folder && <MetaRow label={t('project.overview.folder')} value={<span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13 }}><IconFolder size={13} stroke={1.5} />{project.folder}</span>} />}
                    {project.target_completion_at && <MetaRow label={t('project.overview.targetDate')} value={<span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13, color: 'var(--text-secondary)' }}><IconCalendar size={13} stroke={1.5} />{fmtDate(project.target_completion_at)}</span>} />}
                    <MetaRow label={t('project.overview.createdAt')} value={<span style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>{fmtDate(project.created_at)}</span>} />
                    {project.tags.length > 0 && (
                        <div>
                            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>{t('project.overview.tags')}</div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                {project.tags.map(tag => (
                                    <span key={tag.id} style={{ fontSize: 11, padding: '2px 8px', borderRadius: 20, background: tag.color ? `${tag.color}20` : 'var(--bg-secondary)', color: tag.color || 'var(--text-tertiary)', border: `1px solid ${tag.color ? `${tag.color}40` : 'var(--border-subtle)'}` }}>
                                        {tag.name}
                                    </span>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
    return (
        <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>{label}</div>
            <div>{value}</div>
        </div>
    );
}

// ─── Agents Tab ───────────────────────────────────────────────────────────────

function AgentsTab({ project, canWrite, onUpdate }: { project: Project; canWrite: boolean; onUpdate: (p: Project) => void }) {
    const { t } = useTranslation();
    const [agents, setAgents] = useState<AgentInProject[]>(project.agents);
    const [a2a, setA2A] = useState<A2APair[]>([]);
    const [showAdd, setShowAdd] = useState(false);
    const [a2aLoading, setA2ALoading] = useState<string | null>(null);

    useEffect(() => {
        if (agents.length >= 2) {
            api.getA2AMatrix(project.id).then(setA2A).catch(() => { });
        }
    }, [agents]);

    const handleRemove = async (agentId: string) => {
        try {
            await api.removeAgent(project.id, agentId);
            setAgents(prev => prev.filter(a => a.agent_id !== agentId));
        } catch (e: any) { alert(e?.message || 'Error'); }
    };

    const handleRoleChange = async (agentId: string, role: string) => {
        try {
            await api.updateAgentRole(project.id, agentId, role);
            setAgents(prev => prev.map(a => a.agent_id === agentId ? { ...a, role } : a));
        } catch (e: any) { alert(e?.message || 'Error'); }
    };

    const handleGrant = async (src: string, dst: string) => {
        const key = `${src}-${dst}`;
        setA2ALoading(key);
        try {
            await api.grantA2A(project.id, src, dst);
            const updated = await api.getA2AMatrix(project.id);
            setA2A(updated);
        } catch (e: any) { alert(e?.message || 'Error'); }
        finally { setA2ALoading(null); }
    };

    const agentName = (id: string) => agents.find(a => a.agent_id === id)?.name || id.slice(0, 8);

    return (
        <div style={{ padding: '24px 0' }}>
            {/* Agent list */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{t('project.tabs.agents')} ({agents.length})</h3>
                {canWrite && (
                    <button onClick={() => setShowAdd(true)} className="btn btn-ghost" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                        <IconPlus size={14} stroke={2} />
                        {t('project.agents.addAgent')}
                    </button>
                )}
            </div>

            {agents.length === 0 ? (
                <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-tertiary)', border: '1px dashed var(--border-subtle)', borderRadius: 8, fontSize: 13 }}>
                    {t('project.agents.empty')}
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 32 }}>
                    {agents.map(a => (
                        <div key={a.agent_id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)', borderRadius: 10 }}>
                            <AvatarOrInitial name={a.name} avatarUrl={a.avatar_url} size={32} />
                            <div style={{ flex: 1 }}>
                                <div style={{ fontWeight: 500, fontSize: 14 }}>{a.name}</div>
                            </div>
                            {/* Role selector */}
                            <div style={{ display: 'flex', gap: 4 }}>
                                {['lead', 'member', 'observer'].map(r => (
                                    <button key={r} onClick={() => canWrite && handleRoleChange(a.agent_id, r)}
                                        disabled={!canWrite}
                                        style={{
                                            padding: '3px 10px', borderRadius: 20, fontSize: 11, cursor: canWrite ? 'pointer' : 'default',
                                            border: `1px solid ${a.role === r ? 'var(--primary)' : 'var(--border-subtle)'}`,
                                            background: a.role === r ? 'var(--primary)' : 'transparent',
                                            color: a.role === r ? '#fff' : 'var(--text-secondary)',
                                        }}>
                                        {t(`project.role.${r}`)}
                                    </button>
                                ))}
                            </div>
                            {canWrite && (
                                <button onClick={() => handleRemove(a.agent_id)}
                                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', display: 'flex', padding: 4 }}
                                    title={t('project.agents.removeAgent')}>
                                    <IconTrash size={14} stroke={1.5} />
                                </button>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {/* A2A Matrix */}
            {agents.length >= 2 && (
                <div>
                    <h3 style={{ margin: '0 0 8px', fontSize: 14, fontWeight: 600 }}>{t('project.agents.a2aMatrix')}</h3>
                    <p style={{ margin: '0 0 14px', fontSize: 13, color: 'var(--text-secondary)' }}>{t('project.agents.a2aMatrixDesc')}</p>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {a2a.map(pair => {
                            const key = `${pair.source_agent_id}-${pair.target_agent_id}`;
                            const bothAuth = pair.forward_authorized && pair.reverse_authorized;
                            return (
                                <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', background: 'var(--bg-secondary)', borderRadius: 8, border: '1px solid var(--border-subtle)' }}>
                                    <span style={{ fontSize: 13, flex: 1 }}>{agentName(pair.source_agent_id)} ↔ {agentName(pair.target_agent_id)}</span>
                                    <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: bothAuth ? 'var(--success)' : 'var(--text-tertiary)' }}>
                                        {bothAuth ? <IconLockOpen size={13} stroke={1.5} /> : <IconLock size={13} stroke={1.5} />}
                                        {bothAuth ? t('project.agents.authorized') : t('project.agents.notAuthorized')}
                                    </span>
                                    {!bothAuth && canWrite && (
                                        <button
                                            onClick={() => handleGrant(pair.source_agent_id, pair.target_agent_id)}
                                            disabled={a2aLoading === key}
                                            className="btn btn-ghost"
                                            style={{ fontSize: 12, padding: '3px 10px' }}>
                                            {a2aLoading === key ? '...' : t('project.agents.grantBoth')}
                                        </button>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {showAdd && (
                <AddAgentModal
                    projectId={project.id}
                    existing={agents.map(a => a.agent_id)}
                    onClose={() => setShowAdd(false)}
                    onAdded={a => { setAgents(prev => [...prev, a]); setShowAdd(false); }}
                />
            )}
        </div>
    );
}

// ─── Settings Tab ─────────────────────────────────────────────────────────────

function SettingsTab({ project, canWrite, onUpdate, onDeleted }: { project: Project; canWrite: boolean; onUpdate: (p: Project) => void; onDeleted: () => void; }) {
    const { t } = useTranslation();
    const [name, setName] = useState(project.name);
    const [folder, setFolder] = useState(project.folder || '');
    const [targetDate, setTargetDate] = useState(project.target_completion_at ? project.target_completion_at.slice(0, 10) : '');
    const [collabMode, setCollabMode] = useState(project.collab_mode);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const save = async () => {
        setSaving(true); setError('');
        try {
            const updated = await api.updateProject(project.id, {
                name, folder: folder || null, collab_mode: collabMode,
                target_completion_at: targetDate ? new Date(targetDate).toISOString() : '',
            });
            onUpdate(updated);
        } catch (e: any) { setError(e?.message || 'Error'); }
        finally { setSaving(false); }
    };

    const handleTransition = async (action: string) => {
        const confirmMsgs: Record<string, string> = {
            complete: t('project.transition.confirmComplete'),
            archive: t('project.transition.confirmArchive'),
        };
        if (confirmMsgs[action] && !window.confirm(confirmMsgs[action])) return;
        try {
            const updated = await api.transition(project.id, action);
            onUpdate(updated);
        } catch (e: any) { alert(e?.message || 'Error'); }
    };

    const handleDelete = async () => {
        if (!window.confirm(t('project.settings.confirmDelete'))) return;
        try {
            await api.deleteProject(project.id);
            onDeleted();
        } catch (e: any) { alert(e?.message || 'Error'); }
    };

    const transitions = TRANSITION_MAP[project.status] || [];
    const TRANSITION_LABELS: Record<string, string> = {
        start: t('project.transition.start'), pause: t('project.transition.pause'),
        resume: t('project.transition.resume'), complete: t('project.transition.complete'),
        archive: t('project.transition.archive'),
    };

    return (
        <div style={{ padding: '24px 0', maxWidth: 560 }}>
            {/* Basic info */}
            <h3 style={{ margin: '0 0 16px', fontSize: 14, fontWeight: 600 }}>{t('project.settings.basicInfo')}</h3>
            {error && <div style={{ color: 'var(--error)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginBottom: 24 }}>
                <div>
                    <label style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>{t('project.settings.name')}</label>
                    <input value={name} onChange={e => setName(e.target.value)} disabled={!canWrite}
                        style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--border-subtle)', borderRadius: 8, background: canWrite ? 'var(--bg-secondary)' : 'var(--bg-tertiary)', color: 'var(--text-primary)', fontSize: 14, boxSizing: 'border-box', outline: 'none' }} />
                </div>
                <div>
                    <label style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>{t('project.settings.folder')}</label>
                    <input value={folder} onChange={e => setFolder(e.target.value)} disabled={!canWrite}
                        placeholder={t('project.settings.folderPlaceholder')}
                        style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--border-subtle)', borderRadius: 8, background: canWrite ? 'var(--bg-secondary)' : 'var(--bg-tertiary)', color: 'var(--text-primary)', fontSize: 14, boxSizing: 'border-box', outline: 'none' }} />
                </div>
                <div>
                    <label style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>{t('project.settings.targetDate')}</label>
                    <input type="date" value={targetDate} onChange={e => setTargetDate(e.target.value)} disabled={!canWrite}
                        style={{ padding: '8px 12px', border: '1px solid var(--border-subtle)', borderRadius: 8, background: canWrite ? 'var(--bg-secondary)' : 'var(--bg-tertiary)', color: 'var(--text-primary)', fontSize: 14, outline: 'none' }} />
                </div>
                <div>
                    <label style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>{t('project.settings.collabMode')}</label>
                    <div style={{ display: 'flex', gap: 8 }}>
                        {['isolated', 'group_chat', 'lead_helper'].map(m => (
                            <button key={m} onClick={() => canWrite && setCollabMode(m)} disabled={!canWrite}
                                style={{ padding: '6px 14px', borderRadius: 8, fontSize: 12, cursor: canWrite ? 'pointer' : 'default', border: `1px solid ${collabMode === m ? 'var(--primary)' : 'var(--border-subtle)'}`, background: collabMode === m ? 'var(--primary)10' : 'transparent', color: collabMode === m ? 'var(--primary)' : 'var(--text-secondary)' }}>
                                {t(`project.collabMode.${m}`)}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            {canWrite && (
                <button onClick={save} disabled={saving || !name.trim()} className="btn btn-primary" style={{ fontSize: 13 }}>
                    {saving ? '...' : 'Save Changes'}
                </button>
            )}

            {/* State machine */}
            {transitions.length > 0 && canWrite && (
                <div style={{ marginTop: 32 }}>
                    <h3 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 600 }}>Status</h3>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {transitions.map(action => (
                            <button key={action} onClick={() => handleTransition(action)}
                                style={{
                                    padding: '7px 16px', borderRadius: 8, fontSize: 13, cursor: 'pointer',
                                    border: `1px solid ${action === 'archive' || action === 'complete' ? 'var(--warning)' : 'var(--border-subtle)'}`,
                                    background: 'transparent',
                                    color: action === 'archive' || action === 'complete' ? 'var(--warning)' : 'var(--text-secondary)',
                                }}>
                                {TRANSITION_LABELS[action]}
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* Danger zone */}
            {canWrite && (
                <div style={{ marginTop: 40, padding: 20, border: '1px solid var(--error)30', borderRadius: 10 }}>
                    <h3 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 600, color: 'var(--error)' }}>{t('project.settings.dangerZone')}</h3>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                        <div>
                            <div style={{ fontSize: 13, fontWeight: 500 }}>{t('project.settings.archiveProject')}</div>
                            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t('project.settings.archiveDesc')}</div>
                        </div>
                        {project.status !== 'archived' && (
                            <button onClick={() => handleTransition('archive')} style={{ padding: '6px 14px', borderRadius: 8, fontSize: 13, cursor: 'pointer', border: '1px solid var(--warning)', background: 'transparent', color: 'var(--warning)' }}>
                                {t('project.transition.archive')}
                            </button>
                        )}
                    </div>
                    {project.status === 'archived' && (
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div>
                                <div style={{ fontSize: 13, fontWeight: 500 }}>{t('project.settings.deleteProject')}</div>
                                <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t('project.settings.deleteDesc')}</div>
                            </div>
                            <button onClick={handleDelete} style={{ padding: '6px 14px', borderRadius: 8, fontSize: 13, cursor: 'pointer', border: '1px solid var(--error)', background: 'transparent', color: 'var(--error)' }}>
                                {t('project.settings.deleteProject')}
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function ProjectDetail() {
    const { id } = useParams<{ id: string }>();
    const { t } = useTranslation();
    const navigate = useNavigate();
    const user = useAuthStore(s => s.user);

    const [project, setProject] = useState<Project | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [activeTab, setActiveTab] = useState<'overview' | 'agents' | 'settings'>('overview');

    useEffect(() => {
        if (!id) return;
        setLoading(true);
        api.getProject(id)
            .then(setProject)
            .catch(e => setError(e?.message || 'Error'))
            .finally(() => setLoading(false));
    }, [id]);

    if (loading) return <div style={{ padding: 40, color: 'var(--text-tertiary)' }}>{t('common.loading', 'Loading...')}</div>;
    if (error || !project) return <div style={{ padding: 40, color: 'var(--error)' }}>{error || 'Project not found'}</div>;

    const canWrite = user ? (String(project.created_by) === String(user.id) || ['platform_admin', 'org_admin'].includes(user.role || '')) : false;

    const TABS = [
        { key: 'overview', label: t('project.tabs.overview') },
        { key: 'agents', label: t('project.tabs.agents') },
        { key: 'settings', label: t('project.tabs.settings') },
    ] as const;

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
            {/* Header */}
            <div style={{ padding: '20px 32px 0', borderBottom: '1px solid var(--border-subtle)', flexShrink: 0 }}>
                <button onClick={() => navigate('/projects')} style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 13, padding: 0, marginBottom: 12 }}>
                    <IconArrowLeft size={14} stroke={2} /> {t('project.title')}
                </button>

                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 16 }}>
                    <div style={{ flex: 1 }}>
                        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>{project.name}</h1>
                        {project.folder && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>
                                <IconFolder size={12} stroke={1.5} /> {project.folder}
                            </div>
                        )}
                    </div>
                    <StatusBadge status={project.status} label={t(`project.status.${project.status}`)} />
                </div>

                {/* Tabs */}
                <div style={{ display: 'flex', gap: 0 }}>
                    {TABS.map(tab => (
                        <button key={tab.key} onClick={() => setActiveTab(tab.key)}
                            style={{
                                padding: '8px 18px', background: 'none', border: 'none', cursor: 'pointer',
                                fontSize: 14, fontWeight: activeTab === tab.key ? 600 : 400,
                                color: activeTab === tab.key ? 'var(--text-primary)' : 'var(--text-tertiary)',
                                borderBottom: activeTab === tab.key ? '2px solid var(--primary)' : '2px solid transparent',
                                marginBottom: -1, transition: 'color 0.15s',
                            }}>
                            {tab.label}
                        </button>
                    ))}
                </div>
            </div>

            {/* Tab content */}
            <div style={{ flex: 1, overflowY: 'auto', padding: '0 32px' }}>
                {activeTab === 'overview' && <OverviewTab project={project} canWrite={canWrite} onUpdate={setProject} />}
                {activeTab === 'agents' && <AgentsTab project={project} canWrite={canWrite} onUpdate={setProject} />}
                {activeTab === 'settings' && <SettingsTab project={project} canWrite={canWrite} onUpdate={setProject} onDeleted={() => navigate('/projects')} />}
            </div>
        </div>
    );
}
