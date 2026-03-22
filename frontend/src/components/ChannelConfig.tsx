import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { channelApi } from '../services/api';

// ─── Shared fetchAuth (same as AgentDetail) ─────────────
function fetchAuth<T>(url: string, options?: RequestInit): Promise<T> {
    const token = localStorage.getItem('token');
    return fetch(`/api${url}`, {
        ...options,
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    }).then(r => r.json());
}

// ─── Types ──────────────────────────────────────────────
interface ChannelConfigProps {
    mode: 'create' | 'edit';
    agentId?: string;          // required for edit mode
    canManage?: boolean;       // edit mode: whether current user can manage
    values?: Record<string, string>;
    onChange?: (values: Record<string, string>) => void;
}

interface ChannelField {
    key: string;
    label: string;
    placeholder?: string;
    type?: 'text' | 'password';
    required?: boolean;
}

interface GuideConfig {
    prefix: string;           // i18n key prefix e.g. 'channelGuide.slack'
    steps: number;
    noteKey?: string;         // override note key
}

interface ChannelDef {
    id: string;
    icon: ReactNode;
    nameKey: string;
    nameFallback: string;
    desc: string;
    // API endpoint slug: e.g. 'slack-channel', 'discord-channel'
    apiSlug?: string;
    // Feishu uses channelApi instead of fetchAuth
    useChannelApi?: boolean;
    // Fields for configuration form
    fields: ChannelField[];
    // Setup guide
    guide: GuideConfig;
    // Whether this channel supports connection_mode toggle (feishu, wecom)
    connectionMode?: boolean;
    // WebSocket guide config (when connection_mode === 'websocket')
    wsGuide?: GuideConfig;
    // Whether this channel shows feishu permission JSON block
    showPermJson?: boolean;
    // Webhook URL label
    webhookLabel?: string;
    // Channels only shown in edit mode (not in create wizard)
    editOnly?: boolean;
    // Custom fields for websocket mode (wecom)
    wsFields?: ChannelField[];
    // Atlassian-specific test connection feature
    hasTestConnection?: boolean;
}

// ─── SVG Icons ──────────────────────────────────────────
const SlackIcon = <img src="/slack.png" alt="Slack" width="20" height="20" style={{ borderRadius: '4px' }} />;

const DiscordIcon = <img src="/discord.png" alt="Discord" width="20" height="20" style={{ borderRadius: '4px' }} />;

const FeishuIcon = <img src="/feishu.png" alt="Feishu" width="20" height="20" style={{ borderRadius: '4px' }} />;

const TeamsIcon = <img src="/teams.png" alt="Teams" width="20" height="20" style={{ borderRadius: '4px' }} />;

const WeComIcon = <img src="/wecom.png" alt="WeCom" width="20" height="20" style={{ borderRadius: '4px' }} />;

const DingTalkIcon = <img src="/dingtalk.png" alt="DingTalk" width="20" height="20" style={{ borderRadius: '4px' }} />;

const AtlassianIcon = <img src="/atlassian.png" alt="Atlassian" width="20" height="20" style={{ borderRadius: '4px' }} />;

// Eye icons for password toggle
const EyeOpen = <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>;
const EyeClosed = <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19" /><line x1="1" y1="1" x2="23" y2="23" /></svg>;

