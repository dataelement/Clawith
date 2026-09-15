type FetchTools = (
  url: string,
  init: RequestInit & { headers: { Authorization: string } },
) => Promise<Response>;

interface ParseListOptions<T> {
  payload: unknown;
  parseItem: (item: unknown) => T | null;
  contractName: string;
}

export function parseCompleteList<T>({
  payload,
  parseItem,
  contractName,
}: ParseListOptions<T>): T[] {
  if (!Array.isArray(payload)) {
    throw new Error(`Invalid ${contractName} response`);
  }
  const parsed = payload.map(parseItem);
  if (parsed.some((item) => item === null)) {
    throw new Error(`Invalid ${contractName} response`);
  }
  return parsed.filter((item): item is T => item !== null);
}

export function updateToolEnabled<T extends { id: string; enabled: boolean }>(
  tools: T[],
  toolIds: ReadonlySet<string>,
  enabled: boolean,
): T[] {
  return tools.map((tool) =>
    toolIds.has(tool.id) ? { ...tool, enabled } : tool,
  );
}

interface RequestAgentToolsOptions<T> {
  agentId: string;
  token: string | null;
  parsePayload: (payload: unknown) => T;
  parseError: (response: Response) => Promise<Error>;
  fetchImpl?: FetchTools;
}

export async function requestAgentToolsWithConfig<T>({
  agentId,
  token,
  parsePayload,
  parseError,
  fetchImpl = fetch,
}: RequestAgentToolsOptions<T>): Promise<T> {
  const response = await fetchImpl(
    `/api/tools/agents/${encodeURIComponent(agentId)}/with-config`,
    { headers: { Authorization: `Bearer ${token || ""}` } },
  );
  if (!response.ok) {
    throw await parseError(response);
  }
  return parsePayload(await response.json());
}

interface RequestToolsJsonOptions<T> {
  url: string;
  token: string | null;
  method?: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
  parsePayload: (payload: unknown) => T;
  parseError: (response: Response) => Promise<Error>;
  fetchImpl?: FetchTools;
}

export async function requestToolsJson<T>({
  url,
  token,
  method = "GET",
  body,
  signal,
  parsePayload,
  parseError,
  fetchImpl = fetch,
}: RequestToolsJsonOptions<T>): Promise<T> {
  const response = await fetchImpl(url, {
    method,
    signal,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token || ""}`,
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) throw await parseError(response);
  return parsePayload(await response.json());
}

interface RequestToolsMutationOptions {
  url: string;
  token: string | null;
  method: "POST" | "PUT" | "DELETE";
  body?: unknown;
  parseError: (response: Response) => Promise<Error>;
  fetchImpl?: FetchTools;
}

export async function requestToolsMutation({
  url,
  token,
  method,
  body,
  parseError,
  fetchImpl = fetch,
}: RequestToolsMutationOptions): Promise<void> {
  const response = await fetchImpl(url, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token || ""}`,
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) throw await parseError(response);
}

export function resetAgentToolConfig({
  agentId,
  toolId,
  token,
  parseError,
  fetchImpl,
}: {
  agentId: string;
  toolId: string;
  token: string | null;
  parseError: (response: Response) => Promise<Error>;
  fetchImpl?: FetchTools;
}): Promise<void> {
  return requestToolsMutation({
    url: `/api/tools/agents/${encodeURIComponent(agentId)}/tool-config/${encodeURIComponent(toolId)}`,
    token,
    method: "PUT",
    body: { config: {} },
    parseError,
    fetchImpl,
  });
}
