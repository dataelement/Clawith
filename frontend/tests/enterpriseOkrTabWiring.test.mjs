import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const enterpriseSettingsPath = new URL('../src/pages/EnterpriseSettings.tsx', import.meta.url);
const enPath = new URL('../src/i18n/en.json', import.meta.url);
const zhPath = new URL('../src/i18n/zh.json', import.meta.url);

test('EnterpriseSettings wires the OKR tab and honors #okr deep links', async () => {
  const source = await readFile(enterpriseSettingsPath, 'utf8');

  assert.match(
    source,
    /import\s+OkrTab\s+from\s+['"]\.\/enterprise-settings\/tabs\/OkrTab['"];?/,
    'EnterpriseSettings.tsx should import OkrTab from the canonical tab module',
  );

  assert.match(
    source,
    /type\s+EnterpriseTab\s*=\s*[^;]*'okr'[^;]*;/,
    'EnterpriseSettings tab type should include the okr tab key',
  );

  assert.match(
    source,
    /ENTERPRISE_TABS\s*=\s*\[(?:[^\]]*'okr'[^\]]*)\]\s+as\s+const/,
    'EnterpriseSettings tab list should include the okr tab',
  );

  assert.match(
    source,
    /activeTab\s*===\s*'okr'\s*&&\s*<OkrTab\s+tenantId=\{selectedTenantId\}\s+t=\{t\}\s*\/>/,
    'EnterpriseSettings should render OkrTab when the okr tab is active',
  );

  assert.match(
    source,
    /location\.hash|window\.location\.hash/,
    'EnterpriseSettings should read the URL hash so /enterprise#okr opens the OKR tab',
  );
});

test('EnterpriseSettings translations include an OKR tab label', async () => {
  const en = JSON.parse(await readFile(enPath, 'utf8'));
  const zh = JSON.parse(await readFile(zhPath, 'utf8'));

  assert.equal(en.enterprise?.tabs?.okr, 'OKR', 'English enterprise tabs should include an OKR label');
  assert.equal(zh.enterprise?.tabs?.okr, 'OKR', 'Chinese enterprise tabs should include an OKR label');
});
