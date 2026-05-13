import { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { Outlet, NavLink, useNavigate, useMatch, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../stores';
import { agentApi, tenantApi, authApi, onboardingApi } from '../services/api';
import { useToast } from '../components/Toast/ToastProvider';

import {
    IconHome,
    IconPlus,
    IconSettings,
    IconUser,
    IconSun,
    IconMoon,
    IconLogout,
    IconWorld,
    IconChevronsLeft,
    IconChevronsRight,
    IconBell,
    IconBuildingMonument,
    IconSearch,
    IconX,
    IconPin,
    IconPinnedOff,
    IconArrowUpRight,
    IconBuilding,
    IconChevronUp,
    IconChevronRight,
    IconCheck,
    IconChevronDown,
} from '@tabler/icons-react';
import { useAppStore } from '../stores';
import TalentMarketModal from '../components/TalentMarketModal';

/* ────── Tabler Icons ────── */
const SidebarIcons = {
    home: <IconHome size={16} stroke={1.5} />,
    plus: <IconPlus size={16} stroke={1.5} />,
    settings: <IconSettings size={16} stroke={1.5} />,
    user: <IconUser size={16} stroke={1.5} />,
    sun: <IconSun size={16} stroke={1.5} />,
    moon: <IconMoon size={16} stroke={1.5} />,
    logout: <IconLogout size={16} stroke={1.5} />,
    globe: <IconWorld size={16} stroke={1.5} />,
    collapse: <IconChevronsLeft size={16} stroke={1.5} />,
    expand: <IconChevronsRight size={16} stroke={1.5} />,
    bell: <IconBell size={16} stroke={1.5} />,
};

/** UI locales: native endonym per row. */
const APP_UI_LANGUAGES: { code: string; nativeLabel: string }[] = [
    { code: 'zh', nativeLabel: '中文' },
    { code: 'en', nativeLabel: 'English' },
];

function resolveUiLangCode(lang: string | undefined): string {
    if (!lang) return 'en';
    if (lang.startsWith('zh')) return 'zh';
    return 'en';
}

const fetchJson = async <T,>(url: string): Promise<T> => {
    const token = localStorage.getItem('token');
    const res = await fetch(`/api${url}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!res.ok) return [] as T;
    return res.json();
};

/* Compute display badge status for an agent */
const getAgentBadgeStatus = (agent: any): string | null => {
    if (agent.status === 'error') return 'error';
    if (agent.status === 'creating') return 'creating';
    // OpenClaw disconnected detection: 60 min timeout
    if (agent.agent_type === 'openclaw' && agent.status === 'running' && agent.openclaw_last_seen) {
        const elapsed = Date.now() - new Date(agent.openclaw_last_seen).getTime();
        if (elapsed > 60 * 60 * 1000) return 'disconnected';
    }
    // idle / running / stopped → no badge
    return null;
};

const getWorkspaceAvatarTone = (name: string): number => {
    let hash = 0;
    for (const char of name) {
        hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
    }
    return (hash % 6) + 1;
};

/* ────── Account Settings Modal ────── */
function AccountSettingsModal({ user, onClose, isChinese }: { user: any; onClose: () => void; isChinese: boolean }) {
    const { setUser } = useAuthStore();
    const [username, setUsername] = useState(user?.username || '');
    const [email, setEmail] = useState(user?.email || '');
    const [displayName, setDisplayName] = useState(user?.display_name || '');
    const [oldPassword, setOldPassword] = useState('');
    const [newPassword, setNewPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [saving, setSaving] = useState(false);
    const [resendingEmail, setResendingEmail] = useState(false);
    const [msg, setMsg] = useState('');
    const [msgType, setMsgType] = useState<'success' | 'error'>('success');

    const showMsg = (text: string, type: 'success' | 'error' = 'success') => {
        setMsg(text); setMsgType(type); setTimeout(() => setMsg(''), 3000);
    };

    const handleSaveProfile = async () => {
        setSaving(true);
        try {
            const token = localStorage.getItem('token');
            const body: any = {};
            if (username !== user?.username) body.username = username;
            if (email !== user?.email) body.email = email;
            if (displayName !== user?.display_name) body.display_name = displayName;
            if (Object.keys(body).length === 0) { showMsg(isChinese ? '没有变更' : 'No changes', 'error'); setSaving(false); return; }
            const res = await fetch('/api/auth/me', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify(body),
            });
            if (!res.ok) { const err = await res.json().catch(() => ({ detail: 'Failed' })); throw new Error(err.detail); }
            const updated = await res.json();
            setUser(updated);
            showMsg(isChinese ? '个人信息已更新' : 'Profile updated');
        } catch (e: any) { showMsg(e.message || 'Failed', 'error'); }
        setSaving(false);
    };

    const handleResendVerification = async () => {
        setResendingEmail(true);
        try {
            const token = localStorage.getItem('token');
            const res = await fetch('/api/auth/resend-verification', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify({ email: user?.email }),
            });
            if (!res.ok) { const err = await res.json().catch(() => ({ detail: 'Failed' })); throw new Error(err.detail); }
            showMsg(isChinese ? '验证邮件已发送，请查收' : 'Verification email sent. Please check your inbox.');
        } catch (e: any) { showMsg(e.message || 'Failed', 'error'); }
        setResendingEmail(false);
    };

    const handleChangePassword = async () => {
        if (!oldPassword || !newPassword) { showMsg(isChinese ? '请填写所有密码字段' : 'Fill all password fields', 'error'); return; }
        if (newPassword.length < 6) { showMsg(isChinese ? '新密码至少 6 个字符' : 'Min 6 characters', 'error'); return; }
        if (newPassword !== confirmPassword) { showMsg(isChinese ? '两次密码不一致' : 'Passwords do not match', 'error'); return; }
        setSaving(true);
        try {
            const token = localStorage.getItem('token');
            const res = await fetch('/api/auth/me/password', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
            });
            if (!res.ok) { const err = await res.json().catch(() => ({ detail: 'Failed' })); throw new Error(err.detail); }
            showMsg(isChinese ? '密码已修改' : 'Password changed');
            setOldPassword(''); setNewPassword(''); setConfirmPassword('');
        } catch (e: any) { showMsg(e.message || 'Failed', 'error'); }
        setSaving(false);
    };

    const inputStyle = { width: '100%', fontSize: '13px' };
    const labelStyle = { display: 'block' as const, fontSize: '12px', fontWeight: 500, marginBottom: '4px', color: 'var(--text-secondary)' };

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 10000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
            <div style={{ background: 'var(--bg-primary)', borderRadius: '12px', border: '1px solid var(--border-subtle)', width: '420px', maxHeight: '90vh', overflow: 'auto', padding: '24px', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
                    <h3 style={{ margin: 0 }}>{isChinese ? '账户设置' : 'Account Settings'}</h3>
                    <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', fontSize: '18px', cursor: 'pointer', padding: '4px 8px' }}>×</button>
                </div>
                {msg && <div style={{ padding: '8px 12px', borderRadius: '6px', fontSize: '12px', marginBottom: '16px', background: msgType === 'success' ? 'rgba(0,180,120,0.12)' : 'rgba(255,80,80,0.12)', color: msgType === 'success' ? 'var(--success)' : 'var(--error)' }}>{msg}</div>}
                {/* Profile */}
                <h4 style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-secondary)' }}>{isChinese ? '个人信息' : 'Profile'}</h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '20px' }}>
                    <div><label style={labelStyle}>{isChinese ? '用户名' : 'Username'}</label><input className="form-input" value={username} onChange={e => setUsername(e.target.value)} style={inputStyle} /></div>
                    <div>
                        <label style={labelStyle}>{isChinese ? '邮箱' : 'Email'}</label>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <input className="form-input" type="email" value={email} onChange={e => setEmail(e.target.value)} style={inputStyle} disabled />
                            {user?.email_verified ? (
                                <span style={{ color: '#16a34a', fontSize: '12px', whiteSpace: 'nowrap' }}>✓ {isChinese ? '已验证' : 'Verified'}</span>
                            ) : (
                                <button
                                    onClick={handleResendVerification}
                                    disabled={resendingEmail}
                                    style={{
                                        fontSize: '11px',
                                        padding: '4px 8px',
                                        borderRadius: '4px',
                                        border: '1px solid var(--border-subtle)',
                                        background: 'var(--bg-secondary)',
                                        color: 'var(--text-secondary)',
                                        cursor: resendingEmail ? 'not-allowed' : 'pointer',
                                        whiteSpace: 'nowrap',
                                    }}
                                >
                                    {resendingEmail ? '...' : (isChinese ? '发送验证' : 'Verify')}
                                </button>
                            )}
                        </div>
                        {!user?.email_verified && (
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                {isChinese ? '邮箱未验证，请点击按钮发送验证邮件' : 'Email not verified. Click button to send verification email.'}
                            </div>
                        )}
                    </div>
                    <div><label style={labelStyle}>{isChinese ? '显示名称' : 'Display Name'}</label><input className="form-input" value={displayName} onChange={e => setDisplayName(e.target.value)} style={inputStyle} /></div>
                    <div style={{ display: 'flex', justifyContent: 'flex-end' }}><button className="btn btn-primary" onClick={handleSaveProfile} disabled={saving} style={{ padding: '6px 16px', fontSize: '12px' }}>{saving ? '...' : (isChinese ? '保存' : 'Save')}</button></div>
                </div>
                <div style={{ borderTop: '1px solid var(--border-subtle)', marginBottom: '20px' }} />
                {/* Password */}
                <h4 style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-secondary)' }}>{isChinese ? '修改密码' : 'Change Password'}</h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                    <div><label style={labelStyle}>{isChinese ? '当前密码' : 'Current Password'}</label><input className="form-input" type="password" value={oldPassword} onChange={e => setOldPassword(e.target.value)} style={inputStyle} /></div>
                    <div><label style={labelStyle}>{isChinese ? '新密码' : 'New Password'}</label><input className="form-input" type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} placeholder={isChinese ? '至少 6 个字符' : 'Min 6 characters'} style={inputStyle} /></div>
                    <div><label style={labelStyle}>{isChinese ? '确认新密码' : 'Confirm New Password'}</label><input className="form-input" type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} style={inputStyle} /></div>
                    <div style={{ display: 'flex', justifyContent: 'flex-end' }}><button className="btn btn-primary" onClick={handleChangePassword} disabled={saving} style={{ padding: '6px 16px', fontSize: '12px' }}>{saving ? '...' : (isChinese ? '修改密码' : 'Change Password')}</button></div>
                </div>
            </div>
        </div>
    );
}

/* ────── Version Display (runtime) ────── */
function VersionDisplay() {
    const [info, setInfo] = useState<{ version?: string; commit?: string }>({});
    useEffect(() => {
        fetch('/api/version').then(r => r.json()).then(setInfo).catch(() => {});
    }, []);
    if (!info.version) return null;
    return (
        <div style={{ textAlign: 'center', fontSize: '10px', color: 'var(--text-quaternary)', marginTop: '8px', letterSpacing: '0.3px' }}>
            v{info.version}
            {info.commit && <span style={{ opacity: 0.6 }}> ({info.commit})</span>}
        </div>
    );
}

type CompanyTourStep = {
    selector: string;
    title: string;
    body: string;
    pad?: number;
    radius?: number;
};

type TourRect = {
    left: number;
    top: number;
    width: number;
    height: number;
    radius: number;
};

type TourCardPlacement = 'right' | 'left' | 'below' | 'above';

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), Math.max(min, max));

function CompanyTourOverlay({ assistantId, isChinese, onDone }: { assistantId: string; isChinese: boolean; onDone: () => void }) {
    const [step, setStep] = useState(0);
    const [spotRect, setSpotRect] = useState<TourRect | null>(null);
    const [cardPos, setCardPos] = useState<{ left: number; top: number; placement: TourCardPlacement }>({ left: 24, top: 24, placement: 'right' });
    const steps: CompanyTourStep[] = useMemo(() => [
        {
            selector: '[data-tour-target="company-switcher"]',
            title: isChinese ? '公司切换' : 'Company switcher',
            body: isChinese ? '左上角是当前公司。你加入或创建更多公司后，可以在这里切换。' : 'The top-left area is your current company. Switch here when you join or create more companies.',
            pad: 8,
            radius: 14,
        },
        {
            selector: '[data-tour-target="main-nav"]',
            title: isChinese ? '三个主要功能' : 'Three main rooms',
            body: isChinese ? 'Plaza 是公司广场，Dashboard 看公司概况，OKR 用来设定和追踪目标。' : 'Plaza is the company square, Dashboard shows company activity, and OKR tracks goals.',
            pad: 8,
            radius: 14,
        },
        {
            selector: '[data-tour-target="agent-list"]',
            title: isChinese ? '员工列表' : 'Employee list',
            body: isChinese ? '这里是公司员工和你可见的 agent。你的私人助理也会在这里。' : 'This is the employee and agent list you can access. Your private assistant appears here too.',
            pad: 8,
            radius: 14,
        },
        {
            selector: '[data-tour-target="hire-agent"]',
            title: isChinese ? '新增员工' : 'Hire new employees',
            body: isChinese ? '点击加号可以从 Talent Market 增加新的 agent 员工。' : 'Use the plus button to add new agent employees from the Talent Market.',
            pad: 7,
            radius: 10,
        },
    ], [isChinese]);
    const current = steps[step];
    const progress = `${step + 1} / ${steps.length}`;
    const updateTourPosition = useCallback(() => {
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;
        const viewportPad = 12;
        const element = document.querySelector<HTMLElement>(current.selector);
        const measuredRect = element?.getBoundingClientRect();
        const fallbackWidth = Math.min(280, viewportWidth - viewportPad * 2);
        const fallback = {
            left: viewportPad,
            top: Math.max(72, viewportHeight * 0.18),
            width: fallbackWidth,
            height: 52,
        };
        const sourceRect = measuredRect && measuredRect.width > 0 && measuredRect.height > 0 ? measuredRect : fallback;
        const pad = current.pad ?? 8;
        const nextSpot: TourRect = {
            left: clamp(sourceRect.left - pad, viewportPad / 2, viewportWidth - viewportPad),
            top: clamp(sourceRect.top - pad, viewportPad / 2, viewportHeight - viewportPad),
            width: Math.min(sourceRect.width + pad * 2, viewportWidth - viewportPad),
            height: Math.min(sourceRect.height + pad * 2, viewportHeight - viewportPad),
            radius: current.radius ?? 14,
        };
        if (nextSpot.left + nextSpot.width > viewportWidth - viewportPad / 2) {
            nextSpot.left = Math.max(viewportPad / 2, viewportWidth - viewportPad / 2 - nextSpot.width);
        }
        if (nextSpot.top + nextSpot.height > viewportHeight - viewportPad / 2) {
            nextSpot.top = Math.max(viewportPad / 2, viewportHeight - viewportPad / 2 - nextSpot.height);
        }

        const cardWidth = Math.min(320, viewportWidth - viewportPad * 2);
        const cardHeight = 220;
        const gap = 20;
        let placement: TourCardPlacement = 'right';
        let cardLeft = nextSpot.left + nextSpot.width + gap;
        let cardTop = nextSpot.top + Math.min(24, Math.max(0, nextSpot.height / 2 - 18));

        if (cardLeft + cardWidth > viewportWidth - viewportPad) {
            placement = 'left';
            cardLeft = nextSpot.left - cardWidth - gap;
        }
        if (cardLeft < viewportPad) {
            placement = 'below';
            cardLeft = clamp(nextSpot.left, viewportPad, viewportWidth - cardWidth - viewportPad);
            cardTop = nextSpot.top + nextSpot.height + gap;
        }
        if (cardTop + cardHeight > viewportHeight - 92) {
            if (nextSpot.top - cardHeight - gap > viewportPad) {
                placement = 'above';
                cardTop = nextSpot.top - cardHeight - gap;
                cardLeft = clamp(nextSpot.left, viewportPad, viewportWidth - cardWidth - viewportPad);
            } else {
                cardTop = clamp(cardTop, viewportPad, viewportHeight - cardHeight - 92);
            }
        }

        setSpotRect(nextSpot);
        setCardPos({
            left: Math.round(clamp(cardLeft, viewportPad, viewportWidth - cardWidth - viewportPad)),
            top: Math.round(clamp(cardTop, viewportPad, viewportHeight - cardHeight - viewportPad)),
            placement,
        });
    }, [current]);

    useLayoutEffect(() => {
        updateTourPosition();
        const raf = window.requestAnimationFrame(updateTourPosition);
        window.addEventListener('resize', updateTourPosition);
        window.addEventListener('scroll', updateTourPosition, true);
        return () => {
            window.cancelAnimationFrame(raf);
            window.removeEventListener('resize', updateTourPosition);
            window.removeEventListener('scroll', updateTourPosition, true);
        };
    }, [updateTourPosition]);

    const next = () => {
        if (step >= steps.length - 1) onDone();
        else setStep(s => s + 1);
    };
    const previous = () => setStep(s => Math.max(0, s - 1));
    return (
        <div className="company-tour-overlay">
            {spotRect && (
                <div
                    className="tour-spot"
                    style={{
                        left: spotRect.left,
                        top: spotRect.top,
                        width: spotRect.width,
                        height: spotRect.height,
                        borderRadius: spotRect.radius,
                    }}
                />
            )}
            <div
                className={`company-tour-card company-tour-card--${cardPos.placement}`}
                style={{ left: cardPos.left, top: cardPos.top }}
            >
                <div className="company-tour-step">{step + 1}</div>
                <h3>{current.title}</h3>
                <p>{current.body}</p>
            </div>
            <div className="company-tour-controls">
                <button type="button" onClick={onDone}>{isChinese ? '跳过导览' : 'Skip tour'}</button>
                <button type="button" onClick={previous} disabled={step === 0}>{isChinese ? '上一步' : 'Back'}</button>
                <div className="company-tour-dots">
                    {steps.map((_, i) => <span key={i} className={i === step ? 'active' : ''} />)}
                </div>
                <span>{progress}</span>
                <button type="button" className="company-tour-next" onClick={next}>
                    {step >= steps.length - 1 ? (isChinese ? '完成' : 'Finish') : (isChinese ? '下一个' : 'Next')} →
                </button>
            </div>
            {assistantId && <div className="company-tour-hint">{isChinese ? '导览结束后，会进入你的私人助理对话页。' : 'After the tour, you will land in your private assistant chat.'}</div>}
        </div>
    );
}

export default function Layout() {
    const { t, i18n } = useTranslation();
    const toast = useToast();
    const navigate = useNavigate();
    const location = useLocation();
    const { user, logout, setAuth } = useAuthStore();
    const queryClient = useQueryClient();
    const isChinese = i18n.language?.startsWith('zh');
    // Detect chat page: needs fixed-height main-content for inner scroll to work
    const isChatPage = !!useMatch('/agents/:id/chat');
    const isAgentSettingsPage = !!useMatch('/agents/:id/settings');
    const activeAgentNestedMatch = useMatch('/agents/:id/*');
    const activeAgentRootMatch = useMatch('/agents/:id');
    const activeAgentId = activeAgentNestedMatch?.params.id || activeAgentRootMatch?.params.id;
    const canAccessPlatformSettings = user?.role === 'platform_admin' || !!(user as any)?.is_platform_admin;
    const canAccessCompanySettings = user?.role === 'platform_admin' || user?.role === 'org_admin' || !!(user as any)?.is_platform_admin;
    const routeParams = new URLSearchParams(location.search);
    const showCompanyTour = routeParams.get('tour') === 'company';
    const tourAssistantId = routeParams.get('assistantId') || '';

    const [showAccountSettings, setShowAccountSettings] = useState(false);
    const [showAccountMenu, setShowAccountMenu] = useState(false);
    const [showLanguageSubmenu, setShowLanguageSubmenu] = useState(false);
    const [langSubmenuPos, setLangSubmenuPos] = useState({ top: 0, left: 0 });
    const accountMenuRef = useRef<HTMLDivElement>(null);
    const accountDropdownRef = useRef<HTMLDivElement>(null);
    const langSubmenuPortalRef = useRef<HTMLDivElement>(null);
    const langHoverCloseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [showNotifications, setShowNotifications] = useState(false);
    const [showTalentMarket, setShowTalentMarket] = useState(false);
    const [notifCategory, setNotifCategory] = useState<string>('all');
    const [selectedNotification, setSelectedNotification] = useState<any | null>(null);
    const [showTenantMenu, setShowTenantMenu] = useState(false);
    const [showTenantSetupModal, setShowTenantSetupModal] = useState(false);
    const [tenantSearch, setTenantSearch] = useState('');
    const [joinInviteCode, setJoinInviteCode] = useState('');
    const [createCompanyName, setCreateCompanyName] = useState('');
    const [tenantFormLoading, setTenantFormLoading] = useState(false);
    const [tenantFormError, setTenantFormError] = useState('');
    const [allowSelfCreate, setAllowSelfCreate] = useState(true);
    const tenantSwitcherRef = useRef<HTMLDivElement>(null);
    const tenantMenuPortalRef = useRef<HTMLDivElement>(null);
    const [tenantMenuPos, setTenantMenuPos] = useState({ top: 0, left: 0, maxHeight: 520 });

    // Notification polling
    const { data: unreadCount = 0 } = useQuery({
        queryKey: ['notifications-unread'],
        queryFn: async () => {
            const res = await fetchJson<{ unread_count: number }>('/notifications/unread-count');
            return (res as any)?.unread_count || 0;
        },
        refetchInterval: 30000,
        enabled: !!user,
    });
    const { data: notifications = [] } = useQuery({
        queryKey: ['notifications', notifCategory],
        queryFn: () => fetchJson<any[]>(`/notifications?limit=50${notifCategory !== 'all' ? `&category=${notifCategory}` : ''}`),
        enabled: !!user && showNotifications,
    });
    const markAllRead = async () => {
        const token = localStorage.getItem('token');
        await fetch('/api/notifications/read-all', { method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {} });
        queryClient.invalidateQueries({ queryKey: ['notifications-unread'] });
        queryClient.invalidateQueries({ queryKey: ['notifications'] });
    };
    const markOneRead = async (id: string) => {
        const token = localStorage.getItem('token');
        await fetch(`/api/notifications/${id}/read`, { method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {} });
        queryClient.invalidateQueries({ queryKey: ['notifications-unread'] });
        queryClient.invalidateQueries({ queryKey: ['notifications'] });
    };

    // Tenant switching
    const { data: myTenants = [] } = useQuery({
        queryKey: ['my-tenants'],
        queryFn: async () => {
            const token = localStorage.getItem('token');
            const res = await fetch('/api/auth/my-tenants', { headers: token ? { Authorization: `Bearer ${token}` } : {} });
            if (!res.ok) return [];
            return res.json();
        },
        enabled: !!user,
    });

    const handleSwitchTenant = async (tenantId: string) => {
        const token = localStorage.getItem('token');
        const res = await fetch('/api/auth/switch-tenant', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
            body: JSON.stringify({ tenant_id: tenantId }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: 'Failed to switch tenant' }));
            toast.error('切换公司失败', { details: String(err.detail || `HTTP ${res.status}`) });
            return;
        }
        const data = await res.json();
        if (data.redirect_url) {
            localStorage.setItem('token', data.access_token);
            const targetUrl = new URL(data.redirect_url, window.location.origin);
            if (targetUrl.hostname === window.location.hostname) {
                targetUrl.protocol = window.location.protocol;
                targetUrl.port = window.location.port;
            }
            targetUrl.pathname = '/';
            targetUrl.hash = '';
            window.location.href = targetUrl.toString();
        } else if (data.access_token) {
            localStorage.setItem('token', data.access_token);
            window.location.href = '/';
        }
    };

    // Open the tenant switcher modal — also fetch self-create config
    const openTenantModal = () => {
        setShowTenantMenu(true);
        setTenantSearch('');
    };

    const openTenantSetupModal = () => {
        setShowTenantMenu(false);
        setShowTenantSetupModal(true);
        setJoinInviteCode('');
        setCreateCompanyName('');
        setTenantFormError('');
        tenantApi.registrationConfig().then((d: any) => {
            setAllowSelfCreate(d.allow_self_create_company);
        }).catch(() => {});
    };

    // Join company via invite code (inside modal)
    const handleModalJoin = async (e: React.FormEvent) => {
        e.preventDefault();
        setTenantFormError('');
        setTenantFormLoading(true);
        try {
            const result = await tenantApi.join(joinInviteCode);
            if (result.access_token) {
                // Multi-tenant: backend created a new User record, switch context
                localStorage.setItem('token', result.access_token);
            } else {
                // Registration flow: same user updated, refresh store
                const me = await authApi.me();
                const token = localStorage.getItem('token');
                if (token) setAuth(me, token);
            }
            setShowTenantMenu(false);
            setShowTenantSetupModal(false);
            window.location.href = '/onboarding?mode=join';
        } catch (err: any) {
            setTenantFormError(err.message || 'Failed to join company');
        } finally {
            setTenantFormLoading(false);
        }
    };

    // Create company (inside modal)
    const handleModalCreate = async (e: React.FormEvent) => {
        e.preventDefault();
        setTenantFormError('');
        setTenantFormLoading(true);
        try {
            const result = await tenantApi.selfCreate({ name: createCompanyName });
            if (result.access_token) {
                // Multi-tenant: backend created a new User record, switch context
                localStorage.setItem('token', result.access_token);
            } else {
                // Registration flow: same user updated, refresh store
                const me = await authApi.me();
                const token = localStorage.getItem('token');
                if (token) setAuth(me, token);
            }
            setShowTenantMenu(false);
            setShowTenantSetupModal(false);
            window.location.href = '/onboarding?mode=create';
        } catch (err: any) {
            setTenantFormError(err.message || 'Failed to create company');
        } finally {
            setTenantFormLoading(false);
        }
    };

    // Theme
    const [theme, setTheme] = useState<'dark' | 'light'>(() => {
        return (localStorage.getItem('theme') as 'dark' | 'light') || 'light';
    });

    useEffect(() => {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);
    }, [theme]);

    const toggleTheme = () => setTheme(prev => prev === 'dark' ? 'light' : 'dark');

    // Sidebar collapse state
    const isSidebarCollapsed = useAppStore(s => s.sidebarCollapsed);
    const toggleSidebar = useAppStore(s => s.toggleSidebar);

    // Sidebar agent search & pin
    const [sidebarSearch, setSidebarSearch] = useState('');
    const [agentDrawerOpen, setAgentDrawerOpen] = useState(false);
    const agentDrawerCloseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [pinnedAgents, setPinnedAgents] = useState<Set<string>>(() => {
        try {
            const stored = localStorage.getItem('pinned_agents');
            return stored ? new Set(JSON.parse(stored)) : new Set();
        } catch { return new Set(); }
    });
    const togglePin = (agentId: string) => {
        setPinnedAgents(prev => {
            const next = new Set(prev);
            if (next.has(agentId)) next.delete(agentId);
            else next.add(agentId);
            localStorage.setItem('pinned_agents', JSON.stringify([...next]));
            return next;
        });
    };

    // Use user's own tenant_id directly (no switching)
    const currentTenant = user?.tenant_id || '';
    const currentTenantName = useMemo(() => {
        const tenant = (myTenants as any[]).find((item: any) => item.tenant_id === currentTenant);
        return tenant?.tenant_name || (isChinese ? '当前公司' : 'Current Company');
    }, [currentTenant, isChinese, myTenants]);
    const currentTenantLogoUrl = useMemo(() => {
        const tenant = (myTenants as any[]).find((item: any) => item.tenant_id === currentTenant);
        return tenant?.logo_url || '';
    }, [currentTenant, myTenants]);
    const currentTenantInitial = (Array.from(currentTenantName.trim())[0] as string | undefined)?.toUpperCase() || 'C';
    const currentTenantAvatarTone = useMemo(() => getWorkspaceAvatarTone(currentTenantName), [currentTenantName]);
    const filteredTenants = useMemo(() => {
        const query = tenantSearch.trim().toLowerCase();
        if (!query) return myTenants as any[];
        return (myTenants as any[]).filter((tenant: any) => (tenant.tenant_name || '').toLowerCase().includes(query));
    }, [myTenants, tenantSearch]);

    // Keep tenant in localStorage for other components that read it
    useEffect(() => {
        if (currentTenant) {
            localStorage.setItem('current_tenant_id', currentTenant);
        }
    }, [currentTenant]);

    const { data: agents = [] } = useQuery({
        queryKey: ['agents', currentTenant],
        queryFn: () => agentApi.list(currentTenant || undefined),
        refetchInterval: 30000,
    });

    const openAgentDrawer = useCallback(() => {
        if (!isSidebarCollapsed) return;
        if (agentDrawerCloseTimerRef.current) {
            clearTimeout(agentDrawerCloseTimerRef.current);
            agentDrawerCloseTimerRef.current = null;
        }
        setAgentDrawerOpen(true);
    }, [isSidebarCollapsed]);

    const scheduleCloseAgentDrawer = useCallback(() => {
        if (agentDrawerCloseTimerRef.current) clearTimeout(agentDrawerCloseTimerRef.current);
        agentDrawerCloseTimerRef.current = setTimeout(() => {
            setAgentDrawerOpen(false);
            agentDrawerCloseTimerRef.current = null;
        }, 160);
    }, []);

    const handleLogout = () => {
        logout();
        navigate('/login');
    };

    const selectUiLanguage = (code: string) => {
        i18n.changeLanguage(code);
        setShowLanguageSubmenu(false);
        setShowAccountMenu(false);
    };

    const openLangSubmenu = useCallback(() => {
        if (langHoverCloseTimerRef.current) {
            clearTimeout(langHoverCloseTimerRef.current);
            langHoverCloseTimerRef.current = null;
        }
        setShowLanguageSubmenu(true);
    }, []);

    const scheduleCloseLangSubmenu = useCallback(() => {
        if (langHoverCloseTimerRef.current) clearTimeout(langHoverCloseTimerRef.current);
        langHoverCloseTimerRef.current = setTimeout(() => {
            setShowLanguageSubmenu(false);
            langHoverCloseTimerRef.current = null;
        }, 200);
    }, []);

    useEffect(() => {
        if (!showAccountMenu) {
            if (langHoverCloseTimerRef.current) {
                clearTimeout(langHoverCloseTimerRef.current);
                langHoverCloseTimerRef.current = null;
            }
            setShowLanguageSubmenu(false);
        }
    }, [showAccountMenu]);

    useEffect(() => () => {
        if (langHoverCloseTimerRef.current) clearTimeout(langHoverCloseTimerRef.current);
        if (agentDrawerCloseTimerRef.current) clearTimeout(agentDrawerCloseTimerRef.current);
    }, []);

    useEffect(() => {
        if (!isSidebarCollapsed) setAgentDrawerOpen(false);
    }, [isSidebarCollapsed]);

    const updateLangSubmenuPosition = useCallback(() => {
        const el = accountDropdownRef.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        setLangSubmenuPos({ top: r.top, left: r.right + 2 });
    }, []);

    const updateTenantMenuPosition = useCallback(() => {
        const el = tenantSwitcherRef.current;
        if (!el) return;
        const rect = el.getBoundingClientRect();
        const viewportPadding = 12;
        const menuWidth = 304;
        const preferredLeft = isSidebarCollapsed ? rect.right + 8 : rect.left;
        const left = Math.min(
            Math.max(viewportPadding, preferredLeft),
            Math.max(viewportPadding, window.innerWidth - menuWidth - viewportPadding),
        );
        const top = Math.max(viewportPadding, rect.bottom + 8);
        const maxHeight = Math.max(220, window.innerHeight - top - viewportPadding);
        setTenantMenuPos({ top, left, maxHeight });
    }, [isSidebarCollapsed]);

    useLayoutEffect(() => {
        if (!showLanguageSubmenu) return;
        updateLangSubmenuPosition();
        window.addEventListener('resize', updateLangSubmenuPosition);
        window.addEventListener('scroll', updateLangSubmenuPosition, true);
        return () => {
            window.removeEventListener('resize', updateLangSubmenuPosition);
            window.removeEventListener('scroll', updateLangSubmenuPosition, true);
        };
    }, [showLanguageSubmenu, updateLangSubmenuPosition]);

    useLayoutEffect(() => {
        if (!showTenantMenu) return;
        updateTenantMenuPosition();
        window.addEventListener('resize', updateTenantMenuPosition);
        window.addEventListener('scroll', updateTenantMenuPosition, true);
        return () => {
            window.removeEventListener('resize', updateTenantMenuPosition);
            window.removeEventListener('scroll', updateTenantMenuPosition, true);
        };
    }, [showTenantMenu, updateTenantMenuPosition]);

    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            const t = e.target as Node;
            if (accountMenuRef.current?.contains(t)) return;
            if (langSubmenuPortalRef.current?.contains(t)) return;
            if (tenantSwitcherRef.current?.contains(t)) return;
            if (tenantMenuPortalRef.current?.contains(t)) return;
            setShowAccountMenu(false);
            setShowTenantMenu(false);
        };
        if (showAccountMenu || showTenantMenu) document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [showAccountMenu, showTenantMenu]);

    const langSubmenuContent = showAccountMenu && showLanguageSubmenu && (
        <div
            ref={langSubmenuPortalRef}
            className="account-lang-submenu account-lang-submenu-portal"
            role="menu"
            style={{ top: langSubmenuPos.top, left: langSubmenuPos.left }}
            onMouseEnter={openLangSubmenu}
            onMouseLeave={scheduleCloseLangSubmenu}
        >
            {APP_UI_LANGUAGES.map(({ code, nativeLabel }) => {
                const active = resolveUiLangCode(i18n.language) === code;
                return (
                    <button
                        key={code}
                        type="button"
                        role="menuitem"
                        className={`account-lang-submenu-item${active ? ' is-active' : ''}`}
                        onClick={() => selectUiLanguage(code)}
                    >
                        <span>{nativeLabel}</span>
                        {active && <IconCheck size={14} stroke={2} />}
                    </button>
                );
            })}
        </div>
    );

    const tenantMenuContent = showTenantMenu && typeof document !== 'undefined' && createPortal(
        <div
            ref={tenantMenuPortalRef}
            className="tenant-switcher-popover"
            role="menu"
            style={{ top: tenantMenuPos.top, left: tenantMenuPos.left, maxHeight: tenantMenuPos.maxHeight }}
        >
            <div className="tenant-switcher-label">{isChinese ? '切换公司' : 'Switch company'}</div>
            {(myTenants as any[]).length > 8 && (
                <div className="tenant-switcher-search">
                    <IconSearch size={14} stroke={1.7} />
                    <input
                        value={tenantSearch}
                        onChange={e => setTenantSearch(e.target.value)}
                        placeholder={isChinese ? '搜索公司' : 'Search companies'}
                    />
                    {tenantSearch && (
                        <button type="button" onClick={() => setTenantSearch('')} aria-label={isChinese ? '清空搜索' : 'Clear search'}>
                            <IconX size={14} stroke={1.7} />
                        </button>
                    )}
                </div>
            )}
            <div className="tenant-switcher-list">
                {filteredTenants.map((tenant: any) => (
                    <button
                        key={tenant.tenant_id}
                        type="button"
                        className={`tenant-switcher-item${tenant.tenant_id === currentTenant ? ' active' : ''}`}
                        onClick={() => {
                            if (tenant.tenant_id === currentTenant) {
                                setShowTenantMenu(false);
                                return;
                            }
                            handleSwitchTenant(tenant.tenant_id);
                        }}
                    >
                        <span className="tenant-switcher-icon">
                            <IconBuilding size={16} stroke={1.6} />
                        </span>
                        <span className="tenant-switcher-name">{tenant.tenant_name}</span>
                        {tenant.tenant_id === currentTenant && <IconCheck size={16} stroke={2} />}
                    </button>
                ))}
                {filteredTenants.length === 0 && (
                    <div className="tenant-switcher-empty">{isChinese ? '没有匹配的公司' : 'No matching companies'}</div>
                )}
            </div>

            <div className="tenant-switcher-divider" />

            <button
                type="button"
                className="tenant-switcher-action"
                onClick={openTenantSetupModal}
            >
                <IconPlus size={17} stroke={1.6} />
                <span>{isChinese ? '创建或加入新公司' : 'Create or join company'}</span>
            </button>
            {canAccessCompanySettings && (
                <button
                    type="button"
                    className="tenant-switcher-action"
                    onClick={() => {
                        setShowTenantMenu(false);
                        navigate('/enterprise');
                    }}
                >
                    <IconSettings size={16} stroke={1.6} />
                    <span>{isChinese ? '公司信息设置' : 'Company settings'}</span>
                </button>
            )}
        </div>,
        document.body,
    );

    const q = sidebarSearch.trim().toLowerCase();
    const sortedAgents = [...agents].filter((a: any) => {
        if (!q) return true;
        return (a.name || '').toLowerCase().includes(q) || (a.role_description || '').toLowerCase().includes(q);
    }).sort((a: any, b: any) => {
        const ap = pinnedAgents.has(a.id) ? 1 : 0;
        const bp = pinnedAgents.has(b.id) ? 1 : 0;
        if (ap !== bp) return bp - ap;
        const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
        const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
        return bTime - aTime;
    });

    const agentSearchBox = (force = false) => (force || agents.length >= 5) && (
        <div className="sidebar-agent-search">
            <IconSearch size={14} stroke={2} className="sidebar-agent-search-icon" />
            <input
                type="text"
                value={sidebarSearch}
                onChange={e => setSidebarSearch(e.target.value)}
                placeholder={isChinese ? '搜索...' : 'Search...'}
            />
            {sidebarSearch && (
                <button onClick={() => setSidebarSearch('')} aria-label={isChinese ? '清空搜索' : 'Clear search'}>
                    <IconX size={14} stroke={2} />
                </button>
            )}
        </div>
    );

    const renderAgent = (agent: any, options?: { drawer?: boolean }) => {
        const badge = getAgentBadgeStatus(agent);
        const avatarChar = ((Array.from(agent.name || '?')[0] as string) || '?').toUpperCase();
        const unreadCount = Number(agent.unread_count || 0);
        const showPin = !isSidebarCollapsed || options?.drawer;
        return (
            <div key={agent.id} className={`sidebar-agent-item${agent.creator_id === user?.id ? ' owned' : ''}${options?.drawer ? ' drawer-agent' : ''}`}>
                <NavLink
                    to={`/agents/${agent.id}/chat`}
                    className={({ isActive }) => `sidebar-item ${isActive || activeAgentId === agent.id ? 'active' : ''}`}
                    title={agent.name}
                    onClick={() => setAgentDrawerOpen(false)}
                >
                    <span className="sidebar-item-icon" style={{ position: 'relative' }}>
                        <span className={`agent-avatar${agent.agent_type === 'openclaw' ? ' openclaw' : ''}`}>{avatarChar}</span>
                        {agent.agent_type === 'openclaw' && (
                            <span className="agent-avatar-link" style={{ display: 'flex' }}>
                                <IconArrowUpRight size={10} stroke={2.5} />
                            </span>
                        )}
                        {badge && <span className={`agent-avatar-badge ${badge}`} />}
                        {unreadCount > 0 && (
                            <span className="sidebar-agent-unread">
                                {unreadCount > 99 ? '99+' : unreadCount}
                            </span>
                        )}
                    </span>
                    <span className="sidebar-item-text">{agent.name}</span>
                </NavLink>
                {showPin && (
                    <button
                        onClick={e => { e.preventDefault(); e.stopPropagation(); togglePin(agent.id); }}
                        className={`sidebar-pin-btn ${pinnedAgents.has(agent.id) ? 'pinned' : ''}`}
                        title={pinnedAgents.has(agent.id) ? (isChinese ? '取消置顶' : 'Unpin') : (isChinese ? '置顶' : 'Pin to top')}
                    >
                        {pinnedAgents.has(agent.id) ? (
                            <>
                                <IconPin size={14} stroke={1.5} className="pin-default" />
                                <IconPinnedOff size={14} stroke={1.5} className="pin-hover" />
                            </>
                        ) : (
                            <IconPin size={14} stroke={1.5} className="pin-on" />
                        )}
                    </button>
                )}
            </div>
        );
    };

    const agentListContent = (drawer = false) => (
        <>
            {sortedAgents.map(agent => renderAgent(agent, { drawer }))}
            {agents.length === 0 && (
                <div className="sidebar-section">
                    <div className="sidebar-section-title">{t('nav.myAgents')}</div>
                </div>
            )}
            {agents.length > 0 && sortedAgents.length === 0 && q && (
                <div className="sidebar-agent-empty">
                    {isChinese ? '无匹配结果' : 'No matches'}
                </div>
            )}
        </>
    );

    const agentDrawer = isSidebarCollapsed && agentDrawerOpen && typeof document !== 'undefined' && createPortal(
        <div
            className="sidebar-agent-drawer"
            onMouseEnter={openAgentDrawer}
            onMouseLeave={scheduleCloseAgentDrawer}
        >
            <div className="sidebar-agent-drawer-header">
                <span>{isChinese ? '智能体' : 'Agents'}</span>
                <button
                    type="button"
                    onClick={() => {
                        setShowTalentMarket(true);
                        setAgentDrawerOpen(false);
                    }}
                    title={t('nav.hire', t('nav.newAgent'))}
                >
                    <IconPlus size={16} stroke={1.7} />
                </button>
            </div>
            {agentSearchBox(true)}
            <div className="sidebar-agent-drawer-list">
                {agentListContent(true)}
            </div>
        </div>,
        document.body,
    );

    return (
        <div className={`app-layout ${isSidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
            <nav className={`sidebar ${isSidebarCollapsed ? 'collapsed' : ''}`}>
                <div className="sidebar-top">
                    <div className="sidebar-workspace-row" ref={tenantSwitcherRef} data-tour-target="company-switcher">
                        <button
                            type="button"
                            className={`workspace-switcher-trigger${showTenantMenu ? ' open' : ''}`}
                            onClick={() => {
                                if (showTenantMenu) {
                                    setShowTenantMenu(false);
                                    return;
                                }
                                openTenantModal();
                            }}
                            title={isChinese ? '切换企业' : 'Switch Organization'}
                        >
                            <span className={`workspace-switcher-avatar tone-${currentTenantAvatarTone}`}>
                                {currentTenantLogoUrl ? <img src={currentTenantLogoUrl} alt="" /> : currentTenantInitial}
                            </span>
                            <span className="workspace-switcher-name">{currentTenantName}</span>
                            <IconChevronDown className="workspace-switcher-chevron" size={15} stroke={1.7} />
                        </button>
                        <button className="btn btn-ghost sidebar-collapse-btn" onClick={toggleSidebar} style={{
                            padding: '4px', display: 'flex', alignItems: 'center', justifyContent: 'center',
                            marginLeft: 'auto', color: 'var(--text-tertiary)',
                        }} title={isSidebarCollapsed ? t('common.expandSidebar') : t('common.collapseSidebar')}>
                            {isSidebarCollapsed ? SidebarIcons.expand : SidebarIcons.collapse}
                        </button>

                    </div>



                    <div className="sidebar-section" data-tour-target="main-nav">
                        <NavLink to="/plaza" className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}>
                            <span className="sidebar-item-icon" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
                                <IconBuildingMonument size={14} stroke={1.5} />
                            </span>
                            <span className="sidebar-item-text">{t('nav.plaza', 'Plaza')}</span>
                        </NavLink>
                        <NavLink to="/dashboard" className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}>
                            <span className="sidebar-item-icon" style={{ display: 'flex' }}>{SidebarIcons.home}</span>
                            <span className="sidebar-item-text">{t('nav.dashboard')}</span>
                        </NavLink>
                        <NavLink to="/okr" className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}>
                            <span className="sidebar-item-icon" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
                                {/* OKR target icon */}
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                                    <circle cx="12" cy="12" r="10"/>
                                    <circle cx="12" cy="12" r="6"/>
                                    <circle cx="12" cy="12" r="2"/>
                                </svg>
                            </span>
                            <span className="sidebar-item-text">{t('nav.okr', 'OKR')}</span>
                        </NavLink>
                    </div>
                </div>
                
                <div className="sidebar-divider" />

                <div
                    className="sidebar-scrollable"
                    data-tour-target="agent-list"
                    onMouseEnter={openAgentDrawer}
                    onMouseLeave={scheduleCloseAgentDrawer}
                >
                    {!isSidebarCollapsed && (
                        <div className="sidebar-agent-header">
                            <span>{isChinese ? '智能体' : 'Agents'}</span>
                            <button
                                type="button"
                                data-tour-target="hire-agent"
                                onClick={() => setShowTalentMarket(true)}
                                title={t('nav.hire', t('nav.newAgent'))}
                            >
                                <IconPlus size={15} stroke={1.7} />
                            </button>
                        </div>
                    )}
                    {!isSidebarCollapsed && agentSearchBox()}
                    {agentListContent()}
                </div>

                <div className="sidebar-bottom">
                    <div className="sidebar-footer">
                        <div className="sidebar-footer-controls" style={{
                            display: 'flex', alignItems: 'center', gap: '4px', marginBottom: '8px',
                        }}>
                            <button className="btn btn-ghost" onClick={toggleTheme} style={{
                                padding: '4px 8px', display: 'flex', alignItems: 'center', justifyContent: 'center',
                            }} title={theme === 'dark' ? t('common.lightMode') : t('common.darkMode')}>
                                {theme === 'dark' ? SidebarIcons.sun : SidebarIcons.moon}
                            </button>
                            <button className="btn btn-ghost" onClick={() => setShowNotifications(v => !v)} style={{
                                padding: '4px 8px', display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative',
                            }} title={isChinese ? '通知' : 'Notifications'}>
                                {SidebarIcons.bell}
                                {(unreadCount as number) > 0 && (
                                    <span style={{
                                        position: 'absolute', top: '-2px', right: '-4px',
                                        minWidth: '16px', height: '16px', borderRadius: '8px',
                                        padding: '0 4px', boxSizing: 'border-box',
                                        background: 'var(--error)', color: '#fff',
                                        fontSize: '10px', fontWeight: 600,
                                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                                        lineHeight: 1,
                                    }}>{(unreadCount as number) > 99 ? '99+' : unreadCount}</span>
                                )}
                            </button>
                        </div>
                        <div ref={accountMenuRef} style={{ position: 'relative' }}>
                            {showAccountMenu && (
                                <div className="account-menus-container">
                                <div className="account-dropdown" ref={accountDropdownRef}>
                                    <div
                                        className="account-dropdown-language-hover-wrap"
                                        onMouseEnter={openLangSubmenu}
                                        onMouseLeave={scheduleCloseLangSubmenu}
                                    >
                                        <button
                                            type="button"
                                            className="account-dropdown-item language-menu-trigger"
                                            aria-haspopup="menu"
                                            aria-expanded={showLanguageSubmenu}
                                        >
                                            <IconWorld size={15} stroke={1.5} />
                                            <span className="language-menu-label">
                                                {t('layout.language', {
                                                    lng: resolveUiLangCode(i18n.language),
                                                    defaultValue: 'Language',
                                                })}
                                            </span>
                                            <span className="language-menu-chevron" aria-hidden>
                                                <IconChevronRight size={16} stroke={1.75} />
                                            </span>
                                        </button>
                                    </div>
                                    <button className="account-dropdown-item" onClick={() => { setShowAccountSettings(true); setShowAccountMenu(false); }}>
                                        <IconUser size={15} stroke={1.5} />
                                        <span>{isChinese ? '账户设置' : 'Account Settings'}</span>
                                    </button>
                                    {canAccessPlatformSettings && (
                                        <button className="account-dropdown-item" onClick={() => { navigate('/admin/platform-settings'); setShowAccountMenu(false); }}>
                                            <IconSettings size={15} stroke={1.5} />
                                            <span>{t('nav.platformSettings', 'Platform Settings')}</span>
                                        </button>
                                    )}
                                    <div style={{ height: '1px', background: 'var(--border-subtle)', margin: '4px 0' }} />
                                    <button className="account-dropdown-item account-dropdown-danger" onClick={() => { handleLogout(); setShowAccountMenu(false); }}>
                                        <IconLogout size={15} stroke={1.5} />
                                        <span>{t('layout.logout', 'Logout')}</span>
                                    </button>
                                </div>
                                </div>
                            )}
                            {typeof document !== 'undefined' && langSubmenuContent && createPortal(langSubmenuContent, document.body)}
                            <div
                                className="sidebar-account-row"
                                onClick={() => setShowAccountMenu(v => !v)}
                            >
                                <div style={{
                                    width: '28px', height: '28px', borderRadius: 'var(--radius-md)',
                                    background: 'var(--bg-tertiary)', border: '1px solid var(--border-subtle)',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                    color: 'var(--text-tertiary)', flexShrink: 0,
                                }}>
                                    {SidebarIcons.user}
                                </div>
                                <div className="sidebar-footer-user-info" style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ fontSize: '13px', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {user?.display_name}
                                    </div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                        {user?.role === 'platform_admin' ? t('roles.platformAdmin') :
                                            user?.role === 'org_admin' ? t('roles.orgAdmin') :
                                                user?.role === 'agent_admin' ? t('roles.agentAdmin') : t('roles.member')}
                                    </div>
                                </div>
                                <IconChevronUp size={14} stroke={1.5} style={{
                                    color: 'var(--text-tertiary)', flexShrink: 0,
                                    transform: showAccountMenu ? 'rotate(0deg)' : 'rotate(180deg)',
                                    transition: 'transform 0.2s ease',
                                }} />
                            </div>
                        </div>
                        <VersionDisplay />
                    </div>
                </div>
            </nav>
            {agentDrawer}
            {tenantMenuContent}

            {showTenantSetupModal && (
                <div className="tenant-setup-modal-backdrop" onClick={() => setShowTenantSetupModal(false)}>
                    <div className="tenant-setup-modal" onClick={e => e.stopPropagation()}>
                        <div className="tenant-setup-modal-header">
                            <div>
                                <h3>{isChinese ? '创建或加入新公司' : 'Create or Join Company'}</h3>
                                <p>{isChinese ? '加入已有公司，或创建一个新的工作空间。' : 'Join an existing company or start a new workspace.'}</p>
                            </div>
                            <button type="button" onClick={() => setShowTenantSetupModal(false)} aria-label={isChinese ? '关闭' : 'Close'}>
                                <IconX size={18} stroke={1.8} />
                            </button>
                        </div>

                        {tenantFormError && <div className="tenant-setup-error">{tenantFormError}</div>}

                        <form onSubmit={handleModalJoin} className="tenant-setup-section">
                            <div className="tenant-setup-section-title">{isChinese ? '通过邀请码加入' : 'Join via invitation code'}</div>
                            <div className="tenant-setup-row">
                                <input
                                    className="form-input"
                                    value={joinInviteCode}
                                    onChange={e => setJoinInviteCode(e.target.value)}
                                    placeholder={isChinese ? '输入邀请码' : 'Enter invitation code'}
                                />
                                <button className="btn btn-primary" type="submit" disabled={tenantFormLoading || !joinInviteCode.trim()}>
                                    {tenantFormLoading ? '...' : (isChinese ? '加入' : 'Join')}
                                </button>
                            </div>
                        </form>

                        {allowSelfCreate && (
                            <>
                                <div className="tenant-setup-divider"><span>{isChinese ? '或者' : 'OR'}</span></div>
                                <form onSubmit={handleModalCreate} className="tenant-setup-section">
                                    <div className="tenant-setup-section-title">{isChinese ? '创建新公司' : 'Create a new company'}</div>
                                    <div className="tenant-setup-row">
                                        <input
                                            className="form-input"
                                            value={createCompanyName}
                                            onChange={e => setCreateCompanyName(e.target.value)}
                                            placeholder={isChinese ? '公司名称' : 'Company name'}
                                        />
                                        <button className="btn btn-primary" type="submit" disabled={tenantFormLoading || !createCompanyName.trim()}>
                                            {tenantFormLoading ? '...' : (isChinese ? '创建' : 'Create')}
                                        </button>
                                    </div>
                                </form>
                            </>
                        )}
                    </div>
                </div>
            )}

            {/* Notification Modal */}
            {showNotifications && (
                <>
                    <div style={{ position: 'fixed', inset: 0, zIndex: 9998, background: 'rgba(0,0,0,0.5)' }} onClick={() => setShowNotifications(false)} />
                    <div style={{
                        position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
                        width: 'calc(100vw - 80px)', maxWidth: '800px',
                        height: '80vh', maxHeight: '800px',
                        background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)',
                        borderRadius: '12px', boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
                        zIndex: 9999, display: 'flex', flexDirection: 'column', overflow: 'hidden',
                    }}>
                        <div style={{ borderBottom: '1px solid var(--border-subtle)', flexShrink: 0 }}>
                            <div style={{ padding: '16px 24px 0', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600, flex: 1 }}>{isChinese ? '通知' : 'Notifications'}</h3>
                                {(unreadCount as number) > 0 && (
                                    <button className="btn btn-ghost" onClick={markAllRead} style={{ fontSize: '12px', padding: '4px 10px' }}>
                                        {isChinese ? '全部已读' : 'Mark all read'}
                                    </button>
                                )}
                                <button className="btn btn-ghost" onClick={() => setShowNotifications(false)} style={{ padding: '4px 8px', fontSize: '18px', lineHeight: 1 }}>×</button>
                            </div>
                            <div style={{ display: 'flex', gap: '0', padding: '0 24px', marginTop: '12px' }}>
                                {[
                                    { key: 'all', zh: '全部', en: 'All' },
                                    { key: 'tool', zh: '工具执行', en: 'Tool' },
                                    { key: 'approval', zh: '审批', en: 'Approval' },
                                    { key: 'social', zh: '社交', en: 'Social' },
                                ].map(tab => (
                                    <button
                                        key={tab.key}
                                        onClick={() => { setNotifCategory(tab.key); }}
                                        style={{
                                            background: 'none', border: 'none', cursor: 'pointer',
                                            padding: '8px 14px', fontSize: '13px', fontWeight: 500,
                                            color: notifCategory === tab.key ? 'var(--text-primary)' : 'var(--text-tertiary)',
                                            borderBottom: notifCategory === tab.key ? '2px solid var(--accent-primary)' : '2px solid transparent',
                                            marginBottom: '-1px', transition: 'all 0.15s',
                                        }}
                                    >
                                        {isChinese ? tab.zh : tab.en}
                                    </button>
                                ))}
                            </div>
                        </div>
                        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
                            {(notifications as any[]).length === 0 && (
                                <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                                    {isChinese ? '暂无通知' : 'No notifications'}
                                </div>
                            )}
                            {(notifications as any[]).map((n: any) => (
                                <div
                                    key={n.id}
                                    onClick={() => {
                                        if (!n.is_read) markOneRead(n.id);
                                        if (n.type === 'broadcast' || !n.link) {
                                            setSelectedNotification(n);
                                        } else if (n.link) {
                                            navigate(n.link); setShowNotifications(false);
                                        }
                                    }}
                                    style={{
                                        padding: '14px 24px', cursor: 'pointer',
                                        borderBottom: '1px solid var(--border-subtle)',
                                        background: n.is_read ? 'transparent' : 'var(--bg-secondary)',
                                        transition: 'background 0.15s',
                                    }}
                                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-tertiary)')}
                                    onMouseLeave={e => (e.currentTarget.style.background = n.is_read ? 'transparent' : 'var(--bg-secondary)')}
                                >
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
                                        {!n.is_read && <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'var(--accent-primary)', flexShrink: 0 }} />}
                                        <span style={{ fontSize: '13px', fontWeight: 500, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {n.title}
                                        </span>
                                    </div>
                                    {n.body && <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', lineHeight: '1.4', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.body}</div>}
                                    <div style={{ fontSize: '11px', color: 'var(--text-quaternary)', marginTop: '4px' }}>
                                        {n.created_at ? new Date(n.created_at).toLocaleString() : ''}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </>
            )}
            
            {/* Notification Detail Modal */}
            {selectedNotification && (
                <div style={{ position: 'fixed', inset: 0, zIndex: 10000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setSelectedNotification(null)}>
                    <div style={{ background: 'var(--bg-primary)', borderRadius: '12px', border: '1px solid var(--border-subtle)', width: '480px', maxHeight: '90vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }} onClick={e => e.stopPropagation()}>
                        <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--border-subtle)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600 }}>{selectedNotification.title}</h3>
                            <button onClick={() => setSelectedNotification(null)} style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', fontSize: '20px', cursor: 'pointer', padding: '0' }}>×</button>
                        </div>
                        <div style={{ padding: '20px 24px', overflowY: 'auto', fontSize: '14px', lineHeight: '1.6', color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>
                            {selectedNotification.body || (isChinese ? '无详细内容' : 'No details provided')}
                        </div>
                        <div style={{ padding: '16px 24px', borderTop: '1px solid var(--border-subtle)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', color: 'var(--text-tertiary)', fontSize: '12px' }}>
                            <span>{selectedNotification.sender_name ? (isChinese ? `来自: ${selectedNotification.sender_name}` : `From: ${selectedNotification.sender_name}`) : ''}</span>
                            <span>{selectedNotification.created_at ? new Date(selectedNotification.created_at).toLocaleString() : ''}</span>
                        </div>
                    </div>
                </div>
            )}

            <main className={`main-content${isChatPage ? ' chat-page' : ''}${isAgentSettingsPage ? ' agent-settings-page' : ''}`}>
                <Outlet context={{ openTalentMarket: () => setShowTalentMarket(true) }} />
            </main>

            {showAccountSettings && (
                <AccountSettingsModal
                    user={user}
                    onClose={() => setShowAccountSettings(false)}
                    isChinese={!!isChinese}
                />
            )}

            <TalentMarketModal
                open={showTalentMarket}
                onClose={() => setShowTalentMarket(false)}
            />
            {showCompanyTour && (
                <CompanyTourOverlay
                    assistantId={tourAssistantId}
                    isChinese={!!isChinese}
                    onDone={async () => {
                        try { await onboardingApi.complete(); } catch { }
                        navigate(tourAssistantId ? `/agents/${tourAssistantId}/chat?onboarding=1` : '/plaza', { replace: true });
                    }}
                />
            )}
        </div>
    );
}
