import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { adminApi } from '../services/api';
import { useAuthStore } from '../stores';
import { saveAccentColor, getSavedAccentColor } from '../utils/theme';

// Helper for authenticated JSON fetch
async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
    const token = localStorage.getItem('token');
    const res = await fetch(`/api${url}`, {
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        ...options,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || res.statusText);
    }
    return res.json();
}


// Format large token numbers with K/M/B suffixes
function formatTokens(n: number | null | undefined): string {
    if (n == null) return '-';
    if (n < 1000) return String(n);
    if (n < 1_000_000) return (n / 1000).toFixed(n < 10_000 ? 1 : 0) + 'K';
    if (n < 1_000_000_000) return (n / 1_000_000).toFixed(n < 10_000_000 ? 1 : 0) + 'M';
    return (n / 1_000_000_000).toFixed(1) + 'B';
}

// Format datetime to locale string
function formatDate(dt: string | null | undefined): string {
    if (!dt) return '-';
    return new Date(dt).toLocaleDateString(undefined, { year: 'numeric', month: '2-digit', day: '2-digit' });
}

type SortKey = 'name' | 'user_count' | 'agent_count' | 'total_tokens' | 'created_at' | 'is_active';
type SortDir = 'asc' | 'desc';

const PAGE_SIZE = 15;

