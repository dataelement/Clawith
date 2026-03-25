import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { workflowApi } from '../services/api';

interface WorkflowItem {
    id: string;
    title: string;
    status: string;
    created_at: string;
    completed_at: string | null;
}

export default function WorkflowList() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const [instruction, setInstruction] = useState('');
    const [workflows, setWorkflows] = useState<WorkflowItem[]>([]);
    const [loading, setLoading] = useState(false);
    const [creating, setCreating] = useState(false);

    const fetchList = async () => {
        setLoading(true);
        try {
            const res = await workflowApi.list();
            setWorkflows(res.items || []);
        } catch { /* ignore */ }
        setLoading(false);
    };

    useEffect(() => { fetchList(); }, []);

    const handleCreate = async () => {
        if (!instruction.trim() || creating) return;
        setCreating(true);
        try {
            const res = await workflowApi.create(instruction.trim());
            navigate(`/app/workflows/${res.id}`);
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Failed');
        }
        setCreating(false);
    };

    const statusLabel = (s: string) => {
        const map: Record<string, string> = {
            planning: t('workflow.status.planning', 'Planning'),
            running: t('workflow.status.running', 'Running'),
            done: t('workflow.status.done', 'Completed'),
            failed: t('workflow.status.failed', 'Failed'),
        };
        return map[s] || s;
    };

    const statusColor = (s: string) => {
        const map: Record<string, string> = { planning: '#f59e0b', running: '#3b82f6', done: '#10b981', failed: '#ef4444' };
        return map[s] || '#888';
    };

    return (
        <div style={{ maxWidth: 800, margin: '0 auto', padding: '24px 16px' }}>
            <h2 style={{ marginBottom: 8 }}>{t('workflow.title', 'Workflows')}</h2>
            <p style={{ color: 'var(--text-secondary)', marginBottom: 24, fontSize: 14 }}>
                {t('workflow.subtitle', 'Enter your business goal and AI agents will collaborate to complete it')}
            </p>

            <div style={{ display: 'flex', gap: 8, marginBottom: 32 }}>
                <input
                    value={instruction}
                    onChange={(e) => setInstruction(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                    placeholder={t('workflow.placeholder', 'e.g. Help me develop the German LED lighting market')}
                    style={{
                        flex: 1, padding: '12px 16px', borderRadius: 8,
                        border: '1px solid var(--border)', background: 'var(--bg-secondary)',
                        color: 'var(--text-primary)', fontSize: 15,
                    }}
                />
                <button
                    onClick={handleCreate}
                    disabled={creating || !instruction.trim()}
                    style={{
                        padding: '12px 24px', borderRadius: 8, border: 'none',
                        background: creating ? '#666' : 'var(--accent)', color: '#fff',
                        cursor: creating ? 'wait' : 'pointer', fontWeight: 600, fontSize: 15,
                        whiteSpace: 'nowrap',
                    }}
                >
                    {creating ? t('workflow.creating', 'Starting...') : t('workflow.create', 'Start Workflow')}
                </button>
            </div>

            {loading ? (
                <p style={{ color: 'var(--text-tertiary)', textAlign: 'center' }}>{t('workflow.loading', 'Loading...')}</p>
            ) : workflows.length === 0 ? (
                <p style={{ color: 'var(--text-tertiary)', textAlign: 'center', padding: 40 }}>
                    {t('workflow.empty', 'No workflows yet. Enter your goal to get started.')}
                </p>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {workflows.map((w) => (
                        <div
                            key={w.id}
                            onClick={() => navigate(`/app/workflows/${w.id}`)}
                            style={{
                                padding: '16px 20px', borderRadius: 8,
                                border: '1px solid var(--border)', background: 'var(--bg-secondary)',
                                cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                transition: 'border-color 0.15s',
                            }}
                            onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                            onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                        >
                            <div>
                                <div style={{ fontWeight: 500, marginBottom: 4 }}>{w.title}</div>
                                <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                                    {new Date(w.created_at).toLocaleString()}
                                </div>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span style={{
                                    fontSize: 12, padding: '4px 10px', borderRadius: 12,
                                    background: statusColor(w.status) + '20', color: statusColor(w.status), fontWeight: 500,
                                }}>
                                    {statusLabel(w.status)}
                                </span>
                                <button
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        if (confirm(t('workflow.deleteConfirm', 'Delete this workflow?'))) {
                                            workflowApi.delete(w.id).then(fetchList).catch(() => {});
                                        }
                                    }}
                                    style={{
                                        padding: '4px 8px', borderRadius: 6, border: '1px solid var(--border)',
                                        background: 'transparent', color: 'var(--text-tertiary)',
                                        cursor: 'pointer', fontSize: 12,
                                    }}
                                    title={t('common.delete', 'Delete')}
                                >
                                    ×
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
