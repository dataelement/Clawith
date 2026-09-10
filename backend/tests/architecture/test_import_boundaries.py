from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = BACKEND_ROOT / "app"
FIXTURE_ROOT = Path(__file__).with_name("fixtures") / "import_boundaries"
OWNER_CONTRACTS = BACKEND_ROOT / "rewrite" / "owner-contracts.json"
LEGACY_MODULES = {
    "app.api",
    "app.core",
    "app.dao",
    "app.models",
    "app.schemas",
    "app.services",
    "app.config",
    "app.database",
}
PRIVATE_OWNER_MODULES = {
    "adapters",
    "continuation",
    "contracts",
    "crypto",
    "execution",
    "files",
    "mcp",
    "model",
    "models",
    "repository",
    "repositories",
    "skills",
}
METADATA_FACTORIES = {
    "sqlalchemy.MetaData",
    "sqlalchemy.orm.registry",
    "sqlalchemy.orm.declarative_base",
}
OBJECT_STORAGE_ROOT = "app.infrastructure.object_storage"
OBJECT_STORAGE_PUBLIC_CONTRACT = f"{OBJECT_STORAGE_ROOT}.base"


class FixtureCase(TypedDict):
    id: str
    path: str
    source: str


@dataclass(frozen=True, slots=True)
class Violation:
    rule: str
    path: Path
    detail: str


def _fixture_cases(name: str) -> list[FixtureCase]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _materialize_case(root: Path, case: FixtureCase) -> Path:
    path = root / case["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(case["source"], encoding="utf-8")
    return root / "app"


def _canonical_owner_ids() -> frozenset[str]:
    manifest = json.loads(OWNER_CONTRACTS.read_text(encoding="utf-8"))
    return frozenset(row["owner_id"] for row in manifest["owners"])


def _target_files(app_root: Path) -> list[Path]:
    files = [
        path
        for path in (
            app_root / "__init__.py",
            app_root / "application.py",
            app_root / "main.py",
        )
        if path.is_file()
    ]
    for directory in ("infrastructure", "modules", "runtime", "execution_dependencies", "api/product_inputs"):
        root = app_root / directory
        if root.is_dir():
            files.extend(root.rglob("*.py"))
    return sorted(set(files))


def _module_parts(path: Path, app_root: Path) -> list[str]:
    relative = path.relative_to(app_root).with_suffix("")
    parts = ["app", *relative.parts]
    if parts[-1] == "__init__":
        parts.pop()
    return parts


def _resolve_from_module(node: ast.ImportFrom, path: Path, app_root: Path) -> str:
    if node.level == 0:
        return node.module or ""
    module = _module_parts(path, app_root)
    package = module if path.name == "__init__.py" else module[:-1]
    retained = package[: len(package) - (node.level - 1)]
    if node.module:
        retained.extend(node.module.split("."))
    return ".".join(retained)


def _aliases_and_imports(
    tree: ast.Module,
    path: Path,
    app_root: Path,
) -> tuple[dict[str, str], set[str]]:
    aliases: dict[str, str] = {}
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                imports.add(imported.name)
                bound_name = imported.asname or imported.name.split(".")[0]
                aliases[bound_name] = imported.name if imported.asname else bound_name
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_from_module(node, path, app_root)
            if module:
                imports.add(module)
            for imported in node.names:
                if imported.name == "*":
                    continue
                qualified = f"{module}.{imported.name}" if module else imported.name
                imports.add(qualified)
                aliases[imported.asname or imported.name] = qualified
    return aliases, imports


def _qualified_name(node: ast.expr, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _expand_assigned_aliases(tree: ast.Module, aliases: dict[str, str]) -> None:
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)]
    for _ in range(len(assignments)):
        changed = False
        for node in assignments:
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            target = node.targets[0]
            qualified = _qualified_name(node.value, aliases)
            if qualified and aliases.get(target.id) != qualified:
                aliases[target.id] = qualified
                changed = True
        if not changed:
            return


def _is_module_or_child(imported: str, module: str) -> bool:
    return imported == module or imported.startswith(f"{module}.")


