import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { fetchJson } from '../services/api';
import { useAuthStore } from '../stores';

export default function OAuthCallback() {
    const navigate = useNavigate();
    const { provider = '' } = useParams();
    const setAuth = useAuthStore((s) => s.setAuth);
    const [error, setError] = useState('');

    useEffect(() => {
        const code = new URLSearchParams(window.location.search).get('code');
        const state = new URLSearchParams(window.location.search).get('state') || '';
        const oauthError = new URLSearchParams(window.location.search).get('error');

        if (oauthError) {
            setError(oauthError);
            return;
        }
        if (!provider || !code) {
            setError('Missing OAuth callback parameters');
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
                setAuth(res.user, res.access_token);
                if (res.needs_company_setup || !res.user?.tenant_id) {
                    navigate('/setup-company', { replace: true });
                    return;
                }
                navigate('/', { replace: true });
            })
            .catch((err: any) => {
                setError(err.message || 'OAuth login failed');
            });
    }, [navigate, provider, setAuth]);

    if (error) {
        return (
            <div style={{ padding: '40px', textAlign: 'center' }}>
                <h3 style={{ color: 'var(--error)' }}>OAuth Login Failed</h3>
                <p>{error}</p>
            </div>
        );
    }

    return (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100vh', padding: '20px', textAlign: 'center' }}>
            <div className="login-spinner" style={{ width: '40px', height: '40px', marginBottom: '20px' }}></div>
            <p>Completing sign-in...</p>
        </div>
    );
}
