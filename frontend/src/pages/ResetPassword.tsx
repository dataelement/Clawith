import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { IconAlertTriangle, IconCheck } from '@tabler/icons-react';
import { authApi } from '../services/api';

export default function ResetPassword() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const [params] = useSearchParams();
    const token = useMemo(() => params.get('token') || '', [params]);
    const [password, setPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [success, setSuccess] = useState(false);

    useEffect(() => {
        document.documentElement.setAttribute('data-theme', localStorage.getItem('theme') || 'light');
    }, []);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');

        if (!token) {
            setError(t('auth.resetPasswordMissingToken', 'Reset token is missing from the link.'));
            return;
        }
        if (password.length < 6) {
            setError(t('auth.resetPasswordTooShort', 'New password must be at least 6 characters.'));
            return;
        }
        if (password !== confirmPassword) {
            setError(t('auth.resetPasswordMismatch', 'Passwords do not match.'));
            return;
        }

        setLoading(true);
        try {
            await authApi.resetPassword({ token, new_password: password });
            setSuccess(true);
            window.setTimeout(() => navigate('/login'), 1200);
        } catch (err: any) {
            setError(err.message || t('auth.resetPasswordFailed', 'Failed to reset password'));
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
                        <h2 className="login-form-title">{t('auth.resetPasswordTitle', 'Reset password')}</h2>
                        <p className="login-form-subtitle">
                            {t('auth.resetPasswordSubtitle', 'Choose a new password for your account.')}
                        </p>
                    </div>

                    {error && (
                        <div className="login-error">
                            <span><IconAlertTriangle size={14} stroke={1.8} /></span> {error}
                        </div>
                    )}

                    {success && (
                        <div className="login-error" style={{ background: 'rgba(34,197,94,0.14)', borderColor: 'rgba(34,197,94,0.35)', color: '#dcfce7' }}>
                            <span><IconCheck size={14} stroke={1.8} /></span> {t('auth.resetPasswordSuccess', 'Password updated. Redirecting to login...')}
                        </div>
                    )}

                    <form onSubmit={handleSubmit} className="login-form">
                        <div className="login-field">
                            <label>{t('auth.newPassword', 'New password')}</label>
                            <input
                                type="password"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                required
                                autoFocus
                                placeholder={t('auth.newPasswordPlaceholder', 'At least 6 characters')}
                            />
                        </div>

                        <div className="login-field">
                            <label>{t('auth.confirmNewPassword', 'Confirm new password')}</label>
                            <input
                                type="password"
                                value={confirmPassword}
                                onChange={(e) => setConfirmPassword(e.target.value)}
                                required
                                placeholder={t('auth.confirmNewPasswordPlaceholder', 'Repeat your new password')}
                            />
                        </div>

                        <button className="login-submit" type="submit" disabled={loading || success}>
                            {loading ? <span className="login-spinner" /> : t('auth.updatePassword', 'Update password')}
                        </button>
                    </form>

                    <div className="login-switch">
                        <Link to="/login">{t('auth.backToLogin', 'Back to login')}</Link>
                    </div>
                </div>
            </div>
        </div>
    );
}
