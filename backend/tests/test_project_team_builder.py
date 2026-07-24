import pytest

from app.services.project_team_builder import HRPlanningError, parse_hr_team_plan, validate_team_plan


def test_hr_plan_is_derived_from_its_response_not_a_builtin_template() -> None:
    plan = parse_hr_team_plan(
        name="品牌快闪活动",
        requirements="在两周内完成线下快闪活动的策划、执行和复盘。",
        response_text='''{"roles":[
          {"name":"品牌主理人","role_description":"统筹活动并向用户汇报","is_group_leader":true},
          {"name":"线下活动执行","role_description":"负责场地、供应商和现场执行","is_group_leader":false},
          {"name":"品牌内容策划","role_description":"负责传播主题和内容素材","is_group_leader":false}
        ]}''',
    )
    assert plan["planner_name"] == "HR 招聘 Agent"
    assert [role["name"] for role in plan["roles"]] == ["品牌主理人", "线下活动执行", "品牌内容策划"]
    assert sum(role["is_group_leader"] for role in plan["roles"]) == 1
    assert "@品牌主理人" in plan["wake_up_message"]
    assert "@线下活动执行" in plan["wake_up_message"]


def test_confirmed_plan_requires_one_and_only_one_group_leader() -> None:
    plan = {"roles": [
        {"key": "leader", "name": "Founder", "role_description": "Coordinate", "is_group_leader": True},
        {"key": "writer", "name": "Writer", "role_description": "Write", "is_group_leader": False},
    ]}
    for role in plan["roles"]:
        role["is_group_leader"] = False
    with pytest.raises(ValueError, match="exactly one group leader"):
        validate_team_plan(plan)

    plan["roles"][0]["is_group_leader"] = True
    plan["roles"][1]["is_group_leader"] = True
    with pytest.raises(ValueError, match="exactly one group leader"):
        validate_team_plan(plan)


def test_hr_response_without_json_is_rejected() -> None:
    with pytest.raises(HRPlanningError):
        parse_hr_team_plan(name="Launch", requirements="Ship it", response_text="team: leader")
