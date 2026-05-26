import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const cssPath = path.resolve(__dirname, '../src/index.css');

function getRuleBody(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`${escaped}\\s*\\{([\\s\\S]*?)\\}`, 'm'));
  return match?.[1] ?? null;
}

test('chat input defines a base full-width layout rule', async () => {
  const css = await readFile(cssPath, 'utf8');
  const body = getRuleBody(css, '.chat-input');

  assert.ok(body, 'expected a base .chat-input rule');
  assert.match(body, /width:\s*100%\s*;/, 'expected .chat-input to set width: 100%');
  assert.match(body, /min-width:\s*0\s*;/, 'expected .chat-input to set min-width: 0');
});
