/**
 * AgentCredentials — Credential management component for Agent Settings.
 *
 * Allows managing stored login credentials (username/password) and
 * browser cookies for specific platforms. Cookies are automatically
 * injected into new AgentBay browser sessions via CDP.
 *
 * Linear-style design with card-based credential list and modal editor.
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { credentialApi } from '../services/api';

/* ── Types ── */
interface Credential {
    id: string;
    agent_id: string;
    credential_type: string;
    platform: string;
    display_name: string;
    username: string | null;
    login_url: string | null;
    status: string;
    cookies_updated_at: string | null;
    last_login_at: string | null;
    last_injected_at: string | null;
    has_cookies: boolean;
    has_password: boolean;
    created_at: string;
    updated_at: string;
}

interface FormData {
    credential_type: string;
    platform: string;
    display_name: string;
    username: string;
    password: string;
    login_url: string;
    cookies_json: string;
}

const EMPTY_FORM: FormData = {
    credential_type: 'website',
    platform: '',
    display_name: '',
    username: '',
    password: '',
    login_url: '',
    cookies_json: '',
};

/* ── Icons ── */
const PlusIcon = (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
        <path d="M8 3v10M3 8h10" />
    </svg>
);

const KeyIcon = (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M10.5 2a3.5 3.5 0 0 0-3.23 4.84L2 12.11V14h1.89l.53-.53V12.5H5.5v-.53l.53-.53H7v-1.06l.53-.53h.63A3.5 3.5 0 1 0 10.5 2z" />
        <circle cx="11" cy="5" r="0.8" fill="currentColor" stroke="none" />
    </svg>
);

const TrashIcon = (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 4.5h10M6 4.5V3a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v1.5M4.5 4.5l.5 9a1 1 0 0 0 1 1h4a1 1 0 0 0 1-1l.5-9" />
    </svg>
);

const EditIcon = (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M11 2.5a1.41 1.41 0 0 1 2 2L5.5 12 2 13l1-3.5z" />
    </svg>
);

const CloseIcon = (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
        <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
);

const CookieIcon = (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
        <circle cx="8" cy="8" r="6" />
        <circle cx="6" cy="6" r="1" fill="currentColor" stroke="none" />
        <circle cx="10" cy="7" r="0.8" fill="currentColor" stroke="none" />
        <circle cx="7" cy="10" r="0.8" fill="currentColor" stroke="none" />
    </svg>
);

/* ── Component ── */
interface Props {
    agentId: string;
}

