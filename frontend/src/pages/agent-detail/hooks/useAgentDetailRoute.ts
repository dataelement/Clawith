import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { type AgentDetailTab, isAgentDetailSettingsTab } from '../agentDetailTabs';

function resolveInitialTab(isSettingsRoute: boolean, hashTab: string | null): AgentDetailTab {
    if (!isSettingsRoute) return 'chat';
    return isAgentDetailSettingsTab(hashTab) ? hashTab : 'status';
}

export function useAgentDetailRoute({ agentId }: { agentId?: string }) {
    const navigate = useNavigate();
    const location = useLocation();
    const isSettingsRoute = location.pathname.endsWith('/settings');
    const isChatRoute = !isSettingsRoute;
    const hashTab = location.hash ? location.hash.replace('#', '') : null;
    const [activeTab, setActiveTabRaw] = useState<AgentDetailTab>(() => resolveInitialTab(isSettingsRoute, hashTab));

    useEffect(() => {
        const nextTab = resolveInitialTab(isSettingsRoute, hashTab);
        setActiveTabRaw((currentTab) => currentTab === nextTab ? currentTab : nextTab);
    }, [hashTab, isSettingsRoute]);

    const setActiveTab = (tab: AgentDetailTab) => {
        if (tab === 'chat') {
            setActiveTabRaw('chat');
            if (agentId) navigate(`/agents/${agentId}/chat`);
            return;
        }

        const nextTab = isAgentDetailSettingsTab(tab) ? tab : 'status';
        setActiveTabRaw(nextTab);
        if (agentId && !isSettingsRoute) {
            navigate(`/agents/${agentId}/settings#${nextTab}`);
            return;
        }
        window.history.replaceState(null, '', `#${nextTab}`);
    };

    return {
        activeTab,
        isChatRoute,
        isSettingsRoute,
        setActiveTab,
    };
}
