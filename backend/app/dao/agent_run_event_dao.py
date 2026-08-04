"""DAO for AgentRunEvent model — re-exported via agent_run_dao for domain grouping."""
# AgentRunEvent queries are included in AgentRunDAO (agent_run_dao.py) per the
# "helper submodels in same DAO file" rule from dao/AGENTS.md §6.4.
# This stub file exists only for import compatibility; do not add queries here.
from app.dao.agent_run_dao import agent_run_dao

__all__ = ["agent_run_dao"]
