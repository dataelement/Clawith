import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { workflowApi } from '../services/api';

interface Step {
    id: string;
    step_order: number;
    title: string;
    agent_name: string | null;
    status: string;
    deliverable_type: string;
    deliverable_data: { content?: string } | null;
    raw_output: string | null;
    started_at: string | null;
    completed_at: string | null;
}

interface WorkflowData {
    id: string;
    title: string;
    user_instruction: string;
    status: string;
    summary: string | null;
    next_steps: string | null;
    steps: Step[];
    created_at: string;
    completed_at: string | null;
}

interface ChatMsg {
    role: 'user' | 'assistant';
    content: string;
}

export default function WorkflowDetail() {
    const { t } = useTranslation();
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const [data, setData] = useState<WorkflowData | null>(null);
    const [selectedStep, setSelectedStep] = useState<number>(0);
    const [copied, setCopied] = useState(false);
    const [retrying, setRetrying] = useState(false);
    const pollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

    // Chat state
    const [chatOpen, setChatOpen] = useState(true);
    const [chatMessages, setChatMessages] = useState<ChatMsg[]>([]);
    const [chatInput, setChatInput] = useState('');
    const [chatLoading, setChatLoading] = useState(false);
    const chatEndRef = useRef<HTMLDivElement>(null);

    const initialLoadRef = useRef(true);

    const fetchData = async () => {
        if (!id) return;
        try {
            const res = await workflowApi.get(id);
            setData(res);
            // Only auto-select running step on first load
            if (initialLoadRef.current) {
                const steps = res.steps || [];
                const running = steps.findIndex((s: Step) => s.status === 'running');
                if (running >= 0) setSelectedStep(running);
                initialLoadRef.current = false;
            }
        } catch { /* ignore */ }
    };

    useEffect(() => {
        fetchData();
        pollRef.current = setInterval(fetchData, 3000);
        return () => clearInterval(pollRef.current);
    }, [id]);

    useEffect(() => {
        if (data && (data.status === 'done' || data.status === 'failed')) {
            clearInterval(pollRef.current);
        }
    }, [data?.status]);

    useEffect(() => {
        chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [chatMessages]);

    const handleChat = async () => {
        if (!chatInput.trim() || chatLoading || !id) return;
        const msg = chatInput.trim();
        setChatInput('');
        setChatMessages((prev) => [...prev, { role: 'user', content: msg }]);
        setChatLoading(true);
        try {
            const res = await workflowApi.chat(id, msg);
            setChatMessages((prev) => [...prev, { role: 'assistant', content: res.reply }]);
        } catch (e: unknown) {
            const errMsg = e instanceof Error ? e.message : 'Failed';
            setChatMessages((prev) => [...prev, { role: 'assistant', content: t('workflow.chat.error', 'Error: {{message}}', { message: errMsg }) }]);
        }
        setChatLoading(false);
    };

    if (!data) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>{t('workflow.detail.loading', 'Loading...')}</div>;

    const steps = data.steps || [];
    const currentStep = steps[selectedStep];
    const doneCount = steps.filter((s) => s.status === 'done').length;
    const totalCount = steps.length;

    const statusIcon = (s: string) => {
        if (s === 'done') return '\u2705';
        if (s === 'running') return '\u23f3';
        if (s === 'failed') return '\u274c';
        return '\u25cb';
    };

    const handleCopy = () => {
        if (currentStep?.raw_output) {
            navigator.clipboard.writeText(currentStep.raw_output);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        }
    };

    const handleImportCRM = async () => {
        if (!currentStep || !id) return;
        try {
            const token = localStorage.getItem('token');
            const res = await fetch(`/api/workflows/${id}/steps/${currentStep.id}/import-to-crm`, {
                method: 'POST', headers: { Authorization: `Bearer ${token}` },
            });
            const d = await res.json();
            if (!res.ok) throw new Error(d.detail || 'Import failed');
            alert(t('workflow.importSuccess', 'Import successful: {{imported}} contacts, skipped {{skipped}}', { imported: d.imported, skipped: d.skipped }));
        } catch (e: unknown) { alert(e instanceof Error ? e.message : 'Failed'); }
    };

    const handleExportPDF = async () => {
        if (!currentStep?.raw_output || !id) return;
        try {
            const token = localStorage.getItem('token');
            const res = await fetch(`/api/workflows/${id}/steps/${currentStep.id}/export-pdf`, {
                method: 'POST', headers: { Authorization: `Bearer ${token}` },
            });
            if (!res.ok) throw new Error('Export failed');
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${currentStep.title || 'report'}.pdf`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (e: unknown) { alert(e instanceof Error ? e.message : 'Failed'); }
    };

    const handleExportCSV = async () => {
        if (!currentStep || !id) return;
        try {
            const token = localStorage.getItem('token');
            const res = await fetch(`/api/workflows/${id}/steps/${currentStep.id}/export`, {
                method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
            });
            if (!res.ok) throw new Error('Export failed');
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `step-${currentStep.id}.csv`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (e: unknown) { alert(e instanceof Error ? e.message : 'Failed'); }
    };

    const handleRetry = async () => {
        if (!id || retrying) return;
        setRetrying(true);
        try {
            await workflowApi.retry(id);
            pollRef.current = setInterval(fetchData, 3000);
            await fetchData();
        } catch (e: unknown) { alert(e instanceof Error ? e.message : 'Retry failed'); }
        setRetrying(false);
    };

    const statusColor = (s: string) => {
        const map: Record<string, string> = { planning: '#f59e0b', running: '#3b82f6', done: '#10b981', failed: '#ef4444' };
        return map[s] || '#888';
    };

    const statusLabel = (s: string) => {
        if (s === 'running') return t('workflow.detail.runningProgress', 'Running {{done}}/{{total}}', { done: doneCount, total: totalCount });
        if (s === 'done') return t('workflow.status.done', 'Completed');
        if (s === 'failed') return t('workflow.status.failed', 'Failed');
        return t('workflow.status.planning', 'Planning');
    };

    const hasFailedSteps = steps.some((s) => s.status === 'failed');

    const chatSuggestions = [
        t('workflow.chat.suggestAnalyze', 'Analyze the customer mining results'),
        t('workflow.chat.suggestPriority', 'Which customers should I prioritize?'),
        t('workflow.chat.suggestOptimize', 'Help me optimize outreach email content'),
        t('workflow.chat.suggestRetry', 'Retry failed steps'),
    ];

    const planningSteps = [
        t('workflow.detail.analyzing', 'Analyzing requirements...'),
        t('workflow.detail.breakingDown', 'Breaking down tasks...'),
        t('workflow.detail.assigning', 'Assigning agents...'),
    ];

    return (
        <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            {/* Header */}
            <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12 }}>
                <button onClick={() => navigate('/app/workflows')} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)', fontSize: 18 }}>
                    \u2190
                </button>
                <div style={{ flex: 1, minWidth: 0 }}>
                    <h3 style={{ margin: 0, fontSize: 15, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{data.title}</h3>
                    <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{data.user_instruction}</span>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    {hasFailedSteps && (
                        <button onClick={handleRetry} disabled={retrying} style={{ ...btnStyle, background: '#ef444420', color: '#ef4444', borderColor: '#ef4444' }}>
                            {retrying ? t('workflow.detail.retrying', 'Retrying...') : t('workflow.detail.retryFailed', 'Retry Failed Steps')}
                        </button>
                    )}
                    <span style={{
                        fontSize: 12, padding: '4px 12px', borderRadius: 12,
                        background: statusColor(data.status) + '20', color: statusColor(data.status), fontWeight: 500,
                    }}>
                        {statusLabel(data.status)}
                    </span>
                    <button onClick={() => setChatOpen(!chatOpen)} style={{ ...btnStyle, fontWeight: 600 }} title={t('workflow.detail.chat', 'Chat')}>
                        {chatOpen ? t('workflow.detail.hideChat', 'Hide Chat') : t('workflow.detail.chat', 'Chat')}
                    </button>
                </div>
            </div>

            {/* Body: steps + deliverable + chat */}
            <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
                {/* Left: Steps */}
                <div style={{ width: 220, borderRight: '1px solid var(--border)', overflowY: 'auto', padding: '8px 0', flexShrink: 0 }}>
                    {steps.length === 0 && (data.status === 'planning' || data.status === 'running') ? (
                        <div style={{ padding: '24px 14px', textAlign: 'center' }}>
                            <div style={{ fontSize: 32, marginBottom: 12, animation: 'pulse 2s infinite' }}>&#x1f9e0;</div>
                            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6 }}>{t('workflow.detail.planningTitle', 'Agents are planning')}</div>
                            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
                                {t('workflow.detail.planningDesc', 'AI team is analyzing your requirements, breaking down tasks and assigning to the best agents...')}
                            </div>
                            <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
                                {planningSteps.map((text, i) => (
                                    <div key={i} style={{
                                        padding: '6px 12px', borderRadius: 6, background: 'var(--bg-secondary)',
                                        fontSize: 11, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 6,
                                    }}>
                                        <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)', opacity: 0.5, animation: `pulse 1.5s infinite ${i * 0.3}s` }} />
                                        {text}
                                    </div>
                                ))}
                            </div>
                        </div>
                    ) : steps.length === 0 ? (
                        <div style={{ padding: '24px 14px', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>{t('workflow.detail.noSteps', 'No task steps yet')}</div>
                    ) : (
                        steps.map((step, i) => (
                            <div
                                key={step.id}
                                onClick={() => setSelectedStep(i)}
                                style={{
                                    padding: '8px 14px', cursor: 'pointer',
                                    background: selectedStep === i ? 'var(--bg-hover)' : 'transparent',
                                    borderLeft: selectedStep === i ? '3px solid var(--accent)' : '3px solid transparent',
                                }}
                            >
                                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                                    <span style={{ fontSize: 13 }}>{statusIcon(step.status)}</span>
                                    <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-primary)' }}>
                                        {step.agent_name || `Agent ${i + 1}`}
                                    </span>
                                </div>
                                <div style={{ fontSize: 11, color: 'var(--text-secondary)', paddingLeft: 21, lineHeight: 1.4 }}>
                                    {step.title}
                                </div>
                            </div>
                        ))
                    )}

                    {/* Summary at bottom */}
                    {data.status === 'done' && data.summary && (
                        <div style={{ padding: '12px 14px', borderTop: '1px solid var(--border)', marginTop: 8 }}>
                            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>{t('workflow.detail.summary', 'Summary')}</div>
                            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>{data.summary}</div>
                        </div>
                    )}
                </div>

                {/* Center: Deliverable */}
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
                    {currentStep ? (
                        <>
                            <div style={{ padding: '6px 16px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 8, alignItems: 'center' }}>
                                <span style={{ fontSize: 12, color: 'var(--text-secondary)', flex: 1 }}>
                                    {currentStep.title} — {currentStep.deliverable_type}
                                </span>
                                {currentStep.raw_output && (
                                    <>
                                        <button onClick={handleCopy} style={btnStyle}>{copied ? t('workflow.detail.copied', 'Copied') : t('workflow.detail.copy', 'Copy')}</button>
                                        {currentStep.deliverable_type === 'table' && (
                                            <>
                                                <button onClick={handleExportCSV} style={btnStyle}>CSV</button>
                                                <button onClick={handleImportCRM} style={{ ...btnStyle, background: '#10b98120', color: '#10b981', borderColor: '#10b981' }}>{t('workflow.detail.importCRM', 'Import to CRM')}</button>
                                            </>
                                        )}
                                        <button onClick={handleExportPDF} style={btnStyle}>PDF</button>
                                    </>
                                )}
                            </div>
                            <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
                                {currentStep.status === 'running' ? (
                                    <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-tertiary)' }}>
                                        <div style={{ fontSize: 28, marginBottom: 8 }}>\u23f3</div>
                                        <div>{t('workflow.detail.agentWorking', '{{name}} is working...', { name: currentStep.agent_name })}</div>
                                    </div>
                                ) : currentStep.status === 'pending' ? (
                                    <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-tertiary)' }}>{t('workflow.detail.pendingExecution', 'Waiting for execution')}</div>
                                ) : currentStep.raw_output ? (
                                    <div className="workflow-markdown" style={{ fontSize: 14, lineHeight: 1.8, color: 'var(--text-primary)' }}>
                                        <Markdown
                                            remarkPlugins={[remarkGfm]}
                                            components={{
                                                h1: ({ children }) => <h1 style={{ fontSize: 22, fontWeight: 700, margin: '20px 0 10px', borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>{children}</h1>,
                                                h2: ({ children }) => <h2 style={{ fontSize: 18, fontWeight: 600, margin: '18px 0 8px' }}>{children}</h2>,
                                                h3: ({ children }) => <h3 style={{ fontSize: 15, fontWeight: 600, margin: '14px 0 6px' }}>{children}</h3>,
                                                p: ({ children }) => <p style={{ margin: '8px 0' }}>{children}</p>,
                                                ul: ({ children }) => <ul style={{ margin: '8px 0', paddingLeft: 20 }}>{children}</ul>,
                                                ol: ({ children }) => <ol style={{ margin: '8px 0', paddingLeft: 20 }}>{children}</ol>,
                                                li: ({ children }) => <li style={{ margin: '4px 0' }}>{children}</li>,
                                                table: ({ children }) => <div style={{ overflowX: 'auto', margin: '12px 0' }}><table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>{children}</table></div>,
                                                th: ({ children }) => <th style={{ border: '1px solid var(--border)', padding: '8px 12px', background: 'var(--bg-secondary)', fontWeight: 600, textAlign: 'left' }}>{children}</th>,
                                                td: ({ children }) => <td style={{ border: '1px solid var(--border)', padding: '8px 12px' }}>{children}</td>,
                                                blockquote: ({ children }) => <blockquote style={{ borderLeft: '3px solid var(--accent)', margin: '12px 0', padding: '4px 16px', color: 'var(--text-secondary)', background: 'var(--bg-secondary)', borderRadius: '0 6px 6px 0' }}>{children}</blockquote>,
                                                code: ({ className, children }) => {
                                                    const isBlock = className?.includes('language-');
                                                    return isBlock
                                                        ? <pre style={{ background: 'var(--bg-secondary)', padding: 14, borderRadius: 8, overflow: 'auto', fontSize: 12, lineHeight: 1.6 }}><code>{children}</code></pre>
                                                        : <code style={{ background: 'var(--bg-secondary)', padding: '2px 6px', borderRadius: 4, fontSize: '0.9em' }}>{children}</code>;
                                                },
                                                a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent)', textDecoration: 'none' }}>{children}</a>,
                                                strong: ({ children }) => <strong style={{ fontWeight: 600 }}>{children}</strong>,
                                                hr: () => <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '16px 0' }} />,
                                            }}
                                        >
                                            {currentStep.raw_output}
                                        </Markdown>
                                    </div>
                                ) : (
                                    <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-tertiary)' }}>{t('workflow.detail.noContent', 'No content')}</div>
                                )}
                            </div>
                        </>
                    ) : steps.length === 0 && (data.status === 'planning' || data.status === 'running') ? (
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12, color: 'var(--text-tertiary)' }}>
                            <div style={{ fontSize: 48 }}>\u26a1</div>
                            <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>{t('workflow.detail.assembling', 'Agent team assembling')}</div>
                            <div style={{ fontSize: 13, maxWidth: 320, textAlign: 'center', lineHeight: 1.6 }}>
                                {t('workflow.detail.assemblingDesc', 'AI is analyzing "{{instruction}}", breaking it down into subtasks and assigning them to the best agents.', { instruction: data.user_instruction })}
                            </div>
                            <div style={{ marginTop: 8, padding: '6px 16px', borderRadius: 20, background: 'var(--accent)', color: '#fff', fontSize: 12, fontWeight: 500, opacity: 0.8 }}>
                                {t('workflow.detail.estimatedTime', 'Usually takes 10-30 seconds')}
                            </div>
                        </div>
                    ) : (
                        <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>{t('workflow.detail.selectStep', 'Select a step on the left to view deliverables')}</div>
                    )}
                </div>

                {/* Right: Chat Panel */}
                {chatOpen && (
                    <div style={{
                        width: 360, borderLeft: '1px solid var(--border)', display: 'flex', flexDirection: 'column',
                        flexShrink: 0, background: 'var(--bg-primary)',
                    }}>
                        {/* Chat header */}
                        <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center' }}>
                            <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{t('workflow.chat.title', 'Workflow Assistant')}</span>
                            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{t('workflow.chat.context', 'Based on current workflow context')}</span>
                        </div>

                        {/* Chat messages */}
                        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
                            {chatMessages.length === 0 && (
                                <div style={{ color: 'var(--text-tertiary)', fontSize: 12, textAlign: 'center', padding: '20px 0' }}>
                                    <div style={{ marginBottom: 8 }}>{t('workflow.chat.empty', 'Ask me anything about this workflow')}</div>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                                        {chatSuggestions.map((q) => (
                                            <button
                                                key={q}
                                                onClick={() => { setChatInput(q); }}
                                                style={{
                                                    padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
                                                    background: 'var(--bg-secondary)', color: 'var(--text-secondary)',
                                                    cursor: 'pointer', fontSize: 11, textAlign: 'left',
                                                }}
                                            >
                                                {q}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            )}
                            {chatMessages.map((msg, i) => (
                                <div key={i} style={{
                                    alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
                                    maxWidth: '85%',
                                }}>
                                    <div style={{
                                        padding: '8px 12px', borderRadius: 12, fontSize: 13, lineHeight: 1.6,
                                        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                        ...(msg.role === 'user'
                                            ? { background: 'var(--accent)', color: '#fff', borderBottomRightRadius: 4 }
                                            : { background: 'var(--bg-secondary)', color: 'var(--text-primary)', borderBottomLeftRadius: 4 }),
                                    }}>
                                        {msg.content}
                                    </div>
                                </div>
                            ))}
                            {chatLoading && (
                                <div style={{ alignSelf: 'flex-start', padding: '8px 12px', borderRadius: 12, background: 'var(--bg-secondary)', fontSize: 13, color: 'var(--text-tertiary)' }}>
                                    {t('workflow.chat.thinking', 'Thinking...')}
                                </div>
                            )}
                            <div ref={chatEndRef} />
                        </div>

                        {/* Chat input */}
                        <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8 }}>
                            <input
                                value={chatInput}
                                onChange={(e) => setChatInput(e.target.value)}
                                onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleChat()}
                                placeholder={t('workflow.chat.placeholder', 'Type a message...')}
                                disabled={chatLoading}
                                style={{
                                    flex: 1, padding: '8px 12px', borderRadius: 8,
                                    border: '1px solid var(--border)', background: 'var(--bg-secondary)',
                                    color: 'var(--text-primary)', fontSize: 13, outline: 'none',
                                }}
                            />
                            <button
                                onClick={handleChat}
                                disabled={chatLoading || !chatInput.trim()}
                                style={{
                                    padding: '8px 14px', borderRadius: 8, border: 'none',
                                    background: chatLoading || !chatInput.trim() ? 'var(--bg-secondary)' : 'var(--accent)',
                                    color: chatLoading || !chatInput.trim() ? 'var(--text-tertiary)' : '#fff',
                                    cursor: chatLoading ? 'wait' : 'pointer', fontSize: 13, fontWeight: 600,
                                }}
                            >
                                {t('workflow.chat.send', 'Send')}
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

const btnStyle: React.CSSProperties = {
    padding: '4px 10px', borderRadius: 6, border: '1px solid var(--border)',
    background: 'var(--bg-secondary)', color: 'var(--text-primary)', cursor: 'pointer',
    fontSize: 11,
};
