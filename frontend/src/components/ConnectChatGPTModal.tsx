import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
    CODEX_OAUTH_MODELS,
    codexOauthApi,
    type CodexOauthStartResponse,
} from '../services/api';

interface ConnectChatGPTModalProps {
    open: boolean;
    onClose: () => void;
    onCreated: (modelId: string) => void;
    /** Tenant to provision the model under. Required for platform-admin sessions
     *  managing a non-default tenant; when unset the backend falls back to the
     *  caller's own tenant. */
    tenantId?: string | null;
}

type FlowTab = 'oauth' | 'paste';
type OauthStep = 'idle' | 'authorizing' | 'got-code' | 'submitting' | 'done';

const DEFAULT_LABEL = 'Codex (ChatGPT subscription)';
const POLL_INTERVAL_MS = 1500;
const POLL_MAX_DURATION_MS = 5 * 60_000;

export default function ConnectChatGPTModal({ open, onClose, onCreated, tenantId }: ConnectChatGPTModalProps) {
    const { t } = useTranslation();
    const [tab, setTab] = useState<FlowTab>('oauth');

    // OAuth state
    const [oauthStep, setOauthStep] = useState<OauthStep>('idle');
    const [oauthSession, setOauthSession] = useState<CodexOauthStartResponse | null>(null);
    const [oauthCode, setOauthCode] = useState<string | null>(null);
    const [oauthError, setOauthError] = useState<string | null>(null);
    const [manualUrl, setManualUrl] = useState('');
    const pollTimerRef = useRef<number | null>(null);
    const pollDeadlineRef = useRef<number>(0);

    // Shared form state
    const [label, setLabel] = useState(DEFAULT_LABEL);
    const [model, setModel] = useState<typeof CODEX_OAUTH_MODELS[number]>('gpt-5.1-codex');

    // Paste-creds form state
    const [accessToken, setAccessToken] = useState('');
    const [refreshToken, setRefreshToken] = useState('');
    const [expiresIn, setExpiresIn] = useState(3600);
    const [accountId, setAccountId] = useState('');
    const [pasteError, setPasteError] = useState<string | null>(null);
    const [pasteSubmitting, setPasteSubmitting] = useState(false);

    // Reset everything when modal opens
    useEffect(() => {
        if (!open) {
            stopPolling();
            return;
        }
        setTab('oauth');
        setOauthStep('idle');
        setOauthSession(null);
        setOauthCode(null);
        setOauthError(null);
        setManualUrl('');
        setLabel(DEFAULT_LABEL);
        setModel('gpt-5.1-codex');
        setAccessToken('');
        setRefreshToken('');
        setExpiresIn(3600);
        setAccountId('');
        setPasteError(null);
        setPasteSubmitting(false);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);

    useEffect(() => () => stopPolling(), []);

    function stopPolling() {
        if (pollTimerRef.current) {
            window.clearTimeout(pollTimerRef.current);
            pollTimerRef.current = null;
        }
    }

    async function startOauth() {
        setOauthError(null);
        setOauthCode(null);
        setOauthStep('authorizing');
        try {
            const resp = await codexOauthApi.start();
            setOauthSession(resp);
            window.open(resp.authorize_url, '_blank', 'noopener');
            if (resp.loopback_ready) {
                pollDeadlineRef.current = Date.now() + POLL_MAX_DURATION_MS;
                schedulePoll(resp.state);
            }
        } catch (e: any) {
            setOauthStep('idle');
            setOauthError(e?.message || String(e));
        }
    }

    function schedulePoll(state: string) {
        pollTimerRef.current = window.setTimeout(() => pollOnce(state), POLL_INTERVAL_MS);
    }

    async function pollOnce(state: string) {
        if (Date.now() > pollDeadlineRef.current) {
            setOauthError(t('enterprise.llm.codex.errors.pollTimeout'));
            setOauthStep('idle');
            return;
        }
        try {
            const resp = await codexOauthApi.poll(state);
            if (resp.expired) {
                setOauthError(t('enterprise.llm.codex.errors.sessionExpired'));
                setOauthStep('idle');
                return;
            }
            if (resp.error) {
                setOauthError(resp.error);
                setOauthStep('idle');
                return;
            }
            if (resp.code) {
                setOauthCode(resp.code);
                setOauthStep('got-code');
                return;
            }
            schedulePoll(state);
        } catch (e: any) {
            setOauthError(e?.message || String(e));
            setOauthStep('idle');
        }
    }

    function parseCodeFromUrl(raw: string): { code: string; state: string } | null {
        const trimmed = raw.trim();
        if (!trimmed) return null;
        try {
            const url = new URL(trimmed);
            const code = url.searchParams.get('code');
            const state = url.searchParams.get('state');
            if (code && state) return { code, state };
        } catch {
            // not a URL — maybe "code#state" or just code
        }
        if (trimmed.includes('#')) {
            const [c, s] = trimmed.split('#', 2);
            if (c && s) return { code: c, state: s };
        }
        return null;
    }

    function submitManualUrl() {
        setOauthError(null);
        const parsed = parseCodeFromUrl(manualUrl);
        if (!parsed) {
            setOauthError(t('enterprise.llm.codex.errors.manualUrlInvalid'));
            return;
        }
        if (oauthSession && parsed.state !== oauthSession.state) {
            setOauthError(t('enterprise.llm.codex.errors.stateMismatch'));
            return;
        }
        stopPolling();
        setOauthCode(parsed.code);
        setOauthStep('got-code');
    }

    async function finalizeOauth() {
        if (!oauthSession || !oauthCode) return;
        if (!label.trim()) {
            setOauthError(t('enterprise.llm.codex.errors.labelRequired'));
            return;
        }
        setOauthStep('submitting');
        setOauthError(null);
        try {
            const result = await codexOauthApi.complete(
                {
                    state: oauthSession.state,
                    code: oauthCode,
                    label: label.trim(),
                    model,
                },
                tenantId,
            );
            setOauthStep('done');
            onCreated(result.id);
            setTimeout(onClose, 600);
        } catch (e: any) {
            setOauthStep('got-code');
            setOauthError(e?.message || String(e));
        }
    }

    async function submitPaste() {
        setPasteError(null);
        if (!accessToken.trim() || !refreshToken.trim()) {
            setPasteError(t('enterprise.llm.codex.errors.tokensRequired'));
            return;
        }
        if (!label.trim()) {
            setPasteError(t('enterprise.llm.codex.errors.labelRequired'));
            return;
        }
        setPasteSubmitting(true);
        try {
            const result = await codexOauthApi.pasteCreds(
                {
                    access_token: accessToken.trim(),
                    refresh_token: refreshToken.trim(),
                    expires_in_seconds: Math.max(60, Number(expiresIn) || 3600),
                    account_id: accountId.trim() || null,
                    label: label.trim(),
                    model,
                },
                tenantId,
            );
            onCreated(result.id);
            setTimeout(onClose, 300);
        } catch (e: any) {
            setPasteError(e?.message || String(e));
        } finally {
            setPasteSubmitting(false);
        }
    }

    if (!open) return null;

    const modelSelect = (
        <label style={{ display: 'block', marginBottom: '12px' }}>
            <span style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                {t('enterprise.llm.codex.model')}
            </span>
            <select
                value={model}
                onChange={(e) => setModel(e.target.value as typeof model)}
                style={inputStyle}
            >
                {CODEX_OAUTH_MODELS.map((m) => (
                    <option key={m} value={m}>{m}</option>
                ))}
            </select>
        </label>
    );

    const labelInput = (
        <label style={{ display: 'block', marginBottom: '12px' }}>
            <span style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                {t('enterprise.llm.codex.label')}
            </span>
            <input
                type="text"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                style={inputStyle}
            />
        </label>
    );

    return (
        <div
            style={{
                position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
                background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                zIndex: 10000,
            }}
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div style={{
                background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px',
                width: '560px', maxWidth: '92vw', maxHeight: '90vh', overflowY: 'auto',
                border: '1px solid var(--border-subtle)', boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
            }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                    <h4 style={{ margin: 0, fontSize: '16px' }}>{t('enterprise.llm.codex.title')}</h4>
                    <button onClick={onClose} style={closeBtn} aria-label="close">×</button>
                </div>
                <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '16px', lineHeight: 1.5 }}>
                    {t('enterprise.llm.codex.subtitle')}
                </p>

                <div style={{ display: 'flex', gap: '4px', marginBottom: '16px', borderBottom: '1px solid var(--border-subtle)' }}>
                    <TabButton active={tab === 'oauth'} onClick={() => setTab('oauth')}>
                        {t('enterprise.llm.codex.tabs.oauth')}
                    </TabButton>
                    <TabButton active={tab === 'paste'} onClick={() => setTab('paste')}>
                        {t('enterprise.llm.codex.tabs.paste')}
                    </TabButton>
                </div>

                {tab === 'oauth' && (
                    <div>
                        {oauthStep === 'idle' && (
                            <div>
                                <p style={helpText}>{t('enterprise.llm.codex.oauth.idleHint')}</p>
                                <button className="btn btn-primary" onClick={startOauth}>
                                    {t('enterprise.llm.codex.oauth.startButton')}
                                </button>
                            </div>
                        )}
                        {oauthStep === 'authorizing' && oauthSession && (
                            <div>
                                <p style={helpText}>
                                    {oauthSession.loopback_ready
                                        ? t('enterprise.llm.codex.oauth.waitingLoopback')
                                        : t('enterprise.llm.codex.oauth.waitingManual')}
                                </p>
                                <div style={urlBox}>
                                    <a href={oauthSession.authorize_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-primary)', wordBreak: 'break-all' }}>
                                        {oauthSession.authorize_url}
                                    </a>
                                </div>
                                <div style={{ marginTop: '16px' }}>
                                    <span style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                                        {t('enterprise.llm.codex.oauth.manualPasteLabel')}
                                    </span>
                                    <textarea
                                        value={manualUrl}
                                        onChange={(e) => setManualUrl(e.target.value)}
                                        placeholder="http://localhost:1455/auth/callback?code=...&state=..."
                                        rows={3}
                                        style={{ ...inputStyle, fontFamily: 'ui-monospace, monospace' }}
                                    />
                                    <button className="btn btn-secondary" onClick={submitManualUrl} style={{ marginTop: '8px' }}>
                                        {t('enterprise.llm.codex.oauth.submitManual')}
                                    </button>
                                </div>
                            </div>
                        )}
                        {(oauthStep === 'got-code' || oauthStep === 'submitting') && (
                            <div>
                                <p style={{ ...helpText, color: 'var(--accent-primary)' }}>
                                    {t('enterprise.llm.codex.oauth.codeReceived')}
                                </p>
                                {labelInput}
                                {modelSelect}
                                <button
                                    className="btn btn-primary"
                                    onClick={finalizeOauth}
                                    disabled={oauthStep === 'submitting'}
                                >
                                    {oauthStep === 'submitting'
                                        ? t('enterprise.llm.codex.oauth.creating')
                                        : t('enterprise.llm.codex.oauth.createButton')}
                                </button>
                            </div>
                        )}
                        {oauthStep === 'done' && (
                            <p style={{ ...helpText, color: 'var(--accent-primary)' }}>
                                {t('enterprise.llm.codex.oauth.done')}
                            </p>
                        )}
                        {oauthError && (
                            <p style={errorText}>{oauthError}</p>
                        )}
                    </div>
                )}

                {tab === 'paste' && (
                    <div>
                        <p style={helpText}>{t('enterprise.llm.codex.paste.hint')}</p>
                        <label style={{ display: 'block', marginBottom: '12px' }}>
                            <span style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                                access_token
                            </span>
                            <textarea
                                value={accessToken}
                                onChange={(e) => setAccessToken(e.target.value)}
                                rows={3}
                                style={{ ...inputStyle, fontFamily: 'ui-monospace, monospace' }}
                            />
                        </label>
                        <label style={{ display: 'block', marginBottom: '12px' }}>
                            <span style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                                refresh_token
                            </span>
                            <textarea
                                value={refreshToken}
                                onChange={(e) => setRefreshToken(e.target.value)}
                                rows={2}
                                style={{ ...inputStyle, fontFamily: 'ui-monospace, monospace' }}
                            />
                        </label>
                        <label style={{ display: 'block', marginBottom: '12px' }}>
                            <span style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                                expires_in_seconds ({t('enterprise.llm.codex.paste.expiresHint')})
                            </span>
                            <input
                                type="number"
                                value={expiresIn}
                                onChange={(e) => setExpiresIn(Number(e.target.value))}
                                style={inputStyle}
                            />
                        </label>
                        <label style={{ display: 'block', marginBottom: '12px' }}>
                            <span style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                                {t('enterprise.llm.codex.paste.accountIdOptional')}
                            </span>
                            <input
                                type="text"
                                value={accountId}
                                onChange={(e) => setAccountId(e.target.value)}
                                style={inputStyle}
                            />
                        </label>
                        {labelInput}
                        {modelSelect}
                        <button
                            className="btn btn-primary"
                            onClick={submitPaste}
                            disabled={pasteSubmitting}
                        >
                            {pasteSubmitting
                                ? t('enterprise.llm.codex.oauth.creating')
                                : t('enterprise.llm.codex.oauth.createButton')}
                        </button>
                        {pasteError && <p style={errorText}>{pasteError}</p>}
                    </div>
                )}
            </div>
        </div>
    );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
    return (
        <button
            onClick={onClick}
            style={{
                padding: '8px 14px', fontSize: '13px', background: 'transparent',
                color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                borderBottom: active ? '2px solid var(--accent-primary)' : '2px solid transparent',
                borderTop: 'none', borderLeft: 'none', borderRight: 'none',
                marginBottom: '-1px', cursor: 'pointer',
            }}
        >
            {children}
        </button>
    );
}

const inputStyle: CSSProperties = {
    width: '100%', padding: '8px 10px', fontSize: '13px',
    background: 'var(--bg-secondary)', color: 'var(--text-primary)',
    border: '1px solid var(--border-subtle)', borderRadius: '6px',
    boxSizing: 'border-box',
};

const helpText: CSSProperties = {
    fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px', lineHeight: 1.5,
};

const errorText: CSSProperties = {
    marginTop: '12px', fontSize: '12px', color: '#e06a63', lineHeight: 1.5,
};

const closeBtn: CSSProperties = {
    background: 'transparent', border: 'none', color: 'var(--text-secondary)',
    fontSize: '20px', cursor: 'pointer', padding: 0, lineHeight: 1,
};

const urlBox: CSSProperties = {
    padding: '8px 10px', background: 'var(--bg-secondary)', borderRadius: '6px',
    border: '1px solid var(--border-subtle)', fontSize: '11px',
};
