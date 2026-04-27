import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuthStore } from './stores';
import { useEffect, useState, useRef } from 'react';
import { authApi } from './services/api';
import Login from './pages/Login';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
import VerifyEmail from './pages/VerifyEmail';
import CompanySetup from './pages/CompanySetup';
import Layout from './pages/Layout';
import Dashboard from './pages/Dashboard';
import Plaza from './pages/Plaza';
import AgentDetail from './pages/AgentDetail';
import AgentCreate from './pages/AgentCreate';
import Messages from './pages/Messages';
import EnterpriseSettings from './pages/EnterpriseSettings';
import InvitationCodes from './pages/InvitationCodes';
import AdminCompanies from './pages/AdminCompanies';
import SSOEntry from './pages/SSOEntry';
import OKR from './pages/OKR';

function ProtectedRoute({ children }: { children: React.ReactNode }) {
    const token = useAuthStore((s) => s.token);
    const user = useAuthStore((s) => s.user);
    if (!token) return <Navigate to="/login" replace />;
    // Force company setup for users without a tenant
    if (user && !user.tenant_id) return <Navigate to="/setup-company" replace />;
    
    // Force email verification if not active/verified
    if (user && !user.is_active) return <Navigate to="/verify-email" state={{ email: user.email }} replace />;
    
    return <>{children}</>;
}

/* ─── Notification Bar ─── */
function NotificationBar() {
    const [config, setConfig] = useState<{ enabled: boolean; text: string } | null>(null);
    const [dismissed, setDismissed] = useState(false);
    
    const textRef = useRef<HTMLSpanElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const [isMarquee, setIsMarquee] = useState(false);

    useEffect(() => {
        fetch('/api/enterprise/system-settings/notification_bar/public')
            .then(r => r.ok ? r.json() : null)
            .then(d => { if (d) setConfig(d); })
            .catch(() => { });
    }, []);

    // Check sessionStorage for dismissal (keyed by text so new messages re-show)
    useEffect(() => {
        if (config?.text) {
            const key = `notification_bar_dismissed_${btoa(encodeURIComponent(config.text))}`;
            if (sessionStorage.getItem(key)) setDismissed(true);
        }
    }, [config?.text]);

    // Manage body class: add when visible, remove when hidden or dismissed
    const isVisible = !!config?.enabled && !!config?.text && !dismissed;
    useEffect(() => {
        if (isVisible) {
            document.body.classList.add('has-notification-bar');
        } else {
            document.body.classList.remove('has-notification-bar');
        }
        return () => { document.body.classList.remove('has-notification-bar'); };
    }, [isVisible]);

    // Dynamic marquee if text is too wide
    useEffect(() => {
        if (!isVisible) return;
        const checkWidth = () => {
            if (textRef.current && containerRef.current) {
                // Determine if text is wider than its container
                setIsMarquee(textRef.current.scrollWidth > containerRef.current.clientWidth);
            }
        };
        // Small delay to ensure DOM is fully rendered
        const timer = setTimeout(checkWidth, 100);
        window.addEventListener('resize', checkWidth);
        return () => {
            clearTimeout(timer);
            window.removeEventListener('resize', checkWidth);
        };
    }, [isVisible, config?.text]);

    if (!isVisible) return null;

    const handleDismiss = () => {
        const key = `notification_bar_dismissed_${btoa(encodeURIComponent(config!.text))}`;
        sessionStorage.setItem(key, '1');
        setDismissed(true);
    };

    // Calculate dynamic duration: longer text = longer animation so speed is consistent
    const duration = config ? Math.max(20, config.text.length * 0.2) + 's' : '20s';

    return (
        <div className="notification-bar">
            <div className="notification-bar-inner" ref={containerRef}>
                <span 
                    ref={textRef} 
                    className={`notification-bar-text ${isMarquee ? 'marquee' : ''}`}
                    title={config!.text}
                    style={isMarquee ? { animationDuration: duration } : {}}
                >
                    {config!.text}
                </span>
            </div>
            <button className="notification-bar-close" onClick={handleDismiss} aria-label="Close">✕</button>
        </div>
    );
}

export default function App() {
    const { token, setAuth, user } = useAuthStore();
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        // Initialize theme on app mount (ensures login page gets correct theme)
        const savedTheme = localStorage.getItem('theme') || 'dark';
        document.documentElement.setAttribute('data-theme', savedTheme);

        // Cross-domain tenant switch: the backend appends ?token=<jwt> to the redirect URL
        // so the new domain receives a fresh scoped token. Consume it here (before any other
        // auth logic) so it always takes precedence over a stale token in localStorage.
        //
        // IMPORTANT: Only apply this on paths that do NOT use ?token= for their own purposes.
        // /reset-password and /verify-email both receive a one-time token for their own flow —
        // consuming it here as a session JWT would call /auth/me, fail, log out the user,
        // and redirect them to /login instead of showing the correct page.
        const urlParams = new URLSearchParams(window.location.search);
        const urlToken = urlParams.get('token');
        const currentPath = window.location.pathname;
        const pathsWithOwnToken = ['/reset-password', '/verify-email'];
        let effectiveToken = token;

        if (urlToken && !pathsWithOwnToken.includes(currentPath)) {
            // Persist the new token and update the zustand store's in-memory value
            localStorage.setItem('token', urlToken);
            useAuthStore.setState({ token: urlToken, user: null });
            effectiveToken = urlToken;

            // Remove token from URL to prevent it from leaking into browser history
            // and to avoid re-applying it on a manual page refresh.
            urlParams.delete('token');
            const cleanSearch = urlParams.toString();
            const cleanUrl = window.location.pathname
                + (cleanSearch ? `?${cleanSearch}` : '')
                + window.location.hash;
            window.history.replaceState({}, '', cleanUrl);
        }


        if (effectiveToken && !user) {
            authApi.me()
                .then((u) => setAuth(u, effectiveToken!))
                .catch(() => useAuthStore.getState().logout())
                .finally(() => setLoading(false));
        } else {
            setLoading(false);
        }
    }, []);


    if (loading) {
        return (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', color: 'var(--text-tertiary)' }}>
                加载中...
            </div>
        );
    }

    return (
        <>
            <NotificationBar />
            <Routes>
                <Route path="/login" element={<Login />} />
                <Route path="/forgot-password" element={<ForgotPassword />} />
                <Route path="/reset-password" element={<ResetPassword />} />
                <Route path="/verify-email" element={<VerifyEmail />} />
                <Route path="/sso/entry" element={<SSOEntry />} />
                <Route path="/setup-company" element={<CompanySetup />} />
                <Route path="/" element={<ProtectedRoute><Layout /></ProtectedRoute>}>
                    <Route index element={<Navigate to="/plaza" replace />} />
                    <Route path="dashboard" element={<Dashboard />} />
                    <Route path="plaza" element={<Plaza />} />
                    <Route path="agents/new" element={<AgentCreate />} />
                    <Route path="agents/:id" element={<AgentDetail />} />
                    {/* NOTE: Chat is a tab inside AgentDetail (#chat), not a separate route.
                        The deprecated /agents/:id/chat path is intentionally removed. */}
                    <Route path="messages" element={<Messages />} />
                    <Route path="enterprise" element={<EnterpriseSettings />} />
                    <Route path="okr" element={<OKR />} />
                    <Route path="invitations" element={<InvitationCodes />} />
                    <Route path="admin/platform-settings" element={<AdminCompanies />} />
                </Route>
            </Routes>
        </>
    );
}
