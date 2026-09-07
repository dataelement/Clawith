from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BACKEND_RULES = BACKEND_ROOT / "AGENTS.md"
ALEMBIC_RULES = BACKEND_ROOT / "alembic/AGENTS.md"
DAG_PATH = BACKEND_ROOT / "rewrite/owner-dag.json"

EXPECTED_WAVES = {
    "S0": ["identity_tenant"],
    "S1": ["agent", "credential", "model", "audit", "run", "permission", "context", "auth"],
    "S2": [
        "workspace",
        "tool",
        "capability_market",
        "session",
        "a2a",
        "group",
        "trigger",
        "heartbeat",
        "channel",
    ],
    "S3": [
        "sso",
        "organization",
        "invitation",
        "onboarding",
        "okr",
        "focus",
        "notification",
        "published_page",
        "plaza",
        "enterprise_settings",
        "platform_administration",
        "agentbay",
        "directory",
        "agent_template",
        "observability",
        "tenant_knowledge",
    ],
}
EXPECTED_DEPENDENCIES = {
    "identity_tenant": [],
    "credential": ["identity_tenant"],
    "model": ["identity_tenant", "credential"],
    "agent": ["identity_tenant", "credential", "model"],
    "permission": ["identity_tenant", "agent"],
    "audit": ["identity_tenant"],
    "workspace": ["identity_tenant", "agent", "permission", "audit"],
    "tool": ["identity_tenant", "credential", "agent", "permission", "audit"],
    "capability_market": ["identity_tenant", "credential", "agent", "permission", "audit", "workspace", "tool"],
    "run": [
        "identity_tenant",
        "agent",
        "model",
        "permission",
        "workspace",
        "tool",
        "capability_market",
        "audit",
    ],
    "context": ["run", "model", "permission", "workspace", "tool", "capability_market"],
    "session": ["run", "context"],
    "a2a": ["run", "context"],
    "group": ["run", "context"],
    "trigger": ["run", "context"],
    "heartbeat": ["run", "context"],
    "channel": ["run", "context", "credential"],
    "auth": ["identity_tenant", "permission"],
    "sso": ["auth", "identity_tenant", "credential"],
    "organization": ["identity_tenant", "permission"],
    "invitation": ["identity_tenant", "organization", "auth"],
    "onboarding": ["identity_tenant", "organization", "agent"],
    "okr": ["identity_tenant", "agent", "permission"],
    "focus": ["identity_tenant", "agent", "run"],
    "notification": ["identity_tenant", "permission"],
    "published_page": ["identity_tenant", "permission"],
    "plaza": ["identity_tenant", "agent", "permission"],
    "enterprise_settings": ["identity_tenant", "permission"],
    "platform_administration": ["identity_tenant", "permission", "audit"],
    "agentbay": ["identity_tenant", "agent", "credential", "permission"],
    "directory": ["identity_tenant", "organization", "permission"],
    "agent_template": ["identity_tenant", "agent", "capability_market", "permission"],
    "observability": ["identity_tenant", "run", "audit", "permission"],
    "tenant_knowledge": ["identity_tenant", "context", "workspace", "permission"],
}


