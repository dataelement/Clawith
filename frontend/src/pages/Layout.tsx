import { useState, useEffect } from 'react';
import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../stores';
import { agentApi } from '../services/api';

/* ────── SVG Icons ────── */
const SidebarIcons = {
    home: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2.5 6.5L8 2l5.5 4.5V13a1 1 0 01-1 1h-3V10H6.5v4h-3a1 1 0 01-1-1V6.5z" />
        </svg>
    ),
    plus: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <path d="M8 3v10M3 8h10" />
        </svg>
    ),
    settings: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="8" cy="8" r="2" />
            <path d="M13.5 8a5.5 5.5 0 00-.3-1.8l1.3-1-1.2-2-1.5.6a5.5 5.5 0 00-1.6-.9L9.8 1.5H7.6l-.4 1.4a5.5 5.5 0 00-1.6.9L4 3.2 2.8 5.2l1.3 1A5.5 5.5 0 003.8 8c0 .6.1 1.2.3 1.8l-1.3 1 1.2 2 1.5-.6c.5.4 1 .7 1.6.9l.4 1.4h2.2l.4-1.4c.6-.2 1.1-.5 1.6-.9l1.5.6 1.2-2-1.3-1c.2-.6.3-1.2.3-1.8z" />
        </svg>
    ),
    user: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="8" cy="5.5" r="2.5" />
            <path d="M3 14v-1a4 4 0 018 0v1" />
        </svg>
    ),
    sun: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <circle cx="8" cy="8" r="3" />
            <path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1" />
        </svg>
    ),
    moon: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M13.5 8.5a5.5 5.5 0 01-8-4.5 5.5 5.5 0 003 10c2 0 3.8-1 4.8-2.7a4 4 0 01.2-2.8z" />
        </svg>
    ),
    logout: (
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 14H3a1 1 0 01-1-1V3a1 1 0 011-1h3M11 11l3-3-3-3M14 8H6" />
        </svg>
    ),
    globe: (
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="8" cy="8" r="6" />
            <path d="M2 8h12M8 2a10 10 0 013 6 10 10 0 01-3 6 10 10 0 01-3-6 10 10 0 013-6z" />
        </svg>
    ),
    collapse: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M15 18l-6-6 6-6" />
        </svg>
    ),
    expand: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 18l6-6-6-6" />
        </svg>
    ),
    bell: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 6a4 4 0 018 0c0 2 1 3.5 1.5 4.5H2.5C3 9.5 4 8 4 6z" />
            <path d="M6.5 12.5a1.5 1.5 0 003 0" />
        </svg>
    ),
};

