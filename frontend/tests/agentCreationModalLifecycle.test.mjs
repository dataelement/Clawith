import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const customAgentModal = readFileSync(
  new URL("../src/components/CustomAgentModal.tsx", import.meta.url),
  "utf8",
);
const postHireModal = readFileSync(
  new URL("../src/components/PostHireSettingsModal.tsx", import.meta.url),
  "utf8",
);

test("agent creation forms reset by unmounting their local draft owners", () => {
  assert.match(customAgentModal, /if \(!open\) return null/);
  assert.match(customAgentModal, /<CustomAgentModalContent/);
  assert.match(postHireModal, /if \(!open \|\| !template\) return null/);
  assert.match(postHireModal, /<PostHireSettingsModalContent/);
  assert.doesNotMatch(customAgentModal, /setMode\(initialMode\)[\s\S]*?\[open/);
  assert.doesNotMatch(
    postHireModal,
    /setVisibility\(["']company["']\)[\s\S]*?\[open/,
  );
});

test("agent creation model defaults stay derived until the user chooses one", () => {
  for (const source of [customAgentModal, postHireModal]) {
    assert.match(source, /const preferredModelId =/);
    assert.match(
      source,
      /const selectedModelId = modelId \|\| preferredModelId/,
    );
    assert.doesNotMatch(source, /setModelId\(preferred/);
  }
});