class GovernanceViolation(AssertionError):
    pass


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _wave_table(text: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for wave, owners in re.findall(r"^\| (S[0-3]) \| `([^\n]+)` \|$", text, flags=re.MULTILINE):
        rows[wave] = [part.strip(" `") for part in owners.split(",")]
    return rows


def _validate_owner_dag(dag: dict[str, object], waves: dict[str, list[str]]) -> None:
    owners = dag.get("owners")
    if not isinstance(owners, list):
        raise GovernanceViolation("DAG owners must be a list")
    owner_ids = [row.get("owner_id") for row in owners if isinstance(row, dict)]
    expected_ids = [owner for wave in EXPECTED_WAVES.values() for owner in wave]
    duplicates = [owner for owner, count in Counter(owner_ids).items() if count > 1]
    if duplicates:
        raise GovernanceViolation(f"duplicate DAG owners: {duplicates}")
    if set(owner_ids) != set(expected_ids):
        raise GovernanceViolation("DAG owner roster differs from the exact governance roster")
    if waves != EXPECTED_WAVES:
        raise GovernanceViolation("schema waves differ from the exact governance roster")

    dependencies: dict[str, list[str]] = {}
    for row in owners:
        assert isinstance(row, dict)
        owner_id = row["owner_id"]
        schema_wave = row["schema_wave"]
        if owner_id not in waves[schema_wave]:
            raise GovernanceViolation(f"{owner_id} has the wrong schema wave")
        dependencies[owner_id] = row["depends_on"]
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(owner_id: str) -> None:
        if owner_id in visiting:
            raise GovernanceViolation("public owner DAG contains a dependency cycle")
        if owner_id in visited:
            return
        visiting.add(owner_id)
        for dependency in dependencies[owner_id]:
            if dependency not in dependencies:
                raise GovernanceViolation(f"unknown DAG dependency: {dependency}")
            visit(dependency)
        visiting.remove(owner_id)
        visited.add(owner_id)

    for owner_id in dependencies:
        visit(owner_id)
    if dependencies != EXPECTED_DEPENDENCIES:
        raise GovernanceViolation("public owner DAG differs from the exact governance DAG")


def _validate_ledger_separation(root: Path, governance: str) -> None:
    contracts = {
        "coverage.json": ("entries", "`rewrite/coverage.json` records old endpoint and lifecycle disposition"),
        "owner-contracts.json": (
            "owners",
            "`rewrite/owner-contracts.json` is the sole readiness authority for every target owner",
        ),
        "product-contracts.json": ("modules", "`rewrite/product-contracts.json` records S3 product decisions"),
    }
    authority_roots = {root_key for root_key, _ in contracts.values()}
    for filename, (root_key, authority_clause) in contracts.items():
        data = json.loads((root / filename).read_text(encoding="utf-8"))
        if root_key not in data:
            raise GovernanceViolation(f"{filename} must own {root_key}")
        if (authority_roots - {root_key}).intersection(data):
            raise GovernanceViolation("rewrite ledgers must not share authority roots")
        if authority_clause not in governance:
            raise GovernanceViolation(f"missing ledger authority clause: {filename}")
    if "it does not replace that row" not in governance:
        raise GovernanceViolation("product decisions must not replace owner readiness")


def _validate_branch_decision(governance: str) -> None:
    if "The clean-break rewrite is implemented directly on `develop`." not in governance:
        raise GovernanceViolation("clean-break branch decision must name develop directly")


def _validate_g002_alembic_policy(alembic_governance: str) -> None:
    clauses = (
        "G002 has no target schema baseline",
        "Only read-only structural inspection is supported",
        "`uv run alembic heads`",
        "`uv run alembic history`",
        "All other Alembic CLI and programmatic execution fail before database connection or mutation",
        "G008 owns the reviewed one-time target baseline replacement",
    )
    missing = [clause for clause in clauses if clause not in alembic_governance]
    if missing:
        raise GovernanceViolation(f"missing G002 Alembic policy clause: {missing[0]}")


def _revision(source: str) -> tuple[str, str | None]:
    values: dict[str, str | None] = {}
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                values[target.id] = ast.literal_eval(value)
    revision = values.get("revision")
    if revision is None:
        raise GovernanceViolation("migration revision must be a string")
    return revision, values.get("down_revision")


def _validate_forward_only_single_head(revisions: list[str]) -> None:
    parsed = [_revision(source) for source in revisions]
    revision_ids = [revision for revision, _ in parsed]
    if len(revision_ids) != len(set(revision_ids)):
        raise GovernanceViolation("migration revision IDs must be unique")
    roots = [revision for revision, parent in parsed if parent is None]
    if len(roots) != 1:
        raise GovernanceViolation("migration history must have one baseline")
    known = set(revision_ids)
    referenced = {parent for _, parent in parsed if parent is not None}
    if not referenced.issubset(known):
        raise GovernanceViolation("forward migration references an unknown revision")
    heads = known - referenced
    if len(heads) != 1:
        raise GovernanceViolation("migration history must have exactly one head")


def _validate_startup_source(source: str) -> None:
    forbidden_tokens = ("create_all", "repair", "translate_old", "legacy", "compat")
    tree = ast.parse(source)
    startup_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (node.name in {"lifespan", "startup"} or node.decorator_list)
    ]
    for node in startup_nodes:
        for descendant in ast.walk(node):
            names: list[str] = []
            if isinstance(descendant, ast.Name):
                names.append(descendant.id)
            elif isinstance(descendant, ast.Attribute):
                names.append(descendant.attr)
            elif isinstance(descendant, ast.Constant) and isinstance(descendant.value, str):
                names.append(descendant.value)
            normalized = " ".join(names).lower()
            if any(token in normalized for token in forbidden_tokens):
                raise GovernanceViolation(f"startup contains prohibited operation: {normalized}")


