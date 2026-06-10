import type { Dispatch, ReactNode, SetStateAction } from 'react';
import { IconTools, IconWorld } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';

import AgentCredentials from '../../../components/AgentCredentials';
import ChannelConfig from '../../../components/ChannelConfig';
import OpenClawSettings from '../../OpenClawSettings';
import { agentApi } from '../../../services/api';

type SettingsFormState = {
    primary_model_id: string;
    fallback_model_id: string;
    context_window_size: number;
    max_tool_rounds: number;
    max_tokens_per_day: string | number;
    max_tokens_per_month: string | number;
    max_triggers: number;
    min_poll_interval_min: number;
    webhook_rate_limit: number;
};

interface Props {
    agent: any;
    agentId: string;
    canManage: boolean;
    llmModels: any[];
    settingsForm: SettingsFormState;
    setSettingsForm: Dispatch<SetStateAction<SettingsFormState>>;
    settingsSaved: boolean;
    settingsError: string;
    settingsSaving: boolean;
    hasChanges: boolean;
    onSaveSettings: () => Promise<void>;
    wmDraft: string;
    setWmDraft: Dispatch<SetStateAction<string>>;
    wmSaved: boolean;
    onSaveWelcomeMessage: () => Promise<void>;
    accessPermissionsPanel: ReactNode;
    queryClient: any;
    formatTokens: (n: number) => string;
    showDeleteConfirm: boolean;
    setShowDeleteConfirm: Dispatch<SetStateAction<boolean>>;
    onDeleteAgent: () => Promise<void>;
}

