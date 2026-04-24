import { useState, useEffect } from 'react';
import { Link, useSearchParams, useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { authApi } from '../services/api';
import { useAuthStore } from '../stores';
import { useToast } from '../components/Toast/ToastProvider';

export default function VerifyEmail() {
    const { t, i18n } = useTranslation();
    const toast = useToast();
    const [searchParams] = useSearchParams();
    const navigate = useNavigate();
    const location = useLocation();
    const { setAuth, user } = useAuthStore();

    // Get email from location state (passed from register) or from URL params
    const [email, setEmail] = useState<string>((location.state as any)?.email || searchParams.get('email') || user?.email || '');

    // Support both 'token' and 'code' from URL
    const urlToken = searchParams.get('token') || searchParams.get('code');

    const [code, setCode] = useState(urlToken || '');
    const [status, setStatus] = useState<'idle' | 'verifying' | 'success' | 'error'>('idle');
    const [message, setMessage] = useState('');
    const [loading, setLoading] = useState(false);

    const isChinese = i18n.language?.startsWith('zh');
 
    useEffect(() => {
        if (user?.is_active) {
            navigate('/');
        }
    }, [user, navigate]);

    const handleVerify = async (tokenToUse: string) => {
        if (!tokenToUse || tokenToUse.length < 6) return;

        setLoading(true);
        setStatus('verifying');
        setError('');

        try {
            const res = await authApi.verifyEmail(tokenToUse);
            setStatus('success');
            setMessage(isChinese ? '邮箱验证成功！' : 'Email verified successfully!');

            // Auto-login with the returned token
            if (res.access_token && res.user) {
                setAuth(res.user, res.access_token);

                // Redirect based on needs_company_setup
                setTimeout(() => {
                    if (res.needs_company_setup) {
                        navigate('/setup-company');
                    } else {
                        navigate('/');
                    }
                }, 1500); // Short delay to show success message
            }
        } catch (err: any) {
            setStatus('error');
            setMessage(err.message || (isChinese ? '验证失败，请检查验证码是否正确' : 'Verification failed, please check the code'));
        } finally {
            setLoading(false);
        }
    };

    // Auto-verify if token is in URL
    useEffect(() => {
        if (urlToken) {
            handleVerify(urlToken);
        }
    }, [urlToken]);

    const handleResend = async () => {
        if (!email) {
            setStatus('error');
            setMessage(isChinese ? '请输入邮箱地址' : 'Please enter your email address');
            return;
        }
        setLoading(true);
        try {
            await authApi.resendVerification(email);
            toast.success(isChinese ? '验证码已重发，请检查您的邮箱' : 'Verification code resent. Please check your email.');
        } catch (err: any) {
            toast.error(isChinese ? '重发失败' : 'Failed to resend verification', { details: String(err?.message || err) });
        } finally {
            setLoading(false);
        }
    };

    const setError = (msg: string) => {
        if (msg) {
            setStatus('error');
            setMessage(msg);
        }
    };

    return (
        <div style={{
            minHeight: '100vh',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'var(--bg-primary)',
            padding: '20px',
        }}>
            <div className="company-setup-container" style={{ maxWidth: '440px', width: '100%' }}>
                <div className="company-setup-header">
                    <div style={{ fontSize: '48px', marginBottom: '16px' }}>
                        {status === 'success' ? '✅' : status === 'error' ? '❌' : '📧'}
                    </div>
                    <h1>{isChinese ? '邮箱验证' : 'Email Verification'}</h1>
                    <p className="company-setup-subtitle">
                        {email
                            ? (isChinese ? `验证码已发送至 ${email}` : `A 6-digit verification code has been sent to ${email}`)
                            : (isChinese ? '请输入您收到的 6 位验证码' : 'Please enter the 6-digit verification code sent to your email')}
                    </p>
                </div>

                {message && (
                    <div className={status === 'success' ? 'login-success' : 'login-error'} style={{ marginBottom: 20 }}>
                        <span>{status === 'success' ? '✓' : '⚠'}</span> {message}
                    </div>
                )}

                {status !== 'success' && (
                    <div className="company-setup-panel" style={{ padding: '32px' }}>
                        <div className="login-field" style={{ marginBottom: 24 }}>
                            <label style={{ textAlign: 'center', display: 'block', marginBottom: 16 }}>
                                {isChinese ? '6 位数字验证码' : '6-Digit Verification Code'}
                            </label>
                            <input
                                value={code}
                                onChange={(e) => setCode(e.target.value.replace(/[^0-9]/g, '').slice(0, 6))}
                                placeholder="000000"
                                style={{
                                    fontSize: '32px',
                                    textAlign: 'center',
                                    letterSpacing: '8px',
                                    fontWeight: 'bold',
                                    height: '64px',
                                    fontFamily: 'monospace'
                                }}
                                disabled={loading}
                            />
                        </div>

                        <button 
                            className="login-submit" 
                            onClick={() => handleVerify(code)}
                            disabled={loading || code.length < 6}
                        >
                            {loading ? <span className="login-spinner" /> : (isChinese ? '提交验证' : 'Verify Email')}
                        </button>

                        <div style={{ marginTop: 24, textAlign: 'center', fontSize: '14px' }}>
                            <span style={{ color: 'var(--text-secondary)' }}>
                                {isChinese ? '没收到邮件？' : "Didn't receive the email?"}{' '}
                            </span>
                            <button 
                                onClick={handleResend}
                                disabled={loading}
                                style={{ 
                                    background: 'none', border: 'none', color: 'var(--accent-primary)', 
                                    cursor: 'pointer', padding: 0, fontSize: '14px', fontWeight: 500 
                                }}
                            >
                                {isChinese ? '点击重发' : 'Click to resend'}
                            </button>
                        </div>
                    </div>
                )}

                {status === 'success' && (
                    <div style={{ textAlign: 'center', marginTop: 8 }}>
                        <p style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>
                            {isChinese ? '正在跳转...' : 'Redirecting...'}
                        </p>
                    </div>
                )}

                <div style={{ textAlign: 'center', marginTop: 24 }}>
                    <Link to="/login" style={{ color: 'var(--text-secondary)', fontSize: '14px', textDecoration: 'none' }}>
                        {isChinese ? '← 返回登录' : '← Back to Login'}
                    </Link>
                </div>
            </div>
        </div>
    );
}