def test_owner_dag_matches_the_exact_backend_schema_waves() -> None:
    dag = json.loads(_text(DAG_PATH))

    _validate_owner_dag(dag, _wave_table(_text(BACKEND_RULES)))


def test_backend_defines_schema_waves_while_frozen_alembic_rules_do_not() -> None:
    assert _wave_table(_text(BACKEND_RULES)) == EXPECTED_WAVES
    assert _wave_table(_text(ALEMBIC_RULES)) == {}


def test_owner_dag_rejects_a_schema_wave_drift_fixture() -> None:
    dag = json.loads(_text(DAG_PATH))
    dag["owners"][0]["schema_wave"] = "S1"

    with pytest.raises(GovernanceViolation, match="wrong schema wave"):
        _validate_owner_dag(dag, EXPECTED_WAVES)


def test_owner_dag_rejects_a_cycle_fixture() -> None:
    dag = json.loads(_text(DAG_PATH))
    dag["owners"][0]["depends_on"] = ["auth"]

    with pytest.raises(GovernanceViolation, match="dependency cycle"):
        _validate_owner_dag(dag, EXPECTED_WAVES)


def test_owner_dag_rejects_dependency_drift_fixture() -> None:
    dag = json.loads(_text(DAG_PATH))
    dag["owners"][1]["depends_on"] = []

    with pytest.raises(GovernanceViolation, match="exact governance DAG"):
        _validate_owner_dag(dag, EXPECTED_WAVES)


@pytest.mark.parametrize("owner,dependency", [
    ("workspace", "audit"), ("tool", "audit"),
    ("capability_market", "audit"), ("capability_market", "workspace"),
])
def test_g004_dag_requires_public_installation_and_observation_dependencies(owner: str, dependency: str) -> None:
    dag = json.loads(_text(DAG_PATH))
    row = next(row for row in dag["owners"] if row["owner_id"] == owner)
    row["depends_on"].remove(dependency)
    with pytest.raises(GovernanceViolation, match="exact governance DAG"):
        _validate_owner_dag(dag, EXPECTED_WAVES)


def test_backend_governance_defines_one_modular_application_and_metadata_registry() -> None:
    governance = _text(BACKEND_RULES)

    assert "one final-form application factory and one SQLAlchemy `Base`/metadata registry" in governance
    assert "The target is one modular monolith under `app/modules/<owner>/`" in governance


def test_backend_governance_keeps_owner_models_and_repositories_private() -> None:
    governance = _text(BACKEND_RULES)

    assert "Every owner keeps its ORM models and repositories private." in governance
    assert "Another owner may use only its typed public service contract" in governance


