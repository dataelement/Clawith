import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '../stores';
import { tenantApi, authApi } from '../services/api';

export default function CompanySetup() {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const { user, setAuth } = useAuthStore();
    const [allowCreate, setAllowCreate] = useState(true);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    // Join company form
    const [inviteCode, setInviteCode] = useState('');
    // Create company form
    const [companyName, setCompanyName] = useState('');

    useEffect(() => {
        // Check if self-creation is allowed
        tenantApi.registrationConfig().then((d: any) => {
            setAllowCreate(d.allow_self_create_company);
        }).catch(() => {});
    }, []);

    // If user already has a company, redirect home
    useEffect(() => {
        if (user?.tenant_id) {
            navigate('/');
        }
    }, [user, navigate]);

    const refreshUser = async () => {
        try {
            const me = await authApi.me();
            const token = useAuthStore.getState().token;
            if (token) setAuth(me, token);
        } catch { /* ignore */ }
    };

    const handleJoin = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            await tenantApi.join(inviteCode);
            await refreshUser();
            navigate('/');
        } catch (err: any) {
            setError(err.message || 'Failed to join company');
        } finally {
            setLoading(false);
        }
    };

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            await tenantApi.selfCreate({ name: companyName });
            await refreshUser();
            // Navigate to Enterprise Settings to configure LLM models
            navigate('/enterprise');
        } catch (err: any) {
            setError(err.message || 'Failed to create company');
        } finally {
            setLoading(false);
        }
    };

    const toggleLang = () => {
        i18n.changeLanguage(i18n.language === 'zh' ? 'en' : 'zh');
    };

    return (
        <div className="company-setup-page">
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

            <div className="company-setup-container">
                <div className="company-setup-header">
                    <img src="/logo-black.png" alt="" style={{ width: 32, height: 32 }} />
                    <h1>{t('companySetup.title', 'Set Up Your Workspace')}</h1>
                    <p className="company-setup-subtitle">
                        {t('companySetup.subtitle', 'Join an existing company or create your own to get started.')}
                    </p>
                </div>

                {error && (
                    <div className="login-error" style={{ marginBottom: 16 }}>
                        <span>⚠</span> {error}
                    </div>
                )}

                <div className={`company-setup-panels ${!allowCreate ? 'single' : ''}`}>
                    {/* ── Join Company Panel ── */}
                    <form className="company-setup-panel" onSubmit={handleJoin}>
                        <div className="company-setup-panel-header">
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" />
                                <polyline points="10 17 15 12 10 7" />
                                <line x1="15" y1="12" x2="3" y2="12" />
                            </svg>
                            <h3>{t('companySetup.joinTitle', 'Join a Company')}</h3>
                        </div>
                        <p className="company-setup-panel-desc">
                            {t('companySetup.joinDesc', 'Enter the invitation code provided by your company administrator.')}
                        </p>
                        <div className="login-field">
                            <label>{t('companySetup.inviteCode', 'Invitation Code')}</label>
                            <input
                                value={inviteCode}
                                onChange={(e) => setInviteCode(e.target.value)}
                                required
                                placeholder={t('companySetup.inviteCodePlaceholder', 'e.g. ABC12345')}
                                style={{ textTransform: 'uppercase', letterSpacing: '2px', fontFamily: 'monospace' }}
                            />
                        </div>
                        <button className="login-submit" type="submit" disabled={loading || !inviteCode}>
                            {loading ? <span className="login-spinner" /> : t('companySetup.joinBtn', 'Join Company')}
                        </button>
                    </form>

                    {/* ── Create Company Panel ── */}
                    {allowCreate && (
                        <>
                            <div className="company-setup-divider">
                                <span>{t('companySetup.or', 'OR')}</span>
                            </div>
                            <form className="company-setup-panel" onSubmit={handleCreate}>
                                <div className="company-setup-panel-header">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <rect x="2" y="7" width="20" height="14" rx="2" ry="2" />
                                        <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
                                    </svg>
                                    <h3>{t('companySetup.createTitle', 'Create a Company')}</h3>
                                </div>
                                <p className="company-setup-panel-desc">
                                    {t('companySetup.createDesc', 'Start a new workspace. You can invite team members later.')}
                                </p>
                                <div className="login-field">
                                    <label>{t('companySetup.companyName', 'Company Name')}</label>
                                    <input
                                        value={companyName}
                                        onChange={(e) => setCompanyName(e.target.value)}
                                        required
                                        placeholder={t('companySetup.companyNamePlaceholder', 'e.g. Acme Inc.')}
                                    />
                                </div>
                                <button className="login-submit" type="submit" disabled={loading || !companyName}>
                                    {loading ? <span className="login-spinner" /> : t('companySetup.createBtn', 'Create Company')}
                                </button>
                            </form>
                        </>
                    )}
                </div>

                {!allowCreate && (
                    <p className="company-setup-hint">
                        {t('companySetup.contactAdmin', 'Contact your platform administrator for an invitation code.')}
                    </p>
                )}
            </div>
        </div>
    );
}
