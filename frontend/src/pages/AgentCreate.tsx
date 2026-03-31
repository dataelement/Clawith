import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Eye, Settings } from 'lucide-react';
import { agentApi, channelApi, enterpriseApi, skillApi } from '../services/api';
import ChannelConfig from '../components/ChannelConfig';
import { copyToClipboard } from '../utils/clipboard';

const STEPS = ['basicInfo', 'personality', 'skills', 'permissions', 'channel'] as const;
const OPENCLAW_STEPS = ['basicInfo', 'permissions'] as const;

/**
 * Generic parser for soul_template markdown format.
 * Extracts content from sections by header names (## Header Name).
 * 
 * @param soulTemplate - The markdown template string
 * @param sectionNames - Array of section names to extract (e.g., ['Personality', 'Boundaries'])
 * @returns Object with extracted section contents (lowercase keys)
 * 
 * @example
 * const sections = parseSoulTemplate(markdown, ['Personality', 'Boundaries', 'Identity']);
 * // Returns: { personality: '...', boundaries: '...', identity: '...' }
 */
function parseSoulTemplate(soulTemplate: string, sectionNames: string[] = []): Record<string, string> {
    if (!soulTemplate) {
        const empty: Record<string, string> = {};
        sectionNames.forEach(name => {
            empty[name.toLowerCase()] = '';
        });
        return empty;
    }

    const result: Record<string, string> = {};
    
    // Initialize all requested sections as empty
    sectionNames.forEach(name => {
        result[name.toLowerCase()] = '';
    });

    // Split by markdown ## headers
    const sections = soulTemplate.split(/^##\s+/m);

    for (let i = 0; i < sections.length; i++) {
        const section = sections[i].trim();
        const firstLineEnd = section.indexOf('\n');
        const headerName = firstLineEnd > 0 ? section.slice(0, firstLineEnd).trim() : section.trim();
        const content = firstLineEnd > 0 ? section.slice(firstLineEnd + 1).trim() : '';

        // If this header matches one of our requested sections
        const matchedSection = sectionNames.find(name => 
            name.toLowerCase() === headerName.toLowerCase()
        );
        
        if (matchedSection) {
            result[matchedSection.toLowerCase()] = content;
        }
    }

    return result;
}

export default function AgentCreate() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [step, setStep] = useState(0);
    const [error, setError] = useState('');
    const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
    const [agentType, setAgentType] = useState<'native' | 'openclaw'>('native');
    // Clear field error when user edits a field
    const clearFieldError = (field: string) => setFieldErrors(prev => { const n = { ...prev }; delete n[field]; return n; });
    const [createdApiKey, setCreatedApiKey] = useState('');
    // Current company (tenant) selection from layout sidebar
    const [currentTenant] = useState<string | null>(() => localStorage.getItem('current_tenant_id'));

    const [form, setForm] = useState({
        name: '',
        role_description: '',
        personality: '',
        boundaries: '',
        primary_model_id: '' as string,
        fallback_model_id: '' as string,
        permission_scope_type: 'company',
        permission_access_level: 'use',
        template_id: '' as string,
        max_tokens_per_day: '',
        max_tokens_per_month: '',
        skill_ids: [] as string[],
    });
    const [channelValues, setChannelValues] = useState<Record<string, string>>({});

    // Fetch LLM models for step 1
    const { data: models = [] } = useQuery({
        queryKey: ['llm-models'],
        queryFn: enterpriseApi.llmModels,
    });

    // Fetch templates
    const { data: templates = [] } = useQuery({
        queryKey: ['templates'],
        queryFn: enterpriseApi.templates,
    });

    // Fetch global skills for step 3
    const { data: globalSkills = [] } = useQuery({
        queryKey: ['global-skills'],
        queryFn: skillApi.list,
    });

    // Auto-select default skills
    useEffect(() => {
        if (globalSkills.length > 0) {
            const defaultIds = globalSkills.filter((s: any) => s.is_default).map((s: any) => s.id);
            if (defaultIds.length > 0) {
                setForm(prev => ({
                    ...prev,
                    skill_ids: Array.from(new Set([...prev.skill_ids, ...defaultIds]))
                }));
            }
        }
    }, [globalSkills]);

    const createMutation = useMutation({
        mutationFn: async (data: any) => {
            const agent = await agentApi.create(data);
            return agent;
        },
        onSuccess: async (agent) => {
            queryClient.invalidateQueries({ queryKey: ['agents'] });

            // Automatically bind channels if configured in wizard
            // Feishu
            if (channelValues.feishu_app_id && channelValues.feishu_app_secret) {
                try {
                    await channelApi.create(agent.id, {
                        channel_type: 'feishu',
                        app_id: channelValues.feishu_app_id,
                        app_secret: channelValues.feishu_app_secret,
                        encrypt_key: channelValues.feishu_encrypt_key || undefined,
                        extra_config: {
                            connection_mode: channelValues.feishu_connection_mode || 'websocket'
                        }
                    });
                } catch (err) {
                    console.error('Failed to bind Feishu channel:', err);
                    setError(
                        'Failed to bind the Feishu channel. Please verify the Feishu configuration on the agent settings page and try again.'
                    );
                }
            }

            // Slack
            if (channelValues.slack_bot_token && channelValues.slack_signing_secret) {
                try {
                    await channelApi.create(agent.id, {
                        channel_type: 'slack',
                        app_id: channelValues.slack_bot_token,
                        app_secret: channelValues.slack_signing_secret,
                    });
                } catch (err) {
                    console.error('Failed to bind Slack channel:', err);
                    setError(
                        'Failed to bind the Slack channel. Please verify the Slack configuration on the agent settings page and try again.'
                    );
                }
            }

            // Discord
            if (channelValues.discord_bot_token && channelValues.discord_application_id) {
                try {
                    await channelApi.create(agent.id, {
                        channel_type: 'discord',
                        app_id: channelValues.discord_application_id,
                        app_secret: channelValues.discord_bot_token,
                        encrypt_key: channelValues.discord_public_key || undefined,
                    });
                } catch (err) {
                    console.error('Failed to bind Discord channel:', err);
                    setError(
                        'Failed to bind the Discord channel. Please verify the Discord configuration on the agent settings page and try again.'
                    );
                }
            }

            // WeCom
            if (channelValues.wecom_bot_id && channelValues.wecom_bot_secret) {
                try {
                    const connMode = channelValues.wecom_connection_mode || 'websocket';
                    await channelApi.create(agent.id, {
                        channel_type: 'wecom',
                        app_id: connMode === 'websocket' ? channelValues.wecom_bot_id : undefined,
                        app_secret: connMode === 'websocket' ? channelValues.wecom_bot_secret : undefined,
                        extra_config: {
                            connection_mode: connMode,
                            bot_id: channelValues.wecom_bot_id,
                            bot_secret: channelValues.wecom_bot_secret,
                        }
                    });
                } catch (err) {
                    console.error('Failed to bind WeCom channel:', err);
                    setError(
                        'Failed to bind the WeCom channel. Please verify the WeCom configuration on the agent settings page and try again.'
                    );
                }
            }

            if (agent.api_key) {
                setCreatedApiKey(agent.api_key);
            } else {
                navigate(`/agents/${agent.id}`);
            }
        },
        onError: (err: any) => setError(err.message),
    });

    const validateStep0 = (): boolean => {
        const errors: Record<string, string> = {};
        const name = form.name.trim();
        if (!name) {
            errors.name = t('wizard.errors.nameRequired', '智能体名称不能为空');
        } else if (name.length < 2) {
            errors.name = t('wizard.errors.nameTooShort', '名称至少需要 2 个字符');
        } else if (name.length > 100) {
            errors.name = t('wizard.errors.nameTooLong', '名称不能超过 100 个字符');
        }
        if (form.role_description.length > 500) {
            errors.role_description = t('wizard.errors.roleDescTooLong', '角色描述不能超过 500 个字符（当前 {{count}} 字符）').replace('{{count}}', String(form.role_description.length));
        }
        if (form.max_tokens_per_day && (isNaN(Number(form.max_tokens_per_day)) || Number(form.max_tokens_per_day) <= 0)) {
            errors.max_tokens_per_day = t('wizard.errors.tokenLimitInvalid', '请输入有效的正整数');
        }
        if (form.max_tokens_per_month && (isNaN(Number(form.max_tokens_per_month)) || Number(form.max_tokens_per_month) <= 0)) {
            errors.max_tokens_per_month = t('wizard.errors.tokenLimitInvalid', '请输入有效的正整数');
        }
        const enabledModels = (models as any[]).filter((m: any) => m.enabled);
        if (agentType === 'native' && enabledModels.length > 0 && !form.primary_model_id) {
            errors.primary_model_id = t('wizard.errors.modelRequired', '请选择一个主模型');
        }
        setFieldErrors(errors);
        return Object.keys(errors).length === 0;
    };

    const handleNext = () => {
        setError('');
        if (step === 0 && !validateStep0()) return;
        setStep(step + 1);
    };

    const handleFinish = () => {
        setError('');
        if (step === 0 || agentType === 'openclaw') {
            if (!validateStep0()) return;
        }
        createMutation.mutate({
            name: form.name,
            agent_type: agentType,
            role_description: form.role_description,
            personality: agentType === 'native' ? form.personality : undefined,
            boundaries: agentType === 'native' ? form.boundaries : undefined,
            primary_model_id: agentType === 'native' ? (form.primary_model_id || undefined) : undefined,
            fallback_model_id: agentType === 'native' ? (form.fallback_model_id || undefined) : undefined,
            template_id: form.template_id || undefined,
            permission_scope_type: form.permission_scope_type,
            max_tokens_per_day: form.max_tokens_per_day ? Number(form.max_tokens_per_day) : undefined,
            max_tokens_per_month: form.max_tokens_per_month ? Number(form.max_tokens_per_month) : undefined,
            skill_ids: agentType === 'native' ? form.skill_ids : [],
            permission_access_level: form.permission_access_level,
            tenant_id: currentTenant || undefined,
        });
    };

    const selectedModel = models.find((m: any) => m.id === form.primary_model_id);
    const activeSteps = agentType === 'openclaw' ? OPENCLAW_STEPS : STEPS;

    // If OpenClaw agent just created, show success page with API key
    if (createdApiKey && createMutation.data) {
        const agent = createMutation.data;
        return (
            <div>
                <div className="page-header">
                    <h1 className="page-title">{t('openclaw.created', 'OpenClaw Agent Created')}</h1>
                </div>
                <div className="card" style={{ maxWidth: '640px' }}>
                    <div style={{ textAlign: 'center', padding: '20px 0' }}>
                        <div style={{ fontSize: '32px', marginBottom: '12px' }}>&#x2713;</div>
                        <h3 style={{ fontWeight: 600, marginBottom: '8px' }}>{agent.name}</h3>
                        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '24px' }}>
                            {t('openclaw.createdDesc2', 'Your OpenClaw agent has been registered. Copy the instruction below and send it to your OpenClaw agent to complete the setup.')}
                        </p>
                    </div>

                    {/* Setup Instruction — single block to send to OpenClaw */}
                    <div style={{ marginBottom: '20px' }}>
                        <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, marginBottom: '6px', color: 'var(--text-secondary)' }}>
                            {t('openclaw.setupInstruction', 'Setup Instruction')}
                        </label>
                        <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>
                            {t('openclaw.setupInstructionDesc', 'Copy and send this to your OpenClaw agent. It will configure itself automatically.')}
                        </p>
                        <div style={{ position: 'relative' }}>
                            <pre style={{
                                padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px',
                                fontSize: '11px', lineHeight: 1.6, overflow: 'auto', maxHeight: '280px',
                                border: '1px solid var(--border-default)', whiteSpace: 'pre-wrap',
                            }}>{`Please create a skill file at skills/clawith_sync.md with the following content, then add "Check Clawith inbox using the clawith_sync skill and process any pending messages" to your HEARTBEAT.md:

---
name: clawith_sync
description: Sync with Clawith platform — check inbox, submit results, and send messages.
---

# Clawith Sync

## When to use
Check for new messages from the Clawith platform during every heartbeat cycle.
You can also proactively send messages to people and agents in your relationships.

## Instructions

### 1. Check inbox
Make an HTTP GET request:
- URL: ${window.location.origin}/api/gateway/poll
- Header: X-Api-Key: ${createdApiKey}

The response contains a \`messages\` array. Each message includes:
- \`id\` — unique message ID (use this for reporting)
- \`content\` — the message text
- \`sender_user_name\` — name of the Clawith user who sent it
- \`sender_user_id\` — unique ID of the sender
- \`conversation_id\` — the conversation this message belongs to
- \`history\` — array of previous messages in this conversation for context

The response also contains a \`relationships\` array describing your colleagues:
- \`name\` — the person or agent name
- \`type\` — "human" or "agent"
- \`role\` — relationship type (e.g. collaborator, supervisor)
- \`channels\` — available communication channels (e.g. ["feishu"], ["agent"])

**IMPORTANT**: Use the \`history\` array to understand conversation context before replying.
Different \`sender_user_name\` values mean different people — address them accordingly.

### 2. Report results
For each completed message, make an HTTP POST request:
- URL: ${window.location.origin}/api/gateway/report
- Header: X-Api-Key: ${createdApiKey}
- Header: Content-Type: application/json
- Body: {"message_id": "<id from the message>", "result": "<your response>"}

### 3. Send a message to someone
To proactively contact a person or agent, make an HTTP POST request:
- URL: ${window.location.origin}/api/gateway/send-message
- Header: X-Api-Key: ${createdApiKey}
- Header: Content-Type: application/json
- Body: {"target": "<name of person or agent>", "content": "<your message>"}

The system auto-detects the best channel. For agents, the reply appears in your next poll.
For humans, the message is delivered via their available channel (e.g. Feishu).`}</pre>
                            <button className="btn btn-ghost" style={{ position: 'absolute', top: '4px', right: '4px', fontSize: '11px' }}
                                onClick={() => {
                                    const text = `Please create a skill file at skills/clawith_sync.md with the following content, then add "Check Clawith inbox using the clawith_sync skill and process any pending messages" to your HEARTBEAT.md:\n\n---\nname: clawith_sync\ndescription: Sync with Clawith platform — check inbox, submit results, and send messages.\n---\n\n# Clawith Sync\n\n## When to use\nCheck for new messages from the Clawith platform during every heartbeat cycle.\nYou can also proactively send messages to people and agents in your relationships.\n\n## Instructions\n\n### 1. Check inbox\nMake an HTTP GET request:\n- URL: ${window.location.origin}/api/gateway/poll\n- Header: X-Api-Key: ${createdApiKey}\n\nThe response contains a \`messages\` array. Each message includes:\n- \`id\` — unique message ID (use this for reporting)\n- \`content\` — the message text\n- \`sender_user_name\` — name of the Clawith user who sent it\n- \`sender_user_id\` — unique ID of the sender\n- \`conversation_id\` — the conversation this message belongs to\n- \`history\` — array of previous messages in this conversation for context\n\nThe response also contains a \`relationships\` array describing your colleagues:\n- \`name\` — the person or agent name\n- \`type\` — "human" or "agent"\n- \`role\` — relationship type (e.g. collaborator, supervisor)\n- \`channels\` — available communication channels (e.g. ["feishu"], ["agent"])\n\n**IMPORTANT**: Use the \`history\` array to understand conversation context before replying.\nDifferent \`sender_user_name\` values mean different people — address them accordingly.\n\n### 2. Report results\nFor each completed message, make an HTTP POST request:\n- URL: ${window.location.origin}/api/gateway/report\n- Header: X-Api-Key: ${createdApiKey}\n- Header: Content-Type: application/json\n- Body: {"message_id": "<id from the message>", "result": "<your response>"}\n\n### 3. Send a message to someone\nTo proactively contact a person or agent, make an HTTP POST request:\n- URL: ${window.location.origin}/api/gateway/send-message\n- Header: X-Api-Key: ${createdApiKey}\n- Header: Content-Type: application/json\n- Body: {"target": "<name of person or agent>", "content": "<your message>"}\n\nThe system auto-detects the best channel. For agents, the reply appears in your next poll.\nFor humans, the message is delivered via their available channel (e.g. Feishu).`;
                                    copyToClipboard(text);
                                }}
                            >{t('common.copy', 'Copy')}</button>
                        </div>
                    </div>

                    {/* API Key — collapsed by default */}
                    <details style={{ marginBottom: '24px' }}>
                        <summary style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', cursor: 'pointer', userSelect: 'none' }}>
                            API Key
                        </summary>
                        <div style={{ marginTop: '8px' }}>
                            <div style={{ display: 'flex', gap: '8px' }}>
                                <code style={{
                                    flex: 1, padding: '10px 12px', background: 'var(--bg-secondary)', borderRadius: '6px',
                                    fontSize: '13px', fontFamily: 'monospace', wordBreak: 'break-all',
                                    border: '1px solid var(--border-default)',
                                }}>{createdApiKey}</code>
                                <button className="btn btn-secondary" onClick={() => copyToClipboard(createdApiKey)}>
                                    {t('common.copy', 'Copy')}
                                </button>
                            </div>
                            <p style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '6px' }}>
                                {t('openclaw.keyNote', 'This key is already embedded in the instruction above. Save it separately if needed for manual configuration.')}
                            </p>
                        </div>
                    </details>

                    <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => navigate(`/agents/${agent.id}`)}>
                        {t('openclaw.goToAgent', 'Go to Agent Page')}
                    </button>
                </div>
            </div>
        );
    }

    // ── Type Selector (shared between both modes) ──
    const typeSelector = (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', maxWidth: '640px', marginBottom: '24px' }}>
            <div
                onClick={() => { setAgentType('native'); setStep(0); }}
                style={{
                    padding: '16px', borderRadius: '8px', cursor: 'pointer',
                    border: `1.5px solid ${agentType === 'native' ? 'var(--accent-primary)' : 'var(--border-default)'}`,
                    background: agentType === 'native' ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                }}
            >
                <div style={{ fontWeight: 600, fontSize: '14px', marginBottom: '4px' }}>{t('openclaw.nativeTitle', 'Platform Hosted')}</div>
                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('openclaw.nativeDesc', 'Full agent running on Clawith platform')}</div>
            </div>
            <div
                onClick={() => { setAgentType('openclaw'); setStep(0); }}
                style={{
                    padding: '16px', borderRadius: '8px', cursor: 'pointer', position: 'relative',
                    border: `1.5px solid ${agentType === 'openclaw' ? 'var(--accent-primary)' : 'var(--border-default)'}`,
                    background: agentType === 'openclaw' ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                }}
            >
                <span style={{
                    position: 'absolute', top: '8px', right: '8px',
                    fontSize: '10px', padding: '2px 6px', borderRadius: '4px',
                    background: 'linear-gradient(135deg, #6366f1, #8b5cf6)', color: '#fff', fontWeight: 600,
                    letterSpacing: '0.5px',
                }}>Lab</span>
                <div style={{ fontWeight: 600, fontSize: '14px', marginBottom: '4px' }}>{t('openclaw.openclawTitle', 'Link OpenClaw')}</div>
                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('openclaw.openclawDesc', 'Connect your existing OpenClaw agent')}</div>
            </div>
        </div>
    );

    // ── OpenClaw mode: completely separate page ──
    if (agentType === 'openclaw') {
        return (
            <div>
                <div className="page-header">
                    <h1 className="page-title">{t('nav.newAgent')}</h1>
                </div>

                {typeSelector}

                {error && (
                    <div style={{ background: 'var(--error-subtle)', color: 'var(--error)', padding: '8px 12px', borderRadius: '6px', fontSize: '13px', marginBottom: '16px', maxWidth: '640px' }}>
                        {error}
                    </div>
                )}

                <div className="card" style={{ maxWidth: '640px' }}>
                    <h3 style={{ marginBottom: '6px', fontWeight: 600, fontSize: '15px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                        {t('openclaw.basicTitle', 'Link OpenClaw Agent')}
                        <span style={{
                            fontSize: '10px', padding: '2px 6px', borderRadius: '4px',
                            background: 'linear-gradient(135deg, #6366f1, #8b5cf6)', color: '#fff', fontWeight: 600,
                        }}>Lab</span>
                    </h3>
                    <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '20px' }}>
                        {t('openclaw.basicDesc', 'Give your OpenClaw agent a name and description. The LLM model, personality, and skills are configured on your OpenClaw instance.')}
                    </p>

                    <div className="form-group">
                        <label className="form-label">{t('agent.fields.name')} *</label>
                        <input className={`form-input${fieldErrors.name ? ' input-error' : ''}`} value={form.name}
                            onChange={(e) => { setForm({ ...form, name: e.target.value }); clearFieldError('name'); }}
                            placeholder={t('openclaw.namePlaceholder', 'e.g. My OpenClaw Bot')} autoFocus />
                        {fieldErrors.name && <div style={{ color: 'var(--error)', fontSize: '12px', marginTop: '4px' }}>{fieldErrors.name}</div>}
                    </div>
                    <div className="form-group">
                        <label className="form-label">{t('agent.fields.role')}</label>
                        <input className={`form-input${fieldErrors.role_description ? ' input-error' : ''}`} value={form.role_description}
                            onChange={(e) => { setForm({ ...form, role_description: e.target.value }); clearFieldError('role_description'); }}
                            placeholder={t('openclaw.rolePlaceholder', 'e.g. Personal assistant running on my Mac')} />
                        {fieldErrors.role_description && <div style={{ color: 'var(--error)', fontSize: '12px', marginTop: '4px' }}>{fieldErrors.role_description}</div>}
                    </div>

                    {/* Permissions */}
                    <div className="form-group" style={{ marginTop: '8px' }}>
                        <label className="form-label">{t('wizard.step4.title')}</label>
                        <div style={{ display: 'flex', gap: '8px' }}>
                            {[
                                { value: 'company', label: t('wizard.step4.companyWide'), desc: t('wizard.step4.companyWideDesc') },
                                { value: 'user', label: t('wizard.step4.selfOnly'), desc: t('wizard.step4.selfOnlyDesc') },
                            ].map((scope) => (
                                <label key={scope.value} style={{
                                    flex: 1, display: 'flex', alignItems: 'center', gap: '10px', padding: '12px',
                                    background: form.permission_scope_type === scope.value ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                                    border: `1px solid ${form.permission_scope_type === scope.value ? 'var(--accent-primary)' : 'var(--border-default)'}`,
                                    borderRadius: '8px', cursor: 'pointer',
                                }}>
                                    <input type="radio" name="scope" checked={form.permission_scope_type === scope.value}
                                        onChange={() => setForm({ ...form, permission_scope_type: scope.value })} />
                                    <div>
                                        <div style={{ fontWeight: 500, fontSize: '13px' }}>{scope.label}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{scope.desc}</div>
                                    </div>
                                </label>
                            ))}
                        </div>
                    </div>

                    {/* Actions */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '24px' }}>
                        <button className="btn btn-secondary" onClick={() => navigate('/')}>{t('common.cancel')}</button>
                        <button className="btn btn-primary" onClick={handleFinish}
                            disabled={createMutation.isPending}>
                            {createMutation.isPending ? t('common.loading') : t('openclaw.createBtn', 'Link Agent')}
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    // ── Native mode: original multi-step wizard ──
    return (
        <div>
            <div className="page-header">
                <h1 className="page-title">{t('nav.newAgent')}</h1>
            </div>

            {typeSelector}

            {/* Stepper */}
            <div className="wizard-steps">
                {STEPS.map((s, i) => (
                    <div key={s} style={{ display: 'contents' }}>
                        <div className={`wizard-step ${i === step ? 'active' : i < step ? 'completed' : ''}`}>
                            <div className="wizard-step-number">{i < step ? '\u2713' : i + 1}</div>
                            <span>{t(`wizard.steps.${s}`)}</span>
                        </div>
                        {i < STEPS.length - 1 && <div className="wizard-connector" />}
                    </div>
                ))}
            </div>

            {/* Removed top navigation, moved to bottom */}

            {error && (
                <div style={{ background: 'var(--error-subtle)', color: 'var(--error)', padding: '8px 12px', borderRadius: '6px', fontSize: '13px', marginBottom: '16px' }}>
                    {error}
                </div>
            )}

            <div className="card" style={{ maxWidth: '640px' }}>
                {/* Step 1: Basic Info + Model */}
                {step === 0 && (
                    <div>
                        <h3 style={{ marginBottom: '20px', fontWeight: 600, fontSize: '15px' }}>{t('wizard.step1.title')}</h3>

                        {/* Template selector */}
                        {templates.length > 0 && (
                            <div className="form-group">
                                <label className="form-label">{t('wizard.step1.selectTemplate')}</label>
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
                                    <div
                                        onClick={() => setForm({ ...form, template_id: '' })}
                                        style={{
                                            padding: '12px', borderRadius: '8px', cursor: 'pointer', textAlign: 'center',
                                            border: `1px solid ${!form.template_id ? 'var(--accent-primary)' : 'var(--border-default)'}`,
                                            background: !form.template_id ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                                        }}
                                    >
                                        <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)' }}>{t('wizard.step1.custom')}</div>
                                        <div style={{ fontSize: '12px', marginTop: '4px' }}>{t('wizard.step1.custom')}</div>
                                    </div>
                                    {templates.map((tmpl: any) => (
                                        <div
                                            key={tmpl.id}
                                            onClick={() => {
                                                // Parse soul_template to extract personality and boundaries
                                                const sections = parseSoulTemplate(tmpl.soul_template, ['Personality', 'Boundaries']);
                                                setForm({
                                                    ...form,
                                                    template_id: tmpl.id,
                                                    role_description: tmpl.description,
                                                    personality: sections.personality || '',
                                                    boundaries: sections.boundaries || '',
                                                });
                                            }}
                                            style={{
                                                padding: '12px', borderRadius: '8px', cursor: 'pointer', textAlign: 'center',
                                                border: `1px solid ${form.template_id === tmpl.id ? 'var(--accent-primary)' : 'var(--border-default)'}`,
                                                background: form.template_id === tmpl.id ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                                            }}
                                        >
                                            <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)' }}>{tmpl.icon || tmpl.name?.[0] || '·'}</div>
                                            <div style={{ fontSize: '12px', marginTop: '4px' }}>{String(t(`wizard.templates.${tmpl.name}`, tmpl.name))}</div>
                                        </div>
                                    ))}
                                </div>

                                {/* JSON Import */}
                                <div style={{ marginTop: '8px' }}>
                                    <label className="btn btn-ghost" style={{ fontSize: '12px', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
                                        ↑ {t('wizard.step1.importFromJson')}
                                        <input type="file" accept=".json" style={{ display: 'none' }} onChange={e => {
                                            const file = e.target.files?.[0];
                                            if (!file) return;
                                            const reader = new FileReader();
                                            reader.onload = ev => {
                                                try {
                                                    const data = JSON.parse(ev.target?.result as string);
                                                    setForm(prev => ({
                                                        ...prev,
                                                        name: data.name || prev.name,
                                                        role_description: data.role_description || data.description || prev.role_description,
                                                        template_id: '',
                                                    }));
                                                } catch {
                                                    alert('Invalid JSON file');
                                                }
                                            };
                                            reader.readAsText(file);
                                            e.target.value = '';
                                        }} />
                                    </label>
                                </div>
                            </div>
                        )}

                        <div className="form-group">
                            <label className="form-label">{t('agent.fields.name')} <span style={{ color: 'var(--error)' }}>*</span></label>
                            <input className={`form-input${fieldErrors.name ? ' input-error' : ''}`} value={form.name}
                                onChange={(e) => { setForm({ ...form, name: e.target.value }); clearFieldError('name'); }}
                                placeholder={t("wizard.step1.namePlaceholder")} autoFocus />
                            {fieldErrors.name && <div style={{ color: 'var(--error)', fontSize: '12px', marginTop: '4px' }}>{fieldErrors.name}</div>}
                        </div>
                        <div className="form-group">
                            <label className="form-label">{t('agent.fields.role')}</label>
                            <input className={`form-input${fieldErrors.role_description ? ' input-error' : ''}`} value={form.role_description}
                                onChange={(e) => { setForm({ ...form, role_description: e.target.value }); clearFieldError('role_description'); }}
                                placeholder={t('wizard.roleHint')} />
                            {fieldErrors.role_description && <div style={{ color: 'var(--error)', fontSize: '12px', marginTop: '4px' }}>{fieldErrors.role_description}</div>}
                        </div>

                        {/* Model Selection */}
                        <div className="form-group">
                            <label className="form-label">{t('wizard.step1.primaryModel')} <span style={{ color: 'var(--error)' }}>*</span></label>
                            {models.length > 0 ? (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                    {models.filter((m: any) => m.enabled).map((m: any) => (
                                        <label key={m.id} style={{
                                            display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 12px',
                                            background: form.primary_model_id === m.id ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                                            border: `1px solid ${form.primary_model_id === m.id ? 'var(--accent-primary)' : fieldErrors.primary_model_id ? 'var(--error)' : 'var(--border-default)'}`,
                                            borderRadius: '8px', cursor: 'pointer',
                                        }}>
                                            <input type="radio" name="model" checked={form.primary_model_id === m.id}
                                                onChange={() => { setForm({ ...form, primary_model_id: m.id }); clearFieldError('primary_model_id'); }} />
                                            <div>
                                                <div style={{ fontWeight: 500, fontSize: '13px' }}>{m.label}</div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{m.provider}/{m.model}</div>
                                            </div>
                                        </label>
                                    ))}
                                    {fieldErrors.primary_model_id && <div style={{ color: 'var(--error)', fontSize: '12px', marginTop: '2px' }}>{fieldErrors.primary_model_id}</div>}
                                </div>
                            ) : (
                                <div style={{ padding: '16px', background: 'var(--bg-elevated)', borderRadius: '8px', fontSize: '13px', color: 'var(--text-tertiary)', textAlign: 'center' }}>
                                    {t('wizard.step1.noModels')} <span style={{ color: 'var(--accent-primary)', cursor: 'pointer' }} onClick={() => navigate('/enterprise')}>{t('wizard.step1.enterpriseSettings')}</span> {t('wizard.step1.addModels')}
                                </div>
                            )}
                        </div>

                        {/* Token limits */}
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                            <div className="form-group">
                                <label className="form-label">{t('wizard.step1.dailyTokenLimit')}</label>
                                <input className={`form-input${fieldErrors.max_tokens_per_day ? ' input-error' : ''}`} type="number" value={form.max_tokens_per_day}
                                    onChange={(e) => { setForm({ ...form, max_tokens_per_day: e.target.value }); clearFieldError('max_tokens_per_day'); }}
                                    placeholder={t("wizard.step1.unlimited")} />
                                {fieldErrors.max_tokens_per_day && <div style={{ color: 'var(--error)', fontSize: '12px', marginTop: '4px' }}>{fieldErrors.max_tokens_per_day}</div>}
                            </div>
                            <div className="form-group">
                                <label className="form-label">{t('wizard.step1.monthlyTokenLimit')}</label>
                                <input className={`form-input${fieldErrors.max_tokens_per_month ? ' input-error' : ''}`} type="number" value={form.max_tokens_per_month}
                                    onChange={(e) => { setForm({ ...form, max_tokens_per_month: e.target.value }); clearFieldError('max_tokens_per_month'); }}
                                    placeholder={t("wizard.step1.unlimited")} />
                                {fieldErrors.max_tokens_per_month && <div style={{ color: 'var(--error)', fontSize: '12px', marginTop: '4px' }}>{fieldErrors.max_tokens_per_month}</div>}
                            </div>
                        </div>
                    </div>
                )}

                {/* Step 2: Personality */}
                {step === 1 && (
                    <div>
                        <h3 style={{ marginBottom: '20px', fontWeight: 600, fontSize: '15px' }}>{t('wizard.step2.title')}</h3>
                        <div className="form-group">
                            <label className="form-label">{t('agent.fields.personality')}</label>
                            <textarea className="form-textarea" rows={4} value={form.personality}
                                onChange={(e) => setForm({ ...form, personality: e.target.value })}
                                placeholder={t("wizard.step2.personalityPlaceholder")} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">{t('agent.fields.boundaries')}</label>
                            <textarea className="form-textarea" rows={4} value={form.boundaries}
                                onChange={(e) => setForm({ ...form, boundaries: e.target.value })}
                                placeholder={t("wizard.step2.boundariesPlaceholder")} />
                        </div>
                    </div>
                )}

                {/* Step 3: Skills */}
                {step === 2 && (
                    <div>
                        <h3 style={{ marginBottom: '20px', fontWeight: 600, fontSize: '15px' }}>{t('wizard.step3.title')}</h3>
                        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                            {t('wizard.step3.description')}
                        </p>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                            {globalSkills.map((skill: any) => {
                                const isDefault = skill.is_default;
                                const isChecked = form.skill_ids.includes(skill.id);
                                return (
                                    <label key={skill.id} style={{
                                        display: 'flex', alignItems: 'center', gap: '12px', padding: '12px',
                                        background: isChecked ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                                        border: `1px solid ${isChecked ? 'var(--accent-primary)' : 'var(--border-default)'}`,
                                        borderRadius: '8px', cursor: isDefault ? 'default' : 'pointer',
                                        opacity: isDefault ? 0.85 : 1,
                                    }}>
                                        <input type="checkbox"
                                            checked={isChecked}
                                            disabled={isDefault}
                                            onChange={(e) => {
                                                if (isDefault) return;
                                                if (e.target.checked) {
                                                    setForm({ ...form, skill_ids: [...form.skill_ids, skill.id] });
                                                } else {
                                                    setForm({ ...form, skill_ids: form.skill_ids.filter((id: string) => id !== skill.id) });
                                                }
                                            }}
                                        />
                                        <div style={{ fontSize: '18px' }}>{skill.icon}</div>
                                        <div style={{ flex: 1 }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                <span style={{ fontWeight: 500, fontSize: '13px' }}>{skill.name}</span>
                                                {isDefault && <span style={{ fontSize: '10px', padding: '1px 6px', borderRadius: '4px', background: 'var(--accent-primary)', color: '#fff', fontWeight: 500 }}>Required</span>}
                                            </div>
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{skill.description}</div>
                                        </div>
                                    </label>);
                            })}
                            {globalSkills.length === 0 && (
                                <div style={{ padding: '16px', background: 'var(--bg-elevated)', borderRadius: '8px', fontSize: '13px', color: 'var(--text-tertiary)', textAlign: 'center' }}>
                                    No skills available. Add skills in Company Settings.
                                </div>
                            )}
                        </div>
                    </div>
                )}

                {/* Step 4: Permissions */}
                {step === 3 && (
                    <div>
                        <h3 style={{ marginBottom: '20px', fontWeight: 600, fontSize: '15px' }}>{t('wizard.step4.title')}</h3>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '20px' }}>
                            {[
                                { value: 'company', label: t('wizard.step4.companyWide'), desc: t('wizard.step4.companyWideDesc') },
                                { value: 'user', label: t('wizard.step4.selfOnly'), desc: t('wizard.step4.selfOnlyDesc') },
                            ].map((scope) => (
                                <label key={scope.value} style={{
                                    display: 'flex', alignItems: 'center', gap: '12px', padding: '14px',
                                    background: form.permission_scope_type === scope.value ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                                    border: `1px solid ${form.permission_scope_type === scope.value ? 'var(--accent-primary)' : 'var(--border-default)'}`,
                                    borderRadius: '8px', cursor: 'pointer',
                                }}>
                                    <input type="radio" name="scope" checked={form.permission_scope_type === scope.value}
                                        onChange={() => setForm({ ...form, permission_scope_type: scope.value })} />

                                    <div>
                                        <div style={{ fontWeight: 500, fontSize: '13px' }}>{scope.label}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{scope.desc}</div>
                                    </div>
                                </label>
                            ))}
                        </div>

                        {/* Access Level — only for company scope */}
                        {form.permission_scope_type === 'company' && (
                            <div>
                                <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, marginBottom: '10px' }}>
                                    {t('wizard.step4.accessLevel', 'Default Access Level')}
                                </label>
                                <div style={{ display: 'flex', gap: '8px' }}>
                                    {([
                                        { value: 'use', icon: <Eye size={14} />, label: t('wizard.step4.useLevel', 'Use'), desc: t('wizard.step4.useDesc', 'Can use Task, Chat, Tools, Skills, Workspace') },
                                        { value: 'manage', icon: <Settings size={14} />, label: t('wizard.step4.manageLevel', 'Manage'), desc: t('wizard.step4.manageDesc', 'Full access including Settings, Mind, Relationships') },
                                    ] as const).map((lvl) => (
                                        <label key={lvl.value} style={{
                                            flex: 1, display: 'flex', alignItems: 'flex-start', gap: '10px', padding: '12px',
                                            background: form.permission_access_level === lvl.value ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                                            border: `1px solid ${form.permission_access_level === lvl.value ? 'var(--accent-primary)' : 'var(--border-default)'}`,
                                            borderRadius: '8px', cursor: 'pointer',
                                        }}>
                                            <input type="radio" name="access_level" checked={form.permission_access_level === lvl.value}
                                                onChange={() => setForm({ ...form, permission_access_level: lvl.value })} style={{ marginTop: '2px' }} />
                                            <div>
                                                <div style={{ fontWeight: 500, fontSize: '13px' }}>{lvl.icon} {lvl.label}</div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{lvl.desc}</div>
                                            </div>
                                        </label>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {/* Step 5: Channel */}
                {step === 4 && (
                    <div>
                        <h3 style={{ marginBottom: '20px', fontWeight: 600, fontSize: '15px' }}>{t('wizard.step5.title', 'Channel Configuration')}</h3>
                        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                            {t('wizard.step5.description', 'Connect messaging platforms to enable your agent to communicate through different channels.')}
                        </p>

                        <ChannelConfig mode="create" values={channelValues} onChange={setChannelValues} />

                        {Object.keys(channelValues).length === 0 && (
                            <div style={{ padding: '12px', background: 'var(--bg-secondary)', borderRadius: '8px', fontSize: '12px', color: 'var(--text-tertiary)', textAlign: 'center', marginTop: '12px' }}>
                                {t('wizard.step5.skipHint')}
                            </div>
                        )}
                    </div>
                )}


            </div>

            {/* Summary sidebar */}
            {selectedModel && (
                <div style={{ marginTop: '16px', padding: '12px', background: 'var(--bg-elevated)', borderRadius: '8px', fontSize: '12px', color: 'var(--text-secondary)', maxWidth: '640px', marginBottom: '80px' }}>
                    <strong>{form.name || t('wizard.summary.unnamed')}</strong> · {t('wizard.summary.model')}: {selectedModel.label}
                    {form.max_tokens_per_day && ` · ${t('wizard.summary.dailyLimit')}: ${Number(form.max_tokens_per_day).toLocaleString()}`}
                </div>
            )}
            {!selectedModel && <div style={{ marginBottom: '80px' }}></div>}

            {/* Navigation — sticky footer at the bottom */}
            <div style={{
                position: 'fixed', bottom: 0, left: 'var(--sidebar-width)', right: 0,
                background: 'var(--bg-primary)', borderTop: '1px solid var(--border-subtle)',
                padding: '16px 32px', zIndex: 100,
                display: 'flex', justifyContent: 'flex-start',
                transition: 'left var(--transition-default)'
            }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', maxWidth: '640px' }}>
                    <button className="btn btn-secondary" onClick={() => step > 0 ? setStep(step - 1) : navigate('/')}
                        disabled={createMutation.isPending}>
                        {step === 0 ? t('common.cancel') : t('wizard.prev')}
                    </button>
                    {step < STEPS.length - 1 ? (
                        <button className="btn btn-primary" onClick={handleNext}>
                            {t('wizard.next')} →
                        </button>
                    ) : (
                        <button className="btn btn-primary" onClick={handleFinish}
                            disabled={createMutation.isPending}>
                            {createMutation.isPending ? t('common.loading') : t('wizard.finish')}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}