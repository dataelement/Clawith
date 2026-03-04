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

export default function Layout() {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const { user, logout } = useAuthStore();
    const queryClient = useQueryClient();

    // Theme
    const [theme, setTheme] = useState<'dark' | 'light'>(() => {
        return (localStorage.getItem('theme') as 'dark' | 'light') || 'dark';
    });

    useEffect(() => {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);
    }, [theme]);

    const toggleTheme = () => setTheme(prev => prev === 'dark' ? 'light' : 'dark');

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
            <nav className="sidebar">
                <div className="sidebar-logo"><img src="/logo.png" alt="" style={{ width: 22, height: 22 }} />Clawith</div>

                {/* Company Switcher */}
                {user?.role === 'platform_admin' && (
                    <div style={{ padding: '0 12px 8px', borderBottom: '1px solid var(--border-subtle)', marginBottom: '4px' }}>
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
                    <div style={{
                        padding: '0 16px 8px', fontSize: '11px', color: 'var(--text-secondary)',
                        borderBottom: '1px solid var(--border-subtle)', marginBottom: '4px',
                    }}>
                        {currentTenantName || t('layout.myCompany')}
                    </div>
                )}

                <div className="sidebar-section">
                    <NavLink to="/" end className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}>
                        <span className="sidebar-item-icon" style={{ display: 'flex' }}>{SidebarIcons.home}</span>
                        {t('nav.dashboard')}
                    </NavLink>
                    <NavLink to="/plaza" className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}>
                        <span className="sidebar-item-icon" style={{ display: 'flex', fontSize: '14px' }}>🏛️</span>
                        {t('nav.plaza', 'Plaza')}
                    </NavLink>
                </div>

                <div className="sidebar-section">
                    <div className="sidebar-section-title">{t('nav.myAgents')}</div>
                    {agents.map((agent) => (
                        <NavLink
                            key={agent.id}
                            to={`/agents/${agent.id}`}
                            className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}
                        >
                            <span className="sidebar-item-icon">
                                <span className={`status-dot ${statusDotClass(agent.status)}`} />
                            </span>
                            {agent.name}
                        </NavLink>
                    ))}
                </div>

                <div className="sidebar-section">
                    {user && (
                        <NavLink to="/agents/new" className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}>
                            <span className="sidebar-item-icon" style={{ display: 'flex' }}>{SidebarIcons.plus}</span>
                            {t('nav.newAgent')}
                        </NavLink>
                    )}
                    {user && ['platform_admin', 'org_admin'].includes(user.role) && (
                        <NavLink to="/enterprise" className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}>
                            <span className="sidebar-item-icon" style={{ display: 'flex' }}>{SidebarIcons.settings}</span>
                            {t('nav.enterprise')}
                        </NavLink>
                    )}
                </div>

                <div className="sidebar-footer">
                    <div style={{
                        display: 'flex', alignItems: 'center', gap: '4px', marginBottom: '12px',
                    }}>
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
                        <div style={{
                            width: '28px', height: '28px', borderRadius: 'var(--radius-md)',
                            background: 'var(--bg-tertiary)', border: '1px solid var(--border-subtle)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            color: 'var(--text-tertiary)',
                        }}>
                            {SidebarIcons.user}
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontSize: '13px', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {user?.display_name}
                            </div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                {user?.role === 'platform_admin' ? t('roles.platformAdmin') :
                                    user?.role === 'org_admin' ? t('roles.orgAdmin') :
                                        user?.role === 'agent_admin' ? t('roles.agentAdmin') : t('roles.member')}
                            </div>
                        </div>
                        <button className="btn btn-ghost" onClick={handleLogout} style={{
                            padding: '4px 6px', color: 'var(--text-tertiary)',
                            display: 'flex', alignItems: 'center',
                        }}>
                            {SidebarIcons.logout}
                        </button>
                    </div>
                </div>
            </nav>

            <main className="main-content">
                <Outlet />
            </main>
        </div>
    );
}