// Platform Admin — Platform Settings page with tabs
export default function AdminCompanies() {
    const { t } = useTranslation();
    const user = useAuthStore((s) => s.user);
    const [activeTab, setActiveTab] = useState<'platform' | 'companies'>('platform');

    // Guard: only platform_admin
    if (user?.role !== 'platform_admin') {
        return (
            <div style={{ padding: '60px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                {t('common.noPermission', 'You do not have permission to access this page.')}
            </div>
        );
    }

    const tabs = [
        { key: 'platform' as const, label: t('admin.tab.platform', 'Platform') },
        { key: 'companies' as const, label: t('admin.tab.companies', 'Companies') },
    ];

    return (
        <div>
            <div className="page-header">
                <div>
                    <h1 className="page-title">{t('admin.platformSettings', 'Platform Settings')}</h1>
                    <p className="page-subtitle">
                        {t('admin.platformSettingsDesc', 'Manage platform-wide settings and company tenants.')}
                    </p>
                </div>
            </div>

            <div className="tabs">
                {tabs.map(tab => (
                    <div
                        key={tab.key}
                        className={`tab ${activeTab === tab.key ? 'active' : ''}`}
                        onClick={() => setActiveTab(tab.key)}
                    >
                        {tab.label}
                    </div>
                ))}
            </div>

            {activeTab === 'platform' && <PlatformTab />}
            {activeTab === 'companies' && <CompaniesTab />}
        </div>
    );
}


// ─── Platform Tab ──────────────────────────────────
function PlatformTab() {
    const { t } = useTranslation();

    // Platform settings toggles
    const [settings, setSettings] = useState<any>({});
    const [settingsLoading, setSettingsLoading] = useState(false);

    // Notification bar
    const [nbEnabled, setNbEnabled] = useState(false);
    const [nbText, setNbText] = useState('');
    const [nbSaving, setNbSaving] = useState(false);
    const [nbSaved, setNbSaved] = useState(false);

    // Public URL
    const [publicBaseUrl, setPublicBaseUrl] = useState('');
    const [urlSaving, setUrlSaving] = useState(false);
    const [urlSaved, setUrlSaved] = useState(false);

    // Toast
    const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
    const showToast = (msg: string, type: 'success' | 'error' = 'success') => {
        setToast({ msg, type });
        setTimeout(() => setToast(null), 3000);
    };

    useEffect(() => {
        // Load platform toggles
        adminApi.getPlatformSettings().then(setSettings).catch(() => { });
        // Load notification bar
        const token = localStorage.getItem('token');
        fetch('/api/enterprise/system-settings/notification_bar', {
            headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        }).then(r => r.json()).then(d => {
            if (d?.value) {
                setNbEnabled(!!d.value.enabled);
                setNbText(d.value.text || '');
            }
        }).catch(() => { });
        // Load Public URL
        fetchJson<any>('/enterprise/system-settings/platform')
            .then(d => {
                if (d.value?.public_base_url) setPublicBaseUrl(d.value.public_base_url);
            }).catch(() => { });
    }, []);

    const handleToggleSetting = async (key: string, value: boolean) => {
        setSettingsLoading(true);
        try {
            await adminApi.updatePlatformSettings({ [key]: value });
            setSettings((s: any) => ({ ...s, [key]: value }));
            showToast('Setting updated');
        } catch (e: any) {
            showToast(e.message || 'Failed', 'error');
        }
        setSettingsLoading(false);
    };

    const saveNotificationBar = async () => {
        setNbSaving(true);
        try {
            const token = localStorage.getItem('token');
            await fetch('/api/enterprise/system-settings/notification_bar', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
                body: JSON.stringify({ value: { enabled: nbEnabled, text: nbText } }),
            });
            setNbSaved(true);
            setTimeout(() => setNbSaved(false), 2000);
        } catch { }
        setNbSaving(false);
    };

    const savePublicUrl = async () => {
        setUrlSaving(true);
        try {
            await fetchJson('/enterprise/system-settings/platform', {
                method: 'PUT',
                body: JSON.stringify({ value: { public_base_url: publicBaseUrl } }),
            });
            setUrlSaved(true);
            setTimeout(() => setUrlSaved(false), 2000);
        } catch (e) {
            showToast('Failed to save', 'error');
        }
        setUrlSaving(false);
    };

    return (
        <>
            {toast && (
                <div style={{
                    position: 'fixed', top: '20px', right: '20px', padding: '10px 20px',
                    borderRadius: '8px', background: toast.type === 'success' ? 'var(--success)' : 'var(--error)',
                    color: '#fff', fontSize: '13px', zIndex: 9999, boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
                }}>{toast.msg}</div>
            )}

            {/* Allow self-create company toggle */}
            <div className="card" style={{ padding: '16px', marginBottom: '16px' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                    {[
                        { key: 'allow_self_create_company', label: t('admin.allowSelfCreate', 'Allow users to create their own companies'), desc: t('admin.allowSelfCreateDesc', 'When disabled, only platform admins can create companies.') },
                    ].map(s => (
                        <div key={s.key} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 0' }}>
                            <div>
                                <div style={{ fontSize: '13px', fontWeight: 500 }}>{s.label}</div>
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{s.desc}</div>
                            </div>
                            <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: settingsLoading ? 'not-allowed' : 'pointer', flexShrink: 0 }}>
                                <input type="checkbox" checked={!!settings[s.key]} onChange={(e) => handleToggleSetting(s.key, e.target.checked)} disabled={settingsLoading}
                                    style={{ opacity: 0, width: 0, height: 0 }} />
                                <span style={{ position: 'absolute', inset: 0, background: settings[s.key] ? '#22c55e' : 'var(--bg-tertiary)', borderRadius: '11px', transition: 'background 0.2s' }}>
                                    <span style={{ position: 'absolute', left: settings[s.key] ? '20px' : '2px', top: '2px', width: '18px', height: '18px', background: '#fff', borderRadius: '50%', transition: 'left 0.2s' }} />
                                </span>
                            </label>
                        </div>
                    ))}
                </div>
            </div>

            {/* Notification Bar */}
            <div className="card" style={{ padding: '16px', marginBottom: '16px' }}>
                <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '4px', color: 'var(--text-secondary)' }}>
                    {t('enterprise.notificationBar.title', 'Notification Bar')}
                </div>
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                    {t('enterprise.notificationBar.description', 'Display a notification bar at the top of the page, visible to all users.')}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '13px', fontWeight: 500 }}>
                        <input
                            type="checkbox"
                            checked={nbEnabled}
                            onChange={e => setNbEnabled(e.target.checked)}
                            style={{ width: '16px', height: '16px', cursor: 'pointer' }}
                        />
                        {t('enterprise.notificationBar.enabled', 'Enable notification bar')}
                    </label>
                </div>
                <div style={{ marginBottom: '12px' }}>
                    <label className="form-label">{t('enterprise.notificationBar.text', 'Notification text')}</label>
                    <input
                        className="form-input"
                        value={nbText}
                        onChange={e => setNbText(e.target.value)}
                        placeholder={t('enterprise.notificationBar.textPlaceholder', 'e.g. v2.1 released with new features!')}
                        style={{ fontSize: '13px' }}
                    />
                </div>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <button className="btn btn-primary" onClick={saveNotificationBar} disabled={nbSaving}>
                        {nbSaving ? t('common.loading') : t('common.save', 'Save')}
                    </button>
                    {nbSaved && <span style={{ color: 'var(--success)', fontSize: '12px' }}>{t('enterprise.config.saved', 'Saved')}</span>}
                </div>
            </div>

            {/* Public URL */}
            <div className="card" style={{ padding: '16px', marginBottom: '16px' }}>
                <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '4px', color: 'var(--text-secondary)' }}>
                    {t('admin.publicUrl.title', 'Public URL')}
                </div>
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                    {t('admin.publicUrl.desc', 'The external URL used for webhook callbacks (Slack, Feishu, Discord, etc.) and published page links. Include the protocol (e.g. https://example.com).')}
                </div>
                <div style={{ marginBottom: '12px' }}>
                    <input
                        className="form-input"
                        value={publicBaseUrl}
                        onChange={e => setPublicBaseUrl(e.target.value)}
                        placeholder="https://your-domain.com"
                        style={{ fontSize: '13px' }}
                    />
                </div>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <button className="btn btn-primary" onClick={savePublicUrl} disabled={urlSaving}>
                        {urlSaving ? t('common.loading') : t('common.save', 'Save')}
                    </button>
                    {urlSaved && <span style={{ color: 'var(--success)', fontSize: '12px' }}>{t('enterprise.config.saved', 'Saved')}</span>}
                </div>
            </div>
        </>
    );
}


