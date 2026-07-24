import { fetchJson } from './api';
import type { ProjectWorkflow, TeamPlan } from '../types/project';

export const projectApi = {
    buildTeamPlan: (data: { name: string; requirements: string }) =>
        fetchJson<TeamPlan>('/projects/team-plans', { method: 'POST', body: JSON.stringify(data) }),
    create: (data: { name: string; requirements: string; team_plan: TeamPlan }) =>
        fetchJson<ProjectWorkflow>('/projects', { method: 'POST', body: JSON.stringify(data) }),
    provision: (workflowId: string) =>
        fetchJson<ProjectWorkflow>(`/projects/${workflowId}/provision`, { method: 'POST' }),
    list: () => fetchJson<ProjectWorkflow[]>('/projects'),
};
