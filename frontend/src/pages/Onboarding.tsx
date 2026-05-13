import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { IconArrowLeft, IconArrowRight, IconCheck, IconWorld } from '@tabler/icons-react';
import { onboardingApi } from '../services/api';
import { useAuthStore } from '../stores';

type Step = 'assistant' | 'opening';

export default function Onboarding() {
    const { i18n } = useTranslation();
    const isZh = i18n.language.startsWith('zh');
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    const user = useAuthStore((s) => s.user);
    const mode = (searchParams.get('mode') === 'join' ? 'join' : 'create') as 'create' | 'join';
    const [step, setStep] = useState<Step>('assistant');
    const [assistantId, setAssistantId] = useState<string | null>(null);
    const [assistantName, setAssistantName] = useState('Clawiee');
    const [personality, setPersonality] = useState('warm');
    const [workStyle, setWorkStyle] = useState('concise');
    const [boundaries, setBoundaries] = useState('');
    const [expanded, setExpanded] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        document.documentElement.setAttribute('data-theme', localStorage.getItem('theme') || 'light');
    }, []);

    useEffect(() => {
        let cancelled = false;
        onboardingApi.start(mode)
            .then((status) => {
                if (cancelled) return;
                if (status?.status === 'completed' && status.personal_assistant_agent_id) {
                    navigate(`/agents/${status.personal_assistant_agent_id}/chat`, { replace: true });
                    return;
                }
                if (status?.personal_assistant_agent_id) {
                    setAssistantId(status.personal_assistant_agent_id);
                    setStep('opening');
                }
            })
            .catch((err) => setError(err.message || 'Failed to start onboarding'));
        return () => { cancelled = true; };
    }, [mode, navigate]);

    const personalityOptions = useMemo(() => [
        { id: 'warm', zh: '温和', en: 'Warm' },
        { id: 'precise', zh: '严谨', en: 'Precise' },
        { id: 'quiet', zh: '幽默', en: 'Witty' },
        { id: 'direct', zh: '直接', en: 'Direct' },
    ], []);
    const workStyleOptions = useMemo(() => [
        { id: 'concise', zh: '简洁', en: 'Concise' },
        { id: 'efficient', zh: '高效', en: 'Efficient' },
        { id: 'detailed', zh: '详尽', en: 'Detailed' },
        { id: 'steady', zh: '保守', en: 'Steady' },
    ], []);

    const createAssistant = async () => {
        setError('');
        setLoading(true);
        try {
            const result = await onboardingApi.createPersonalAssistant({
                name: assistantName.trim(),
                personality,
                work_style: workStyle,
                boundaries,
            });
            const nextId = result?.agent?.id || result?.onboarding?.personal_assistant_agent_id;
            setAssistantId(nextId);
            setStep('opening');
        } catch (err: any) {
            setError(err.message || 'Failed to create personal assistant');
        } finally {
            setLoading(false);
        }
    };

    const enterOffice = () => {
        if (!assistantId) return;
        navigate(`/plaza?tour=company&assistantId=${assistantId}`);
    };

    const toggleLang = () => i18n.changeLanguage(isZh ? 'en' : 'zh');

    if (!user?.tenant_id) {
        return (
            <div className="onboarding-page onboarding-page--light">
                <div className="onboarding-center-card">
                    <h1>{isZh ? '先创建或加入一家公司' : 'Create or join a company first'}</h1>
                    <button className="onboarding-primary-btn" onClick={() => navigate('/setup-company')}>
                        {isZh ? '去设置公司' : 'Set up company'}
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className={step === 'assistant' ? 'onboarding-page onboarding-page--dark' : 'onboarding-page onboarding-page--light'}>
            <button className="onboarding-lang" type="button" onClick={toggleLang} aria-label="Language">
                <IconWorld size={18} stroke={1.8} />
            </button>

            {step === 'assistant' && (
                <>
                    <button className="onboarding-back" onClick={() => navigate(-1)}>
                        <IconArrowLeft size={15} /> {isZh ? '返回' : 'Back'}
                    </button>
                    <button className="onboarding-skip" onClick={createAssistant} disabled={loading}>
                        {isZh ? '暂时跳过' : 'Skip for now'}
                    </button>
                    <div className="assistant-stage">
                        <div className="assistant-spotlight" aria-hidden="true">
                            <div className="assistant-light-beam" />
                            <div className="assistant-silhouette">
                                <span />
                                <strong />
                            </div>
                        </div>
                        <div className="assistant-panel">
                            <div className="onboarding-kicker onboarding-kicker--dark">{isZh ? '第 2 幕 · 雇佣第一位员工' : 'Act 2 · Hire the first employee'}</div>
                            <h1>{isZh ? '有人来应聘你的第一位私人助理。' : 'Someone is here to become your first private assistant.'}</h1>
                            <p>{isZh ? 'ta 只向你汇报，帮你打理日常。先给 ta 起个名字。' : 'They report only to you and help with daily coordination. Give them a name first.'}</p>
                            {error && <div className="onboarding-error">{error}</div>}
                            <input
                                className="assistant-name-input"
                                value={assistantName}
                                onChange={(e) => setAssistantName(e.target.value)}
                                placeholder={isZh ? '助理的名字' : 'Assistant name'}
                            />
                            <button className="assistant-expand" type="button" onClick={() => setExpanded(v => !v)}>
                                {isZh ? '进阶设置 · 性格 · 办事风格 · 禁忌' : 'Advanced · personality · work style · boundaries'}
                                <span>{expanded ? (isZh ? '收起' : 'Collapse') : (isZh ? '展开' : 'Expand')}</span>
                            </button>
                            {expanded && (
                                <div className="assistant-options">
                                    <div>
                                        <label>{isZh ? '性格' : 'Personality'}</label>
                                        <div className="assistant-chips">
                                            {personalityOptions.map((item) => (
                                                <button key={item.id} type="button" className={personality === item.id ? 'active' : ''} onClick={() => setPersonality(item.id)}>
                                                    {isZh ? item.zh : item.en}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                    <div>
                                        <label>{isZh ? '办事风格' : 'Work style'}</label>
                                        <div className="assistant-chips">
                                            {workStyleOptions.map((item) => (
                                                <button key={item.id} type="button" className={workStyle === item.id ? 'active' : ''} onClick={() => setWorkStyle(item.id)}>
                                                    {isZh ? item.zh : item.en}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                    <textarea
                                        value={boundaries}
                                        onChange={(e) => setBoundaries(e.target.value)}
                                        placeholder={isZh ? '绝对不要做的事情（可留空）' : 'Things they should never do (optional)'}
                                    />
                                </div>
                            )}
                            <div className="assistant-footer">
                                <button className="onboarding-primary-btn" onClick={createAssistant} disabled={loading || !assistantName.trim()}>
                                    {loading ? <span className="login-spinner" /> : (isZh ? '欢迎入职' : 'Welcome aboard')} <IconArrowRight size={16} />
                                </button>
                            </div>
                        </div>
                    </div>
                </>
            )}

            {step === 'opening' && (
                <div className="opening-stage">
                    <div className="opening-confetti" aria-hidden="true">
                        <span /><span /><span /><span /><span />
                    </div>
                    <div className="opening-cards" aria-hidden="true">
                        <div>灵</div>
                        <div>{isZh ? '你' : 'You'}</div>
                        <div>?</div>
                    </div>
                    <div className="onboarding-kicker">{isZh ? '第 3 幕 · 灯光亮起' : 'Act 3 · Lights on'}</div>
                    <h1>{isZh ? '公司正式开业。' : 'The company is open.'}</h1>
                    <p>{isZh ? '你的第一位员工已就位。先带你看一眼办公室。' : 'Your first employee is in place. Let us take a quick look around the office.'}</p>
                    {error && <div className="onboarding-error">{error}</div>}
                    <button className="onboarding-primary-btn" onClick={enterOffice} disabled={!assistantId}>
                        {isZh ? '进入办公室' : 'Enter office'} <IconArrowRight size={16} />
                    </button>
                    <div className="opening-check">
                        <IconCheck size={15} /> {assistantName || (isZh ? '私人助理' : 'Private Assistant')} {isZh ? '已入职' : 'is ready'}
                    </div>
                </div>
            )}
        </div>
    );
}
