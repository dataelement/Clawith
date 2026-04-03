import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import TakeControlPanel from './TakeControlPanel';

/* ── Types ── */
export interface LivePreviewState {
    desktop?: { screenshotUrl: string };
    browser?: { screenshotUrl: string };
    code?: { output: string };
}

interface Props {
    liveState: LivePreviewState;
    visible: boolean;
    onToggle: () => void;
    agentId?: string;     // needed for Take Control
    sessionId?: string;   // needed for Take Control
    /** Called by TC panel on close to push the latest screenshot into liveState */
    onLiveUpdate?: (env: 'browser' | 'desktop', screenshotDataUri: string) => void;
}

/* ── Tab Icons (Linear-style minimal SVGs) ── */
const TabIcons = {
    desktop: (
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2" y="2" width="12" height="9" rx="1.5" />
            <path d="M5.5 14h5M8 11v3" />
        </svg>
    ),
    browser: (
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2" y="2" width="12" height="12" rx="1.5" />
            <path d="M2 5.5h12" />
            <circle cx="4" cy="3.8" r="0.5" fill="currentColor" stroke="none" />
            <circle cx="5.5" cy="3.8" r="0.5" fill="currentColor" stroke="none" />
            <circle cx="7" cy="3.8" r="0.5" fill="currentColor" stroke="none" />
        </svg>
    ),
    code: (
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5.5 4.5L2.5 8l3 3.5M10.5 4.5l3 3.5-3 3.5" />
        </svg>
    ),
};

const CollapseIcon = (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M10 4l4 4-4 4" />
    </svg>
);

const ExpandIcon = (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M6 4l-4 4 4 4" />
    </svg>
);

type TabType = 'desktop' | 'browser' | 'code';

/* ── Constants for resize constraints ── */
const MIN_WIDTH = 300;  // minimum panel width in px
const MAX_WIDTH_VW = 0.65; // maximum panel width as fraction of viewport width

/**
 * Calculate initial panel width as 50% of the chat container.
 * The chat container sits inside `.main-content` (after the sidebar),
 * so we use the viewport width minus sidebar instead of a fixed value.
 */
function calcHalfContainerWidth(): number {
    // Try to measure the actual chat container
    const container = document.querySelector('.chat-container') as HTMLElement | null;
    if (container) {
        return Math.max(MIN_WIDTH, Math.floor(container.clientWidth / 2));
    }
    // Fallback: guess sidebar is ~60px, split the remaining viewport in half
    return Math.max(MIN_WIDTH, Math.floor((window.innerWidth - 60) / 2));
}

