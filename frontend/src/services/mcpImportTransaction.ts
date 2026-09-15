export interface McpImportOperations<T> {
  createTool: (tool: T) => Promise<{ id: string }>;
  saveCredential: () => Promise<void>;
  deleteTool: (id: string) => Promise<void>;
}

export interface McpImportResult {
  createdIds: string[];
  creationErrors: Array<{ index: number; error: unknown }>;
}

export class McpCredentialSaveError extends Error {
  readonly cause: unknown;
  readonly rollbackFailedIds: string[];

  constructor(cause: unknown, rollbackFailedIds: string[]) {
    super("MCP credential save failed");
    this.name = "McpCredentialSaveError";
    this.cause = cause;
    this.rollbackFailedIds = rollbackFailedIds;
  }
}

export async function importMcpToolsTransaction<T>(
  tools: T[],
  options: { apiKey: string },
  operations: McpImportOperations<T>,
): Promise<McpImportResult> {
  const createdIds: string[] = [];
  const creationErrors: Array<{ index: number; error: unknown }> = [];

  for (const [index, tool] of tools.entries()) {
    try {
      const created = await operations.createTool(tool);
      createdIds.push(created.id);
    } catch (error) {
      creationErrors.push({ index, error });
    }
  }

  if (options.apiKey && createdIds.length > 0) {
    try {
      await operations.saveCredential();
    } catch (error) {
      const rollbackResults = await Promise.allSettled(
        createdIds.map((id) => operations.deleteTool(id)),
      );
      const rollbackFailedIds = createdIds.filter(
        (_id, index) => rollbackResults[index]?.status === "rejected",
      );
      throw new McpCredentialSaveError(error, rollbackFailedIds);
    }
  }

  return { createdIds, creationErrors };
}
