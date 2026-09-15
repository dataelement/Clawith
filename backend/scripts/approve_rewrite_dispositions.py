"""Approve Phase 0 endpoint and lifecycle dispositions from accepted source maps.

This command is intentionally narrower than ``rewrite_inventory.py``. It does
not discover routes or advance owner contracts. It classifies the frozen
coverage rows, writes row-specific audit evidence, and performs only the legal
``unreviewed -> disposition_approved`` transition.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
DEFAULT_MANIFEST = BACKEND_ROOT / "rewrite/coverage.json"
DEFAULT_EVIDENCE = BACKEND_ROOT / "rewrite/disposition-evidence/endpoint-lifecycle-dispositions.json"
SOURCE_DISPOSITION_NOTE = ".agents/notes/proposed/simplification/2026-09-01-clean-break-backend-source-disposition.md"
COVERAGE_MATRIX = "backend/rewrite/backend-capability-coverage-matrix.md"


@dataclass(frozen=True)
class Decision:
    disposition: str
    owner: str | None
    rationale: str


DELETE_MODULES = {"experience", "gateway", "tasks"}
REUSE_CHANNEL_MODULES = {
    "atlassian",
    "dingtalk",
    "discord_bot",
    "feishu",
    "slack",
    "teams",
    "wechat",
    "wecom",
}
DEFER_MODULE_OWNERS = {
    "admin": "platform_administration",
    "directory": "directory",
    "focus": "focus",
    "notification": "notification",
    "okr": "okr",
    "onboarding": "onboarding",
    "organization": "organization",
    "pages": "published_page",
    "plaza": "plaza",
    "sso": "sso",
}
REWRITE_MODULE_OWNERS = {
    "activity": "observability",
    "agent_credentials": "credential",
    "auth": "auth",
    "chat_sessions": "session",
    "group_websocket": "group",
    "groups": "group",
    "messages": "notification",
    "schedules": "trigger",
    "tenants": "identity_tenant",
    "triggers": "trigger",
    "upload": "session",
    "webhooks": "trigger",
    "websocket": "session",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _authority_record(path: str, *, repo_root: Path = REPO_ROOT) -> dict[str, str]:
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"authority path must stay inside the repository: {path}")
    authority = repo_root / relative
    if not authority.is_file():
        raise ValueError(f"authority path is not a file: {path}")
    ignored = subprocess.run(
        ["git", "-C", str(repo_root), "check-ignore", "--quiet", "--no-index", "--", path],
        check=False,
    )
    if ignored.returncode == 0:
        raise ValueError(f"authority path is ignored: {path}")
    if ignored.returncode != 1:
        raise ValueError(f"cannot determine whether authority path is ignored: {path}")
    tracked = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--error-unmatch", "--", path],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if tracked.returncode != 0:
        raise ValueError(f"authority path is not Git-tracked: {path}")
    return {"path": path, "sha256": _sha256(authority)}


def _authority_records() -> list[dict[str, str]]:
    return [
        _authority_record(SOURCE_DISPOSITION_NOTE),
        _authority_record(COVERAGE_MATRIX),
    ]


def _module_and_symbol(row: dict[str, Any]) -> tuple[str, str]:
    source_path, symbol = row["source"].split(":", 1)
    return Path(source_path).stem, symbol


def classify(row: dict[str, Any]) -> Decision:
    """Return the accepted disposition for one frozen coverage row."""
    module, symbol = _module_and_symbol(row)

    if row["kind"] != "http" and row["kind"] != "websocket":
        return _classify_lifecycle(row["id"])
    if module in DELETE_MODULES:
        return Decision("delete", None, "The accepted source-disposition Note removes this legacy authority.")
    if module in REUSE_CHANNEL_MODULES:
        return Decision(
            "reuse_rewrite",
            "channel",
            "The accepted matrix retains protocol mechanics behind the target Channel owner.",
        )
    if module == "agentbay_control":
        return Decision(
            "reuse_rewrite",
            "agentbay",
            "The accepted matrix retains bounded AgentBay control mechanics behind the AgentBay owner.",
        )
    if module == "google_workspace":
        return Decision(
            "reuse_rewrite",
            "organization",
            "The accepted matrix retains Google Workspace OAuth and sync mechanics behind Organization.",
        )
    if module in DEFER_MODULE_OWNERS:
        return Decision(
            "defer_rewrite",
            DEFER_MODULE_OWNERS[module],
            "The source-disposition Note preserves this product capability for its later owner slice.",
        )
    if module in REWRITE_MODULE_OWNERS:
        disposition = (
            "defer_rewrite"
            if module in {"groups", "group_websocket", "messages", "schedules", "triggers", "webhooks"}
            else "rewrite"
        )
        return Decision(
            disposition,
            REWRITE_MODULE_OWNERS[module],
            "The accepted matrix assigns this surface to the named target owner.",
        )
    if module == "agents":
        if symbol in {
            "generate_or_reset_api_key",
            "list_agent_approvals",
            "list_gateway_messages",
            "resolve_agent_approval",
            "start_agent",
            "stop_agent",
        }:
            return Decision(
                "delete", None, "Agent API keys, approvals, Gateway state, and Agent start/stop lifecycle are removed."
            )
        if symbol in {"get_agent_permission_candidates", "get_agent_permissions", "update_agent_permissions"}:
            return Decision(
                "rewrite", "permission", "Explicit Agent visibility assignment is rewritten under Permission."
            )
        if symbol == "list_templates":
            return Decision(
                "defer_rewrite",
                "agent_template",
                "Agent template presentation is preserved for the Agent Template slice.",
            )
        return Decision(
            "rewrite", "agent", "The narrow target Agent owner replaces legacy Agent identity and configuration."
        )
    if module == "advanced":
        if symbol == "handover_agent":
            return Decision("delete", None, "Changing Agent creator identity is explicitly removed.")
        if symbol in {"delegate_task", "list_collaborators", "send_inter_agent_message"}:
            return Decision(
                "rewrite", "a2a", "Cross-Agent collaboration is rewritten as A2A delivery and Child Run behavior."
            )
        if symbol == "get_agent_metrics":
            return Decision(
                "defer_rewrite", "observability", "Agent metrics are preserved for the Observability slice."
            )
        return Decision("defer_rewrite", "agent_template", "Template APIs are preserved for the Agent Template slice.")
    if module == "enterprise":
        return _classify_enterprise(symbol)
    if module == "files":
        return _classify_files(symbol)
    if module == "relationships":
        return Decision(
            "delete", None, "Legacy relationship labels, rows, Memory metadata, and compatibility routes are removed."
        )
    if module == "skills":
        return _classify_skills(symbol)
    if module == "tools":
        return Decision(
            "rewrite", "tool", "Tool registry, definition, grant, connection, and execution surfaces move to Tool."
        )
    if module == "users":
        if symbol == "update_user_quota":
            return Decision("delete", None, "Legacy quota enforcement is explicitly removed.")
        return Decision("rewrite", "identity_tenant", "User identity and Tenant membership move to Identity/Tenant.")
    if module == "main" and symbol in {"get_version", "health_check"}:
        return Decision(
            "defer_rewrite", "observability", "Version and health projection move to the target Observability surface."
        )
    raise ValueError(f"unclassified coverage row: {row['id']} ({row['source']})")


def _classify_enterprise(symbol: str) -> Decision:
    if symbol in {"list_approvals", "resolve_approval", "get_tenant_quotas", "update_tenant_quotas"}:
        return Decision("delete", None, "Legacy approvals and quota enforcement are explicitly removed.")
    if symbol == "list_audit_logs":
        return Decision("rewrite", "audit", "Audit queries move to the closed target Audit contract.")
    if "llm" in symbol or symbol == "get_runtime_model_settings" or symbol == "update_runtime_model_settings":
        return Decision("rewrite", "model", "LLM and runtime Model policy surfaces move to Model.")
    if "identity_provider" in symbol or "oauth2_provider" in symbol:
        return Decision("defer_rewrite", "sso", "Identity-provider administration is preserved for SSO.")
    if "invitation" in symbol or symbol in {"check_email_exists", "invite_users"}:
        return Decision(
            "defer_rewrite", "invitation", "Invitation and invitee validation are preserved for Invitation."
        )
    if symbol in {
        "list_org_departments",
        "list_org_members",
        "trigger_org_sync",
        "wecom_callback_verify_universal",
        "wecom_org_sync_verify",
    }:
        return Decision(
            "defer_rewrite", "organization", "Organization directory synchronization is preserved for Organization."
        )
    if symbol in {"list_enterprise_info", "update_enterprise_info"}:
        return Decision(
            "defer_rewrite",
            "tenant_knowledge",
            "Enterprise information remains explicitly deferred to Tenant Knowledge.",
        )
    if symbol == "get_enterprise_stats":
        return Decision("defer_rewrite", "observability", "Enterprise metrics are preserved for Observability.")
    return Decision(
        "defer_rewrite",
        "enterprise_settings",
        "Tenant email and system settings are preserved for Enterprise Settings.",
    )


def _classify_files(symbol: str) -> Decision:
    if "enterprise" in symbol:
        return Decision(
            "defer_rewrite",
            "tenant_knowledge",
            "Enterprise knowledge files remain explicitly deferred to Tenant Knowledge.",
        )
    if symbol in {"agent_import_from_clawhub", "agent_import_from_url", "import_skill_to_agent"}:
        return Decision(
            "reuse_rewrite",
            "capability_market",
            "Skill acquisition mechanics move behind controlled Capability Market installation.",
        )
    if symbol in {"download_file", "list_files", "preview_file", "read_file"}:
        return Decision("rewrite", "workspace", "Bounded Workspace list/read/preview/download behavior is retained.")
    return Decision(
        "delete", None, "The first target release removes human Workspace mutation, locks, and revision APIs."
    )


def _classify_skills(symbol: str) -> Decision:
    if symbol in {"browse_delete", "browse_write"}:
        return Decision("delete", None, "Direct Skill file mutation is explicitly removed.")
    if symbol in {"get_skill_token_status", "set_skill_token"}:
        return Decision("rewrite", "credential", "Capability tokens move to Credential bindings.")
    if symbol in {"clawhub_detail", "import_from_url", "install_from_clawhub", "preview_url_import", "search_clawhub"}:
        return Decision(
            "reuse_rewrite", "capability_market", "ClawHub transport mechanics move behind Capability Market."
        )
    return Decision(
        "rewrite", "capability_market", "Controlled Skill catalog administration moves to Capability Market."
    )


def _classify_lifecycle(row_id: str) -> Decision:
    if row_id in {
        "LIFECYCLE:bootstrap:Base_metadata_create_all",
        "LIFECYCLE:bootstrap:clean_orphaned_mcp_tools",
        "LIFECYCLE:bootstrap:default_tenant_creation",
        "LIFECYCLE:bootstrap:patch_existing_okr_agent",
        "LIFECYCLE:bootstrap:push_default_skills_to_existing_agents",
        "LIFECYCLE:bootstrap:shutil_copytree",
        "LIFECYCLE:run:running_runtime_worker_context",
        "LIFECYCLE:run:runtime_stack_aclose",
    }:
        return Decision(
            "delete", None, "The accepted lifecycle matrix removes startup repair and the legacy Runtime lifecycle."
        )
    if row_id == "LIFECYCLE:audit:write_audit_log":
        return Decision("rewrite", "audit", "Startup audit moves to the target Audit actor contract.")
    if row_id == "LIFECYCLE:bootstrap:seed_agent_templates":
        return Decision("defer_rewrite", "agent_template", "Template bootstrap moves to the Agent Template owner.")
    if row_id in {"LIFECYCLE:bootstrap:seed_default_agents"}:
        return Decision(
            "defer_rewrite", "onboarding", "Default assistants become ordinary Onboarding-owned Agent creation."
        )
    if row_id in {"LIFECYCLE:bootstrap:seed_okr_agent"}:
        return Decision("defer_rewrite", "okr", "OKR Agent bootstrap is preserved only in the OKR product slice.")
    if row_id in {
        "LIFECYCLE:bootstrap:seed_atlassian_rovo_config",
        "LIFECYCLE:bootstrap:seed_atlassian_rovo_tools",
        "LIFECYCLE:bootstrap:seed_skills",
    }:
        return Decision(
            "defer_rewrite", "capability_market", "Capability bootstrap moves to the Capability Market owner."
        )
    if row_id == "LIFECYCLE:bootstrap:seed_builtin_tools":
        return Decision("rewrite", "tool", "Builtin Tool registration becomes deterministic Tool bootstrap.")
    if row_id.startswith("LIFECYCLE:channel:") or row_id == "LIFECYCLE:discord_infrastructure:start_ss_local":
        return Decision("reuse_rewrite", "channel", "Connector mechanics remain bounded Channel-owned lifecycles.")
    if row_id.startswith("LIFECYCLE:trigger:"):
        return Decision("defer_rewrite", "trigger", "Scheduler and daemon intake consolidate under Trigger.")
    if row_id in {
        "LIFECYCLE:application:lifespan",
        "LIFECYCLE:infrastructure:close_redis",
        "LIFECYCLE:realtime:realtime_router_start",
        "LIFECYCLE:realtime:realtime_router_stop",
    }:
        return Decision(
            "rewrite",
            "run",
            "Application and shared realtime lifecycle composition is replaced with bounded target owners; Run owns Runner mechanics.",
        )
    raise ValueError(f"unclassified lifecycle row: {row_id}")


def _source_evidence(row: dict[str, Any]) -> dict[str, Any]:
    source_path, symbol = row["source"].split(":", 1)
    path = BACKEND_ROOT / source_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == symbol
    ]
    if len(nodes) != 1:
        raise ValueError(f"source symbol must resolve exactly once: {row['source']}")
    node = nodes[0]
    return {
        "path": source_path,
        "symbol": symbol,
        "line_start": node.lineno,
        "line_end": node.end_lineno,
        "docstring": ast.get_docstring(node),
        "observed_contract": row["id"],
    }


def _normalized_route_patterns(row_id: str) -> tuple[str, ...]:
    route = row_id.split(":", 1)[1]
    normalized = re.sub(r"\{[^}]+\}", "{}", route)
    patterns = [normalized]
    if normalized.startswith("/api/"):
        patterns.append(normalized.removeprefix("/api"))
    return tuple(patterns)


def _normalize_frontend_window(text: str) -> str:
    return re.sub(r"\$\{[^}]+\}", "{}", "".join(text.split()))


@cache
def _frontend_windows() -> tuple[tuple[str, int, str], ...]:
    windows: list[tuple[str, int, str]] = []
    for path in sorted((REPO_ROOT / "frontend/src").rglob("*")):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        display = path.relative_to(REPO_ROOT).as_posix()
        for line_number in range(1, len(lines) + 1):
            window = _normalize_frontend_window("\n".join(lines[line_number - 1 : line_number + 4]))
            windows.append((display, line_number, window))
    return tuple(windows)


def _frontend_consumers(row: dict[str, Any]) -> dict[str, Any]:
    if row["kind"] not in {"http", "websocket"}:
        return {
            "status": "not_applicable_internal_lifecycle",
            "evidence": ["app/main.py"],
            "note": "The consumer is application process composition, not a Frontend call site.",
        }
    patterns = _normalized_route_patterns(row["id"])
    matches: list[str] = []
    for display, line_number, window in _frontend_windows():
        if any(pattern in window for pattern in patterns):
            matches.append(f"{display}:{line_number}")
    matches = sorted(set(matches))
    if matches:
        return {
            "status": "frontend_static_consumers_found",
            "evidence": matches,
            "note": "A five-line Frontend source window contains the normalized route template, with JavaScript interpolations treated as path parameters and the shared /api prefix optional.",
        }
    external = (
        row["id"].startswith(
            (
                "GET:/p/",
                "POST:/api/channel/",
                "POST:/api/gateway/",
                "GET:/api/gateway/",
                "POST:/api/webhooks/",
                "WEBSOCKET:",
            )
        )
        or "callback" in row["id"]
        or "webhook" in row["id"]
    )
    return {
        "status": "external_or_dynamic_consumer" if external else "no_frontend_static_consumer",
        "evidence": [row["source"]],
        "note": (
            "The mounted route is an external callback, webhook, public page, Gateway, or WebSocket entry; no Frontend literal is required."
            if external
            else "No five-line Frontend src window contains the normalized route template. This is explicit static no-consumer evidence, not a claim about unobserved external traffic."
        ),
    }


def _gate(decision: Decision, row: dict[str, Any]) -> str:
    if decision.disposition == "delete":
        return f"tests/architecture/test_deleted_authorities.py::{row['source'].replace('/', '_').replace(':', '_')}"
    return f"tests/acceptance/{decision.owner}/test_{decision.owner}_contract.py"


def build_evidence(manifest: dict[str, Any], owners: set[str]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for row in manifest["entries"]:
        decision = classify(row)
        if decision.owner is not None and decision.owner not in owners:
            raise ValueError(f"{row['id']}: unknown target owner {decision.owner}")
        records.append(
            {
                "id": row["id"],
                "source": _source_evidence(row),
                "consumer": _frontend_consumers(row),
                "decision": {
                    "disposition": decision.disposition,
                    "target_owner_id": decision.owner,
                    "deletion_intent": decision.rationale if decision.owner is None else None,
                    "rationale": decision.rationale,
                    "planned_gate": _gate(decision, row),
                },
            }
        )
    return {
        "schema_version": 1,
        "authorities": _authority_records(),
        "owner_roster": sorted(owners),
        "method": "Backend handler AST locations plus deterministic Frontend fixed-route-segment scan.",
        "entries": records,
    }


def refresh_evidence_authorities(
    manifest: dict[str, Any],
    owners: set[str],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    if evidence.get("schema_version") != 1:
        raise ValueError("cannot refresh unsupported disposition evidence schema")
    if evidence.get("owner_roster") != sorted(owners):
        raise ValueError("cannot refresh disposition evidence with a drifted owner roster")
    entries = evidence.get("entries")
    if not isinstance(entries, list):
        raise TypeError("cannot refresh disposition evidence without entries")
    evidence_by_id = {
        entry.get("id"): entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    manifest_ids = [row["id"] for row in manifest["entries"]]
    if len(evidence_by_id) != len(entries) or set(evidence_by_id) != set(manifest_ids):
        raise ValueError("cannot refresh disposition evidence with drifted coverage IDs")
    for row in manifest["entries"]:
        decision = classify(row)
        recorded = evidence_by_id[row["id"]].get("decision")
        if not isinstance(recorded, dict):
            raise TypeError(f"cannot refresh disposition evidence without decision: {row['id']}")
        if recorded.get("disposition") != decision.disposition or recorded.get("target_owner_id") != decision.owner:
            raise ValueError(f"cannot refresh drifted disposition evidence decision: {row['id']}")
    refreshed = dict(evidence)
    refreshed["authorities"] = _authority_records()
    return refreshed


def approve(manifest_path: Path, evidence_path: Path) -> tuple[int, int]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    owner_manifest = json.loads((manifest_path.parent / "owner-contracts.json").read_text(encoding="utf-8"))
    owners = {owner["owner_id"] for owner in owner_manifest["owners"]}
    if all(row.get("state") == "disposition_approved" for row in manifest["entries"]):
        existing_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence = refresh_evidence_authorities(manifest, owners, existing_evidence)
    else:
        evidence = build_evidence(manifest, owners)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    evidence_record = {
        "path": evidence_path.relative_to(BACKEND_ROOT).as_posix(),
        "sha256": _sha256(evidence_path),
    }
    by_id = {record["id"]: record for record in evidence["entries"]}
    changed = 0
    already_approved = 0
    for row in manifest["entries"]:
        record = by_id[row["id"]]
        decision = record["decision"]
        if row["state"] == "disposition_approved":
            if row["disposition"] != decision["disposition"] or row["target_owner_id"] != decision["target_owner_id"]:
                raise ValueError(f"approved disposition drifted from accepted decision: {row['id']}")
            row["behavior_evidence"] = [evidence_record]
            row["consumer_evidence"] = [evidence_record]
            row["planned_gate"] = decision["planned_gate"]
            row["transition_evidence"] = [
                {"from": "unreviewed", **evidence_record, "to": "disposition_approved"}
            ]
            already_approved += 1
            continue
        if row["state"] != "unreviewed":
            raise ValueError(f"refusing to edit post-disposition row: {row['id']} ({row['state']})")
        row["disposition"] = decision["disposition"]
        row["target_owner_id"] = decision["target_owner_id"]
        row["behavior_evidence"] = [evidence_record]
        row["consumer_evidence"] = [evidence_record]
        row["planned_gate"] = decision["planned_gate"]
        row["state"] = "disposition_approved"
        row["transition_evidence"].append({"from": "unreviewed", "to": "disposition_approved", **evidence_record})
        changed += 1
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed, already_approved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    changed, already_approved = approve(args.manifest.resolve(), args.evidence.resolve())
    print(f"dispositions approved: changed={changed} already_approved={already_approved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
