import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const editor = readFileSync(
  new URL("../src/pages/groups/GroupTextFileEditor.tsx", import.meta.url),
  "utf8",
);

test("cached group announcement remains visible when its tab remounts", () => {
  assert.match(editor, /key=\{JSON\.stringify\(queryKey\)\}/);
  assert.match(
    editor,
    /const \[draft, setDraft\] = useState<string \| null>\(null\)/,
  );
  assert.match(
    editor,
    /const content = draft \?\? data\?\.content \?\? ["']["']/,
  );
  assert.doesNotMatch(editor, /setDraft\(data\.content\)/);
});
