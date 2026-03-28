import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

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

export default function AgentBayLivePanel({ liveState, visible, onToggle }: Props) {
    const { t } = useTranslation();
    // Determine available tabs from live state
    const availableTabs: TabType[] = [];
    if (liveState.desktop) availableTabs.push('desktop');
    if (liveState.browser) availableTabs.push('browser');
    if (liveState.code) availableTabs.push('code');

    const [activeTab, setActiveTab] = useState<TabType>('desktop');
    const codeEndRef = useRef<HTMLDivElement>(null);

    // Auto-switch to the most recently active tab
    useEffect(() => {
        if (availableTabs.length > 0 && !availableTabs.includes(activeTab)) {
            setActiveTab(availableTabs[0]);
        }
    }, [availableTabs.length]);

    // Auto-scroll code output
    useEffect(() => {
        if (activeTab === 'code') {
            codeEndRef.current?.scrollIntoView({ behavior: 'smooth' });
        }
    }, [liveState.code?.output]);

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
        <div className="live-panel">
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
        </div>
    );
}