export default function AgentBayLivePanel({ liveState, visible, onToggle, agentId, sessionId, onLiveUpdate }: Props) {
    const { t } = useTranslation();

    // Take Control state
    const [showTakeControl, setShowTakeControl] = useState(false);

    // Determine available tabs from live state
    const availableTabs: TabType[] = [];
    if (liveState.desktop) availableTabs.push('desktop');
    if (liveState.browser) availableTabs.push('browser');
    if (liveState.code) availableTabs.push('code');

    const [activeTab, setActiveTab] = useState<TabType>('desktop');
    const codeEndRef = useRef<HTMLDivElement>(null);

    const [panelWidth, setPanelWidth] = useState(() => calcHalfContainerWidth());
    const panelRef = useRef<HTMLDivElement>(null);

    // Recalculate on window resize to keep approximate 50% split
    useEffect(() => {
        const onResize = () => {
            // Only auto-resize if user hasn't manually dragged
            if (!isDragging.current && !userResized.current) {
                setPanelWidth(calcHalfContainerWidth());
            }
        };
        window.addEventListener('resize', onResize);
        return () => window.removeEventListener('resize', onResize);
    }, []);
    const isDragging = useRef(false);
    const userResized = useRef(false);  // Once user manually drags, stop auto-resizing
    const dragStartX = useRef(0);
    const dragStartWidth = useRef(0);

    // Track latest data to auto-switch tabs when new activity arrives
    const prevDesktopUrl = useRef(liveState.desktop?.screenshotUrl);
    const prevBrowserUrl = useRef(liveState.browser?.screenshotUrl);
    const prevCodeLength = useRef(liveState.code?.output?.length || 0);

    useEffect(() => {
        // Switch to the tab that just received a new update
        if (liveState.desktop?.screenshotUrl !== prevDesktopUrl.current) {
            setActiveTab('desktop');
            prevDesktopUrl.current = liveState.desktop?.screenshotUrl;
        }
        if (liveState.browser?.screenshotUrl !== prevBrowserUrl.current) {
            setActiveTab('browser');
            prevBrowserUrl.current = liveState.browser?.screenshotUrl;
        }
        const currentCodeLength = liveState.code?.output?.length || 0;
        if (currentCodeLength !== prevCodeLength.current) {
            setActiveTab('code');
            prevCodeLength.current = currentCodeLength;
        }
        
        // Fallback: If current tab is completely gone, switch to first available
        if (availableTabs.length > 0 && !availableTabs.includes(activeTab)) {
            setActiveTab(availableTabs[0]);
        }
    }, [
        liveState.desktop?.screenshotUrl, 
        liveState.browser?.screenshotUrl, 
        liveState.code?.output,
        availableTabs,
        activeTab
    ]);

    // Auto-scroll code output
    useEffect(() => {
        if (activeTab === 'code') {
            codeEndRef.current?.scrollIntoView({ behavior: 'smooth' });
        }
    }, [liveState.code?.output]);

    /* ── Drag logic for the left resize handle ── */
    const handleDragMouseDown = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        isDragging.current = true;
        dragStartX.current = e.clientX;
        dragStartWidth.current = panelWidth;

        // Set cursor state on body to prevent flicker while dragging
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    }, [panelWidth]);

    useEffect(() => {
        const onMouseMove = (e: MouseEvent) => {
            if (!isDragging.current) return;
            // Moving left (smaller clientX) increases panel width
            const delta = dragStartX.current - e.clientX;
            const maxWidth = window.innerWidth * MAX_WIDTH_VW;
            const newWidth = Math.min(maxWidth, Math.max(MIN_WIDTH, dragStartWidth.current + delta));
            setPanelWidth(newWidth);
        };

        const onMouseUp = () => {
            if (!isDragging.current) return;
            isDragging.current = false;
            userResized.current = true;  // User manually chose a width; stop auto-resizing
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        };

        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
        return () => {
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
        };
    }, []);

    // Collapsed toggle button (shown when panel is hidden)
    if (!visible) {
        if (availableTabs.length === 0) return null;
        return (
            <button
                className="live-panel-toggle"
                onClick={onToggle}
                title="Open live preview"
            >
                {ExpandIcon}
                <span className="live-panel-toggle-dot" />
            </button>
        );
    }

    const tabLabels: Record<TabType, string> = {
        desktop: 'Desktop',
        browser: 'Browser',
        code: 'Code',
    };

    return (
        <div className="live-panel" style={{ width: `${panelWidth}px`, flexShrink: 0 }}>
            {/* Drag handle on the left edge */}
            <div
                className="live-panel-resize-handle"
                onMouseDown={handleDragMouseDown}
                title="Drag to resize"
            />

            {/* Header with tabs and collapse button */}
            <div className="live-panel-header">
                <div className="live-panel-tabs">
                    {availableTabs.map((tab) => (
                        <button
                            key={tab}
                            className={`live-panel-tab ${activeTab === tab ? 'active' : ''}`}
                            onClick={() => setActiveTab(tab)}
                        >
                            {TabIcons[tab]}
                            <span>{tabLabels[tab]}</span>
                        </button>
                    ))}
                </div>
                {/* Take Control button — shown when browser/desktop has data */}
                {agentId && sessionId && (activeTab === 'browser' || activeTab === 'desktop') && (
                    <button
                        className="live-panel-take-control"
                        onClick={() => setShowTakeControl(true)}
                        title="Take Control — manually interact with the browser"
                    >
                        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M3 3l5.5 10 1.5-4 4-1.5z" />
                        </svg>
                        <span>Control</span>
                    </button>
                )}
                <button className="live-panel-collapse" onClick={onToggle} title="Collapse">
                    {CollapseIcon}
                </button>
            </div>

            {/* Content area */}
            <div className="live-panel-content">
                {activeTab === 'desktop' && liveState.desktop && (
                    <div className="live-panel-browser">
                        <img
                            src={liveState.desktop.screenshotUrl}
                            alt="Desktop preview"
                            className="live-panel-screenshot"
                        />
                        <div className="live-panel-badge">
                            <span className="live-dot" />
                            Live
                        </div>
                    </div>
                )}

                {activeTab === 'browser' && liveState.browser && (
                    <div className="live-panel-browser">
                        <img
                            src={liveState.browser.screenshotUrl}
                            alt="Browser preview"
                            className="live-panel-screenshot"
                        />
                        <div className="live-panel-badge">
                            <span className="live-dot" />
                            Live
                        </div>
                    </div>
                )}

                {activeTab === 'code' && liveState.code && (
                    <div className="live-panel-code">
                        <pre>{liveState.code.output}</pre>
                        <div ref={codeEndRef} />
                    </div>
                )}

                {/* Fallback: no content yet for the active tab */}
                {((activeTab === 'desktop' && !liveState.desktop) ||
                  (activeTab === 'browser' && !liveState.browser) ||
                  (activeTab === 'code' && !liveState.code)) && (
                    <div className="live-panel-empty">
                        <span style={{ opacity: 0.5 }}>
                            {TabIcons[activeTab]}
                        </span>
                        <span>Waiting for {tabLabels[activeTab].toLowerCase()} activity...</span>
                    </div>
                )}
            </div>

            {/* Take Control fullscreen panel */}
            {showTakeControl && agentId && sessionId && (
                <TakeControlPanel
                    agentId={agentId}
                    sessionId={sessionId}
                    onClose={() => setShowTakeControl(false)}
                    onLastScreenshot={(dataUri) => {
                        // Push the final TC screenshot to the live preview
                        console.log('[LivePanel] Received last screenshot from TC, size:', dataUri.length, 'onLiveUpdate:', !!onLiveUpdate);
                        if (onLiveUpdate) {
                            onLiveUpdate('browser', dataUri);
                        }
                    }}
                />
            )}
        </div>
    );
}
