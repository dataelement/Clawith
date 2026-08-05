import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../src/pages/agent-detail/AgentDetailPage.tsx', import.meta.url),
  'utf8',
);

test('final empty assistant packets do not create a chat bubble', () => {
  assert.match(source, /const isAssistantEmpty = msg\.role === 'assistant'[\s\S]*?!msg\.content\?\.trim\(\)[\s\S]*?!msg\.thinking\?\.trim\(\)[\s\S]*?!msg\.runtimeError[\s\S]*?!msg\.fileName[\s\S]*?!msg\.imageUrl/);
  assert.match(source, /if \(!isAssistantEmpty\) \{\s*grouped\.push\(\{ type: 'msg', msg, i \}\);\s*\}/);
});