export default function AgentCredentials({ agentId }: Props) {
    const { t } = useTranslation();
    const [credentials, setCredentials] = useState<Credential[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Modal state
    const [showModal, setShowModal] = useState(false);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [form, setForm] = useState<FormData>({ ...EMPTY_FORM });
    const [saving, setSaving] = useState(false);
    const [formError, setFormError] = useState('');

    // Delete confirmation
    const [deletingId, setDeletingId] = useState<string | null>(null);

    // Status badge styles - using translation keys
    const getStatusConfig = useCallback((status: string) => {
        const configs: Record<string, { bg: string; text: string; labelKey: string }> = {
            active: { bg: 'rgba(52, 199, 89, 0.12)', text: '#34c759', labelKey: 'agent.credentials.status.active' },
            expired: { bg: 'rgba(255, 149, 0, 0.12)', text: '#ff9500', labelKey: 'agent.credentials.status.expired' },
            needs_relogin: { bg: 'rgba(255, 59, 48, 0.12)', text: '#ff3b30', labelKey: 'agent.credentials.status.needs_relogin' },
        };
        return configs[status] || configs.active;
    }, []);

    // Relative time helper using translations
    const timeAgo = useCallback((dateStr: string | null): string => {
        if (!dateStr) return '';
        const diff = Date.now() - new Date(dateStr).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return t('agent.credentials.timeAgo.justNow');
        if (mins < 60) return t('agent.credentials.timeAgo.minutes', { count: mins });
        const hours = Math.floor(mins / 60);
        if (hours < 24) return t('agent.credentials.timeAgo.hours', { count: hours });
        const days = Math.floor(hours / 24);
        return t('agent.credentials.timeAgo.days', { count: days });
    }, [t]);

    const fetchCredentials = useCallback(async () => {
        try {
            setLoading(true);
            const data = await credentialApi.list(agentId);
            setCredentials(data);
        } catch (e: any) {
            setError(e.message || t('agent.credentials.error'));
        } finally {
            setLoading(false);
        }
    }, [agentId, t]);

    useEffect(() => {
        fetchCredentials();
    }, [fetchCredentials]);

    const handleAdd = () => {
        setEditingId(null);
        setForm({ ...EMPTY_FORM });
        setFormError('');
        setShowModal(true);
    };

    const handleEdit = (cred: Credential) => {
        setEditingId(cred.id);
        setForm({
            credential_type: cred.credential_type,
            platform: cred.platform,
            display_name: cred.display_name,
            username: cred.username || '',
            password: '', // Never pre-fill password
            login_url: cred.login_url || '',
            cookies_json: '', // Never pre-fill cookies
        });
        setFormError('');
        setShowModal(true);
    };

    const handleSave = async () => {
        if (!form.platform.trim()) {
            setFormError(t('agent.credentials.platformRequired'));
            return;
        }

        // Validate cookies JSON if provided
        if (form.cookies_json.trim()) {
            try {
                const parsed = JSON.parse(form.cookies_json);
                if (!Array.isArray(parsed)) {
                    setFormError(t('agent.credentials.cookiesInvalid'));
                    return;
                }
            } catch {
                setFormError(t('agent.credentials.cookiesJsonInvalid'));
                return;
            }
        }

        setSaving(true);
        setFormError('');

        try {
            // Build payload — only include non-empty fields for updates
            const payload: any = {
                credential_type: form.credential_type,
                platform: form.platform.trim(),
                display_name: form.display_name.trim(),
            };
            if (form.username.trim()) payload.username = form.username.trim();
            if (form.password) payload.password = form.password;
            if (form.login_url.trim()) payload.login_url = form.login_url.trim();
            if (form.cookies_json.trim()) payload.cookies_json = form.cookies_json.trim();

            if (editingId) {
                await credentialApi.update(agentId, editingId, payload);
            } else {
                await credentialApi.create(agentId, payload);
            }

            setShowModal(false);
            await fetchCredentials();
        } catch (e: any) {
            setFormError(e.message || t('agent.credentials.saveError'));
        } finally {
            setSaving(false);
        }
    };

    const handleDelete = async (id: string) => {
        try {
            await credentialApi.delete(agentId, id);
            setDeletingId(null);
            await fetchCredentials();
        } catch (e: any) {
            setError(e.message || t('agent.credentials.deleteError'));
        }
    };

    return (
        <div className="credentials-section">
            {/* Header */}
            <div className="credentials-header">
                <div className="credentials-title">
                    {KeyIcon}
                    <span>{t('agent.credentials.title')}</span>
                    <span className="credentials-count">{credentials.length}</span>
                </div>
                <button className="credentials-add-btn" onClick={handleAdd}>
                    {PlusIcon}
                    <span>{t('agent.credentials.add')}</span>
                </button>
            </div>

            {/* Description */}
            <p className="credentials-desc">
                {t('agent.credentials.description')}
            </p>

            {/* Error */}
            {error && <div className="credentials-error">{error}</div>}

            {/* Credential list */}
            {loading ? (
                <div className="credentials-loading">{t('agent.credentials.loading')}</div>
            ) : credentials.length === 0 ? (
                <div className="credentials-empty">
                    {KeyIcon}
                    <span>{t('agent.credentials.empty')}</span>
                </div>
            ) : (
                <div className="credentials-list">
                    {credentials.map((cred) => {
                        const statusConfig = getStatusConfig(cred.status);
                        return (
                            <div key={cred.id} className="credential-card">
                                <div className="credential-card-top">
                                    <div className="credential-platform">
                                        {cred.platform}
                                    </div>
                                    <span
                                        className="credential-status-badge"
                                        style={{ background: statusConfig.bg, color: statusConfig.text }}
                                    >
                                        {t(statusConfig.labelKey)}
                                    </span>
                                </div>
                                {cred.display_name && (
                                    <div className="credential-display-name">{cred.display_name}</div>
                                )}
                                <div className="credential-meta">
                                    {cred.has_cookies && (
                                        <span className="credential-meta-tag">
                                            {CookieIcon}
                                            {t('agent.credentials.meta.cookies')} {cred.cookies_updated_at ? `(${timeAgo(cred.cookies_updated_at)})` : ''}
                                        </span>
                                    )}
                                    {cred.username && (
                                        <span className="credential-meta-tag">
                                            {cred.username}
                                        </span>
                                    )}
                                    {cred.last_injected_at && (
                                        <span className="credential-meta-tag">
                                            {t('agent.credentials.meta.injected')} {timeAgo(cred.last_injected_at)}
                                        </span>
                                    )}
                                </div>
                                <div className="credential-actions">
                                    <button
                                        className="credential-action-btn"
                                        onClick={() => handleEdit(cred)}
                                        title={t('agent.credentials.actions.edit')}
                                    >
                                        {EditIcon}
                                    </button>
                                    <button
                                        className="credential-action-btn credential-action-danger"
                                        onClick={() => setDeletingId(cred.id)}
                                        title={t('agent.credentials.actions.delete')}
                                    >
                                        {TrashIcon}
                                    </button>
                                </div>

                                {/* Delete confirmation */}
                                {deletingId === cred.id && (
                                    <div className="credential-delete-confirm">
                                        <span>{t('agent.credentials.deleteConfirm.title', { platform: cred.platform })}</span>
                                        <div className="credential-delete-actions">
                                            <button onClick={() => setDeletingId(null)}>{t('agent.credentials.actions.cancel')}</button>
                                            <button
                                                className="credential-delete-yes"
                                                onClick={() => handleDelete(cred.id)}
                                            >
                                                {t('agent.credentials.deleteConfirm.confirm')}
                                            </button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}

            {/* Add/Edit Modal */}
            {showModal && (
                <div className="credential-modal-overlay" onClick={() => setShowModal(false)}>
                    <div className="credential-modal" onClick={(e) => e.stopPropagation()}>
                        <div className="credential-modal-header">
                            <h3>{editingId ? t('agent.credentials.modal.editTitle') : t('agent.credentials.modal.addTitle')}</h3>
                            <button className="credential-modal-close" onClick={() => setShowModal(false)}>
                                {CloseIcon}
                            </button>
                        </div>

                        <div className="credential-modal-body">
                            {formError && <div className="credential-form-error">{formError}</div>}

                            <label className="credential-field">
                                <span className="credential-field-label">{t('agent.credentials.modal.platform')} <span style={{ color: 'var(--error)' }}>*</span></span>
                                <input
                                    type="text"
                                    value={form.platform}
                                    onChange={(e) => setForm(f => ({ ...f, platform: e.target.value }))}
                                    placeholder={t('agent.credentials.modal.platformPlaceholder')}
                                    autoFocus
                                />
                            </label>

                            <label className="credential-field">
                                <span className="credential-field-label">{t('agent.credentials.modal.displayName')}</span>
                                <input
                                    type="text"
                                    value={form.display_name}
                                    onChange={(e) => setForm(f => ({ ...f, display_name: e.target.value }))}
                                    placeholder={t('agent.credentials.modal.displayNamePlaceholder')}
                                />
                            </label>

                            <label className="credential-field">
                                <span className="credential-field-label">{t('agent.credentials.modal.type')}</span>
                                <select
                                    value={form.credential_type}
                                    onChange={(e) => setForm(f => ({ ...f, credential_type: e.target.value }))}
                                >
                                    <option value="website">{t('agent.credentials.modal.typeOptions.website')}</option>
                                    <option value="email">{t('agent.credentials.modal.typeOptions.email')}</option>
                                    <option value="social">{t('agent.credentials.modal.typeOptions.social')}</option>
                                    <option value="api_key">{t('agent.credentials.modal.typeOptions.api_key')}</option>
                                </select>
                            </label>

                            <label className="credential-field">
                                <span className="credential-field-label">{t('agent.credentials.modal.username')}</span>
                                <input
                                    type="text"
                                    value={form.username}
                                    onChange={(e) => setForm(f => ({ ...f, username: e.target.value }))}
                                    placeholder={t('agent.credentials.modal.usernamePlaceholder')}
                                />
                            </label>

                            <label className="credential-field">
                                <span className="credential-field-label">
                                    {t('agent.credentials.modal.password')}
                                    {editingId && <span className="credential-field-hint">{t('agent.credentials.modal.passwordHint')}</span>}
                                </span>
                                <input
                                    type="password"
                                    value={form.password}
                                    onChange={(e) => setForm(f => ({ ...f, password: e.target.value }))}
                                    placeholder={t('agent.credentials.modal.passwordPlaceholder')}
                                    autoComplete="new-password"
                                />
                            </label>

                            <label className="credential-field">
                                <span className="credential-field-label">{t('agent.credentials.modal.loginUrl')}</span>
                                <input
                                    type="text"
                                    value={form.login_url}
                                    onChange={(e) => setForm(f => ({ ...f, login_url: e.target.value }))}
                                    placeholder={t('agent.credentials.modal.loginUrlPlaceholder')}
                                />
                            </label>

                            <label className="credential-field">
                                <span className="credential-field-label">
                                    {t('agent.credentials.modal.cookies')}
                                    {editingId && <span className="credential-field-hint">{t('agent.credentials.modal.cookiesHint')}</span>}
                                </span>
                                <textarea
                                    value={form.cookies_json}
                                    onChange={(e) => setForm(f => ({ ...f, cookies_json: e.target.value }))}
                                    placeholder={t('agent.credentials.modal.cookiesPlaceholder')}
                                    rows={6}
                                />
                                <span className="credential-field-help">
                                    {t('agent.credentials.modal.cookiesHelp')}{' '}
                                    <a href="https://cookie-editor.com" target="_blank" rel="noopener noreferrer">
                                        {t('agent.credentials.modal.cookiesHelpLink')}
                                    </a>
                                    {' '}{t('agent.credentials.modal.cookiesHelpSuffix')}
                                </span>
                            </label>
                        </div>

                        <div className="credential-modal-footer">
                            <button className="credential-btn-cancel" onClick={() => setShowModal(false)}>
                                {t('agent.credentials.actions.cancel')}
                            </button>
                            <button
                                className="credential-btn-save"
                                onClick={handleSave}
                                disabled={saving}
                            >
                                {saving ? t('agent.credentials.actions.saving') : editingId ? t('agent.credentials.actions.update') : t('agent.credentials.actions.create')}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Inline styles for the component */}
            <style>{credentialStyles}</style>
        </div>
    );
}

/* ── Styles ── */
const credentialStyles = `
.credentials-section {
    margin-top: 12px;
    padding: 20px;
    background: var(--bg-secondary);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
}

.credentials-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
}

.credentials-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
}

.credentials-count {
    font-size: 11px;
    font-weight: 500;
    color: var(--text-tertiary);
    background: var(--bg-tertiary);
    padding: 1px 7px;
    border-radius: 10px;
}

.credentials-add-btn {
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 500;
    color: var(--text-secondary);
    background: transparent;
    border: 1px solid var(--border-default);
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.15s;
}
.credentials-add-btn:hover {
    background: var(--bg-tertiary);
    color: var(--text-primary);
    border-color: var(--border-default);
}

.credentials-desc {
    font-size: 12px;
    color: var(--text-tertiary);
    margin: 0 0 16px 0;
    line-height: 1.5;
}

.credentials-error {
    font-size: 12px;
    color: var(--error);
    margin-bottom: 12px;
}

.credentials-loading, .credentials-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 32px;
    font-size: 13px;
    color: var(--text-tertiary);
}

.credentials-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.credential-card {
    position: relative;
    padding: 12px 14px;
    background: var(--bg-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    transition: border-color 0.15s;
}
.credential-card:hover {
    border-color: var(--border-default);
}

.credential-card-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 4px;
}

.credential-platform {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
}

.credential-status-badge {
    font-size: 10px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
    letter-spacing: 0.3px;
    text-transform: uppercase;
}

.credential-display-name {
    font-size: 12px;
    color: var(--text-secondary);
    margin-bottom: 6px;
}

.credential-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 8px;
}

.credential-meta-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    color: var(--text-tertiary);
    background: var(--bg-tertiary);
    padding: 2px 8px;
    border-radius: 4px;
}

.credential-actions {
    display: flex;
    gap: 4px;
    justify-content: flex-end;
}

.credential-action-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: var(--text-tertiary);
    cursor: pointer;
    transition: all 0.15s;
}
.credential-action-btn:hover {
    background: var(--bg-tertiary);
    border-color: var(--border-subtle);
    color: var(--text-primary);
}
.credential-action-danger:hover {
    color: var(--error);
    background: rgba(255, 59, 48, 0.08);
}

.credential-delete-confirm {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    margin-top: 8px;
    background: rgba(255, 59, 48, 0.06);
    border: 1px solid rgba(255, 59, 48, 0.15);
    border-radius: 6px;
    font-size: 12px;
    color: var(--text-secondary);
}
.credential-delete-actions {
    display: flex;
    gap: 6px;
}
.credential-delete-actions button {
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 4px;
    border: 1px solid var(--border-default);
    background: transparent;
    color: var(--text-secondary);
    cursor: pointer;
}
.credential-delete-yes {
    background: var(--error) !important;
    color: #fff !important;
    border-color: var(--error) !important;
}

/* ── Modal ── */
.credential-modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(4px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
}

.credential-modal {
    width: 480px;
    max-height: 85vh;
    background: var(--bg-primary);
    border: 1px solid var(--border-default);
    border-radius: 12px;
    display: flex;
    flex-direction: column;
    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
}

.credential-modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px;
    border-bottom: 1px solid var(--border-subtle);
}
.credential-modal-header h3 {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--text-primary);
}
.credential-modal-close {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    background: transparent;
    border: none;
    border-radius: 6px;
    color: var(--text-tertiary);
    cursor: pointer;
}
.credential-modal-close:hover {
    background: var(--bg-tertiary);
}

.credential-modal-body {
    padding: 16px 20px;
    overflow-y: auto;
    flex: 1;
}

.credential-form-error {
    font-size: 12px;
    color: var(--error);
    background: rgba(255, 59, 48, 0.08);
    padding: 8px 12px;
    border-radius: 6px;
    margin-bottom: 12px;
}

.credential-field {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-bottom: 14px;
}
.credential-field-label {
    font-size: 12px;
    font-weight: 500;
    color: var(--text-secondary);
}
.credential-field-hint {
    font-weight: 400;
    font-size: 11px;
    color: var(--text-tertiary);
    margin-left: 6px;
}
.credential-field input,
.credential-field select,
.credential-field textarea {
    padding: 8px 10px;
    font-size: 13px;
    color: var(--text-primary);
    background: var(--bg-secondary);
    border: 1px solid var(--border-default);
    border-radius: 6px;
    outline: none;
    font-family: inherit;
    transition: border-color 0.15s;
}
.credential-field input:focus,
.credential-field select:focus,
.credential-field textarea:focus {
    border-color: var(--accent-primary);
    box-shadow: 0 0 0 3px var(--accent-subtle);
}
.credential-field textarea {
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 12px;
    resize: vertical;
    min-height: 80px;
}
.credential-field-help {
    font-size: 11px;
    color: var(--text-tertiary);
    line-height: 1.4;
}
.credential-field-help a {
    color: var(--accent-text);
    text-decoration: none;
}
.credential-field-help a:hover {
    text-decoration: underline;
}

.credential-modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 20px;
    border-top: 1px solid var(--border-subtle);
}
.credential-btn-cancel {
    padding: 7px 16px;
    font-size: 13px;
    color: var(--text-secondary);
    background: transparent;
    border: 1px solid var(--border-default);
    border-radius: 6px;
    cursor: pointer;
}
.credential-btn-cancel:hover {
    background: var(--bg-tertiary);
}
.credential-btn-save {
    padding: 7px 20px;
    font-size: 13px;
    font-weight: 600;
    color: #fff;
    background: var(--accent-primary);
    border: none;
    border-radius: 6px;
    cursor: pointer;
    transition: opacity 0.15s;
}
.credential-btn-save:hover { opacity: 0.9; }
.credential-btn-save:disabled { opacity: 0.5; cursor: not-allowed; }
`;