export default function SettingsTab(props: Props) {
    const {
        agent,
        agentId,
        canManage,
        llmModels,
        settingsForm,
        setSettingsForm,
        settingsSaved,
        settingsError,
        settingsSaving,
        hasChanges,
        onSaveSettings,
        wmDraft,
        setWmDraft,
        wmSaved,
        onSaveWelcomeMessage,
        accessPermissionsPanel,
        queryClient,
        formatTokens,
        showDeleteConfirm,
        setShowDeleteConfirm,
        onDeleteAgent,
    } = props;
    const { t, i18n } = useTranslation();

    if ((agent as any)?.agent_type === 'openclaw') {
        return <OpenClawSettings agent={agent} agentId={agentId} />;
    }

    return (
        <div>
            <div className="agent-settings-savebar">
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    {settingsSaved && <span style={{ fontSize: '12px', color: 'var(--success)' }}>{t('agent.settings.saved', 'Saved')}</span>}
                    {settingsError && <span style={{ fontSize: '12px', color: settingsError.includes('adjusted') ? 'var(--warning)' : 'var(--error)', whiteSpace: 'pre-line' }}>{settingsError}</span>}
                    <button
                        className="btn btn-primary"
                        disabled={!hasChanges || settingsSaving}
                        onClick={onSaveSettings}
                        style={{
                            opacity: hasChanges ? 1 : 0.5,
                            cursor: hasChanges ? 'pointer' : 'default',
                            padding: '6px 20px',
                            fontSize: '13px',
                        }}
                    >
                        {settingsSaving ? t('agent.settings.saving', 'Saving...') : t('agent.settings.save', 'Save')}
                    </button>
                </div>
            </div>

            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '12px' }}>{t('agent.settings.modelConfig')}</h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                    <div>
                        <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.primaryModel')}</label>
                        <select
                            className="input"
                            value={settingsForm.primary_model_id}
                            onChange={(e) => setSettingsForm((form) => ({ ...form, primary_model_id: e.target.value }))}
                        >
                            <option value="">--</option>
                            {llmModels.filter((m: any) => m.enabled || m.id === settingsForm.primary_model_id).map((m: any) => (
                                <option key={m.id} value={m.id}>
                                    {m.label || m.model}
                                </option>
                            ))}
                        </select>
                        {settingsForm.primary_model_id && llmModels.some((m: any) => m.id === settingsForm.primary_model_id && !m.enabled) && (
                            <div style={{ fontSize: '11px', color: 'var(--error)', marginTop: '4px' }}>
                                {t('agent.settings.modelDisabledWarning', 'This model has been disabled by admin. The agent will automatically use the fallback model.')}
                            </div>
                        )}
                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.primaryModel')}</div>
                    </div>
                    <div>
                        <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.fallbackModel')}</label>
                        <select
                            className="input"
                            value={settingsForm.fallback_model_id}
                            onChange={(e) => setSettingsForm((form) => ({ ...form, fallback_model_id: e.target.value }))}
                        >
                            <option value="">--</option>
                            {llmModels.filter((m: any) => m.enabled || m.id === settingsForm.fallback_model_id).map((m: any) => (
                                <option key={m.id} value={m.id}>
                                    {m.label || m.model}
                                </option>
                            ))}
                        </select>
                        {settingsForm.fallback_model_id && llmModels.some((m: any) => m.id === settingsForm.fallback_model_id && !m.enabled) && (
                            <div style={{ fontSize: '11px', color: 'var(--error)', marginTop: '4px' }}>
                                {t('agent.settings.modelDisabledWarning', 'This model has been disabled by admin. The agent will automatically use the fallback model.')}
                            </div>
                        )}
                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.fallbackModel')}</div>
                    </div>
                </div>
            </div>

            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '12px' }}>{t('agent.settings.conversationContext')}</h4>
                <div>
                    <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.maxRounds')}</label>
                    <input
                        className="input"
                        type="number"
                        min={10}
                        max={500}
                        value={settingsForm.context_window_size}
                        onChange={(e) => setSettingsForm((form) => ({ ...form, context_window_size: Math.max(10, Math.min(500, parseInt(e.target.value) || 100)) }))}
                        style={{ width: '120px' }}
                    />
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.roundsDesc')}</div>
                </div>
            </div>

            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '6px' }}><IconTools size={16} stroke={1.8} /> {t('agent.settings.maxToolRounds', 'Max Tool Call Rounds')}</h4>
                <div>
                    <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.maxToolRoundsLabel', 'Maximum rounds per message')}</label>
                    <input
                        className="input"
                        type="number"
                        min={5}
                        max={200}
                        value={settingsForm.max_tool_rounds}
                        onChange={(e) => setSettingsForm((form) => ({ ...form, max_tool_rounds: Math.max(5, Math.min(200, parseInt(e.target.value) || 50)) }))}
                        style={{ width: '120px' }}
                    />
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.maxToolRoundsDesc', 'How many tool-calling rounds the agent can perform per message (search, write, etc). Default: 50')}</div>
                </div>
            </div>

            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '12px' }}>{t('agent.settings.tokenLimits')}</h4>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                    <div>
                        <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.dailyLimit')}</label>
                        <input className="input" type="number" value={settingsForm.max_tokens_per_day} onChange={(e) => setSettingsForm((form) => ({ ...form, max_tokens_per_day: e.target.value }))} placeholder={t('agent.settings.noLimit')} />
                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.today')}: {formatTokens(agent?.tokens_used_today || 0)}</div>
                    </div>
                    <div>
                        <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.monthlyLimit')}</label>
                        <input className="input" type="number" value={settingsForm.max_tokens_per_month} onChange={(e) => setSettingsForm((form) => ({ ...form, max_tokens_per_month: e.target.value }))} placeholder={t('agent.settings.noLimit')} />
                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.month')}: {formatTokens(agent?.tokens_used_month || 0)}</div>
                    </div>
                </div>
            </div>

            {(() => {
                const isChinese = i18n.language?.startsWith('zh');
                return (
                    <div className="card" style={{ marginBottom: '12px' }}>
                        <h4 style={{ marginBottom: '4px' }}>{isChinese ? '触发器限制' : 'Trigger Limits'}</h4>
                        <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                            {isChinese ? '控制该 Agent 可以创建的触发器数量和行为限制' : 'Limit how many triggers this agent can create and their behavior'}
                        </p>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px' }}>
                            <div>
                                <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{isChinese ? '最大触发器数' : 'Max Triggers'}</label>
                                <input className="input" type="number" min={1} max={100} value={settingsForm.max_triggers} onChange={(e) => setSettingsForm((form) => ({ ...form, max_triggers: Math.max(1, Math.min(100, parseInt(e.target.value) || 20)) }))} style={{ width: '100%' }} />
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{isChinese ? 'Agent 最多可同时拥有的触发器数量' : 'Max active triggers the agent can have'}</div>
                            </div>
                            <div>
                                <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{isChinese ? 'Poll 最短间隔 (分钟)' : 'Min Poll Interval (min)'}</label>
                                <input className="input" type="number" min={1} max={60} value={settingsForm.min_poll_interval_min} onChange={(e) => setSettingsForm((form) => ({ ...form, min_poll_interval_min: Math.max(1, Math.min(60, parseInt(e.target.value) || 5)) }))} style={{ width: '100%' }} />
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{isChinese ? '定时轮询外部接口的最短间隔' : 'Minimum interval for polling external URLs'}</div>
                            </div>
                            <div>
                                <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{isChinese ? 'Webhook 频率限制 (次/分钟)' : 'Webhook Rate Limit (/min)'}</label>
                                <input className="input" type="number" min={1} max={60} value={settingsForm.webhook_rate_limit} onChange={(e) => setSettingsForm((form) => ({ ...form, webhook_rate_limit: Math.max(1, Math.min(60, parseInt(e.target.value) || 5)) }))} style={{ width: '100%' }} />
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{isChinese ? '外部系统每分钟最多可调用的 Webhook 次数' : 'Max webhook calls per minute from external services'}</div>
                            </div>
                        </div>
                    </div>
                );
            })()}

            <div style={{ marginBottom: '12px' }}>
                <AgentCredentials agentId={agentId} />
            </div>

            {(() => {
                const isChinese = i18n.language?.startsWith('zh');
                return (
                    <div className="card" style={{ marginBottom: '12px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' }}>
                            <h4 style={{ margin: 0 }}>{isChinese ? '欢迎语' : 'Welcome Message'}</h4>
                            {wmSaved && <span style={{ fontSize: '12px', color: 'var(--success)' }}>✓ {isChinese ? '已保存' : 'Saved'}</span>}
                        </div>
                        <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                            {isChinese ? '当用户在网页端发起新对话时，Agent 会自动发送的欢迎语。支持 Markdown 语法。留空则不发送。' : 'Greeting message sent automatically when a user starts a new web conversation. Supports Markdown. Leave empty to disable.'}
                        </p>
                        <textarea className="input" rows={4} value={wmDraft} onChange={(e) => setWmDraft(e.target.value)} onBlur={onSaveWelcomeMessage} placeholder={isChinese ? '例如：你好！我是你的 AI 助手，有什么可以帮你的吗？' : "e.g. Hello! I'm your AI assistant. How can I help you?"} style={{ width: '100%', minHeight: '80px', resize: 'vertical', fontFamily: 'inherit', fontSize: '13px' }} />
                    </div>
                );
            })()}

            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '4px' }}>{t('agent.settings.autonomy.title')}</h4>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>{t('agent.settings.autonomy.description')}</p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                    {[
                        { key: 'read_files', label: t('agent.settings.autonomy.readFiles'), desc: t('agent.settings.autonomy.readFilesDesc') },
                        { key: 'write_workspace_files', label: t('agent.settings.autonomy.writeFiles'), desc: t('agent.settings.autonomy.writeFilesDesc') },
                        { key: 'delete_files', label: t('agent.settings.autonomy.deleteFiles'), desc: t('agent.settings.autonomy.deleteFilesDesc') },
                        { key: 'send_feishu_message', label: t('agent.settings.autonomy.sendFeishu'), desc: t('agent.settings.autonomy.sendFeishuDesc') },
                        { key: 'web_search', label: t('agent.settings.autonomy.webSearch'), desc: t('agent.settings.autonomy.webSearchDesc') },
                        { key: 'manage_tasks', label: t('agent.settings.autonomy.manageTasks'), desc: t('agent.settings.autonomy.manageTasksDesc') },
                    ].map((action) => {
                        const currentLevel = (agent?.autonomy_policy as any)?.[action.key] || 'L1';
                        return (
                            <div key={action.key} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
                                <div style={{ flex: 1 }}>
                                    <div style={{ fontWeight: 500, fontSize: '13px' }}>{action.label}</div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{action.desc}</div>
                                </div>
                                <select
                                    className="input"
                                    value={currentLevel}
                                    onChange={async (e) => {
                                        const newPolicy = { ...(agent?.autonomy_policy as any || {}), [action.key]: e.target.value };
                                        await agentApi.update(agentId, { autonomy_policy: newPolicy } as any);
                                        queryClient.invalidateQueries({ queryKey: ['agent', agentId] });
                                    }}
                                    style={{ width: '140px', fontSize: '12px', color: currentLevel === 'L1' ? 'var(--success)' : currentLevel === 'L2' ? 'var(--warning)' : 'var(--error)', fontWeight: 600 }}
                                >
                                    <option value="L1">{t('agent.settings.autonomy.l1Auto')}</option>
                                    <option value="L2">{t('agent.settings.autonomy.l2Notify')}</option>
                                    <option value="L3">{t('agent.settings.autonomy.l3Approve')}</option>
                                </select>
                            </div>
                        );
                    })}
                </div>
            </div>

            {accessPermissionsPanel}

            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <IconWorld size={16} stroke={1.8} /> {t('agent.settings.timezone.title', 'Timezone')}
                </h4>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>{t('agent.settings.timezone.description', 'The timezone used for this agent\'s scheduling, active hours, and time awareness. Defaults to the company timezone if not set.')}</p>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
                    <div>
                        <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('agent.settings.timezone.current', 'Agent Timezone')}</div>
                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{agent?.timezone ? t('agent.settings.timezone.override', 'Custom timezone for this agent') : t('agent.settings.timezone.inherited', 'Using company default timezone')}</div>
                    </div>
                    <select
                        className="input"
                        disabled={!canManage}
                        value={agent?.timezone || ''}
                        onChange={async (e) => {
                            if (!canManage) return;
                            const value = e.target.value || null;
                            await agentApi.update(agentId, { timezone: value } as any);
                            queryClient.invalidateQueries({ queryKey: ['agent', agentId] });
                        }}
                        style={{ width: '200px', fontSize: '12px', opacity: canManage ? 1 : 0.6 }}
                    >
                        <option value="">{t('agent.settings.timezone.default', '↩ Company default')}</option>
                        {['UTC', 'Asia/Shanghai', 'Asia/Tokyo', 'Asia/Seoul', 'Asia/Singapore', 'Asia/Kolkata', 'Asia/Dubai', 'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Moscow', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/Sao_Paulo', 'Australia/Sydney', 'Pacific/Auckland'].map((tz) => (
                            <option key={tz} value={tz}>{tz}</option>
                        ))}
                    </select>
                </div>
            </div>

            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>{t('agent.settings.heartbeat.title', 'Heartbeat')}</h4>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>{t('agent.settings.heartbeat.description', 'Periodic awareness check — agent proactively monitors the plaza and work environment.')}</p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
                        <div>
                            <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('agent.settings.heartbeat.enabled', 'Enable Heartbeat')}</div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.settings.heartbeat.enabledDesc', 'Agent will periodically check plaza and work status')}</div>
                        </div>
                        <label style={{ position: 'relative', display: 'inline-block', width: '44px', height: '24px', cursor: canManage ? 'pointer' : 'default' }}>
                            <input
                                type="checkbox"
                                checked={agent?.heartbeat_enabled ?? true}
                                disabled={!canManage}
                                onChange={async (e) => {
                                    if (!canManage) return;
                                    await agentApi.update(agentId, { heartbeat_enabled: e.target.checked } as any);
                                    queryClient.invalidateQueries({ queryKey: ['agent', agentId] });
                                }}
                                style={{ opacity: 0, width: 0, height: 0 }}
                            />
                            <span style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, background: (agent?.heartbeat_enabled ?? true) ? 'var(--accent-primary)' : 'var(--bg-tertiary)', borderRadius: '12px', transition: 'background 0.2s', opacity: canManage ? 1 : 0.6 }}>
                                <span style={{ position: 'absolute', top: '3px', left: (agent?.heartbeat_enabled ?? true) ? '23px' : '3px', width: '18px', height: '18px', background: 'white', borderRadius: '50%', transition: 'left 0.2s' }} />
                            </span>
                        </label>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
                        <div>
                            <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('agent.settings.heartbeat.interval', 'Check Interval')}</div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.settings.heartbeat.intervalDesc', 'How often the agent checks for updates')}</div>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <input
                                type="number"
                                className="input"
                                disabled={!canManage}
                                min={1}
                                defaultValue={agent?.heartbeat_interval_minutes ?? 120}
                                key={agent?.heartbeat_interval_minutes}
                                onBlur={async (e) => {
                                    if (!canManage) return;
                                    const value = Math.max(1, Number(e.target.value) || 120);
                                    e.target.value = String(value);
                                    await agentApi.update(agentId, { heartbeat_interval_minutes: value } as any);
                                    queryClient.invalidateQueries({ queryKey: ['agent', agentId] });
                                }}
                                style={{ width: '80px', fontSize: '12px', opacity: canManage ? 1 : 0.6 }}
                            />
                            <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('common.minutes', 'min')}</span>
                        </div>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
                        <div>
                            <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('agent.settings.heartbeat.activeHours', 'Active Hours')}</div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.settings.heartbeat.activeHoursDesc', 'Only trigger heartbeat during these hours (HH:MM-HH:MM)')}</div>
                        </div>
                        <input
                            className="input"
                            disabled={!canManage}
                            value={agent?.heartbeat_active_hours ?? '09:00-18:00'}
                            onChange={async (e) => {
                                if (!canManage) return;
                                await agentApi.update(agentId, { heartbeat_active_hours: e.target.value } as any);
                                queryClient.invalidateQueries({ queryKey: ['agent', agentId] });
                            }}
                            style={{ width: '140px', fontSize: '12px', textAlign: 'center', opacity: canManage ? 1 : 0.6 }}
                            placeholder="09:00-18:00"
                        />
                    </div>

                    {agent?.last_heartbeat_at && (
                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', paddingLeft: '4px' }}>
                            {t('agent.settings.heartbeat.lastRun', 'Last heartbeat')}: {new Date(agent.last_heartbeat_at).toLocaleString()}
                        </div>
                    )}
                </div>
            </div>

            <div style={{ marginBottom: '12px' }}>
                <ChannelConfig mode="edit" agentId={agentId} />
            </div>

            <div className="card" style={{ borderColor: 'var(--error)' }}>
                <h4 style={{ color: 'var(--error)', marginBottom: '12px' }}>{t('agent.settings.danger.title')}</h4>
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>{t('agent.settings.danger.deleteWarning')}</p>
                {!showDeleteConfirm ? (
                    <button className="btn btn-danger" onClick={() => setShowDeleteConfirm(true)}>× {t('agent.settings.danger.deleteAgent')}</button>
                ) : (
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <span style={{ fontSize: '13px', color: 'var(--error)', fontWeight: 600 }}>{t('agent.settings.danger.deleteWarning')}</span>
                        <button className="btn btn-danger" onClick={onDeleteAgent}>{t('agent.settings.danger.confirmDelete')}</button>
                        <button className="btn btn-secondary" onClick={() => setShowDeleteConfirm(false)}>{t('common.cancel')}</button>
                    </div>
                )}
            </div>
        </div>
    );
}
