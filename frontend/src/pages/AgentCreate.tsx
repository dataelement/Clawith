import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { agentApi, enterpriseApi, skillApi } from '../services/api';

const STEPS = ['basicInfo', 'personality', 'skills', 'permissions', 'channel'] as const;

export default function AgentCreate() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [step, setStep] = useState(0);
    const [error, setError] = useState('');

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
        feishu_app_id: '',
        feishu_app_secret: '',
        feishu_encrypt_key: '',
        slack_bot_token: '',
        slack_signing_secret: '',
        discord_application_id: '',
        discord_bot_token: '',
        discord_public_key: '',
        skill_ids: [] as string[],
    });
    const [feishuOpen, setFeishuOpen] = useState(false);
    const [slackOpen, setSlackOpen] = useState(false);
    const [discordOpen, setDiscordOpen] = useState(false);

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
        onSuccess: (agent) => {
            queryClient.invalidateQueries({ queryKey: ['agents'] });
            navigate(`/agents/${agent.id}`);
        },
        onError: (err: any) => setError(err.message),
    });

    const handleFinish = () => {
        createMutation.mutate({
            name: form.name,
            role_description: form.role_description,
            personality: form.personality,
            boundaries: form.boundaries,
            primary_model_id: form.primary_model_id || undefined,
            fallback_model_id: form.fallback_model_id || undefined,
            template_id: form.template_id || undefined,
            permission_scope_type: form.permission_scope_type,
            max_tokens_per_day: form.max_tokens_per_day ? Number(form.max_tokens_per_day) : undefined,
            max_tokens_per_month: form.max_tokens_per_month ? Number(form.max_tokens_per_month) : undefined,
            skill_ids: form.skill_ids,
            permission_access_level: form.permission_access_level,
        });
    };

    const selectedModel = models.find((m: any) => m.id === form.primary_model_id);

    return (
        <div>
            <div className="page-header">
                <h1 className="page-title">{t('nav.newAgent')}</h1>
            </div>

            {/* Stepper */}
            <div className="wizard-steps">
                {STEPS.map((s, i) => (
                    <div key={s} style={{ display: 'contents' }}>
                        <div className={`wizard-step ${i === step ? 'active' : i < step ? 'completed' : ''}`}>
                            <div className="wizard-step-number">{i < step ? '✓' : i + 1}</div>
                            <span>{t(`wizard.steps.${s}`)}</span>
                        </div>
                        {i < STEPS.length - 1 && <div className="wizard-connector" />}
                    </div>
                ))}
            </div>

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
                                            onClick={() => setForm({ ...form, template_id: tmpl.id, role_description: tmpl.description })}
                                            style={{
                                                padding: '12px', borderRadius: '8px', cursor: 'pointer', textAlign: 'center',
                                                border: `1px solid ${form.template_id === tmpl.id ? 'var(--accent-primary)' : 'var(--border-default)'}`,
                                                background: form.template_id === tmpl.id ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                                            }}
                                        >
                                            <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)' }}>{tmpl.icon || tmpl.name?.[0] || '·'}</div>
                                            <div style={{ fontSize: '12px', marginTop: '4px' }}>{tmpl.name}</div>
                                        </div>
                                    ))}
                                </div>

                                {/* JSON Import */}
                                <div style={{ marginTop: '8px' }}>
                                    <label className="btn btn-ghost" style={{ fontSize: '12px', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
                                        ↑ Import from JSON
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
                            <label className="form-label">{t('agent.fields.name')} *</label>
                            <input className="form-input" value={form.name}
                                onChange={(e) => setForm({ ...form, name: e.target.value })}
                                placeholder={t("wizard.step1.namePlaceholder")} autoFocus />
                        </div>
                        <div className="form-group">
                            <label className="form-label">{t('agent.fields.role')}</label>
                            <input className="form-input" value={form.role_description}
                                onChange={(e) => setForm({ ...form, role_description: e.target.value })}
                                placeholder={t('wizard.roleHint')} />
                        </div>

                        {/* Model Selection */}
                        <div className="form-group">
                            <label className="form-label">{t('wizard.step1.primaryModel')} *</label>
                            {models.length > 0 ? (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                    {models.filter((m: any) => m.enabled).map((m: any) => (
                                        <label key={m.id} style={{
                                            display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 12px',
                                            background: form.primary_model_id === m.id ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                                            border: `1px solid ${form.primary_model_id === m.id ? 'var(--accent-primary)' : 'var(--border-default)'}`,
                                            borderRadius: '8px', cursor: 'pointer',
                                        }}>
                                            <input type="radio" name="model" checked={form.primary_model_id === m.id}
                                                onChange={() => setForm({ ...form, primary_model_id: m.id })} />
                                            <div>
                                                <div style={{ fontWeight: 500, fontSize: '13px' }}>{m.label}</div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{m.provider}/{m.model}</div>
                                            </div>
                                        </label>
                                    ))}
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
                                <input className="form-input" type="number" value={form.max_tokens_per_day}
                                    onChange={(e) => setForm({ ...form, max_tokens_per_day: e.target.value })}
                                    placeholder={t("wizard.step1.unlimited")} />
                            </div>
                            <div className="form-group">
                                <label className="form-label">{t('wizard.step1.monthlyTokenLimit')}</label>
                                <input className="form-input" type="number" value={form.max_tokens_per_month}
                                    onChange={(e) => setForm({ ...form, max_tokens_per_month: e.target.value })}
                                    placeholder={t("wizard.step1.unlimited")} />
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
                                    No skills available. Add skills in Enterprise Settings.
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
                                    {[
                                        { value: 'use', icon: '👁️', label: t('wizard.step4.useLevel', 'Use'), desc: t('wizard.step4.useDesc', 'Can use Task, Chat, Tools, Skills, Workspace') },
                                        { value: 'manage', icon: '⚙️', label: t('wizard.step4.manageLevel', 'Manage'), desc: t('wizard.step4.manageDesc', 'Full access including Settings, Mind, Relationships') },
                                    ].map((lvl) => (
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

                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                            {/* Slack — expandable */}
                            <div style={{ border: '1px solid var(--border-default)', borderRadius: '8px', overflow: 'hidden' }}>
                                <div
                                    onClick={() => setSlackOpen(!slackOpen)}
                                    style={{
                                        display: 'flex', alignItems: 'center', gap: '12px', padding: '14px',
                                        cursor: 'pointer', background: slackOpen ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                                        borderBottom: slackOpen ? '1px solid var(--border-default)' : 'none',
                                    }}
                                >
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M6.194 14.644a2.194 2.194 0 110 4.388 2.194 2.194 0 010-4.388zm-2.194 0H0v-2.194a2.194 2.194 0 014.388 0v2.194zm16.612 0a2.194 2.194 0 110 4.388 2.194 2.194 0 010-4.388zm0-2.194a2.194 2.194 0 010-4.388 2.194 2.194 0 010 4.388zm0 0v2.194h2.194A2.194 2.194 0 0024 12.45a2.194 2.194 0 00-2.194-2.194h-1.194zm-16.612 0a2.194 2.194 0 010-4.388 2.194 2.194 0 010 4.388zm0 0v2.194H2A2.194 2.194 0 010 12.45a2.194 2.194 0 012.194-2.194h1.806z" fill="#611F69" opacity=".4" /><path d="M9.388 4.388a2.194 2.194 0 110-4.388 2.194 2.194 0 010 4.388zm0 2.194v-2.194H7.194A2.194 2.194 0 005 6.582a2.194 2.194 0 002.194 2.194h2.194zm0 12.612a2.194 2.194 0 110 4.388 2.194 2.194 0 010-4.388zm0-2.194v2.194H7.194A2.194 2.194 0 005 17.418a2.194 2.194 0 002.194 2.194h.194zm4.224-12.612a2.194 2.194 0 110-4.388 2.194 2.194 0 010 4.388zm2.194 0H13.612V2.194a2.194 2.194 0 014.388 0v2.194zm-2.194 14.806a2.194 2.194 0 110 4.388 2.194 2.194 0 010-4.388zm-2.194 0h2.194v2.194a2.194 2.194 0 01-4.388 0v-2.194z" fill="#611F69" /></svg>
                                    <div style={{ flex: 1 }}>
                                        <div style={{ fontWeight: 500, fontSize: '13px' }}>Slack</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>Slack Bot</div>
                                    </div>
                                    {form.slack_bot_token && <span style={{ fontSize: '10px', padding: '2px 8px', borderRadius: '10px', background: 'rgba(16,185,129,0.15)', color: 'rgb(16,185,129)', fontWeight: 500 }}>Configured</span>}
                                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', transition: 'transform 0.2s', transform: slackOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>▼</span>
                                </div>
                                {slackOpen && (
                                    <div style={{ padding: '16px' }}>
                                        <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                            <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                            </summary>
                                            <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                <li>{t('channelGuide.slack.step1')}</li>
                                                <li>{t('channelGuide.slack.step2')}</li>
                                                <li>{t('channelGuide.slack.step3')}</li>
                                                <li>{t('channelGuide.slack.step4')}</li>
                                                <li>{t('channelGuide.slack.step5')}</li>
                                                <li>{t('channelGuide.slack.step6')}</li>
                                                <li>{t('channelGuide.slack.step7')}</li>
                                                <li>{t('channelGuide.slack.step8')}</li>
                                            </ol>
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>💡 {t('channelGuide.slack.note')}</div>
                                        </details>
                                        <div className="form-group">
                                            <label className="form-label">Bot Token</label>
                                            <input className="form-input" value={form.slack_bot_token}
                                                onChange={(e) => setForm({ ...form, slack_bot_token: e.target.value })}
                                                placeholder="xoxb-..." />
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label">Signing Secret</label>
                                            <input className="form-input" type="password" value={form.slack_signing_secret}
                                                onChange={(e) => setForm({ ...form, slack_signing_secret: e.target.value })} />
                                        </div>
                                    </div>
                                )}
                            </div>

                            {/* Discord — expandable */}
                            <div style={{ border: '1px solid var(--border-default)', borderRadius: '8px', overflow: 'hidden' }}>
                                <div
                                    onClick={() => setDiscordOpen(!discordOpen)}
                                    style={{
                                        display: 'flex', alignItems: 'center', gap: '12px', padding: '14px',
                                        cursor: 'pointer', background: discordOpen ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                                        borderBottom: discordOpen ? '1px solid var(--border-default)' : 'none',
                                    }}
                                >
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="#5865F2"><path d="M20.317 4.37a19.791 19.791 0 00-4.885-1.515.074.074 0 00-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 00-5.487 0 12.64 12.64 0 00-.617-1.25.077.077 0 00-.079-.037A19.736 19.736 0 003.677 4.37a.07.07 0 00-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 00.031.057 19.9 19.9 0 005.993 3.03.078.078 0 00.084-.028 14.09 14.09 0 001.226-1.994.076.076 0 00-.041-.106 13.107 13.107 0 01-1.872-.892.077.077 0 01-.008-.128 10.2 10.2 0 00.372-.292.074.074 0 01.077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 01.078.01c.12.098.246.198.373.292a.077.077 0 01-.006.127 12.299 12.299 0 01-1.873.892.077.077 0 00-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 00.084.028 19.839 19.839 0 006.002-3.03.077.077 0 00.032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 00-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z" /></svg>
                                    <div style={{ flex: 1 }}>
                                        <div style={{ fontWeight: 500, fontSize: '13px' }}>Discord</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>Slash Commands (/ask)</div>
                                    </div>
                                    {form.discord_bot_token && <span style={{ fontSize: '10px', padding: '2px 8px', borderRadius: '10px', background: 'rgba(16,185,129,0.15)', color: 'rgb(16,185,129)', fontWeight: 500 }}>Configured</span>}
                                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', transition: 'transform 0.2s', transform: discordOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>▼</span>
                                </div>
                                {discordOpen && (
                                    <div style={{ padding: '16px' }}>
                                        <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                            <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                            </summary>
                                            <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                <li>{t('channelGuide.discord.step1')}</li>
                                                <li>{t('channelGuide.discord.step2')}</li>
                                                <li>{t('channelGuide.discord.step3')}</li>
                                                <li>{t('channelGuide.discord.step4')}</li>
                                                <li>{t('channelGuide.discord.step5')}</li>
                                                <li>{t('channelGuide.discord.step6')}</li>
                                                <li>{t('channelGuide.discord.step7')}</li>
                                            </ol>
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>💡 {t('channelGuide.discord.note')}</div>
                                        </details>
                                        <div className="form-group">
                                            <label className="form-label">Application ID</label>
                                            <input className="form-input" value={form.discord_application_id}
                                                onChange={(e) => setForm({ ...form, discord_application_id: e.target.value })}
                                                placeholder="1234567890" />
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label">Bot Token</label>
                                            <input className="form-input" type="password" value={form.discord_bot_token}
                                                onChange={(e) => setForm({ ...form, discord_bot_token: e.target.value })} />
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label">Public Key</label>
                                            <input className="form-input" value={form.discord_public_key}
                                                onChange={(e) => setForm({ ...form, discord_public_key: e.target.value })} />
                                        </div>
                                    </div>
                                )}
                            </div>

                            {/* Feishu — expandable */}
                            <div style={{ border: '1px solid var(--border-default)', borderRadius: '8px', overflow: 'hidden' }}>
                                <div
                                    onClick={() => setFeishuOpen(!feishuOpen)}
                                    style={{
                                        display: 'flex', alignItems: 'center', gap: '12px', padding: '14px',
                                        cursor: 'pointer', background: feishuOpen ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                                        borderBottom: feishuOpen ? '1px solid var(--border-default)' : 'none',
                                    }}
                                >
                                    <span style={{ fontSize: '20px' }}>🐦</span>
                                    <div style={{ flex: 1 }}>
                                        <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('wizard.step5.feishu', 'Feishu / Lark')}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('wizard.step5.feishuDesc', 'Connect via Feishu Open Platform bot')}</div>
                                    </div>
                                    {form.feishu_app_id && <span style={{ fontSize: '10px', padding: '2px 8px', borderRadius: '10px', background: 'rgba(16,185,129,0.15)', color: 'rgb(16,185,129)', fontWeight: 500 }}>Configured</span>}
                                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', transition: 'transform 0.2s', transform: feishuOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>▼</span>
                                </div>
                                {feishuOpen && (
                                    <div style={{ padding: '16px' }}>
                                        <div style={{ background: 'var(--bg-elevated)', borderRadius: '8px', padding: '12px', marginBottom: '14px', fontSize: '12px', lineHeight: '1.8' }}>
                                            <strong>{t('wizard.step5.configSteps')}</strong>
                                            <ol style={{ paddingLeft: '16px', margin: '6px 0 0' }}>
                                                <li>{t('wizard.step5.step1Feishu')} <a href="https://open.feishu.cn" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-primary)' }}>{t('wizard.step5.feishuPlatform')}</a></li>
                                                <li>{t('wizard.step5.step2Feishu')}</li>
                                                <li>{t('wizard.step5.step3Feishu')}</li>
                                                <li>{t('wizard.step5.step4Feishu')}</li>
                                            </ol>
                                        </div>
                                        <details style={{ marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                            <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--text-primary)', userSelect: 'none', listStyle: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                <span style={{ fontSize: '10px' }}>▶</span> {t('channelGuide.setupGuide')}
                                            </summary>
                                            <ol style={{ paddingLeft: '16px', margin: '8px 0', lineHeight: 1.9 }}>
                                                <li>{t('channelGuide.feishu.step1')}</li>
                                                <li>{t('channelGuide.feishu.step2')}</li>
                                                <li>{t('channelGuide.feishu.step3')}</li>
                                                <li>{t('channelGuide.feishu.step4')}</li>
                                                <li>{t('channelGuide.feishu.step5')}</li>
                                                <li>{t('channelGuide.feishu.step6')}</li>
                                                <li>{t('channelGuide.feishu.step7')}</li>
                                                <li>{t('channelGuide.feishu.step8')}</li>
                                            </ol>
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', background: 'var(--bg-secondary)', padding: '6px 10px', borderRadius: '6px' }}>💡 {t('channelGuide.feishu.note')}</div>
                                        </details>
                                        <div className="form-group">
                                            <label className="form-label">App ID</label>
                                            <input className="form-input" value={form.feishu_app_id}
                                                onChange={(e) => setForm({ ...form, feishu_app_id: e.target.value })}
                                                placeholder="cli_xxxxxxxxxxxxxxxx" />
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label">App Secret</label>
                                            <input className="form-input" type="password" value={form.feishu_app_secret}
                                                onChange={(e) => setForm({ ...form, feishu_app_secret: e.target.value })}
                                                placeholder="xxxxxxxxxxxxxxxxxxxxxxxx" />
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label">{t('wizard.step5.encryptKeyOptional')}</label>
                                            <input className="form-input" value={form.feishu_encrypt_key}
                                                onChange={(e) => setForm({ ...form, feishu_encrypt_key: e.target.value })}
                                                placeholder={t('wizard.step5.encryptKeyPlaceholder')} />
                                        </div>
                                    </div>
                                )}
                            </div>

                            {/* Microsoft Teams (configure after creation in Agent settings) */}
                            <div style={{ border: '1px solid var(--border-default)', borderRadius: '8px', overflow: 'hidden' }}>
                                <div
                                    style={{
                                        display: 'flex', alignItems: 'center', gap: '12px', padding: '14px',
                                        background: 'var(--bg-elevated)',
                                    }}
                                >
                                    {/* Simple Teams icon */}
                                    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
                                        <rect x="2.5" y="4" width="12" height="12" rx="3" fill="#6264A7" />
                                        <path
                                            d="M7 9h5c.3 0 .5.2.5.5v1.1c0 .3-.2.5-.5.5H10.9v4.7c0 .3-.2.5-.5.5H8.7c-.3 0-.5-.2-.5-.5v-4.7H7c-.3 0-.5-.2-.5-.5V9.5C6.5 9.2 6.7 9 7 9Z"
                                            fill="#FFFFFF"
                                        />
                                        <circle cx="18.5" cy="7.5" r="2.1" fill="#7B83EB" />
                                        <rect x="16.4" y="10.3" width="4.6" height="5.8" rx="2" fill="#7B83EB" />
                                    </svg>
                                    <div style={{ flex: 1 }}>
                                        <div style={{ fontWeight: 500, fontSize: '13px' }}>Microsoft Teams</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                            Teams Bot (configure details in Agent Settings after creation)
                                        </div>
                                    </div>
                                    <span style={{ fontSize: '10px', padding: '2px 8px', borderRadius: '10px', background: 'var(--bg-secondary)', color: 'var(--text-tertiary)', fontWeight: 500 }}>
                                        Available
                                    </span>
                                </div>
                            </div>

                            {/* Other channels — coming soon */}
                            {[
                                { icon: '💬', name: t('wizard.step5.dingtalk', 'DingTalk'), desc: t('wizard.step5.dingtalkDesc', 'DingTalk custom robot integration') },
                                { icon: '🏢', name: t('wizard.step5.wecom', 'WeCom'), desc: t('wizard.step5.wecomDesc', 'WeCom (企业微信) application bot') },
                                { icon: '📱', name: 'WhatsApp', desc: t('wizard.step5.whatsappDesc', 'WhatsApp Business API integration') },
                            ].map((ch) => (
                                <div key={ch.name} style={{
                                    display: 'flex', alignItems: 'center', gap: '12px', padding: '14px',
                                    background: 'var(--bg-elevated)', border: '1px solid var(--border-default)',
                                    borderRadius: '8px', opacity: 0.6,
                                }}>
                                    <span style={{ fontSize: '20px' }}>{ch.icon}</span>
                                    <div style={{ flex: 1 }}>
                                        <div style={{ fontWeight: 500, fontSize: '13px' }}>{ch.name}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{ch.desc}</div>
                                    </div>
                                    <span style={{ fontSize: '10px', padding: '2px 8px', borderRadius: '10px', background: 'var(--bg-secondary)', color: 'var(--text-tertiary)', fontWeight: 500 }}>Coming Soon</span>
                                </div>
                            ))}
                        </div>

                        {!form.feishu_app_id && !form.slack_bot_token && !form.discord_bot_token && (
                            <div style={{ padding: '12px', background: 'var(--bg-secondary)', borderRadius: '8px', fontSize: '12px', color: 'var(--text-tertiary)', textAlign: 'center', marginTop: '12px' }}>
                                {t('wizard.step5.skipHint')}
                            </div>
                        )}
                    </div>
                )}

                {/* Navigation */}
                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '24px' }}>
                    <button className="btn btn-secondary" onClick={() => step > 0 ? setStep(step - 1) : navigate('/')}
                        disabled={createMutation.isPending}>
                        {step === 0 ? t('common.cancel') : t('wizard.prev')}
                    </button>
                    {step < STEPS.length - 1 ? (
                        <button className="btn btn-primary" onClick={() => setStep(step + 1)}
                            disabled={step === 0 && !form.name}>
                            {t('wizard.next')} →
                        </button>
                    ) : (
                        <button className="btn btn-primary" onClick={handleFinish}
                            disabled={createMutation.isPending || !form.name}>
                            {createMutation.isPending ? t('common.loading') : t('wizard.finish')}
                        </button>
                    )}
                </div>
            </div>

            {/* Summary sidebar */}
            {selectedModel && (
                <div style={{ marginTop: '16px', padding: '12px', background: 'var(--bg-elevated)', borderRadius: '8px', fontSize: '12px', color: 'var(--text-secondary)', maxWidth: '640px' }}>
                    <strong>{form.name || t('wizard.summary.unnamed')}</strong> · {t('wizard.summary.model')}: {selectedModel.label}
                    {form.max_tokens_per_day && ` · ${t('wizard.summary.dailyLimit')}: ${Number(form.max_tokens_per_day).toLocaleString()}`}
                </div>
            )}
        </div>
    );
}
