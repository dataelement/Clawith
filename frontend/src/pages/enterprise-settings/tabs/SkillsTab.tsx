import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import PromptModal from '../../../components/PromptModal';
import FileBrowser from '../../../components/FileBrowser';
import type { FileBrowserApi } from '../../../components/FileBrowser';
import SkillFolderUploadModal from '../../../components/skills/SkillFolderUploadModal';
import SkillsActionBar, { type SkillsActionBarAction } from '../../../components/skills/SkillsActionBar';
import { createEnterpriseSkillUploadAdapter } from '../../../components/skills/skillUploadSurfaceAdapters';
import { getEnterpriseSkillActionIds, type SkillActionId } from '../../../components/skills/skillsActionItems';
import { skillApi } from '../../../services/api';

// ─── Skills Tab ────────────────────────────────────
export default function SkillsTab() {
    const { t } = useTranslation();
    const [refreshKey, setRefreshKey] = useState(0);
    const [showClawhubModal, setShowClawhubModal] = useState(false);
    const [showUrlModal, setShowUrlModal] = useState(false);
    const [showSkillFolderUploadModal, setShowSkillFolderUploadModal] = useState(false);
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

    const uploadAdapter = useMemo(() => createEnterpriseSkillUploadAdapter({
        preview: (file, targetFolder) => skillApi.folderUpload.preview(file, targetFolder),
        apply: (input) => skillApi.folderUpload.apply(input),
        refresh: () => setRefreshKey(k => k + 1),
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

    const handleSettingsClick = async () => {
        setShowSettings(s => !s);
        if (!tokenStatus) {
            try {
                const status = await skillApi.settings.getToken();
                setTokenStatus(status);
            } catch {
                // ignore
            }
        }
    };

    const actionConfig: Record<SkillActionId, SkillsActionBarAction | undefined> = {
        settings: {
            id: 'settings',
            label: t('common.settings', 'Settings'),
            title: t('common.settings', 'Settings'),
            onClick: handleSettingsClick,
        },
        'upload-folder': {
            id: 'upload-folder',
            label: t('enterprise.tools.uploadFolderModal.openButton'),
            onClick: () => setShowSkillFolderUploadModal(true),
        },
        'import-url': {
            id: 'import-url',
            label: t('enterprise.tools.importFromUrl'),
            onClick: () => {
                setShowUrlModal(true);
                setUrlInput('');
                setUrlPreview(null);
            },
        },
        'browse-clawhub': {
            id: 'browse-clawhub',
            label: t('enterprise.tools.browseClawhub'),
            variant: 'primary',
            onClick: () => {
                setShowClawhubModal(true);
                setSearchQuery('');
                setSearchResults([]);
                setHasSearched(false);
            },
        },
        'import-presets': undefined,
    };

    const enterpriseActions = getEnterpriseSkillActionIds()
        .map((id) => actionConfig[id])
        .filter((action): action is SkillsActionBarAction => Boolean(action));

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
            <SkillsActionBar
                title={t('enterprise.tabs.skills', 'Skill Registry')}
                description={t('enterprise.tools.manageGlobalSkills')}
                actions={enterpriseActions}
            />

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

            <SkillFolderUploadModal
                open={showSkillFolderUploadModal}
                onClose={() => setShowSkillFolderUploadModal(false)}
                i18nPrefix="enterprise.tools.uploadFolderModal"
                previewRequest={uploadAdapter.previewRequest}
                applyRequest={uploadAdapter.applyRequest}
                onApplied={uploadAdapter.onApplied}
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
