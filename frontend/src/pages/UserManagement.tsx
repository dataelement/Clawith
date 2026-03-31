/**
 * User Management — admin page to view and manage user quotas and roles.
 */
import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '../stores';

import { Pencil } from "lucide-react";
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
    primary_mobile?: string;
    feishu_open_id?: string;
    created_at?: string;
    source?: string;
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

const PAGE_SIZE = 15;

export default function UserManagement() {
    const { t, i18n } = useTranslation();
    const isChinese = i18n.language?.startsWith('zh');
    const { user: currentUser, setUser } = useAuthStore();

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
    const [changingRoleUserId, setChangingRoleUserId] = useState<string | null>(null);
    const [editingProfileUser, setEditingProfileUser] = useState<UserInfo | null>(null);

    // Search, sort & pagination
    const [searchQuery, setSearchQuery] = useState('');
    const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
    const [page, setPage] = useState(1);

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
            setToast(isChinese ? '配额已更新' : 'Quota updated');
            setTimeout(() => setToast(''), 2000);
            setEditingUserId(null);
            loadUsers();
        } catch (e: any) {
            setToast(`Error: ${e.message}`);
            setTimeout(() => setToast(''), 3000);
        }
        setSaving(false);
    };

    // ── Role change handler ──
    const handleRoleChange = async (userId: string, newRole: string) => {
        setChangingRoleUserId(userId);
        try {
            await fetchJson(`/users/${userId}/role`, {
                method: 'PATCH',
                body: JSON.stringify({ role: newRole }),
            });
            setToast(isChinese ? 'Role updated' : 'Role updated');
            setTimeout(() => setToast(''), 2000);
            // If changed own role, update auth store
            if (userId === currentUser?.id) {
                setUser({ ...currentUser, role: newRole as any });
            }
            loadUsers();
        } catch (e: any) {
            const detail = (() => { try { return JSON.parse(e.message)?.detail; } catch { return e.message; } })();
            setToast(`Error: ${detail || e.message}`);
            setTimeout(() => setToast(''), 4000);
        }
        setChangingRoleUserId(null);
    };

    const periodLabel = (period: string) => {
        if (isChinese) {
            const map: Record<string, string> = { permanent: '永久', daily: '每天', weekly: '每周', monthly: '每月' };
            return map[period] || period;
        }
        return PERIOD_OPTIONS.find(p => p.value === period)?.label || period;
    };

    // Role label & styling helpers
    const roleBadge = (role: string) => {
        const styles: Record<string, { bg: string; color: string; label: string; labelZh: string }> = {
            platform_admin: { bg: 'rgba(239,68,68,0.12)', color: '#ef4444', label: 'Platform Admin', labelZh: 'Platform Admin' },
            org_admin:      { bg: 'rgba(168,85,247,0.12)', color: '#a855f7', label: 'Admin', labelZh: 'Admin' },
        };
        const s = styles[role];
        if (!s) return null;
        return (
            <span style={{ marginLeft: '6px', fontSize: '10px', background: s.bg, color: s.color, borderRadius: '4px', padding: '1px 6px', fontWeight: 500 }}>
                {isChinese ? s.labelZh : s.label}
            </span>
        );
    };

    const formatDate = (iso?: string) => {
        if (!iso) return '-';
        const d = new Date(iso);
        return d.toLocaleString(isChinese ? 'zh-CN' : 'en-US', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    };

    // Search filter
    const filtered = searchQuery.trim()
        ? users.filter(u => {
            const q = searchQuery.toLowerCase();
            return (u.username?.toLowerCase().includes(q))
                || (u.display_name?.toLowerCase().includes(q))
                || (u.email?.toLowerCase().includes(q));
        })
        : users;

    // Sort
    const sorted = [...filtered].sort((a, b) => {
        const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
        const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
        return sortOrder === 'asc' ? ta - tb : tb - ta;
    });

    // Paginate
    const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
    const paged = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

    const toggleSort = () => {
        setSortOrder(o => o === 'asc' ? 'desc' : 'asc');
        setPage(1);
    };

    return (
        <div>
            {toast && (
                <div style={{
                    position: 'fixed', top: '20px', right: '20px', padding: '10px 20px',
                    borderRadius: '8px', background: !toast.startsWith('Error:') ? 'var(--success)' : 'var(--error)',
                    color: '#fff', fontSize: '13px', zIndex: 9999, transition: 'all 0.3s',
                }}>
                    {toast}
                </div>
            )}

            {editingProfileUser && (
                <EditUserModal
                    user={editingProfileUser}
                    onClose={() => setEditingProfileUser(null)}
                    onUpdated={() => { loadUsers(); setEditingProfileUser(null); }}
                />
            )}

            {loading ? (
                <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>
                    {t('common.loading')}...
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {/* Search bar */}
                    <div style={{ position: 'relative', marginBottom: '4px' }}>
                        <input
                            className="form-input"
                            type="text"
                            placeholder={isChinese ? '搜索用户名、显示名或邮箱…' : 'Search username, name or email…'}
                            value={searchQuery}
                            onChange={e => { setSearchQuery(e.target.value); setPage(1); }}
                            style={{
                                width: '100%', maxWidth: '360px', fontSize: '13px',
                                padding: '8px 12px 8px 12px',
                                background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)',
                                borderRadius: '8px',
                            }}
                        />
                        {searchQuery && (
                            <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginLeft: '12px' }}>
                                {isChinese ? `${filtered.length} / ${users.length} 位用户` : `${filtered.length} / ${users.length} users`}
                            </span>
                        )}
                    </div>

                    {/* Header */}
                    <div style={{
                        display: 'grid', gridTemplateColumns: '1.4fr 1.4fr 0.8fr 0.7fr 0.7fr 0.8fr 0.8fr 0.8fr 0.8fr 120px',
                        gap: '10px', padding: '10px 16px', fontSize: '11px', fontWeight: 600,
                        color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em',
                    }}>
                        <div>{t('enterprise.users.user', isChinese ? '用户' : 'User')}</div>
                        <div>{t('enterprise.users.email', 'Email')}</div>
                        {/* Created At with sort toggle */}
                        <div
                            style={{ cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', gap: '3px' }}
                            onClick={toggleSort}
                            title={isChinese ? '点击切换排序' : 'Click to toggle sort order'}
                        >
                            {isChinese ? '注册时间' : 'Joined'} {sortOrder === 'asc' ? '↑' : '↓'}
                        </div>
                        <div>{isChinese ? '角色' : 'Role'}</div>
                        <div>{isChinese ? '来源' : 'Source'}</div>
                        <div>{t('enterprise.users.msgQuota', isChinese ? '消息配额' : 'Msg Quota')}</div>
                        <div>{t('enterprise.users.period', isChinese ? '周期' : 'Period')}</div>
                        <div>{t('enterprise.users.agents', isChinese ? '数字员工' : 'Agents')}</div>
                        <div>{t('enterprise.users.ttl', 'TTL')}</div>
                        <div></div>
                    </div>

                    {paged.map(user => (
                        <div key={user.id}>
                            <div className="card" style={{
                                display: 'grid', gridTemplateColumns: '1.4fr 1.4fr 0.8fr 0.7fr 0.7fr 0.8fr 0.8fr 0.8fr 0.8fr 120px',
                                gap: '10px', alignItems: 'center', padding: '12px 16px',
                            }}>
                                <div>
                                    <div style={{ fontWeight: 500, fontSize: '14px' }}>
                                        {user.display_name || user.username}
                                        {roleBadge(user.role)}
                                    </div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>@{user.username}</div>
                                </div>
                                <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{user.email}</div>
                                <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{formatDate(user.created_at)}</div>
                                {/* Role selector — only for admin users, not for platform_admin targets */}
                                <div>
                                    {currentUser?.role && ['platform_admin', 'org_admin'].includes(currentUser.role) && user.role !== 'platform_admin' ? (
                                        <select
                                            className="form-input"
                                            value={user.role}
                                            disabled={changingRoleUserId === user.id}
                                            onChange={e => {
                                                const newRole = e.target.value;
                                                const confirmMsg = isChinese
                                                    ? `确认将 ${user.display_name || user.username} 的角色更改为 ${newRole === 'org_admin' ? 'Admin' : 'Member'}？`
                                                    : `Change ${user.display_name || user.username}'s role to ${newRole === 'org_admin' ? 'Admin' : 'Member'}?`;
                                                if (confirm(confirmMsg)) handleRoleChange(user.id, newRole);
                                            }}
                                            style={{ fontSize: '11px', padding: '2px 4px', width: '100%', minWidth: 0 }}
                                        >
                                            <option value="member">{isChinese ? 'Member' : 'Member'}</option>
                                            <option value="org_admin">{isChinese ? 'Admin' : 'Admin'}</option>
                                        </select>
                                    ) : (
                                        <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                                            {user.role === 'platform_admin' ? 'Platform Admin'
                                                : user.role === 'org_admin' ? 'Admin' : 'Member'}
                                        </span>
                                    )}
                                </div>
                                <div>
                                    {user.source === 'feishu' ? (
                                        <span style={{ fontSize: '10px', background: 'rgba(58,132,255,0.12)', color: '#3a84ff', borderRadius: '4px', padding: '2px 7px', whiteSpace: 'nowrap' }}>
                                            飞书
                                        </span>
                                    ) : (
                                        <span style={{ fontSize: '10px', background: 'rgba(0,180,120,0.12)', color: 'var(--success)', borderRadius: '4px', padding: '2px 7px', whiteSpace: 'nowrap' }}>
                                            {isChinese ? '注册' : 'Reg'}
                                        </span>
                                    )}
                                </div>
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
                                    <div style={{ display: 'flex', gap: '4px', flexDirection: 'column' }}>
                                    <button
                                        className="btn btn-ghost"
                                        style={{ padding: '2px 8px', fontSize: '11px', color: 'var(--accent-primary)' }}
                                        onClick={() => setEditingProfileUser(user)}
                                    >
                                        {isChinese ? '编辑信息' : 'Edit Info'}
                                    </button>
                                    <button
                                        className="btn btn-secondary"
                                        style={{ padding: '2px 8px', fontSize: '11px' }}
                                        onClick={() => editingUserId === user.id ? setEditingUserId(null) : startEdit(user)}
                                    >
                                        {editingUserId === user.id ? t('common.cancel') : <><Pencil size={14} /> {isChinese ? '配额' : 'Quota'}</>}
                                    </button>
                                </div>
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
                                                {t('enterprise.users.msgLimit', isChinese ? '消息限额' : 'Message Limit')}
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
                                                {t('enterprise.users.period', isChinese ? '重置周期' : 'Period')}
                                            </label>
                                            <select
                                                className="form-input"
                                                value={editForm.quota_message_period}
                                                onChange={e => setEditForm({ ...editForm, quota_message_period: e.target.value })}
                                            >
                                                {PERIOD_OPTIONS.map(p => (
                                                    <option key={p.value} value={p.value}>{periodLabel(p.value)}</option>
                                                ))}
                                            </select>
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label" style={{ fontSize: '11px' }}>
                                                {t('enterprise.users.maxAgents', isChinese ? '最多数字员工' : 'Max Agents')}
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
                                                {t('enterprise.users.agentTTL', isChinese ? '员工存活时长(h)' : 'Agent TTL (hours)')}
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

                    {/* Pagination */}
                    {totalPages > 1 && (
                        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px', marginTop: '16px' }}>
                            <button
                                className="btn btn-secondary"
                                style={{ padding: '4px 10px', fontSize: '12px' }}
                                disabled={page <= 1}
                                onClick={() => setPage(p => p - 1)}
                            >
                                ‹ {isChinese ? '上一页' : 'Prev'}
                            </button>
                            {Array.from({ length: totalPages }, (_, i) => i + 1).map(p => (
                                <button
                                    key={p}
                                    className={`btn ${p === page ? 'btn-primary' : 'btn-secondary'}`}
                                    style={{ padding: '4px 10px', fontSize: '12px', minWidth: '32px' }}
                                    onClick={() => setPage(p)}
                                >
                                    {p}
                                </button>
                            ))}
                            <button
                                className="btn btn-secondary"
                                style={{ padding: '4px 10px', fontSize: '12px' }}
                                disabled={page >= totalPages}
                                onClick={() => setPage(p => p + 1)}
                            >
                                {isChinese ? '下一页' : 'Next'} ›
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}


// ─── Edit User Profile Modal ───────────────────────────────
interface EditUserModalProps {
    user: UserInfo;
    onClose: () => void;
    onUpdated: () => void;
}

function EditUserModal({ user, onClose, onUpdated }: EditUserModalProps) {
    const { t, i18n } = useTranslation();
    const isChinese = i18n.language?.startsWith('zh');
    const { user: currentUser } = useAuthStore();

    const [form, setForm] = useState({
        display_name: user.display_name || '',
        email: user.email || '',
        primary_mobile: user.primary_mobile || '',
        is_active: user.is_active,
        new_password: '',
    });
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const handleSave = async () => {
        if (!form.display_name.trim()) {
            setError(isChinese ? '显示名称不能为空' : 'Display name is required');
            return;
        }
        setSaving(true);
        setError('');
        try {
            const payload: any = {
                    display_name: form.display_name.trim(),
                    email: form.email.trim(),
                    primary_mobile: form.primary_mobile.trim() || null,
                    is_active: form.is_active,
                };
            if (form.new_password.trim()) {
                payload.new_password = form.new_password.trim();
            }
            await fetchJson(`/users/${user.id}/profile`, {
                method: 'PATCH',
                body: JSON.stringify(payload),
            });
            onUpdated();
            onClose();
        } catch (e: any) {
            const detail = (() => {
                try { return JSON.parse(e.message)?.detail; }
                catch { return e.message; }
            })();
            setError(detail || (isChinese ? '保存失败' : 'Save failed'));
        }
        setSaving(false);
    };

    const isReadOnly = currentUser?.role === 'org_admin' && user.role === 'platform_admin';

    return (
        <div style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 10001,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            backdropFilter: 'blur(4px)',
        }} onClick={onClose}>
            <div className="card" style={{
                padding: '24px', maxWidth: '440px', width: '90%',
                boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
            }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
                    <h2 style={{ fontSize: '16px', fontWeight: 600 }}>
                        {isChinese ? '编辑用户信息' : 'Edit User'}
                    </h2>
                    <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                        </svg>
                    </button>
                </div>

                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                    {isChinese ? '用户名' : 'Username'}: <span style={{ fontFamily: 'monospace', color: 'var(--text-secondary)' }}>@{user.username}</span>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                    <div className="form-group">
                        <label className="form-label" style={{ fontSize: '12px' }}>
                            {isChinese ? '显示名称' : 'Display Name'} <span style={{ color: 'var(--error)' }}>*</span>
                        </label>
                        <input
                            className="form-input"
                            value={form.display_name}
                            onChange={e => setForm({ ...form, display_name: e.target.value })}
                            disabled={isReadOnly}
                            style={{ fontSize: '13px' }}
                        />
                    </div>

                    <div className="form-group">
                        <label className="form-label" style={{ fontSize: '12px' }}>
                            {isChinese ? '邮箱' : 'Email'}
                        </label>
                        <input
                            className="form-input"
                            type="email"
                            value={form.email}
                            onChange={e => setForm({ ...form, email: e.target.value })}
                            disabled={isReadOnly}
                            style={{ fontSize: '13px' }}
                        />
                    </div>

                    <div className="form-group">
                        <label className="form-label" style={{ fontSize: '12px' }}>
                            {isChinese ? '手机号' : 'Mobile'}
                        </label>
                        <input
                            className="form-input"
                            type="tel"
                            value={form.primary_mobile}
                            onChange={e => setForm({ ...form, primary_mobile: e.target.value })}
                            disabled={isReadOnly}
                            placeholder={isChinese ? '可选' : 'Optional'}
                            style={{ fontSize: '13px' }}
                        />
                    </div>

                    <div className="form-group">
                        <label className="form-label" style={{ fontSize: '12px' }}>
                            {t('users.newPassword', isChinese ? '新密码' : 'New Password')}
                        </label>
                        <input
                            className="form-input"
                            type="password"
                            value={form.new_password}
                            onChange={e => setForm({ ...form, new_password: e.target.value })}
                            disabled={isReadOnly}
                            placeholder={t('users.newPasswordPlaceholder', isChinese ? '留空则不修改密码' : 'Leave blank to keep current password')}
                            style={{ fontSize: '13px' }}
                            autoComplete="new-password"
                        />
                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                            {t('users.newPasswordHint', isChinese ? '可选。填写后将重置用户密码。' : 'Optional. If provided, user password will be reset.')}
                        </div>
                    </div>

                    {user.role !== 'platform_admin' && (
                        <div style={{
                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                            padding: '10px 12px', borderRadius: '8px',
                            background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)',
                        }}>
                            <div>
                                <div style={{ fontSize: '13px', fontWeight: 500 }}>
                                    {isChinese ? '账号状态' : 'Account Status'}
                                </div>
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                    {isChinese ? '禁用后用户无法登录' : 'Disabled users cannot log in'}
                                </div>
                            </div>
                            <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer', flexShrink: 0 }}>
                                <input
                                    type="checkbox"
                                    checked={form.is_active}
                                    onChange={e => setForm({ ...form, is_active: e.target.checked })}
                                    style={{ opacity: 0, width: 0, height: 0 }}
                                />
                                <span style={{
                                    position: 'absolute', inset: 0,
                                    background: form.is_active ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                                    borderRadius: '11px', transition: 'background 0.2s',
                                }}>
                                    <span style={{
                                        position: 'absolute',
                                        left: form.is_active ? '20px' : '2px', top: '2px',
                                        width: '18px', height: '18px', background: '#fff',
                                        borderRadius: '50%', transition: 'left 0.2s',
                                    }} />
                                </span>
                            </label>
                        </div>
                    )}
                </div>

                {error && (
                    <div style={{ color: 'var(--error)', fontSize: '12px', marginTop: '12px' }}>{error}</div>
                )}

                <div style={{ display: 'flex', gap: '8px', marginTop: '20px' }}>
                    <button className="btn btn-secondary" style={{ flex: 1 }} onClick={onClose} disabled={saving}>
                        {t('common.cancel', 'Cancel')}
                    </button>
                    {!isReadOnly && (
                        <button className="btn btn-primary" style={{ flex: 1 }} onClick={handleSave} disabled={saving}>
                            {saving ? t('common.loading', 'Loading...') : t('common.save', 'Save')}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}
