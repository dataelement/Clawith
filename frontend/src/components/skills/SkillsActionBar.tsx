import React from 'react';
import type { SkillActionId } from './skillsActionItems';

export type SkillsActionBarAction = {
  id: SkillActionId;
  label: string;
  variant?: 'primary' | 'secondary' | 'outline' | 'ghost';
  title?: string;
  onClick: () => void | Promise<void>;
};

type SkillsActionBarProps = {
  title: React.ReactNode;
  description: React.ReactNode;
  actions: SkillsActionBarAction[];
};

export default function SkillsActionBar({
  title,
  description,
  actions,
}: SkillsActionBarProps) {
  return (
    <div style={{ marginBottom: '12px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px' }}>
      <div>
        <h3>{title}</h3>
        <p style={{ fontSize: '13px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
          {description}
        </p>
      </div>
      <div style={{ display: 'flex', gap: '8px', flexShrink: 0, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        {actions.map((action) => (
          <button
            key={action.id}
            className={`btn btn-${action.variant || 'secondary'}`}
            style={{ fontSize: '13px' }}
            onClick={action.onClick}
            title={action.title}
          >
            {action.label}
          </button>
        ))}
      </div>
    </div>
  );
}
