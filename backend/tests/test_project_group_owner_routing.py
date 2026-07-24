from pathlib import Path


def test_project_group_messages_force_route_to_group_leader() -> None:
    source = Path("app/services/group_message_service.py").read_text()
    assert "scope.group.owner_agent_id is not None" in source
    assert 'Participant.type == "agent"' in source
    assert "mention_ids = (owner_participant_id,)" in source


def test_owner_role_is_distinct_from_human_manager() -> None:
    source = Path("app/models/group.py").read_text()
    assert "'manager', 'owner', 'member'" in source
    assert "owner_agent_id" in source


def test_project_group_leader_cannot_be_removed_by_a_group_manager() -> None:
    source = Path("app/services/group_chat_service.py").read_text()
    assert "group_owner_required" in source


def test_standard_agent_initialization_materializes_workspace_roots() -> None:
    source = Path("app/services/agent_manager.py").read_text()
    assert '"workspace/.gitkeep"' in source
    assert '"daily_reports/.gitkeep"' in source


def test_project_creation_makes_teammates_mutual_contacts_and_kicks_off_leader() -> None:
    source = Path("app/api/projects.py").read_text()
    assert "AgentAgentRelationship" in source
    assert 'relation="project_teammate"' in source
    assert "build_team_wakeup_message" in source
    assert "group_message_service.enqueue_group_message" in source


def test_project_group_is_exposed_only_after_all_members_are_ready() -> None:
    source = Path("app/api/projects.py").read_text()
    assert "async def _provision_project_agents" in source
    assert "项目团队缺少可用主模型" in source
    assert "agent.status not in {\"running\", \"idle\"}" in source
    assert source.index("await _provision_project_agents(") < source.index(
        "await group_chat_service.create_group("
    )
    assert "_background_project_agent_setup" not in source


def test_project_owner_can_repair_a_previously_creating_team() -> None:
    source = Path("app/api/projects.py").read_text()
    assert '@router.post("/{workflow_id}/provision", response_model=ProjectOut)' in source
    assert "Project workflow not found" in source
    assert "without requiring administrator access" in source
    assert "ProjectProvisioningError" in source
    assert 'workflow.status = "active"' in source


def test_decision_reply_can_be_an_ai_modification_instruction() -> None:
    source = Path("app/api/projects.py").read_text()
    assert 'intent: Literal["decision", "modification"]' in source
    assert "【用户修改指令】" in source
    assert "更新相关任务、依赖、负责人或验收标准" in source


def test_decision_ai_draft_is_generated_without_answering_or_notifying_group() -> None:
    source = Path("app/api/projects.py").read_text()
    start = source.index("async def generate_project_decision_draft(")
    end = source.index('@router.post("/groups/{group_id}/decisions/{decision_id}/reply"', start)
    draft_route = source[start:end]

    assert 'ProjectDecision.status == "pending"' in draft_route
    assert "decision.status =" not in draft_route
    assert "decision.response =" not in draft_route
    assert "enqueue_group_message" not in draft_route
    assert "<think>/<thinking>" in draft_route
