import React, { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { agentApi } from '../services/api';
import { copyToClipboard } from '../utils/clipboard';

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
    const [copied, setCopied] = useState(false);

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
                handleCopy(result.api_key);
            }
        } catch (e) {
            console.error('Failed to regenerate API key', e);
        } finally {
            setRegenerating(false);
        }
    };

    const handleCopy = async (text: string) => {
        try {
            await copyToClipboard(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch { }
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
                                <button
                                    className="btn btn-secondary"
                                    onClick={() => handleCopy(activeKey)}
                                    style={{ padding: '4px 12px', fontSize: '12px', whiteSpace: 'nowrap' }}
                                >
                                    {copied ? 'Copied' : 'Copy'}
                                </button>
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