const fetchJson = async <T,>(url: string): Promise<T> => {
    const token = localStorage.getItem('token');
    const res = await fetch(`/api${url}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!res.ok) return [] as T;
    return res.json();
};

/* Compute display badge status for an agent */
const getAgentBadgeStatus = (agent: any): string | null => {
    if (agent.status === 'error') return 'error';
    if (agent.status === 'creating') return 'creating';
    // OpenClaw disconnected detection: 60 min timeout
    if (agent.agent_type === 'openclaw' && agent.status === 'running' && agent.openclaw_last_seen) {
        const elapsed = Date.now() - new Date(agent.openclaw_last_seen).getTime();
        if (elapsed > 60 * 60 * 1000) return 'disconnected';
    }
    // idle / running / stopped → no badge
    return null;
};

/* ────── Account Settings Modal ────── */
function AccountSettingsModal({ user, onClose, isChinese }: { user: any; onClose: () => void; isChinese: boolean }) {
    const { setUser } = useAuthStore();
    const [username, setUsername] = useState(user?.username || '');
    const [displayName, setDisplayName] = useState(user?.display_name || '');
    const [oldPassword, setOldPassword] = useState('');
    const [newPassword, setNewPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState('');
    const [msgType, setMsgType] = useState<'success' | 'error'>('success');

    const showMsg = (text: string, type: 'success' | 'error' = 'success') => {
        setMsg(text); setMsgType(type); setTimeout(() => setMsg(''), 3000);
    };

    const handleSaveProfile = async () => {
        setSaving(true);
        try {
            const token = localStorage.getItem('token');
            const body: any = {};
            if (username !== user?.username) body.username = username;
            if (displayName !== user?.display_name) body.display_name = displayName;
            if (Object.keys(body).length === 0) { showMsg(isChinese ? '没有变更' : 'No changes', 'error'); setSaving(false); return; }
            const res = await fetch('/api/auth/me', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify(body),
            });
            if (!res.ok) { const err = await res.json().catch(() => ({ detail: 'Failed' })); throw new Error(err.detail); }
            const updated = await res.json();
            setUser(updated);
            showMsg(isChinese ? '个人信息已更新' : 'Profile updated');
        } catch (e: any) { showMsg(e.message || 'Failed', 'error'); }
        setSaving(false);
    };

    const handleChangePassword = async () => {
        if (!oldPassword || !newPassword) { showMsg(isChinese ? '请填写所有密码字段' : 'Fill all password fields', 'error'); return; }
        if (newPassword.length < 6) { showMsg(isChinese ? '新密码至少 6 个字符' : 'Min 6 characters', 'error'); return; }
        if (newPassword !== confirmPassword) { showMsg(isChinese ? '两次密码不一致' : 'Passwords do not match', 'error'); return; }
        setSaving(true);
        try {
            const token = localStorage.getItem('token');
            const res = await fetch('/api/auth/me/password', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
            });
            if (!res.ok) { const err = await res.json().catch(() => ({ detail: 'Failed' })); throw new Error(err.detail); }
            showMsg(isChinese ? '密码已修改' : 'Password changed');
            setOldPassword(''); setNewPassword(''); setConfirmPassword('');
        } catch (e: any) { showMsg(e.message || 'Failed', 'error'); }
        setSaving(false);
    };

    const inputStyle = { width: '100%', fontSize: '13px' };
    const labelStyle = { display: 'block' as const, fontSize: '12px', fontWeight: 500, marginBottom: '4px', color: 'var(--text-secondary)' };

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 10000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
            <div style={{ background: 'var(--bg-primary)', borderRadius: '12px', border: '1px solid var(--border-subtle)', width: '420px', maxHeight: '90vh', overflow: 'auto', padding: '24px', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
                    <h3 style={{ margin: 0 }}>{isChinese ? '账户设置' : 'Account Settings'}</h3>
                    <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', fontSize: '18px', cursor: 'pointer', padding: '4px 8px' }}>×</button>
                </div>
                {msg && <div style={{ padding: '8px 12px', borderRadius: '6px', fontSize: '12px', marginBottom: '16px', background: msgType === 'success' ? 'rgba(0,180,120,0.12)' : 'rgba(255,80,80,0.12)', color: msgType === 'success' ? 'var(--success)' : 'var(--error)' }}>{msg}</div>}
                {/* Profile */}
                <h4 style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-secondary)' }}>{isChinese ? '个人信息' : 'Profile'}</h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '20px' }}>
                    <div><label style={labelStyle}>{isChinese ? '用户名' : 'Username'}</label><input className="form-input" value={username} onChange={e => setUsername(e.target.value)} style={inputStyle} /></div>
                    <div><label style={labelStyle}>{isChinese ? '显示名称' : 'Display Name'}</label><input className="form-input" value={displayName} onChange={e => setDisplayName(e.target.value)} style={inputStyle} /></div>
                    <div style={{ display: 'flex', justifyContent: 'flex-end' }}><button className="btn btn-primary" onClick={handleSaveProfile} disabled={saving} style={{ padding: '6px 16px', fontSize: '12px' }}>{saving ? '...' : (isChinese ? '保存' : 'Save')}</button></div>
                </div>
                <div style={{ borderTop: '1px solid var(--border-subtle)', marginBottom: '20px' }} />
                {/* Password */}
                <h4 style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-secondary)' }}>{isChinese ? '修改密码' : 'Change Password'}</h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                    <div><label style={labelStyle}>{isChinese ? '当前密码' : 'Current Password'}</label><input className="form-input" type="password" value={oldPassword} onChange={e => setOldPassword(e.target.value)} style={inputStyle} /></div>
                    <div><label style={labelStyle}>{isChinese ? '新密码' : 'New Password'}</label><input className="form-input" type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} placeholder={isChinese ? '至少 6 个字符' : 'Min 6 characters'} style={inputStyle} /></div>
                    <div><label style={labelStyle}>{isChinese ? '确认新密码' : 'Confirm New Password'}</label><input className="form-input" type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} style={inputStyle} /></div>
                    <div style={{ display: 'flex', justifyContent: 'flex-end' }}><button className="btn btn-primary" onClick={handleChangePassword} disabled={saving} style={{ padding: '6px 16px', fontSize: '12px' }}>{saving ? '...' : (isChinese ? '修改密码' : 'Change Password')}</button></div>
                </div>
            </div>
        </div>
    );
}

/* ────── Version Display (runtime) ────── */
function VersionDisplay() {
    const [info, setInfo] = useState<{ version?: string; commit?: string }>({});
    useEffect(() => {
        fetch('/api/version').then(r => r.json()).then(setInfo).catch(() => {});
    }, []);
    if (!info.version) return null;
    return (
        <div style={{ textAlign: 'center', fontSize: '10px', color: 'var(--text-quaternary)', marginTop: '8px', letterSpacing: '0.3px' }}>
            v{info.version}
            {info.commit && <span style={{ opacity: 0.6 }}> ({info.commit})</span>}
        </div>
    );
}

export default function Layout() {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const { user, logout } = useAuthStore();
    const queryClient = useQueryClient();
    const isChinese = i18n.language?.startsWith('zh');
    const [showAccountSettings, setShowAccountSettings] = useState(false);
    const [showNotifications, setShowNotifications] = useState(false);
    const [notifCategory, setNotifCategory] = useState<string>('all');

    // Notification polling
    const { data: unreadCount = 0 } = useQuery({
        queryKey: ['notifications-unread'],
        queryFn: async () => {
            const res = await fetchJson<{ unread_count: number }>('/notifications/unread-count');
            return (res as any)?.unread_count || 0;
        },
        refetchInterval: 30000,
        enabled: !!user,
    });
    const { data: notifications = [], refetch: refetchNotifications } = useQuery({
        queryKey: ['notifications', notifCategory],
        queryFn: () => fetchJson<any[]>(`/notifications?limit=50${notifCategory !== 'all' ? `&category=${notifCategory}` : ''}`),
        enabled: !!user && showNotifications,
    });
    const markAllRead = async () => {
        const token = localStorage.getItem('token');
        await fetch('/api/notifications/read-all', { method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {} });
        queryClient.invalidateQueries({ queryKey: ['notifications-unread'] });
        queryClient.invalidateQueries({ queryKey: ['notifications'] });
    };
    const markOneRead = async (id: string) => {
        const token = localStorage.getItem('token');
        await fetch(`/api/notifications/${id}/read`, { method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {} });
        queryClient.invalidateQueries({ queryKey: ['notifications-unread'] });
        queryClient.invalidateQueries({ queryKey: ['notifications'] });
    };

    // Theme
    const [theme, setTheme] = useState<'dark' | 'light'>(() => {
        return (localStorage.getItem('theme') as 'dark' | 'light') || 'dark';
    });

    useEffect(() => {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);
    }, [theme]);

    const toggleTheme = () => setTheme(prev => prev === 'dark' ? 'light' : 'dark');

    // Sidebar collapse state
    const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => {
        return localStorage.getItem('sidebar_collapsed') === 'true';
    });

    const toggleSidebar = () => {
        setIsSidebarCollapsed(prev => {
            const newState = !prev;
            localStorage.setItem('sidebar_collapsed', String(newState));
            return newState;
        });
    };

    // Sidebar agent search & pin
    const [sidebarSearch, setSidebarSearch] = useState('');
    const [pinnedAgents, setPinnedAgents] = useState<Set<string>>(() => {
        try {
            const stored = localStorage.getItem('pinned_agents');
            return stored ? new Set(JSON.parse(stored)) : new Set();
        } catch { return new Set(); }
    });
    const togglePin = (agentId: string) => {
        setPinnedAgents(prev => {
            const next = new Set(prev);
            if (next.has(agentId)) next.delete(agentId);
            else next.add(agentId);
            localStorage.setItem('pinned_agents', JSON.stringify([...next]));
            return next;
        });
    };

    // Use user's own tenant_id directly (no switching)
    const currentTenant = user?.tenant_id || '';

    // Keep tenant in localStorage for other components that read it
    useEffect(() => {
        if (currentTenant) {
            localStorage.setItem('current_tenant_id', currentTenant);
        }
    }, [currentTenant]);

    const { data: agents = [] } = useQuery({
        queryKey: ['agents', currentTenant],
        queryFn: () => agentApi.list(currentTenant || undefined),
        refetchInterval: 30000,
    });

    const handleLogout = () => {
        logout();
        navigate('/login');
    };

    const toggleLang = () => {
        i18n.changeLanguage(i18n.language === 'zh' ? 'en' : 'zh');
    };

    return (
        <div className="app-layout">
            <nav className={`sidebar ${isSidebarCollapsed ? 'collapsed' : ''}`}>
                <div className="sidebar-top">
                    <div className="sidebar-logo">
                        <img src={theme === 'dark' ? '/logo-white.png' : '/logo-black.png'} alt="" style={{ width: 22, height: 22 }} />
                        <span className="sidebar-logo-text">Clawith</span>
                    </div>



                    <div className="sidebar-section">
                        <NavLink to="/plaza" className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}>
                            <span className="sidebar-item-icon" style={{ display: 'flex', fontSize: '14px' }}>🏛️</span>
                            <span className="sidebar-item-text">{t('nav.plaza', 'Plaza')}</span>
                        </NavLink>
                        <NavLink to="/dashboard" className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}>
                            <span className="sidebar-item-icon" style={{ display: 'flex' }}>{SidebarIcons.home}</span>
                            <span className="sidebar-item-text">{t('nav.dashboard')}</span>
                        </NavLink>
                    </div>
                </div>

                <div className="sidebar-scrollable">
                    {/* Sidebar search */}
                    {!isSidebarCollapsed && agents.length >= 5 && (
                        <div style={{ padding: '4px 12px 4px', position: 'relative' }}>
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--text-tertiary)" strokeWidth="2" strokeLinecap="round" style={{ position: 'absolute', left: '20px', top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }}>
                                <circle cx="11" cy="11" r="8" /><path d="M21 21l-4.35-4.35" />
                            </svg>
                            <input
                                type="text"
                                value={sidebarSearch}
                                onChange={e => setSidebarSearch(e.target.value)}
                                placeholder={isChinese ? '搜索...' : 'Search...'}
                                style={{
                                    width: '100%', padding: '5px 24px 5px 28px', border: '1px solid var(--border-subtle)',
                                    borderRadius: '6px', background: 'var(--bg-secondary)', color: 'var(--text-primary)',
                                    fontSize: '12px', outline: 'none', boxSizing: 'border-box',
                                }}
                                onFocus={e => e.target.style.borderColor = 'var(--primary)'}
                                onBlur={e => e.target.style.borderColor = 'var(--border-subtle)'}
                            />
                            {sidebarSearch && (
                                <button onClick={() => setSidebarSearch('')} style={{ position: 'absolute', right: '18px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer', fontSize: '12px', padding: '2px', lineHeight: 1 }}>&#x2715;</button>
                            )}
                        </div>
                    )}
                    {/* Agent list */}
                    {(() => {
                        const q = sidebarSearch.trim().toLowerCase();
                        const filterAgent = (a: any) => !q || (a.name || '').toLowerCase().includes(q) || (a.role_description || '').toLowerCase().includes(q);
                        const sortedAgents = [...agents].filter(filterAgent).sort((a: any, b: any) => {
                            const ap = pinnedAgents.has(a.id) ? 1 : 0;
                            const bp = pinnedAgents.has(b.id) ? 1 : 0;
                            if (ap !== bp) return bp - ap;
                            // Sort by created_at descending (newest first)
                            const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
                            const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
                            return bTime - aTime;
                        });
                        const renderAgent = (agent: any) => {
                            const badge = getAgentBadgeStatus(agent);
                            const avatarChar = ((Array.from(agent.name || '?')[0] as string) || '?').toUpperCase();
                            return (
                            <div key={agent.id} style={{ position: 'relative' }} className={`sidebar-agent-item${agent.creator_id === user?.id ? ' owned' : ''}`}>
                                <NavLink
                                    to={`/agents/${agent.id}`}
                                    className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}
                                    title={agent.name}
                                    style={{ paddingRight: '28px' }}
                                >
                                    <span className="sidebar-item-icon" style={{ position: 'relative' }}>
                                        <span className={`agent-avatar${agent.agent_type === 'openclaw' ? ' openclaw' : ''}`}>{avatarChar}</span>
                                        {agent.agent_type === 'openclaw' && (
                                            <span className="agent-avatar-link">
                                                <svg width="6" height="6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                                                    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                                                    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
                                                </svg>
                                            </span>
                                        )}
                                        {badge && <span className={`agent-avatar-badge ${badge}`} />}
                                    </span>
                                    <span className="sidebar-item-text">{agent.name}</span>
                                </NavLink>
                                {!isSidebarCollapsed && (
                                    <button
                                        onClick={e => { e.preventDefault(); e.stopPropagation(); togglePin(agent.id); }}
                                        className={`sidebar-pin-btn ${pinnedAgents.has(agent.id) ? 'pinned' : ''}`}
                                        title={pinnedAgents.has(agent.id) ? (isChinese ? '取消置顶' : 'Unpin') : (isChinese ? '置顶' : 'Pin to top')}
                                    >
                                        <svg width="10" height="10" viewBox="0 0 24 24" fill={pinnedAgents.has(agent.id) ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                            <path d="M12 17v5" /><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16h14v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V5a1 1 0 0 1 1-1h1V2H7v2h1a1 1 0 0 1 1 1z" />
                                        </svg>
                                    </button>
                                )}
                            </div>
                        );};
                        return (
                            <>
                                {sortedAgents.map(renderAgent)}
                                {agents.length === 0 && (
                                    <div className="sidebar-section">
                                        <div className="sidebar-section-title">{t('nav.myAgents')}</div>
                                    </div>
                                )}
                                {agents.length > 0 && sortedAgents.length === 0 && q && (
                                    <div style={{ padding: '12px 16px', fontSize: '12px', color: 'var(--text-tertiary)', textAlign: 'center' }}>
                                        {isChinese ? '无匹配结果' : 'No matches'}
                                    </div>
                                )}
                            </>
                        );
                    })()}
                </div>

                <div className="sidebar-bottom">
                    <div className="sidebar-section" style={{ borderBottom: '1px solid var(--border-subtle)', paddingBottom: '8px', marginBottom: 0 }}>
                        {user && (
                            <NavLink to="/agents/new" className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`} title={t('nav.newAgent')}>
                                <span className="sidebar-item-icon" style={{ display: 'flex' }}>{SidebarIcons.plus}</span>
                                <span className="sidebar-item-text">{t('nav.newAgent')}</span>
                            </NavLink>
                        )}
                        {user && ['platform_admin', 'org_admin'].includes(user.role) && (
                            <NavLink to="/enterprise" className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`} title={t('nav.enterprise')}>
                                <span className="sidebar-item-icon" style={{ display: 'flex' }}>{SidebarIcons.settings}</span>
                                <span className="sidebar-item-text">{t('nav.enterprise')}</span>
                            </NavLink>
                        )}
                        {user && user.role === 'platform_admin' && (
                            <NavLink to="/admin/platform-settings" className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`} title={t('nav.platformSettings', 'Platform Settings')}>
                                <span className="sidebar-item-icon" style={{ display: 'flex' }}>
                                    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                                        <circle cx="8" cy="8" r="2.5" /><path d="M13.5 8a5.5 5.5 0 01-.3 1.8l1.3.8-.8 1.4-1.3-.8a5.5 5.5 0 01-1.5 1l.1 1.5H9.2l.1-1.5a5.5 5.5 0 01-1.5-1l-1.3.8-.8-1.4 1.3-.8A5.5 5.5 0 016.7 8a5.5 5.5 0 01.3-1.8l-1.3-.8.8-1.4 1.3.8a5.5 5.5 0 011.5-1L9.2 2.3h1.6l-.1 1.5a5.5 5.5 0 011.5 1l1.3-.8.8 1.4-1.3.8a5.5 5.5 0 01.5 1.8z" />
                                    </svg>
                                </span>
                                <span className="sidebar-item-text">{t('nav.platformSettings', 'Platform Settings')}</span>
                            </NavLink>
                        )}
                    </div>

                    <div className="sidebar-footer">
                        <div className="sidebar-footer-controls" style={{
                            display: 'flex', alignItems: 'center', gap: '4px', marginBottom: '12px',
                        }}>
                            <button className="btn btn-ghost" onClick={toggleSidebar} style={{
                                padding: '4px 8px', display: 'flex', alignItems: 'center', justifyContent: 'center'
                            }} title={isSidebarCollapsed ? "Expand Sidebar" : "Collapse Sidebar"}>
                                {isSidebarCollapsed ? SidebarIcons.expand : SidebarIcons.collapse}
                            </button>
                            <div style={{ flex: 1 }} />
                            {/* Notification bell */}
                            <button className="btn btn-ghost" onClick={() => { setShowNotifications(v => !v); if (!showNotifications) refetchNotifications(); }} style={{
                                padding: '4px 8px', display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative',
                            }} title={isChinese ? '通知' : 'Notifications'}>
                                {SidebarIcons.bell}
                                {(unreadCount as number) > 0 && (
                                    <span style={{
                                        position: 'absolute', top: '-2px', right: '-4px',
                                        minWidth: '16px', height: '16px', borderRadius: '8px',
                                        padding: '0 4px', boxSizing: 'border-box',
                                        background: 'var(--error)', color: '#fff',
                                        fontSize: '10px', fontWeight: 600,
                                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                                        lineHeight: 1,
                                    }}>{(unreadCount as number) > 99 ? '99+' : unreadCount}</span>
                                )}
                            </button>
                            <button className="btn btn-ghost" onClick={toggleTheme} style={{
                                fontSize: '12px', display: 'flex', alignItems: 'center', gap: '4px',
                                padding: '4px 8px',
                            }}>
                                {theme === 'dark' ? SidebarIcons.sun : SidebarIcons.moon}
                            </button>
                            <button className="btn btn-ghost" onClick={toggleLang} style={{
                                fontSize: '12px', display: 'flex', alignItems: 'center', gap: '4px',
                                padding: '4px 8px',
                            }} title={i18n.language === 'zh' ? 'English' : '中文'}>
                                {SidebarIcons.globe}
                            </button>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <div
                                style={{
                                    display: 'flex', alignItems: 'center', gap: '8px',
                                    flex: 1, minWidth: 0, cursor: 'pointer',
                                    padding: '4px 6px', borderRadius: '6px',
                                    transition: 'background 0.15s',
                                }}
                                onClick={() => setShowAccountSettings(true)}
                                onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-tertiary)')}
                                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                                title={isChinese ? '账户设置' : 'Account Settings'}
                            >
                                <div style={{
                                    width: '28px', height: '28px', borderRadius: 'var(--radius-md)',
                                    background: 'var(--bg-tertiary)', border: '1px solid var(--border-subtle)',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                    color: 'var(--text-tertiary)', flexShrink: 0,
                                }}>
                                    {SidebarIcons.user}
                                </div>
                                <div className="sidebar-footer-user-info" style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ fontSize: '13px', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {user?.display_name}
                                    </div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                        {user?.role === 'platform_admin' ? t('roles.platformAdmin') :
                                            user?.role === 'org_admin' ? t('roles.orgAdmin') :
                                                user?.role === 'agent_admin' ? t('roles.agentAdmin') : t('roles.member')}
                                    </div>
                                </div>
                            </div>
                            <button className="btn btn-ghost" onClick={handleLogout} style={{
                                padding: '4px 6px', color: 'var(--text-tertiary)',
                                display: 'flex', alignItems: 'center', flexShrink: 0,
                            }} title={t('layout.logout', 'Logout')}>
                                {SidebarIcons.logout}
                            </button>
                        </div>
                        {/* Version */}
                        <VersionDisplay />
                    </div>
                </div>
            </nav>

            {/* Notification Panel */}
            {showNotifications && (
                <div style={{
                    position: 'fixed', top: 0, bottom: 0, left: isSidebarCollapsed ? '60px' : '220px',
                    width: '360px', background: 'var(--bg-primary)', borderRight: '1px solid var(--border-subtle)',
                    zIndex: 9999, display: 'flex', flexDirection: 'column',
                    boxShadow: '4px 0 24px rgba(0,0,0,0.15)', transition: 'left 0.2s',
                }}>
                    <div style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                        <div style={{ padding: '16px 20px 0', display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 600, flex: 1 }}>{isChinese ? '通知' : 'Notifications'}</h3>
                            {(unreadCount as number) > 0 && (
                                <button className="btn btn-ghost" onClick={markAllRead} style={{ fontSize: '11px', padding: '4px 8px' }}>
                                    {isChinese ? '全部已读' : 'Mark all read'}
                                </button>
                            )}
                            <button className="btn btn-ghost" onClick={() => setShowNotifications(false)} style={{ padding: '4px 8px', fontSize: '16px', lineHeight: 1 }}>×</button>
                        </div>
                        <div style={{ display: 'flex', gap: '0', padding: '0 20px', marginTop: '12px' }}>
                            {[
                                { key: 'all', zh: '全部', en: 'All' },
                                { key: 'tool', zh: '工具执行', en: 'Tool' },
                                { key: 'approval', zh: '审批', en: 'Approval' },
                                { key: 'social', zh: '社交', en: 'Social' },
                            ].map(tab => (
                                <button
                                    key={tab.key}
                                    onClick={() => { setNotifCategory(tab.key); }}
                                    style={{
                                        background: 'none', border: 'none', cursor: 'pointer',
                                        padding: '6px 12px', fontSize: '12px', fontWeight: 500,
                                        color: notifCategory === tab.key ? 'var(--text-primary)' : 'var(--text-tertiary)',
                                        borderBottom: notifCategory === tab.key ? '2px solid var(--accent-primary)' : '2px solid transparent',
                                        marginBottom: '-1px', transition: 'all 0.15s',
                                    }}
                                >
                                    {isChinese ? tab.zh : tab.en}
                                </button>
                            ))}
                        </div>
                    </div>
                    <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
                        {(notifications as any[]).length === 0 && (
                            <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                                {isChinese ? '暂无通知' : 'No notifications'}
                            </div>
                        )}
                        {(notifications as any[]).map((n: any) => (
                            <div
                                key={n.id}
                                onClick={() => {
                                    if (!n.is_read) markOneRead(n.id);
                                    if (n.link) { navigate(n.link); setShowNotifications(false); }
                                }}
                                style={{
                                    padding: '12px 20px', cursor: n.link ? 'pointer' : 'default',
                                    borderBottom: '1px solid var(--border-subtle)',
                                    background: n.is_read ? 'transparent' : 'var(--bg-secondary)',
                                    transition: 'background 0.15s',
                                }}
                                onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-tertiary)')}
                                onMouseLeave={e => (e.currentTarget.style.background = n.is_read ? 'transparent' : 'var(--bg-secondary)')}
                            >
                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
                                    {!n.is_read && <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'var(--accent-primary)', flexShrink: 0 }} />}
                                    <span style={{ fontSize: '12px', fontWeight: 500, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {n.title}
                                    </span>
                                </div>
                                {n.body && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', lineHeight: '1.4', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.body}</div>}
                                <div style={{ fontSize: '10px', color: 'var(--text-quaternary)', marginTop: '4px' }}>
                                    {n.created_at ? new Date(n.created_at).toLocaleString() : ''}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}
            {showNotifications && <div style={{ position: 'fixed', inset: 0, zIndex: 9998 }} onClick={() => setShowNotifications(false)} />}

            <main className="main-content">
                <Outlet />
            </main>

            {showAccountSettings && (
                <AccountSettingsModal
                    user={user}
                    onClose={() => setShowAccountSettings(false)}
                    isChinese={!!isChinese}
                />
            )}
        </div>
    );
}
