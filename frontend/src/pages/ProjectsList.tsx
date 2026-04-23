import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
    IconPlus, IconSearch, IconFolderOpen, IconFolder,
    IconCalendar, IconTag, IconX,
} from '@tabler/icons-react';
import { fetchJson } from '../services/api';

// ─── Types ───────────────────────────────────────────────────────────────────

interface Tag { id: string; name: string; color: string | null; }
interface AgentInProject { agent_id: string; name: string; avatar_url: string | null; role: string; }
interface Project {
    id: string;
    name: string;
    description: string | null;
    folder: string | null;
    status: string;
    target_completion_at: string | null;
    created_at: string;
    tags: Tag[];
    agents: AgentInProject[];
    agent_count: number;
    task_count: number;
    task_completed_count: number;
    task_open_count: number;
    completion_ratio: number;
}

// ─── API helpers ─────────────────────────────────────────────────────────────

const api = {
    listProjects: (params: Record<string, string> = {}): Promise<Project[]> => {
        const qs = new URLSearchParams(params).toString();
        return fetchJson(`/projects${qs ? '?' + qs : ''}`);
    },
    listTags: (): Promise<Tag[]> => fetchJson('/project-tags'),
    listFolders: (): Promise<string[]> => fetchJson('/projects/folders'),
    createProject: (body: object): Promise<Project> =>
        fetchJson('/projects', { method: 'POST', body: JSON.stringify(body) }),
};

// ─── Status badge ────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
    draft: 'var(--text-tertiary)',
    active: 'var(--success)',
    on_hold: 'var(--warning)',
    completed: 'var(--primary)',
    archived: 'var(--text-tertiary)',
};

function StatusBadge({ status, label }: { status: string; label: string }) {
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            padding: '2px 8px', borderRadius: 20, fontSize: 11, fontWeight: 500,
            background: `${STATUS_COLORS[status] || 'var(--text-tertiary)'}18`,
            color: STATUS_COLORS[status] || 'var(--text-tertiary)',
            border: `1px solid ${STATUS_COLORS[status] || 'var(--text-tertiary)'}40`,
        }}>
            <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'currentColor' }} />
            {label}
        </span>
    );
}

// ─── Create modal ────────────────────────────────────────────────────────────

function CreateProjectModal({ onClose, onCreate }: { onClose: () => void; onCreate: (p: Project) => void }) {
    const { t } = useTranslation();
    const [name, setName] = useState('');
    const [description, setDescription] = useState('');
    const [folder, setFolder] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const submit = async () => {
        if (!name.trim()) { setError(t('project.settings.namePlaceholder')); return; }
        setLoading(true);
        try {
            const p = await api.createProject({ name: name.trim(), description: description.trim() || undefined, folder: folder.trim() || undefined });
            onCreate(p);
        } catch (e: any) {
            setError(e?.message || 'Error');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
            <div style={{ background: 'var(--bg-primary)', borderRadius: 12, padding: 28, width: 440, boxShadow: '0 8px 32px rgba(0,0,0,0.2)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{t('project.newProject')}</h3>
                    <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', display: 'flex' }}>
                        <IconX size={18} stroke={2} />
                    </button>
                </div>

                {error && <div style={{ color: 'var(--error)', fontSize: 13, marginBottom: 12, padding: '8px 12px', background: 'var(--error)10', borderRadius: 6 }}>{error}</div>}

                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                    <div>
                        <label style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>{t('project.settings.name')} *</label>
                        <input
                            value={name} onChange={e => setName(e.target.value)}
                            placeholder={t('project.settings.namePlaceholder')}
                            onKeyDown={e => e.key === 'Enter' && submit()}
                            autoFocus
                            style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--border-subtle)', borderRadius: 8, background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 14, boxSizing: 'border-box', outline: 'none' }}
                        />
                    </div>
                    <div>
                        <label style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>{t('project.overview.description')}</label>
                        <textarea
                            value={description} onChange={e => setDescription(e.target.value)}
                            rows={3}
                            style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--border-subtle)', borderRadius: 8, background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 14, boxSizing: 'border-box', resize: 'vertical', outline: 'none', fontFamily: 'inherit' }}
                        />
                    </div>
                    <div>
                        <label style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>{t('project.settings.folder')}</label>
                        <input
                            value={folder} onChange={e => setFolder(e.target.value)}
                            placeholder={t('project.settings.folderPlaceholder')}
                            style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--border-subtle)', borderRadius: 8, background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 14, boxSizing: 'border-box', outline: 'none' }}
                        />
                    </div>
                </div>

                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 24 }}>
                    <button onClick={onClose} className="btn btn-ghost" style={{ fontSize: 14 }}>{t('common.cancel', 'Cancel')}</button>
                    <button onClick={submit} disabled={loading || !name.trim()} className="btn btn-primary" style={{ fontSize: 14 }}>
                        {loading ? '...' : t('project.newProject')}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─── Project Card ────────────────────────────────────────────────────────────

