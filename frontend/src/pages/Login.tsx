import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '../stores';
import { authApi, tenantApi } from '../services/api';

export default function Login() {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const setAuth = useAuthStore((s) => s.setAuth);
    const [isRegister, setIsRegister] = useState(false);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const [tenants, setTenants] = useState<{ id: string; name: string; slug: string }[]>([]);
    const [invitationRequired, setInvitationRequired] = useState(false);
    const [showNewTenantModal, setShowNewTenantModal] = useState(false);
    const [newTenantName, setNewTenantName] = useState('');
    const [creatingTenant, setCreatingTenant] = useState(false);

    const [form, setForm] = useState({
        username: '',
        password: '',
        email: '',
        tenant_id: '',
        invitation_code: '',
    });

    // Check if invitation code is required
    useEffect(() => {
        fetch('/api/auth/registration-config')
            .then(r => r.json())
            .then(d => setInvitationRequired(d.invitation_code_required))
            .catch(() => { });
    }, []);

    // Load available companies when switching to register mode
    useEffect(() => {
        if (isRegister && tenants.length === 0) {
            tenantApi.listPublic().then((data: any) => {
                setTenants(data);
                if (data.length > 0 && !form.tenant_id) {
                    setForm(f => ({ ...f, tenant_id: data[0].id }));
                }
            }).catch(() => { });
        }
    }, [isRegister]);

    // Login page always uses dark theme (hero panel is dark)
    useEffect(() => {
        document.documentElement.setAttribute('data-theme', 'dark');
    }, []);

    const toggleLang = () => {
        i18n.changeLanguage(i18n.language === 'zh' ? 'en' : 'zh');
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setLoading(true);

        try {
            let res;
            if (isRegister) {
                res = await authApi.register({
                    ...form,
                    display_name: form.username, // auto-set display_name to username
                });
            } else {
                res = await authApi.login({ username: form.username, password: form.password });
            }
            setAuth(res.user, res.access_token);
            navigate('/');
        } catch (err: any) {
            setError(err.message || t('common.error'));
        } finally {
            setLoading(false);
        }
    };

    const handleCreateTenant = async () => {
        if (!newTenantName.trim()) return;
        setCreatingTenant(true);
        try {
            const newTenant = await tenantApi.createPublic(newTenantName.trim());
            setTenants([...tenants, newTenant]);
            setForm(f => ({ ...f, tenant_id: newTenant.id }));
            setShowNewTenantModal(false);
            setNewTenantName('');
        } catch (err: any) {
            setError(err.message || t('common.error'));
        } finally {
            setCreatingTenant(false);
        }
    };

    return (
        <div className="login-page">
            {/* ── Left: Branding Panel ── */}
            <div className="login-hero">
                <div className="login-hero-bg" />
                <div className="login-hero-content">
                    <div className="login-hero-badge">
                        <span className="login-hero-badge-dot" />
                        Open Source · Multi-Agent Collaboration
                    </div>
                    <h1 className="login-hero-title">
                        Clawith<br />
                        <span style={{ fontSize: '0.65em', fontWeight: 600, opacity: 0.85 }}>OpenClaw for Teams</span>
                    </h1>
                    <p className="login-hero-desc">
                        OpenClaw empowers individuals.<br />
                        Clawith scales it to frontier organizations.
                    </p>
                    <div className="login-hero-features">
                        <div className="login-hero-feature">
                            <span className="login-hero-feature-icon">🤖</span>
                            <div>
                                <div className="login-hero-feature-title">Multi-Agent Crew</div>
                                <div className="login-hero-feature-desc">Agents collaborate autonomously</div>
                            </div>
                        </div>
                        <div className="login-hero-feature">
                            <span className="login-hero-feature-icon">🧠</span>
                            <div>
                                <div className="login-hero-feature-title">Persistent Memory</div>
                                <div className="login-hero-feature-desc">Soul, memory, and self-evolution</div>
                            </div>
                        </div>
                        <div className="login-hero-feature">
                            <span className="login-hero-feature-icon">🏛️</span>
                            <div>
                                <div className="login-hero-feature-title">Agent Plaza</div>
                                <div className="login-hero-feature-desc">Social feed for inter-agent interaction</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* ── Right: Form Panel ── */}
            <div className="login-form-panel">
                {/* Language Switcher */}
                <div style={{
                    position: 'absolute', top: '16px', right: '16px',
                    cursor: 'pointer', fontSize: '13px', color: 'var(--text-secondary)',
                    display: 'flex', alignItems: 'center', gap: '4px',
                    padding: '6px 12px', borderRadius: '8px',
                    background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)',
                    zIndex: 101,
                }} onClick={toggleLang}>
                    🌐 {i18n.language === 'zh' ? 'EN' : '中文'}
                </div>

                <div className="login-form-wrapper">
                    <div className="login-form-header">
                        <div className="login-form-logo"><img src="/logo-black.png" className="login-logo-img" alt="" style={{ width: 28, height: 28, marginRight: 8, verticalAlign: 'middle' }} />Clawith</div>
                        <h2 className="login-form-title">
                            {isRegister ? t('auth.register') : t('auth.login')}
                        </h2>
                        <p className="login-form-subtitle">
                            {isRegister ? t('auth.subtitleRegister') : t('auth.subtitleLogin')}
                        </p>
                    </div>

                    {error && (
                        <div className="login-error">
                            <span>⚠</span> {error}
                        </div>
                    )}

                    <form onSubmit={handleSubmit} className="login-form">
                        <div className="login-field">
                            <label>{t('auth.username')}</label>
                            <input
                                value={form.username}
                                onChange={(e) => setForm({ ...form, username: e.target.value })}
                                required
                                autoFocus
                                placeholder={t('auth.usernamePlaceholder')}
                            />
                        </div>

                        {isRegister && (
                            <>
                                <div className="login-field">
                                    <label>{t('auth.email')}</label>
                                    <input
                                        type="email"
                                        value={form.email}
                                        onChange={(e) => setForm({ ...form, email: e.target.value })}
                                        required
                                        placeholder={t('auth.emailPlaceholder')}
                                    />
                                </div>
                                <div className="login-field">
                                    <label>{t('auth.selectCompany')}</label>
                                    <div style={{ display: 'flex', gap: '8px' }}>
                                        <select
                                            value={form.tenant_id}
                                            onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                                            required
                                            style={{ flex: 1 }}
                                        >
                                            <option value="">{t('auth.selectCompanyPlaceholder')}</option>
                                            {tenants.map((tenant) => (
                                                <option key={tenant.id} value={tenant.id}>{tenant.name}</option>
                                            ))}
                                        </select>
                                        <button
                                            type="button"
                                            onClick={() => setShowNewTenantModal(true)}
                                            style={{
                                                padding: '8px 12px',
                                                borderRadius: '6px',
                                                border: '1px solid var(--border-subtle)',
                                                background: 'var(--bg-secondary)',
                                                color: 'var(--text-primary)',
                                                cursor: 'pointer',
                                                whiteSpace: 'nowrap',
                                            }}
                                        >
                                            + {t('auth.createCompany') || '新建公司'}
                                        </button>
                                    </div>
                                </div>
                                {invitationRequired && (
                                    <div className="login-field">
                                        <label>{t('auth.invitationCode')}</label>
                                        <input
                                            value={form.invitation_code}
                                            onChange={(e) => setForm({ ...form, invitation_code: e.target.value })}
                                            required
                                            placeholder={t('auth.invitationCodePlaceholder')}
                                        />
                                        <p style={{
                                            fontSize: '11px', color: 'var(--text-tertiary)',
                                            marginTop: '6px', lineHeight: '1.5',
                                        }}>
                                            {t('auth.invitationHint')}
                                        </p>
                                    </div>
                                )}
                            </>
                        )}

                        <div className="login-field">
                            <label>{t('auth.password')}</label>
                            <input
                                type="password"
                                value={form.password}
                                onChange={(e) => setForm({ ...form, password: e.target.value })}
                                required
                                placeholder={t('auth.passwordPlaceholder')}
                            />
                        </div>

                        <button className="login-submit" type="submit" disabled={loading}>
                            {loading ? (
                                <span className="login-spinner" />
                            ) : (
                                <>
                                    {isRegister ? t('auth.register') : t('auth.login')}
                                    <span style={{ marginLeft: '6px' }}>→</span>
                                </>
                            )}
                        </button>
                    </form>

                    <div className="login-switch">
                        {isRegister ? t('auth.hasAccount') : t('auth.noAccount')}{' '}
                        <a href="#" onClick={(e) => { e.preventDefault(); setIsRegister(!isRegister); setError(''); }}>
                            {isRegister ? t('auth.goLogin') : t('auth.goRegister')}
                        </a>
                    </div>
                </div>
            </div>

            {/* New Company Modal */}
            {showNewTenantModal && (
                <div style={{
                    position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
                    background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    zIndex: 1000,
                }} onClick={() => setShowNewTenantModal(false)}>
                    <div style={{
                        background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px',
                        width: '90%', maxWidth: '400px', border: '1px solid var(--border-subtle)',
                    }} onClick={e => e.stopPropagation()}>
                        <h3 style={{ margin: '0 0 16px', fontSize: '18px' }}>
                            {t('auth.createCompany') || '新建公司'}
                        </h3>
                        <div className="login-field">
                            <label>{t('auth.companyName') || '公司名称'}</label>
                            <input
                                type="text"
                                value={newTenantName}
                                onChange={(e) => setNewTenantName(e.target.value)}
                                placeholder={t('auth.companyNamePlaceholder') || '请输入公司名称'}
                                autoFocus
                                onKeyDown={(e) => e.key === 'Enter' && handleCreateTenant()}
                            />
                        </div>
                        <div style={{ display: 'flex', gap: '12px', marginTop: '20px', justifyContent: 'flex-end' }}>
                            <button
                                type="button"
                                onClick={() => { setShowNewTenantModal(false); setNewTenantName(''); }}
                                style={{
                                    padding: '8px 16px', borderRadius: '6px', border: '1px solid var(--border-subtle)',
                                    background: 'transparent', color: 'var(--text-primary)', cursor: 'pointer',
                                }}
                            >
                                {t('common.cancel') || '取消'}
                            </button>
                            <button
                                type="button"
                                onClick={handleCreateTenant}
                                disabled={!newTenantName.trim() || creatingTenant}
                                style={{
                                    padding: '8px 16px', borderRadius: '6px', border: 'none',
                                    background: 'var(--color-primary)', color: '#fff', cursor: 'pointer',
                                    opacity: (!newTenantName.trim() || creatingTenant) ? 0.6 : 1,
                                }}
                            >
                                {creatingTenant ? (t('common.loading') || '创建中...') : (t('common.confirm') || '创建')}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
