export type SkillActionId =
  | 'settings'
  | 'upload-folder'
  | 'import-url'
  | 'browse-clawhub'
  | 'import-presets';

const ENTERPRISE_SKILL_ACTION_IDS: SkillActionId[] = [
  'settings',
  'upload-folder',
  'import-url',
  'browse-clawhub',
];

const AGENT_SKILL_ACTION_IDS: SkillActionId[] = [
  'browse-clawhub',
  'import-presets',
  'upload-folder',
];

export function getEnterpriseSkillActionIds(): SkillActionId[] {
  return [...ENTERPRISE_SKILL_ACTION_IDS];
}

export function getAgentSkillActionIds(): SkillActionId[] {
  return [...AGENT_SKILL_ACTION_IDS];
}
