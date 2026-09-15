import assert from "node:assert/strict";
import test from "node:test";

import { skillApi } from "../src/services/api.ts";

globalThis.localStorage = {
  getItem: () => null,
  removeItem: () => {},
  setItem: () => {},
};
globalThis.window = { location: { href: "" } };

test("skill get, create, update, and delete parse their real endpoint shapes", async () => {
  const responses = [
    {
      id: "skill-1",
      name: "Research",
      description: null,
      category: "general",
      icon: null,
      folder_name: "research",
      is_builtin: false,
      files: [{ path: "SKILL.md", content: "# Research" }],
    },
    { id: "skill-2", name: "Created" },
    { id: "skill-2", name: "Updated" },
    { ok: true },
  ];
  globalThis.fetch = async () =>
    new Response(JSON.stringify(responses.shift()), { status: 200 });

  assert.equal((await skillApi.get("skill-1")).files.length, 1);
  assert.deepEqual(await skillApi.create({ name: "Created" }), {
    id: "skill-2",
    name: "Created",
  });
  assert.deepEqual(await skillApi.update("skill-2", { name: "Updated" }), {
    id: "skill-2",
    name: "Updated",
  });
  assert.equal(await skillApi.delete("skill-2"), undefined);
});

test("skill endpoint parsers reject cross-endpoint successful shapes", async () => {
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ id: "skill-1", name: "Incomplete" }), {
      status: 200,
    });
  await assert.rejects(
    () => skillApi.get("skill-1"),
    (error) => error?.code === "invalid_api_response",
  );

  globalThis.fetch = async () =>
    new Response(JSON.stringify({ status: "deleted" }), { status: 200 });
  await assert.rejects(
    () => skillApi.delete("skill-1"),
    (error) => error?.code === "invalid_api_response",
  );
});
