import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const editor = readFileSync(
  new URL("../src/components/ExperienceDraftEditor.tsx", import.meta.url),
  "utf8",
);
const api = readFileSync(
  new URL("../src/services/api.ts", import.meta.url),
  "utf8",
);
const apiContracts = readFileSync(
  new URL("../src/services/apiContracts.ts", import.meta.url),
  "utf8",
);

test("editing a published experience saves through an independent revision draft", () => {
  assert.match(apiContracts, /draft_of_id: string \| null/);
  assert.match(
    api,
    /createRevision: \(id: string, data: Partial<ExperienceEntry>\)/,
  );
  assert.match(
    editor,
    /const isRevisionSource =\s*draft\.status === ["']published["'] \|\|\s*draft\.status === ["']retired["']/,
  );
  assert.match(
    editor,
    /const draftId = draft\.id;[\s\S]*?if \(!draftId\) throw new Error\(["']Draft id is required["']\);[\s\S]*?if \(isRevisionSource\)\s*return experienceApi\.createRevision\(draftId, payload\)/,
  );
});

test("publishing a published-entry edit promotes its revision instead of patching the live source first", () => {
  assert.match(
    editor,
    /else if \(isRevisionSource\) \{[\s\S]*?if \(!draft\.id\) throw new Error\(["']Draft id is required["']\);[\s\S]*?experienceApi\.createRevision\(draft\.id, payload\)[\s\S]*?experienceApi\.publish\(id\)/,
  );
});

test("the editor never offers draft deletion for a live published entry", () => {
  assert.match(
    editor,
    /const canDelete =\s*draft\.status === ["']draft["'] \|\|\s*draft\.status === ["']retired["']/,
  );
  assert.match(editor, /\{canDelete && onDeleted && \(/);
  assert.doesNotMatch(editor, /\{!isNew && onDeleted && \(/);
});