// ─── Channel Registry ───────────────────────────────────
const CHANNEL_REGISTRY: ChannelDef[] = [
    {
        id: 'slack',
        icon: SlackIcon,
        nameKey: 'common.channels.slack',
        nameFallback: 'Slack',
        desc: 'Slack Bot',
        apiSlug: 'slack-channel',
        fields: [
            { key: 'bot_token', label: 'Bot Token', placeholder: 'xoxb-...', type: 'password', required: true },
            { key: 'signing_secret', label: 'Signing Secret', type: 'password', required: true },
        ],
        guide: { prefix: 'channelGuide.slack', steps: 8 },
        webhookLabel: 'Webhook URL (Event Subscriptions URL)',
    },
    {
        id: 'discord',
        icon: DiscordIcon,
        nameKey: 'common.channels.discord',
        nameFallback: 'Discord',
        desc: 'Gateway / Webhook',
        apiSlug: 'discord-channel',
        connectionMode: true,
        fields: [
            { key: 'application_id', label: 'Application ID', placeholder: '1234567890', required: true },
            { key: 'bot_token', label: 'Bot Token', type: 'password', required: true },
            { key: 'public_key', label: 'Public Key', required: true },
        ],
        wsFields: [
            { key: 'bot_token', label: 'Bot Token', type: 'password', required: true },
        ],
        guide: { prefix: 'channelGuide.discord', steps: 7 },
        wsGuide: { prefix: 'channelGuide.discord', steps: 4 },
        webhookLabel: 'Interactions Endpoint URL',
    },
    {
        id: 'teams',
        icon: TeamsIcon,
        nameKey: 'common.channels.teams',
        nameFallback: 'Microsoft Teams',
        desc: 'Teams Bot',
        apiSlug: 'teams-channel',
        fields: [
            { key: 'app_id', label: 'App ID (Client ID)', placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', required: true },
            { key: 'app_secret', label: 'App Secret (Client Secret)', type: 'password', required: true },
            { key: 'tenant_id', label: 'channelGuide.teams.tenantId', placeholder: 'channelGuide.teams.tenantIdPlaceholder' },
        ],
        guide: { prefix: 'channelGuide.teams', steps: 5 },
        webhookLabel: 'Messaging Endpoint URL',
    },
    {
        id: 'feishu',
        icon: FeishuIcon,
        nameKey: 'agent.settings.channel.feishu',
        nameFallback: 'Feishu / Lark',
        desc: 'Feishu / Lark',
        useChannelApi: true,
        connectionMode: true,
        fields: [
            { key: 'app_id', label: 'App ID', placeholder: 'cli_xxxxxxxxxxxxxxxx', required: true },
            { key: 'app_secret', label: 'App Secret', type: 'password', required: true },
            { key: 'encrypt_key', label: 'Encrypt Key', type: 'password' },
        ],
        guide: { prefix: 'channelGuide.feishu', steps: 8 },
        wsGuide: { prefix: 'channelGuide.feishu', steps: 8 },
        showPermJson: true,
        webhookLabel: 'Webhook URL',
    },
    {
        id: 'wecom',
        icon: WeComIcon,
        nameKey: 'common.channels.wecom',
        nameFallback: 'WeCom',
        desc: 'WebSocket / Webhook',
        apiSlug: 'wecom-channel',
        connectionMode: true,
        fields: [
            { key: 'corp_id', label: 'CorpID', required: true },
            { key: 'wecom_agent_id', label: 'AgentID', required: true },
            { key: 'secret', label: 'Secret', type: 'password', required: true },
            { key: 'token', label: 'Token', required: true },
            { key: 'encoding_aes_key', label: 'EncodingAESKey', required: true },
        ],
        wsFields: [
            { key: 'bot_id', label: 'Bot ID', placeholder: 'aibXXXXXXXXXXXX', required: true },
            { key: 'bot_secret', label: 'Bot Secret', type: 'password', required: true },
        ],
        guide: { prefix: 'channelGuide.wecom', steps: 6 },
        wsGuide: { prefix: 'channelGuide.wecom', steps: 6 },
        webhookLabel: 'Webhook URL',
    },
    {
        id: 'dingtalk',
        icon: DingTalkIcon,
        nameKey: 'common.channels.dingtalk',
        nameFallback: 'DingTalk',
        desc: 'Stream Mode',
        apiSlug: 'dingtalk-channel',
        fields: [
            { key: 'app_key', label: 'AppKey', type: 'password', required: true },
            { key: 'app_secret', label: 'AppSecret', type: 'password', required: true },
        ],
        guide: { prefix: 'channelGuide.dingtalk', steps: 6 },
    },
    {
        id: 'atlassian',
        icon: AtlassianIcon,
        nameKey: 'common.channels.atlassian',
        nameFallback: 'Atlassian',
        desc: 'Jira / Confluence / Compass (Rovo MCP)',
        apiSlug: 'atlassian-channel',
        hasTestConnection: true,
        fields: [
            { key: 'api_key', label: 'API Key', type: 'password', required: true },
            { key: 'cloud_id', label: 'Cloud ID', placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx' },
        ],
        guide: { prefix: 'channelGuide.atlassian', steps: 5 },
    },
];

// ─── Feishu Permission JSON ─────────────────────────────
const FEISHU_PERM_JSON = '{"scopes":{"tenant":["contact:contact.base:readonly","contact:user.base:readonly","contact:user.id:readonly","im:chat","im:message","im:message.group_at_msg:readonly","im:message.p2p_msg:readonly","im:message:send_as_bot","im:resource"],"user":[]}}';

const FEISHU_PERM_DISPLAY = `{
  "scopes": {
    "tenant": [
      "contact:contact.base:readonly",
      "contact:user.base:readonly",
      "contact:user.id:readonly",
      "im:chat",
      "im:message",
      "im:message.group_at_msg:readonly",
      "im:message.p2p_msg:readonly",
      "im:message:send_as_bot",
      "im:resource"
    ],
    "user": []
  }
}`;

// ─── Copy Button helper ─────────────────────────────────
function CopyBtn({ url }: { url: string }) {
    return (
        <button title="Copy" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', marginLeft: '6px', padding: '1px 4px', cursor: 'pointer', borderRadius: '3px', border: '1px solid var(--border-color)', background: 'var(--bg-primary)', color: 'var(--text-secondary)', verticalAlign: 'middle', lineHeight: 1 }}
            onClick={() => navigator.clipboard.writeText(url)}>
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <rect x="4" y="4" width="9" height="11" rx="1.5" /><path d="M3 11H2a1 1 0 01-1-1V2a1 1 0 011-1h8a1 1 0 011 1v1" />
            </svg>
        </button>
    );
}

// ─── Main Component ─────────────────────────────────────
export default function ChannelConfig({ mode, agentId, canManage = true, values, onChange }: ChannelConfigProps) {
    const { t } = useTranslation();
    const queryClient = useQueryClient();

    // Collapsible state per channel
    const [openChannels, setOpenChannels] = useState<Record<string, boolean>>({});
    const toggleChannel = (id: string) => setOpenChannels(prev => ({ ...prev, [id]: !prev[id] }));

    // Editing state per channel (edit mode only)
    const [editingChannels, setEditingChannels] = useState<Record<string, boolean>>({});
    const setEditing = (id: string, val: boolean) => setEditingChannels(prev => ({ ...prev, [id]: val }));

    // Form state per channel (edit mode only)
    const [forms, setForms] = useState<Record<string, Record<string, string>>>({});
    const setFormField = (channelId: string, key: string, val: string) =>
        setForms(prev => ({ ...prev, [channelId]: { ...prev[channelId], [key]: val } }));
    const getForm = (channelId: string) => forms[channelId] || {};

    // Connection mode state for feishu/wecom (edit mode)
    const [connectionModes, setConnectionModes] = useState<Record<string, string>>({
        feishu: 'websocket',
        wecom: 'websocket',
        discord: 'gateway',
    });

    // Password visibility
    const [showPwds, setShowPwds] = useState<Record<string, boolean>>({});
    const togglePwd = (fieldId: string) => setShowPwds(p => ({ ...p, [fieldId]: !p[fieldId] }));

    // Atlassian test connection state
    const [atlassianTesting, setAtlassianTesting] = useState(false);
    const [atlassianTestResult, setAtlassianTestResult] = useState<{ ok: boolean; message?: string; tool_count?: number; error?: string } | null>(null);

    // ─── Edit mode: queries for each channel ────────────
    const enabled = mode === 'edit' && !!agentId;

    const { data: feishuConfig } = useQuery({
        queryKey: ['channel', agentId],
        queryFn: () => channelApi.get(agentId!),
        enabled: enabled,
    });
    const { data: feishuWebhook } = useQuery({
        queryKey: ['webhook-url', agentId],
        queryFn: () => channelApi.webhookUrl(agentId!),
        enabled: enabled,
    });
    const { data: slackConfig } = useQuery({
        queryKey: ['slack-channel', agentId],
        queryFn: () => fetchAuth<any>(`/agents/${agentId}/slack-channel`).catch(() => null),
        enabled: enabled,
    });
    const { data: slackWebhook } = useQuery({
        queryKey: ['slack-webhook-url', agentId],
        queryFn: () => fetchAuth<any>(`/agents/${agentId}/slack-channel/webhook-url`),
        enabled: enabled,
    });
    const { data: discordConfig } = useQuery({
        queryKey: ['discord-channel', agentId],
        queryFn: () => fetchAuth<any>(`/agents/${agentId}/discord-channel`).catch(() => null),
        enabled: enabled,
    });
    const { data: discordWebhook } = useQuery({
        queryKey: ['discord-webhook-url', agentId],
        queryFn: () => fetchAuth<any>(`/agents/${agentId}/discord-channel/webhook-url`),
        enabled: enabled,
    });
    const { data: teamsConfig } = useQuery({
        queryKey: ['teams-channel', agentId],
        queryFn: () => fetchAuth<any>(`/agents/${agentId}/teams-channel`).catch(() => null),
        enabled: enabled,
    });
    const { data: teamsWebhook } = useQuery({
        queryKey: ['teams-webhook-url', agentId],
        queryFn: () => fetchAuth<any>(`/agents/${agentId}/teams-channel/webhook-url`).catch(() => null),
        enabled: enabled,
    });
    const { data: dingtalkConfig } = useQuery({
        queryKey: ['dingtalk-channel', agentId],
        queryFn: () => fetchAuth<any>(`/agents/${agentId}/dingtalk-channel`).catch(() => null),
        enabled: enabled,
    });
    const { data: wecomConfig } = useQuery({
        queryKey: ['wecom-channel', agentId],
        queryFn: () => fetchAuth<any>(`/agents/${agentId}/wecom-channel`).catch(() => null),
        enabled: enabled,
    });
    const { data: wecomWebhook } = useQuery({
        queryKey: ['wecom-webhook-url', agentId],
        queryFn: () => fetchAuth<any>(`/agents/${agentId}/wecom-channel/webhook-url`),
        enabled: enabled,
    });
    const { data: atlassianConfig } = useQuery({
        queryKey: ['atlassian-channel', agentId],
        queryFn: () => fetchAuth<any>(`/agents/${agentId}/atlassian-channel`).catch(() => null),
        enabled: enabled,
    });

    // Helper: get config data for a channel
    const getConfig = (id: string): any => {
        switch (id) {
            case 'feishu': return feishuConfig;
            case 'slack': return slackConfig;
            case 'discord': return discordConfig;
            case 'teams': return teamsConfig;
            case 'dingtalk': return dingtalkConfig;
            case 'wecom': return wecomConfig;
            case 'atlassian': return atlassianConfig;
            default: return null;
        }
    };

    // Helper: get webhook data for a channel
    const getWebhook = (id: string): any => {
        switch (id) {
            case 'feishu': return feishuWebhook;
            case 'slack': return slackWebhook;
            case 'discord': return discordWebhook;
            case 'teams': return teamsWebhook;
            case 'wecom': return wecomWebhook;
            default: return null;
        }
    };

    // ─── Edit mode: mutations ───────────────────────────
    const saveMutation = useMutation({
        mutationFn: ({ ch, data }: { ch: ChannelDef; data: any }) => {
            if (ch.useChannelApi) {
                return channelApi.create(agentId!, data);
            }
            return fetchAuth(`/agents/${agentId}/${ch.apiSlug}`, { method: 'POST', body: JSON.stringify(data) });
        },
        onSuccess: (_d, { ch }) => {
            const keys = ch.useChannelApi
                ? [['channel', agentId]]
                : [[`${ch.apiSlug}`, agentId], [`${ch.id}-webhook-url`, agentId]];
            keys.forEach(k => queryClient.invalidateQueries({ queryKey: k }));
            // Reset form
            setForms(prev => ({ ...prev, [ch.id]: {} }));
            setEditing(ch.id, false);
        },
    });

    const deleteMutation = useMutation({
        mutationFn: ({ ch }: { ch: ChannelDef }) => {
            if (ch.useChannelApi) {
                return channelApi.delete(agentId!);
            }
            return fetchAuth(`/agents/${agentId}/${ch.apiSlug}`, { method: 'DELETE' });
        },
        onSuccess: (_d, { ch }) => {
            const keys = ch.useChannelApi
                ? [['channel', agentId]]
                : [[`${ch.apiSlug}`, agentId]];
            keys.forEach(k => queryClient.invalidateQueries({ queryKey: k }));
            if (ch.id === 'atlassian') setAtlassianTestResult(null);
        },
    });

    const testAtlassian = async () => {
        setAtlassianTesting(true);
        setAtlassianTestResult(null);
        try {
            const res = await fetchAuth<any>(`/agents/${agentId}/atlassian-channel/test`, { method: 'POST' });
            setAtlassianTestResult(res);
        } catch (e: any) {
            setAtlassianTestResult({ ok: false, error: String(e) });
        }
        setAtlassianTesting(false);
    };

    // ─── Build save payload for a channel ───────────────
    const buildPayload = (ch: ChannelDef, form: Record<string, string>) => {
        if (ch.id === 'feishu') {
            return {
                channel_type: 'feishu',
                app_id: form.app_id,
                app_secret: form.app_secret,
                encrypt_key: form.encrypt_key || undefined,
                extra_config: { connection_mode: connectionModes.feishu || 'websocket' },
            };
        }
        if (ch.id === 'wecom') {
            const connMode = connectionModes.wecom || 'websocket';
            if (connMode === 'websocket') {
                return { connection_mode: 'websocket', bot_id: form.bot_id, bot_secret: form.bot_secret };
            }
            return { ...form, connection_mode: 'webhook' };
        }
        if (ch.id === 'discord') {
            const connMode = connectionModes.discord || 'gateway';
            if (connMode === 'websocket') {
                return { bot_token: form.bot_token, connection_mode: 'gateway' };
            }
            return { ...form, connection_mode: 'webhook' };
        }
        // Generic channels
        return form;
    };

    // ─── Render guide steps ─────────────────────────────
    const renderGuide = (guide: GuideConfig, isWs: boolean, ch: ChannelDef) => {
        const prefix = isWs && ch.wsGuide ? `${ch.wsGuide.prefix}.ws_step` : `${guide.prefix}.step`;
        const stepCount = isWs && ch.wsGuide ? ch.wsGuide.steps : guide.steps;
        const noteKey = isWs && ch.wsGuide ? `${ch.wsGuide.prefix}.ws_note` : (guide.noteKey || `${guide.prefix}.note`);

        return (
            <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span style={{ fontSize: '10px' }}>&#9654;</span> {t('channelGuide.setupGuide')}
                </summary>
                <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                    {Array.from({ length: stepCount }, (_, i) => (
                        <li key={i}>{t(`${prefix}${i + 1}`)}</li>
                    ))}
                </ol>
                {ch.showPermJson && (
                    <div style={{ margin: '8px 0', borderRadius: '6px', border: '1px solid var(--border-color)', overflow: 'hidden' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 10px', background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-color)' }}>
                            <span style={{ fontSize: '10px', color: 'var(--text-secondary)', fontWeight: 500 }}>{t('channelGuide.feishuPermJson')}</span>
                            <button type="button" style={{ fontSize: '10px', padding: '1px 7px', cursor: 'pointer', borderRadius: '3px', border: '1px solid var(--border-color)', background: 'var(--bg-primary)', color: 'var(--text-secondary)' }}
                                onClick={(e) => {
                                    const btn = e.currentTarget;
                                    navigator.clipboard.writeText(FEISHU_PERM_JSON).then(() => {
                                        const o = btn.textContent;
                                        btn.textContent = t('channelGuide.feishuPermCopied');
                                        btn.style.color = 'rgb(16,185,129)';
                                        setTimeout(() => { btn.textContent = o; btn.style.color = ''; }, 1500);
                                    });
                                }}>{t('channelGuide.feishuPermCopy')}</button>
                        </div>
                        <pre style={{ margin: 0, padding: '6px 10px', fontSize: '10px', fontFamily: 'var(--font-mono)', lineHeight: 1.5, background: 'var(--bg-primary)', color: 'var(--text-secondary)', overflowX: 'auto', userSelect: 'all' }}>{FEISHU_PERM_DISPLAY}</pre>
                    </div>
                )}
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>
                    {t(noteKey)}
                </div>
            </details>
        );
    };

    // ─── Render a password field with toggle ─────────────
    const renderField = (field: ChannelField, channelId: string, fieldValue: string, onFieldChange: (val: string) => void) => {
        const fieldId = `${channelId}_${field.key}`;
        const isSecret = field.type === 'password';
        const labelText = field.label.startsWith('channelGuide.') ? t(field.label) : field.label;
        const placeholderText = field.placeholder?.startsWith('channelGuide.') ? t(field.placeholder) : field.placeholder;

        return (
            <div key={field.key}>
                <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '4px' }}>
                    {labelText} {field.required && '*'}
                    {!field.required && <span style={{ fontWeight: 400, color: 'var(--text-tertiary)' }}> (Optional)</span>}
                </label>
                <div style={{ position: 'relative' }}>
                    <input
                        className={mode === 'edit' ? 'input' : 'form-input'}
                        type={isSecret && !showPwds[fieldId] ? 'password' : 'text'}
                        value={fieldValue}
                        onChange={e => onFieldChange(e.target.value)}
                        placeholder={placeholderText || ''}
                        style={mode === 'edit' ? { fontSize: '12px', paddingRight: isSecret ? '36px' : undefined, width: '100%' } : undefined}
                    />
                    {isSecret && (
                        <button type="button" onClick={() => togglePwd(fieldId)}
                            style={{ position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '2px', display: 'flex', alignItems: 'center' }}>
                            {showPwds[fieldId] ? EyeClosed : EyeOpen}
                        </button>
                    )}
                </div>
                {/* Tenant ID hint for Teams */}
                {channelId === 'teams' && field.key === 'tenant_id' && (
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('channelGuide.teams.tenantIdHint')}</div>
                )}
            </div>
        );
    };

    // ─── Render create mode channel card ─────────────────
    const renderCreateChannel = (ch: ChannelDef) => {
        const isOpen = openChannels[ch.id] || false;

        // Ensure we default to 'websocket' for connectionMode in create view if enabled
        const connMode = ch.connectionMode ? (connectionModes[ch.id] || 'websocket') : null;
        const isWs = connMode === 'websocket';
        
        // Active fields for current mode
        const activeFields = (ch.connectionMode && isWs && ch.wsFields) ? ch.wsFields : ch.fields;
        
        // Special Feishu field filtering (hide encrypt_key if websocket mode)
        const formFields = ch.id === 'feishu' && isWs
            ? ch.fields.filter(f => f.key !== 'encrypt_key')
            : activeFields;

        // Determine if configured (any required field has value)
        const hasValues = formFields.some(f => f.required && values?.[`${ch.id}_${f.key}`]);

        let subtitle = ch.desc;
        if (ch.connectionMode && hasValues) {
            subtitle = isWs ? 'WebSocket Mode' : 'Webhook Mode';
        }

        return (
            <div key={ch.id} style={{ border: '1px solid var(--border-default)', borderRadius: '8px', overflow: 'hidden', marginBottom: '8px' }}>
                <div
                    onClick={() => toggleChannel(ch.id)}
                    style={{
                        display: 'flex', alignItems: 'center', gap: '12px', padding: '14px',
                        cursor: 'pointer', background: isOpen ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                        borderBottom: isOpen ? '1px solid var(--border-default)' : 'none',
                    }}
                >
                    {ch.icon}
                    <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 500, fontSize: '13px' }}>{t(ch.nameKey, ch.nameFallback)}</div>
                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{subtitle}</div>
                    </div>
                    {hasValues && <span style={{ fontSize: '10px', padding: '2px 8px', borderRadius: '10px', background: 'rgba(16,185,129,0.15)', color: 'rgb(16,185,129)', fontWeight: 500 }}>{t('agent.settings.channel.configured', 'Configured')}</span>}
                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', transition: 'transform 0.2s', transform: isOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>&#9660;</span>
                </div>
                {isOpen && (
                    <div style={{ padding: '16px' }}>
                        {/* Connection Mode Toggle */}
                        {ch.connectionMode && (
                            <div style={{ marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <label style={{ fontSize: '12px', fontWeight: 500, width: '120px' }}>{t('agent.settings.channel.mode', 'Connection Mode')}</label>
                                <label style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '13px', cursor: 'pointer' }}>
                                    <input type="radio" checked={isWs} onChange={() => setConnectionModes(p => ({ ...p, [ch.id]: 'websocket' }))} />
                                    {t('agent.settings.channel.modeWs', 'WebSocket (Recommended)')}
                                </label>
                                <label style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '13px', cursor: 'pointer', marginLeft: '12px' }}>
                                    <input type="radio" checked={!isWs} onChange={() => setConnectionModes(p => ({ ...p, [ch.id]: 'webhook' }))} />
                                    {t('agent.settings.channel.modeWebhook', 'Webhook')}
                                </label>
                            </div>
                        )}
                        
                        {renderGuide(ch.guide, !!isWs, ch)}
                        
                        {formFields.map(field => (
                            <div className="form-group" key={field.key}>
                                {renderField(
                                    field, ch.id,
                                    values?.[`${ch.id}_${field.key}`] || '',
                                    (val) => {
                                        const newValues = { ...values, [`${ch.id}_${field.key}`]: val };
                                        // Save connection mode if this channel supports it
                                        if (ch.connectionMode) {
                                            newValues[`${ch.id}_connection_mode`] = connMode || 'websocket';
                                        }
                                        onChange?.(newValues);
                                    },
                                )}
                            </div>
                        ))}
                    </div>
                )}
            </div>
        );
    };

    // ─── Render edit mode channel card ───────────────────
    const renderEditChannel = (ch: ChannelDef) => {
        const config = getConfig(ch.id);
        const webhook = getWebhook(ch.id);
        const isOpen = openChannels[ch.id] || false;
        const isEditing = editingChannels[ch.id] || false;
        const form = getForm(ch.id);
        const isConfigured = ch.id === 'feishu' ? config?.is_configured : config?.is_configured;
        const connMode = connectionModes[ch.id] || 'websocket';
        const isWs = ch.connectionMode && connMode === 'websocket';
        const configConnMode = config?.extra_config?.connection_mode;

        // Determine desc subtitle based on current mode
        let subtitle = ch.desc;
        if (ch.connectionMode && config) {
            subtitle = configConnMode === 'websocket' ? 'WebSocket Mode' : ch.desc;
        }

        // Webhook URL for this channel
        const webhookUrl = webhook?.webhook_url || `${window.location.origin}/api/channel/${ch.id === 'feishu' ? 'feishu' : ch.apiSlug?.replace('-channel', '')}/${agentId}/webhook`;

        // Determine which fields to use (wecom websocket mode has different fields)
        const activeFields = (ch.connectionMode && isWs && ch.wsFields) ? ch.wsFields : ch.fields;
        // For feishu, hide encrypt_key in websocket mode (non-editing form)
        const formFields = ch.id === 'feishu' && connMode === 'webhook'
            ? ch.fields
            : ch.id === 'feishu'
                ? ch.fields.filter(f => f.key !== 'encrypt_key')
                : activeFields;

        // Check if all required fields are filled
        const allRequired = formFields.filter(f => f.required).every(f => form[f.key]);

        return (
            <div key={ch.id} style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', overflow: 'hidden', marginBottom: '12px' }}>
                {/* Header */}
                <div onClick={() => toggleChannel(ch.id)}
                    style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', cursor: 'pointer', transition: 'background 0.15s' }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-hover)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        {ch.icon}
                        <div>
                            <div style={{ fontWeight: 600, fontSize: '14px' }}>{t(ch.nameKey, ch.nameFallback)}</div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{subtitle}</div>
                        </div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        {config && <span className={`badge ${isConfigured ? 'badge-success' : 'badge-warning'}`}>{isConfigured ? t('agent.settings.channel.configured') : t('agent.settings.channel.notConfigured')}</span>}
                        <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', transition: 'transform 0.2s', transform: isOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>&#9660;</span>
                    </div>
                </div>

                {/* Body */}
                {isOpen && (
                    <div style={{ padding: '0 16px 16px', borderTop: '1px solid var(--border-subtle)' }}>
                        {!canManage ? (
                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontStyle: 'italic', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
                                Only the creator or admin can configure communication channels.
                            </div>
                        ) : isConfigured && !isEditing ? (
                            /* ── Configured view ── */
                            <div>
                                {/* Feishu websocket status */}
                                {ch.id === 'feishu' && configConnMode === 'websocket' && (
                                    <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', marginBottom: '12px' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
                                            <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#00D6B9', display: 'inline-block' }}></span>
                                            <span style={{ color: 'var(--text-secondary)' }}>Connected via WebSocket (No callback URL needed)</span>
                                        </div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>App ID: <code>{config.app_id}</code></div>
                                    </div>
                                )}
                                {ch.id === 'feishu' && configConnMode !== 'websocket' && (
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>
                                        <div style={{ marginBottom: '4px' }}>Mode: <strong>Webhook</strong></div>
                                        <div>App ID: <code>{config.app_id}</code></div>
                                    </div>
                                )}

                                {/* WeCom websocket status */}
                                {ch.id === 'wecom' && configConnMode === 'websocket' && (
                                    <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', marginBottom: '12px' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                            <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#07C160', display: 'inline-block' }}></span>
                                            <span style={{ color: 'var(--text-secondary)' }}>Connected via WebSocket (No callback URL needed)</span>
                                        </div>
                                    </div>
                                )}

                                {/* Webhook URL (non-websocket channels) */}
                                {ch.webhookLabel && !(ch.connectionMode && configConnMode === 'websocket') && ch.id !== 'dingtalk' && ch.id !== 'atlassian' && (
                                    <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', fontFamily: 'var(--font-mono)', marginBottom: '12px' }}>
                                        <div style={{ color: 'var(--text-tertiary)', marginBottom: '6px' }}>{ch.webhookLabel}</div>
                                        <div style={{ lineHeight: 1.6, wordBreak: 'break-all' }}>
                                            <span style={{ color: 'var(--accent-primary)' }}>{webhookUrl}</span>
                                            <CopyBtn url={webhookUrl} />
                                        </div>
                                    </div>
                                )}

                                {/* Discord extra hint */}
                                {ch.id === 'discord' && configConnMode !== 'gateway' && (
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>Use <code>/ask message:&lt;your question&gt;</code> to talk to this agent</div>
                                )}
                                {ch.id === 'discord' && configConnMode === 'gateway' && (
                                    <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', marginBottom: '12px' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
                                            <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#5865F2', display: 'inline-block' }}></span>
                                            <span style={{ color: 'var(--text-secondary)' }}>Connected via Gateway (No public URL needed)</span>
                                        </div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>@mention the bot or send a DM to interact</div>
                                    </div>
                                )}

                                {/* DingTalk stream mode hint */}
                                {ch.id === 'dingtalk' && (
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '8px', padding: '8px 10px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
                                        Stream mode active. No webhook URL needed.
                                    </div>
                                )}

                                {/* Atlassian status */}
                                {ch.id === 'atlassian' && (
                                    <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '10px', fontSize: '12px', marginBottom: '12px' }}>
                                        <div style={{ color: 'var(--text-tertiary)', marginBottom: '4px' }}>Status</div>
                                        <div style={{ color: 'var(--text-primary)', fontWeight: 500 }}>API Key configured — Jira / Confluence / Compass tools available</div>
                                        {config.cloud_id && <div style={{ color: 'var(--text-tertiary)', marginTop: '4px', fontSize: '11px' }}>Cloud ID: <code>{config.cloud_id}</code></div>}
                                    </div>
                                )}
                                {ch.id === 'atlassian' && atlassianTestResult && (
                                    <div style={{ padding: '8px 12px', borderRadius: '6px', fontSize: '12px', marginBottom: '10px', background: atlassianTestResult.ok ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)', border: `1px solid ${atlassianTestResult.ok ? 'rgba(16,185,129,0.25)' : 'rgba(239,68,68,0.25)'}`, color: atlassianTestResult.ok ? 'rgb(5,150,105)' : 'rgb(220,38,38)' }}>
                                        {atlassianTestResult.ok
                                            ? `${atlassianTestResult.message || `Connected — ${atlassianTestResult.tool_count} tools available`}`
                                            : `${atlassianTestResult.error}`}
                                    </div>
                                )}

                                {/* Setup guide in configured view */}
                                {renderGuide(ch.guide, !!(ch.connectionMode && configConnMode === 'websocket'), ch)}

                                {/* Action buttons */}
                                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                                    {ch.hasTestConnection && (
                                        <button className="btn btn-secondary" style={{ fontSize: '12px', padding: '4px 12px' }} onClick={testAtlassian} disabled={atlassianTesting}>
                                            {atlassianTesting ? 'Testing...' : 'Test Connection'}
                                        </button>
                                    )}
                                    <button className="btn btn-secondary" style={{ fontSize: '12px', padding: '4px 12px' }}
                                        onClick={() => {
                                            // Populate form with existing config data
                                            const prefill: Record<string, string> = {};
                                            if (ch.id === 'feishu') {
                                                prefill.app_id = config.app_id || '';
                                                prefill.app_secret = config.app_secret || '';
                                                prefill.encrypt_key = config.encrypt_key || '';
                                                setConnectionModes(prev => ({ ...prev, feishu: config.extra_config?.connection_mode || 'websocket' }));
                                            } else if (ch.id === 'wecom') {
                                                const cm = config.extra_config?.connection_mode === 'websocket' ? 'websocket' : 'webhook';
                                                setConnectionModes(prev => ({ ...prev, wecom: cm }));
                                                if (cm === 'websocket') {
                                                    prefill.bot_id = config.extra_config?.bot_id || '';
                                                    prefill.bot_secret = config.extra_config?.bot_secret || '';
                                                } else {
                                                    prefill.corp_id = config.app_id || '';
                                                    prefill.wecom_agent_id = config.extra_config?.wecom_agent_id || '';
                                                    prefill.secret = config.app_secret || '';
                                                    prefill.token = config.verification_token || '';
                                                    prefill.encoding_aes_key = config.encrypt_key || '';
                                                }
                                            } else if (ch.id === 'slack') {
                                                prefill.bot_token = config.app_secret || '';
                                                prefill.signing_secret = config.encrypt_key || '';
                                            } else if (ch.id === 'discord') {
                                                const cm = config.extra_config?.connection_mode === 'gateway' ? 'websocket' : 'webhook';
                                                setConnectionModes(prev => ({ ...prev, discord: cm }));
                                                if (cm === 'websocket') {
                                                    prefill.bot_token = config.app_secret || '';
                                                } else {
                                                    prefill.application_id = config.app_id || '';
                                                    prefill.bot_token = config.app_secret || '';
                                                    prefill.public_key = config.encrypt_key || '';
                                                }
                                            } else if (ch.id === 'teams') {
                                                prefill.app_id = config.app_id || '';
                                                prefill.app_secret = config.app_secret || '';
                                                prefill.tenant_id = config.extra_config?.tenant_id || '';
                                            } else if (ch.id === 'dingtalk') {
                                                prefill.app_key = config.app_id || '';
                                                prefill.app_secret = config.app_secret || '';
                                            } else if (ch.id === 'atlassian') {
                                                prefill.api_key = '';
                                                prefill.cloud_id = config.cloud_id || '';
                                            }
                                            setForms(prev => ({ ...prev, [ch.id]: prefill }));
                                            setEditing(ch.id, true);
                                        }}>Edit</button>
                                    <button className="btn btn-danger" style={{ fontSize: '12px', padding: '4px 12px' }}
                                        onClick={() => deleteMutation.mutate({ ch })}>Disconnect</button>
                                </div>
                            </div>
                        ) : (
                            /* ── Form view (new or editing) ── */
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                {/* Connection mode toggle (feishu, wecom) */}
                                {ch.connectionMode && (
                                    <div style={{ marginBottom: '8px' }}>
                                        <label style={{ fontSize: '12px', fontWeight: 500, display: 'block', marginBottom: '8px' }}>{t('wizard.step5.connectionMode')}</label>
                                        <div style={{ display: 'flex', gap: '16px', marginBottom: '8px' }}>
                                            <label style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                                                <input type="radio" name={`${ch.id}_connection_mode`} value="websocket" checked={connMode === 'websocket'}
                                                    onChange={() => setConnectionModes(prev => ({ ...prev, [ch.id]: 'websocket' }))} />
                                                {t('wizard.step5.modeWebsocket')}
                                            </label>
                                            <label style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                                                <input type="radio" name={`${ch.id}_connection_mode`} value="webhook" checked={connMode === 'webhook'}
                                                    onChange={() => setConnectionModes(prev => ({ ...prev, [ch.id]: 'webhook' }))} />
                                                {t('wizard.step5.modeWebhook')}
                                            </label>
                                        </div>
                                    </div>
                                )}

                                {renderGuide(ch.guide, !!isWs, ch)}

                                {/* Form fields */}
                                {formFields.map(field =>
                                    renderField(field, ch.id, form[field.key] || '', (val) => setFormField(ch.id, field.key, val))
                                )}

                                {/* Atlassian extra hints */}
                                {ch.id === 'atlassian' && (
                                    <>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '-4px' }}>
                                            Service account key starts with <code>ATSTT</code>. Personal API token: base64-encode <code>email:token</code> and prefix with <code>Basic </code>
                                        </div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>Required for multi-site setups. Find it at <code>your-site.atlassian.net/_edge/tenant_info</code></div>
                                    </>
                                )}

                                {/* Save / Cancel buttons */}
                                <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                                    <button className="btn btn-primary" style={{ fontSize: '12px', alignSelf: 'flex-start' }}
                                        onClick={() => {
                                            const payload = buildPayload(ch, form);
                                            saveMutation.mutate({ ch, data: payload });
                                        }}
                                        disabled={!allRequired || saveMutation.isPending}>
                                        {saveMutation.isPending ? t('common.loading') : (isEditing ? 'Save Changes' : t('agent.settings.channel.saveChannel'))}
                                    </button>
                                    {isEditing && <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setEditing(ch.id, false)}>Cancel</button>}
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </div>
        );
    };

    // ─── Render ─────────────────────────────────────────
    if (mode === 'create') {
        return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {/* Configurable channels */}
                {CHANNEL_REGISTRY.filter(ch => !ch.editOnly).map(renderCreateChannel)}

                {/* Disabled channels: configure in settings after creation */}
                {CHANNEL_REGISTRY.filter(ch => ch.editOnly).map(ch => (
                    <div key={ch.id} style={{
                        display: 'flex', alignItems: 'center', gap: '12px', padding: '14px',
                        background: 'var(--bg-elevated)', border: '1px solid var(--border-default)',
                        borderRadius: '8px', opacity: 0.7,
                    }}>
                        {ch.icon}
                        <div style={{ flex: 1 }}>
                            <div style={{ fontWeight: 500, fontSize: '13px' }}>{t(ch.nameKey, ch.nameFallback)}</div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{ch.desc}</div>
                        </div>
                        <span style={{ fontSize: '10px', padding: '2px 8px', borderRadius: '10px', background: 'var(--bg-secondary)', color: 'var(--text-tertiary)', fontWeight: 500 }}>Configure in Settings</span>
                    </div>
                ))}
            </div>
        );
    }

    // Edit mode
    return (
        <div className="card" style={{ marginBottom: '12px' }}>
            <h4 style={{ marginBottom: '12px' }}>{t('agent.settings.channel.title')}</h4>
            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>{t('agent.settings.channel.title')}</p>
            <div style={{
                padding: '10px 14px', borderRadius: '8px', marginBottom: '16px',
                background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.2)',
                fontSize: '12px', color: 'var(--text-secondary)', lineHeight: '1.6',
            }}>
                {t('agent.settings.channel.syncHint', 'Before configuring the Feishu bot, please sync your organization structure in Enterprise Settings → Org Structure first. This ensures the bot can identify message senders.')}
            </div>
            {CHANNEL_REGISTRY.map(renderEditChannel)}
        </div>
    );
}
