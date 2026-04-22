import React, { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { agentApi } from '../services/api';
import LinearCopyButton from '../components/LinearCopyButton';
function fetchAuth<T>(url: string, options?: RequestInit): Promise<T> {
    const token = localStorage.getItem('token');
    return fetch(`/api${url}`, {
        ...options,
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    }).then(r => r.json());
}

interface OpenClawSettingsProps {
    agent: any;
    agentId: string;
}

export default function OpenClawSettings({ agent, agentId }: OpenClawSettingsProps) {
    const { t, i18n } = useTranslation();
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const isChinese = i18n.language?.startsWith('zh');

    // ─── API Key state ──────────────────────────────────
    const [apiKey, setApiKey] = useState<string | null>(null);
    const [regenerating, setRegenerating] = useState(false);
    const [showConfirm, setShowConfirm] = useState(false);
    // ─── Delete state ───────────────────────────────────
    const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
    const [deleting, setDeleting] = useState(false);

    const hasKey = agent?.has_api_key || false;

    const handleRegenerate = async (autoCopy = false) => {
        setRegenerating(true);
        try {
            const result = await fetchAuth<{ api_key: string }>(`/agents/${agentId}/api-key`, { method: 'POST' });
            setApiKey(result.api_key);
            setShowConfirm(false);
            // Refresh agent data so has_api_key updates
            queryClient.invalidateQueries({ queryKey: ['agent', agentId] });
            if (autoCopy) {
                try {
                    await navigator.clipboard.writeText(result.api_key);
                } catch (err) {
                    console.error('Failed to auto-copy to clipboard:', err);
                }
            }
        } catch (e) {
            console.error('Failed to regenerate API key', e);
        } finally {
            setRegenerating(false);
        }
    };

    const handleDelete = async () => {
        setDeleting(true);
        try {
            await agentApi.delete(agentId);
            queryClient.invalidateQueries({ queryKey: ['agents'] });
            navigate('/');
        } catch (e) {
            console.error('Failed to delete agent', e);
            setDeleting(false);
        }
    };

    // ─── Permissions state ──────────────────────────────
    const { data: permData } = useQuery({
        queryKey: ['agent-permissions', agentId],
        queryFn: () => fetchAuth<any>(`/agents/${agentId}/permissions`),
        enabled: !!agentId,
    });

    const handleScopeChange = async (newScope: string) => {
        try {
            await fetchAuth(`/agents/${agentId}/permissions`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ scope_type: newScope, scope_ids: [], access_level: permData?.access_level || 'use' }),
            });
            queryClient.invalidateQueries({ queryKey: ['agent-permissions', agentId] });
            queryClient.invalidateQueries({ queryKey: ['agent', agentId] });
        } catch (e) {
            console.error('Failed to update permissions', e);
        }
    };

    const handleAccessLevelChange = async (newLevel: string) => {
        try {
            await fetchAuth(`/agents/${agentId}/permissions`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ scope_type: permData?.scope_type || 'company', scope_ids: permData?.scope_ids || [], access_level: newLevel }),
            });
            queryClient.invalidateQueries({ queryKey: ['agent-permissions', agentId] });
            queryClient.invalidateQueries({ queryKey: ['agent', agentId] });
        } catch (e) {
            console.error('Failed to update access level', e);
        }
    };

    const isOwner = permData?.is_owner ?? false;
    const currentScope = permData?.scope_type || 'company';
    const currentAccessLevel = permData?.access_level || 'use';

    // ─── Bridge mode state ──────────────────────────────
    const currentBridgeMode: 'disabled' | 'enabled' | 'auto' =
        (agent?.bridge_mode as any) || 'disabled';
    const [bridgeSaving, setBridgeSaving] = useState<string | null>(null);

    const handleBridgeModeChange = async (newMode: 'disabled' | 'enabled' | 'auto') => {
        if (newMode === currentBridgeMode) return;
        setBridgeSaving(newMode);
        try {
            await agentApi.update(agentId, { bridge_mode: newMode } as any);
            queryClient.invalidateQueries({ queryKey: ['agent', agentId] });
        } catch (e) {
            console.error('Failed to update bridge_mode', e);
        } finally {
            setBridgeSaving(null);
        }
    };

    // ─── Bridge installer download ──────────────────────
    const detectedPlatform: 'windows' | 'macos' | 'linux' = (() => {
        const p = (typeof navigator !== 'undefined' ? navigator.platform || '' : '').toLowerCase();
        const ua = (typeof navigator !== 'undefined' ? navigator.userAgent || '' : '').toLowerCase();
        if (p.startsWith('win') || ua.includes('windows')) return 'windows';
        if (p.startsWith('mac') || ua.includes('mac os')) return 'macos';
        return 'linux';
    })();
    const [installerPlatform, setInstallerPlatform] = useState<'windows' | 'macos' | 'linux'>(detectedPlatform);
    const [installerDownloading, setInstallerDownloading] = useState(false);
    const [installerConfirm, setInstallerConfirm] = useState(false);
    const [installerError, setInstallerError] = useState<string>('');
    const [installerDownloaded, setInstallerDownloaded] = useState(false);

    const handleDownloadInstaller = async () => {
        setInstallerDownloading(true);
        setInstallerError('');
        try {
            const token = localStorage.getItem('token');
            const resp = await fetch(`/api/agents/${agentId}/bridge-installer?platform=${installerPlatform}`, {
                method: 'POST',
                headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
            });
            if (!resp.ok) {
                const errText = await resp.text().catch(() => '');
                throw new Error(errText || `HTTP ${resp.status}`);
            }
            const blob = await resp.blob();
            const filename = resp.headers.get('X-Clawith-Filename')
                || (installerPlatform === 'windows' ? 'clawith-bridge-setup.exe' : 'install-clawith-bridge.sh');

            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            setInstallerDownloaded(true);
            setInstallerConfirm(false);
            // Agent's api_key_hash + bridge_mode may have changed server-side
            queryClient.invalidateQueries({ queryKey: ['agent', agentId] });
            // Clear previously-shown plaintext key (it's now invalid)
            setApiKey(null);
        } catch (e: any) {
            console.error('Failed to download installer', e);
            setInstallerError(e?.message || 'Download failed');
        } finally {
            setInstallerDownloading(false);
        }
    };

    const runCommand = installerPlatform === 'windows'
        ? (isChinese
            ? '双击 clawith-bridge-setup.exe 即可安装'
            : 'Double-click clawith-bridge-setup.exe to install')
        : 'bash install-clawith-bridge.sh';

    return (
        <div>
            <h3 style={{ marginBottom: '16px' }}>{t('agent.settings.title')}</h3>

            {/* ── API Key Management ── */}
            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '4px' }}>
                    API Key
                </h4>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                    {isChinese
                        ? 'OpenClaw 通过此 Key 连接平台。重新生成后旧 Key 将立即失效。'
                        : 'OpenClaw uses this key to connect to the platform. Regenerating will immediately invalidate the old key.'}
                </p>

                {/* API Key Display Logic */}
                {(() => {
                    const activeKey = apiKey || (agent?.api_key_hash?.startsWith('oc-') ? agent.api_key_hash : null);
                    const isLegacyHash = hasKey && !activeKey;

                    if (activeKey) {
                        return (
                            <div style={{
                                display: 'flex', alignItems: 'center', gap: '8px',
                                padding: '10px 14px', background: 'rgba(99,102,241,0.06)',
                                borderRadius: '8px', border: '1px solid var(--accent-primary)',
                            }}>
                                <code style={{
                                    flex: 1, fontSize: '13px', fontFamily: 'monospace',
                                    wordBreak: 'break-all', color: 'var(--text-primary)',
                                }}>
                                    {activeKey}
                                </code>
                                <LinearCopyButton
                                    className="btn btn-secondary"
                                    textToCopy={activeKey}
                                    label="Copy"
                                    copiedLabel="Copied"
                                    style={{ padding: '4px 12px', fontSize: '12px', whiteSpace: 'nowrap', minWidth: '70px', height: 'fit-content' }}
                                />
                                <button
                                    className="btn btn-secondary"
                                    onClick={() => setShowConfirm(true)}
                                    style={{ padding: '4px 12px', fontSize: '12px', whiteSpace: 'nowrap' }}
                                >
                                    {isChinese ? '重新生成' : 'Regenerate'}
                                </button>
                            </div>
                        );
                    }

                    return (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                            <div style={{
                                flex: 1, padding: '8px 14px', borderRadius: '8px',
                                background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)',
                                fontFamily: 'monospace', fontSize: '13px', color: 'var(--text-secondary)',
                                letterSpacing: '0.5px',
                            }}>
                                {isLegacyHash
                                    ? (isChinese ? '旧版密钥（已加密隐藏），请重新生成以查看明文' : 'Legacy key (encrypted), please regenerate to view')
                                    : (isChinese ? '未生成' : 'Not generated')}
                            </div>
                            <button
                                className="btn btn-secondary"
                                onClick={() => setShowConfirm(true)}
                                style={{ padding: '6px 16px', fontSize: '12px', whiteSpace: 'nowrap' }}
                            >
                                {isLegacyHash
                                    ? (isChinese ? '重新生成' : 'Regenerate')
                                    : (isChinese ? '生成' : 'Generate')}
                            </button>
                        </div>
                    );
                })()}

                {/* Confirmation dialog */}
                {showConfirm && (
                    <div style={{
                        marginTop: '12px', padding: '14px', borderRadius: '8px',
                        background: hasKey ? 'rgba(255,80,80,0.06)' : 'rgba(99,102,241,0.04)',
                        border: hasKey ? '1px solid rgba(255,80,80,0.2)' : '1px solid var(--border-subtle)',
                    }}>
                        <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '8px', color: 'var(--text-primary)' }}>
                            {hasKey
                                ? (isChinese ? '确认重新生成 API Key？' : 'Regenerate API Key?')
                                : (isChinese ? '生成 API Key？' : 'Generate API Key?')}
                        </div>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                            {hasKey
                                ? (isChinese
                                    ? '当前 Key 将立即失效，所有使用旧 Key 的设备将断开连接。'
                                    : 'The current key will be revoked immediately. All devices using the old key will be disconnected.')
                                : (isChinese
                                    ? '将为此 Agent 生成一个新的 API Key。'
                                    : 'A new API Key will be generated for this agent.')}
                        </div>
                        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                            <button
                                className="btn btn-secondary"
                                onClick={() => setShowConfirm(false)}
                                style={{ padding: '5px 14px', fontSize: '12px' }}
                            >
                                {isChinese ? '取消' : 'Cancel'}
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={() => handleRegenerate(false)}
                                disabled={regenerating}
                                style={{ padding: '5px 14px', fontSize: '12px' }}
                            >
                                {regenerating
                                    ? (isChinese ? '生成中...' : 'Generating...')
                                    : (isChinese ? '确认' : 'Confirm')}
                            </button>
                        </div>
                    </div>
                )}
            </div>

            {/* ── Bridge Mode ── */}
            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '4px' }}>
                    {isChinese ? '本地 Bridge 连接模式' : 'Local Bridge Mode'}
                </h4>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                    {isChinese
                        ? 'Bridge 是跑在你本机的小程序，把 Claude Code 等本地工具接入 Clawith。'
                        : 'The bridge is a small program running on your machine that connects local tools like Claude Code to Clawith.'}
                </p>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {([
                        {
                            val: 'enabled' as const,
                            label: isChinese ? '启用（推荐）' : 'Enabled (recommended)',
                            desc: isChinese
                                ? '通过 Bridge 实时流式执行。Bridge 未连接时消息会失败。'
                                : 'Stream execution via bridge. Messages fail if bridge is not connected.',
                        },
                        {
                            val: 'auto' as const,
                            label: isChinese ? '自动回落' : 'Auto fallback',
                            desc: isChinese
                                ? '优先 Bridge；未连接时回落到旧版 Gateway 轮询（~5 分钟延迟）。'
                                : 'Prefer bridge; fall back to legacy gateway polling (~5 min delay) when offline.',
                        },
                        {
                            val: 'disabled' as const,
                            label: isChinese ? '禁用（兼容旧版）' : 'Disabled (legacy)',
                            desc: isChinese
                                ? '只走 Gateway 轮询。Bridge 连接会被拒绝。'
                                : 'Use gateway polling only. Bridge connections will be rejected.',
                        },
                    ]).map(opt => {
                        const selected = currentBridgeMode === opt.val;
                        const isSaving = bridgeSaving === opt.val;
                        return (
                            <label
                                key={opt.val}
                                style={{
                                    display: 'flex', alignItems: 'flex-start', gap: '10px',
                                    padding: '12px 14px', borderRadius: '8px',
                                    cursor: isOwner && !bridgeSaving ? 'pointer' : 'default',
                                    border: selected ? '1px solid var(--accent-primary)' : '1px solid var(--border-subtle)',
                                    background: selected ? 'rgba(99,102,241,0.06)' : 'transparent',
                                    opacity: isOwner ? 1 : 0.7,
                                    transition: 'all 0.15s',
                                }}
                            >
                                <input
                                    type="radio"
                                    name="bridge_mode_oc"
                                    checked={selected}
                                    disabled={!isOwner || !!bridgeSaving}
                                    onChange={() => handleBridgeModeChange(opt.val)}
                                    style={{ accentColor: 'var(--accent-primary)', marginTop: '2px' }}
                                />
                                <div style={{ flex: 1 }}>
                                    <div style={{ fontWeight: 500, fontSize: '13px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        {opt.label}
                                        {isSaving && (
                                            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                {isChinese ? '保存中…' : 'Saving…'}
                                            </span>
                                        )}
                                    </div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                        {opt.desc}
                                    </div>
                                </div>
                            </label>
                        );
                    })}
                </div>

                {!isOwner && (
                    <div style={{ marginTop: '12px', fontSize: '11px', color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
                        {isChinese ? '只有创建者或管理员可以修改此设置' : 'Only the creator or admin can change this setting'}
                    </div>
                )}
            </div>

            {/* ── Install Bridge ── */}
            {isOwner && (
                <div className="card" style={{ marginBottom: '12px' }}>
                    <h4 style={{ marginBottom: '4px' }}>
                        {isChinese ? '一键安装 Bridge' : 'One-click Bridge Install'}
                    </h4>
                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                        {isChinese
                            ? '下载预配置好 API Key 的安装包，在你本机运行即可完成安装 + 自启 + 连接。Windows 无需 Python。'
                            : 'Download a pre-configured installer. Run it on your local machine to install + autostart + connect. No Python required on Windows.'}
                    </p>

                    {/* Runtime selector (editable) */}
                    <RuntimeSelector agent={agent} agentId={agentId} isChinese={isChinese} />

                    {/* Platform selector */}
                    <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
                        {([
                            { val: 'windows' as const, label: 'Windows' },
                            { val: 'macos' as const, label: 'macOS' },
                            { val: 'linux' as const, label: 'Linux' },
                        ]).map(opt => {
                            const selected = installerPlatform === opt.val;
                            return (
                                <label
                                    key={opt.val}
                                    style={{
                                        flex: 1, padding: '10px 12px', borderRadius: '8px',
                                        cursor: 'pointer', textAlign: 'center',
                                        border: selected ? '1px solid var(--accent-primary)' : '1px solid var(--border-subtle)',
                                        background: selected ? 'rgba(99,102,241,0.06)' : 'transparent',
                                        transition: 'all 0.15s', fontSize: '13px', fontWeight: 500,
                                    }}
                                >
                                    <input
                                        type="radio"
                                        name="bridge_installer_platform"
                                        checked={selected}
                                        onChange={() => setInstallerPlatform(opt.val)}
                                        style={{ display: 'none' }}
                                    />
                                    {opt.label}
                                </label>
                            );
                        })}
                    </div>

                    {/* Warning about key regeneration */}
                    <div style={{
                        padding: '10px 12px', borderRadius: '6px',
                        background: 'rgba(255,180,50,0.08)', border: '1px solid rgba(255,180,50,0.25)',
                        fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px',
                    }}>
                        {isChinese
                            ? '⚠ 每次下载都会重新生成 API Key，已在运行的 bridge 会断开。需要重新分发安装器给多台机器时请一次性配置完。'
                            : '⚠ Each download regenerates the API Key; any running bridge will disconnect. Set up all machines from a single download.'}
                    </div>

                    {/* Download action */}
                    {!installerConfirm ? (
                        <button
                            className="btn btn-primary"
                            onClick={() => setInstallerConfirm(true)}
                            disabled={installerDownloading}
                            style={{ padding: '8px 20px', fontSize: '13px' }}
                        >
                            {isChinese
                                ? `下载 ${installerPlatform === 'windows' ? 'Windows' : installerPlatform === 'macos' ? 'macOS' : 'Linux'} 安装器`
                                : `Download ${installerPlatform === 'windows' ? 'Windows' : installerPlatform === 'macos' ? 'macOS' : 'Linux'} Installer`}
                        </button>
                    ) : (
                        <div style={{
                            padding: '12px 14px', borderRadius: '8px',
                            background: 'rgba(99,102,241,0.04)', border: '1px solid var(--border-subtle)',
                        }}>
                            <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '8px' }}>
                                {isChinese ? '确认下载并重新生成 API Key？' : 'Confirm download and regenerate API Key?'}
                            </div>
                            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                                <button
                                    className="btn btn-secondary"
                                    onClick={() => setInstallerConfirm(false)}
                                    disabled={installerDownloading}
                                    style={{ padding: '5px 14px', fontSize: '12px' }}
                                >
                                    {isChinese ? '取消' : 'Cancel'}
                                </button>
                                <button
                                    className="btn btn-primary"
                                    onClick={handleDownloadInstaller}
                                    disabled={installerDownloading}
                                    style={{ padding: '5px 14px', fontSize: '12px' }}
                                >
                                    {installerDownloading
                                        ? (isChinese ? '生成中…' : 'Generating…')
                                        : (isChinese ? '下载' : 'Download')}
                                </button>
                            </div>
                        </div>
                    )}

                    {installerError && (
                        <div style={{ marginTop: '10px', fontSize: '12px', color: 'var(--error)' }}>
                            {installerError}
                        </div>
                    )}

                    {/* Post-download instructions */}
                    {installerDownloaded && (
                        <div style={{
                            marginTop: '14px', padding: '12px 14px', borderRadius: '8px',
                            background: 'rgba(50,200,100,0.06)', border: '1px solid rgba(50,200,100,0.2)',
                        }}>
                            <div style={{ fontSize: '12px', fontWeight: 500, marginBottom: '8px', color: 'var(--text-primary)' }}>
                                {isChinese ? '✓ 已下载。在本机运行：' : '✓ Downloaded. Run it on your machine:'}
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <code style={{
                                    flex: 1, padding: '6px 10px', borderRadius: '6px',
                                    background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)',
                                    fontSize: '12px', fontFamily: 'monospace',
                                    wordBreak: 'break-all', color: 'var(--text-primary)',
                                }}>
                                    {runCommand}
                                </code>
                                <LinearCopyButton
                                    className="btn btn-secondary"
                                    textToCopy={runCommand}
                                    label={isChinese ? '复制' : 'Copy'}
                                    copiedLabel={isChinese ? '已复制' : 'Copied'}
                                    style={{ padding: '4px 12px', fontSize: '12px', whiteSpace: 'nowrap', minWidth: '60px' }}
                                />
                            </div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '10px', lineHeight: 1.6 }}>
                                {isChinese
                                    ? <>前置：先装 <code>claude</code> CLI 并登录（<code>npm install -g @anthropic-ai/claude-code</code> 然后 <code>claude login</code>）。</>
                                    : <>Prereq: install <code>claude</code> CLI and login first (<code>npm install -g @anthropic-ai/claude-code</code> then <code>claude login</code>).</>}
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* ── Permissions ── */}
            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '12px' }}>
                    {t('agent.settings.perm.title', 'Access Permissions')}
                </h4>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                    {t('agent.settings.perm.description', 'Control who can see and interact with this agent. Only the creator or admin can change this.')}
                </p>

                {/* Scope Selection */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '16px' }}>
                    {(['company', 'user'] as const).map((scope) => (
                        <label
                            key={scope}
                            style={{
                                display: 'flex', alignItems: 'center', gap: '10px',
                                padding: '12px 14px', borderRadius: '8px',
                                cursor: isOwner ? 'pointer' : 'default',
                                border: currentScope === scope
                                    ? '1px solid var(--accent-primary)'
                                    : '1px solid var(--border-subtle)',
                                background: currentScope === scope
                                    ? 'rgba(99,102,241,0.06)'
                                    : 'transparent',
                                opacity: isOwner ? 1 : 0.7,
                                transition: 'all 0.15s',
                            }}
                        >
                            <input
                                type="radio"
                                name="perm_scope_oc"
                                checked={currentScope === scope}
                                disabled={!isOwner}
                                onChange={() => handleScopeChange(scope)}
                                style={{ accentColor: 'var(--accent-primary)' }}
                            />
                            <div>
                                <div style={{ fontWeight: 500, fontSize: '13px' }}>
                                    {scope === 'company'
                                        ? t('agent.settings.perm.companyWide', 'Company-wide')
                                        : t('agent.settings.perm.onlyMe', 'Only Me')}
                                </div>
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                    {scope === 'company' && t('agent.settings.perm.companyWideDesc', 'All users in the organization can use this agent')}
                                    {scope === 'user' && t('agent.settings.perm.onlyMeDesc', 'Only the creator can use this agent')}
                                </div>
                            </div>
                        </label>
                    ))}
                </div>

                {/* Access Level for company scope */}
                {currentScope === 'company' && isOwner && (
                    <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '12px' }}>
                        <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '8px' }}>
                            {t('agent.settings.perm.defaultAccess', 'Default Access Level')}
                        </label>
                        <div style={{ display: 'flex', gap: '8px' }}>
                            {[
                                { val: 'use', label: t('agent.settings.perm.useAccess', 'Use'), desc: t('agent.settings.perm.useAccessDesc', 'Task, Chat, Tools, Skills, Workspace') },
                                { val: 'manage', label: t('agent.settings.perm.manageAccess', 'Manage'), desc: t('agent.settings.perm.manageAccessDesc', 'Full access including Settings, Mind, Relationships') },
                            ].map(opt => (
                                <label key={opt.val}
                                    style={{
                                        flex: 1, padding: '10px 12px', borderRadius: '8px',
                                        cursor: 'pointer',
                                        border: currentAccessLevel === opt.val
                                            ? '1px solid var(--accent-primary)'
                                            : '1px solid var(--border-subtle)',
                                        background: currentAccessLevel === opt.val
                                            ? 'rgba(99,102,241,0.06)'
                                            : 'transparent',
                                        transition: 'all 0.15s',
                                    }}
                                >
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                        <input type="radio" name="access_level_oc" checked={currentAccessLevel === opt.val}
                                            onChange={() => handleAccessLevelChange(opt.val)}
                                            style={{ accentColor: 'var(--accent-primary)' }} />
                                        <span style={{ fontWeight: 500, fontSize: '13px' }}>{opt.label}</span>
                                    </div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px', marginLeft: '20px' }}>{opt.desc}</div>
                                </label>
                            ))}
                        </div>
                    </div>
                )}

                {currentScope !== 'company' && permData?.scope_names?.length > 0 && (
                    <div style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                        <span style={{ fontWeight: 500 }}>{t('agent.settings.perm.currentAccess', 'Current access')}:</span>{' '}
                        {permData.scope_names.map((s: any) => s.name).join(', ')}
                    </div>
                )}

                {!isOwner && (
                    <div style={{ marginTop: '12px', fontSize: '11px', color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
                        {t('agent.settings.perm.readOnly', 'Only the creator or admin can change permissions')}
                    </div>
                )}
            </div>

            {/* ── Danger Zone: Delete Agent ── */}
            {isOwner && (
                <div className="card" style={{
                    marginBottom: '12px',
                    border: '1px solid rgba(255,80,80,0.2)',
                }}>
                    <h4 style={{ marginBottom: '4px', color: 'var(--error)' }}>
                        {isChinese ? '危险操作' : 'Danger Zone'}
                    </h4>
                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                        {isChinese
                            ? '删除后无法恢复，所有聊天记录、活动日志和关联数据都将被永久清除。'
                            : 'This action cannot be undone. All chat history, activity logs, and associated data will be permanently deleted.'}
                    </p>

                    {showDeleteConfirm ? (
                        <div style={{
                            padding: '14px', borderRadius: '8px',
                            background: 'rgba(255,80,80,0.06)', border: '1px solid rgba(255,80,80,0.2)',
                        }}>
                            <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '8px', color: 'var(--text-primary)' }}>
                                {isChinese
                                    ? `确认删除 Agent "${agent?.name}"？`
                                    : `Delete agent "${agent?.name}"?`}
                            </div>
                            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                                {isChinese
                                    ? '此操作不可撤销。'
                                    : 'This action is irreversible.'}
                            </div>
                            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                                <button
                                    className="btn btn-secondary"
                                    onClick={() => setShowDeleteConfirm(false)}
                                    style={{ padding: '5px 14px', fontSize: '12px' }}
                                >
                                    {isChinese ? '取消' : 'Cancel'}
                                </button>
                                <button
                                    className="btn btn-danger"
                                    onClick={handleDelete}
                                    disabled={deleting}
                                    style={{ padding: '5px 14px', fontSize: '12px' }}
                                >
                                    {deleting
                                        ? (isChinese ? '删除中...' : 'Deleting...')
                                        : (isChinese ? '确认删除' : 'Delete')}
                                </button>
                            </div>
                        </div>
                    ) : (
                        <button
                            className="btn btn-danger"
                            onClick={() => setShowDeleteConfirm(true)}
                            style={{ padding: '6px 20px', fontSize: '12px' }}
                        >
                            {isChinese ? '删除此 Agent' : 'Delete this Agent'}
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

// ────────────────────────────────────────────────────────────────
// RuntimeSelector
//
// Lets the creator/admin change bridge_adapter after agent creation.
// When it changes, the already-installed bridge keeps advertising the
// old adapter until the user reinstalls — we surface that clearly
// instead of silently drifting.
// ────────────────────────────────────────────────────────────────
interface RuntimeSelectorProps {
    agent: any;
    agentId: string;
    isChinese: boolean;
}

function RuntimeSelector({ agent, agentId, isChinese }: RuntimeSelectorProps) {
    const qc = useQueryClient();
    const current: 'claude_code' | 'openclaw' | 'hermes' =
        (agent?.bridge_adapter as any) || 'claude_code';
    const [saving, setSaving] = useState<string | null>(null);
    const [justChanged, setJustChanged] = useState(false);

    const OPTIONS: { value: 'claude_code' | 'openclaw' | 'hermes'; label: string }[] = [
        { value: 'claude_code', label: 'Claude Code' },
        { value: 'openclaw', label: 'OpenClaw' },
        { value: 'hermes', label: 'Hermes' },
    ];

    const onSelect = async (next: 'claude_code' | 'openclaw' | 'hermes') => {
        if (next === current || saving) return;
        setSaving(next);
        try {
            await agentApi.update(agentId, { bridge_adapter: next } as any);
            await qc.invalidateQueries({ queryKey: ['agent', agentId] });
            setJustChanged(true);
        } catch (e) {
            console.error('Failed to update runtime', e);
            alert(isChinese ? '切换 runtime 失败，请稍后重试' : 'Failed to change runtime, please retry');
        } finally {
            setSaving(null);
        }
    };

    return (
        <div style={{ marginBottom: '12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                    {isChinese ? '运行时' : 'Runtime'}
                </span>
                <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                    {isChinese ? '（创建后可切换）' : '(changeable after creation)'}
                </span>
            </div>
            <div style={{ display: 'flex', gap: '6px' }}>
                {OPTIONS.map(opt => {
                    const selected = current === opt.value;
                    const isLoading = saving === opt.value;
                    return (
                        <button
                            key={opt.value}
                            type="button"
                            onClick={() => onSelect(opt.value)}
                            disabled={!!saving}
                            style={{
                                flex: 1, padding: '8px 10px', borderRadius: '6px',
                                cursor: saving ? 'not-allowed' : 'pointer',
                                border: selected ? '1px solid var(--accent-primary)' : '1px solid var(--border-subtle)',
                                background: selected ? 'rgba(99,102,241,0.08)' : 'transparent',
                                fontSize: '12px', fontWeight: selected ? 600 : 500,
                                opacity: saving && !selected && !isLoading ? 0.5 : 1,
                            }}
                        >
                            {isLoading ? (isChinese ? '切换中…' : 'Saving…') : opt.label}
                        </button>
                    );
                })}
            </div>
            {justChanged && (
                <div style={{
                    marginTop: '8px', padding: '10px 12px', borderRadius: '6px',
                    background: 'rgba(255,180,50,0.10)', border: '1px solid rgba(255,180,50,0.30)',
                    fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.55,
                }}>
                    {isChinese ? (
                        <>
                            已切换到 <strong>{OPTIONS.find(o => o.value === current)?.label}</strong>。
                            之前安装过的 bridge 仍然只启用旧 runtime，请 <strong>重新下载安装器</strong> 并在本机运行，
                            或在 <code>~/.clawith-bridge.toml</code> 里把 <code>[{current}]</code> 下的 <code>enabled</code> 改成 <code>true</code> 后重启 bridge。
                        </>
                    ) : (
                        <>
                            Switched to <strong>{OPTIONS.find(o => o.value === current)?.label}</strong>.
                            Your already-installed bridge still only enables the old runtime —
                            <strong> redownload the installer</strong> below and run it again on your local machine,
                            or edit <code>~/.clawith-bridge.toml</code> to set <code>enabled = true</code> under <code>[{current}]</code> and restart the bridge.
                        </>
                    )}
                </div>
            )}
        </div>
    );
}
