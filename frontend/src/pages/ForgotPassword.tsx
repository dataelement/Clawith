import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { IconAlertTriangle, IconBulb, IconCheck } from '@tabler/icons-react';
import { authApi } from '../services/api';

export default function ForgotPassword() {
    const { t } = useTranslation();
    const [email, setEmail] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [message, setMessage] = useState('');
    const [showHintForm, setShowHintForm] = useState(false);
    const [usernameHint, setUsernameHint] = useState('');
    const [hintResult, setHintResult] = useState('');

    useEffect(() => {
        document.documentElement.setAttribute('data-theme', localStorage.getItem('theme') || 'light');
    }, []);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setMessage('');
        setLoading(true);

        try {
            const res = await authApi.forgotPassword({ email: email.trim() });
            setMessage(res.message);
        } catch (err: any) {
            setError(err.message || t('auth.forgotPasswordRequestFailed', 'Failed to request password reset'));
        } finally {
            setLoading(false);
        }
    };

    const handleGetHint = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setMessage('');
        setHintResult('');
        setLoading(true);

        try {
            const res = await authApi.emailHint(usernameHint.trim());
            setHintResult(res.hint);
            setShowHintForm(false);
        } catch (err: any) {
            setError(err.message || t('auth.emailHintFailed', 'Failed to get email hint. User may not exist.'));
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="login-page">
            <div className="login-form-panel" style={{ width: '100%', display: 'flex', justifyContent: 'center' }}>
                <div className="login-form-wrapper" style={{ maxWidth: '460px' }}>
                    <div className="login-form-header">
                        <div className="login-form-logo">
                            <img src="/logo-black.png" className="login-logo-img" alt="" style={{ width: 28, height: 28, marginRight: 8, verticalAlign: 'middle' }} />
                            Clawith
                        </div>
                        <h2 className="login-form-title">{t('auth.forgotPasswordTitle', 'Forgot password')}</h2>
                        <p className="login-form-subtitle">
                            {t('auth.forgotPasswordSubtitle', 'Enter your account email and we will send a reset link if the account exists.')}
                        </p>
                    </div>

                    {error && (
                        <div className="login-error">
                            <span><IconAlertTriangle size={14} stroke={1.8} /></span> {error}
                        </div>
                    )}

                    {message && (
                        <div className="login-error" style={{ background: 'rgba(34,197,94,0.14)', borderColor: 'rgba(34,197,94,0.35)', color: '#dcfce7' }}>
                            <span><IconCheck size={14} stroke={1.8} /></span> {message}
                        </div>
                    )}

                    {hintResult && (
                        <div className="login-error" style={{ background: 'rgba(56,189,248,0.1)', borderColor: 'rgba(56,189,248,0.3)', color: '#bae6fd' }}>
                            <IconBulb size={14} stroke={1.8} style={{ marginRight: '6px' }} />
                            {t('auth.emailHintResult', 'Email hint')}: <strong>{hintResult}</strong>
                        </div>
                    )}

                    {!showHintForm ? (
                        <form onSubmit={handleSubmit} className="login-form">
                            <div className="login-field">
                                <label>{t('auth.email', 'Email')}</label>
                                <input
                                    type="email"
                                    value={email}
                                    onChange={(e) => setEmail(e.target.value)}
                                    required
                                    autoFocus
                                    placeholder={t('auth.emailPlaceholderReset', 'name@company.com')}
                                />
                            </div>

                            <button className="login-submit" type="submit" disabled={loading || !email.trim()}>
                                {loading ? <span className="login-spinner" /> : t('auth.sendResetLink', 'Send reset link')}
                            </button>

                            <div style={{ marginTop: '16px', textAlign: 'center', fontSize: '13px' }}>
                                <button type="button" onClick={() => setShowHintForm(true)} style={{ background: 'none', border: 'none', color: 'var(--accent-primary)', cursor: 'pointer', padding: 0 }}>
                                    {t('auth.forgotEmailHint', 'Forgot which email you used?')}
                                </button>
                            </div>
                        </form>
                    ) : (
                        <form onSubmit={handleGetHint} className="login-form">
                            <div className="login-field">
                                <label>{t('auth.username', 'Username')}</label>
                                <input
                                    type="text"
                                    value={usernameHint}
                                    onChange={(e) => setUsernameHint(e.target.value)}
                                    required
                                    autoFocus
                                    placeholder={t('auth.usernamePlaceholderHint', 'Enter your account username')}
                                />
                            </div>

                            <button className="login-submit" type="submit" disabled={loading || !usernameHint.trim()}>
                                {loading ? <span className="login-spinner" /> : t('auth.getEmailHint', 'Get Email Hint')}
                            </button>

                            <div style={{ marginTop: '16px', textAlign: 'center', fontSize: '13px' }}>
                                <button type="button" onClick={() => setShowHintForm(false)} style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', padding: 0 }}>
                                    {t('common.cancel', 'Cancel')}
                                </button>
                            </div>
                        </form>
                    )}

                    <div className="login-switch">
                        {t('auth.rememberedPassword', 'Remembered your password?')} <Link to="/login">{t('auth.backToLogin', 'Back to login')}</Link>
                    </div>
                </div>
            </div>
        </div>
    );
}
