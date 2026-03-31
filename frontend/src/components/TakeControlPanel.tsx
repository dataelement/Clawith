/**
 * TakeControlPanel — Human-agent collaborative browser control panel.
 *
 * Renders a fullscreen overlay that:
 * 1. Polls screenshots from the AgentBay session every 500ms
 * 2. Forwards mouse clicks (with coordinate mapping) to the session
 * 3. Forwards keyboard input (text + special keys) to the session
 * 4. On "Complete Login", exports cookies and releases the lock
 *
 * The panel automatically acquires a Take Control lock on mount
 * and releases it on unmount/close.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { controlApi } from '../services/api';

/* ── Props ── */
interface Props {
    agentId: string;
    sessionId: string;
    onClose: () => void;
}

/* ── Quick-key pre-defined buttons ── */
const QUICK_KEYS: { label: string; keys: string[] }[] = [
    { label: 'Tab', keys: ['Tab'] },
    { label: 'Enter', keys: ['Enter'] },
    { label: 'Esc', keys: ['Escape'] },
    { label: 'Ctrl+A', keys: ['Control', 'a'] },
    { label: 'Ctrl+C', keys: ['Control', 'c'] },
    { label: 'Ctrl+V', keys: ['Control', 'v'] },
    { label: 'Backspace', keys: ['Backspace'] },
];

/* ── Icons ── */
const CloseIcon = (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
        <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
);

const SendIcon = (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M2 8h12M10 4l4 4-4 4" />
    </svg>
);

