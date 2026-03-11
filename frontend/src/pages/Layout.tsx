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
};

const fetchJson = async <T,>(url: string): Promise<T> => {
    const token = localStorage.getItem('token');
    const res = await fetch(`/api${url}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!res.ok) return [] as T;
    return res.json();
};

const statusDotClass = (status: string) => {
    switch (status) {
        case 'running': return 'running';
        case 'stopped': return 'stopped';
        case 'creating': return 'creating';
        case 'error': return 'error';
        default: return 'idle';
    }
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

export default function Layout() {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const { user, logout } = useAuthStore();
    const queryClient = useQueryClient();
    const isChinese = i18n.language?.startsWith('zh');
    const [showAccountSettings, setShowAccountSettings] = useState(false);

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

    // Tenant state
    const [currentTenant, setCurrentTenant] = useState(() => localStorage.getItem('current_tenant_id') || '');
    const [showNewCompany, setShowNewCompany] = useState(false);
    const [newCompanyName, setNewCompanyName] = useState('');

    const { data: tenants = [] } = useQuery({
        queryKey: ['tenants'],
        queryFn: () => fetchJson<any[]>('/tenants/'),
        enabled: !!user && user.role === 'platform_admin',
    });

    // Auto-select user's tenant or first available tenant; also fix stale localStorage values
    useEffect(() => {
        if (!user) return;
        const validTenantIds = tenants.map((t: any) => t.id);
        const storedIsValid = currentTenant &&
            (validTenantIds.includes(currentTenant) || currentTenant === user.tenant_id);
        if (!storedIsValid) {
            const fallback = user.tenant_id || (tenants.length > 0 ? tenants[0].id : '');
            if (fallback) {
                setCurrentTenant(fallback);
                localStorage.setItem('current_tenant_id', fallback);
            }
        }
    }, [user, tenants, currentTenant]);

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
    const switchTenant = (tenantId: string) => {
        setCurrentTenant(tenantId);
        localStorage.setItem('current_tenant_id', tenantId);
        // Notify other components about tenant change
        window.dispatchEvent(new StorageEvent('storage', { key: 'current_tenant_id', newValue: tenantId }));
    };
    const currentTenantName = tenants.find((t: any) => t.id === currentTenant)?.name;
    const createCompany = async () => {
        if (!newCompanyName.trim()) return;
        const token = localStorage.getItem('token');
        const slug = newCompanyName.toLowerCase().replace(/[\s]+/g, '-').replace(/[^a-z0-9_-]/g, '').slice(0, 50);
        await fetch('/api/tenants/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
            body: JSON.stringify({ name: newCompanyName, slug, im_provider: 'web_only' }),
        });
        setNewCompanyName('');
        setShowNewCompany(false);
        queryClient.invalidateQueries({ queryKey: ['tenants'] });
    };

    return (
        <div className="app-layout">
            <nav className={`sidebar ${isSidebarCollapsed ? 'collapsed' : ''}`}>
                <div className="sidebar-top">
                    <div className="sidebar-logo">
                        <img src="/logo.png" alt="" style={{ width: 22, height: 22 }} />
                        <span className="sidebar-logo-text">Clawith</span>
                    </div>

                    {/* Company Switcher */}
                    {user?.role === 'platform_admin' && (
                        <div className="tenant-switcher" style={{ padding: '0 12px 8px', borderBottom: '1px solid var(--border-subtle)', marginBottom: '4px' }}>
                            <select
                                value={currentTenant}
                                onChange={e => switchTenant(e.target.value)}
                                style={{
                                    width: '100%', padding: '6px 8px', fontSize: '12px',
                                    background: 'var(--bg-secondary)', color: 'var(--text-primary)',
                                    border: '1px solid var(--border-subtle)', borderRadius: '6px',
                                    cursor: 'pointer',
                                }}
                            >
                                {tenants.map((t: any) => (
                                    <option key={t.id} value={t.id}>{t.name}</option>
                                ))}
                            </select>
                            {showNewCompany ? (
                                <div style={{ marginTop: '6px', display: 'flex', gap: '4px' }}>
                                    <input
                                        value={newCompanyName}
                                        onChange={e => setNewCompanyName(e.target.value)}
                                        onKeyDown={e => e.key === 'Enter' && createCompany()}
                                        placeholder={t('layout.companyName')}
                                        style={{
                                            flex: 1, padding: '4px 6px', fontSize: '11px',
                                            background: 'var(--bg-elevated)', color: 'var(--text-primary)',
                                            border: '1px solid var(--border-subtle)', borderRadius: '4px',
                                        }}
                                        autoFocus
                                    />
                                    <button onClick={createCompany} style={{
                                        fontSize: '11px', padding: '4px 6px',
                                        background: 'var(--accent-primary)', color: 'white',
                                        border: 'none', borderRadius: '4px', cursor: 'pointer',
                                    }}>{t('layout.create')}</button>
                                    <button onClick={() => { setShowNewCompany(false); setNewCompanyName(''); }} style={{
                                        fontSize: '11px', padding: '4px 6px',
                                        background: 'transparent', color: 'var(--text-tertiary)',
                                        border: 'none', cursor: 'pointer',
                                    }}>✕</button>
                                </div>
                            ) : (
                                <button
                                    onClick={() => setShowNewCompany(true)}
                                    style={{
                                        marginTop: '4px', width: '100%', padding: '4px', fontSize: '11px',
                                        background: 'transparent', color: 'var(--text-tertiary)',
                                        border: '1px dashed var(--border-subtle)', borderRadius: '4px',
                                        cursor: 'pointer', textAlign: 'center',
                                        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '4px',
                                    }}
                                >
                                    {SidebarIcons.plus} {t('layout.newCompany')}
                                </button>
                            )}
                        </div>
                    )}
                    {user?.role !== 'platform_admin' && user?.tenant_id && (
                        <div className="tenant-name" style={{
                            padding: '0 16px 8px', fontSize: '11px', color: 'var(--text-secondary)',
                            borderBottom: '1px solid var(--border-subtle)', marginBottom: '4px',
                            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
                        }}>
                            {currentTenantName || t('layout.myCompany')}
                        </div>
                    )}

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
                    {/* My created agents */}
                    {(() => {
                        const myAgents = agents.filter((a: any) => a.created_by === user?.id);
                        const sharedAgents = agents.filter((a: any) => a.created_by !== user?.id);
                        return (
                            <>
                                {myAgents.length > 0 && (
                                    <div className="sidebar-section">
                                        <div className="sidebar-section-title">{t('nav.myCreated')}</div>
                                        {myAgents.map((agent: any) => (
                                            <NavLink
                                                key={agent.id}
                                                to={`/agents/${agent.id}`}
                                                className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}
                                                title={agent.name}
                                            >
                                                <span className="sidebar-item-icon">
                                                    <span className={`status-dot ${statusDotClass(agent.status)}`} />
                                                </span>
                                                <span className="sidebar-item-text">{agent.name}</span>
                                            </NavLink>
                                        ))}
                                    </div>
                                )}
                                {sharedAgents.length > 0 && (
                                    <div className="sidebar-section">
                                        <div className="sidebar-section-title">{t('nav.companyShared')}</div>
                                        {sharedAgents.map((agent: any) => (
                                            <NavLink
                                                key={agent.id}
                                                to={`/agents/${agent.id}`}
                                                className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}
                                                title={agent.name}
                                            >
                                                <span className="sidebar-item-icon">
                                                    <span className={`status-dot ${statusDotClass(agent.status)}`} />
                                                </span>
                                                <span className="sidebar-item-text">{agent.name}</span>
                                            </NavLink>
                                        ))}
                                    </div>
                                )}
                                {agents.length === 0 && (
                                    <div className="sidebar-section">
                                        <div className="sidebar-section-title">{t('nav.myAgents')}</div>
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
                            <NavLink to="/invitations" className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`} title={t('nav.invitations', 'Invitation Codes')}>
                                <span className="sidebar-item-icon" style={{ display: 'flex' }}>🎟️</span>
                                <span className="sidebar-item-text">{t('nav.invitations', 'Invitation Codes')}</span>
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
                            <button className="btn btn-ghost" onClick={toggleTheme} style={{
                                fontSize: '12px', display: 'flex', alignItems: 'center', gap: '4px',
                                padding: '4px 8px',
                            }}>
                                {theme === 'dark' ? SidebarIcons.sun : SidebarIcons.moon}
                            </button>
                            <button className="btn btn-ghost" onClick={toggleLang} style={{
                                fontSize: '12px', display: 'flex', alignItems: 'center', gap: '4px',
                                padding: '4px 8px',
                            }}>
                                {SidebarIcons.globe}
                                <span>{i18n.language === 'zh' ? '中文' : 'EN'}</span>
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
                    </div>
                </div>
            </nav>

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
