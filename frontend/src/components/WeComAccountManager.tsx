import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import LinearCopyButton from './LinearCopyButton';

// ── 新数据结构：支持三种通信模式 ──

export interface WeComBotConfig {
    id: string;              // Bot ID (验证后填入，可选)
    secret: string;          // Bot Secret (仅长连接需要)
    token?: string;          // Webhook 模式需要
    encoding_aes_key?: string; // Webhook 模式需要
}

export interface WeComAgentConfig {
    corp_id: string;         // 企业ID
    agent_id: string;        // 应用ID
    secret: string;          // 应用Secret
    token: string;           // 回调配置Token
    encoding_aes_key: string; // 回调配置AESKey
}

export interface WeComAccount {
    id: string;                          // System generated: wecom_xxx
    nickname: string;                    // 用户定义的显示名称
    
    // 三种通信模式独立开关
    bot_websocket_enabled?: boolean;     // 智能机器人-长连接
    bot_webhook_enabled?: boolean;       // 智能机器人-短链接
    agent_webhook_enabled?: boolean;     // 企业应用
    
    // 嵌套配置对象
    bot?: WeComBotConfig;                // 智能机器人配置
    agent?: WeComAgentConfig;            // 企业应用配置
}

interface WeComAccountManagerProps {
    accounts: WeComAccount[];
    onAccountsChange: (accounts: WeComAccount[]) => void;
    webhookUrls?: { bot?: Record<string, string>; agent?: Record<string, string> };
    agentId?: string;
}

