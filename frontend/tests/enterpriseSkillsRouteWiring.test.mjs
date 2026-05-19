import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const enterpriseSettingsPath = new URL('../src/pages/EnterpriseSettings.tsx', import.meta.url);

test('EnterpriseSettings routes skills tab through canonical tab file', async () => {
  const source = await readFile(enterpriseSettingsPath, 'utf8');

  assert.match(
    source,
    /import\s+SkillsTab\s+from\s+['"]\.\/enterprise-settings\/tabs\/SkillsTab['"];?/,
    'EnterpriseSettings.tsx should import SkillsTab from the canonical tab module',
  );

  assert.doesNotMatch(
    source,
    /function\s+SkillsTab\s*\(/,
    'EnterpriseSettings.tsx should not define an inline SkillsTab implementation',
  );
});
