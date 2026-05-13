import { useState, useEffect } from 'react';
import { useNavigate, useLocation, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { IconAlertTriangle, IconWorld } from '@tabler/icons-react';
import { useAuthStore } from '../stores';
import { tenantApi, authApi } from '../services/api';

export default function CompanySetup() {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const location = useLocation();
    const { user, setAuth } = useAuthStore();
    const [allowCreate, setAllowCreate] = useState(true);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    // Check if coming from registration flow.
    // Primary: location.state.fromRegister (set by Login page).
    // Fallback: if user exists but is not active, they're in the registration flow
    // (the Navigate in ProtectedRoute may strip location.state).
    const fromRegister = (location.state as any)?.fromRegister || (user && !user.is_active);

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

    // Allow access from login tenant selection dialog ("Create or Join Organization")
    // Use URL param instead of location.state for robustness (survives refresh)
    const [searchParams] = useSearchParams();
    const fromTenantSelection = searchParams.get('from') === 'tenant-selection';

    // If user already has a company and not from registration/tenant-selection, redirect home
    useEffect(() => {
        if (user?.tenant_id && !fromRegister && !fromTenantSelection) {
            navigate('/');
        }
    }, [user, navigate, fromRegister, fromTenantSelection]);

    const refreshUser = async () => {
        try {
            const me = await authApi.me();
            const token = useAuthStore.getState().token;
            if (token) setAuth(me, token);
            return me;
        } catch { return null; }
    };

    const applyTenantSetupResult = async (result: any) => {
        const nextTenantId = result?.tenant?.id ? String(result.tenant.id) : '';
        if (result?.access_token) {
            localStorage.setItem('token', result.access_token);
            if (nextTenantId) {
                localStorage.setItem('current_tenant_id', nextTenantId);
                window.dispatchEvent(new StorageEvent('storage', { key: 'current_tenant_id', newValue: nextTenantId }));
            }
            const me = await authApi.me();
            setAuth(me, result.access_token);
            return me;
        }
        if (nextTenantId) {
            localStorage.setItem('current_tenant_id', nextTenantId);
            window.dispatchEvent(new StorageEvent('storage', { key: 'current_tenant_id', newValue: nextTenantId }));
        }
        return refreshUser();
    };

    const handleJoin = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            const result = await tenantApi.join(inviteCode);
            await applyTenantSetupResult(result);
            if (fromRegister) {
                navigate('/onboarding?mode=join');
            } else {
                navigate('/onboarding?mode=join');
            }
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
            const result = await tenantApi.selfCreate({ name: companyName });
            await applyTenantSetupResult(result);
            if (fromRegister) {
                navigate('/onboarding?mode=create');
            } else {
                navigate('/onboarding?mode=create');
            }
        } catch (err: any) {
            setError(err.message || 'Failed to create company');
        } finally {
            setLoading(false);
        }
    };

    const toggleLang = () => {
        i18n.changeLanguage(i18n.language === 'zh' ? 'en' : 'zh');
    };

    // If not from registration/tenant-selection and user already has tenant, don't show
    if (!fromRegister && !fromTenantSelection && user?.tenant_id) {
        return null;
    }

    return (
        <div className="company-setup-page">
            {/* Language Switcher */}
            <div style={{
                position: 'absolute', top: '16px', right: '16px',
                cursor: 'pointer', fontSize: '13px', color: 'var(--text-secondary)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                width: 42, height: 38, padding: 0, borderRadius: '12px',
                background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)',
                zIndex: 101,
            }} onClick={toggleLang}>
                <IconWorld size={18} stroke={1.8} />
            </div>

            <div className="company-setup-container">
                <div className="company-setup-header company-setup-header--ritual">
                    <div className="onboarding-kicker">{i18n.language.startsWith('zh') ? '第 1 幕 · 给公司起名' : 'Act 1 · Name the company'}</div>
                    <h1>{i18n.language.startsWith('zh') ? '你的公司叫什么？' : 'What is your company called?'}</h1>
                    <p className="company-setup-subtitle">
                        {i18n.language.startsWith('zh')
                            ? '这个名字会出现在门牌、邀请函和所有对外文件上。不用现在完美，之后随时可以重命名。'
                            : 'This name appears on the sign, invites, and shared work. It does not need to be perfect; you can rename it later.'}
                    </p>
                </div>

                {error && (
                    <div className="login-error" style={{ marginBottom: 16 }}>
                        <span><IconAlertTriangle size={14} stroke={1.8} /></span> {error}
                    </div>
                )}

                {allowCreate ? (
                    <form className="company-name-form" onSubmit={handleCreate}>
                        <input
                            value={companyName}
                            onChange={(e) => setCompanyName(e.target.value)}
                            required
                            autoFocus
                            placeholder={i18n.language.startsWith('zh') ? '在这里写下名字' : 'Write the name here'}
                        />
                        <button className="onboarding-primary-btn" type="submit" disabled={loading || !companyName.trim()}>
                            {loading ? <span className="login-spinner" /> : (i18n.language.startsWith('zh') ? '继续' : 'Continue')}
                        </button>
                    </form>
                ) : (
                    <form className="company-name-form" onSubmit={handleJoin}>
                        <input
                            value={inviteCode}
                            onChange={(e) => setInviteCode(e.target.value)}
                            required
                            autoFocus
                            placeholder={t('companySetup.inviteCodePlaceholder', 'e.g. ABC12345')}
                            style={{ textTransform: 'uppercase', letterSpacing: '2px', fontFamily: 'monospace' }}
                        />
                        <button className="onboarding-primary-btn" type="submit" disabled={loading || !inviteCode.trim()}>
                            {loading ? <span className="login-spinner" /> : t('companySetup.joinBtn', 'Join Company')}
                        </button>
                    </form>
                )}

                {allowCreate && (
                    <form className="company-setup-join-inline" onSubmit={handleJoin}>
                        <span>{i18n.language.startsWith('zh') ? '已经有邀请码？' : 'Already have an invitation code?'}</span>
                        <input
                            value={inviteCode}
                            onChange={(e) => setInviteCode(e.target.value)}
                            placeholder={i18n.language.startsWith('zh') ? '从侧门进入' : 'Enter through the side door'}
                        />
                        <button type="submit" disabled={loading || !inviteCode.trim()}>
                            {i18n.language.startsWith('zh') ? '加入' : 'Join'}
                        </button>
                    </form>
                )}
            </div>
        </div>
    );
}