// ─── Companies Tab ─────────────────────────────────
function CompaniesTab() {
    const { t } = useTranslation();
    const [companies, setCompanies] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Sorting
    const [sortKey, setSortKey] = useState<SortKey>('created_at');
    const [sortDir, setSortDir] = useState<SortDir>('desc');

    // Pagination
    const [page, setPage] = useState(0);

    // Create company
    const [showCreate, setShowCreate] = useState(false);
    const [newName, setNewName] = useState('');
    const [creating, setCreating] = useState(false);
    const [createdCode, setCreatedCode] = useState('');
    const [createdCompanyName, setCreatedCompanyName] = useState('');
    const [codeCopied, setCodeCopied] = useState(false);

    // Toast
    const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
    const showToast = (msg: string, type: 'success' | 'error' = 'success') => {
        setToast({ msg, type });
        setTimeout(() => setToast(null), 3000);
    };

    const loadCompanies = async () => {
        setLoading(true);
        try {
            const data = await adminApi.listCompanies();
            setCompanies(data);
        } catch (e: any) {
            setError(e.message);
        }
        setLoading(false);
    };

    useEffect(() => {
        loadCompanies();
    }, []);

    // Sorting logic
    const handleSort = (key: SortKey) => {
        if (sortKey === key) {
            setSortDir(d => d === 'asc' ? 'desc' : 'asc');
        } else {
            setSortKey(key);
            setSortDir(key === 'name' ? 'asc' : 'desc');
        }
        setPage(0);
    };

    const sorted = useMemo(() => {
        const list = [...companies];
        list.sort((a, b) => {
            let av = a[sortKey], bv = b[sortKey];
            if (sortKey === 'name') {
                av = (av || '').toLowerCase();
                bv = (bv || '').toLowerCase();
            }
            if (sortKey === 'created_at') {
                av = av ? new Date(av).getTime() : 0;
                bv = bv ? new Date(bv).getTime() : 0;
            }
            if (sortKey === 'is_active') {
                av = av ? 1 : 0;
                bv = bv ? 1 : 0;
            }
            if (av < bv) return sortDir === 'asc' ? -1 : 1;
            if (av > bv) return sortDir === 'asc' ? 1 : -1;
            return 0;
        });
        return list;
    }, [companies, sortKey, sortDir]);

    // Pagination
    const totalPages = Math.ceil(sorted.length / PAGE_SIZE);
    const paged = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

    const handleCreate = async () => {
        if (!newName.trim()) return;
        setCreating(true);
        try {
            const result = await adminApi.createCompany({ name: newName.trim() });
            setCreatedCompanyName(newName.trim());
            setCreatedCode(result.admin_invitation_code || '');
            setCodeCopied(false);
            setNewName('');
            setShowCreate(false);
            loadCompanies();
        } catch (e: any) {
            showToast(e.message || 'Failed', 'error');
        }
        setCreating(false);
    };

    const handleCopyCode = () => {
        navigator.clipboard.writeText(createdCode).then(() => {
            setCodeCopied(true);
            setTimeout(() => setCodeCopied(false), 2000);
        });
    };

    const handleToggle = async (id: string, currentlyActive: boolean) => {
        const action = currentlyActive ? 'disable' : 'enable';
        if (currentlyActive && !confirm(t('admin.confirmDisable', 'Disable this company? All users and agents will be paused.'))) return;
        try {
            await adminApi.toggleCompany(id);
            loadCompanies();
            showToast(`Company ${action}d`);
        } catch (e: any) {
            showToast(e.message || 'Failed', 'error');
        }
    };

    // Sort indicator arrow
    const SortArrow = ({ col }: { col: SortKey }) => {
        if (sortKey !== col) return <span style={{ opacity: 0.3, marginLeft: '2px' }}>&#x2195;</span>;
        return <span style={{ marginLeft: '2px' }}>{sortDir === 'asc' ? '\u2191' : '\u2193'}</span>;
    };

    // Column header style
    const thStyle: React.CSSProperties = {
        cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', gap: '2px',
    };

    const columns: { key: SortKey; label: string; flex: string }[] = [
        { key: 'name', label: t('admin.company', 'Company'), flex: '2fr' },
        { key: 'user_count', label: t('admin.users', 'Users'), flex: '80px' },
        { key: 'agent_count', label: t('admin.agents', 'Agents'), flex: '80px' },
        { key: 'total_tokens', label: t('admin.tokens', 'Token Usage'), flex: '100px' },
        { key: 'created_at', label: t('admin.createdAt', 'Created'), flex: '100px' },
        { key: 'is_active', label: t('admin.status', 'Status'), flex: '120px' },
    ];

    const gridCols = columns.map(c => c.flex).join(' ');

    return (
        <>
            {toast && (
                <div style={{
                    position: 'fixed', top: '20px', right: '20px', padding: '10px 20px',
                    borderRadius: '8px', background: toast.type === 'success' ? 'var(--success)' : 'var(--error)',
                    color: '#fff', fontSize: '13px', zIndex: 9999, boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
                }}>{toast.msg}</div>
            )}

            {/* Invitation Code Modal */}
            {createdCode && (
                <div style={{
                    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 10000,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    backdropFilter: 'blur(4px)',
                }} onClick={() => setCreatedCode('')}>
                    <div className="card" style={{
                        padding: '32px', maxWidth: '480px', width: '90%',
                        boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
                    }} onClick={e => e.stopPropagation()}>
                        <div style={{ textAlign: 'center', marginBottom: '20px' }}>
                            <div style={{
                                width: '48px', height: '48px', borderRadius: '50%',
                                background: 'rgba(34,197,94,0.1)', display: 'flex',
                                alignItems: 'center', justifyContent: 'center',
                                margin: '0 auto 12px', fontSize: '20px',
                            }}>
                                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                                    <polyline points="22 4 12 14.01 9 11.01" />
                                </svg>
                            </div>
                            <h2 style={{ fontSize: '18px', fontWeight: 600, marginBottom: '4px' }}>
                                {t('admin.companyCreated', 'Company Created')}
                            </h2>
                            <p style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>
                                <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{createdCompanyName}</span>
                                {' '}{t('admin.companyCreatedDesc', 'has been created successfully.')}
                            </p>
                        </div>

                        <div style={{
                            padding: '16px', borderRadius: '8px',
                            background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)',
                            marginBottom: '16px',
                        }}>
                            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '8px' }}>
                                {t('admin.inviteCodeLabel', 'Admin Invitation Code')}
                            </div>
                            <div style={{
                                fontFamily: 'monospace', fontSize: '22px', fontWeight: 700,
                                letterSpacing: '3px', color: 'var(--success)',
                                textAlign: 'center', padding: '8px 0',
                                userSelect: 'all',
                            }}>
                                {createdCode}
                            </div>
                        </div>

                        <div style={{
                            fontSize: '12px', color: 'var(--text-tertiary)',
                            lineHeight: '1.6', marginBottom: '20px',
                            padding: '12px', borderRadius: '6px',
                            background: 'rgba(59,130,246,0.06)', border: '1px solid rgba(59,130,246,0.12)',
                        }}>
                            <div style={{ fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '4px' }}>
                                {t('admin.inviteCodeHowTo', 'How to use this code:')}
                            </div>
                            {t('admin.inviteCodeExplain', 'Send this code to the person who will manage this company. They should register a new account on the platform, then enter this code to join. The first person to use it will automatically become the Org Admin of this company. This code is single-use.')}
                        </div>

                        <div style={{ display: 'flex', gap: '8px' }}>
                            <button className="btn btn-primary" onClick={handleCopyCode}
                                style={{ flex: 1, height: '36px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}>
                                {codeCopied ? (
                                    <>{t('admin.copied', 'Copied')}</>
                                ) : (
                                    <>
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <rect x="9" y="9" width="13" height="13" rx="2" />
                                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                                        </svg>
                                        {t('admin.copyCode', 'Copy Code')}
                                    </>
                                )}
                            </button>
                            <button className="btn btn-secondary" onClick={() => setCreatedCode('')}
                                style={{ height: '36px', padding: '0 20px' }}>
                                {t('common.close', 'Close')}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Create Company button */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '16px' }}>
                <button className="btn btn-primary" onClick={() => { setShowCreate(true); setCreatedCode(''); }}>
                    + {t('admin.createCompany', 'Create Company')}
                </button>
            </div>

            {/* Create Company — inline input */}
            {showCreate && (
                <div className="card" style={{ padding: '16px', marginBottom: '16px', border: '1px solid var(--accent-primary)' }}>
                    <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '12px' }}>
                        {t('admin.createCompany', 'Create Company')}
                    </div>
                    <div style={{ display: 'flex', gap: '8px' }}>
                        <input className="form-input" value={newName} onChange={e => setNewName(e.target.value)}
                            placeholder={t('admin.companyNamePlaceholder', 'Company name')}
                            onKeyDown={e => e.key === 'Enter' && handleCreate()}
                            style={{ flex: 1 }} autoFocus />
                        <button className="btn btn-primary" onClick={handleCreate} disabled={creating || !newName.trim()}>
                            {creating ? '...' : t('common.create', 'Create')}
                        </button>
                        <button className="btn btn-secondary" onClick={() => setShowCreate(false)}>
                            {t('common.cancel', 'Cancel')}
                        </button>
                    </div>
                </div>
            )}

            {/* Company List */}
            <div className="card" style={{ padding: '0', overflow: 'hidden' }}>
                {/* Table Header */}
                <div style={{
                    display: 'grid', gridTemplateColumns: gridCols,
                    gap: '12px', padding: '10px 16px', fontSize: '11px', fontWeight: 600,
                    color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em',
                    borderBottom: '1px solid var(--border-subtle)', background: 'var(--bg-secondary)',
                }}>
                    {columns.map(col => (
                        <div key={col.key} style={thStyle} onClick={() => handleSort(col.key)}>
                            {col.label}<SortArrow col={col.key} />
                        </div>
                    ))}
                </div>

                {loading && (
                    <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                        {t('common.loading', 'Loading...')}
                    </div>
                )}

                {error && (
                    <div style={{ textAlign: 'center', padding: '24px', color: 'var(--error)', fontSize: '13px' }}>
                        {error}
                    </div>
                )}

                {!loading && paged.map((c: any) => (
                    <div key={c.id} style={{
                        display: 'grid', gridTemplateColumns: gridCols,
                        gap: '12px', padding: '12px 16px', alignItems: 'center',
                        borderBottom: '1px solid var(--border-subtle)', fontSize: '13px',
                        opacity: c.is_active ? 1 : 0.5,
                    }}>
                        <div>
                            <div style={{ fontWeight: 500 }}>{c.name}</div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>{c.slug}</div>
                        </div>
                        <div>{c.user_count ?? '-'}</div>
                        <div>{c.agent_count ?? '-'}</div>
                        <div style={{ fontSize: '12px', fontFamily: 'var(--font-mono)' }}>
                            {formatTokens(c.total_tokens)}
                        </div>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            {formatDate(c.created_at)}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <span className={`badge ${c.is_active ? 'badge-success' : 'badge-error'}`} style={{ fontSize: '10px' }}>
                                {c.is_active ? t('admin.active', 'Active') : t('admin.disabled', 'Disabled')}
                            </span>
                            <button
                                className="btn btn-ghost"
                                style={{
                                    padding: '2px 6px', fontSize: '10px',
                                    color: c.slug === 'default' ? 'var(--text-tertiary)' : c.is_active ? 'var(--error)' : 'var(--success)',
                                    cursor: c.slug === 'default' ? 'not-allowed' : 'pointer',
                                    opacity: c.slug === 'default' ? 0.5 : 1,
                                }}
                                onClick={() => handleToggle(c.id, c.is_active)}
                                disabled={c.slug === 'default'}
                                title={c.slug === 'default' ? t('admin.cannotDisableDefault', 'Cannot disable the default company — platform admin would be locked out') : undefined}
                            >
                                {c.is_active ? t('admin.disable', 'Disable') : t('admin.enable', 'Enable')}
                            </button>
                        </div>
                    </div>
                ))}

                {!loading && companies.length === 0 && !error && (
                    <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                        {t('common.noData', 'No data')}
                    </div>
                )}

                {/* Pagination */}
                {!loading && totalPages > 1 && (
                    <div style={{
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        padding: '10px 16px', borderTop: '1px solid var(--border-subtle)',
                        fontSize: '12px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)',
                    }}>
                        <span>
                            {t('admin.showing', '{{start}}-{{end}} of {{total}}', {
                                start: page * PAGE_SIZE + 1,
                                end: Math.min((page + 1) * PAGE_SIZE, sorted.length),
                                total: sorted.length,
                            })}
                        </span>
                        <div style={{ display: 'flex', gap: '4px' }}>
                            <button className="btn btn-ghost" style={{ padding: '4px 10px', fontSize: '12px' }}
                                disabled={page === 0} onClick={() => setPage(p => p - 1)}>
                                &lsaquo; {t('admin.prev', 'Prev')}
                            </button>
                            <button className="btn btn-ghost" style={{ padding: '4px 10px', fontSize: '12px' }}
                                disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>
                                {t('admin.next', 'Next')} &rsaquo;
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </>
    );
}
