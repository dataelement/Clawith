import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';
import MarkdownRenderer from '../components/MarkdownRenderer';
import AgentBayLivePanel, { LivePreviewState } from '../components/AgentBayLivePanel';
import { agentApi, enterpriseApi, uploadFileWithProgress } from '../services/api';
import { IconPaperclip, IconSend } from '@tabler/icons-react';
import { formatFileSize } from '../utils/formatFileSize';
import { useAuthStore } from '../stores';

/* ── Inline SVG Icons ── */
const Icons = {
    bot: (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="5" width="12" height="10" rx="2" />
            <circle cx="7" cy="10" r="1" fill="currentColor" stroke="none" />
            <circle cx="11" cy="10" r="1" fill="currentColor" stroke="none" />
            <path d="M9 2v3M6 2h6" />
        </svg>
    ),
    user: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="8" cy="5.5" r="2.5" />
            <path d="M3 14v-1a4 4 0 018 0v1" />
        </svg>
    ),
    chat: (
        <svg width="28" height="28" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2 3a1 1 0 011-1h10a1 1 0 011 1v7a1 1 0 01-1 1H5l-3 3V3z" />
            <path d="M5 5.5h6M5 8h4" />
        </svg>
    ),
    tool: (
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10.5 10.5L14 14M4.5 2a2.5 2.5 0 00-1.8 4.2l5.1 5.1A2.5 2.5 0 1012 7.2L6.8 2.2A2.5 2.5 0 004.5 2z" />
        </svg>
    ),
};

interface ToolCall {
    name: string;
    args: any;
    result?: string;
}

interface Message {
    role: 'user' | 'assistant';
    content: string;
    fileName?: string;
    toolCalls?: ToolCall[];
    thinking?: string;
    imageUrl?: string;
    timestamp?: string;
    _isToolGroup?: boolean;
}

// CSS keyframe for the pulse/breathing LED — injected once into <head>
const PULSE_STYLE_ID = 'cw-tool-pulse-style';
if (typeof document !== 'undefined' && !document.getElementById(PULSE_STYLE_ID)) {
    const s = document.createElement('style');
    s.id = PULSE_STYLE_ID;
    s.textContent = `
        @keyframes cw-pulse-led {
            0%, 100% { opacity: 1; transform: scale(1); box-shadow: 0 0 0 0 rgba(99,102,241,0.6); }
            50%       { opacity: 0.55; transform: scale(1.5); box-shadow: 0 0 0 4px rgba(99,102,241,0); }
        }
        .cw-running-led { animation: cw-pulse-led 1.4s ease-in-out infinite; }
    `;
    document.head.appendChild(s);
}

