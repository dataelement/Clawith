/**
 * User Management — admin page to view and manage user quotas.
 */
import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';

interface UserInfo {
    id: string;
    username: string;
    email: string;
    display_name: string;
    role: string;
    is_active: boolean;
    quota_message_limit: number;
    quota_message_period: string;
    quota_messages_used: number;
    quota_max_agents: number;
    quota_agent_ttl_hours: number;
    agents_count: number;
}

const API_PREFIX = '/api';

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
    const token = localStorage.getItem('token');
    const res = await fetch(`${API_PREFIX}${url}`, {
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        ...options,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

const PERIOD_OPTIONS = [
    { value: 'permanent', label: 'Permanent' },
    { value: 'daily', label: 'Daily' },
    { value: 'weekly', label: 'Weekly' },
    { value: 'monthly', label: 'Monthly' },
];

export default function UserManagement() {
    const { t } = useTranslation();
    const [users, setUsers] = useState<UserInfo[]>([]);
    const [loading, setLoading] = useState(true);
    const [editingUserId, setEditingUserId] = useState<string | null>(null);
    const [editForm, setEditForm] = useState({
        quota_message_limit: 50,
        quota_message_period: 'permanent',
        quota_max_agents: 2,
        quota_agent_ttl_hours: 48,
    });
    const [saving, setSaving] = useState(false);
    const [toast, setToast] = useState('');

    const loadUsers = async () => {
        setLoading(true);
        try {
            const tenantId = localStorage.getItem('current_tenant_id') || '';
            const data = await fetchJson<UserInfo[]>(`/users/${tenantId ? `?tenant_id=${tenantId}` : ''}`);
            setUsers(data);
        } catch (e) {
            console.error('Failed to load users', e);
        }
        setLoading(false);
    };

    useEffect(() => { loadUsers(); }, []);

    const startEdit = (user: UserInfo) => {
        setEditingUserId(user.id);
        setEditForm({
            quota_message_limit: user.quota_message_limit,
            quota_message_period: user.quota_message_period,
            quota_max_agents: user.quota_max_agents,
            quota_agent_ttl_hours: user.quota_agent_ttl_hours,
        });
    };

    const handleSave = async () => {
        if (!editingUserId) return;
        setSaving(true);
        try {
            await fetchJson(`/users/${editingUserId}/quota`, {
                method: 'PATCH',
                body: JSON.stringify(editForm),
            });
            setToast('✅ Quota updated');
            setTimeout(() => setToast(''), 2000);
            setEditingUserId(null);
            loadUsers();
        } catch (e: any) {
            setToast(`❌ ${e.message}`);
            setTimeout(() => setToast(''), 3000);
        }
        setSaving(false);
    };

    const periodLabel = (period: string) => PERIOD_OPTIONS.find(p => p.value === period)?.label || period;

    return (
        <div>
            {toast && (
                <div style={{
                    position: 'fixed', top: '20px', right: '20px', padding: '10px 20px',
                    borderRadius: '8px', background: toast.startsWith('✅') ? 'var(--success)' : 'var(--error)',
                    color: '#fff', fontSize: '13px', zIndex: 9999, transition: 'all 0.3s',
                }}>
                    {toast}
                </div>
            )}

            {loading ? (
                <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>
                    {t('common.loading')}...
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {/* Header */}
                    <div style={{
                        display: 'grid', gridTemplateColumns: '1.5fr 1.5fr 1fr 1fr 1fr 1fr 120px',
                        gap: '12px', padding: '10px 16px', fontSize: '11px', fontWeight: 600,
                        color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em',
                    }}>
                        <div>{t('enterprise.users.user', 'User')}</div>
                        <div>{t('enterprise.users.email', 'Email')}</div>
                        <div>{t('enterprise.users.msgQuota', 'Msg Quota')}</div>
                        <div>{t('enterprise.users.period', 'Period')}</div>
                        <div>{t('enterprise.users.agents', 'Agents')}</div>
                        <div>{t('enterprise.users.ttl', 'Agent TTL')}</div>
                        <div></div>
                    </div>

                    {users.map(user => (
                        <div key={user.id}>
                            <div className="card" style={{
                                display: 'grid', gridTemplateColumns: '1.5fr 1.5fr 1fr 1fr 1fr 1fr 120px',
                                gap: '12px', alignItems: 'center', padding: '12px 16px',
                            }}>
                                <div>
                                    <div style={{ fontWeight: 500, fontSize: '14px' }}>
                                        {user.display_name || user.username}
                                        {user.role === 'platform_admin' && (
                                            <span style={{ marginLeft: '6px', fontSize: '10px', background: 'var(--accent-color)', color: '#fff', borderRadius: '4px', padding: '1px 6px' }}>Admin</span>
                                        )}
                                    </div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>@{user.username}</div>
                                </div>
                                <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{user.email}</div>
                                <div>
                                    <span style={{ fontSize: '13px', fontWeight: 500 }}>{user.quota_messages_used}</span>
                                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}> / {user.quota_message_limit}</span>
                                </div>
                                <div>
                                    <span className="badge badge-info" style={{ fontSize: '10px' }}>{periodLabel(user.quota_message_period)}</span>
                                </div>
                                <div>
                                    <span style={{ fontSize: '13px', fontWeight: 500 }}>{user.agents_count}</span>
                                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}> / {user.quota_max_agents}</span>
                                </div>
                                <div style={{ fontSize: '12px' }}>{user.quota_agent_ttl_hours}h</div>
                                <div>
                                    {user.role !== 'platform_admin' && (
                                        <button
                                            className="btn btn-secondary"
                                            style={{ padding: '4px 10px', fontSize: '11px' }}
                                            onClick={() => editingUserId === user.id ? setEditingUserId(null) : startEdit(user)}
                                        >
                                            {editingUserId === user.id ? t('common.cancel') : '✏️ Edit'}
                                        </button>
                                    )}
                                </div>
                            </div>

                            {/* Inline edit form */}
                            {editingUserId === user.id && (
                                <div className="card" style={{
                                    marginTop: '4px', padding: '16px',
                                    background: 'var(--bg-secondary)',
                                    borderLeft: '3px solid var(--accent-color)',
                                }}>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: '16px' }}>
                                        <div className="form-group">
                                            <label className="form-label" style={{ fontSize: '11px' }}>
                                                {t('enterprise.users.msgLimit', 'Message Limit')}
                                            </label>
                                            <input
                                                className="form-input"
                                                type="number" min={0}
                                                value={editForm.quota_message_limit}
                                                onChange={e => setEditForm({ ...editForm, quota_message_limit: Number(e.target.value) })}
                                            />
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label" style={{ fontSize: '11px' }}>
                                                {t('enterprise.users.period', 'Period')}
                                            </label>
                                            <select
                                                className="form-input"
                                                value={editForm.quota_message_period}
                                                onChange={e => setEditForm({ ...editForm, quota_message_period: e.target.value })}
                                            >
                                                {PERIOD_OPTIONS.map(p => (
                                                    <option key={p.value} value={p.value}>{p.label}</option>
                                                ))}
                                            </select>
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label" style={{ fontSize: '11px' }}>
                                                {t('enterprise.users.maxAgents', 'Max Agents')}
                                            </label>
                                            <input
                                                className="form-input"
                                                type="number" min={0}
                                                value={editForm.quota_max_agents}
                                                onChange={e => setEditForm({ ...editForm, quota_max_agents: Number(e.target.value) })}
                                            />
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label" style={{ fontSize: '11px' }}>
                                                {t('enterprise.users.agentTTL', 'Agent TTL (hours)')}
                                            </label>
                                            <input
                                                className="form-input"
                                                type="number" min={1}
                                                value={editForm.quota_agent_ttl_hours}
                                                onChange={e => setEditForm({ ...editForm, quota_agent_ttl_hours: Number(e.target.value) })}
                                            />
                                        </div>
                                    </div>
                                    <div style={{ marginTop: '12px', display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                                        <button className="btn btn-secondary" onClick={() => setEditingUserId(null)}>
                                            {t('common.cancel')}
                                        </button>
                                        <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                                            {saving ? t('common.loading') : t('common.save', 'Save')}
                                        </button>
                                    </div>
                                </div>
                            )}
                        </div>
                    ))}

                    {users.length === 0 && (
                        <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>
                            {t('common.noData')}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
