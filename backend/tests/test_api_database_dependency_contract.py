"""Regression contract for FastAPI database-session injection."""

from __future__ import annotations

import ast
from pathlib import Path


def test_database_route_parameters_use_fastapi_dependency_injection() -> None:
    api_root = Path(__file__).parents[1] / "app" / "api"
    missing: list[str] = []

    for path in api_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "router"
                for decorator in node.decorator_list
            ):
                continue
            positional = [*node.args.posonlyargs, *node.args.args]
            defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
            parameters = [*zip(positional, defaults), *zip(node.args.kwonlyargs, node.args.kw_defaults)]
            for argument, default in parameters:
                if argument.arg != "db":
                    continue
                if not (
                    isinstance(default, ast.Call)
                    and isinstance(default.func, ast.Name)
                    and default.func.id == "Depends"
                    and len(default.args) == 1
                    and isinstance(default.args[0], ast.Name)
                    and default.args[0].id == "get_db"
                ):
                    missing.append(f"{path.name}:{node.lineno}")

    assert missing == []
