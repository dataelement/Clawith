import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../src/pages/enterprise-settings/tabs/LlmTab.tsx', import.meta.url),
  'utf8',
);

test('company admins can select planning and group context models', () => {
  assert.match(source, /\/enterprise\/runtime-model-settings/);
  assert.match(source, /planning_model_id/);
  assert.match(source, /compact_model_id/);
  assert.match(source, /currentUser\?\.role === 'platform_admin'/);
  assert.match(source, /currentUser\?\.role === 'org_admin'/);
  assert.match(source, /currentUser\?\.is_platform_admin/);
  assert.match(source, /tenant_id=\$\{selectedTenantId\}/);
  assert.match(source, /群聊规划模型/);
  assert.match(source, /群聊上下文模型/);
});

test('runtime model choices are restricted to tenant-safe backend candidates', () => {
  assert.match(source, /runtimeModelSettings\.candidates\.map/);
  assert.match(source, /当前公司的模型或平台模型/);
  assert.match(source, /保存后立即生效/);
});
