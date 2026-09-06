from __future__ import annotations

import ast
from pathlib import Path

import pytest


class BoundaryViolation(AssertionError):
    pass


def _write(root: Path, relative_path: str, source: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _python_files(root: Path) -> list[Path]:
    return sorted((root / "app").rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _validate_single_application_contract(root: Path) -> None:
    factories: list[Path] = []
    registries: list[Path] = []
    for path in _python_files(root):
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _qualified_name(node.func) == "FastAPI":
                factories.append(path)
            if isinstance(node, ast.Call) and _qualified_name(node.func) in {
                "MetaData",
                "sqlalchemy.MetaData",
            }:
                registries.append(path)
            if isinstance(node, ast.ClassDef) and any(
                (_qualified_name(base) or "").endswith("DeclarativeBase") for base in node.bases
            ):
                registries.append(path)
    if len(factories) != 1:
        raise BoundaryViolation(f"expected one FastAPI application factory, found {len(factories)}")
    if len(registries) != 1:
        raise BoundaryViolation(f"expected one SQLAlchemy metadata registry, found {len(registries)}")


def _imported_modules(tree: ast.Module) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _validate_owner_private_imports(root: Path) -> None:
    modules_root = root / "app/modules"
    for path in sorted(modules_root.rglob("*.py")):
        relative = path.relative_to(modules_root)
        if len(relative.parts) < 2:
            continue
        importing_owner = relative.parts[0]
        for imported in _imported_modules(_parse(path)):
            parts = imported.split(".")
            if len(parts) < 4 or parts[:2] != ["app", "modules"]:
                continue
            imported_owner = parts[2]
            private_surface = parts[3] in {
                "crypto",
                "model",
                "models",
                "repository",
                "repositories",
            }
            if imported_owner != importing_owner and private_surface:
                raise BoundaryViolation(
                    f"{importing_owner} imports {imported_owner}'s private owner surface: {imported}"
                )


def _validate_runtime_is_narrow(root: Path) -> None:
    runtime_root = root / "app/runtime"
    forbidden_filenames = {"model.py", "models.py", "repository.py", "repositories.py"}
    for path in sorted(runtime_root.rglob("*.py")):
        if path.name in forbidden_filenames:
            raise BoundaryViolation(f"runtime must not own persistence: {path.name}")
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                (_qualified_name(base) or "").endswith("DeclarativeBase") for base in node.bases
            ):
                raise BoundaryViolation("runtime must not declare a metadata registry")
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__tablename__" for target in node.targets
            ):
                raise BoundaryViolation("runtime must not own ORM tables")


def _valid_modular_tree(root: Path) -> None:
    _write(
        root,
        "app/infrastructure/database.py",
        "from sqlalchemy.orm import DeclarativeBase\n\nclass Base(DeclarativeBase):\n    pass\n",
    )
    _write(
        root,
        "app/factory.py",
        "from fastapi import FastAPI\n\ndef create_app():\n    return FastAPI()\n",
    )


def test_modular_application_accepts_one_factory_and_metadata_registry(tmp_path: Path) -> None:
    _valid_modular_tree(tmp_path)

    _validate_single_application_contract(tmp_path)


def test_modular_application_rejects_a_second_application_factory(tmp_path: Path) -> None:
    _valid_modular_tree(tmp_path)
    _write(tmp_path, "app/modules/auth/app.py", "from fastapi import FastAPI\napp = FastAPI()\n")

    with pytest.raises(BoundaryViolation, match="one FastAPI application factory"):
        _validate_single_application_contract(tmp_path)


def test_modular_application_rejects_a_second_metadata_registry(tmp_path: Path) -> None:
    _valid_modular_tree(tmp_path)
    _write(
        tmp_path,
        "app/modules/agent/models.py",
        "from sqlalchemy.orm import DeclarativeBase\n\nclass AgentBase(DeclarativeBase):\n    pass\n",
    )

    with pytest.raises(BoundaryViolation, match="one SQLAlchemy metadata registry"):
        _validate_single_application_contract(tmp_path)


def test_owner_can_import_another_owners_public_service_contract(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "app/modules/session/service.py",
        "from app.modules.run.public import RunService\n",
    )

    _validate_owner_private_imports(tmp_path)


@pytest.mark.parametrize("private_module", ["crypto", "models", "repositories"])
def test_owner_cannot_import_another_owners_private_surface(
    tmp_path: Path, private_module: str
) -> None:
    _write(
        tmp_path,
        "app/modules/session/service.py",
        f"from app.modules.run.{private_module} import Run\n",
    )

    with pytest.raises(BoundaryViolation, match="private owner surface"):
        _validate_owner_private_imports(tmp_path)


def test_owner_can_import_its_own_private_crypto(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "app/modules/credential/public.py",
        "from app.modules.credential.crypto import CredentialKeyring\n",
    )

    _validate_owner_private_imports(tmp_path)


def test_runtime_accepts_run_loop_execution_mechanics(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "app/runtime/loop.py",
        "from app.modules.run.public import RunCommand\n\nasync def execute(command: RunCommand): ...\n",
    )

    _validate_runtime_is_narrow(tmp_path)


def test_runtime_rejects_its_own_repository(tmp_path: Path) -> None:
    _write(tmp_path, "app/runtime/repositories.py", "class RuntimeRepository: ...\n")

    with pytest.raises(BoundaryViolation, match="runtime must not own persistence"):
        _validate_runtime_is_narrow(tmp_path)


def test_runtime_rejects_its_own_orm_table(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "app/runtime/state.py",
        "class RuntimeState:\n    __tablename__ = 'runtime_state'\n",
    )

    with pytest.raises(BoundaryViolation, match="runtime must not own ORM tables"):
        _validate_runtime_is_narrow(tmp_path)