def test_backend_governance_keeps_runtime_as_run_owned_mechanics() -> None:
    governance = _text(BACKEND_RULES)

    assert "`run` owns Run persistence and Runner/Loop mechanics" in governance
    assert "`app/runtime/` is only its implementation package" in governance
    assert "neither `runtime` nor `goal` is an owner" in governance


def test_three_rewrite_ledgers_keep_separate_authority() -> None:
    _validate_ledger_separation(BACKEND_ROOT / "rewrite", _text(BACKEND_RULES))


def test_ledger_separation_rejects_a_shared_authority_root(tmp_path: Path) -> None:
    rewrite = tmp_path / "rewrite"
    rewrite.mkdir()
    (rewrite / "coverage.json").write_text('{"entries": []}', encoding="utf-8")
    (rewrite / "owner-contracts.json").write_text('{"owners": [], "entries": []}', encoding="utf-8")
    (rewrite / "product-contracts.json").write_text('{"modules": []}', encoding="utf-8")

    with pytest.raises(GovernanceViolation, match="must not share authority roots"):
        _validate_ledger_separation(rewrite, _text(BACKEND_RULES))


def test_clean_break_governance_selects_develop_directly() -> None:
    _validate_branch_decision(_text(BACKEND_RULES))


def test_clean_break_governance_rejects_an_indirect_branch_fixture() -> None:
    governance = _text(BACKEND_RULES).replace(
        "implemented directly on `develop`", "implemented on a temporary rewrite branch"
    )

    with pytest.raises(GovernanceViolation, match="must name develop directly"):
        _validate_branch_decision(governance)


def test_alembic_governance_quarantines_execution_until_g008() -> None:
    _validate_g002_alembic_policy(_text(ALEMBIC_RULES))


def test_alembic_policy_rejects_an_executable_g002_fixture() -> None:
    governance = _text(ALEMBIC_RULES).replace(
        "All other Alembic CLI and programmatic execution fail before database connection or mutation",
        "Upgrade commands may connect to the legacy database",
    )

    with pytest.raises(GovernanceViolation, match="missing G002 Alembic policy clause"):
        _validate_g002_alembic_policy(governance)


def test_forward_migration_fixture_has_one_baseline_and_one_head() -> None:
    revisions = [
        'revision = "baseline"\ndown_revision = None\n',
        'revision = "add_agent"\ndown_revision = "baseline"\n',
    ]

    _validate_forward_only_single_head(revisions)


def test_migration_fixture_rejects_multiple_heads() -> None:
    revisions = [
        'revision = "baseline"\ndown_revision = None\n',
        'revision = "add_agent"\ndown_revision = "baseline"\n',
        'revision = "add_tool"\ndown_revision = "baseline"\n',
    ]

    with pytest.raises(GovernanceViolation, match="exactly one head"):
        _validate_forward_only_single_head(revisions)


def test_startup_fixture_accepts_composition_without_schema_or_compatibility_work() -> None:
    source = "async def lifespan(app):\n    await start_workers()\n    yield\n"

    _validate_startup_source(source)


@pytest.mark.parametrize(
    "operation",
    [
        "await connection.run_sync(Base.metadata.create_all)",
        "await repair_missing_rows()",
        "await translate_old_state()",
        "await enable_legacy_adapter()",
        "await activate_compat_routes()",
    ],
)
def test_startup_fixture_rejects_schema_repair_or_compatibility_work(operation: str) -> None:
    source = f"async def lifespan(app):\n    {operation}\n    yield\n"

    with pytest.raises(GovernanceViolation, match="startup contains prohibited operation"):
        _validate_startup_source(source)


def test_backend_governance_explicitly_prohibits_startup_mutation_and_compatibility() -> None:
    governance = _text(BACKEND_RULES)

    assert (
        "Startup must never call `create_all`, mutate the schema, repair data, translate old state, "
        "or activate compatibility paths."
    ) in governance
    assert "Do not add legacy imports, dual reads or writes, old-schema adapters, startup repair" in governance