def _snake_case(identifier: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", identifier).lower()


def _matches_owner_fact(identifier: str, product_owners: frozenset[str]) -> bool:
    normalized = _snake_case(identifier)
    if any(
        normalized == owner or normalized.startswith(f"{owner}_")
        for owner in product_owners
    ):
        return True
    collapsed = normalized.replace("_", "")
    return any(
        any(character.isdigit() for character in owner) and collapsed.startswith(owner)
        for owner in product_owners
    )


def _scan_target_tree(
    app_root: Path,
    *,
    require_canonical_singletons: bool = False,
) -> list[Violation]:
    violations: list[Violation] = []
    owner_ids = _canonical_owner_ids()
    product_owners = owner_ids - {"run"}
    application_factories: list[Path] = []
    metadata_registries: list[Path] = []

    for path in _target_files(app_root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases, imports = _aliases_and_imports(tree, path, app_root)
        _expand_assigned_aliases(tree, aliases)
        relative = path.relative_to(app_root)
        relative_parts = relative.parts

        for imported in imports:
            if any(_is_module_or_child(imported, legacy) for legacy in LEGACY_MODULES):
                product_transport = _is_module_or_child(imported, "app.api.product_inputs") and (
                    relative == Path("application.py") or relative.parts[:2] == ("api", "product_inputs"))
                if not product_transport:
                    violations.append(Violation("legacy-import", relative, imported))

        object_storage_imports = {
            imported
            for imported in imports
            if _is_module_or_child(imported, OBJECT_STORAGE_ROOT)
        }
        if object_storage_imports:
            imports_concrete_storage = any(
                not _is_module_or_child(imported, OBJECT_STORAGE_PUBLIC_CONTRACT)
                for imported in object_storage_imports
            )
            infrastructure_or_composition = (
                relative == Path("application.py")
                or relative == Path("execution_dependencies/resources.py")
                or relative_parts[0] == "infrastructure"
            )
            workspace_public_contract = (
                len(relative_parts) >= 2
                and relative_parts[:2] == ("modules", "workspace")
                and not imports_concrete_storage
            )
            if not (infrastructure_or_composition or workspace_public_contract):
                violations.extend(
                    Violation("object-storage-bypass", relative, imported)
                    for imported in sorted(object_storage_imports)
                )

        if relative_parts[0] == "execution_dependencies" or relative_parts[:2] == ("api", "product_inputs") or (
            len(relative_parts) >= 3 and relative_parts[0] == "modules"
        ):
            importing_owner = relative_parts[1] if relative_parts[0] == "modules" else None
            for imported in imports:
                parts = imported.split(".")
                if len(parts) < 4 or parts[:2] != ["app", "modules"]:
                    continue
                imported_owner = parts[2]
                if (
                    imported_owner in owner_ids
                    and imported_owner != importing_owner
                    and parts[3] in PRIVATE_OWNER_MODULES
                ):
                    violations.append(
                        Violation("cross-owner-private-import", relative, imported)
                    )

        if relative_parts[0] in {"modules", "runtime", "infrastructure"}:
            for imported in imports:
                if _is_module_or_child(imported, "app.execution_dependencies"):
                    violations.append(Violation("composition-reverse-import", relative, imported))

        if relative_parts and relative_parts[0] == "runtime":
            for imported in imports:
                parts = imported.split(".")
                if (
                    len(parts) >= 3
                    and parts[:2] == ["app", "modules"]
                    and parts[2] in product_owners
                ):
                    violations.append(Violation("runtime-product-fact", relative, imported))
            if any(
                _matches_owner_fact(part.removesuffix(".py"), product_owners)
                for part in relative_parts[1:]
            ) or any(
                part.removesuffix(".py") in PRIVATE_OWNER_MODULES
                for part in relative_parts[1:]
            ):
                violations.append(Violation("runtime-product-fact", relative, "module path"))
            for node in tree.body:
                names: list[str] = []
                if isinstance(node, ast.ClassDef):
                    names.append(node.name)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    names.extend(target.id for target in targets if isinstance(target, ast.Name))
                if any(_matches_owner_fact(name, product_owners) for name in names):
                    violations.append(
                        Violation("runtime-product-fact", relative, ", ".join(names))
                    )
            if any(
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == "__tablename__"
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                )
                for node in ast.walk(tree)
            ):
                violations.append(Violation("runtime-product-fact", relative, "ORM table"))

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called = _qualified_name(node.func, aliases)
                if called == "fastapi.FastAPI":
                    application_factories.append(relative)
                if called in METADATA_FACTORIES:
                    metadata_registries.append(relative)
            elif isinstance(node, ast.ClassDef) and any(
                _qualified_name(base, aliases) == "sqlalchemy.orm.DeclarativeBase"
                for base in node.bases
            ):
                metadata_registries.append(relative)

    expected_application = Path("application.py")
    expected_metadata = Path("infrastructure/database.py")
    if require_canonical_singletons:
        if application_factories != [expected_application]:
            violations.append(
                Violation(
                    "application-factory",
                    expected_application,
                    f"expected one canonical factory, found {application_factories}",
                )
            )
        if metadata_registries != [expected_metadata]:
            violations.append(
                Violation(
                    "metadata-registry",
                    expected_metadata,
                    f"expected one canonical registry, found {metadata_registries}",
                )
            )
    else:
        violations.extend(
            Violation("application-factory", path, "FastAPI constructor")
            for path in application_factories
        )
        violations.extend(
            Violation("metadata-registry", path, "SQLAlchemy registry constructor")
            for path in metadata_registries
        )
    return violations


def _violation_rules(app_root: Path, *, require_canonical_singletons: bool = False) -> set[str]:
    return {
        violation.rule
        for violation in _scan_target_tree(
            app_root,
            require_canonical_singletons=require_canonical_singletons,
        )
    }


def test_current_target_tree_satisfies_import_boundaries() -> None:
    violations = _scan_target_tree(APP_ROOT, require_canonical_singletons=True)

    assert violations == []


@pytest.mark.parametrize("path,source,allowed", [
    ("app/application.py", "from app.api.product_inputs.sessions import router", True),
    ("app/api/product_inputs/sessions.py", "from app.modules.session.public import SessionService", True),
    ("app/api/product_inputs/groups.py", "from app.api.product_inputs.auth import authenticated", True),
    ("app/modules/run/service.py", "from app.api.product_inputs.sessions import router", False),
    ("app/execution_dependencies/bridge.py", "from app.api.product_inputs.sessions import router", False),
    ("app/application.py", "from app.api.auth import router", False),
    ("app/api/product_inputs/sessions.py", "from app.modules.session.models import SessionRecord", False),
    ("app/api/product_inputs/sessions.py", "from app.services.agent_service import AgentService", False),
])
def test_product_transport_keeps_public_owner_direction(tmp_path: Path, path: str, source: str, allowed: bool) -> None:
    app_root = _materialize_case(tmp_path, {"id": "product-api", "path": path, "source": source + "\n"})
    assert (not _violation_rules(app_root)) is allowed


@pytest.mark.parametrize("case", _fixture_cases("allowed.json"), ids=lambda case: case["id"])
def test_typed_public_contracts_and_infrastructure_imports_are_allowed(
    tmp_path: Path,
    case: FixtureCase,
) -> None:
    app_root = _materialize_case(tmp_path, case)

    assert _violation_rules(app_root) == set()


@pytest.mark.parametrize(
    "case",
    [
        {
            "id": "application_local_backend",
            "path": "app/application.py",
            "source": (
                "from app.infrastructure.object_storage.local "
                "import LocalStorageBackend\n"
            ),
        },
        {
            "id": "infrastructure_s3_backend",
            "path": "app/infrastructure/storage_factory.py",
            "source": (
                "from app.infrastructure.object_storage.s3 "
                "import S3StorageBackend\n"
            ),
        },
        {
            "id": "workspace_storage_contract",
            "path": "app/modules/workspace/service.py",
            "source": (
                "from app.infrastructure.object_storage.base import StorageBackend\n"
            ),
        },
        {
            "id": "workspace_package_storage_contract",
            "path": "app/modules/workspace/__init__.py",
            "source": (
                "from app.infrastructure.object_storage.base import StorageBackend\n"
            ),
        },
    ],
    ids=lambda case: case["id"],
)
def test_approved_object_storage_imports_are_allowed(
    tmp_path: Path,
    case: FixtureCase,
) -> None:
    app_root = _materialize_case(tmp_path, case)

    assert "object-storage-bypass" not in _violation_rules(app_root)


@pytest.mark.parametrize(
    "case",
    [
        {
            "id": "workspace_concrete_backend",
            "path": "app/modules/workspace/service.py",
            "source": (
                "from app.infrastructure.object_storage.local "
                "import LocalStorageBackend\n"
            ),
        },
        {
            "id": "other_owner_storage_contract",
            "path": "app/modules/session/service.py",
            "source": (
                "from app.infrastructure.object_storage.base import StorageBackend\n"
            ),
        },
        {
            "id": "runtime_storage_contract",
            "path": "app/runtime/runner.py",
            "source": (
                "from app.infrastructure.object_storage.base import StorageBackend\n"
            ),
        },
        {
            "id": "runtime_concrete_backend",
            "path": "app/runtime/runner.py",
            "source": (
                "from app.infrastructure.object_storage.s3 import S3StorageBackend\n"
            ),
        },
    ],
    ids=lambda case: case["id"],
)
def test_unapproved_object_storage_imports_are_rejected(
    tmp_path: Path,
    case: FixtureCase,
) -> None:
    app_root = _materialize_case(tmp_path, case)

    assert "object-storage-bypass" in _violation_rules(app_root)


@pytest.mark.parametrize("case", _fixture_cases("legacy_imports.json"), ids=lambda case: case["id"])
def test_target_tree_rejects_legacy_authority_imports(
    tmp_path: Path,
    case: FixtureCase,
) -> None:
    app_root = _materialize_case(tmp_path, case)

    assert "legacy-import" in _violation_rules(app_root)


@pytest.mark.parametrize("case", _fixture_cases("private_imports.json"), ids=lambda case: case["id"])
def test_owner_rejects_another_owners_private_persistence_imports(
    tmp_path: Path,
    case: FixtureCase,
) -> None:
    app_root = _materialize_case(tmp_path, case)

    assert "cross-owner-private-import" in _violation_rules(app_root)


@pytest.mark.parametrize("private_module", sorted(PRIVATE_OWNER_MODULES))
@pytest.mark.parametrize("same_owner", [False, True])
def test_execution_implementation_modules_remain_owner_private(
    tmp_path: Path, private_module: str, same_owner: bool
) -> None:
    imported_owner = "tool" if same_owner else "model"
    app_root = _materialize_case(tmp_path, {
        "id": "execution-private-boundary",
        "path": "app/modules/tool/public.py",
        "source": f"from app.modules.{imported_owner}.{private_module} import Implementation\n",
    })
    violations = _violation_rules(app_root)
    assert ("cross-owner-private-import" in violations) is not same_owner


@pytest.mark.parametrize("surface", ["public", "models", "repository", "contracts", "execution"])
def test_execution_composition_consumes_only_public_owner_contracts(tmp_path: Path, surface: str) -> None:
    app_root = _materialize_case(tmp_path, {
        "id": "execution-composition",
        "path": "app/execution_dependencies/workspace_tools.py",
        "source": f"from app.modules.tool.{surface} import Contract\n",
    })
    assert ("cross-owner-private-import" in _violation_rules(app_root)) is (surface != "public")


@pytest.mark.parametrize("filename,allowed", [("resources.py", True), ("workspace_tools.py", False)])
def test_only_resource_composition_may_construct_storage(tmp_path: Path, filename: str, allowed: bool) -> None:
    app_root = _materialize_case(tmp_path, {
        "id": "storage-composition-boundary",
        "path": f"app/execution_dependencies/{filename}",
        "source": "from app.infrastructure.object_storage.s3 import S3StorageBackend\n",
    })
    assert ("object-storage-bypass" in _violation_rules(app_root)) is not allowed


@pytest.mark.parametrize("path", ["modules/tool/execution.py", "runtime/loop.py", "infrastructure/config.py"])
def test_owners_cannot_depend_on_execution_composition(tmp_path: Path, path: str) -> None:
    app_root = _materialize_case(tmp_path, {
        "id": "composition-reverse",
        "path": f"app/{path}",
        "source": "from app.execution_dependencies.workspace_tools import workspace_bindings\n",
    })
    assert "composition-reverse-import" in _violation_rules(app_root)


@pytest.mark.parametrize("case", _fixture_cases("runtime_facts.json"), ids=lambda case: case["id"])
def test_runtime_rejects_product_fact_ownership_and_imports(
    tmp_path: Path,
    case: FixtureCase,
) -> None:
    app_root = _materialize_case(tmp_path, case)

    assert "runtime-product-fact" in _violation_rules(app_root)


@pytest.mark.parametrize("case", _fixture_cases("factories.json"), ids=lambda case: case["id"])
def test_target_tree_rejects_additional_fastapi_factories_including_aliases(
    tmp_path: Path,
    case: FixtureCase,
) -> None:
    app_root = _materialize_case(tmp_path, case)

    assert "application-factory" in _violation_rules(app_root)


@pytest.mark.parametrize("case", _fixture_cases("metadata.json"), ids=lambda case: case["id"])
def test_target_tree_rejects_additional_sqlalchemy_registries_including_aliases(
    tmp_path: Path,
    case: FixtureCase,
) -> None:
    app_root = _materialize_case(tmp_path, case)

    assert "metadata-registry" in _violation_rules(app_root)
