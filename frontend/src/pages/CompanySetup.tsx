import { useState, useEffect } from 'react';
import { useNavigate, useLocation, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { IconAlertTriangle, IconArrowRight, IconX } from '@tabler/icons-react';
import { useAuthStore } from '../stores';
import { tenantApi, authApi } from '../services/api';
import { AtlasFrame, StarField } from '../components/atlas';

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
    const [showJoinModal, setShowJoinModal] = useState(false);
    const [joinError, setJoinError] = useState('');
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
        setJoinError('');
        setError('');
        setLoading(true);
        try {
            const result = await tenantApi.join(inviteCode);
            await applyTenantSetupResult(result);
            navigate('/onboarding?mode=join');
        } catch (err: any) {
            const msg = err.message || 'Failed to join company';
            if (showJoinModal) setJoinError(msg);
            else setError(msg);
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

    const isZh = i18n.language.startsWith('zh');
    const MAX_LEN = 48;

    return (
        <AtlasFrame onToggleLang={toggleLang}>
            <StarField density="medium" seed={17} />
            <svg
                className="atlas-name-bg-circle"
                viewBox="0 0 800 800"
                aria-hidden="true"
            >
                <circle cx="400" cy="400" r="320" fill="none" stroke="currentColor" strokeWidth="0.5" />
                <circle cx="400" cy="400" r="220" fill="none" stroke="currentColor" strokeWidth="0.5" strokeDasharray="2 4" />
            </svg>
            <div className="atlas-screen-center atlas-screen-pad">
                <div className="atlas-name-stack">
                    <h1 className="atlas-display atlas-display--centered">
                        {isZh ? (
                            <><span>开始吧。</span><br /><span>给你的公司起个名字。</span></>
                        ) : (
                            <><span>Let's begin.</span><br /><span>Name your Company.</span></>
                        )}
                    </h1>
                    <p className="atlas-body atlas-body--muted atlas-name-sub">
                        {isZh
                            ? '每个宇宙都从一个名字开始。让它具体、属于你 —— 之后随时能改。'
                            : 'Every universe begins with a name. Make it specific, make it yours — it can change later.'}
                    </p>

                    {error && (
                        <div className="atlas-error">
                            <IconAlertTriangle size={14} stroke={1.8} /> {error}
                        </div>
                    )}

                    {allowCreate ? (
                        <form className="atlas-name-form" onSubmit={handleCreate}>
                            <input
                                className="atlas-input atlas-input--serif-lg atlas-name-input"
                                value={companyName}
                                maxLength={MAX_LEN}
                                onChange={(e) => setCompanyName(e.target.value)}
                                required
                                autoFocus
                                placeholder={isZh ? '在这里写下名字' : 'Atlas & Co.'}
                            />
                            <div className="atlas-input-meta atlas-input-meta--right">
                                <span>{companyName.length} / {MAX_LEN}</span>
                            </div>
                            <button
                                className="atlas-btn atlas-btn--primary atlas-name-cta"
                                type="submit"
                                disabled={loading || !companyName.trim()}
                            >
                                {loading ? '…' : (isZh ? '继续' : 'Continue')}
                                <IconArrowRight size={14} stroke={1.5} />
                            </button>
                            <button
                                type="button"
                                className="atlas-text-underline"
                                onClick={() => { setJoinError(''); setShowJoinModal(true); }}
                            >
                                {isZh ? '加入已有团队？' : 'Joining an existing team?'}
                            </button>
                        </form>
                    ) : (
                        <form className="atlas-name-form" onSubmit={handleJoin}>
                            <input
                                className="atlas-input atlas-input--serif-lg atlas-name-input"
                                value={inviteCode}
                                onChange={(e) => setInviteCode(e.target.value)}
                                required
                                autoFocus
                                placeholder={t('companySetup.inviteCodePlaceholder', 'e.g. ABC12345')}
                                style={{ textTransform: 'uppercase', letterSpacing: '4px', fontFamily: 'var(--font-mono)' }}
                            />
                            <div className="atlas-input-meta">
                                <span>{isZh ? '邀请码' : 'INVITATION CODE'}</span>
                                <span>{isZh ? '必填' : 'REQUIRED'}</span>
                            </div>
                            <button
                                className="atlas-btn atlas-btn--primary atlas-name-cta"
                                type="submit"
                                disabled={loading || !inviteCode.trim()}
                            >
                                {loading ? '…' : t('companySetup.joinBtn', 'Join Company')}
                                <IconArrowRight size={14} stroke={1.5} />
                            </button>
                        </form>
                    )}
                </div>
            </div>

            {showJoinModal && (
                <div
                    className="atlas-modal-overlay"
                    onClick={() => !loading && setShowJoinModal(false)}
                >
                    <div className="atlas-modal" onClick={(e) => e.stopPropagation()}>
                        <button
                            type="button"
                            className="atlas-modal-close"
                            onClick={() => setShowJoinModal(false)}
                            disabled={loading}
                            aria-label="Close"
                        >
                            <IconX size={18} stroke={1.8} />
                        </button>
                        <h2 className="atlas-modal-title">
                            {isZh ? '加入已有团队' : 'Join an existing team'}
                        </h2>
                        <p className="atlas-modal-desc">
                            {isZh
                                ? '输入团队管理员发给你的邀请码。'
                                : 'Enter the invitation code your team admin shared with you.'}
                        </p>
                        <form onSubmit={handleJoin} className="atlas-form">
                            <input
                                className="atlas-input-standalone"
                                value={inviteCode}
                                onChange={(e) => setInviteCode(e.target.value)}
                                required
                                autoFocus
                                placeholder={t('companySetup.inviteCodePlaceholder', 'e.g. ABC12345')}
                                style={{ textTransform: 'uppercase', letterSpacing: '2px', fontFamily: 'var(--font-mono)' }}
                            />
                            {joinError && (
                                <div className="atlas-error">
                                    <IconAlertTriangle size={14} stroke={1.8} /> {joinError}
                                </div>
                            )}
                            <button
                                className="atlas-btn atlas-btn--primary"
                                type="submit"
                                disabled={loading || !inviteCode.trim()}
                            >
                                {loading ? '…' : (isZh ? '加入' : 'Join')}
                                <IconArrowRight size={14} stroke={1.5} />
                            </button>
                        </form>
                    </div>
                </div>
            )}
        </AtlasFrame>
    );
}
