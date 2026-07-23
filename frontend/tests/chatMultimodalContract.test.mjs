import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../src/pages/agent-detail/AgentDetailPage.tsx', import.meta.url),
  'utf8',
);

test('persisted multi-image chat renders individual image markers', () => {
  assert.match(
    source,
    /const inlineImageMarkerCount = \(\s*parsed\.content\.match\(\/\\\[image_data:data:image\\\/\/g\) \|\| \[\]\s*\)\.length/,
  );
  assert.match(source, /inlineImageMarkerCount <= 1/);
  assert.match(source, /inlineImages\.map\(\(url, idx\) =>/);
});
