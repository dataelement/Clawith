import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { fetchJson } from '../services/api';
import { useAuthStore } from '../stores';

interface TenantChoice {
    tenant_id: string;
    tenant_name: string;
    tenant_slug: string;
    logo_url?: string;
}

export default function OAuthCallback() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const { provider = '' } = useParams();
    const setAuth = useAuthStore((s) => s.setAuth);
    const [error, setError] = useState('');
    const [tenants, setTenants] = useState<TenantChoice[] | null>(null);
    const [pendingToken, setPendingToken] = useState('');
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (tenants) return; // Already showing selection UI

        const code = new URLSearchParams(window.location.search).get('code');
        const state = new URLSearchParams(window.location.search).get('state') || '';
        const oauthError = new URLSearchParams(window.location.search).get('error');

        if (oauthError) {
            setError(oauthError);
            return;
        }
        if (!provider || !code) {
            setError(t('oauth.missingParams'));
            return;
        }

        fetchJson<any>(`/auth/${provider}/callback`, {
            method: 'POST',
            body: JSON.stringify({
                code,
                state,
                redirect_uri: `${window.location.origin}/oauth/callback/${provider}`,
            }),
        })
            .then((res) => {
                // Step 1 result: multi-tenant selection needed
                if (res.requires_tenant_selection) {
                    setTenants(res.tenants);
                    setPendingToken(res.pending_token);
                    return;
                }
                // Normal single-tenant login
                setAuth(res.user, res.access_token);
                if (res.needs_company_setup || !res.user?.tenant_id) {
                    navigate('/setup-company', { replace: true });
                    return;
                }
                navigate('/', { replace: true });
            })
            .catch((err: any) => {
                setError(err.message || t('oauth.oauthLoginFailed'));
            });
    }, [navigate, provider, setAuth, t, tenants]);

    const handleTenantSelect = async (tenantId: string) => {
        setLoading(true);
        setError('');
        try {
            const state = new URLSearchParams(window.location.search).get('state') || '';
            // Step 2: re-call the same callback endpoint — no code, just pending_token + tenant_id
            const res = await fetchJson<any>(`/auth/${provider}/callback`, {
                method: 'POST',
                body: JSON.stringify({
                    state,
                    pending_token: pendingToken,
                    tenant_id: tenantId,
                }),
            });
            setAuth(res.user, res.access_token);
            if (res.needs_company_setup || !res.user?.tenant_id) {
                navigate('/setup-company', { replace: true });
                return;
            }
            navigate('/', { replace: true });
        } catch (err: any) {
            setError(err.message || t('oauth.loginFailed'));
            setLoading(false);
        }
    };

    // ── Tenant selection UI ──────────────────────────────────────────
    if (tenants) {
        return (
            <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                justifyContent: 'center', height: '100vh', padding: '20px',
            }}>
                <div style={{
                    background: '#fbfbfa',
                    borderRadius: '16px',
                    padding: '32px',
                    maxWidth: '420px',
                    width: '100%',
                    border: '1px solid rgba(17, 17, 20, 0.1)',
                    boxShadow: '0 24px 80px rgba(17,17,20,0.18), 0 0 0 1px rgba(255,255,255,0.55) inset',
                }}>
                    <h2 style={{ fontSize: '18px', fontWeight: 700, marginBottom: '4px', color: '#17171a', textAlign: 'center' }}>
                        {t('oauth.selectCompany')}
                    </h2>
                    <p style={{ fontSize: '13px', color: '#767681', marginBottom: '20px', textAlign: 'center', lineHeight: '1.5' }}>
                        {t('oauth.selectCompanyHint')}
                    </p>

                    {error && (
                        <div style={{
                            padding: '10px 14px', borderRadius: '8px', marginBottom: '16px',
                            background: 'rgba(220,38,38,0.08)', color: 'var(--error, #dc2626)',
                            fontSize: '13px',
                        }}>
                            {error}
                        </div>
                    )}

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {tenants.map((tenant) => (
                            <button
                                key={tenant.tenant_id}
                                onClick={() => handleTenantSelect(tenant.tenant_id)}
                                disabled={loading}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: '12px',
                                    padding: '12px 16px', borderRadius: '10px',
                                    border: '1px solid rgba(17,17,20,0.1)',
                                    background: '#ffffff',
                                    cursor: loading ? 'not-allowed' : 'pointer',
                                    opacity: loading ? 0.6 : 1,
                                    transition: 'background 0.15s, border-color 0.15s',
                                    textAlign: 'left', width: '100%',
                                }}
                                onMouseEnter={(e) => {
                                    if (!loading) {
                                        (e.currentTarget as HTMLButtonElement).style.background = '#f2f2f0';
                                        (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(17,17,20,0.2)';
                                    }
                                }}
                                onMouseLeave={(e) => {
                                    (e.currentTarget as HTMLButtonElement).style.background = '#ffffff';
                                    (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(17,17,20,0.1)';
                                }}
                            >
                                {tenant.logo_url && (
                                    <img src={tenant.logo_url} alt="" style={{ width: 28, height: 28, borderRadius: 6, objectFit: 'cover' }} />
                                )}
                                <div>
                                    <div style={{ fontSize: '14px', fontWeight: 500, color: '#2b2b31' }}>{tenant.tenant_name}</div>
                                    {tenant.tenant_slug && (
                                        <div style={{ fontSize: '12px', color: '#767681' }}>{tenant.tenant_slug}</div>
                                    )}
                                </div>
                            </button>
                        ))}
                    </div>
                </div>
            </div>
        );
    }

    // ── Error state ──────────────────────────────────────────────────
    if (error) {
        return (
            <div style={{ padding: '40px', textAlign: 'center' }}>
                <h3 style={{ color: 'var(--error)' }}>{t('oauth.loginFailedTitle')}</h3>
                <p>{error}</p>
            </div>
        );
    }

    // ── Loading state ────────────────────────────────────────────────
    return (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100vh', padding: '20px', textAlign: 'center' }}>
            <div className="login-spinner" style={{ width: '40px', height: '40px', marginBottom: '20px' }}></div>
            <p>{t('oauth.completingSignIn')}</p>
        </div>
    );
}
