import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../src/pages/EnterpriseSettings.tsx', import.meta.url),
  'utf8',
);

test('company settings do not render the protocol-level system tools group', () => {
  assert.match(source, /if \(category === 'system'\) return null/);
  assert.doesNotMatch(source, /visibleGlobalTools/);
});