function ChatToolChain({ toolCalls }: { toolCalls: ToolCall[] }) {
    const { t } = useTranslation();
    const [expanded, setExpanded] = useState(false);
    const count = toolCalls.length;

    // Find the last tool without a result — that is the currently-executing one.
    const activeIdx = (() => {
        for (let i = toolCalls.length - 1; i >= 0; i--) {
            if (!toolCalls[i].result) return i;
        }
        return -1; // -1 = all done
    })();
    const isRunning = activeIdx >= 0;
    const activeTool = isRunning ? toolCalls[activeIdx] : null;

    return (
        <div style={{
            borderRadius: '8px',
            background: 'rgba(99,102,241,0.06)',
            border: `1px solid ${isRunning ? 'rgba(99,102,241,0.32)' : 'rgba(99,102,241,0.18)'}`,
            fontSize: '12px',
            overflow: 'hidden',
            marginBottom: '6px',
            transition: 'border-color 0.3s ease',
        }}>
            {/* ── Header / toggle row ── */}
            <button
                onClick={() => setExpanded(v => !v)}
                style={{
                    background: 'none', border: 'none', cursor: 'pointer',
                    width: '100%', display: 'flex', alignItems: 'center', gap: '6px',
                    padding: '7px 10px',
                    color: 'var(--accent-text, #818cf8)',
                }}
            >
                {/* Left label: title + running-tool indicator */}
                <span style={{ flex: 1, textAlign: 'left', display: 'flex', alignItems: 'center', gap: '6px', minWidth: 0 }}>
                    <span style={{ fontWeight: 500, flexShrink: 0 }}>{t('agent.chat.toolCallChain')}</span>
                    <span style={{ color: 'rgba(99,102,241,0.4)', flexShrink: 0 }}>·</span>
                    {isRunning && activeTool ? (
                        <>
                            {/* Pulse LED: breathing dot while a tool runs */}
                            <span
                                className="cw-running-led"
                                style={{
                                    display: 'inline-block',
                                    width: '6px', height: '6px',
                                    borderRadius: '50%',
                                    background: '#818cf8',
                                    flexShrink: 0,
                                }}
                            />
                            {/* Currently-running tool name */}
                            <span style={{
                                fontFamily: 'var(--font-mono)',
                                fontSize: '11px',
                                color: '#a5b4fc',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                            }}>
                                {activeTool.name}
                            </span>
                        </>
                    ) : (
                        /* Static green dot when all tools are done */
                        <span style={{
                            display: 'inline-block',
                            width: '6px', height: '6px',
                            borderRadius: '50%',
                            background: '#22c55e',
                            flexShrink: 0,
                            opacity: 0.85,
                        }} />
                    )}
                </span>

                {/* Count badge */}
                <span style={{
                    background: 'rgba(99,102,241,0.18)', color: '#818cf8',
                    borderRadius: '10px', padding: '1px 7px',
                    fontSize: '10px', fontWeight: 600, flexShrink: 0,
                }}>
                    {count}
                </span>

                {/* Expand chevron */}
                <span style={{
                    fontSize: '10px', color: 'var(--text-tertiary)',
                    transition: 'transform 0.2s', display: 'inline-block',
                    transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
                    flexShrink: 0,
                }}>▶</span>
            </button>

            {/* ── Collapsed: pills with individual run-state dots ── */}
            {!expanded && count > 0 && (
                <div style={{ padding: '0 10px 7px 10px', display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                    {toolCalls.map((tc, i) => {
                        const running = !tc.result;
                        return (
                            <span key={i} style={{
                                background: running ? 'rgba(99,102,241,0.14)' : 'rgba(99,102,241,0.08)',
                                border: `1px solid ${running ? 'rgba(99,102,241,0.28)' : 'rgba(99,102,241,0.14)'}`,
                                borderRadius: '4px', padding: '1px 6px',
                                fontSize: '10px', color: running ? '#818cf8' : '#a5b4fc',
                                fontFamily: 'var(--font-mono)',
                                display: 'inline-flex', alignItems: 'center', gap: '4px',
                            }}>
                                {running && (
                                    <span
                                        className="cw-running-led"
                                        style={{
                                            display: 'inline-block',
                                            width: '4px', height: '4px',
                                            borderRadius: '50%',
                                            background: '#818cf8',
                                            flexShrink: 0,
                                        }}
                                    />
                                )}
                                {tc.name}
                            </span>
                        );
                    })}
                </div>
            )}

            {/* ── Expanded: each tool's full detail row ── */}
            {expanded && (
                <div style={{ borderTop: '1px solid rgba(99,102,241,0.15)' }}>
                    {toolCalls.map((tc, i) => {
                        const running = !tc.result;
                        return (
                            <div key={i} style={{
                                padding: '7px 10px',
                                borderBottom: i < toolCalls.length - 1 ? '1px solid rgba(99,102,241,0.10)' : 'none',
                            }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '5px', marginBottom: '4px' }}>
                                    {/* Status dot: amber + pulse = running; green = done */}
                                    <span
                                        className={running ? 'cw-running-led' : undefined}
                                        style={{
                                            display: 'inline-block',
                                            width: '5px', height: '5px',
                                            borderRadius: '50%',
                                            background: running ? '#f59e0b' : '#22c55e',
                                            flexShrink: 0,
                                        }}
                                    />
                                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: '#818cf8', fontWeight: 600 }}>
                                        {tc.name}
                                    </span>
                                    {running && (
                                        <span style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
                                            {t('common.loading')}
                                        </span>
                                    )}
                                </div>
                                {tc.args && Object.keys(tc.args).length > 0 && (
                                    <div style={{
                                        fontFamily: 'var(--font-mono)', fontSize: '10px',
                                        color: 'var(--text-tertiary)', whiteSpace: 'pre-wrap',
                                        wordBreak: 'break-all', maxHeight: '80px', overflowY: 'auto',
                                        background: 'rgba(0,0,0,0.12)', borderRadius: '4px',
                                        padding: '4px 6px', marginBottom: tc.result ? '4px' : 0,
                                    }}>
                                        {JSON.stringify(tc.args, null, 2)}
                                    </div>
                                )}
                                {tc.result && (
                                    <div style={{
                                        fontSize: '10px', color: 'var(--text-secondary)',
                                        whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                                        maxHeight: '80px', overflowY: 'auto',
                                        borderTop: '1px solid rgba(99,102,241,0.10)', paddingTop: '4px',
                                    }}>
                                        {tc.result.length > 500 ? tc.result.slice(0, 500) + '…' : tc.result}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

export default function Chat() {
    const { t } = useTranslation();
    const { id } = useParams<{ id: string }>();
    const token = useAuthStore((s) => s.token);
    const [messages, setMessages] = useState<Message[]>([]);
    const [input, setInput] = useState('');
    const [connected, setConnected] = useState(false);
    const [uploadProgress, setUploadProgress] = useState<{
        name: string;
        percent: number;
        previewUrl?: string;
        sizeBytes: number;
    } | null>(null);
    const [streaming, setStreaming] = useState(false);
    const [isWaiting, setIsWaiting] = useState(false);
    const [attachedFile, setAttachedFile] = useState<{ name: string; text: string; path?: string; imageUrl?: string } | null>(null);
    const [liveState, setLiveState] = useState<LivePreviewState>({});
    const [livePanelVisible, setLivePanelVisible] = useState(false);
    const [wsSessionId, setWsSessionId] = useState<string>('');
    const wsRef = useRef<WebSocket | null>(null);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    // Ref to the chat textarea for direct DOM height manipulation
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const pendingToolCalls = useRef<ToolCall[]>([]);
    const streamContent = useRef('');
    const thinkingContent = useRef('');

    const { data: agent } = useQuery({
        queryKey: ['agent', id],
        queryFn: () => agentApi.get(id!),
        enabled: !!id,
    });

    const { data: llmModels = [] } = useQuery({
        queryKey: ['llm-models'],
        queryFn: () => enterpriseApi.llmModels(),
        enabled: !!agent?.primary_model_id,
    });

    const supportsVision = !!agent?.primary_model_id && llmModels.some(
        (m: any) => m.id === agent.primary_model_id && m.supports_vision
    );

    const parseMessage = (msg: Message): Message => {
        if (msg.role !== 'user') return msg;
        // Standard web chat format: [file:name.pdf]\ncontent
        const newFmt = msg.content.match(/^\[file:([^\]]+)\]\n?/);
        if (newFmt) return { ...msg, fileName: newFmt[1], content: msg.content.slice(newFmt[0].length).trim() };
        // Feishu/Slack channel format: [\u6587\u4ef6\u5df2\u4e0a\u4f20: workspace/uploads/name]
        const chanFmt = msg.content.match(/^\[\u6587\u4ef6\u5df2\u4e0a\u4f20: (?:workspace\/uploads\/)?([^\]\n]+)\]/);
        if (chanFmt) {
            const raw = chanFmt[1]; const fileName = raw.split('/').pop() || raw;
            return { ...msg, fileName, content: msg.content.slice(chanFmt[0].length).trim() };
        }
        // Old format: [File: name.pdf]\nFile location:...\nQuestion: user_msg
        const oldFmt = msg.content.match(/^\[File: ([^\]]+)\]/);
        if (oldFmt) {
            const fileName = oldFmt[1];
            const qMatch = msg.content.match(/\nQuestion: ([\s\S]+)$/);
            return { ...msg, fileName, content: qMatch ? qMatch[1].trim() : '' };
        }
        return msg;
    };

    // Load chat history on mount
    useEffect(() => {
        if (!id || !token) return;
        fetch(`/api/chat/${id}/history`, {
            headers: { Authorization: `Bearer ${token}` },
        })
            .then(r => r.json())
            .then((history: any[]) => {
                if (history.length > 0) {
                    // Group consecutive tool_call entries into _isToolGroup messages
                    const processed: Message[] = [];
                    for (const h of history) {
                        if (h.role === 'tool_call') {
                            const tc: ToolCall = {
                                name: h.toolName || h.tool_name || '',
                                args: h.toolArgs || h.tool_args || {},
                                result: h.toolResult || h.tool_result || '',
                            };
                            const last = processed[processed.length - 1];
                            if (last && last._isToolGroup) {
                                // Merge into existing tool group
                                last.toolCalls = [...(last.toolCalls || []), tc];
                            } else if (last && last.role === 'assistant' && !(last.content && last.content.trim())) {
                                // Previous is empty assistant — convert to tool group
                                last._isToolGroup = true;
                                last.toolCalls = [...(last.toolCalls || []), tc];
                            } else {
                                // Start new tool group
                                processed.push({
                                    role: 'assistant', content: '', toolCalls: [tc],
                                    timestamp: h.created_at || undefined,
                                    _isToolGroup: true,
                                });
                            }
                        } else {
                            const msg = parseMessage({ role: h.role, content: h.content, fileName: h.fileName, toolCalls: h.toolCalls, thinking: h.thinking, imageUrl: h.imageUrl });
                            msg.timestamp = h.created_at || undefined;
                            processed.push(msg);
                        }
                    }
                    setMessages(processed);
                }
            })
            .catch(() => { /* ignore */ });
    }, [id, token]);

    useEffect(() => {
        if (!id || !token) return;

        let cancelled = false;

        const connect = () => {
            if (cancelled) return;
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws/chat/${id}?token=${token}`;
            const ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                if (cancelled) {
                    ws.close();
                    return;
                }
                setConnected(true);
                wsRef.current = ws;
            };
            ws.onclose = () => {
                if (!cancelled) {
                    setConnected(false);
                    setTimeout(() => connect(), 2000);
                }
            };
            ws.onerror = () => {
                if (!cancelled) setConnected(false);
            };
            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (['thinking', 'chunk', 'tool_call', 'done', 'error', 'quota_exceeded'].includes(data.type)) {
                    setIsWaiting(false);
                }
                if (['error', 'quota_exceeded'].includes(data.type)) {
                    setStreaming(false);
                }

                // Capture session_id from the 'connected' message for Take Control
                if (data.type === 'connected' && data.session_id) {
                    setWsSessionId(data.session_id);
                    return;
                }

                // ── AgentBay live preview events ──
                if (data.type === 'agentbay_live') {
                    console.log('[LivePreview] Received:', data.env, 'url:', data.screenshot_url?.substring(0, 60));
                    setLiveState(prev => {
                        const next = { ...prev };
                        if ((data.env === 'desktop' || data.env === 'browser') && data.screenshot_url) {
                            // Use URL-based approach: append cache-busting timestamp
                            const imgUrl = data.screenshot_url + '&_t=' + Date.now();
                            if (data.env === 'desktop') next.desktop = { screenshotUrl: imgUrl };
                            else next.browser = { screenshotUrl: imgUrl };
                        } else if (data.env === 'code' && data.output) {
                            // Append code output
                            const existing = prev.code?.output || '';
                            next.code = { output: existing + (existing ? '\n---\n' : '') + data.output };
                        }
                        return next;
                    });
                    // Auto-expand the live panel on first data
                    setLivePanelVisible(true);
                    return;
                }

                if (data.type === 'thinking') {
                    // Accumulate thinking content
                    thinkingContent.current += data.content;
                    setMessages(prev => {
                        const last = prev[prev.length - 1];
                        if (last && last.role === 'assistant') {
                            const updated = [...prev];
                            updated[updated.length - 1] = { ...last, thinking: thinkingContent.current };
                            return updated;
                        }
                        return [...prev, { role: 'assistant', content: '', thinking: thinkingContent.current, timestamp: new Date().toISOString() }];
                    });
                } else if (data.type === 'chunk') {
                    // Streaming text chunk — accumulate and update live preview
                    streamContent.current += data.content;
                    setMessages(prev => {
                        const last = prev[prev.length - 1];
                        if (last && last.role === 'assistant') {
                            // Update the streaming message in-place
                            const updated = [...prev];
                            updated[updated.length - 1] = { ...last, content: streamContent.current };
                            return updated;
                        }
                        return [...prev, { role: 'assistant', content: streamContent.current, timestamp: new Date().toISOString() }];
                    });
                } else if (data.type === 'tool_call') {
                    console.log('[ToolCall]', data.name, data.status);
                    if (data.status === 'running') {
                        // Tool execution started — show in-progress in tool group
                        const tc: ToolCall = { name: data.name, args: data.args || {} };
                        pendingToolCalls.current.push(tc);
                        const now = new Date().toISOString();
                        setMessages(prev => {
                            let msgs = [...prev];
                            // Remove trailing empty assistant messages (stream placeholders)
                            while (msgs.length > 0) {
                                const last = msgs[msgs.length - 1];
                                if (last.role === 'assistant' && !last._isToolGroup && !(last.content && last.content.trim())) {
                                    msgs.pop();
                                } else break;
                            }
                            // Merge into existing tool group, but stop at user messages (new turn)
                            for (let i = msgs.length - 1; i >= Math.max(0, msgs.length - 5); i--) {
                                if (msgs[i].role === 'user') break;
                                if (msgs[i]._isToolGroup) {
                                    msgs[i] = { ...msgs[i], toolCalls: [...(msgs[i].toolCalls || []), tc], timestamp: now };
                                    return msgs;
                                }
                            }
                            return [...msgs, { role: 'assistant', content: '', toolCalls: [tc], timestamp: now, _isToolGroup: true }];
                        });
                    } else if (data.status === 'done') {
                        // Tool execution finished — update result
                        streamContent.current = '';
                        thinkingContent.current = '';
                        const newCall: ToolCall = { name: data.name, args: data.args, result: data.result || '' };
                        // Update pending: replace running entry or add new
                        const idx = pendingToolCalls.current.findIndex(tc => tc.name === data.name && !tc.result);
                        if (idx >= 0) {
                            pendingToolCalls.current[idx] = newCall;
                        } else {
                            pendingToolCalls.current.push(newCall);
                        }
                        const now = new Date().toISOString();
                        setMessages(prev => {
                            let msgs = [...prev];
                            // Remove trailing empty assistant messages
                            while (msgs.length > 0) {
                                const last = msgs[msgs.length - 1];
                                if (last.role === 'assistant' && !last._isToolGroup && !(last.content && last.content.trim())) {
                                    msgs.pop();
                                } else break;
                            }
                            // Find recent tool group, but stop at user messages (new turn)
                            for (let i = msgs.length - 1; i >= Math.max(0, msgs.length - 5); i--) {
                                if (msgs[i].role === 'user') break;
                                if (msgs[i]._isToolGroup) {
                                    // Update the matching tool call with result, or add new
                                    const existing = (msgs[i].toolCalls || []).map(tc =>
                                        tc.name === data.name && !tc.result ? newCall : tc
                                    );
                                    const hasIt = existing.some(tc => tc.name === data.name && tc.result);
                                    msgs[i] = { ...msgs[i], toolCalls: hasIt ? existing : [...existing, newCall], timestamp: now };
                                    return msgs;
                                }
                            }
                            return [...msgs, { role: 'assistant', content: '', toolCalls: [newCall], timestamp: now, _isToolGroup: true }];
                        });

                        // ── AgentBay live preview (embedded in tool_call) ──
                        if (data.live_preview) {
                            const lp = data.live_preview;
                            setLiveState(prev => {
                                const next = { ...prev };
                                if ((lp.env === 'desktop' || lp.env === 'browser') && lp.screenshot_url) {
                                    const imgUrl = lp.screenshot_url + '&_t=' + Date.now();
                                    if (lp.env === 'desktop') next.desktop = { screenshotUrl: imgUrl };
                                    else next.browser = { screenshotUrl: imgUrl };
                                } else if (lp.env === 'code' && lp.output) {
                                    const existing = prev.code?.output || '';
                                    next.code = { output: existing + (existing ? '\n---\n' : '') + lp.output };
                                }
                                return next;
                            });
                            setLivePanelVisible(true);
                        }
                    }
                } else if (data.type === 'done') {
                    // Final response — replace streaming message with final + tool calls
                    const toolCalls = pendingToolCalls.current.length > 0 ? [...pendingToolCalls.current] : undefined;
                    const thinking = thinkingContent.current || undefined;
                    pendingToolCalls.current = [];
                    streamContent.current = '';
                    thinkingContent.current = '';
                    setStreaming(false);
                    setMessages(prev => {
                        const updated = [...prev];
                        // Replace the last streaming assistant message
                        if (updated.length > 0 && updated[updated.length - 1].role === 'assistant') {
                            updated[updated.length - 1] = { role: 'assistant', content: data.content, toolCalls, thinking };
                        } else {
                            updated.push({ role: 'assistant', content: data.content, toolCalls, thinking });
                        }
                        return updated;
                    });
                } else {
                    // Legacy format: {role, content}
                    setMessages(prev => [...prev, { role: data.role, content: data.content }]);
                }
            };
        };

        connect();

        return () => {
            cancelled = true;
            if (wsRef.current) {
                wsRef.current.close();
                wsRef.current = null;
            }
        };
    }, [id, token]);

    // Auto-focus input when connection is established
    useEffect(() => {
        if (connected) {
            setTimeout(() => textareaRef.current?.focus(), 50);
        }
    }, [connected]);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined;
        setUploadProgress({ name: file.name, percent: 0, previewUrl, sizeBytes: file.size });

        try {
            const { promise } = uploadFileWithProgress(
                '/chat/upload',
                file,
                (pct) => {
                    setUploadProgress((prev) =>
                        prev ? { ...prev, percent: pct >= 101 ? 100 : pct } : null,
                    );
                },
                id ? { agent_id: id } : undefined,
            );
            const data = await promise;
            setAttachedFile({
                name: data.filename,
                text: data.extracted_text,
                path: data.workspace_path,
                imageUrl: data.image_data_url || undefined,
            });
        } catch (err: any) {
            if (err?.message !== 'Upload cancelled') {
                alert(t('agent.upload.failed') + (err?.message ? `: ${err.message}` : ''));
            }
        } finally {
            if (previewUrl) URL.revokeObjectURL(previewUrl);
            setUploadProgress(null);
            if (fileInputRef.current) fileInputRef.current.value = '';
        }
    };

    const sendMessage = () => {
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
        if (!input.trim() && !attachedFile) return;

        // Reset streaming state for new response
        pendingToolCalls.current = [];
        streamContent.current = '';
        thinkingContent.current = '';
        setIsWaiting(true);
        setStreaming(true);

        let userMsg = input.trim();
        let contentForLLM = userMsg;

        if (attachedFile) {
            if (attachedFile.imageUrl && supportsVision) {
                // Vision model — embed image data marker for direct analysis
                const imageMarker = `[image_data:${attachedFile.imageUrl}]`;
                contentForLLM = userMsg
                    ? `${imageMarker}\n${userMsg}`
                    : `${imageMarker}\n请分析这张图片`;
                userMsg = userMsg || `[图片] ${attachedFile.name}`;
            } else if (attachedFile.imageUrl) {
                // Non-vision model — just reference the file path
                const wsPath = attachedFile.path || '';
                contentForLLM = userMsg
                    ? `[图片文件已上传: ${attachedFile.name}，保存在 ${wsPath}]\n\n${userMsg}`
                    : `[图片文件已上传: ${attachedFile.name}，保存在 ${wsPath}]\n请描述或处理这个图片文件。你可以使用 read_document 工具读取它。`;
                userMsg = userMsg || `[图片] ${attachedFile.name}`;
            } else {
                const wsPath = attachedFile.path || '';
                const codePath = wsPath.replace(/^workspace\//, '');
                const fileLoc = wsPath ? `\nFile location: ${wsPath} (for read_file/read_document tools)\nIn execute_code, use relative path: "${codePath}" (working directory is workspace/)` : '';
                const fileContext = `[文件: ${attachedFile.name}]${fileLoc}\n\n${attachedFile.text}`;
                contentForLLM = userMsg
                    ? `${fileContext}\n\n用户问题: ${userMsg}`
                    : `请阅读并分析以下文件内容:\n\n${fileContext}`;
                userMsg = userMsg || `[${t('agent.chat.attachment')}] ${attachedFile.name}`;
            }
        }

        setMessages((prev) => [...prev, {
            role: 'user',
            content: userMsg,
            fileName: attachedFile?.name,
            imageUrl: attachedFile?.imageUrl,
            timestamp: new Date().toISOString(),
        }]);
        wsRef.current.send(JSON.stringify({ content: contentForLLM, display_content: userMsg, file_name: attachedFile?.name || '' }));
        setInput('');
        setAttachedFile(null);
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        // Enter sends the message; Shift+Enter inserts a newline
        if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && !isWaiting && !streaming) {
            e.preventDefault();
            sendMessage();
        }
    };

    const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        setInput(e.target.value);
    };

    const hasLiveData = !!(liveState.desktop || liveState.browser || liveState.code);

    return (
        <div>
            <div className="page-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <div style={{ width: '36px', height: '36px', borderRadius: 'var(--radius-md)', background: 'var(--bg-tertiary)', border: '1px solid var(--border-subtle)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)' }}>
                        {Icons.bot}
                    </div>
                    <div>
                        <h1 className="page-title" style={{ fontSize: '18px' }}>{agent?.name || '...'}</h1>
                        <div style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span className={`status-dot ${connected ? 'running' : 'stopped'}`} />
                            <span style={{ color: 'var(--text-tertiary)' }}>{connected ? t('agent.chat.connected') : t('agent.chat.disconnected')}</span>
                        </div>
                    </div>
                </div>
            </div>

            <div className={`chat-container ${hasLiveData ? 'chat-with-live-panel' : ''}`}>
                {/* Wrap chat area in a column so it coexists with the live panel in flex-row */}
                <div className="chat-main">
                <div className="chat-messages">
                    {messages.length === 0 && (
                        <div style={{ textAlign: 'center', padding: '60px', color: 'var(--text-tertiary)' }}>
                            <div style={{ marginBottom: '12px', display: 'flex', justifyContent: 'center' }}>{Icons.chat}</div>
                            <div>{t('agent.chat.startConversation', { name: agent?.name || t('nav.newAgent') })}</div>
                            <div style={{ fontSize: '12px', marginTop: '8px', opacity: 0.7 }}>{t('agent.chat.fileSupport')}</div>
                        </div>
                    )}
                    {messages.filter(m => {
                        // Skip empty assistant messages (stream placeholders)
                        if (m.role === 'assistant' && !m._isToolGroup && !(m.content && m.content.trim()) && !m.toolCalls?.length && !m.thinking) return false;
                        return true;
                    }).map((msg, i) => (
                        msg._isToolGroup ? (
                            /* Tool call group — compact display without avatar bubble */
                            <div key={i} style={{ marginLeft: '48px', marginBottom: '8px' }}>
                                {msg.toolCalls && msg.toolCalls.length > 0 && (
                                    <ChatToolChain toolCalls={msg.toolCalls} />
                                )}
                            </div>
                        ) :
                        <div key={i} className={`chat-message ${msg.role}`}>
                            <div className="chat-avatar" style={{ color: 'var(--text-tertiary)' }}>
                                {msg.role === 'user' ? Icons.user : Icons.bot}
                            </div>
                            <div className="chat-bubble">
                                {msg.fileName && (() => {
                                    const fe = msg.fileName!.split('.').pop()?.toLowerCase() ?? '';
                                    const isImage = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'].includes(fe);
                                    if (isImage && msg.imageUrl) {
                                        return (<div style={{ marginBottom: '4px' }}>
                                            <img src={msg.imageUrl} alt={msg.fileName} style={{ maxWidth: '240px', maxHeight: '180px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }} />
                                        </div>);
                                    }
                                    const fi = fe === 'pdf' ? '\uD83D\uDCC4' : (fe === 'csv' || fe === 'xlsx' || fe === 'xls') ? '\uD83D\uDCCA' : (fe === 'docx' || fe === 'doc') ? '\uD83D\uDCDD' : '\uD83D\uDCCE';
                                    return (<div style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', background: 'rgba(0,0,0,0.08)', borderRadius: '6px', padding: '4px 8px', marginBottom: msg.content ? '4px' : '0', fontSize: '11px', border: '1px solid var(--border-subtle)', color: 'var(--text-secondary)' }}><span>{fi}</span><span style={{ fontWeight: 500, color: 'var(--text-primary)', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{msg.fileName}</span></div>);
                                })()}
                                {msg.thinking && (
                                    <details style={{
                                        marginBottom: '8px', fontSize: '12px',
                                        background: 'rgba(147, 130, 220, 0.08)', borderRadius: '6px',
                                        border: '1px solid rgba(147, 130, 220, 0.15)',
                                    }}>
                                        <summary style={{
                                            padding: '6px 10px', cursor: 'pointer',
                                            color: 'rgba(147, 130, 220, 0.9)', fontWeight: 500,
                                            userSelect: 'none', display: 'flex', alignItems: 'center', gap: '4px',
                                        }}>
                                            Thinking
                                        </summary>
                                        <div style={{
                                            padding: '4px 10px 8px',
                                            fontSize: '12px', lineHeight: '1.6',
                                            color: 'var(--text-secondary)',
                                            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                            maxHeight: '300px', overflow: 'auto',
                                        }}>
                                            {msg.thinking}
                                        </div>
                                    </details>
                                )}
                                {msg.toolCalls && msg.toolCalls.length > 0 && (
                                    <ChatToolChain toolCalls={msg.toolCalls} />
                                )}
                                {msg.role === 'assistant' ? (
                                    streaming && !msg.content && i === messages.length - 1 ? (
                                        <div className="thinking-indicator">
                                            <div className="thinking-dots">
                                                <span /><span /><span />
                                            </div>
                                            <span style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>{t('agent.chat.thinking', 'Thinking...')}</span>
                                        </div>
                                    ) : (
                                        <MarkdownRenderer content={msg.content} />
                                    )
                                ) : (
                                    <div style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div>
                                )}
                                {msg.timestamp && (
                                    <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '4px', opacity: 0.7 }}>
                                        {new Date(msg.timestamp).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                                    </div>
                                )}
                            </div>
                        </div>
                    ))}
                    {(isWaiting || (streaming && (messages.length === 0 || messages[messages.length - 1].role === 'user'))) && (
                        <div className="chat-message assistant">
                            <div className="chat-avatar" style={{ color: 'var(--text-tertiary)' }}>
                                {Icons.bot}
                            </div>
                            <div className="chat-bubble">
                                <div className="thinking-indicator">
                                    <div className="thinking-dots">
                                        <span /><span /><span />
                                    </div>
                                    <span style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>{t('agent.chat.thinking', 'Thinking...')}</span>
                                </div>
                            </div>
                        </div>
                    )}
                    <div ref={messagesEndRef} />
                </div>

                <div className="chat-input-area">
                    <div className="chat-composer">
                        {(uploadProgress || (attachedFile && !uploadProgress)) && (
                            <div className="chat-composer-attachments">
                                {uploadProgress && (
                                    <div className="chat-file-pill">
                                        <div
                                            className="chat-file-pill__fill"
                                            style={{ width: `${uploadProgress.percent}%` }}
                                        />
                                        <div className="chat-file-pill__row">
                                            {uploadProgress.previewUrl ? (
                                                <img className="chat-file-pill__thumb" src={uploadProgress.previewUrl} alt="" />
                                            ) : (
                                                <span className="chat-file-pill__icon">
                                                    <IconPaperclip size={14} stroke={1.75} />
                                                </span>
                                            )}
                                            <span className="chat-file-pill__name">{uploadProgress.name}</span>
                                            <span className="chat-file-pill__size">{formatFileSize(uploadProgress.sizeBytes)}</span>
                                            <span className="chat-file-pill__pct">{uploadProgress.percent}%</span>
                                        </div>
                                    </div>
                                )}
                                {attachedFile && !uploadProgress && (
                                    <div className="chat-file-pill">
                                        <div className="chat-file-pill__row">
                                            {attachedFile.imageUrl ? (
                                                <img className="chat-file-pill__thumb" src={attachedFile.imageUrl} alt="" />
                                            ) : (
                                                <span className="chat-file-pill__icon">
                                                    <IconPaperclip size={14} stroke={1.75} />
                                                </span>
                                            )}
                                            <span className="chat-file-pill__name">{attachedFile.name}</span>
                                            <button
                                                type="button"
                                                className="chat-file-pill__remove"
                                                onClick={() => setAttachedFile(null)}
                                                title={t('common.close', 'Close')}
                                            >
                                                ×
                                            </button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                        <div className="chat-composer-input-block">
                            <textarea
                                ref={textareaRef}
                                className="chat-input"
                                value={input}
                                onChange={handleInputChange}
                                onKeyDown={handleKeyDown}
                                placeholder={t('chat.placeholder')}
                                disabled={!connected}
                                rows={1}
                            />
                        </div>
                        <div className="chat-composer-toolbar">
                            <input type="file" ref={fileInputRef} onChange={handleFileSelect} style={{ display: 'none' }} />
                            <button
                                type="button"
                                className="chat-composer-btn"
                                onClick={() => fileInputRef.current?.click()}
                                disabled={!connected || !!uploadProgress || isWaiting || streaming}
                                title={t('agent.workspace.uploadFile')}
                            >
                                <IconPaperclip size={16} stroke={1.75} />
                            </button>
                            {(streaming || isWaiting) ? (
                                <button
                                    type="button"
                                    className="btn btn-stop-generation"
                                    onClick={() => {
                                        if (wsRef.current?.readyState === WebSocket.OPEN) {
                                            wsRef.current.send(JSON.stringify({ type: 'abort' }));
                                            setStreaming(false);
                                            setIsWaiting(false);
                                        }
                                    }}
                                    title={t('chat.stop', 'Stop')}
                                >
                                    <span className="stop-icon" />
                                </button>
                            ) : (
                                <button
                                    type="button"
                                    className="btn btn-primary chat-composer-send"
                                    onClick={sendMessage}
                                    disabled={!connected || (!input.trim() && !attachedFile)}
                                    title={t('chat.send')}
                                >
                                    <IconSend size={16} stroke={1.75} />
                                </button>
                            )}
                        </div>
                    </div>
                </div>
                </div>

                {/* AgentBay Live Preview Panel */}
                {hasLiveData && (
                    <AgentBayLivePanel
                        liveState={liveState}
                        visible={livePanelVisible}
                        onToggle={() => setLivePanelVisible(v => !v)}
                        agentId={id}
                        sessionId={wsSessionId}
                        onLiveUpdate={(env, screenshotDataUri) => {
                            // Update live preview with the latest screenshot from Take Control
                            setLiveState(prev => ({
                                ...prev,
                                [env]: { screenshotUrl: screenshotDataUri },
                            }));
                        }}
                    />
                )}
            </div>
        </div>
    );
}
