import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useAuthStore } from '../stores';
import { fetchJson } from '../services/api';

export default function SSOEntry() {
    const { t } = useTranslation();
    const [searchParams] = useSearchParams();
    const navigate = useNavigate();
    const setAuth = useAuthStore((s) => s.setAuth);
    const sid = searchParams.get('sid');
    const complete = searchParams.get('complete') === '1';
    const [error, setError] = useState('');
    const [providers, setProviders] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    // Initialize polling=true when complete=1 to avoid briefly showing
    // "No SSO providers configured." before the first poll completes.
    const [polling, setPolling] = useState(complete);

    useEffect(() => {
        if (!sid) {
            setError(t('sso.missingSessionId'));
            setLoading(false);
            return;
        }

        // 1. Mark as scanned
        fetchJson(`/sso/session/${sid}/scan`, { method: 'PUT' }).catch(() => {});

        // 2. Load SSO configs (skip auto-redirect on completion step)
        if (!complete) {
            fetchJson<any[]>(`/sso/config?sid=${sid}`)
                .then(data => {
                    setProviders(data);
                    setLoading(false);
                    
                    // 3. Detect UA and Auto-redirect if possible
                    const ua = navigator.userAgent.toLowerCase();
                    let targetProvider = '';
                    
                    if (ua.includes('lark') || ua.includes('feishu')) {
                        targetProvider = 'feishu';
                    } else if (ua.includes('dingtalk')) {
                        targetProvider = 'dingtalk';
                    } else if (ua.includes('wxwork')) {
                        targetProvider = 'wecom';
                    }

                    if (targetProvider) {
                        const p = data.find(it => it.provider_type === targetProvider);
                        if (p && p.url) {
                            window.location.href = p.url;
                        }
                    }
                })
                .catch(() => {
                    setError(t('sso.failedToLoadConfig'));
                    setLoading(false);
                });
        } else {
            setLoading(false);
        }
    }, [sid, complete]);

    useEffect(() => {
        if (!sid) return;
        let cancelled = false;
        let timer: number | undefined;

        const poll = async () => {
            if (cancelled) return;
            try {
                setPolling(true);
                const res = await fetchJson<any>(`/sso/session/${sid}/status`);
                if (res?.access_token && res?.user) {
                    setAuth(res.user, res.access_token);
                    if (res.user && !res.user.tenant_id) {
                        navigate('/setup-company');
                    } else {
                        navigate('/');
                    }
                    return;
                }
                if (res?.status === 'expired') {
                    setError(t('sso.sessionExpired'));
                    return;
                }
                if (res?.error_msg) {
                    setError(res.error_msg);
                    return;
                }
            } catch {
                // ignore transient errors
            } finally {
                setPolling(false);
            }
            timer = window.setTimeout(poll, 1500);
        };

        poll();

        return () => {
            cancelled = true;
            if (timer) window.clearTimeout(timer);
        };
    }, [sid, setAuth, navigate]);

    if (loading) {
        return (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100vh', padding: '20px', textAlign: 'center' }}>
                <div className="login-spinner" style={{ width: '40px', height: '40px', marginBottom: '20px' }}></div>
                <p>{t('sso.redirectingToLogin')}</p>
            </div>
        );
    }

    if (error) {
        return (
            <div style={{ padding: '40px', textAlign: 'center' }}>
                <h3 style={{ color: 'var(--error)', display: 'inline-flex', alignItems: 'center', gap: '6px' }}><IconAlertTriangle size={18} stroke={1.8} /> {t('sso.error')}</h3>
                <p>{error}</p>
            </div>
        );
    }

    // When complete=1, only show a completion spinner — no provider selection UI
    if (complete) {
        return (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100vh', padding: '20px', textAlign: 'center' }}>
                <div className="login-spinner" style={{ width: '40px', height: '40px', marginBottom: '20px' }}></div>
                <p>{t('sso.completingLogin')}</p>
            </div>
        );
    }

    return (
        <div style={{ padding: '40px', textAlign: 'center' }}>
            <h2>{t('sso.loginTitle')}</h2>
            <p style={{ color: 'var(--text-secondary)', marginBottom: '30px' }}>{t('sso.selectLoginMethod')}</p>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {providers.map(p => (
                    <button 
                        key={p.provider_type} 
                        className="btn btn-primary" 
                        style={{ padding: '12px', fontSize: '16px' }}
                        onClick={() => window.location.href = p.url}
                    >
                        {t('sso.loginWith', { provider: p.name })}
                    </button>
                ))}
                
                {providers.length === 0 && (
                    <p style={{ color: 'var(--text-tertiary)' }}>
                        {polling ? t('sso.completingLogin') : t('sso.noProviders')}
                    </p>
                )}
            </div>
        </div>
    );
}
