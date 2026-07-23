import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../src/pages/enterprise-settings/tabs/LlmTab.tsx', import.meta.url),
  'utf8',
);
const modelSwitcher = readFileSync(
  new URL('../src/components/ModelSwitcher.tsx', import.meta.url),
  'utf8',
);
const modelCacheEvents = readFileSync(
  new URL('../src/services/modelCacheEvents.ts', import.meta.url),
  'utf8',
);
const agentDetail = readFileSync(
  new URL('../src/pages/agent-detail/AgentDetailPage.tsx', import.meta.url),
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

test('stale runtime model ids stay unselected instead of selecting the first option', () => {
  assert.match(source, /planning_source: 'database' \| 'environment' \| 'unavailable'/);
  assert.match(source, /planning_model_id: runtimeModelSettings\.planning_model_id \|\| ''/);
  assert.match(source, /compact_model_id: runtimeModelSettings\.compact_model_id \|\| ''/);
  assert.match(source, /<option value="" disabled>/);
});

test('chat model choices allow every enabled model and refresh across tabs', () => {
  assert.match(
    modelSwitcher,
    /filter\(m => m\.enabled !== false\)/,
  );
  assert.match(modelSwitcher, /subscribeModelCacheInvalidation/);
  assert.match(modelSwitcher, /void refetchModels\(\)/);
  assert.match(source, /notifyModelCacheInvalidated\(\)/);
  assert.match(modelCacheEvents, /window\.addEventListener\('storage'/);
  assert.match(modelCacheEvents, /window\.dispatchEvent\(new Event\(MODEL_CACHE_EVENT\)\)/);
});

test('chat ignores stale preferred ids before falling back to an enabled model', () => {
  assert.match(
    agentDetail,
    /filter\(\(m: any\) => m\.enabled\)/,
  );
  assert.match(
    agentDetail,
    /\[\s*overrideModelId,\s*agent\?\.primary_model_id,\s*myTenant\?\.default_model_id,\s*\]\.find\(/,
  );
  assert.match(
    agentDetail,
    /enabledLlmModels\.some\(\(model: any\) => model\.id === candidate\)/,
  );
  assert.match(agentDetail, /\|\| enabledLlmModels\[0\]\?\.id/);
});
