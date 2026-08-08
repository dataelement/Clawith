export type McpAuthorizationState =
  | 'unknown'
  | 'auth_required'
  | 'connected'
  | 'unavailable';

export type McpAuthorizationStatus = {
  state: Exclude<McpAuthorizationState, 'unknown'>;
  authorizationUrl?: string;
};

type SmitheryManagedTool = {
  id?: string;
  type?: string;
  mcp_authorization_provider?: string | null;
};

type FetchLike = (
  url: string,
  init: {
    method: 'GET';
    cache: 'no-store';
    headers: { Authorization: string };
  },
) => Promise<{
  ok: boolean;
  json: () => Promise<unknown>;
}>;

type AuthorizationWindow = Pick<Window, 'closed' | 'close'> & {
  location: Pick<Location, 'replace'>;
  opener: WindowProxy | null;
};

type OpenWindowLike = (
  url?: string | URL,
  target?: string,
) => AuthorizationWindow | null;

type AssignLocationLike = (url: string | URL) => void;

export function isSmitheryManagedMcpTool(tool: SmitheryManagedTool): boolean {
  return tool.type === 'mcp' && tool.mcp_authorization_provider === 'smithery';
}

export function getSmitheryAuthorizationTool<T extends SmitheryManagedTool>(
  tools: T[],
): T | null {
  return tools.find(isSmitheryManagedMcpTool) ?? null;
}

export function getSmitheryAuthorizationButtonLabel(
  state: McpAuthorizationState,
): 'Authorize' | 'Re-authorize' | 'Check' {
  if (state === 'unknown') return 'Authorize';
  if (state === 'auth_required') return 'Re-authorize';
  return 'Check';
}

export function shouldPreopenMcpAuthorizationWindow(
  state: McpAuthorizationState,
): boolean {
  return state === 'unknown' || state === 'auth_required';
}

export function openMcpAuthorizationWindow(
  openWindow: OpenWindowLike = window.open.bind(window),
): AuthorizationWindow | null {
  const authorizationWindow = openWindow('about:blank', '_blank');
  if (authorizationWindow) authorizationWindow.opener = null;
  return authorizationWindow;
}

export function closeMcpAuthorizationWindow(
  authorizationWindow: AuthorizationWindow | null,
): void {
  if (!authorizationWindow || authorizationWindow.closed) return;
  try {
    authorizationWindow.close();
  } catch {
    // A user-closed or browser-owned window needs no further cleanup.
  }
}

export function navigateMcpAuthorizationWindow(
  authorizationWindow: AuthorizationWindow | null,
  authorizationUrl: string,
  assignLocation: AssignLocationLike = window.location.assign.bind(window.location),
): 'popup' | 'same_page' {
  if (authorizationWindow && !authorizationWindow.closed) {
    try {
      authorizationWindow.location.replace(authorizationUrl);
      return 'popup';
    } catch {
      closeMcpAuthorizationWindow(authorizationWindow);
    }
  }
  assignLocation(authorizationUrl);
  return 'same_page';
}

function safeAuthorizationUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:'
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}

function normalizeAuthorizationStatus(payload: unknown): McpAuthorizationStatus {
  if (!payload || typeof payload !== 'object') {
    throw new Error('Invalid authorization status response');
  }
  const data = payload as Record<string, unknown>;
  if (data.provider !== 'smithery') {
    throw new Error('Invalid authorization status provider');
  }
  if (data.state === 'connected') return { state: 'connected' };
  if (data.state === 'unavailable') return { state: 'unavailable' };
  if (data.state === 'auth_required') {
    const authorizationUrl = safeAuthorizationUrl(data.authorization_url);
    if (!authorizationUrl) {
      throw new Error('Invalid authorization status response');
    }
    return { state: 'auth_required', authorizationUrl };
  }
  throw new Error('Invalid authorization status response');
}

export async function requestMcpAuthorizationStatus(
  agentId: string,
  toolId: string,
  token: string,
  fetchImpl: FetchLike = fetch,
): Promise<McpAuthorizationStatus> {
  const response = await fetchImpl(
    `/api/tools/agents/${encodeURIComponent(agentId)}/mcp-tools/${encodeURIComponent(toolId)}/authorization-status`,
    {
      method: 'GET',
      cache: 'no-store',
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  if (!response.ok) {
    throw new Error('Authorization status request failed');
  }
  return normalizeAuthorizationStatus(await response.json());
}
