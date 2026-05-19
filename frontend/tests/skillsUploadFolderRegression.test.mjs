import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const enterpriseSkillsTabPath = new URL('../src/pages/enterprise-settings/tabs/SkillsTab.tsx', import.meta.url);
const skillFolderUploadModalPath = new URL('../src/components/skills/SkillFolderUploadModal.tsx', import.meta.url);

test('enterprise skills tab keeps the upload-folder button and modal wiring', async () => {
  const source = await readFile(enterpriseSkillsTabPath, 'utf8');

  assert.match(source, /uploadFolderModal\.openButton/);
  assert.match(source, /SkillFolderUploadModal/);
  assert.match(source, /showSkillFolderUploadModal/);
  assert.match(source, /i18nPrefix="enterprise\.tools\.uploadFolderModal"/);
});

test('skill folder upload modal renders directory-selection attributes on the file input', async () => {
  const source = await readFile(skillFolderUploadModalPath, 'utf8');

  assert.match(source, /<input[\s\S]*type="file"/);
  assert.match(source, /webkitdirectory/);
  assert.match(source, /directory/);
  assert.doesNotMatch(source, /setAttribute\('webkitdirectory'/);
});
