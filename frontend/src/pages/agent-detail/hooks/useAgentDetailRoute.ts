import { useLocation, useNavigate } from "react-router-dom";

import {
  type AgentDetailTab,
  isAgentDetailSettingsTab,
} from "../agentDetailTabs";

function resolveInitialTab(
  isSettingsRoute: boolean,
  isDirectoryRoute: boolean,
  hashTab: string | null,
): AgentDetailTab {
  if (isDirectoryRoute) return "relationships";
  if (!isSettingsRoute) return "chat";
  return isAgentDetailSettingsTab(hashTab) ? hashTab : "status";
}

export function useAgentDetailRoute({ agentId }: { agentId?: string }) {
  const navigate = useNavigate();
  const location = useLocation();
  const isDirectoryRoute = location.pathname.endsWith("/directory");
  const isSettingsPath = location.pathname.endsWith("/settings");
  const isSettingsRoute = isSettingsPath || isDirectoryRoute;
  const isChatRoute = !isSettingsRoute;
  const hashTab = location.hash ? location.hash.replace("#", "") : null;
  const activeTab = resolveInitialTab(
    isSettingsRoute,
    isDirectoryRoute,
    hashTab,
  );

  const setActiveTab = (tab: AgentDetailTab) => {
    if (tab === "chat") {
      if (agentId) navigate(`/agents/${agentId}/chat`);
      return;
    }

    const nextTab = isAgentDetailSettingsTab(tab) ? tab : "status";

    if (agentId && nextTab === "relationships") {
      navigate(`/agents/${agentId}/directory`);
      return;
    }

    if (agentId && (!isSettingsRoute || isDirectoryRoute)) {
      navigate(`/agents/${agentId}/settings#${nextTab}`);
      return;
    }
    navigate({ hash: `#${nextTab}` }, { replace: true });
  };

  return {
    activeTab,
    isChatRoute,
    isSettingsRoute,
    setActiveTab,
  };
}
