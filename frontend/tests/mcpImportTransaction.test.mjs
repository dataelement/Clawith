import assert from "node:assert/strict";
import test from "node:test";

import {
  McpCredentialSaveError,
  importMcpToolsTransaction,
} from "../src/services/mcpImportTransaction.ts";

test("MCP credential failure rolls back every tool before rejecting", async () => {
  const deleted = [];
  await assert.rejects(
    importMcpToolsTransaction(
      [{ name: "one" }, { name: "two" }],
      { apiKey: "secret" },
      {
        createTool: async (tool) => ({ id: `id-${tool.name}` }),
        saveCredential: async () => {
          throw new Error("credential rejected");
        },
        deleteTool: async (id) => {
          deleted.push(id);
        },
      },
    ),
    (error) => {
      assert.ok(error instanceof McpCredentialSaveError);
      assert.deepEqual(error.rollbackFailedIds, []);
      return true;
    },
  );
  assert.deepEqual(deleted, ["id-one", "id-two"]);
});

test("MCP rollback failures remain explicit", async () => {
  await assert.rejects(
    importMcpToolsTransaction(
      [{ name: "one" }],
      { apiKey: "secret" },
      {
        createTool: async () => ({ id: "id-one" }),
        saveCredential: async () => {
          throw new Error("credential rejected");
        },
        deleteTool: async () => {
          throw new Error("delete rejected");
        },
      },
    ),
    (error) => {
      assert.ok(error instanceof McpCredentialSaveError);
      assert.deepEqual(error.rollbackFailedIds, ["id-one"]);
      return true;
    },
  );
});

test("primary MCP creation failures remain partial only after credential success", async () => {
  let credentialSaved = false;
  const result = await importMcpToolsTransaction(
    [{ name: "one" }, { name: "two" }],
    { apiKey: "secret" },
    {
      createTool: async (tool) => {
        if (tool.name === "two") throw new Error("duplicate");
        return { id: "id-one" };
      },
      saveCredential: async () => {
        credentialSaved = true;
      },
      deleteTool: async () => {},
    },
  );
  assert.equal(credentialSaved, true);
  assert.deepEqual(result.createdIds, ["id-one"]);
  assert.equal(result.creationErrors.length, 1);
});
