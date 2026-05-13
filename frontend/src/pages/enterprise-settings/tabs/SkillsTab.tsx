import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import PromptModal from '../../../components/PromptModal';
import FileBrowser from '../../../components/FileBrowser';
import type { FileBrowserApi } from '../../../components/FileBrowser';
import { skillApi } from '../../../services/api';

// ─── Skills Tab ────────────────────────────────────
export default function SkillsTab() {
    const { t } = useTranslation();
    const [refreshKey, setRefreshKey] = useState(0);
    const [showClawhubModal, setShowClawhubModal] = useState(false);
    const [showUrlModal, setShowUrlModal] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<any[]>([]);
    const [searching, setSearching] = useState(false);
    const [hasSearched, setHasSearched] = useState(false);
    const [installing, setInstalling] = useState<string | null>(null);
    const [urlInput, setUrlInput] = useState('');
    const [urlPreview, setUrlPreview] = useState<any | null>(null);
    const [urlPreviewing, setUrlPreviewing] = useState(false);
    const [urlImporting, setUrlImporting] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [showSettings, setShowSettings] = useState(false);
    const [tokenInput, setTokenInput] = useState('');
    const [tokenStatus, setTokenStatus] = useState<{ configured: boolean; source: string; masked: string; clawhub_configured?: boolean; clawhub_masked?: string } | null>(null);
    const [savingToken, setSavingToken] = useState(false);
    const [clawhubKeyInput, setClawhubKeyInput] = useState('');
    const [savingClawhubKey, setSavingClawhubKey] = useState(false);

    const showToast = (message: string, type: 'success' | 'error' = 'success') => {
        setToast({ message, type });
        setTimeout(() => setToast(null), 4000);
    };

    const adapter: FileBrowserApi = useMemo(() => ({
        list: (path: string) => skillApi.browse.list(path),
        read: (path: string) => skillApi.browse.read(path),
        write: (path: string, content: string) => skillApi.browse.write(path, content),
        delete: (path: string) => skillApi.browse.delete(path),
    }), []);

    const handleSearch = async () => {
        if (!searchQuery.trim()) return;
        setSearching(true);
        setSearchResults([]);
        setHasSearched(true);
        try {
            const results = await skillApi.clawhub.search(searchQuery);
            setSearchResults(results);
        } catch (e: any) {
            showToast(e.message || 'Search failed', 'error');
        }
        setSearching(false);
    };

    const handleInstall = async (slug: string) => {
        setInstalling(slug);
        try {
            const result = await skillApi.clawhub.install(slug);
            const tierLabel = result.tier === 1 ? 'Tier 1 (Pure Prompt)' : result.tier === 2 ? 'Tier 2 (CLI/API)' : 'Tier 3 (OpenClaw Native)';
            showToast(`Installed "${result.name}" — ${tierLabel}, ${result.file_count} files`);
            setRefreshKey(k => k + 1);
            // Remove from search results
            setSearchResults(prev => prev.filter(r => r.slug !== slug));
        } catch (e: any) {
            showToast(e.message || 'Install failed', 'error');
        }
        setInstalling(null);
    };

    const handleUrlPreview = async () => {
        if (!urlInput.trim()) return;
        setUrlPreviewing(true);
        setUrlPreview(null);
        try {
            const preview = await skillApi.previewUrl(urlInput);
            setUrlPreview(preview);
        } catch (e: any) {
            showToast(e.message || 'Preview failed', 'error');
        }
        setUrlPreviewing(false);
    };

    const handleUrlImport = async () => {
        if (!urlInput.trim()) return;
        setUrlImporting(true);
        try {
            const result = await skillApi.importFromUrl(urlInput);
            showToast(`Imported "${result.name}" — ${result.file_count} files`);
            setRefreshKey(k => k + 1);
            setShowUrlModal(false);
            setUrlInput('');
            setUrlPreview(null);
        } catch (e: any) {
            showToast(e.message || 'Import failed', 'error');
        }
        setUrlImporting(false);
    };

    const tierBadge = (tier: number) => {
        const styles: Record<number, { bg: string; color: string; label: string }> = {
            1: { bg: 'rgba(52,199,89,0.12)', color: 'var(--success, #34c759)', label: 'Tier 1 · Pure Prompt' },
            2: { bg: 'rgba(255,159,10,0.12)', color: 'var(--warning, #ff9f0a)', label: 'Tier 2 · CLI/API' },
            3: { bg: 'rgba(255,59,48,0.12)', color: 'var(--error, #ff3b30)', label: 'Tier 3 · OpenClaw Native' },
        };
        const s = styles[tier] || styles[1];
        return (
            <span style={{ padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 500, background: s.bg, color: s.color }}>
                {s.label}
            </span>
        );
    };

    return (
        <div>
            <div style={{ marginBottom: '12px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                    <h3>{t('enterprise.tabs.skills', 'Skill Registry')}</h3>
                    <p style={{ fontSize: '13px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                        {t('enterprise.tools.manageGlobalSkills')}
                    </p>
                </div>
                <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
                    <button
                        className="btn btn-secondary"
                        style={{ fontSize: '13px', padding: '6px 10px', minWidth: 'auto' }}
                        onClick={async () => {
                            setShowSettings(s => !s);
                            if (!tokenStatus) {
                                try {
                                    const status = await skillApi.settings.getToken();
                                    setTokenStatus(status);
                                } catch { /* ignore */ }
                            }
                        }}
                        title="Settings"
                    >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <circle cx="12" cy="12" r="3" />
                            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
                        </svg>
                    </button>
                    <button
                        className="btn btn-secondary"
                        style={{ fontSize: '13px' }}
                        onClick={() => { setShowUrlModal(true); setUrlInput(''); setUrlPreview(null); }}
                    >
                        {t('enterprise.tools.importFromUrl')}
                    </button>
                    <button
                        className="btn btn-primary"
                        style={{ fontSize: '13px' }}
                        onClick={() => { setShowClawhubModal(true); setSearchQuery(''); setSearchResults([]); setHasSearched(false); }}
                    >
                        {t('enterprise.tools.browseClawhub')}
                    </button>
                </div>
            </div>

            {/* GitHub Token Settings Panel */}
            {showSettings && (
                <div style={{
                    marginBottom: '16px', padding: '16px', borderRadius: '8px',
                    border: '1px solid var(--border-primary)',
                    background: 'var(--bg-secondary, rgba(255,255,255,0.02))',
                }}>
                    <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        {t('enterprise.tools.githubToken')}
                        <span className="metric-tooltip-trigger" style={{ display: 'inline-flex', alignItems: 'center', cursor: 'help', color: 'var(--text-tertiary)' }}>
                            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="8" cy="8" r="6.5" /><path d="M8 7v4M8 5.5v0" /></svg>
                            <span className="metric-tooltip" style={{ width: '300px', bottom: 'auto', top: 'calc(100% + 6px)', left: '-8px', fontWeight: 400 }}>
                                <div style={{ marginBottom: '6px', fontWeight: 500 }}>{t('enterprise.tools.howToGenerateGithubToken')}</div>
                                {t('enterprise.tools.githubTokenStep1')}<br />
                                {t('enterprise.tools.githubTokenStep2')}<br />
                                {t('enterprise.tools.githubTokenStep3')}<br />
                                {t('enterprise.tools.githubTokenStep4')}<br />
                                {t('enterprise.tools.githubTokenStep5')}<br />
                                <div style={{ marginTop: '6px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                    {t('enterprise.tools.orVisit')}
                                </div>
                            </span>
                        </span>
                    </div>
                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                        {t('enterprise.tools.githubTokenDesc')}
                    </p>
                    {tokenStatus?.configured && (
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                            {t('enterprise.tools.currentToken')} <code style={{ padding: '2px 6px', borderRadius: '4px', background: 'var(--bg-tertiary)', fontSize: '11px' }}>{tokenStatus.masked}</code>
                            <span style={{ marginLeft: '8px', color: 'var(--text-tertiary)' }}>({tokenStatus.source})</span>
                        </div>
                    )}
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        {/* Hidden inputs to absorb browser autofill */}
                        <input type="text" name="prevent_autofill_user" style={{ display: 'none' }} tabIndex={-1} />
                        <input type="password" name="prevent_autofill_pass" style={{ display: 'none' }} tabIndex={-1} />
                        <input
                            type="text"
                            className="input"
                            autoComplete="off"
                            data-form-type="other"
                            placeholder="ghp_xxxxxxxxxxxx"
                            value={tokenInput}
                            onChange={e => setTokenInput(e.target.value)}
                            style={{ flex: 1, fontSize: '13px', fontFamily: 'monospace', WebkitTextSecurity: 'disc' } as React.CSSProperties}
                        />
                        <button
                            className="btn btn-primary"
                            style={{ fontSize: '13px' }}
                            disabled={!tokenInput.trim() || savingToken}
                            onClick={async () => {
                                setSavingToken(true);
                                try {
                                    await skillApi.settings.setToken(tokenInput.trim());
                                    const status = await skillApi.settings.getToken();
                                    setTokenStatus(status);
                                    setTokenInput('');
                                    showToast(t('enterprise.tools.githubTokenSaved'));
                                } catch (e: any) {
                                    showToast(e.message || t('enterprise.tools.failedToSave'), 'error');
                                }
                                setSavingToken(false);
                            }}
                        >
                            {savingToken ? t('enterprise.tools.saving') : t('enterprise.tools.save')}
                        </button>
                        {tokenStatus?.configured && tokenStatus.source === 'tenant' && (
                            <button
                                className="btn btn-secondary"
                                style={{ fontSize: '13px' }}
                                onClick={async () => {
                                    try {
                                        await skillApi.settings.setToken('');
                                        const status = await skillApi.settings.getToken();
                                        setTokenStatus(status);
                                        showToast(t('enterprise.tools.tokenCleared'));
                                    } catch (e: any) {
                                        showToast(e.message || t('enterprise.tools.failed'), 'error');
                                    }
                                }}
                            >
                                {t('enterprise.tools.clear')}
                            </button>
                        )}
                    </div>

                    {/* ClawHub API Key */}
                    <div style={{ marginTop: '16px' }}>
                        <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                            {t('enterprise.tools.clawhubApiKey')}
                            <span className="metric-tooltip-trigger" style={{ display: 'inline-flex', alignItems: 'center', cursor: 'help', color: 'var(--text-tertiary)' }}>
                                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="8" cy="8" r="6.5" /><path d="M8 7v4M8 5.5v0" /></svg>
                                <span className="metric-tooltip" style={{ width: '280px', bottom: 'auto', top: 'calc(100% + 6px)', left: '-8px', fontWeight: 400 }}>
                                    {t('enterprise.tools.clawhubApiKeyDesc')}
                                </span>
                            </span>
                        </div>
                        <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                            {t('enterprise.tools.authenticatedRequestsGetHigherRateLimits')}
                        </p>
                        {tokenStatus?.clawhub_configured && (
                            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                                {t('enterprise.tools.currentKey')} <code style={{ padding: '2px 6px', borderRadius: '4px', background: 'var(--bg-tertiary)', fontSize: '11px' }}>{tokenStatus.clawhub_masked}</code>
                            </div>
                        )}
                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                            <input type="text" name="prevent_autofill_ch_user" style={{ display: 'none' }} tabIndex={-1} />
                            <input type="password" name="prevent_autofill_ch_pass" style={{ display: 'none' }} tabIndex={-1} />
                            <input
                                type="text"
                                className="input"
                                autoComplete="off"
                                data-form-type="other"
                                placeholder="sk-ant-xxxxxxxxxxxx"
                                value={clawhubKeyInput}
                                onChange={e => setClawhubKeyInput(e.target.value)}
                                style={{ flex: 1, fontSize: '13px', fontFamily: 'monospace', WebkitTextSecurity: 'disc' } as React.CSSProperties}
                            />
                            <button
                                className="btn btn-primary"
                                style={{ fontSize: '13px' }}
                                disabled={!clawhubKeyInput.trim() || savingClawhubKey}
                                onClick={async () => {
                                    setSavingClawhubKey(true);
                                    try {
                                        await skillApi.settings.setClawhubKey(clawhubKeyInput.trim());
                                        const status = await skillApi.settings.getToken();
                                        setTokenStatus(status);
                                        setClawhubKeyInput('');
                                        showToast(t('enterprise.tools.clawhubApiKeySaved'));
                                    } catch (e: any) {
                                        showToast(e.message || t('enterprise.tools.failedToSave'), 'error');
                                    }
                                    setSavingClawhubKey(false);
                                }}
                            >
                                {savingClawhubKey ? t('enterprise.tools.saving') : t('enterprise.tools.save')}
                            </button>
                            {tokenStatus?.clawhub_configured && (
                                <button
                                    className="btn btn-secondary"
                                    style={{ fontSize: '13px' }}
                                    onClick={async () => {
                                        try {
                                            await skillApi.settings.setClawhubKey('');
                                            const status = await skillApi.settings.getToken();
                                            setTokenStatus(status);
                                            showToast(t('enterprise.tools.tokenCleared'));
                                        } catch (e: any) {
                                            showToast(e.message || t('enterprise.tools.failed'), 'error');
                                        }
                                    }}
                                >
                                    {t('enterprise.tools.clear')}
                                </button>
                            )}
                        </div>
                    </div>
                </div>
            )}

            <FileBrowser
                key={refreshKey}
                api={adapter}
                features={{ newFile: true, newFolder: true, edit: true, delete: true, directoryNavigation: true }}
                title={t('agent.skills.skillFiles', 'Skill Files')}
                onRefresh={() => setRefreshKey(k => k + 1)}
            />

            {/* Toast */}
            {toast && (
                <div style={{
                    position: 'fixed', bottom: '24px', right: '24px', zIndex: 10000,
                    padding: '12px 20px', borderRadius: '8px', fontSize: '13px', fontWeight: 500,
                    background: toast.type === 'error' ? 'rgba(255,59,48,0.95)' : 'rgba(52,199,89,0.95)',
                    color: '#fff', boxShadow: '0 4px 16px rgba(0,0,0,0.2)', maxWidth: '400px',
                    animation: 'fadeIn 200ms ease',
                }}>
                    {toast.message}
                </div>
            )}

            {/* ClawHub Search Modal */}
            {showClawhubModal && (
                <div style={{
                    position: 'fixed', inset: 0, zIndex: 9999,
                    background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                }} onClick={() => setShowClawhubModal(false)}>
                    <div style={{
                        background: 'var(--bg-primary)', borderRadius: '12px', width: '640px', maxHeight: '80vh',
                        display: 'flex', flexDirection: 'column', border: '1px solid var(--border-default)',
                        boxShadow: '0 16px 48px rgba(0,0,0,0.2)',
                    }} onClick={e => e.stopPropagation()}>
                        {/* Header */}
                        <div style={{ padding: '20px 24px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                <h3 style={{ margin: 0, fontSize: '16px' }}>{t('enterprise.tools.browseClawhub')}</h3>
                                <button className="btn btn-ghost" onClick={() => setShowClawhubModal(false)} style={{ padding: '4px 8px', fontSize: '16px', lineHeight: 1 }}>x</button>
                            </div>
                            <div style={{ display: 'flex', gap: '8px' }}>
                                <input
                                    className="input"
                                    placeholder={t('enterprise.tools.searchSkills')}
                                    value={searchQuery}
                                    onChange={e => setSearchQuery(e.target.value)}
                                    onKeyDown={e => e.key === 'Enter' && handleSearch()}
                                    autoFocus
                                    style={{ flex: 1, fontSize: '13px' }}
                                />
                                <button className="btn btn-primary" onClick={handleSearch} disabled={searching} style={{ fontSize: '13px' }}>
                                    {searching ? t('enterprise.tools.searching') : t('enterprise.tools.search')}
                                </button>
                            </div>
                        </div>
                        {/* Results */}
                        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 24px' }}>
                            {searchResults.length === 0 && !searching && (
                                <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                                    {hasSearched ? t('enterprise.tools.noResultsFound') : t('enterprise.tools.searchForSkills')}
                                </div>
                            )}
                            {searching && (
                                <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                                    {t('enterprise.tools.searchingClawhub')}
                                </div>
                            )}
                            {searchResults.map((r: any) => (
                                <div key={r.slug} style={{
                                    padding: '12px 0', borderBottom: '1px solid var(--border-subtle)',
                                    display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px',
                                }}>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                                            <span style={{ fontWeight: 600, fontSize: '14px' }}>{r.displayName}</span>
                                            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>{r.slug}</span>
                                            {r.version && <span style={{ fontSize: '10px', color: 'var(--accent-text)', background: 'var(--accent-subtle)', padding: '1px 6px', borderRadius: '4px' }}>v{r.version}</span>}
                                        </div>
                                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: '1.4' }}>
                                            {r.summary?.slice(0, 160)}{r.summary?.length > 160 ? '...' : ''}
                                        </div>
                                        {r.updatedAt && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>Updated {new Date(r.updatedAt).toLocaleDateString()}</div>}
                                    </div>
                                    <button
                                        className="btn btn-secondary"
                                        style={{ fontSize: '12px', flexShrink: 0 }}
                                        disabled={installing === r.slug}
                                        onClick={() => handleInstall(r.slug)}
                                    >
                                        {installing === r.slug ? t('enterprise.tools.installing') : t('enterprise.tools.install')}
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            )}

            {/* URL Import Modal */}
            {showUrlModal && (
                <div style={{
                    position: 'fixed', inset: 0, zIndex: 9999,
                    background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                }} onClick={() => setShowUrlModal(false)}>
                    <div style={{
                        background: 'var(--bg-primary)', borderRadius: '12px', width: '560px',
                        border: '1px solid var(--border-default)', boxShadow: '0 16px 48px rgba(0,0,0,0.2)',
                    }} onClick={e => e.stopPropagation()}>
                        <div style={{ padding: '20px 24px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                <h3 style={{ margin: 0, fontSize: '16px' }}>{t('enterprise.tools.importFromUrl')}</h3>
                                <button className="btn btn-ghost" onClick={() => setShowUrlModal(false)} style={{ padding: '4px 8px', fontSize: '16px', lineHeight: 1 }}>x</button>
                            </div>
                            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', margin: '0 0 12px' }}>
                                {t('enterprise.tools.pasteGithubUrl')}
                            </p>
                            <div style={{ display: 'flex', gap: '8px' }}>
                                <input
                                    className="input"
                                    placeholder={t('enterprise.tools.githubUrlPlaceholder')}
                                    value={urlInput}
                                    onChange={e => { setUrlInput(e.target.value); setUrlPreview(null); }}
                                    autoFocus
                                    style={{ flex: 1, fontSize: '13px', fontFamily: 'var(--font-mono)' }}
                                    onKeyDown={e => e.key === 'Enter' && handleUrlPreview()}
                                />
                                <button className="btn btn-secondary" onClick={handleUrlPreview} disabled={urlPreviewing || !urlInput.trim()} style={{ fontSize: '12px' }}>
                                    {urlPreviewing ? t('enterprise.tools.loading') : t('enterprise.tools.preview')}
                                </button>
                            </div>
                        </div>

                        {/* Preview result */}
                        {urlPreview && (
                            <div style={{ padding: '16px 24px' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                                    <span style={{ fontWeight: 600, fontSize: '14px' }}>{urlPreview.name}</span>
                                    {tierBadge(urlPreview.tier)}
                                    {urlPreview.has_scripts && (
                                        <span style={{ padding: '2px 8px', borderRadius: '4px', fontSize: '11px', background: 'rgba(255,59,48,0.1)', color: 'var(--error, #ff3b30)' }}>
                                            Contains scripts
                                        </span>
                                    )}
                                </div>
                                {urlPreview.description && (
                                    <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: '0 0 8px' }}>{urlPreview.description}</p>
                                )}
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                    {urlPreview.files?.length} files, {(urlPreview.total_size / 1024).toFixed(1)} KB
                                </div>
                                <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                                    <button className="btn btn-secondary" onClick={() => setShowUrlModal(false)} style={{ fontSize: '13px' }}>Cancel</button>
                                    <button className="btn btn-primary" onClick={handleUrlImport} disabled={urlImporting} style={{ fontSize: '13px' }}>
                                        {urlImporting ? 'Importing...' : 'Import'}
                                    </button>
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}




// ─── Company Identity Editor ───────────────────────
function CompanyLogoCropModal({ imageUrl, imageName, onCancel, onSave }: {
    imageUrl: string;
    imageName: string;
    onCancel: () => void;
    onSave: (blob: Blob) => void;
}) {
    const { t } = useTranslation();
    const imgRef = useRef<HTMLImageElement>(null);
    const [naturalSize, setNaturalSize] = useState({ width: 1, height: 1 });
    const [zoom, setZoom] = useState(1);
    const [offset, setOffset] = useState({ x: 0, y: 0 });
    const [dragStart, setDragStart] = useState<{ x: number; y: number; ox: number; oy: number } | null>(null);
    const cropSize = 240;

    const clampOffset = (next: { x: number; y: number }, nextZoom = zoom) => {
        const baseScale = Math.max(cropSize / naturalSize.width, cropSize / naturalSize.height);
        const displayW = naturalSize.width * baseScale * nextZoom;
        const displayH = naturalSize.height * baseScale * nextZoom;
        const maxX = Math.max(0, (displayW - cropSize) / 2);
        const maxY = Math.max(0, (displayH - cropSize) / 2);
        return {
            x: Math.min(maxX, Math.max(-maxX, next.x)),
            y: Math.min(maxY, Math.max(-maxY, next.y)),
        };
    };

    const handleSave = () => {
        const img = imgRef.current;
        if (!img) return;
        const outputSize = 512;
        const ratio = outputSize / cropSize;
        const baseScale = Math.max(cropSize / naturalSize.width, cropSize / naturalSize.height);
        const displayW = naturalSize.width * baseScale * zoom;
        const displayH = naturalSize.height * baseScale * zoom;
        const dx = ((cropSize - displayW) / 2 + offset.x) * ratio;
        const dy = ((cropSize - displayH) / 2 + offset.y) * ratio;
        const canvas = document.createElement('canvas');
        canvas.width = outputSize;
        canvas.height = outputSize;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, outputSize, outputSize);
        ctx.drawImage(img, dx, dy, displayW * ratio, displayH * ratio);
        canvas.toBlob((blob) => {
            if (blob) onSave(blob);
        }, 'image/png');
    };

    return (
        <div className="tenant-logo-crop-backdrop" onClick={onCancel}>
            <div className="tenant-logo-crop-modal" onClick={e => e.stopPropagation()}>
                <div className="tenant-logo-crop-header">
                    <div>
                        <h3>{t('enterprise.logo.cropTitle', 'Crop company logo')}</h3>
                        <p>{imageName}</p>
                    </div>
                    <button type="button" onClick={onCancel}>×</button>
                </div>
                <div
                    className="tenant-logo-crop-stage"
                    onPointerDown={e => {
                        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
                        setDragStart({ x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y });
                    }}
                    onPointerMove={e => {
                        if (!dragStart) return;
                        setOffset(clampOffset({
                            x: dragStart.ox + e.clientX - dragStart.x,
                            y: dragStart.oy + e.clientY - dragStart.y,
                        }));
                    }}
                    onPointerUp={() => setDragStart(null)}
                    onPointerCancel={() => setDragStart(null)}
                >
                    <img
                        ref={imgRef}
                        src={imageUrl}
                        alt=""
                        draggable={false}
                        onLoad={e => {
                            const img = e.currentTarget;
                            setNaturalSize({ width: img.naturalWidth || 1, height: img.naturalHeight || 1 });
                            setOffset({ x: 0, y: 0 });
                            setZoom(1);
                        }}
                        style={{
                            width: `${naturalSize.width * Math.max(cropSize / naturalSize.width, cropSize / naturalSize.height)}px`,
                            height: `${naturalSize.height * Math.max(cropSize / naturalSize.width, cropSize / naturalSize.height)}px`,
                            transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})`,
                        }}
                    />
                </div>
                <div className="tenant-logo-crop-controls">
                    <span>{t('enterprise.logo.zoom', 'Zoom')}</span>
                    <input
                        type="range"
                        min="1"
                        max="3"
                        step="0.01"
                        value={zoom}
                        onChange={e => {
                            const nextZoom = Number(e.target.value);
                            setZoom(nextZoom);
                            setOffset(prev => clampOffset(prev, nextZoom));
                        }}
                    />
                </div>
                <div className="tenant-logo-crop-actions">
                    <button className="btn btn-secondary" type="button" onClick={onCancel}>{t('common.cancel', 'Cancel')}</button>
                    <button className="btn btn-primary" type="button" onClick={handleSave}>{t('common.save', 'Save')}</button>
                </div>
            </div>
        </div>
    );
}

function CompanyLogoEditor() {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const tenantId = localStorage.getItem('current_tenant_id') || '';
    const [name, setName] = useState('');
    const [logoUrl, setLogoUrl] = useState('');
    const [logoError, setLogoError] = useState('');
    const [logoSaving, setLogoSaving] = useState(false);
    const [cropSource, setCropSource] = useState<{ url: string; name: string } | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (!tenantId) return;
        fetchJson<any>(`/tenants/${tenantId}`)
            .then(d => {
                if (d?.name) setName(d.name);
                setLogoUrl(d?.logo_url || '');
            })
            .catch(() => { });
    }, [tenantId]);

    const handleLogoFile = (file: File | undefined) => {
        setLogoError('');
        if (!file) return;
        if (file.size > 1024 * 1024) {
            setLogoError(t('enterprise.logo.tooLarge', 'Logo image must be 1 MB or smaller.'));
            return;
        }
        if (!file.type.startsWith('image/')) {
            setLogoError(t('enterprise.logo.invalidType', 'Please choose an image file.'));
            return;
        }
        setCropSource({ url: URL.createObjectURL(file), name: file.name });
    };

    const uploadCroppedLogo = async (blob: Blob) => {
        if (!tenantId) return;
        setLogoError('');
        if (blob.size > 1024 * 1024) {
            setLogoError(t('enterprise.logo.croppedTooLarge', 'Cropped logo is still larger than 1 MB.'));
            return;
        }
        setLogoSaving(true);
        try {
            const form = new FormData();
            form.append('file', blob, 'company-logo.png');
            const res = await fetch(`/api/tenants/${tenantId}/logo`, {
                method: 'POST',
                headers: { Authorization: `Bearer ${localStorage.getItem('token') || ''}` },
                body: form,
            });
            if (!res.ok) {
                throw new Error(t('enterprise.logo.uploadFailed', 'Failed to upload logo.'));
            }
            const tenant = await res.json();
            setLogoUrl(tenant.logo_url || '');
            setCropSource(null);
            qc.invalidateQueries({ queryKey: ['tenant', tenantId] });
            qc.invalidateQueries({ queryKey: ['my-tenants'] });
        } catch (e: any) {
            setLogoError(e.message || t('enterprise.logo.uploadFailed', 'Failed to upload logo.'));
        } finally {
            setLogoSaving(false);
        }
    };

    const resetLogo = async () => {
        if (!tenantId || !logoUrl) return;
        setLogoError('');
        setLogoSaving(true);
        try {
            const res = await fetch(`/api/tenants/${tenantId}/logo`, {
                method: 'DELETE',
                headers: { Authorization: `Bearer ${localStorage.getItem('token') || ''}` },
            });
            if (!res.ok) {
                throw new Error(t('enterprise.logo.resetFailed', 'Failed to reset logo.'));
            }
            setLogoUrl('');
            qc.invalidateQueries({ queryKey: ['tenant', tenantId] });
            qc.invalidateQueries({ queryKey: ['my-tenants'] });
        } catch (e: any) {
            setLogoError(e.message || t('enterprise.logo.resetFailed', 'Failed to reset logo.'));
        } finally {
            setLogoSaving(false);
        }
    };

    return (
        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
            <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '4px' }}>
                {t('enterprise.logo.title', 'Company Logo')}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '14px' }}>
                {t('enterprise.logo.description', 'Used in the sidebar workspace switcher and company selection menus.')}
            </div>
            <div className="company-identity-logo-row">
                <div className="company-identity-logo-preview">
                    {logoUrl ? <img src={logoUrl} alt="" /> : <span>{(Array.from(name.trim())[0] as string | undefined)?.toUpperCase() || 'C'}</span>}
                </div>
                <div>
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        style={{ display: 'none' }}
                        onChange={e => {
                            handleLogoFile(e.target.files?.[0]);
                            e.currentTarget.value = '';
                        }}
                    />
                    <button className="btn btn-secondary" type="button" onClick={() => fileInputRef.current?.click()} disabled={logoSaving}>
                        {logoSaving ? t('common.loading') : t('enterprise.logo.upload', 'Upload logo')}
                    </button>
                    {logoUrl && (
                        <button
                            className="btn btn-ghost"
                            type="button"
                            onClick={resetLogo}
                            disabled={logoSaving}
                            style={{ marginLeft: '8px' }}
                        >
                            {t('enterprise.logo.reset', 'Reset to default')}
                        </button>
                    )}
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '6px' }}>
                        {t('enterprise.logo.hint', 'PNG, JPG, or WebP. Max 1 MB. You will crop it to a square before saving.')}
                    </div>
                    {logoError && <div style={{ fontSize: '12px', color: 'var(--error)', marginTop: '6px' }}>{logoError}</div>}
                </div>
            </div>
            {cropSource && (
                <CompanyLogoCropModal
                    imageUrl={cropSource.url}
                    imageName={cropSource.name}
                    onCancel={() => setCropSource(null)}
                    onSave={uploadCroppedLogo}
                />
            )}
        </div>
    );
}

function CompanyNameEditor() {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const tenantId = localStorage.getItem('current_tenant_id') || '';
    const [name, setName] = useState('');
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        if (!tenantId) return;
        fetchJson<any>(`/tenants/${tenantId}`)
            .then(d => { if (d?.name) setName(d.name); })
            .catch(() => { });
    }, [tenantId]);

    const handleSave = async () => {
        if (!tenantId || !name.trim()) return;
        setSaving(true);
        try {
            await fetchJson(`/tenants/${tenantId}`, {
                method: 'PUT', body: JSON.stringify({ name: name.trim() }),
            });
            qc.invalidateQueries({ queryKey: ['tenants'] });
            qc.invalidateQueries({ queryKey: ['tenant', tenantId] });
            qc.invalidateQueries({ queryKey: ['my-tenants'] });
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        } catch (e) { }
        setSaving(false);
    };

    return (
        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
            <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '10px' }}>
                {t('enterprise.companyName.title', 'Company Name')}
            </div>
            <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                <input
                    className="form-input"
                    value={name}
                    onChange={e => setName(e.target.value)}
                    placeholder={t('enterprise.companyName.placeholder', 'Enter company name')}
                    style={{ flex: 1, fontSize: '14px' }}
                    onKeyDown={e => e.key === 'Enter' && handleSave()}
                />
                <button className="btn btn-primary" onClick={handleSave} disabled={saving || !name.trim()}>
                    {saving ? t('common.loading') : t('common.save', 'Save')}
                </button>
                {saved && <IconCheck size={15} stroke={2} style={{ color: 'var(--success)' }} />}
            </div>
        </div>
    );
}


function CompanyTimezoneEditor() {
    const { t, i18n } = useTranslation();
    const user = useAuthStore((s) => s.user);
    const tenantId = user?.tenant_id || localStorage.getItem('current_tenant_id') || '';
    const regionPickerRef = useRef<HTMLDivElement>(null);
    const [timezone, setTimezone] = useState('UTC');
    const [countryRegion, setCountryRegion] = useState('001');
    const [regionInput, setRegionInput] = useState('');
    const [regionOpen, setRegionOpen] = useState(false);
    const [highlightedRegion, setHighlightedRegion] = useState(0);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [error, setError] = useState('');
    const zh = i18n.language?.startsWith('zh');
    const companyRegions = useMemo(() => buildCompanyRegions(zh ? 'zh-Hans' : 'en'), [zh]);
    const regionLabel = (r: CompanyRegion) => zh ? r.zh : r.en;
    const selectedRegion = companyRegions.find(r => r.code === countryRegion) || companyRegions[0];
    const filteredRegions = useMemo(() => {
        const query = regionInput.trim().toLowerCase();
        if (!query || (!regionOpen && regionInput === regionLabel(selectedRegion))) return companyRegions;
        return companyRegions.filter(r => {
            const localName = regionLabel(r).toLowerCase();
            const altName = (zh ? r.en : r.zh).toLowerCase();
            return localName.includes(query)
                || altName.includes(query)
                || r.code.toLowerCase().includes(query)
                || r.timezone.toLowerCase().includes(query);
        });
    }, [companyRegions, regionInput, regionOpen, selectedRegion, zh]);

    useEffect(() => {
        setRegionInput(regionLabel(selectedRegion));
    }, [countryRegion, zh]);

    useEffect(() => {
        if (!regionOpen) return;
        const handlePointerDown = (e: MouseEvent) => {
            if (!regionPickerRef.current?.contains(e.target as Node)) {
                setRegionOpen(false);
                setRegionInput(regionLabel(selectedRegion));
                setHighlightedRegion(0);
            }
        };
        document.addEventListener('mousedown', handlePointerDown);
        return () => document.removeEventListener('mousedown', handlePointerDown);
    }, [regionOpen, selectedRegion, zh]);

    useEffect(() => {
        setHighlightedRegion(0);
    }, [regionInput]);

    useEffect(() => {
        if (!tenantId) return;
        fetchJson<any>(`/tenants/${tenantId}`)
            .then(d => {
                if (d?.timezone) setTimezone(d.timezone);
                if (d?.country_region) setCountryRegion(d.country_region);
            })
            .catch((e: any) => setError(e.message || 'Failed to load timezone'));
    }, [tenantId]);

    const handleSave = async (regionCode: string) => {
        if (!tenantId) return;
        const region = companyRegions.find(r => r.code === regionCode) || companyRegions[0];
        setCountryRegion(region.code);
        setTimezone(region.timezone);
        setSaving(true);
        setError('');
        try {
            await fetchJson(`/tenants/${tenantId}`, {
                method: 'PUT',
                body: JSON.stringify({
                    country_region: region.code,
                    timezone: region.timezone,
                }),
            });
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        } catch (e: any) {
            setError(e.message || 'Failed to save timezone');
        }
        setSaving(false);
    };

    const selectRegion = (region: CompanyRegion) => {
        setRegionInput(regionLabel(region));
        setRegionOpen(false);
        setHighlightedRegion(0);
        if (region.code !== countryRegion) {
            handleSave(region.code);
        }
    };

    return (
        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
            <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '4px' }}>
                {zh ? '公司所在国家或地区' : 'Company Country or Region'}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                {zh
                    ? `用于自动设置公司默认时区和 OKR 休息日规则。当前时区：${timezone}`
                    : `Used to set the company timezone and OKR non-workday rules. Current timezone: ${timezone}`}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: '10px', width: '100%' }}>
                <div ref={regionPickerRef} style={{ position: 'relative', width: 'min(420px, 100%)' }}>
                    <input
                        className="form-input"
                        value={regionInput}
                        onChange={e => {
                            setRegionInput(e.target.value);
                            setRegionOpen(true);
                        }}
                        onFocus={() => {
                            setRegionOpen(true);
                            setRegionInput('');
                        }}
                        onKeyDown={e => {
                            if (e.key === 'ArrowDown') {
                                e.preventDefault();
                                setRegionOpen(true);
                                setHighlightedRegion(i => Math.min(i + 1, Math.max(filteredRegions.length - 1, 0)));
                            } else if (e.key === 'ArrowUp') {
                                e.preventDefault();
                                setHighlightedRegion(i => Math.max(i - 1, 0));
                            } else if (e.key === 'Enter') {
                                e.preventDefault();
                                const region = filteredRegions[highlightedRegion];
                                if (region) selectRegion(region);
                            } else if (e.key === 'Escape') {
                                setRegionOpen(false);
                                setRegionInput(regionLabel(selectedRegion));
                            }
                        }}
                        placeholder={zh ? '搜索国家或地区、代码或时区' : 'Search country, code, or timezone'}
                        style={{
                            width: '100%',
                            fontSize: '13px',
                            paddingRight: '42px',
                            cursor: saving || !tenantId ? 'not-allowed' : 'text',
                        }}
                        disabled={saving || !tenantId}
                        role="combobox"
                        aria-expanded={regionOpen}
                        aria-controls="company-region-listbox"
                        aria-autocomplete="list"
                    />
                    <button
                        type="button"
                        onClick={() => {
                            if (saving || !tenantId) return;
                            setRegionOpen(v => !v);
                            if (!regionOpen) setRegionInput('');
                        }}
                        disabled={saving || !tenantId}
                        aria-label={regionOpen ? (zh ? '收起地区列表' : 'Collapse region list') : (zh ? '展开地区列表' : 'Expand region list')}
                        style={{
                            position: 'absolute',
                            right: '7px',
                            top: '50%',
                            transform: `translateY(-50%) rotate(${regionOpen ? 180 : 0}deg)`,
                            border: 'none',
                            background: 'transparent',
                            color: 'var(--text-secondary)',
                            cursor: saving || !tenantId ? 'not-allowed' : 'pointer',
                            width: '30px',
                            height: '30px',
                            padding: 0,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            transition: 'transform 120ms ease',
                        }}
                    >
                        <span
                            aria-hidden="true"
                            style={{
                                width: '8px',
                                height: '8px',
                                borderRight: '1.6px solid currentColor',
                                borderBottom: '1.6px solid currentColor',
                                transform: 'rotate(45deg) translateY(-2px)',
                                borderRadius: '1px',
                            }}
                        />
                    </button>
                    {regionOpen && (
                        <div
                            id="company-region-listbox"
                            role="listbox"
                            style={{
                                position: 'absolute',
                                top: 'calc(100% + 6px)',
                                left: 0,
                                right: 0,
                                zIndex: 30,
                                background: 'var(--bg-primary)',
                                border: '1px solid var(--border-subtle)',
                                borderRadius: '8px',
                                boxShadow: '0 12px 28px rgba(15, 23, 42, 0.14)',
                                maxHeight: '260px',
                                overflowY: 'auto',
                                padding: '6px',
                            }}
                        >
                            {filteredRegions.length > 0 ? filteredRegions.map((region, index) => {
                                const active = region.code === countryRegion;
                                const highlighted = index === highlightedRegion;
                                return (
                                    <button
                                        key={region.code}
                                        type="button"
                                        role="option"
                                        aria-selected={active}
                                        onMouseEnter={() => setHighlightedRegion(index)}
                                        onMouseDown={e => e.preventDefault()}
                                        onClick={() => selectRegion(region)}
                                        style={{
                                            width: '100%',
                                            border: 'none',
                                            background: highlighted ? 'var(--bg-elevated)' : 'transparent',
                                            color: 'var(--text-primary)',
                                            borderRadius: '6px',
                                            padding: '9px 10px',
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'space-between',
                                            gap: '12px',
                                            textAlign: 'left',
                                            cursor: 'pointer',
                                        }}
                                    >
                                        <span style={{ minWidth: 0 }}>
                                            <span style={{ display: 'block', fontSize: '13px', fontWeight: active ? 600 : 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                {regionLabel(region)}
                                            </span>
                                            <span style={{ display: 'block', marginTop: '2px', color: 'var(--text-tertiary)', fontSize: '11px' }}>
                                                {region.code} · {region.timezone}
                                            </span>
                                        </span>
                                        {active && <span style={{ color: 'var(--text-primary)', fontSize: '14px', flexShrink: 0 }}>✓</span>}
                                    </button>
                                );
                            }) : (
                                <div style={{ padding: '12px 10px', color: 'var(--text-tertiary)', fontSize: '12px' }}>
                                    {zh ? '没有匹配的国家或地区' : 'No matching country or region'}
                                </div>
                            )}
                        </div>
                    )}
                </div>
                {(saved || error || !tenantId) && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minHeight: '16px', flexWrap: 'wrap' }}>
                        {saved && <span style={{ color: 'var(--success)', fontSize: '12px' }}>已保存</span>}
                        {error && (
                            <div style={{ fontSize: '11px', color: 'var(--error)' }}>
                                {error}
                            </div>
                        )}
                        {!tenantId && (
                            <div style={{ fontSize: '11px', color: 'var(--error)' }}>
                                {t('enterprise.timezone.noTenant', 'No company selected. Please refresh the page or contact support.')}
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}


function A2AAsyncToggle() {
    const { t, i18n } = useTranslation();
    const user = useAuthStore((s) => s.user);
    const tenantId = user?.tenant_id || localStorage.getItem('current_tenant_id') || '';
    const [enabled, setEnabled] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const zh = i18n.language?.startsWith('zh');

    useEffect(() => {
        if (!tenantId) return;
        fetchJson<any>(`/tenants/${tenantId}`)
            .then(d => setEnabled(!!d?.a2a_async_enabled))
            .catch((e: any) => setError(e.message || 'Failed to load A2A setting'));
    }, [tenantId]);

    const handleToggle = async () => {
        if (!tenantId || saving) return;
        const next = !enabled;
        setEnabled(next);
        setSaving(true);
        setError('');
        try {
            await fetchJson(`/tenants/${tenantId}`, {
                method: 'PUT',
                body: JSON.stringify({ a2a_async_enabled: next }),
            });
        } catch (e: any) {
            setEnabled(!next);
            setError(e.message || 'Failed to save A2A setting');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
            <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '4px' }}>
                {zh ? 'Agent 异步协作（Beta）' : 'Agent Async Collaboration (Beta)'}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                {zh
                    ? '开启后，数字员工之间可使用 notify / task_delegate 等异步协作模式。关闭后，Agent 间消息统一走同步 consult。'
                    : 'When enabled, agents can use async notify and task_delegate modes. When disabled, agent-to-agent messaging falls back to synchronous consult.'}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: '12px' }}>
                <div style={{ width: '100%' }}>
                    {error && (
                        <div style={{ fontSize: '11px', color: 'var(--error)' }}>
                            {error}
                        </div>
                    )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', flexShrink: 0, opacity: saving ? 0.6 : 1 }}>
                        <input
                            type="checkbox"
                            checked={enabled}
                            onChange={handleToggle}
                            disabled={saving || !tenantId}
                            style={{ opacity: 0, width: 0, height: 0 }}
                        />
                        <span style={{
                            position: 'absolute', inset: 0,
                            borderRadius: '999px',
                            cursor: saving ? 'not-allowed' : 'pointer',
                            background: enabled ? 'var(--accent-primary)' : 'var(--border-subtle)',
                            transition: '0.2s',
                        }}>
                            <span style={{
                                position: 'absolute',
                                top: '2px',
                                left: enabled ? '20px' : '2px',
                                width: '18px',
                                height: '18px',
                                borderRadius: '50%',
                                background: '#fff',
                                transition: '0.2s',
                                boxShadow: '0 1px 2px rgba(0,0,0,0.12)',
                            }} />
                        </span>
                    </label>
                    <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {enabled ? (zh ? '已开启' : 'Enabled') : (zh ? '已关闭' : 'Disabled')}
                    </span>
                </div>
            </div>
            <div style={{ marginTop: '6px', fontSize: '11px', color: 'var(--text-tertiary)', maxWidth: '640px' }}>
                {zh
                    ? '说明：OKR 日报收集本身会优先使用更稳的同步方式，不依赖这里的异步开关。'
                    : 'Note: OKR daily collection itself uses the more reliable synchronous path and does not depend on this toggle.'}
            </div>
        </div>
    );
}


// ── Broadcast Section ──────────────────────────
function BroadcastSection() {
    const { t } = useTranslation();
    const toast = useToast();
    const [title, setTitle] = useState('');
    const [body, setBody] = useState('');
    const [sendEmail, setSendEmail] = useState(false);
    const [sending, setSending] = useState(false);
    const [result, setResult] = useState<{ users: number; agents: number; emails: number } | null>(null);

    const handleSend = async () => {
        if (!title.trim()) return;
        setSending(true);
        setResult(null);
        try {
            const token = localStorage.getItem('token');
            const res = await fetch('/api/notifications/broadcast', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify({ title: title.trim(), body: body.trim(), send_email: sendEmail }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                toast.error('广播发送失败', { details: String(err.detail || `HTTP ${res.status}`) });
                setSending(false);
                return;
            }
            const data = await res.json();
            setResult({
                users: data.users_notified,
                agents: data.agents_notified,
                emails: data.emails_sent || 0,
            });
            setTitle('');
            setBody('');
            setSendEmail(false);
        } catch (e: any) {
            toast.error('广播发送失败', { details: String(e?.message || e) });
        }
        setSending(false);
    };

    return (
        <div style={{ marginTop: '24px', marginBottom: '24px' }}>
            <h3 style={{ marginBottom: '4px' }}>{t('enterprise.broadcast.title', 'Broadcast Notification')}</h3>
            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                {t('enterprise.broadcast.description', 'Send a notification to all users and agents in this company.')}
            </p>
            <div className="card" style={{ padding: '16px' }}>
                <input
                    className="form-input"
                    placeholder={t('enterprise.broadcast.titlePlaceholder', 'Notification title')}
                    value={title}
                    onChange={e => setTitle(e.target.value)}
                    maxLength={200}
                    style={{ marginBottom: '8px', fontSize: '13px' }}
                />
                <textarea
                    className="form-input"
                    placeholder={t('enterprise.broadcast.bodyPlaceholder', 'Optional details...')}
                    value={body}
                    onChange={e => setBody(e.target.value)}
                    maxLength={1000}
                    rows={3}
                    style={{ resize: 'vertical', fontSize: '13px', marginBottom: '12px' }}
                />
                <label style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px', fontSize: '13px' }}>
                    <input
                        type="checkbox"
                        checked={sendEmail}
                        onChange={e => setSendEmail(e.target.checked)}
                    />
                    <span>{t('enterprise.broadcast.sendEmail', 'Also send email to users with a configured address')}</span>
                </label>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <button className="btn btn-primary" onClick={handleSend} disabled={sending || !title.trim()}>
                        {sending ? t('common.loading') : t('enterprise.broadcast.send', 'Send Broadcast')}
                    </button>
                    {result && (
                        <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            {t(
                                'enterprise.broadcast.sentWithEmail',
                                `Sent to ${result.users} users, ${result.agents} agents, and ${result.emails} email recipients`,
                                { users: result.users, agents: result.agents, emails: result.emails },
                            )}
                        </span>
                    )}
                </div>
            </div>
        </div>
    );
}
