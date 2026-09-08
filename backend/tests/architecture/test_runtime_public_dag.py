"""Public imports, unlike schema foreign keys, follow the declared service DAG."""

import ast
import json
from pathlib import Path

import pytest


def violations(source: str, owner: str, allowed: set[str]) -> set[str]:
    result = set()
    for node in ast.walk(ast.parse(source)):
        imports = ([node.module] if isinstance(node, ast.ImportFrom) and node.module else
                   [item.name for item in node.names] if isinstance(node, ast.Import) else [])
        for module in imports:
            parts = module.split(".")
            if (len(parts) >= 4 and parts[:2] == ["app", "modules"] and parts[3] == "public"
                    and parts[2] != owner and parts[2] not in allowed):
                result.add(parts[2])
    return result


def test_run_context_public_imports_follow_declared_dependencies():
    backend = Path(__file__).resolve().parents[2]
    rows = json.loads((backend / "rewrite/owner-dag.json").read_text())["owners"]
    dependencies = {row["owner_id"]: set(row["depends_on"]) for row in rows}
    for owner in ("run", "context"):
        for path in (backend / "app/modules" / owner).glob("*.py"):
            assert not violations(path.read_text(), owner, dependencies[owner]), path


@pytest.mark.parametrize("source,expected", [
    ("from app.modules.model.public import ModelMessage", set()),
    ("from app.modules.run.public import RunService", {"run"}),
    ("import app.modules.run.public", {"run"}),
    ("reference = 'agent_runs.id'", set()),
])
def test_context_cannot_reverse_the_execution_dependency(source, expected):
    assert violations(source, "context", {"model"}) == expected
