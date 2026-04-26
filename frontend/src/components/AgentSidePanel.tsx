import { useCallback, useEffect, useRef, useState } from 'react';
import TakeControlPanel from './TakeControlPanel';
import WorkspaceOperationPanel, { WorkspaceActivity, WorkspaceLiveDraft } from './WorkspaceOperationPanel';
import type { LivePreviewState } from './AgentBayLivePanel';

interface Props {
    liveState: LivePreviewState;
    workspaceActivePath?: string | null;
    workspaceActivities: WorkspaceActivity[];
    workspaceLiveDraft?: WorkspaceLiveDraft | null;
    workspaceLocked?: boolean;
    visible: boolean;
    onToggle: () => void;
    activeTab: SidePanelTab;
    onTabChange: (tab: SidePanelTab) => void;
    onWorkspaceSelectPath: (path: string) => void;
    onWorkspaceToggleLock?: () => void;
    onWorkspaceEditingChange?: (editing: boolean) => void;
    onWorkspacePathDeleted?: (path: string) => void;
    agentId?: string;
    sessionId?: string;
    onLiveUpdate?: (env: 'browser' | 'desktop', screenshotDataUri: string) => void;
}

export type SidePanelTab = 'workspace' | 'browser' | 'desktop' | 'code';

const MIN_WIDTH = 340;
const MAX_WIDTH_VW = 0.68;

function calcInitialWidth(): number {
    const container = document.querySelector('.chat-container') as HTMLElement | null;
    if (container) return Math.max(MIN_WIDTH, Math.floor(container.clientWidth / 2));
    return Math.max(MIN_WIDTH, Math.floor((window.innerWidth - 60) / 2));
}

const labels: Record<SidePanelTab, string> = {
    workspace: 'Workspace',
    browser: 'Browser',
    desktop: 'Desktop',
    code: 'Code',
};

export default function AgentSidePanel({
    liveState,
    workspaceActivePath,
    workspaceActivities,
    workspaceLiveDraft,
    workspaceLocked = false,
    visible,
    onToggle,
    activeTab,
    onTabChange,
    onWorkspaceSelectPath,
    onWorkspaceToggleLock,
    onWorkspaceEditingChange,
    onWorkspacePathDeleted,
    agentId,
    sessionId,
    onLiveUpdate,
}: Props) {
    const [panelWidth, setPanelWidth] = useState(() => calcInitialWidth());
    const [showTakeControl, setShowTakeControl] = useState(false);
    const isDragging = useRef(false);
    const userResized = useRef(false);
    const dragStartX = useRef(0);
    const dragStartWidth = useRef(0);
    const codeEndRef = useRef<HTMLDivElement>(null);
    const onLiveUpdateRef = useRef(onLiveUpdate);

    useEffect(() => {
        onLiveUpdateRef.current = onLiveUpdate;
    });

    const availableTabs: SidePanelTab[] = ['workspace'];
    if (liveState.browser) availableTabs.push('browser');
    if (liveState.desktop) availableTabs.push('desktop');
    if (liveState.code) availableTabs.push('code');

    useEffect(() => {
        const onResize = () => {
            if (!isDragging.current && !userResized.current) setPanelWidth(calcInitialWidth());
        };
        window.addEventListener('resize', onResize);
        return () => window.removeEventListener('resize', onResize);
    }, []);

    useEffect(() => {
        if (activeTab === 'code') codeEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [activeTab, liveState.code?.output]);

    useEffect(() => {
        if (availableTabs.length > 0 && !availableTabs.includes(activeTab)) onTabChange(availableTabs[0]);
    }, [availableTabs.join('|'), activeTab]);

    const handleDragMouseDown = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        isDragging.current = true;
        dragStartX.current = e.clientX;
        dragStartWidth.current = panelWidth;
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    }, [panelWidth]);

    useEffect(() => {
        const onMouseMove = (e: MouseEvent) => {
            if (!isDragging.current) return;
            const delta = dragStartX.current - e.clientX;
            const maxWidth = window.innerWidth * MAX_WIDTH_VW;
            setPanelWidth(Math.min(maxWidth, Math.max(MIN_WIDTH, dragStartWidth.current + delta)));
        };
        const onMouseUp = () => {
            if (!isDragging.current) return;
            isDragging.current = false;
            userResized.current = true;
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

    if (!visible) {
        return (
            <button className="live-panel-toggle" onClick={onToggle} title="Open workspace">
                <span>‹</span>
            </button>
        );
    }

    return (
        <div className="live-panel agent-side-panel" style={{ width: `${panelWidth}px`, flexShrink: 0 }}>
            <div className="live-panel-resize-handle" onMouseDown={handleDragMouseDown} title="Drag to resize" />
            <div className="live-panel-header">
                <div className="live-panel-tabs">
                    {availableTabs.map((tab) => (
                        <button
                            key={tab}
                            className={`live-panel-tab ${activeTab === tab ? 'active' : ''}`}
                            onClick={() => onTabChange(tab)}
                        >
                            <span>{labels[tab]}</span>
                        </button>
                    ))}
                </div>
                {agentId && sessionId && (activeTab === 'browser' || activeTab === 'desktop') && (
                    <button className="live-panel-take-control" onClick={() => setShowTakeControl(true)} title="Take control">
                        <span>Control</span>
                    </button>
                )}
                <button className="live-panel-collapse" onClick={onToggle} title="Collapse">›</button>
            </div>
            <div className="live-panel-content">
                {activeTab === 'workspace' && agentId && (
                    <WorkspaceOperationPanel
                        agentId={agentId}
                        sessionId={sessionId}
                        activePath={workspaceActivePath}
                        activities={workspaceActivities}
                        liveDraft={workspaceLiveDraft}
                        locked={workspaceLocked}
                        onSelectPath={onWorkspaceSelectPath}
                        onToggleLock={onWorkspaceToggleLock}
                        onEditingChange={onWorkspaceEditingChange}
                        onPathDeleted={onWorkspacePathDeleted}
                    />
                )}
                {activeTab === 'desktop' && liveState.desktop && (
                    <div className="live-panel-browser">
                        <img src={liveState.desktop.screenshotUrl} alt="Desktop preview" className="live-panel-screenshot" />
                        <div className="live-panel-badge"><span className="live-dot" />Live</div>
                    </div>
                )}
                {activeTab === 'browser' && liveState.browser && (
                    <div className="live-panel-browser">
                        <img src={liveState.browser.screenshotUrl} alt="Browser preview" className="live-panel-screenshot" />
                        <div className="live-panel-badge"><span className="live-dot" />Live</div>
                    </div>
                )}
                {activeTab === 'code' && liveState.code && (
                    <div className="live-panel-code">
                        <pre>{liveState.code.output}</pre>
                        <div ref={codeEndRef} />
                    </div>
                )}
            </div>
            {showTakeControl && agentId && sessionId && (activeTab === 'browser' || activeTab === 'desktop') && (
                <TakeControlPanel
                    agentId={agentId}
                    sessionId={sessionId}
                    envType={activeTab === 'desktop' ? 'computer' : 'browser'}
                    onClose={() => setShowTakeControl(false)}
                    onLastScreenshot={(dataUri) => {
                        const env = activeTab === 'desktop' ? 'desktop' : 'browser';
                        onLiveUpdateRef.current?.(env, dataUri);
                    }}
                />
            )}
        </div>
    );
}
