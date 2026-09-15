import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { updateLlmModelEnabled } from "../src/pages/enterprise-settings/utils/llmModelToggle.ts";

const model = {
  id: "model-1",
  provider: "openai",
  model: "gpt-test",
  label: "Test",
  enabled: false,
  created_at: "2026-08-26T00:00:00Z",
};

test("LLM model toggle validates success before resolving", async () => {
  const calls = [];
  const result = await updateLlmModelEnabled({
    modelId: "model / 1",
    enabled: false,
    token: "browser-token",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return new Response(JSON.stringify(model), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  assert.equal(result.id, model.id);
  assert.equal(result.enabled, false);
  assert.deepEqual(calls, [
    {
      url: "/api/enterprise/llm-models/model%20%2F%201",
      init: {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer browser-token",
        },
        body: JSON.stringify({ enabled: false }),
      },
    },
  ]);
});

test("LLM model toggle preserves canonical 401, 403, and 500 errors", async () => {
  for (const status of [401, 403, 500]) {
    await assert.rejects(
      () =>
        updateLlmModelEnabled({
          modelId: "model-1",
          enabled: true,
          token: null,
          fetchImpl: async () =>
            new Response(
              JSON.stringify({
                error: {
                  code: `llm_toggle_${status}`,
                  message: `toggle failed with ${status}`,
                },
              }),
              {
                status,
                headers: { "Content-Type": "application/json" },
              },
            ),
        }),
      (error) =>
        error.status === status &&
        error.code === `llm_toggle_${status}` &&
        error.message === `toggle failed with ${status}`,
    );
  }
});

test("LLM model toggle rejects malformed 2xx responses before cache publication", async () => {
  await assert.rejects(
    () =>
      updateLlmModelEnabled({
        modelId: "model-1",
        enabled: true,
        token: null,
        fetchImpl: async () =>
          new Response(JSON.stringify({ ...model, enabled: "yes" }), {
            status: 200,
          }),
      }),
    /LLM model.enabled/,
  );

  const source = readFileSync(
    new URL(
      "../src/pages/enterprise-settings/tabs/LlmTab.tsx",
      import.meta.url,
    ),
    "utf8",
  );
  assert.match(
    source,
    /await updateLlmModelEnabled\([\s\S]*?\);\s*invalidateModelCaches\(\)/,
  );
});
