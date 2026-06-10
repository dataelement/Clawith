import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { IconArrowRight } from '@tabler/icons-react';
import { onboardingApi } from '../services/api';
import { useAuthStore } from '../stores';
import { AtlasFrame, StarField, OrbitPlate, UniverseMap } from '../components/atlas';

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
    const [personalities, setPersonalities] = useState<string[]>(['warm']);
    const togglePersonality = (id: string) => {
        setPersonalities((prev) =>
            prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]
        );
    };
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
                personality: personalities.join(', ') || 'warm',
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
            <AtlasFrame onToggleLang={toggleLang}>
                <div className="atlas-screen-center atlas-screen-pad">
                    <h1 className="atlas-h1">{isZh ? '先创建或加入一家公司' : 'Create or join a company first'}</h1>
                    <button className="atlas-btn atlas-btn--primary" onClick={() => navigate('/setup-company')}>
                        {isZh ? '去设置公司' : 'Set up company'}
                    </button>
                </div>
            </AtlasFrame>
        );
    }

    if (step === 'assistant') {
        return (
            <AtlasFrame onBack={() => navigate(-1)} onToggleLang={toggleLang}>
                <div className="atlas-screen-split">
                    <div className="atlas-screen-plate atlas-screen-plate--gridded">
                        <div className="atlas-grid-bg" aria-hidden="true" />
                        <OrbitPlate
                            assistantLabel={`I — ${(assistantName || 'ASSISTANT').toUpperCase()}`}
                            founderLabel={isZh ? 'FOUNDER' : 'FOUNDER'}
                            width={520}
                        />
                    </div>
                    <div className="atlas-screen-form atlas-screen-form--padded">
                        <h1 className="atlas-h1">
                            {isZh ? (
                                <>见见你的<em>第一位员工</em>。</>
                            ) : (
                                <>Meet your <em>first employee.</em></>
                            )}
                        </h1>
                        <p className="atlas-body atlas-body--muted">{isZh
                            ? '你的私人助理 —— 打理日程、备忘、和你不愿亲自处理的事。给 ta 起个名字。'
                            : "A personal assistant — for your calendar, your memory, and the things you'd rather hand off. Name them."}</p>
                        {error && <div className="atlas-error">{error}</div>}

                        <div className="atlas-input-wrap">
                            <div className="atlas-input-row">
                                <span className="atlas-input-label">{isZh ? '名字' : 'NAME'}</span>
                                <input
                                    className="atlas-input atlas-input--serif"
                                    value={assistantName}
                                    onChange={(e) => setAssistantName(e.target.value)}
                                    placeholder={isZh ? '助理的名字' : 'Assistant name'}
                                />
                            </div>
                        </div>

                        <button
                            className="atlas-expand"
                            type="button"
                            onClick={() => setExpanded((v) => !v)}
                        >
                            <span className="atlas-body">{isZh ? '定制声音 & 气质' : 'Customise voice & temperament'}</span>
                            <span className="atlas-mono">{expanded ? (isZh ? '收起' : 'COLLAPSE') : (isZh ? '展开' : 'EXPAND')}</span>
                        </button>

                        {/* Personality chips — always visible, multi-select */}
                        <div className="atlas-chip-row">
                            {personalityOptions.map((item) => (
                                <button
                                    key={item.id}
                                    type="button"
                                    className={`atlas-chip${personalities.includes(item.id) ? ' is-active' : ''}`}
                                    aria-pressed={personalities.includes(item.id)}
                                    onClick={() => togglePersonality(item.id)}
                                >
                                    {isZh ? item.zh : item.en}
                                </button>
                            ))}
                        </div>

                        {/* Advanced — work style + boundaries — collapsed by default */}
                        {expanded && (
                            <div className="atlas-options">
                                <div>
                                    <span className="atlas-input-label" style={{ display: 'block', marginBottom: 10 }}>
                                        {isZh ? '办事风格' : 'WORK STYLE'}
                                    </span>
                                    <div className="atlas-chip-row">
                                        {workStyleOptions.map((item) => (
                                            <button
                                                key={item.id}
                                                type="button"
                                                className={`atlas-chip${workStyle === item.id ? ' is-active' : ''}`}
                                                onClick={() => setWorkStyle(item.id)}
                                            >
                                                {isZh ? item.zh : item.en}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                                <textarea
                                    className="atlas-textarea"
                                    value={boundaries}
                                    onChange={(e) => setBoundaries(e.target.value)}
                                    placeholder={isZh ? '绝对不要做的事情（可留空）' : 'Things they should never do (optional)'}
                                />
                            </div>
                        )}

                        <div className="atlas-cta-row">
                            <button
                                className="atlas-btn atlas-btn--primary"
                                onClick={createAssistant}
                                disabled={loading || !assistantName.trim()}
                            >
                                {loading ? '…' : (isZh ? '欢迎入职' : 'Welcome aboard')}
                                <IconArrowRight size={14} stroke={1.5} />
                            </button>
                            <button
                                className="atlas-btn atlas-btn--ghost"
                                type="button"
                                onClick={createAssistant}
                                disabled={loading}
                            >
                                {isZh ? '暂时跳过' : 'Skip for now'}
                            </button>
                        </div>
                    </div>
                </div>
            </AtlasFrame>
        );
    }

    // step === 'opening'
    const displayName = (assistantName || 'Clawiee').toUpperCase();
    return (
        <AtlasFrame onToggleLang={toggleLang}>
            <div className="atlas-screen-split">
                <div className="atlas-screen-plate atlas-screen-plate--snug">
                    <StarField density="low" seed={9} />
                    <UniverseMap size={640} assistantName={displayName} />
                </div>
                <div className="atlas-screen-form atlas-screen-form--padded">
                    <h1 className="atlas-display">
                        {isZh ? (
                            <>灯，亮了。</>
                        ) : (
                            <>The lights<br />are on.</>
                        )}
                    </h1>
                    <p className="atlas-body atlas-body--muted">{isZh
                        ? '一片以你的名字命名的小型星座。从这里开始扩展 —— 一条轨道，一次招募，一颗星，慢慢来。'
                        : 'A small constellation, charted in your name. From here it only grows — one orbit, one hire, one star at a time.'}</p>

                    <div className="atlas-divider" />

                    <ul className="atlas-roster">
                        <li>
                            <span className="atlas-roster-mark" aria-hidden="true">★</span>
                            <span className="atlas-roster-label">{isZh ? '创始人' : 'FOUNDER'}</span>
                            <span className="atlas-roster-value">{isZh ? '你' : 'YOU'}</span>
                        </li>
                        <li>
                            <span className="atlas-roster-mark" aria-hidden="true">○</span>
                            <span className="atlas-roster-label">{isZh ? '1 号员工' : 'NO. 1 EMPLOYEE'}</span>
                            <span className="atlas-roster-value">{displayName}</span>
                        </li>
                        <li>
                            <span className="atlas-roster-mark" aria-hidden="true">·</span>
                            <span className="atlas-roster-label">{isZh ? '未来员工' : 'FUTURE EMPLOYEES'}</span>
                            <span className="atlas-roster-value">∞</span>
                        </li>
                    </ul>

                    {error && <div className="atlas-error">{error}</div>}

                    <div className="atlas-cta-row">
                        <button
                            className="atlas-btn atlas-btn--primary"
                            onClick={enterOffice}
                            disabled={!assistantId}
                        >
                            {isZh ? '进入你的宇宙' : 'Enter your universe'}
                            <IconArrowRight size={14} stroke={1.5} />
                        </button>
                    </div>
                </div>
            </div>
        </AtlasFrame>
    );
}
