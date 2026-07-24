export interface ProjectTeamRole {
    key: string;
    name: string;
    role_description: string;
    personality: string;
    boundaries: string;
    is_group_leader: boolean;
}

export interface TeamPlan {
    planner_name: string;
    project_name: string;
    requirements: string;
    roles: ProjectTeamRole[];
    wake_up_message: string;
}

export interface ProjectWorkflowMember {
    agent_id: string;
    role_key: string;
    role_title: string;
    is_group_leader: boolean;
}

export interface ProjectWorkflow {
    id: string;
    name: string;
    template_key: string;
    requirements: string;
    status: 'planning' | 'provisioning' | 'active' | 'failed' | string;
    team_plan: TeamPlan;
    group_id: string | null;
    group_leader_agent_id: string | null;
    failure_reason: string | null;
    created_at: string;
    members: ProjectWorkflowMember[];
}