function ProjectCard({ project, statusLabel, onClick }: { project: Project; statusLabel: string; onClick: () => void }) {
    const { t } = useTranslation();

    const formatDate = (iso: string | null) => {
        if (!iso) return null;
        return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    };
    const completionPercent = Math.round((project.completion_ratio || 0) * 100);

    return (
        <div onClick={onClick} style={{
            background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)',
            borderRadius: 12, padding: 20, cursor: 'pointer', transition: 'box-shadow 0.15s, border-color 0.15s',
        }}
            onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = '0 4px 16px rgba(0,0,0,0.08)'; (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border-default)'; }}
            onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = 'none'; (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border-subtle)'; }}
        >
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 15, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {project.name}
                    </div>
                    {project.folder && (
                        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2, display: 'flex', alignItems: 'center', gap: 3 }}>
                            <IconFolder size={11} stroke={1.5} />
                            {project.folder}
                        </div>
                    )}
                </div>
                <StatusBadge status={project.status} label={statusLabel} />
            </div>

            {/* Description */}
            {project.description && (
                <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 12px', lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                    {project.description}
                </p>
            )}

            {/* Tags */}
            {project.tags.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 12 }}>
                    {project.tags.slice(0, 3).map(tag => (
                        <span key={tag.id} style={{
                            fontSize: 11, padding: '2px 7px', borderRadius: 20,
                            background: tag.color ? `${tag.color}20` : 'var(--bg-secondary)',
                            color: tag.color || 'var(--text-tertiary)',
                            border: `1px solid ${tag.color ? `${tag.color}40` : 'var(--border-subtle)'}`,
                        }}>
                            {tag.name}
                        </span>
                    ))}
                    {project.tags.length > 3 && <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>+{project.tags.length - 3}</span>}
                </div>
            )}

            {project.task_count > 0 && (
                <div style={{ marginBottom: 12 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t('project.overview.progress')}</span>
                        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{completionPercent}%</span>
                    </div>
                    <div style={{ height: 6, borderRadius: 999, background: 'var(--bg-secondary)', overflow: 'hidden' }}>
                        <div style={{ width: `${completionPercent}%`, height: '100%', background: 'var(--primary)' }} />
                    </div>
                    <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-tertiary)' }}>
                        {project.task_completed_count}/{project.task_count} {t('project.overview.progressLabel')}
                    </div>
                </div>
            )}

            {/* Footer */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 4 }}>
                {/* Agent avatars */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <div style={{ display: 'flex' }}>
                        {project.agents.slice(0, 4).map((a, i) => (
                            <div key={a.agent_id} style={{
                                width: 22, height: 22, borderRadius: '50%', border: '2px solid var(--bg-primary)',
                                marginLeft: i > 0 ? -6 : 0, overflow: 'hidden', background: 'var(--bg-secondary)',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                fontSize: 10, fontWeight: 600, color: 'var(--text-secondary)',
                            }}>
                                {a.avatar_url
                                    ? <img src={a.avatar_url} alt={a.name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                                    : a.name[0]?.toUpperCase()}
                            </div>
                        ))}
                    </div>
                    {project.agent_count > 0 && (
                        <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                            {project.agent_count} {t('project.tabs.agents').toLowerCase()}
                        </span>
                    )}
                </div>

                {/* Target date */}
                {project.target_completion_at && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--text-tertiary)' }}>
                        <IconCalendar size={12} stroke={1.5} />
                        {formatDate(project.target_completion_at)}
                    </div>
                )}
            </div>
        </div>
    );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export default function ProjectsList() {
    const { t } = useTranslation();
    const navigate = useNavigate();

    const [projects, setProjects] = useState<Project[]>([]);
    const [folders, setFolders] = useState<string[]>([]);
    const [tags, setTags] = useState<Tag[]>([]);
    const [loading, setLoading] = useState(true);

    const [q, setQ] = useState('');
    const [statusFilter, setStatusFilter] = useState('all');
    const [folderFilter, setFolderFilter] = useState<string | null>(null);
    const [tagFilter, setTagFilter] = useState<string | null>(null);

    const [showCreate, setShowCreate] = useState(false);

    const STATUSES = [
        { key: 'all', label: t('project.status.all') },
        { key: 'draft', label: t('project.status.draft') },
        { key: 'active', label: t('project.status.active') },
        { key: 'on_hold', label: t('project.status.on_hold') },
        { key: 'completed', label: t('project.status.completed') },
        { key: 'archived', label: t('project.status.archived') },
    ];

    const statusLabel = (s: string) => STATUSES.find(x => x.key === s)?.label || s;

    const load = async () => {
        setLoading(true);
        try {
            const params: Record<string, string> = { status: statusFilter };
            if (q) params.q = q;
            if (folderFilter) params.folder = folderFilter;
            if (tagFilter) params.tag = tagFilter;
            const [ps, fs, ts] = await Promise.all([
                api.listProjects(params),
                api.listFolders(),
                api.listTags(),
            ]);
            setProjects(ps);
            setFolders(fs);
            setTags(ts);
        } catch (e) {
            console.error('Failed to load projects', e);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, [statusFilter, folderFilter, tagFilter]);

    // debounce q
    useEffect(() => {
        const timer = setTimeout(() => load(), 300);
        return () => clearTimeout(timer);
    }, [q]);

    const handleCreate = (p: Project) => {
        setShowCreate(false);
        navigate(`/projects/${p.id}`);
    };

    return (
        <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
            {/* Left: Folder sidebar */}
            <div style={{ width: 180, flexShrink: 0, borderRight: '1px solid var(--border-subtle)', padding: '20px 0', overflowY: 'auto' }}>
                <div style={{ padding: '0 16px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                    {t('project.folders')}
                </div>
                <button
                    onClick={() => setFolderFilter(null)}
                    style={{
                        display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                        padding: '7px 16px', background: folderFilter === null ? 'var(--bg-secondary)' : 'none',
                        border: 'none', cursor: 'pointer', fontSize: 13,
                        color: folderFilter === null ? 'var(--text-primary)' : 'var(--text-secondary)',
                        fontWeight: folderFilter === null ? 600 : 400, textAlign: 'left',
                    }}
                >
                    <IconFolderOpen size={14} stroke={1.5} />
                    {t('project.allFolders')}
                </button>
                {folders.map(f => (
                    <button
                        key={f}
                        onClick={() => setFolderFilter(folderFilter === f ? null : f)}
                        style={{
                            display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                            padding: '7px 16px', background: folderFilter === f ? 'var(--bg-secondary)' : 'none',
                            border: 'none', cursor: 'pointer', fontSize: 13,
                            color: folderFilter === f ? 'var(--text-primary)' : 'var(--text-secondary)',
                            fontWeight: folderFilter === f ? 600 : 400, textAlign: 'left',
                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}
                    >
                        <IconFolder size={14} stroke={1.5} style={{ flexShrink: 0 }} />
                        {f}
                    </button>
                ))}

                {/* Tags */}
                {tags.length > 0 && (
                    <>
                        <div style={{ padding: '16px 16px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                            {t('project.tags.manage')}
                        </div>
                        {tags.map(tag => (
                            <button
                                key={tag.id}
                                onClick={() => setTagFilter(tagFilter === tag.id ? null : tag.id)}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                                    padding: '6px 16px', background: tagFilter === tag.id ? 'var(--bg-secondary)' : 'none',
                                    border: 'none', cursor: 'pointer', fontSize: 13,
                                    color: tagFilter === tag.id ? 'var(--text-primary)' : 'var(--text-secondary)',
                                    textAlign: 'left',
                                }}
                            >
                                <span style={{
                                    width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                                    background: tag.color || 'var(--text-tertiary)',
                                }} />
                                {tag.name}
                            </button>
                        ))}
                    </>
                )}
            </div>

            {/* Right: main content */}
            <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                {/* Toolbar */}
                <div style={{ padding: '20px 24px 0', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                    <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, flex: 'none' }}>{t('project.title')}</h1>

                    {/* Search */}
                    <div style={{ position: 'relative', flex: '0 0 220px' }}>
                        <IconSearch size={13} stroke={2} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)', pointerEvents: 'none' }} />
                        <input
                            value={q} onChange={e => setQ(e.target.value)}
                            placeholder={t('project.search')}
                            style={{ width: '100%', padding: '7px 10px 7px 30px', border: '1px solid var(--border-subtle)', borderRadius: 8, background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 13, boxSizing: 'border-box', outline: 'none' }}
                        />
                    </div>

                    {/* Status filter */}
                    <div style={{ display: 'flex', gap: 4 }}>
                        {STATUSES.map(s => (
                            <button key={s.key} onClick={() => setStatusFilter(s.key)} style={{
                                padding: '5px 12px', borderRadius: 20, fontSize: 12, fontWeight: 500, cursor: 'pointer',
                                border: `1px solid ${statusFilter === s.key ? 'var(--primary)' : 'var(--border-subtle)'}`,
                                background: statusFilter === s.key ? 'var(--primary)' : 'var(--bg-secondary)',
                                color: statusFilter === s.key ? '#fff' : 'var(--text-secondary)',
                            }}>
                                {s.label}
                            </button>
                        ))}
                    </div>

                    {/* Active tag filter pill */}
                    {tagFilter && (
                        <span style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px', borderRadius: 20, background: 'var(--primary)18', color: 'var(--primary)', fontSize: 12, border: '1px solid var(--primary)40' }}>
                            <IconTag size={11} stroke={2} />
                            {tags.find(t => t.id === tagFilter)?.name}
                            <button onClick={() => setTagFilter(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'inherit', display: 'flex', padding: 0, marginLeft: 2 }}><IconX size={11} stroke={2} /></button>
                        </span>
                    )}

                    <div style={{ marginLeft: 'auto' }}>
                        <button onClick={() => setShowCreate(true)} className="btn btn-primary" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                            <IconPlus size={14} stroke={2} />
                            {t('project.newProject')}
                        </button>
                    </div>
                </div>

                {/* Grid */}
                <div style={{ flex: 1, overflowY: 'auto', padding: '16px 24px 24px' }}>
                    {loading ? (
                        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 200, color: 'var(--text-tertiary)' }}>
                            {t('common.loading', 'Loading...')}
                        </div>
                    ) : projects.length === 0 ? (
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 300, gap: 12, color: 'var(--text-tertiary)' }}>
                            <IconFolderOpen size={40} stroke={1} />
                            <div style={{ fontWeight: 600, fontSize: 15, color: 'var(--text-secondary)' }}>{t('project.noProjects')}</div>
                            <div style={{ fontSize: 13 }}>{t('project.noProjectsDesc')}</div>
                            <button onClick={() => setShowCreate(true)} className="btn btn-primary" style={{ marginTop: 8, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                                <IconPlus size={14} stroke={2} />
                                {t('project.newProject')}
                            </button>
                        </div>
                    ) : (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16 }}>
                            {projects.map(p => (
                                <ProjectCard
                                    key={p.id}
                                    project={p}
                                    statusLabel={statusLabel(p.status)}
                                    onClick={() => navigate(`/projects/${p.id}`)}
                                />
                            ))}
                        </div>
                    )}
                </div>
            </div>

            {showCreate && <CreateProjectModal onClose={() => setShowCreate(false)} onCreate={handleCreate} />}
        </div>
    );
}