// Generate unique account ID: wecom_xxxxxxxx
const generateAccountId = (): string => {
    const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
    let result = 'wecom_';
    for (let i = 0; i < 8; i++) {
        result += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return result;
};

// Tabler Icons (inline SVG)
const IconPlus = <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14"/></svg>;
const IconEdit = <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>;
const IconTrash = <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>;
const IconX = <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6L6 18M6 6l12 12"/></svg>;

export default function WeComAccountManager({ accounts, onAccountsChange, webhookUrls, agentId }: WeComAccountManagerProps) {
    const { t } = useTranslation();
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [editingAccount, setEditingAccount] = useState<WeComAccount | null>(null);
    const [savedAccountId, setSavedAccountId] = useState<string | null>(null); // Track saved account for URL display
    const [previewAccountId, setPreviewAccountId] = useState<string | null>(null); // For test button preview
    const [connectionMode, setConnectionMode] = useState<'bot_websocket' | 'bot_webhook' | 'agent_webhook'>('bot_websocket');
    const [formData, setFormData] = useState<Partial<WeComAccount>>({
        id: '',
        nickname: '',
        bot_websocket_enabled: true,
        bot_webhook_enabled: false,
        agent_webhook_enabled: false,
        bot: { id: '', secret: '', token: '', encoding_aes_key: '' },
        agent: { corp_id: '', agent_id: '', secret: '', token: '', encoding_aes_key: '' },
    });
    const [errors, setErrors] = useState<Record<string, string>>({});

    // Handle connection mode change (single select)
    const handleModeChange = (mode: 'bot_websocket' | 'bot_webhook' | 'agent_webhook') => {
        setConnectionMode(mode);
        setFormData({
            ...formData,
            bot_websocket_enabled: mode === 'bot_websocket',
            bot_webhook_enabled: mode === 'bot_webhook',
            agent_webhook_enabled: mode === 'agent_webhook',
        });
        setErrors({});
    };

    const validateForm = (): boolean => {
        const newErrors: Record<string, string> = {};

        // Nickname is required
        if (!formData.nickname?.trim()) {
            newErrors.nickname = t('wecomMultiAccount.errors.nicknameRequired', '请填写账号昵称');
        }

        // Validate based on selected mode
        if (connectionMode === 'bot_websocket') {
            // Bot WebSocket mode: require id and secret
            if (!formData.bot?.id?.trim()) {
                newErrors['bot.id'] = t('wecomMultiAccount.errors.botIdRequired', 'Bot ID is required');
            }
            if (!formData.bot?.secret?.trim()) {
                newErrors['bot.secret'] = t('wecomMultiAccount.errors.botSecretRequired', 'Bot Secret is required');
            }
        } else if (connectionMode === 'bot_webhook') {
            // Bot Webhook mode: only require token and encoding_aes_key
            if (!formData.bot?.token?.trim()) {
                newErrors['bot.token'] = t('wecomMultiAccount.errors.tokenRequired', 'Token is required for webhook verification');
            }
            if (!formData.bot?.encoding_aes_key?.trim()) {
                newErrors['bot.encoding_aes_key'] = t('wecomMultiAccount.errors.encodingAesKeyRequired', 'EncodingAESKey is required for webhook verification');
            }
        } else if (connectionMode === 'agent_webhook') {
            // Agent Webhook mode: require all agent fields
            if (!formData.agent?.corp_id?.trim()) {
                newErrors['agent.corp_id'] = t('wecomMultiAccount.errors.corpIdRequired', 'CorpID is required');
            }
            if (!formData.agent?.agent_id?.trim()) {
                newErrors['agent.agent_id'] = t('wecomMultiAccount.errors.agentIdRequired', 'AgentID is required');
            }
            if (!formData.agent?.secret?.trim()) {
                newErrors['agent.secret'] = t('wecomMultiAccount.errors.secretRequired', 'Secret is required');
            }
            if (!formData.agent?.token?.trim()) {
                newErrors['agent.token'] = t('wecomMultiAccount.errors.tokenRequired', 'Token is required');
            }
            if (!formData.agent?.encoding_aes_key?.trim()) {
                newErrors['agent.encoding_aes_key'] = t('wecomMultiAccount.errors.encodingAesKeyRequired', 'EncodingAESKey is required');
            }
        }

        setErrors(newErrors);
        return Object.keys(newErrors).length === 0;
    };

    const handleSave = () => {
        if (!validateForm()) return;

        // Generate ID if new account - use previewAccountId if available
        let accountId = editingAccount?.id || savedAccountId || previewAccountId || '';
        if (!editingAccount && !savedAccountId && !previewAccountId) {
            accountId = generateAccountId();
        }

        // Build account object
        const account: WeComAccount = {
            id: accountId,
            nickname: formData.nickname?.trim() || '',
            bot_websocket_enabled: formData.bot_websocket_enabled || false,
            bot_webhook_enabled: formData.bot_webhook_enabled || false,
            agent_webhook_enabled: formData.agent_webhook_enabled || false,
        };

        // Add bot config if any bot mode enabled
        if (formData.bot_websocket_enabled || formData.bot_webhook_enabled) {
            account.bot = {
                id: formData.bot?.id?.trim() || '',
                secret: formData.bot?.secret?.trim() || '',
                token: formData.bot?.token?.trim(),
                encoding_aes_key: formData.bot?.encoding_aes_key?.trim(),
            };
        }

        // Add agent config if agent mode enabled
        if (formData.agent_webhook_enabled) {
            account.agent = {
                corp_id: formData.agent?.corp_id?.trim() || '',
                agent_id: formData.agent?.agent_id?.trim() || '',
                secret: formData.agent?.secret?.trim() || '',
                token: formData.agent?.token?.trim() || '',
                encoding_aes_key: formData.agent?.encoding_aes_key?.trim() || '',
            };
        }

        if (editingAccount || savedAccountId) {
            onAccountsChange(accounts.map(a => a.id === (editingAccount?.id || savedAccountId) ? account : a));
        } else {
            onAccountsChange([...accounts, account]);
        }

        // For bot webhook mode, keep modal open and track saved account for URL display
        if (connectionMode === 'bot_webhook') {
            // Keep modal open for webhook mode - user needs to verify in WeCom first
            setSavedAccountId(accountId);
            setEditingAccount(account);
            setFormData({ ...formData, id: accountId });
            setPreviewAccountId(null); // Clear preview after save
            setErrors({});
        } else {
            // Close modal for other modes
            setIsModalOpen(false);
            setEditingAccount(null);
            setSavedAccountId(null);
            setPreviewAccountId(null);
            resetFormData();
            setErrors({});
        }
    };

    const resetFormData = () => {
        setFormData({
            id: '',
            nickname: '',
            bot_websocket_enabled: true,
            bot_webhook_enabled: false,
            agent_webhook_enabled: false,
            bot: { id: '', secret: '', token: '', encoding_aes_key: '' },
            agent: { corp_id: '', agent_id: '', secret: '', token: '', encoding_aes_key: '' },
        });
        setSavedAccountId(null);
        setPreviewAccountId(null);
        setConnectionMode('bot_websocket');
    };

    // Test button: save config first, then show Webhook URL
    const handleTestWebhook = () => {
        // Validate required fields for webhook mode
        const newErrors: Record<string, string> = {};
        if (!formData.nickname?.trim()) {
            newErrors.nickname = t('wecomMultiAccount.errors.nicknameRequired', '请填写账号昵称');
        }
        if (!formData.bot?.token?.trim()) {
            newErrors['bot.token'] = t('wecomMultiAccount.errors.tokenRequired', 'Token is required');
        }
        if (!formData.bot?.encoding_aes_key?.trim()) {
            newErrors['bot.encoding_aes_key'] = t('wecomMultiAccount.errors.encodingAesKeyRequired', 'EncodingAESKey is required');
        }
        
        if (Object.keys(newErrors).length > 0) {
            setErrors(newErrors);
            return;
        }

        // Generate or get account ID
        const accountId = editingAccount?.id || savedAccountId || previewAccountId || generateAccountId();
        
        // Build account object
        const account: WeComAccount = {
            id: accountId,
            nickname: formData.nickname?.trim() || '',
            bot_websocket_enabled: false,
            bot_webhook_enabled: true,
            agent_webhook_enabled: false,
            bot: {
                id: formData.bot?.id?.trim() || '',
                secret: formData.bot?.secret?.trim() || '',
                token: formData.bot?.token?.trim() || '',
                encoding_aes_key: formData.bot?.encoding_aes_key?.trim() || '',
            },
        };

        // Update state
        setSavedAccountId(accountId);
        setEditingAccount(account);
        setPreviewAccountId(accountId);
        setFormData({
            ...formData,
            id: accountId,
            bot_websocket_enabled: false,
            bot_webhook_enabled: true,
            agent_webhook_enabled: false,
        });
        setErrors({});

        // Save to backend via onAccountsChange
        if (editingAccount || savedAccountId) {
            onAccountsChange(accounts.map(a => a.id === accountId ? account : a));
        } else {
            onAccountsChange([...accounts, account]);
        }
    };

    // Get the current account ID (preview or saved)
    const getCurrentAccountId = (): string | null => {
        return editingAccount?.id || savedAccountId || previewAccountId;
    };

    const handleEdit = (account: WeComAccount) => {
        setEditingAccount(account);
        setSavedAccountId(account.id);
        
        // Determine connection mode from account
        let mode: 'bot_websocket' | 'bot_webhook' | 'agent_webhook' = 'bot_websocket';
        if (account.bot_webhook_enabled) {
            mode = 'bot_webhook';
        } else if (account.agent_webhook_enabled) {
            mode = 'agent_webhook';
        }
        setConnectionMode(mode);
        
        setFormData({
            id: account.id,
            nickname: account.nickname,
            bot_websocket_enabled: account.bot_websocket_enabled || false,
            bot_webhook_enabled: account.bot_webhook_enabled || false,
            agent_webhook_enabled: account.agent_webhook_enabled || false,
            bot: account.bot ? { ...account.bot } : { id: '', secret: '', token: '', encoding_aes_key: '' },
            agent: account.agent ? { ...account.agent } : { corp_id: '', agent_id: '', secret: '', token: '', encoding_aes_key: '' },
        });
        setErrors({});
        setIsModalOpen(true);
    };

    const handleAdd = () => {
        setEditingAccount(null);
        setSavedAccountId(null);
        resetFormData();
        setErrors({});
        setIsModalOpen(true);
    };

    const handleDelete = (accountId: string) => {
        if (accounts.length <= 1) {
            alert(t('wecomMultiAccount.errors.cannotDeleteLastAccount', 'Cannot delete the last account'));
            return;
        }
        if (confirm(t('wecomMultiAccount.confirmDelete', 'Are you sure you want to delete this account?'))) {
            onAccountsChange(accounts.filter(a => a.id !== accountId));
        }
    };

    const getEnabledModes = (account: WeComAccount): string[] => {
        const modes: string[] = [];
        if (account.bot_websocket_enabled) modes.push('机器人长连接');
        if (account.bot_webhook_enabled) modes.push('机器人短链接');
        if (account.agent_webhook_enabled) modes.push('企业应用');
        return modes;
    };

    const getAccountWebhookUrl = (accountId: string, mode: 'bot' | 'agent'): string | null => {
        if (webhookUrls?.[mode]?.[accountId]) {
            return webhookUrls[mode][accountId];
        }
        // Fallback to default URL pattern - use current protocol
        if (agentId) {
            const protocol = window.location.protocol; // http: or https:
            const host = window.location.host;
            return `${protocol}//${host}/api/channel/wecom/${agentId}/${mode}/${accountId}/webhook`;
        }
        return null;
    };

    // Get accounts with webhook enabled
    const accountsWithWebhook = accounts.filter(a => a.bot_webhook_enabled || a.agent_webhook_enabled);

    return (
        <div style={{ marginTop: '12px' }}>
            {/* Account List */}
            <div style={{ marginBottom: '12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                    <span style={{ fontSize: '12px', fontWeight: 500 }}>
                        {t('wecomMultiAccount.accounts', '账号列表')} ({accounts.length})
                    </span>
                    <button
                        onClick={handleAdd}
                        style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '4px',
                            padding: '4px 10px',
                            fontSize: '11px',
                            background: 'var(--accent-primary, #5e6ad2)',
                            color: '#fff',
                            border: 'none',
                            borderRadius: '4px',
                            cursor: 'pointer',
                        }}
                    >
                        {IconPlus}
                        {t('wecomMultiAccount.addAccount', '添加账号')}
                    </button>
                </div>

                {accounts.map(account => {
                    const enabledModes = getEnabledModes(account);
                    return (
                        <div
                            key={account.id}
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                padding: '8px 10px',
                                background: 'var(--bg-secondary)',
                                borderRadius: '6px',
                                marginBottom: '6px',
                                fontSize: '12px',
                            }}
                        >
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1, minWidth: 0 }}>
                                <span style={{
                                    padding: '2px 6px',
                                    background: 'var(--accent-subtle)',
                                    borderRadius: '4px',
                                    fontWeight: 500,
                                    fontSize: '12px',
                                    whiteSpace: 'nowrap',
                                }}>
                                    {account.nickname || account.id}
                                </span>
                                <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                                    {enabledModes.map(mode => (
                                        <span key={mode} style={{
                                            padding: '1px 6px',
                                            background: 'var(--bg-tertiary, #e5e7eb)',
                                            borderRadius: '3px',
                                            fontSize: '10px',
                                            color: 'var(--text-secondary)',
                                        }}>
                                            {mode}
                                        </span>
                                    ))}
                                </div>
                                {account.bot?.id && (
                                    <span style={{ color: 'var(--text-tertiary)', fontSize: '11px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                        Bot: {account.bot.id.substring(0, 12)}...
                                    </span>
                                )}
                            </div>
                            <div style={{ display: 'flex', gap: '4px' }}>
                                <button
                                    onClick={() => handleEdit(account)}
                                    style={{
                                        padding: '4px 8px',
                                        background: 'transparent',
                                        border: '1px solid var(--border-color)',
                                        borderRadius: '4px',
                                        cursor: 'pointer',
                                        color: 'var(--text-secondary)',
                                    }}
                                    title={t('wecomMultiAccount.editAccount', '编辑账号')}
                                >
                                    {IconEdit}
                                </button>
                                <button
                                    onClick={() => handleDelete(account.id)}
                                    style={{
                                        padding: '4px 8px',
                                        background: 'transparent',
                                        border: '1px solid var(--border-color)',
                                        borderRadius: '4px',
                                        cursor: 'pointer',
                                        color: 'var(--danger-color, #dc2626)',
                                    }}
                                    title={t('wecomMultiAccount.deleteAccount', '删除账号')}
                                >
                                    {IconTrash}
                                </button>
                            </div>
                        </div>
                    );
                })}
            </div>

            {/* Webhook URLs - Only show for webhook-enabled accounts */}
            {accountsWithWebhook.length > 0 && (
                <details style={{ fontSize: '12px' }}>
                    <summary style={{ cursor: 'pointer', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                        {t('wecomMultiAccount.webhookUrls', 'Webhook URLs')} ({accountsWithWebhook.length} {t('wecomMultiAccount.accounts', '账号')})
                    </summary>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', paddingLeft: '8px' }}>
                        {accountsWithWebhook.map(account => (
                            <div key={account.id} style={{ background: 'var(--bg-secondary)', padding: '8px', borderRadius: '6px' }}>
                                <div style={{ fontWeight: 500, marginBottom: '6px' }}>{account.nickname || account.id}</div>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '11px', fontFamily: 'var(--font-mono)' }}>
                                    {/* Bot Webhook URL */}
                                    {account.bot_webhook_enabled && (
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                            <span style={{ color: 'var(--text-tertiary)', minWidth: '60px' }}>机器人:</span>
                                            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                                {getAccountWebhookUrl(account.id, 'bot') || '-'}
                                            </span>
                                            {getAccountWebhookUrl(account.id, 'bot') && (
                                                <LinearCopyButton
                                                    textToCopy={getAccountWebhookUrl(account.id, 'bot')!}
                                                    iconOnly={true}
                                                    style={{ padding: '2px 4px' }}
                                                />
                                            )}
                                        </div>
                                    )}
                                    {/* Agent Webhook URL */}
                                    {account.agent_webhook_enabled && (
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                            <span style={{ color: 'var(--text-tertiary)', minWidth: '60px' }}>企业应用:</span>
                                            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                                {getAccountWebhookUrl(account.id, 'agent') || '-'}
                                            </span>
                                            {getAccountWebhookUrl(account.id, 'agent') && (
                                                <LinearCopyButton
                                                    textToCopy={getAccountWebhookUrl(account.id, 'agent')!}
                                                    iconOnly={true}
                                                    style={{ padding: '2px 4px' }}
                                                />
                                            )}
                                        </div>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </details>
            )}

            {/* Modal */}
            {isModalOpen && (
                <div style={{
                    position: 'fixed',
                    top: 0,
                    left: 0,
                    right: 0,
                    bottom: 0,
                    background: 'rgba(0,0,0,0.5)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    zIndex: 1000,
                }}>
                    <div style={{
                        background: 'var(--bg-primary)',
                        borderRadius: '8px',
                        padding: '20px',
                        width: '90%',
                        maxWidth: '560px',
                        maxHeight: '90vh',
                        overflow: 'auto',
                    }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                            <h3 style={{ margin: 0, fontSize: '16px' }}>
                                {editingAccount 
                                    ? t('wecomMultiAccount.editAccount', '编辑账号')
                                    : t('wecomMultiAccount.addAccount', '添加账号')
                                }
                            </h3>
                            <button
                                onClick={() => { setIsModalOpen(false); setEditingAccount(null); setSavedAccountId(null); setPreviewAccountId(null); resetFormData(); }}
                                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
                            >
                                {IconX}
                            </button>
                        </div>

                        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                            {/* Nickname */}
                            <div>
                                <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>
                                    {t('wecomMultiAccount.nickname', '账号昵称')} *
                                </label>
                                <input
                                    type="text"
                                    value={formData.nickname || ''}
                                    onChange={e => setFormData({ ...formData, nickname: e.target.value })}
                                    placeholder={t('wecomMultiAccount.nicknamePlaceholder', '例如：客服机器人、销售助手')}
                                    style={{
                                        width: '100%',
                                        padding: '8px 10px',
                                        fontSize: '13px',
                                        border: `1px solid ${errors.nickname ? 'var(--danger-color, #dc2626)' : 'var(--border-color)'}`,
                                        borderRadius: '4px',
                                        background: 'var(--bg-primary)',
                                        color: 'var(--text-primary)',
                                    }}
                                />
                                {errors.nickname && <span style={{ fontSize: '11px', color: 'var(--danger-color, #dc2626)', marginTop: '4px', display: 'block' }}>{errors.nickname}</span>}
                            </div>

                            {/* Show Account ID when editing */}
                            {editingAccount && (
                                <div>
                                    <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px', color: 'var(--text-tertiary)' }}>
                                        {t('wecomMultiAccount.accountId', '账号ID')}
                                    </label>
                                    <div style={{ fontSize: '12px', fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '4px' }}>
                                        {editingAccount.id}
                                    </div>
                                </div>
                            )}

                            {/* Communication Modes Section - Radio/Switch Style */}
                            <div>
                                <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '8px' }}>
                                    {t('wecomMultiAccount.communicationModes', '通信模式')} *
                                </label>
                                
                                {/* Segmented control style mode selector */}
                                <div style={{ display: 'flex', gap: '4px', background: 'var(--bg-secondary)', padding: '4px', borderRadius: '6px', marginBottom: '12px' }}>
                                    <button
                                        type="button"
                                        onClick={() => handleModeChange('bot_websocket')}
                                        style={{
                                            flex: 1,
                                            padding: '8px 12px',
                                            fontSize: '12px',
                                            fontWeight: 500,
                                            border: 'none',
                                            borderRadius: '4px',
                                            cursor: 'pointer',
                                            transition: 'all 0.15s ease',
                                            background: connectionMode === 'bot_websocket' ? 'var(--accent-primary, #5e6ad2)' : 'transparent',
                                            color: connectionMode === 'bot_websocket' ? '#fff' : 'var(--text-secondary)',
                                        }}
                                    >
                                        长连接
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => handleModeChange('bot_webhook')}
                                        style={{
                                            flex: 1,
                                            padding: '8px 12px',
                                            fontSize: '12px',
                                            fontWeight: 500,
                                            border: 'none',
                                            borderRadius: '4px',
                                            cursor: 'pointer',
                                            transition: 'all 0.15s ease',
                                            background: connectionMode === 'bot_webhook' ? 'var(--accent-primary, #5e6ad2)' : 'transparent',
                                            color: connectionMode === 'bot_webhook' ? '#fff' : 'var(--text-secondary)',
                                        }}
                                    >
                                        短链接
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => handleModeChange('agent_webhook')}
                                        style={{
                                            flex: 1,
                                            padding: '8px 12px',
                                            fontSize: '12px',
                                            fontWeight: 500,
                                            border: 'none',
                                            borderRadius: '4px',
                                            cursor: 'pointer',
                                            transition: 'all 0.15s ease',
                                            background: connectionMode === 'agent_webhook' ? 'var(--accent-primary, #5e6ad2)' : 'transparent',
                                            color: connectionMode === 'agent_webhook' ? '#fff' : 'var(--text-secondary)',
                                        }}
                                    >
                                        企业应用
                                    </button>
                                </div>
                                
                                {/* Mode description */}
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>
                                    {connectionMode === 'bot_websocket' && '通过 WebSocket 自动接收消息，需要 Bot ID + Secret'}
                                    {connectionMode === 'bot_webhook' && '通过 Webhook 接收消息，只需 Token + EncodingAESKey'}
                                    {connectionMode === 'agent_webhook' && '企业内部应用，通过 Webhook 接收消息'}
                                </div>
                            </div>

                            {/* Bot Config Section - Show when bot mode selected */}
                            {(connectionMode === 'bot_websocket' || connectionMode === 'bot_webhook') && (
                                <div style={{ 
                                    padding: '12px', 
                                    background: 'var(--bg-secondary)', 
                                    borderRadius: '6px',
                                    border: '1px solid var(--border-color)',
                                }}>
                                    <div style={{ fontWeight: 500, fontSize: '13px', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                        <span style={{ padding: '2px 6px', background: 'var(--accent-subtle)', borderRadius: '3px', fontSize: '11px' }}>智能机器人</span>
                                        配置
                                    </div>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                        {/* WebSocket mode: Bot ID and Bot Secret are required */}
                                        {connectionMode === 'bot_websocket' && (
                                            <>
                                                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                                                    <div>
                                                        <label style={{ fontSize: '11px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>
                                                            Bot ID *
                                                        </label>
                                                        <input
                                                            type="text"
                                                            value={formData.bot?.id || ''}
                                                            onChange={e => setFormData({ ...formData, bot: { ...(formData.bot || { id: '', secret: '', token: '', encoding_aes_key: '' }), id: e.target.value } })}
                                                            placeholder="aibXXXXXXXXXXXX"
                                                            style={{
                                                                width: '100%',
                                                                padding: '6px 8px',
                                                                fontSize: '12px',
                                                                border: `1px solid ${errors['bot.id'] ? 'var(--danger-color, #dc2626)' : 'var(--border-color)'}`,
                                                                borderRadius: '4px',
                                                                background: 'var(--bg-primary)',
                                                                color: 'var(--text-primary)',
                                                            }}
                                                        />
                                                        {errors['bot.id'] && <span style={{ fontSize: '10px', color: 'var(--danger-color, #dc2626)' }}>{errors['bot.id']}</span>}
                                                    </div>
                                                    <div>
                                                        <label style={{ fontSize: '11px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>
                                                            Bot Secret *
                                                        </label>
                                                        <input
                                                            type="password"
                                                            value={formData.bot?.secret || ''}
                                                            onChange={e => setFormData({ ...formData, bot: { ...(formData.bot || { id: '', secret: '', token: '', encoding_aes_key: '' }), secret: e.target.value } })}
                                                            style={{
                                                                width: '100%',
                                                                padding: '6px 8px',
                                                                fontSize: '12px',
                                                                border: `1px solid ${errors['bot.secret'] ? 'var(--danger-color, #dc2626)' : 'var(--border-color)'}`,
                                                                borderRadius: '4px',
                                                                background: 'var(--bg-primary)',
                                                                color: 'var(--text-primary)',
                                                            }}
                                                        />
                                                        {errors['bot.secret'] && <span style={{ fontSize: '10px', color: 'var(--danger-color, #dc2626)' }}>{errors['bot.secret']}</span>}
                                                    </div>
                                                </div>
                                            </>
                                        )}

                                        {/* Webhook mode: only Token and EncodingAESKey required */}
                                        {connectionMode === 'bot_webhook' && (
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                    回调配置（从企业微信机器人配置页面获取）
                                                </div>
                                                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                                                    <div>
                                                        <label style={{ fontSize: '11px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>
                                                            Token *
                                                        </label>
                                                        <input
                                                            type="text"
                                                            value={formData.bot?.token || ''}
                                                            onChange={e => setFormData({ ...formData, bot: { ...(formData.bot || { id: '', secret: '', token: '', encoding_aes_key: '' }), token: e.target.value } })}
                                                            placeholder="用于验证回调URL"
                                                            style={{
                                                                width: '100%',
                                                                padding: '6px 8px',
                                                                fontSize: '12px',
                                                                border: `1px solid ${errors['bot.token'] ? 'var(--danger-color, #dc2626)' : 'var(--border-color)'}`,
                                                                borderRadius: '4px',
                                                                background: 'var(--bg-primary)',
                                                                color: 'var(--text-primary)',
                                                            }}
                                                        />
                                                        {errors['bot.token'] && <span style={{ fontSize: '10px', color: 'var(--danger-color, #dc2626)' }}>{errors['bot.token']}</span>}
                                                    </div>
                                                    <div>
                                                        <label style={{ fontSize: '11px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>
                                                            EncodingAESKey *
                                                        </label>
                                                        <input
                                                            type="text"
                                                            value={formData.bot?.encoding_aes_key || ''}
                                                            onChange={e => setFormData({ ...formData, bot: { ...(formData.bot || { id: '', secret: '', token: '', encoding_aes_key: '' }), encoding_aes_key: e.target.value } })}
                                                            placeholder="消息加解密密钥"
                                                            style={{
                                                                width: '100%',
                                                                padding: '6px 8px',
                                                                fontSize: '12px',
                                                                border: `1px solid ${errors['bot.encoding_aes_key'] ? 'var(--danger-color, #dc2626)' : 'var(--border-color)'}`,
                                                                borderRadius: '4px',
                                                                background: 'var(--bg-primary)',
                                                                color: 'var(--text-primary)',
                                                            }}
                                                        />
                                                        {errors['bot.encoding_aes_key'] && <span style={{ fontSize: '10px', color: 'var(--danger-color, #dc2626)' }}>{errors['bot.encoding_aes_key']}</span>}
                                                    </div>
                                                </div>
                                                {/* Test button - always show for webhook mode when fields are filled */}
                                                {agentId && formData.bot?.token && formData.bot?.encoding_aes_key && (
                                                    <button
                                                        onClick={handleTestWebhook}
                                                        style={{
                                                            padding: '6px 12px',
                                                            fontSize: '12px',
                                                            background: 'var(--accent-primary, #5e6ad2)',
                                                            color: '#fff',
                                                            border: 'none',
                                                            borderRadius: '4px',
                                                            cursor: 'pointer',
                                                            display: 'flex',
                                                            alignItems: 'center',
                                                            gap: '4px',
                                                        }}
                                                    >
                                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                            <path d="M22 11.08V12a10 10 0 11-5.93-9.14"/>
                                                            <polyline points="22 4 12 14.01 9 11.01"/>
                                                        </svg>
                                                        保存并生成 URL
                                                    </button>
                                                )}
                                                {/* Webhook URL display - show when account ID is available */}
                                                {agentId && getCurrentAccountId() && formData.bot?.token && formData.bot?.encoding_aes_key && (
                                                    <div style={{ background: 'var(--bg-tertiary, #f3f4f6)', padding: '8px', borderRadius: '4px' }}>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '4px' }}>
                                                            Webhook URL（复制到企业微信配置页面）：
                                                        </div>
                                                        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                                            <code style={{ flex: 1, fontSize: '11px', wordBreak: 'break-all', color: 'var(--text-primary)' }}>
                                                                {getAccountWebhookUrl(getCurrentAccountId() || '', 'bot') || `${window.location.origin}/api/channel/wecom/${agentId}/bot/${getCurrentAccountId()}/webhook`}
                                                            </code>
                                                            <LinearCopyButton
                                                                textToCopy={getAccountWebhookUrl(getCurrentAccountId() || '', 'bot') || `${window.location.origin}/api/channel/wecom/${agentId}/bot/${getCurrentAccountId()}/webhook`}
                                                                iconOnly={true}
                                                                style={{ padding: '2px 4px' }}
                                                            />
                                                        </div>
                                                    </div>
                                                )}
                                                {/* Bot ID - filled after verification (optional for webhook mode) */}
                                                <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid var(--border-color)' }}>
                                                    <label style={{ fontSize: '11px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>
                                                        Bot ID（验证成功后填入）
                                                    </label>
                                                    <input
                                                        type="text"
                                                        value={formData.bot?.id || ''}
                                                        onChange={e => setFormData({ ...formData, bot: { ...(formData.bot || { id: '', secret: '', token: '', encoding_aes_key: '' }), id: e.target.value } })}
                                                        placeholder="企业微信验证成功后返回的 Bot ID"
                                                        style={{
                                                            width: '100%',
                                                            padding: '6px 8px',
                                                            fontSize: '12px',
                                                            border: '1px solid var(--border-color)',
                                                            borderRadius: '4px',
                                                            background: 'var(--bg-primary)',
                                                            color: 'var(--text-primary)',
                                                        }}
                                                    />
                                                    <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                                        在企业微信完成 URL 验证后，将返回的 Bot ID 填入此处
                                                    </div>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )}

                            {/* Agent Config Section - Show when agent mode selected */}
                            {connectionMode === 'agent_webhook' && (
                                <div style={{ 
                                    padding: '12px', 
                                    background: 'var(--bg-secondary)', 
                                    borderRadius: '6px',
                                    border: '1px solid var(--border-color)',
                                }}>
                                    <div style={{ fontWeight: 500, fontSize: '13px', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                        <span style={{ padding: '2px 6px', background: 'var(--accent-subtle)', borderRadius: '3px', fontSize: '11px' }}>企业应用</span>
                                        配置
                                    </div>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                                            <div>
                                                <label style={{ fontSize: '11px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>
                                                    CorpID (企业ID) *
                                                </label>
                                                <input
                                                    type="text"
                                                    value={formData.agent?.corp_id || ''}
                                                    onChange={e => setFormData({ ...formData, agent: { ...(formData.agent || { corp_id: '', agent_id: '', secret: '', token: '', encoding_aes_key: '' }), corp_id: e.target.value } })}
                                                    style={{
                                                        width: '100%',
                                                        padding: '6px 8px',
                                                        fontSize: '12px',
                                                        border: `1px solid ${errors['agent.corp_id'] ? 'var(--danger-color, #dc2626)' : 'var(--border-color)'}`,
                                                        borderRadius: '4px',
                                                        background: 'var(--bg-primary)',
                                                        color: 'var(--text-primary)',
                                                    }}
                                                />
                                                {errors['agent.corp_id'] && <span style={{ fontSize: '10px', color: 'var(--danger-color, #dc2626)' }}>{errors['agent.corp_id']}</span>}
                                            </div>
                                            <div>
                                                <label style={{ fontSize: '11px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>
                                                    AgentID (应用ID) *
                                                </label>
                                                <input
                                                    type="text"
                                                    value={formData.agent?.agent_id || ''}
                                                    onChange={e => setFormData({ ...formData, agent: { ...(formData.agent || { corp_id: '', agent_id: '', secret: '', token: '', encoding_aes_key: '' }), agent_id: e.target.value } })}
                                                    style={{
                                                        width: '100%',
                                                        padding: '6px 8px',
                                                        fontSize: '12px',
                                                        border: `1px solid ${errors['agent.agent_id'] ? 'var(--danger-color, #dc2626)' : 'var(--border-color)'}`,
                                                        borderRadius: '4px',
                                                        background: 'var(--bg-primary)',
                                                        color: 'var(--text-primary)',
                                                    }}
                                                />
                                                {errors['agent.agent_id'] && <span style={{ fontSize: '10px', color: 'var(--danger-color, #dc2626)' }}>{errors['agent.agent_id']}</span>}
                                            </div>
                                        </div>
                                        <div>
                                            <label style={{ fontSize: '11px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>
                                                应用 Secret *
                                            </label>
                                            <input
                                                type="password"
                                                value={formData.agent?.secret || ''}
                                                onChange={e => setFormData({ ...formData, agent: { ...(formData.agent || { corp_id: '', agent_id: '', secret: '', token: '', encoding_aes_key: '' }), secret: e.target.value } })}
                                                style={{
                                                    width: '100%',
                                                    padding: '6px 8px',
                                                    fontSize: '12px',
                                                    border: `1px solid ${errors['agent.secret'] ? 'var(--danger-color, #dc2626)' : 'var(--border-color)'}`,
                                                    borderRadius: '4px',
                                                    background: 'var(--bg-primary)',
                                                    color: 'var(--text-primary)',
                                                }}
                                            />
                                            {errors['agent.secret'] && <span style={{ fontSize: '10px', color: 'var(--danger-color, #dc2626)' }}>{errors['agent.secret']}</span>}
                                        </div>
                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                                            <div>
                                                <label style={{ fontSize: '11px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>
                                                    Token *
                                                </label>
                                                <input
                                                    type="text"
                                                    value={formData.agent?.token || ''}
                                                    onChange={e => setFormData({ ...formData, agent: { ...(formData.agent || { corp_id: '', agent_id: '', secret: '', token: '', encoding_aes_key: '' }), token: e.target.value } })}
                                                    style={{
                                                        width: '100%',
                                                        padding: '6px 8px',
                                                        fontSize: '12px',
                                                        border: `1px solid ${errors['agent.token'] ? 'var(--danger-color, #dc2626)' : 'var(--border-color)'}`,
                                                        borderRadius: '4px',
                                                        background: 'var(--bg-primary)',
                                                        color: 'var(--text-primary)',
                                                    }}
                                                />
                                                {errors['agent.token'] && <span style={{ fontSize: '10px', color: 'var(--danger-color, #dc2626)' }}>{errors['agent.token']}</span>}
                                            </div>
                                            <div>
                                                <label style={{ fontSize: '11px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>
                                                    EncodingAESKey *
                                                </label>
                                                <input
                                                    type="text"
                                                    value={formData.agent?.encoding_aes_key || ''}
                                                    onChange={e => setFormData({ ...formData, agent: { ...(formData.agent || { corp_id: '', agent_id: '', secret: '', token: '', encoding_aes_key: '' }), encoding_aes_key: e.target.value } })}
                                                    style={{
                                                        width: '100%',
                                                        padding: '6px 8px',
                                                        fontSize: '12px',
                                                        border: `1px solid ${errors['agent.encoding_aes_key'] ? 'var(--danger-color, #dc2626)' : 'var(--border-color)'}`,
                                                        borderRadius: '4px',
                                                        background: 'var(--bg-primary)',
                                                        color: 'var(--text-primary)',
                                                    }}
                                                />
                                                {errors['agent.encoding_aes_key'] && <span style={{ fontSize: '10px', color: 'var(--danger-color, #dc2626)' }}>{errors['agent.encoding_aes_key']}</span>}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>

                        <div style={{ display: 'flex', gap: '8px', marginTop: '20px', justifyContent: 'flex-end' }}>
                            <button
                                onClick={() => { setIsModalOpen(false); setEditingAccount(null); setSavedAccountId(null); setPreviewAccountId(null); resetFormData(); }}
                                style={{
                                    padding: '8px 16px',
                                    fontSize: '13px',
                                    background: 'var(--bg-secondary)',
                                    border: '1px solid var(--border-color)',
                                    borderRadius: '4px',
                                    cursor: 'pointer',
                                    color: 'var(--text-primary)',
                                }}
                            >
                                {t('common.cancel', '取消')}
                            </button>
                            <button
                                onClick={handleSave}
                                style={{
                                    padding: '8px 16px',
                                    fontSize: '13px',
                                    background: 'var(--accent-primary, #5e6ad2)',
                                    color: '#fff',
                                    border: 'none',
                                    borderRadius: '4px',
                                    cursor: 'pointer',
                                }}
                            >
                                {t('common.save', '保存')}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
