import { useState, useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { adminApi } from '../services/api';
import { useAuthStore } from '../stores';
import { saveAccentColor, getSavedAccentColor } from '../utils/theme';
import { IconFilter } from '@tabler/icons-react';
import { ShieldCheck } from 'lucide-react';
import PlatformDashboard from './PlatformDashboard';
import { copyToClipboard } from '../utils/clipboard';

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

type SortKey = 'name' | 'sso_enabled' | 'org_admin_email' | 'user_count' | 'agent_count' | 'total_tokens' | 'created_at' | 'is_active';
type SortDir = 'asc' | 'desc';

const PAGE_SIZE = 15;

// Platform Admin — Platform Settings page with tabs
export default function AdminCompanies() {
    const { t } = useTranslation();
    const user = useAuthStore((s) => s.user);
    const [activeTab, setActiveTab] = useState<'dashboard' | 'platform' | 'companies'>('dashboard');

    // Guard: only platform_admin
    if (user?.role !== 'platform_admin') {
        return (
            <div style={{ padding: '60px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                {t('common.noPermission', 'You do not have permission to access this page.')}
            </div>
        );
    }

    const tabs = [
        { key: 'dashboard' as const, label: t('admin.tab.dashboard', 'Dashboard') },
        { key: 'platform' as const, label: t('admin.tab.platform', 'Platform') },
        { key: 'companies' as const, label: t('admin.tab.companies', 'Companies') },
    ];

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 64px)' }}>
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

            <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
                {activeTab === 'dashboard' && <PlatformDashboard />}
                {activeTab === 'platform' && <PlatformTab />}
                {activeTab === 'companies' && <CompaniesTab />}
            </div>
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

    const switchStyle = (checked: boolean, disabled?: boolean): React.CSSProperties => ({
        position: 'relative', display: 'inline-block', width: '40px', height: '22px',
        cursor: disabled ? 'not-allowed' : 'pointer', flexShrink: 0,
    });
    const switchTrack = (checked: boolean): React.CSSProperties => ({
        position: 'absolute', inset: 0,
        background: checked ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
        borderRadius: '11px', transition: 'background 0.2s',
    });
    const switchThumb = (checked: boolean): React.CSSProperties => ({
        position: 'absolute', left: checked ? '20px' : '2px', top: '2px',
        width: '18px', height: '18px', background: '#fff',
        borderRadius: '50%', transition: 'left 0.2s',
    });

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
                            <label style={switchStyle(!!settings[s.key], settingsLoading)}>
                                <input type="checkbox" checked={!!settings[s.key]} onChange={(e) => handleToggleSetting(s.key, e.target.checked)} disabled={settingsLoading}
                                    style={{ opacity: 0, width: 0, height: 0 }} />
                                <span style={switchTrack(!!settings[s.key])}>
                                    <span style={switchThumb(!!settings[s.key])} />
                                </span>
                            </label>
                        </div>
                    ))}
                </div>
            </div>

            {/* Notification Bar */}
            <div className="card" style={{ padding: '16px', marginBottom: '16px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div>
                        <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                            {t('enterprise.notificationBar.title', 'Notification Bar')}
                        </div>
                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                            {t('enterprise.notificationBar.description', 'Display a notification bar at the top of the page, visible to all users.')}
                        </div>
                    </div>
                    <label style={switchStyle(nbEnabled)}>
                        <input type="checkbox" checked={nbEnabled} onChange={e => setNbEnabled(e.target.checked)}
                            style={{ opacity: 0, width: 0, height: 0 }} />
                        <span style={switchTrack(nbEnabled)}>
                            <span style={switchThumb(nbEnabled)} />
                        </span>
                    </label>
                </div>
                <div style={{
                    maxHeight: nbEnabled ? '200px' : '0',
                    opacity: nbEnabled ? 1 : 0,
                    overflow: 'hidden',
                    transition: 'max-height 0.3s ease, opacity 0.25s ease',
                }}>
                    <div style={{ marginBottom: '12px', paddingTop: '16px' }}>
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

    // Status filter
    const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'disabled'>('all');
    const [showStatusDropdown, setShowStatusDropdown] = useState(false);
    const statusDropdownRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const handleClick = (e: MouseEvent) => {
            if (statusDropdownRef.current && !statusDropdownRef.current.contains(e.target as Node)) {
                setShowStatusDropdown(false);
            }
        };
        if (showStatusDropdown) document.addEventListener('mousedown', handleClick);
        return () => document.removeEventListener('mousedown', handleClick);
    }, [showStatusDropdown]);

    // Pagination
    const [page, setPage] = useState(0);

    // Create company
    const [showCreate, setShowCreate] = useState(false);
    const [newName, setNewName] = useState('');
    const [creating, setCreating] = useState(false);
    const [createdCode, setCreatedCode] = useState('');
    const [createdCompanyName, setCreatedCompanyName] = useState('');
    const [codeCopied, setCodeCopied] = useState(false);

    // Edit company modal
    const [editingCompany, setEditingCompany] = useState<any>(null);
    const [deletingCompanyId, setDeletingCompanyId] = useState<string | null>(null);
    const [deleteConfirmCompany, setDeleteConfirmCompany] = useState<any>(null);
    const [publicBaseUrl, setPublicBaseUrl] = useState('');

    // Invitation codes modal
    const [codesModal, setCodesModal] = useState<{ companyId: string; companyName: string } | null>(null);
    const [companyCodes, setCompanyCodes] = useState<any[]>([]);
    const [loadingCodes, setLoadingCodes] = useState(false);
    const [newlyGeneratedCode, setNewlyGeneratedCode] = useState('');
    const [codeCopied2, setCodeCopied2] = useState(false);

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
        fetchJson<any>('/enterprise/system-settings/platform')
            .then(d => { if (d.value?.public_base_url) setPublicBaseUrl(d.value.public_base_url); })
            .catch(() => {});
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
        let list = [...companies];
        if (statusFilter === 'active') list = list.filter(c => c.is_active);
        else if (statusFilter === 'disabled') list = list.filter(c => !c.is_active);
        list.sort((a, b) => {
            let av = a[sortKey], bv = b[sortKey];
            if (sortKey === 'name' || sortKey === 'org_admin_email') {
                av = (av || '').toLowerCase();
                bv = (bv || '').toLowerCase();
            }
            if (sortKey === 'created_at') {
                av = av ? new Date(av).getTime() : 0;
                bv = bv ? new Date(bv).getTime() : 0;
            }
            if (av < bv) return sortDir === 'asc' ? -1 : 1;
            if (av > bv) return sortDir === 'asc' ? 1 : -1;
            return 0;
        });
        return list;
    }, [companies, sortKey, sortDir, statusFilter]);

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
        copyToClipboard(createdCode).then(() => {
            setCodeCopied(true);
            setTimeout(() => setCodeCopied(false), 2000);
        });
    };

    const handleViewCodes = async (companyId: string, companyName: string) => {
        setCodesModal({ companyId, companyName });
        setNewlyGeneratedCode('');
        setLoadingCodes(true);
        try {
            const res = await adminApi.listCompanyCodes(companyId);
            setCompanyCodes(res.codes || []);
        } catch { setCompanyCodes([]); }
        setLoadingCodes(false);
    };

    const handleGenerateCode = async () => {
        if (!codesModal) return;
        try {
            const res = await adminApi.createCompanyCode(codesModal.companyId);
            setNewlyGeneratedCode(res.code || '');
            setCodeCopied2(false);
            // Refresh list
            const listRes = await adminApi.listCompanyCodes(codesModal.companyId);
            setCompanyCodes(listRes.codes || []);
        } catch (e: any) {
            showToast(e.message || 'Failed', 'error');
        }
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

    const handleDelete = async (company: any) => {
        setDeletingCompanyId(company.id);
        try {
            await adminApi.deleteCompany(company.id);
            loadCompanies();
            showToast(`Company "${company.name}" deleted`);
        } catch (e: any) {
            showToast(e.message || 'Failed to delete', 'error');
        }
        setDeletingCompanyId(null);
        setDeleteConfirmCompany(null);
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
        { key: 'sso_enabled', label: 'SSO', flex: '100px' },
        { key: 'org_admin_email', label: t('admin.orgAdmin', 'Admin Email'), flex: '1.5fr' },
        { key: 'user_count', label: t('admin.users', 'Users'), flex: '70px' },
        { key: 'agent_count', label: t('admin.agents', 'Agents'), flex: '70px' },
        { key: 'total_tokens', label: t('admin.tokens', 'Token Usage'), flex: '100px' },
        { key: 'created_at', label: t('admin.createdAt', 'Created'), flex: '100px' },
        { key: 'is_active', label: t('admin.status', 'Status'), flex: '100px' },
    ];
    const actionColFlex = '180px';

    const gridCols = columns.map(c => c.flex).join(' ') + ' ' + actionColFlex;

    return (
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
            {toast && (
                <div style={{
                    position: 'fixed', top: '20px', right: '20px', padding: '10px 20px',
                    borderRadius: '8px', background: toast.type === 'success' ? 'var(--success)' : 'var(--error)',
                    color: '#fff', fontSize: '13px', zIndex: 9999, boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
                }}>{toast.msg}</div>
            )}


            {/* Edit Company Modal */}
            {editingCompany && (
                <EditCompanyModal
                    company={editingCompany}
                    publicBaseUrl={publicBaseUrl}
                    onClose={() => setEditingCompany(null)}
                    onUpdated={() => { loadCompanies(); setEditingCompany(null); }}
                />
            )}

            {/* Delete Confirmation Modal */}
            {deleteConfirmCompany && (
                <div style={{
                    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 10001,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    backdropFilter: 'blur(4px)',
                }} onClick={() => setDeleteConfirmCompany(null)}>
                    <div className="card" style={{
                        padding: '24px', maxWidth: '420px', width: '90%',
                        boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
                    }} onClick={e => e.stopPropagation()}>
                        <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '8px', color: 'var(--error)' }}>
                            {t('admin.deleteCompany', 'Delete Company')}
                        </h2>
                        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                            {t('admin.deleteCompanyWarning', 'This action is irreversible. The following data will be permanently deleted:')}
                        </p>
                        <ul style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px', paddingLeft: '16px', lineHeight: '1.8' }}>
                            <li>{t('admin.deleteDataUsers', 'All users in this company')}</li>
                            <li>{t('admin.deleteDataAgents', 'All agents and their conversations')}</li>
                            <li>{t('admin.deleteDataSkills', 'All skills and LLM model configurations')}</li>
                            <li>{t('admin.deleteDataOther', 'All invitation codes, org structure, token records')}</li>
                        </ul>
                        <div style={{
                            padding: '12px', borderRadius: '8px', marginBottom: '16px',
                            background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)',
                            fontSize: '13px', fontWeight: 500,
                        }}>
                            {t('admin.deleteCompanyName', 'Company')}: <strong>{deleteConfirmCompany.name}</strong>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'monospace', marginTop: '2px' }}>
                                {deleteConfirmCompany.user_count} {t('admin.users', 'users')} &middot; {deleteConfirmCompany.agent_count} {t('admin.agents', 'agents')}
                            </div>
                        </div>
                        <div style={{ display: 'flex', gap: '8px' }}>
                            <button className="btn btn-secondary" style={{ flex: 1 }}
                                onClick={() => setDeleteConfirmCompany(null)}
                                disabled={!!deletingCompanyId}>
                                {t('common.cancel', 'Cancel')}
                            </button>
                            <button
                                className="btn"
                                style={{
                                    flex: 1, background: 'var(--error)', color: '#fff',
                                    border: 'none', borderRadius: 'var(--radius)', cursor: 'pointer',
                                    opacity: deletingCompanyId ? 0.6 : 1,
                                }}
                                onClick={() => handleDelete(deleteConfirmCompany)}
                                disabled={!!deletingCompanyId}>
                                {deletingCompanyId === deleteConfirmCompany.id
                                    ? t('common.loading', 'Loading...')
                                    : t('admin.confirmDelete', 'Yes, Delete')}
                            </button>
                        </div>
                    </div>
                </div>
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

            {/* Invitation Codes Modal */}
            {codesModal && (
                <div style={{
                    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 10001,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    backdropFilter: 'blur(4px)',
                }} onClick={() => { setCodesModal(null); setNewlyGeneratedCode(''); }}>
                    <div className="card" style={{
                        padding: '24px', maxWidth: '480px', width: '90%',
                        boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
                    }} onClick={e => e.stopPropagation()}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                            <h2 style={{ fontSize: '16px', fontWeight: 600 }}>
                                邀请码 — {codesModal.companyName}
                            </h2>
                            <button onClick={() => { setCodesModal(null); setNewlyGeneratedCode(''); }}
                                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                                </svg>
                            </button>
                        </div>

                        {newlyGeneratedCode && (
                            <div style={{
                                padding: '12px 16px', borderRadius: '8px',
                                background: 'rgba(34,197,94,0.06)', border: '1px solid rgba(34,197,94,0.2)',
                                marginBottom: '16px',
                            }}>
                                <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '6px' }}>
                                    新生成的邀请码
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                    <span style={{
                                        fontFamily: 'monospace', fontSize: '18px', fontWeight: 700,
                                        letterSpacing: '2px', color: 'var(--success)', userSelect: 'all',
                                    }}>{newlyGeneratedCode}</span>
                                    <button
                                        className="btn btn-secondary"
                                        style={{ padding: '2px 8px', fontSize: '11px', height: '24px' }}
                                        onClick={() => {
                                            navigator.clipboard.writeText(newlyGeneratedCode).then(() => {
                                                setCodeCopied2(true);
                                                setTimeout(() => setCodeCopied2(false), 2000);
                                            });
                                        }}
                                    >
                                        {codeCopied2 ? '已复制' : '复制'}
                                    </button>
                                </div>
                            </div>
                        )}

                        <div style={{ marginBottom: '12px' }}>
                            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '8px' }}>
                                有效邀请码
                            </div>
                            {loadingCodes ? (
                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', padding: '12px 0' }}>加载中...</div>
                            ) : companyCodes.length === 0 ? (
                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', padding: '12px 0' }}>暂无有效邀请码</div>
                            ) : (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '200px', overflowY: 'auto' }}>
                                    {companyCodes.map((c: any) => (
                                        <div key={c.id} style={{
                                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                            padding: '8px 12px', borderRadius: '6px',
                                            background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)',
                                            fontSize: '12px',
                                        }}>
                                            <span style={{ fontFamily: 'monospace', fontWeight: 600, letterSpacing: '1px' }}>{c.code}</span>
                                            <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>
                                                {c.used_count}/{c.max_uses === 0 ? '∞' : c.max_uses} 次使用
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>

                        <div style={{ display: 'flex', gap: '8px' }}>
                            <button className="btn btn-primary" onClick={handleGenerateCode} style={{ flex: 1 }}>
                                生成新邀请码
                            </button>
                            <button className="btn btn-secondary" onClick={() => { setCodesModal(null); setNewlyGeneratedCode(''); }}
                                style={{ padding: '0 20px' }}>
                                关闭
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
            <div className="card" style={{ padding: '0', flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'visible' }}>
                {/* Table Header */}
                <div style={{
                    display: 'grid', gridTemplateColumns: gridCols,
                    gap: '12px', padding: '10px 16px', fontSize: '11px', fontWeight: 600,
                    color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em',
                    borderBottom: '1px solid var(--border-subtle)', background: 'var(--bg-secondary)',
                    borderRadius: 'var(--radius-lg) var(--radius-lg) 0 0', flexShrink: 0, position: 'relative', zIndex: 10,
                }}>
                    {columns.map(col => (
                        <div key={col.key} style={thStyle} onClick={() => handleSort(col.key)}>
                            {col.label}<SortArrow col={col.key} />
                        </div>
                    ))}
                    <div ref={statusDropdownRef} style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: '4px' }}>
                        {t('admin.status', 'Status')}
                        <button
                            onClick={() => setShowStatusDropdown(v => !v)}
                            style={{
                                background: 'none', border: 'none', padding: '2px', cursor: 'pointer',
                                display: 'flex', alignItems: 'center', borderRadius: '4px',
                                color: statusFilter !== 'all' ? 'var(--accent-primary)' : 'var(--text-tertiary)',
                                transition: 'color 0.15s',
                            }}
                            title={t('admin.filterStatus', 'Filter by status')}
                        >
                            <IconFilter size={14} stroke={statusFilter !== 'all' ? 2.5 : 1.8} />
                        </button>
                        {showStatusDropdown && (
                            <div style={{
                                position: 'absolute', top: '100%', left: 0, marginTop: '4px',
                                background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)',
                                borderRadius: '8px', boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
                                zIndex: 100, minWidth: '120px', padding: '4px', overflow: 'hidden',
                            }}>
                                {(['all', 'active', 'disabled'] as const).map(val => (
                                    <div
                                        key={val}
                                        onClick={() => { setStatusFilter(val); setPage(0); setShowStatusDropdown(false); }}
                                        style={{
                                            padding: '6px 10px', fontSize: '12px', cursor: 'pointer',
                                            borderRadius: '6px', transition: 'background 0.1s',
                                            color: statusFilter === val ? 'var(--accent-primary)' : 'var(--text-secondary)',
                                            fontWeight: statusFilter === val ? 600 : 400,
                                            background: statusFilter === val ? 'var(--bg-secondary)' : 'transparent',
                                        }}
                                        onMouseEnter={e => { if (statusFilter !== val) (e.currentTarget.style.background = 'var(--bg-secondary)'); }}
                                        onMouseLeave={e => { if (statusFilter !== val) (e.currentTarget.style.background = 'transparent'); }}
                                    >
                                        {val === 'all' ? t('admin.all', 'All') : val === 'active' ? t('admin.active', 'Active') : t('admin.disabled', 'Disabled')}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                    <div>{t('admin.action', 'Action')}</div>
                </div>

                {/* Scrollable table body */}
                <div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
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
                            <div style={{ fontWeight: 500, display: 'flex', alignItems: 'center', gap: '6px' }}>
                                {c.name}
                                {c.is_default && (
                                    <span style={{
                                        fontSize: '10px', fontWeight: 600, padding: '1px 5px',
                                        borderRadius: '4px', background: 'rgba(59,130,246,0.1)',
                                        color: 'var(--accent-primary)', border: '1px solid rgba(59,130,246,0.2)',
                                        lineHeight: '16px', flexShrink: 0,
                                    }}>
                                        {t('admin.default', 'Default')}
                                    </span>
                                )}
                            </div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>{c.slug}</div>
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '2px' }}>
                            {c.sso_enabled ? (
                                <>
                                    <span style={{ color: 'var(--accent-primary)', fontSize: '14px' }} title="SSO Enabled"><ShieldCheck size={14} /></span>
                                    {c.sso_domain && (
                                        <span style={{ 
                                            fontSize: '9px', background: 'rgba(59,130,246,0.1)', 
                                            color: 'var(--accent-primary)', padding: '1px 4px', 
                                            borderRadius: '4px', maxWidth: '100px', 
                                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                                        }}>
                                            {c.sso_domain}
                                        </span>
                                    )}
                                </>
                            ) : (
                                <span style={{ color: 'var(--text-tertiary)', opacity: 0.3 }} title="SSO Disabled">—</span>
                            )}
                        </div>
                        <div style={{ fontSize: '12px', color: c.org_admin_email ? 'var(--text-primary)' : 'var(--text-tertiary)' }}>
                            {c.org_admin_email || '-'}
                        </div>
                        <div>{c.user_count ?? '-'}</div>
                        <div>{c.agent_count ?? '-'}</div>
                        <div style={{ fontSize: '12px', fontFamily: 'var(--font-mono)' }}>
                            {formatTokens(c.total_tokens)}
                        </div>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            {formatDate(c.created_at)}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <span className={`badge ${c.is_active ? 'badge-success' : 'badge-error'}`} style={{ fontSize: '10px' }}>
                                {c.is_active ? t('admin.active', 'Active') : t('admin.disabled', 'Disabled')}
                            </span>
                        </div>
                        <div style={{ display: 'flex', gap: '4px' }}>
                            <button
                                className="btn btn-ghost"
                                style={{ padding: '2px 8px', fontSize: '11px', height: '24px', color: 'var(--accent-primary)' }}
                                onClick={() => setEditingCompany(c)}
                            >
                                {t('admin.edit', 'Edit')}
                            </button>
                            <button
                                className="btn btn-ghost"
                                style={{ padding: '2px 8px', fontSize: '11px', height: '24px', color: 'var(--text-secondary)' }}
                                onClick={() => handleViewCodes(c.id, c.name)}
                            >
                                邀请码
                            </button>
                            <button
                                className="btn btn-ghost"
                                style={{
                                    padding: '2px 8px', fontSize: '11px', height: '24px',
                                    color: c.is_default ? 'var(--text-tertiary)' : c.is_active ? 'var(--error)' : 'var(--success)',
                                    cursor: c.is_default ? 'not-allowed' : 'pointer',
                                    opacity: c.is_default ? 0.5 : 1,
                                }}
                                onClick={() => handleToggle(c.id, !!c.is_active)}
                                disabled={c.is_default}
                                title={c.is_default ? t('admin.cannotDisableDefault', 'Cannot disable the default company — platform admin would be locked out') : undefined}
                            >
                                {c.is_active ? t('admin.disable', 'Disable') : t('admin.enable', 'Enable')}
                            </button>
                            <button
                                className="btn btn-ghost"
                                style={{
                                    padding: '2px 8px', fontSize: '11px', height: '24px',
                                    color: c.is_default ? 'var(--text-tertiary)' : 'var(--error)',
                                    cursor: c.is_default ? 'not-allowed' : 'pointer',
                                    opacity: c.is_default ? 0.4 : 1,
                                }}
                                onClick={() => !c.is_default && setDeleteConfirmCompany(c)}
                                disabled={c.is_default}
                                title={c.is_default
                                    ? t('admin.cannotDeleteDefault', 'Cannot delete the default company')
                                    : t('admin.deleteCompany', 'Delete Company')}
                            >
                                {t('admin.delete', 'Delete')}
                            </button>
                        </div>
                    </div>
                ))}

                {!loading && paged.length === 0 && !error && (
                    <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                        {statusFilter !== 'all'
                            ? t('admin.noFilterResults', 'No companies match the current filter.')
                            : t('common.noData', 'No data')}
                    </div>
                )}
                </div>

                {/* Pagination */}
                {!loading && totalPages > 1 && (
                    <div style={{
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        padding: '10px 16px', borderTop: '1px solid var(--border-subtle)',
                        fontSize: '12px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)',
                        flexShrink: 0, borderRadius: '0 0 var(--radius-lg) var(--radius-lg)',
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
        </div>
    );
}

// ─── Edit Company Modal ───────────────────────────────
function EditCompanyModal({ company, publicBaseUrl, onClose, onUpdated }: { company: any, publicBaseUrl: string, onClose: () => void, onUpdated: () => void }) {
    const { t } = useTranslation();
    const [subdomainPrefix, setSubdomainPrefix] = useState(company.subdomain_prefix || '');
    const [prefixStatus, setPrefixStatus] = useState<'idle' | 'checking' | 'available' | 'taken'>('idle');
    const [isDefault, setIsDefault] = useState(!!company.is_default);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const globalHostname = publicBaseUrl ? (() => { try { return new URL(publicBaseUrl).hostname; } catch { return ''; } })() : '';
    const globalProtocol = publicBaseUrl ? (() => { try { return new URL(publicBaseUrl).protocol + '//'; } catch { return 'https://'; } })() : 'https://';

    const checkPrefix = async (prefix: string) => {
        if (!prefix || prefix === company.subdomain_prefix) { setPrefixStatus('idle'); return; }
        setPrefixStatus('checking');
        try {
            const res = await fetchJson<any>(
                `/tenants/check-prefix?prefix=${encodeURIComponent(prefix)}&exclude_tenant_id=${company.id}`
            );
            setPrefixStatus(res.available ? 'available' : 'taken');
        } catch { setPrefixStatus('idle'); }
    };

    const handleSave = async () => {
        if (prefixStatus === 'taken') {
            setError(t('admin.prefixTakenError', 'The subdomain prefix is already taken. Please choose a different one.'));
            return;
        }
        setSaving(true);
        setError('');
        try {
            const updateData: any = {
                subdomain_prefix: subdomainPrefix.trim() || null,
            };
            if (isDefault !== !!company.is_default) {
                updateData.is_default = isDefault;
            }
            await adminApi.updateCompany(company.id, updateData);
            onUpdated();
            onClose();
        } catch (e: any) {
            setError(e.message || 'Failed to update');
        }
        setSaving(false);
    };

    const StatusBadge = ({ status }: { status: 'idle' | 'checking' | 'available' | 'taken' }) => {
        if (status === 'checking') return <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('admin.prefixChecking', 'checking...')}</span>;
        if (status === 'available') return <span style={{ fontSize: '11px', color: 'var(--success)' }}>{t('admin.prefixAvailable', 'Available')}</span>;
        if (status === 'taken') return <span style={{ fontSize: '11px', color: 'var(--error)' }}>{t('admin.prefixTaken', 'Already taken')}</span>;
        return null;
    };

    const switchStyle: React.CSSProperties = {
        position: 'relative', display: 'inline-block', width: '40px', height: '22px',
        cursor: 'pointer', flexShrink: 0,
    };
    const switchTrack = (checked: boolean): React.CSSProperties => ({
        position: 'absolute', inset: 0,
        background: checked ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
        borderRadius: '11px', transition: 'background 0.2s',
    });
    const switchThumb = (checked: boolean): React.CSSProperties => ({
        position: 'absolute', left: checked ? '20px' : '2px', top: '2px',
        width: '18px', height: '18px', background: '#fff',
        borderRadius: '50%', transition: 'left 0.2s',
    });

    return (
        <div style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 10001,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            backdropFilter: 'blur(4px)',
        }} onClick={onClose}>
            <div className="card" style={{
                padding: '24px', maxWidth: '480px', width: '90%',
                boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
            }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                    <h2 style={{ fontSize: '16px', fontWeight: 600 }}>
                        {t('admin.editCompany', 'Edit Company')}: {company.name}
                    </h2>
                    <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <line x1="18" y1="6" x2="6" y2="18" />
                            <line x1="6" y1="6" x2="18" y2="18" />
                        </svg>
                    </button>
                </div>

                {/* Default company toggle */}
                <div style={{ marginBottom: '16px', padding: '12px', borderRadius: '8px', background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <div>
                            <div style={{ fontSize: '13px', fontWeight: 500 }}>
                                {t('admin.setAsDefault', 'Default Company')}
                            </div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                {t('admin.setAsDefaultDesc', 'Users visiting the global domain will be associated with this company.')}
                            </div>
                        </div>
                        {company.is_default ? (
                            <span style={{
                                fontSize: '11px', fontWeight: 600, padding: '3px 8px',
                                borderRadius: '6px', background: 'rgba(59,130,246,0.1)',
                                color: 'var(--accent-primary)', border: '1px solid rgba(59,130,246,0.2)',
                                whiteSpace: 'nowrap',
                            }}>
                                {t('admin.currentDefault', 'Current Default')}
                            </span>
                        ) : (
                            <label style={switchStyle}>
                                <input type="checkbox" checked={isDefault} onChange={e => setIsDefault(e.target.checked)}
                                    style={{ opacity: 0, width: 0, height: 0 }} />
                                <span style={switchTrack(isDefault)}>
                                    <span style={switchThumb(isDefault)} />
                                </span>
                            </label>
                        )}
                    </div>
                </div>

                {/* Domain configuration section */}
                <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '16px', marginBottom: '16px' }}>
                    <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '4px', color: 'var(--text-secondary)' }}>
                        {t('admin.domainConfig', 'Domain Configuration')}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '12px', lineHeight: '1.5' }}>
                        {t('admin.domainConfigDesc', 'Configure a dedicated access domain for this company.')}
                    </div>

                    <div style={{ background: 'var(--bg-secondary)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
                        <label className="form-label" style={{ fontSize: '12px', marginBottom: '4px', display: 'block' }}>
                            {t('admin.subdomainPrefix', 'Subdomain Prefix')}
                        </label>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                            <input
                                className="form-input"
                                value={subdomainPrefix}
                                onChange={e => {
                                    const v = e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '');
                                    setSubdomainPrefix(v);
                                    setPrefixStatus('idle');
                                }}
                                onBlur={() => checkPrefix(subdomainPrefix)}
                                placeholder={company.slug}
                                style={{ fontSize: '13px', maxWidth: '120px' }}
                            />
                            {globalHostname && (
                                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                                    .{globalHostname}
                                </span>
                            )}
                            <StatusBadge status={prefixStatus} />
                        </div>
                        {(subdomainPrefix || company.slug) && globalHostname && (
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '6px', fontFamily: 'var(--font-mono)' }}>
                                {globalProtocol}{subdomainPrefix || company.slug}.{globalHostname}
                            </div>
                        )}
                    </div>
                </div>

                {error && <div style={{ color: 'var(--error)', fontSize: '12px', marginBottom: '12px' }}>{error}</div>}

                <div style={{ display: 'flex', gap: '8px' }}>
                    <button className="btn btn-secondary" style={{ flex: 1 }} onClick={onClose} disabled={saving}>
                        {t('common.cancel', 'Cancel')}
                    </button>
                    <button className="btn btn-primary" style={{ flex: 1 }} onClick={handleSave} disabled={saving || prefixStatus === 'taken'}>
                        {saving ? t('common.loading') : t('common.save', 'Save')}
                    </button>
                </div>
            </div>
        </div>
    );
}
