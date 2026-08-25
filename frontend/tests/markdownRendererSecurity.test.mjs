import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const renderer = readFileSync(
  new URL('../src/components/MarkdownRenderer.tsx', import.meta.url),
  'utf8',
);

test('code fence language is constrained before being interpolated into HTML', () => {
  assert.match(renderer, /function sanitizeCodeLanguage\(language: string\): string/);
  assert.match(renderer, /return \/\^\[A-Za-z0-9\]/);
  assert.match(renderer, /codeLang = sanitizeCodeLanguage\(line\.slice\(3\)\.trim\(\)\)/);
  assert.match(renderer, /class="language-\$\{codeLang\}"/);
});