export default function TakeControlPanel({ agentId, sessionId, onClose }: Props) {
    const [screenshot, setScreenshot] = useState<string | null>(null);
    const [textInput, setTextInput] = useState('');
    const [locked, setLocked] = useState(false);
    const [statusText, setStatusText] = useState('Acquiring control...');
    const [platformHint, setPlatformHint] = useState('');
    const imgRef = useRef<HTMLImageElement>(null);
    const pollingRef = useRef<number | null>(null);
    const mountedRef = useRef(true);

    // Acquire lock on mount
    useEffect(() => {
        mountedRef.current = true;
        (async () => {
            try {
                const res = await controlApi.lock(agentId, { session_id: sessionId });
                if (mountedRef.current) {
                    setLocked(true);
                    setStatusText('You are in control. Click on the screenshot to interact.');
                }
            } catch (e: any) {
                if (mountedRef.current) {
                    setStatusText(`Failed to acquire lock: ${e.message}`);
                }
            }
        })();

        return () => {
            mountedRef.current = false;
            // Release lock on unmount
            controlApi.unlock(agentId, {
                session_id: sessionId,
                export_cookies: false,
            }).catch(() => {}); 
        };
    }, [agentId, sessionId]);

    // Poll screenshots
    useEffect(() => {
        if (!locked) return;

        const poll = async () => {
            try {
                const res = await controlApi.screenshot(agentId, { session_id: sessionId });
                if (mountedRef.current && res.screenshot) {
                    setScreenshot(`data:image/png;base64,${res.screenshot}`);
                }
            } catch {
                // Polling failure is non-fatal, will retry
            }
        };

        // Initial screenshot
        poll();

        // Poll every 600ms
        pollingRef.current = window.setInterval(poll, 600);

        return () => {
            if (pollingRef.current) {
                clearInterval(pollingRef.current);
            }
        };
    }, [locked, agentId, sessionId]);

    // Handle click on screenshot — map coordinates to actual resolution
    const handleScreenshotClick = useCallback(async (e: React.MouseEvent<HTMLImageElement>) => {
        if (!imgRef.current || !locked) return;

        const rect = imgRef.current.getBoundingClientRect();
        const naturalWidth = imgRef.current.naturalWidth;
        const naturalHeight = imgRef.current.naturalHeight;

        // Map display coordinates to actual coordinates
        const scaleX = naturalWidth / rect.width;
        const scaleY = naturalHeight / rect.height;
        const x = Math.round((e.clientX - rect.left) * scaleX);
        const y = Math.round((e.clientY - rect.top) * scaleY);

        setStatusText(`Clicking at (${x}, ${y})...`);
        try {
            await controlApi.click(agentId, { session_id: sessionId, x, y });
            setStatusText(`Clicked at (${x}, ${y})`);
        } catch (err: any) {
            setStatusText(`Click failed: ${err.message}`);
        }
    }, [locked, agentId, sessionId]);

    // Handle text input
    const handleSendText = useCallback(async () => {
        if (!textInput.trim() || !locked) return;
        setStatusText(`Typing: "${textInput.slice(0, 30)}..."`);
        try {
            await controlApi.type(agentId, { session_id: sessionId, text: textInput });
            setStatusText('Text sent');
            setTextInput('');
        } catch (err: any) {
            setStatusText(`Type failed: ${err.message}`);
        }
    }, [textInput, locked, agentId, sessionId]);

    // Handle quick key press
    const handleQuickKey = useCallback(async (keys: string[]) => {
        if (!locked) return;
        setStatusText(`Pressing: ${keys.join('+')}`);
        try {
            await controlApi.pressKeys(agentId, { session_id: sessionId, keys });
            setStatusText(`Pressed: ${keys.join('+')}`);
        } catch (err: any) {
            setStatusText(`Key press failed: ${err.message}`);
        }
    }, [locked, agentId, sessionId]);

    // Complete login — export cookies and close
    const handleComplete = useCallback(async () => {
        setStatusText('Exporting cookies...');
        try {
            const res = await controlApi.unlock(agentId, {
                session_id: sessionId,
                export_cookies: true,
                platform_hint: platformHint || undefined,
            });
            setStatusText(
                res.cookies_exported
                    ? `Login complete! ${res.cookie_count} cookies saved.`
                    : 'Session unlocked (no cookies exported).'
            );
            // Small delay so the user sees the success message
            setTimeout(onClose, 1200);
        } catch (err: any) {
            setStatusText(`Unlock failed: ${err.message}`);
        }
    }, [agentId, sessionId, platformHint, onClose]);

    // Handle cancel
    const handleCancel = useCallback(async () => {
        try {
            await controlApi.unlock(agentId, {
                session_id: sessionId,
                export_cookies: false,
            });
        } catch {}
        onClose();
    }, [agentId, sessionId, onClose]);

    return (
        <div className="tc-overlay">
            <div className="tc-panel">
                {/* Header */}
                <div className="tc-header">
                    <div className="tc-header-left">
                        <span className="tc-live-dot" />
                        <span className="tc-title">Take Control</span>
                        <span className="tc-status">{statusText}</span>
                    </div>
                    <button className="tc-close-btn" onClick={handleCancel} title="Cancel">
                        {CloseIcon}
                    </button>
                </div>

                {/* Screenshot area */}
                <div className="tc-screenshot-area">
                    {screenshot ? (
                        <img
                            ref={imgRef}
                            src={screenshot}
                            alt="Browser session"
                            className="tc-screenshot"
                            onClick={handleScreenshotClick}
                            style={{ cursor: locked ? 'crosshair' : 'default' }}
                        />
                    ) : (
                        <div className="tc-screenshot-placeholder">
                            <span>Waiting for screenshot...</span>
                        </div>
                    )}
                </div>

                {/* Controls */}
                <div className="tc-controls">
                    {/* Text input */}
                    <div className="tc-text-row">
                        <input
                            className="tc-text-input"
                            type="text"
                            value={textInput}
                            onChange={(e) => setTextInput(e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Enter') handleSendText(); }}
                            placeholder="Type text to send..."
                            disabled={!locked}
                        />
                        <button
                            className="tc-send-btn"
                            onClick={handleSendText}
                            disabled={!locked || !textInput.trim()}
                        >
                            {SendIcon}
                        </button>
                    </div>

                    {/* Quick keys */}
                    <div className="tc-quick-keys">
                        {QUICK_KEYS.map((qk) => (
                            <button
                                key={qk.label}
                                className="tc-quick-key"
                                onClick={() => handleQuickKey(qk.keys)}
                                disabled={!locked}
                            >
                                {qk.label}
                            </button>
                        ))}
                    </div>

                    {/* Platform hint + action buttons */}
                    <div className="tc-action-row">
                        <input
                            className="tc-platform-input"
                            type="text"
                            value={platformHint}
                            onChange={(e) => setPlatformHint(e.target.value)}
                            placeholder="Domain to save cookies for (e.g. baidu.com)"
                        />
                        <div className="tc-action-buttons">
                            <button className="tc-btn-cancel" onClick={handleCancel}>
                                Cancel
                            </button>
                            <button
                                className="tc-btn-complete"
                                onClick={handleComplete}
                                disabled={!locked}
                            >
                                Complete Login
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <style>{takeControlStyles}</style>
        </div>
    );
}

/* ── Styles ── */
const takeControlStyles = `
.tc-overlay {
    position: fixed;
    inset: 0;
    z-index: 2000;
    background: rgba(0, 0, 0, 0.85);
    backdrop-filter: blur(8px);
    display: flex;
    align-items: center;
    justify-content: center;
}

.tc-panel {
    width: 90vw;
    max-width: 1100px;
    max-height: 92vh;
    background: var(--card-bg, #141422);
    border: 1px solid var(--border-primary, rgba(255,255,255,0.1));
    border-radius: 14px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 24px 80px rgba(0,0,0,0.6);
}

.tc-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 18px;
    border-bottom: 1px solid var(--border-primary, rgba(255,255,255,0.06));
    flex-shrink: 0;
}

.tc-header-left {
    display: flex;
    align-items: center;
    gap: 10px;
}

.tc-live-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #34c759;
    animation: tc-pulse 2s ease-in-out infinite;
}
@keyframes tc-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}

.tc-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary, #e0e0e0);
}

.tc-status {
    font-size: 12px;
    color: var(--text-tertiary, #888);
    max-width: 400px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.tc-close-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: var(--text-tertiary, #888);
    cursor: pointer;
}
.tc-close-btn:hover {
    background: rgba(255,255,255,0.06);
    color: var(--text-primary, #e0e0e0);
}

.tc-screenshot-area {
    flex: 1;
    min-height: 300px;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    background: #0a0a18;
}

.tc-screenshot {
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
    user-select: none;
    -webkit-user-drag: none;
}

.tc-screenshot-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    height: 100%;
    font-size: 13px;
    color: var(--text-tertiary, #666);
}

.tc-controls {
    padding: 12px 18px;
    border-top: 1px solid var(--border-primary, rgba(255,255,255,0.06));
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
}

.tc-text-row {
    display: flex;
    gap: 8px;
}

.tc-text-input {
    flex: 1;
    padding: 8px 12px;
    font-size: 13px;
    color: var(--text-primary, #e0e0e0);
    background: var(--bg-secondary, rgba(255,255,255,0.04));
    border: 1px solid var(--border-primary, rgba(255,255,255,0.1));
    border-radius: 6px;
    outline: none;
    font-family: inherit;
}
.tc-text-input:focus {
    border-color: var(--accent, #6366f1);
}

.tc-send-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    background: var(--accent, #6366f1);
    border: none;
    border-radius: 6px;
    color: #fff;
    cursor: pointer;
    flex-shrink: 0;
}
.tc-send-btn:hover { opacity: 0.9; }
.tc-send-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.tc-quick-keys {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
}

.tc-quick-key {
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 500;
    font-family: 'SF Mono', 'Fira Code', monospace;
    color: var(--text-secondary, #b0b0b0);
    background: var(--bg-tertiary, rgba(255,255,255,0.05));
    border: 1px solid var(--border-primary, rgba(255,255,255,0.08));
    border-radius: 4px;
    cursor: pointer;
    transition: all 0.12s;
}
.tc-quick-key:hover {
    background: rgba(255,255,255,0.1);
    border-color: rgba(255,255,255,0.15);
    color: var(--text-primary, #e0e0e0);
}
.tc-quick-key:disabled { opacity: 0.4; cursor: not-allowed; }

.tc-action-row {
    display: flex;
    align-items: center;
    gap: 10px;
}

.tc-platform-input {
    flex: 1;
    padding: 7px 10px;
    font-size: 12px;
    color: var(--text-primary, #e0e0e0);
    background: var(--bg-secondary, rgba(255,255,255,0.04));
    border: 1px solid var(--border-primary, rgba(255,255,255,0.08));
    border-radius: 6px;
    outline: none;
}
.tc-platform-input:focus {
    border-color: var(--accent, #6366f1);
}

.tc-action-buttons {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
}

.tc-btn-cancel {
    padding: 7px 16px;
    font-size: 13px;
    color: var(--text-secondary, #b0b0b0);
    background: transparent;
    border: 1px solid var(--border-primary, rgba(255,255,255,0.1));
    border-radius: 6px;
    cursor: pointer;
}
.tc-btn-cancel:hover {
    background: rgba(255,255,255,0.06);
}

.tc-btn-complete {
    padding: 7px 20px;
    font-size: 13px;
    font-weight: 600;
    color: #fff;
    background: #34c759;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    transition: opacity 0.15s;
}
.tc-btn-complete:hover { opacity: 0.9; }
.tc-btn-complete:disabled { opacity: 0.4; cursor: not-allowed; }
`;
