import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { IconFolders, IconPlus, IconSearch, IconArchive, IconUsers, IconFile } from '@tabler/icons-react';
import { projectApi } from '../services/api';
import type { Project } from '../types';


const timeAgo = (dateStr: string | null | undefined, t: any) => {
    if (!dateStr) return t('projects.neverActive', 'No activity yet');
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return t('projects.justNow', 'just now');
    if (mins < 60) return t('projects.minutesAgo', { count: mins, defaultValue: '{{count}}m ago' });
    const hours = Math.floor(mins / 60);
    if (hours < 24) return t('projects.hoursAgo', { count: hours, defaultValue: '{{count}}h ago' });
    return t('projects.daysAgo', { count: Math.floor(hours / 24), defaultValue: '{{count}}d ago' });
};


function CreateProjectModal({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
    const { t } = useTranslation();
    const [name, setName] = useState('');
    const [description, setDescription] = useState('');
    const [error, setError] = useState('');
    const [submitting, setSubmitting] = useState(false);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!name.trim()) return;
        setSubmitting(true);
        setError('');
        try {
            const p = await projectApi.create({ name: name.trim(), description: description.trim() });
            onCreated(p.id);
        } catch (e: any) {
            setError(e?.message || String(e));
            setSubmitting(false);
        }
    };

    return (
        <div
            onClick={onClose}
            style={{
                position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
            }}
        >
            <form
                onClick={e => e.stopPropagation()}
                onSubmit={handleSubmit}
                className="card"
                style={{ width: 480, maxWidth: '95vw', padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}
            >
                <h2 style={{ margin: 0, fontSize: 18 }}>{t('projects.newProject', 'New Project')}</h2>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                        {t('projects.create.nameLabel', 'Name')}
                    </label>
                    <input
                        autoFocus
                        value={name}
                        onChange={e => setName(e.target.value)}
                        maxLength={200}
                        className="input"
                        placeholder={t('projects.create.namePlaceholder', 'e.g. Overseas launch — material prep')}
                    />
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                        {t('projects.create.descriptionLabel', 'Short description')}
                        <span style={{ color: 'var(--text-tertiary)', marginLeft: 6 }}>
                            ({description.length}/500)
                        </span>
                    </label>
                    <textarea
                        value={description}
                        onChange={e => setDescription(e.target.value.slice(0, 500))}
                        className="input"
                        style={{ resize: 'vertical', minHeight: 80 }}
                        placeholder={t('projects.create.descriptionPlaceholder', 'A one-liner to help teammates recognise this project at a glance.')}
                    />
                </div>

                {error && (
                    <div style={{ color: 'var(--error)', fontSize: 13 }}>{error}</div>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                    <button type="button" className="btn" onClick={onClose}>
                        {t('common.cancel', 'Cancel')}
                    </button>
                    <button type="submit" className="btn btn-primary" disabled={!name.trim() || submitting}>
                        {submitting ? t('common.creating', 'Creating...') : t('common.create', 'Create')}
                    </button>
                </div>
            </form>
        </div>
    );
}


function ProjectCard({ project, onClick }: { project: Project; onClick: () => void }) {
    const { t } = useTranslation();
    const archived = !!project.archived_at;

    return (
        <div
            className="card"
            onClick={onClick}
            style={{
                padding: 16, cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 10,
                opacity: archived ? 0.7 : 1,
                transition: 'transform var(--transition-fast), box-shadow var(--transition-fast)',
            }}
            onMouseEnter={e => (e.currentTarget.style.transform = 'translateY(-1px)')}
            onMouseLeave={e => (e.currentTarget.style.transform = 'translateY(0)')}
        >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <IconFolders size={18} stroke={1.5} style={{ color: 'var(--accent-primary)' }} />
                <div style={{ fontWeight: 600, fontSize: 15, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {project.name}
                </div>
                {archived && (
                    <span
                        className="badge"
                        style={{ fontSize: 10, padding: '2px 6px', background: 'var(--bg-tertiary)', color: 'var(--text-tertiary)' }}
                        title={t('projects.status.archived', 'Archived')}
                    >
                        <IconArchive size={10} stroke={2} style={{ verticalAlign: 'middle', marginRight: 3 }} />
                        {t('projects.status.archived', 'Archived')}
                    </span>
                )}
            </div>

            {project.description && (
                <div
                    style={{
                        fontSize: 12, color: 'var(--text-secondary)',
                        display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                        overflow: 'hidden',
                    }}
                >
                    {project.description}
                </div>
            )}

            {project.agents.length > 0 && (
                <div style={{ display: 'flex', alignItems: 'center', marginTop: 4 }}>
                    {project.agents.slice(0, 5).map((a, i) => (
                        <div
                            key={a.agent_id}
                            title={a.name}
                            style={{
                                width: 24, height: 24, borderRadius: '50%',
                                border: '2px solid var(--bg-secondary)',
                                marginLeft: i === 0 ? 0 : -8,
                                background: a.avatar_url
                                    ? `url(${a.avatar_url}) center/cover no-repeat`
                                    : 'var(--bg-tertiary)',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                fontSize: 10, color: 'var(--text-secondary)',
                                overflow: 'hidden', flexShrink: 0, fontWeight: 500,
                            }}
                        >
                            {!a.avatar_url && (a.name || '?').charAt(0).toUpperCase()}
                        </div>
                    ))}
                    {project.agents.length > 5 && (
                        <div
                            style={{
                                width: 24, height: 24, borderRadius: '50%',
                                border: '2px solid var(--bg-secondary)',
                                marginLeft: -8, background: 'var(--bg-tertiary)',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                fontSize: 9, color: 'var(--text-secondary)', fontWeight: 500,
                            }}
                            title={`+${project.agents.length - 5} more`}
                        >
                            +{project.agents.length - 5}
                        </div>
                    )}
                </div>
            )}

            <div style={{ display: 'flex', gap: 12, color: 'var(--text-tertiary)', fontSize: 11, marginTop: 'auto' }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    <IconUsers size={12} stroke={1.5} /> {project.agent_count}
                </span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    <IconFile size={12} stroke={1.5} /> {project.file_count}
                </span>
                <span style={{ marginLeft: 'auto' }}>{timeAgo(project.last_message_at || project.updated_at, t)}</span>
            </div>
        </div>
    );
}


export default function ProjectsList() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const queryClient = useQueryClient();

    const [search, setSearch] = useState('');
    const [showArchived, setShowArchived] = useState(false);
    const [showCreateModal, setShowCreateModal] = useState(false);

    const { data: projects = [], isLoading } = useQuery({
        queryKey: ['projects', search, showArchived],
        queryFn: () => projectApi.list({ q: search || undefined, archived: showArchived }),
    });

    return (
        <div style={{ padding: 24, maxWidth: 1200, margin: '0 auto' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 20 }}>
                <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>
                    {t('projects.title', 'Projects')}
                </h1>
                <button
                    className="btn btn-primary"
                    style={{ marginLeft: 'auto' }}
                    onClick={() => setShowCreateModal(true)}
                >
                    <IconPlus size={14} stroke={2} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                    {t('projects.newProject', 'New Project')}
                </button>
            </div>

            <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center' }}>
                <div style={{ position: 'relative', flex: 1, maxWidth: 360 }}>
                    <IconSearch
                        size={14} stroke={2}
                        style={{
                            position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)',
                            color: 'var(--text-tertiary)', pointerEvents: 'none',
                        }}
                    />
                    <input
                        className="input"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        placeholder={t('projects.searchPlaceholder', 'Search projects...')}
                        style={{ paddingLeft: 32, width: '100%' }}
                    />
                </div>
                <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-secondary)' }}>
                    <input
                        type="checkbox"
                        checked={showArchived}
                        onChange={e => setShowArchived(e.target.checked)}
                    />
                    {t('projects.showArchived', 'Include archived')}
                </label>
            </div>

            {isLoading ? (
                <div style={{ color: 'var(--text-tertiary)', padding: 32, textAlign: 'center' }}>
                    {t('common.loading', 'Loading...')}
                </div>
            ) : projects.length === 0 ? (
                <div style={{ color: 'var(--text-tertiary)', padding: 64, textAlign: 'center' }}>
                    <IconFolders size={32} stroke={1.2} style={{ opacity: 0.4, marginBottom: 8 }} />
                    <div>{t('projects.noProjects', 'No projects yet. Create the first one above.')}</div>
                </div>
            ) : (
                <div
                    style={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))',
                        gap: 12,
                    }}
                >
                    {projects.map(p => (
                        <ProjectCard
                            key={p.id}
                            project={p}
                            onClick={() => navigate(`/projects/${p.id}`)}
                        />
                    ))}
                </div>
            )}

            {showCreateModal && (
                <CreateProjectModal
                    onClose={() => setShowCreateModal(false)}
                    onCreated={(id) => {
                        queryClient.invalidateQueries({ queryKey: ['projects'] });
                        navigate(`/projects/${id}`);
                    }}
                />
            )}
        </div>
    );
}
