import { parseHttpErrorResponse } from "../../../services/apiError.ts";
import { parseLlmModelResponse, type LLMModel } from "./responseParsers.ts";

interface UpdateLlmModelEnabledOptions {
  modelId: string;
  enabled: boolean;
  token: string | null;
  fetchImpl?: typeof fetch;
  parseError?: (response: Response) => Promise<Error>;
}

export async function updateLlmModelEnabled({
  modelId,
  enabled,
  token,
  fetchImpl = fetch,
  parseError = parseHttpErrorResponse,
}: UpdateLlmModelEnabledOptions): Promise<LLMModel> {
  const response = await fetchImpl(
    `/api/enterprise/llm-models/${encodeURIComponent(modelId)}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ enabled }),
    },
  );
  if (!response.ok) throw await parseError(response);
  return parseLlmModelResponse(await response.json());
}
