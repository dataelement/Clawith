"""Agent Bundle hire orchestration.

Top-level entry: ``hire_bundle(slug, user, visibility)`` is called from
``api.agent_bundles.hire_bundle`` to atomically materialise an N-agent team
in the caller's tenant.

Flow:

    1. Load bundle + nested agents/mcp_servers/relationships (eager).
    2. Quota precheck: current_user.agent_count + N <= user.quota_max_agents
       (admin bypasses).
    3. Idempotency precheck: any pre-existing Agent in the same tenant +
       created by the same user with name in bundle.agents.name -> 409.
    4. Tx A: create N Agent rows + Participant + AgentPermission. Commit so
       the agent rows are visible to subsequent independent sessions opened
       by ``import_mcp_direct``.
    5. Per-agent workspace setup: scaffold via initialize_agent_files, then
       overwrite soul.md with bundle's verbatim soul, then copy custom skills
       from the bundle folder. Customised inline since the create_agent flow
       in api.agents.py is tightly coupled to its request shape.
    6. MCP binding: for each declared MCP server, call ``import_mcp_direct``
       against every agent whose ``default_mcp_attach`` references that
       server's ``local_key``. ``import_mcp_direct`` opens its own session
       and is idempotent on Tool.name; a second hire of the same bundle
       reuses Tool rows globally and just creates fresh AgentTool junctions.
    7. Tx B: insert AgentAgentRelationship rows by mapping bundle agent slugs
       to fresh agent UUIDs. Regenerate relationships.md for each new agent.
    8. Start containers (best-effort; mirror of api.agents.py:470).
    9. Return HireResponse.

Failure handling: failures at any step from (4) onwards trigger
``_cleanup_partial_bundle_hire`` which deletes all freshly-created Agent rows
(FK CASCADE cleans Participant / AgentPermission / AgentTool / outbound
AgentAgentRelationship). Tool rows themselves are tenant-shared and left in
place for future hires to reuse.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timedelta, timezone as tz
from pathlib import Path

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.agent import Agent, AgentPermission
from app.models.agent_bundle import (
    AgentBundle,
    AgentBundleAgent,
    AgentBundleMcpServer,
    AgentBundleRelationship,
)
from app.models.org import AgentAgentRelationship
from app.models.participant import Participant
from app.models.user import User


class BundleHireError(Exception):
    """Raised when bundle hire fails after pre-checks — triggers rollback."""

    def __init__(self, message: str, *, status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class BundleHireConflict(BundleHireError):
    """Bundle already hired by this user OR agent name collision -> HTTP 409."""

    def __init__(self, message: str):
        super().__init__(message, status_code=409)


# ── Load helpers ─────────────────────────────────────────────────────


async def _load_bundle(db: AsyncSession, slug: str) -> AgentBundle:
    result = await db.execute(
        select(AgentBundle)
        .where(AgentBundle.slug == slug)
        .options(
            selectinload(AgentBundle.agents),
            selectinload(AgentBundle.mcp_servers),
            selectinload(AgentBundle.relationships),
        )
    )
    bundle = result.scalar_one_or_none()
    if bundle is None:
        raise BundleHireError(f"Bundle '{slug}' not found", status_code=404)
    if not bundle.agents:
        raise BundleHireError(f"Bundle '{slug}' has zero agents", status_code=400)
    return bundle


async def _check_idempotency(
    db: AsyncSession,
    bundle: AgentBundle,
    user: User,
) -> None:
    """Refuse hire if any agent name in the bundle already exists for this user+tenant."""
    names = [a.name for a in bundle.agents]
    if not names:
        return
    result = await db.execute(
        select(Agent.name).where(
            Agent.tenant_id == user.tenant_id,
            Agent.creator_id == user.id,
            Agent.name.in_(names),
            Agent.is_expired == False,  # noqa: E712
        )
    )
    collisions = [row[0] for row in result.all()]
    if collisions:
        raise BundleHireConflict(
            f"Name collision: agents already exist with names {sorted(collisions)}. "
            f"Bundle hire is all-or-nothing — delete the existing agents or hire "
            f"under a different account."
        )


# ── Tenant default discovery ─────────────────────────────────────────


async def _resolve_tenant_defaults(db: AsyncSession, user: User) -> dict:
    """Mirror api.agents.create_agent's tenant-default resolution."""
    from app.models.tenant import Tenant

    defaults = {
        "ttl_hours": user.quota_agent_ttl_hours or 0,
        "max_llm_calls": 1000,
        "default_max_triggers": 20,
        "default_min_poll": 5,
        "default_webhook_rate": 5,
        "default_heartbeat_interval": 240,
        "tenant_default_model_id": None,
    }
    if not user.tenant_id:
        return defaults

    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        return defaults

    if tenant.default_agent_ttl_hours is not None:
        defaults["ttl_hours"] = tenant.default_agent_ttl_hours
    if tenant.default_max_llm_calls_per_day:
        defaults["max_llm_calls"] = tenant.default_max_llm_calls_per_day
    if tenant.default_max_triggers:
        defaults["default_max_triggers"] = tenant.default_max_triggers
    if tenant.min_poll_interval_floor:
        defaults["default_min_poll"] = tenant.min_poll_interval_floor
    if tenant.max_webhook_rate_ceiling:
        defaults["default_webhook_rate"] = tenant.max_webhook_rate_ceiling
    if (
        tenant.min_heartbeat_interval_minutes
        and tenant.min_heartbeat_interval_minutes > defaults["default_heartbeat_interval"]
    ):
        defaults["default_heartbeat_interval"] = tenant.min_heartbeat_interval_minutes
    defaults["tenant_default_model_id"] = tenant.default_model_id
    return defaults


async def _resolve_primary_model(
    db: AsyncSession,
    user: User,
    primary_model_hint: str | None,
    tenant_default_model_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """Resolve bundle's primary_model_hint to an LLMModel.id, fallback to tenant default."""
    if not primary_model_hint:
        return tenant_default_model_id

    from app.models.llm import LLMModel

    # Try match by qualified name ("openai/gpt-5.4" → split into provider + model name).
    # Best-effort: most tenants register models with display_name or name matching this.
    result = await db.execute(
        select(LLMModel).where(
            (LLMModel.tenant_id == user.tenant_id) | (LLMModel.tenant_id.is_(None)),
            (LLMModel.name == primary_model_hint) | (LLMModel.display_name == primary_model_hint),
        ).limit(1)
    )
    model = result.scalar_one_or_none()
    if model:
        return model.id
    logger.warning(
        f"[BundleHire] primary_model_hint '{primary_model_hint}' not found in tenant; "
        f"falling back to tenant default."
    )
    return tenant_default_model_id


# ── Agent creation ───────────────────────────────────────────────────


def _agent_dir(agent_id: uuid.UUID) -> Path:
    return Path(get_settings().AGENT_DATA_DIR) / str(agent_id)


async def _create_agent_row(
    db: AsyncSession,
    ba: AgentBundleAgent,
    user: User,
    visibility: str,
    tenant_defaults: dict,
    primary_model_id: uuid.UUID | None,
    bundle_slug: str,
    bundle_hire_group_id: uuid.UUID,
    is_principal: bool,
) -> Agent:
    """Insert one Agent + Participant + AgentPermission row.

    Mirrors the relevant slice of ``api.agents.create_agent`` lines 314-371.
    Does NOT scaffold the filesystem — that's done separately in
    ``_setup_agent_workspace`` after the parent commit so file system writes
    don't block the DB transaction.

    Bundle-group fields (``bundle_slug`` / ``bundle_hire_group_id`` /
    ``is_principal``) are persisted on the Agent row so the sidebar can fold
    all bundle-mates under one collapsible header and put a yellow star next
    to the principal.
    """
    ttl_hours = tenant_defaults["ttl_hours"] or 0
    expires_at = (
        datetime.now(tz.utc) + timedelta(hours=ttl_hours) if ttl_hours and ttl_hours > 0 else None
    )

    agent = Agent(
        name=ba.name,
        role_description=ba.role_description,
        bio="",
        avatar_url=None,
        creator_id=user.id,
        tenant_id=user.tenant_id,
        agent_type="native",
        primary_model_id=primary_model_id,
        fallback_model_id=None,
        max_tokens_per_day=None,
        max_tokens_per_month=None,
        template_id=None,  # Bundle agents are not template-derived
        is_from_bundle=True,  # short-circuits per-user onboarding ritual for ALL users (hire-er + later org members)
        bundle_slug=bundle_slug,
        bundle_hire_group_id=bundle_hire_group_id,
        is_bundle_principal=is_principal,
        status="creating",
        expires_at=expires_at,
        max_llm_calls_per_day=tenant_defaults["max_llm_calls"],
        max_triggers=tenant_defaults["default_max_triggers"],
        min_poll_interval_min=tenant_defaults["default_min_poll"],
        webhook_rate_limit=tenant_defaults["default_webhook_rate"],
        heartbeat_interval_minutes=tenant_defaults["default_heartbeat_interval"],
    )
    if ba.default_autonomy_policy:
        agent.autonomy_policy = ba.default_autonomy_policy

    db.add(agent)
    await db.flush()  # populate agent.id

    db.add(Participant(
        type="agent",
        ref_id=agent.id,
        display_name=agent.name,
        avatar_url=agent.avatar_url,
    ))

    if visibility == "company":
        agent.access_mode = "company"
        agent.company_access_level = "use"
        db.add(AgentPermission(agent_id=agent.id, scope_type="company", access_level="use"))
    elif visibility == "custom":
        # Mirror single-agent custom flow (api/agents.py:373-376):
        # creator gets manage rights; access_mode = "custom" so the user can
        # later add specific platform users per agent via the Settings UI.
        agent.access_mode = "custom"
        agent.company_access_level = "use"
        db.add(AgentPermission(
            agent_id=agent.id,
            scope_type="user",
            scope_id=user.id,
            access_level="manage",
        ))
    else:  # "only_me"
        agent.access_mode = "private"
        agent.company_access_level = "use"
        db.add(AgentPermission(
            agent_id=agent.id,
            scope_type="user",
            scope_id=user.id,
            access_level="manage",
        ))

    await db.flush()
    return agent


# ── Workspace setup ──────────────────────────────────────────────────


async def _setup_agent_workspace(
    db: AsyncSession,
    agent: Agent,
    ba: AgentBundleAgent,
    bundle_slug: str,
) -> None:
    """Scaffold the agent's filesystem, write its bundle soul, copy bundle skills.

    Side effects only; no DB writes here. Errors propagate to the hire-level
    rollback logic.
    """
    from app.services.agent_manager import agent_manager

    # Scaffold the dir from the global agent_template (sets up workspace/, memory/, skills/)
    await agent_manager.initialize_agent_files(db, agent, personality="", boundaries="")

    # Overwrite soul.md with the bundle's verbatim soul (the scaffold writes
    # a placeholder soul; we replace wholesale because bundle souls don't use
    # the ``{{agent_name}}`` template placeholders).
    agent_dir = _agent_dir(agent.id)
    soul_path = agent_dir / "soul.md"
    soul_path.write_text(ba.soul_md or "", encoding="utf-8")

    # Copy bundle-shipped skills into the agent's workspace.
    # Bundle layout: backend/agent_bundles/<bundle_slug>/agents/<agent_slug>/skills/<name>/SKILL.md
    bundle_root = Path(__file__).resolve().parents[2] / "agent_bundles" / bundle_slug
    bundle_agent_skills = bundle_root / "agents" / ba.slug / "skills"
    if bundle_agent_skills.exists() and bundle_agent_skills.is_dir():
        skills_dest = agent_dir / "skills"
        skills_dest.mkdir(parents=True, exist_ok=True)
        for skill_dir in bundle_agent_skills.iterdir():
            if not skill_dir.is_dir():
                continue
            dest = skills_dest / skill_dir.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(str(skill_dir), str(dest))
            logger.debug(
                f"[BundleHire] Installed bundle skill '{skill_dir.name}' "
                f"into agent {agent.id} workspace"
            )


# ── Tool toggle application ──────────────────────────────────────────


async def _apply_default_tool_toggles(
    db: AsyncSession,
    agent_id: uuid.UUID,
    toggles: dict,
) -> int:
    """Apply the bundle's recorded per-builtin-tool enable/disable state to a freshly-hired agent.

    Clawith uses lazy AgentTool assignment: if no row exists for (agent_id, tool_id),
    the get_agent_tools view falls back to Tool.is_default. To pin the agent into the
    source agent's exact toggle profile (which we snapshot at bundle author time), we
    upsert AgentTool rows for every tool name in the toggle map.

    MCP tools are intentionally NOT in this map — they're handled by ``_bind_bundle_mcps``
    which always enables every discovered tool against the imported ``mcp_server_url``.
    Only builtin tools (snapshotted with mcp_server_url IS NULL) appear here.

    Returns the count of toggles successfully applied. Toggles referencing tool names
    that no longer exist in the tenant (e.g. dropped or renamed since the bundle was
    authored) are logged as warnings and skipped — bundle hire stays best-effort here
    rather than failing the whole transaction on a single missing tool.
    """
    if not toggles:
        return 0

    from app.models.tool import AgentTool, Tool

    names = list(toggles.keys())
    name_rows = await db.execute(select(Tool.id, Tool.name).where(Tool.name.in_(names)))
    name_to_id = {row.name: row.id for row in name_rows}

    # Pre-fetch existing AgentTool rows in one query so we can upsert in a tight loop.
    tool_ids = list(name_to_id.values())
    if tool_ids:
        existing_r = await db.execute(
            select(AgentTool).where(
                AgentTool.agent_id == agent_id,
                AgentTool.tool_id.in_(tool_ids),
            )
        )
        existing_by_tool_id = {at.tool_id: at for at in existing_r.scalars().all()}
    else:
        existing_by_tool_id = {}

    applied = 0
    skipped = 0
    for name, wanted_enabled in toggles.items():
        tool_id = name_to_id.get(name)
        if tool_id is None:
            skipped += 1
            continue
        at = existing_by_tool_id.get(tool_id)
        if at is not None:
            at.enabled = bool(wanted_enabled)
        else:
            db.add(AgentTool(
                agent_id=agent_id,
                tool_id=tool_id,
                enabled=bool(wanted_enabled),
                source="system",  # matches Clawith convention for platform-set toggles
            ))
        applied += 1

    if skipped:
        logger.warning(
            f"[BundleHire] {skipped} tool toggle(s) referenced unknown tool names for "
            f"agent {agent_id} (tool may have been dropped/renamed since bundle authored)."
        )
    return applied


# ── MCP per-tool toggle application ──────────────────────────────────


async def _apply_default_mcp_tool_toggles(
    db: AsyncSession,
    bundle: AgentBundle,
    agent_id: uuid.UUID,
    mcp_toggles: dict,
) -> int:
    """After _bind_bundle_mcps creates AgentTool rows (default enabled=True for
    every MCP-discovered tool), flip per-tool enable state to match the source
    snapshot.

    ``mcp_toggles`` shape: ``{mcp_local_key: {mcp_tool_name: bool}}``.

    Matching is by (Tool.mcp_server_url, Tool.mcp_tool_name) — stable across
    naming conventions (Tool.name may differ between source and target stack
    depending on how the MCP was installed). If a recorded mcp_tool_name no
    longer exists on the target's MCP server (server schema drift), we log and
    skip rather than fail the hire.

    Returns the count of toggles successfully applied.
    """
    if not mcp_toggles:
        return 0

    from app.models.tool import AgentTool, Tool

    # Map local_key -> mcp_server_url via bundle's MCP server table
    key_to_url = {m.local_key: m.url for m in bundle.mcp_servers}

    applied = 0
    skipped = 0
    closed_world_disabled = 0
    for local_key, per_tool in mcp_toggles.items():
        mcp_url = key_to_url.get(local_key)
        if mcp_url is None:
            logger.warning(
                f"[BundleHire] mcp_tool_toggles references unknown local_key "
                f"'{local_key}' (not in bundle mcps.yaml); skipping {len(per_tool)} toggles"
            )
            continue

        # ALL Tool rows on this stack for this MCP server (the live list, possibly
        # larger than the snapshot if the MCP server added tools since bundle was authored).
        all_tools_r = await db.execute(
            select(Tool.id, Tool.mcp_tool_name).where(
                Tool.mcp_server_url == mcp_url,
                Tool.mcp_tool_name.isnot(None),
            )
        )
        live_name_to_id = {row.mcp_tool_name: row.id for row in all_tools_r}
        if not live_name_to_id:
            logger.warning(
                f"[BundleHire] No live Tool rows for mcp_server_url={mcp_url} "
                f"(MCP probably not yet imported on this stack); skipping {len(per_tool)} toggles"
            )
            continue

        # Pre-fetch existing AgentTool rows for ALL live MCP tools (covers both
        # the toggles-recorded tools AND any closed-world disables for unknowns).
        live_tool_ids = list(live_name_to_id.values())
        existing_r = await db.execute(
            select(AgentTool).where(
                AgentTool.agent_id == agent_id,
                AgentTool.tool_id.in_(live_tool_ids),
            )
        )
        existing_by_tool_id = {at.tool_id: at for at in existing_r.scalars().all()}

        # Pass 1: apply known toggles from snapshot.
        recorded_tool_ids = set()
        for mcp_tool_name, wanted_enabled in per_tool.items():
            tool_id = live_name_to_id.get(mcp_tool_name)
            if tool_id is None:
                # Snapshot has this tool name but live MCP doesn't (server dropped/renamed).
                skipped += 1
                continue
            recorded_tool_ids.add(tool_id)
            at = existing_by_tool_id.get(tool_id)
            if at is not None:
                at.enabled = bool(wanted_enabled)
            else:
                db.add(AgentTool(
                    agent_id=agent_id, tool_id=tool_id,
                    enabled=bool(wanted_enabled), source="system",
                ))
            applied += 1

        # Pass 2 (closed-world): any live tool NOT in the snapshot is forced disabled
        # to mirror "source intent". Otherwise _bind_bundle_mcps would have left them
        # enabled by default, polluting the agent with capabilities the source never had.
        for mcp_tool_name, tool_id in live_name_to_id.items():
            if tool_id in recorded_tool_ids:
                continue
            at = existing_by_tool_id.get(tool_id)
            if at is not None:
                if at.enabled:
                    at.enabled = False
                    closed_world_disabled += 1
            else:
                db.add(AgentTool(
                    agent_id=agent_id, tool_id=tool_id,
                    enabled=False, source="system",
                ))
                closed_world_disabled += 1

    if skipped:
        logger.warning(
            f"[BundleHire] {skipped} MCP tool toggle(s) referenced mcp_tool_names "
            f"missing from current MCP server schema (server may have changed since "
            f"bundle was authored); agent {agent_id} continues with defaults for those."
        )
    if closed_world_disabled:
        logger.info(
            f"[BundleHire] agent {agent_id}: closed-world disabled {closed_world_disabled} "
            f"MCP tool(s) absent from source snapshot."
        )
    return applied


# ── 模拟盘发号(招聘交易团队 → 每团队独立账户 + team token)──────────────


def _redact_mcp_url(url: str) -> str:
    """日志脱敏:把 /mcp/<token>/ 里的 token 抹掉,避免 team token 落日志。"""
    import re
    return re.sub(r"(/mcp/)[^/]+", r"\1***", url or "")


async def _provision_team_token(bundle: AgentBundle, hire_group_id: uuid.UUID) -> dict | None:
    """招聘交易团队时向模拟盘发号:给本次团队建专用账户 + team token。

    返回 {token, account_id, idempotency_key};或 None 表示跳过(未配置
    SIM_PROVISION_URL,或该 bundle 没有交易盘 MCP)→ 按 mcps.yaml 原样绑,行为不变。
    token 用于写进各 agent 的 AgentTool.config["api_key"](加密)——运行时
    _execute_mcp_tool 把它合并出来交给 MCPClient 作 Authorization: Bearer 发送,
    模拟盘 /mcp/t 共享端点按 header 绑账户。**绝不再做 per-team URL 覆盖**
    (Tool.mcp_server_url 是全局共享行,覆盖会串所有团队 —— 已踩过)。
    发号失败 → 抛 BundleHireError(整单 fail-hard,绝不降级到共享全局账户)。
    """
    settings = get_settings()
    if not settings.SIM_PROVISION_URL:
        return None  # 未启用 → 跳过(部署本改动默认零影响)
    key = settings.SIM_TEAM_MCP_LOCAL_KEY
    if not any(m.local_key == key for m in bundle.mcp_servers):
        return None  # 该 bundle 不含交易盘 MCP → 不发号
    idem = f"clawith:hire:{hire_group_id}:{key}"
    payload = {
        "team_id": str(hire_group_id),
        "team_name": bundle.name or bundle.slug,
        "hire_id": str(hire_group_id),
        "idempotency_key": idem,
    }
    import httpx
    try:
        async with httpx.AsyncClient(timeout=settings.SIM_PROVISION_TIMEOUT) as cli:
            resp = await cli.post(
                settings.SIM_PROVISION_URL, json=payload,
                headers={"Authorization": f"Bearer {settings.SIM_PROVISION_KEY}"})
        if resp.status_code not in (200, 201):
            raise BundleHireError(
                f"模拟盘发号失败 HTTP {resp.status_code}: {resp.text[:200]}", status_code=502)
        data = resp.json()
    except BundleHireError:
        raise
    except Exception as exc:
        raise BundleHireError(f"模拟盘发号不可达: {exc}", status_code=502) from exc
    raw_token = data.get("token")
    if not raw_token:
        raise BundleHireError("模拟盘发号返回缺 token", status_code=502)
    logger.info(f"[BundleHire] team token 已发 account={data.get('account_id')} "
                f"reused={data.get('reused')}")
    return {"token": raw_token, "account_id": data.get("account_id"), "idempotency_key": idem}


async def _write_team_api_keys(
    db: AsyncSession,
    bundle: AgentBundle,
    slug_map: dict[str, uuid.UUID],
    raw_token: str,
) -> int:
    """把团队 token(加密后)写进每个 attach 了交易盘 MCP 的 agent 的
    AgentTool.config["api_key"]。

    运行时 _execute_mcp_tool 合并 {**Tool.config, **AgentTool.config} 并解密,
    api_key 经 MCPClient 变成 Authorization: Bearer —— 共享 /mcp/t 端点按
    header 绑账户,实现 per-team 隔离而不碰共享 Tool 行。

    工具行按 mcp_server_name 匹配(同一 MCP server 的所有工具),比按 url 匹配
    稳:端点 url 切换(.20:8510 → .118:8503/mcp/t)不影响匹配。
    返回写入的 AgentTool 行数;为 0 时调用方应 fail-hard(否则交易员会
    静默落到共享默认账户,隔离失效)。
    """
    from app.models.tool import AgentTool, Tool
    from app.services.tool_config import encrypt_sensitive_fields

    key = get_settings().SIM_TEAM_MCP_LOCAL_KEY
    mcp = next((m for m in bundle.mcp_servers if m.local_key == key), None)
    if mcp is None:
        return 0
    enc_token = encrypt_sensitive_fields({"api_key": raw_token}, None)["api_key"]

    tools_r = await db.execute(
        select(Tool.id).where(Tool.mcp_server_name == mcp.server_name, Tool.type == "mcp")
    )
    tool_ids = [row[0] for row in tools_r.all()]
    if not tool_ids:
        return 0

    written = 0
    for ba in bundle.agents:
        if key not in (ba.default_mcp_attach or []):
            continue
        agent_id = slug_map.get(ba.slug)
        if agent_id is None:
            continue
        ats_r = await db.execute(
            select(AgentTool).where(
                AgentTool.agent_id == agent_id, AgentTool.tool_id.in_(tool_ids)
            )
        )
        for at in ats_r.scalars().all():
            # Don't put a live trading credential on a binding that's been
            # disabled — the runtime authorization gate in _execute_mcp_tool
            # refuses disabled tools anyway, so a token here would just be a
            # dormant secret. (Today nothing is disabled, so this is a no-op;
            # it stays correct once meta-driven per-tool disabling lands.)
            if not at.enabled:
                continue
            cfg = dict(at.config or {})
            cfg["api_key"] = enc_token
            at.config = cfg
            written += 1
    await db.flush()
    return written


async def _revoke_team_token(idempotency_key: str | None) -> None:
    """回滚用:招聘失败时吊销已发的 team token(best-effort,不抛)。"""
    settings = get_settings()
    if not settings.SIM_PROVISION_URL or not idempotency_key:
        return
    revoke_url = settings.SIM_PROVISION_URL.rstrip("/") + "/revoke"
    import httpx
    try:
        async with httpx.AsyncClient(timeout=settings.SIM_PROVISION_TIMEOUT) as cli:
            await cli.post(revoke_url, json={"idempotency_key": idempotency_key},
                           headers={"Authorization": f"Bearer {settings.SIM_PROVISION_KEY}"})
        logger.info(f"[BundleHire] 已吊销 team token(回滚)idem={idempotency_key}")
    except Exception as exc:
        logger.error(f"[BundleHire] 回滚吊销 team token 失败(需手动清理)idem={idempotency_key}: {exc}")


# ── MCP binding ──────────────────────────────────────────────────────


async def _bind_bundle_mcps(
    bundle: AgentBundle,
    slug_map: dict[str, uuid.UUID],
    url_overrides: dict[str, str] | None = None,
) -> int:
    """For each declared MCP server × every agent whose default_mcp_attach references it,
    call import_mcp_direct (which opens its own session and is idempotent).

    Returns the total count of (agent, mcp) bindings created/refreshed.

    P1 fix: ``import_mcp_direct`` silently falls back to a single placeholder Tool
    (mcp_tool_name=None) when the MCP server is unreachable / list_tools fails.
    Without verification, hire would report success while the agent has no usable
    trading tools. After each import, we count real tools (mcp_tool_name IS NOT NULL)
    attached to this agent for this URL; if 0, raise so cleanup rolls the hire back.
    """
    from app.services.resource_discovery import import_mcp_direct
    from app.database import async_session as _verify_session
    from app.models.tool import AgentTool, Tool

    binding_count = 0
    overrides = url_overrides or {}
    # Build mcp_local_key -> AgentBundleMcpServer lookup
    mcp_by_key: dict[str, AgentBundleMcpServer] = {m.local_key: m for m in bundle.mcp_servers}

    for ba in bundle.agents:
        attach_keys = list(ba.default_mcp_attach or [])
        for key in attach_keys:
            mcp = mcp_by_key.get(key)
            if mcp is None:
                logger.warning(
                    f"[BundleHire] Bundle '{bundle.slug}': agent '{ba.slug}' references "
                    f"unknown MCP local_key '{key}', skipping."
                )
                continue
            agent_id = slug_map.get(ba.slug)
            if agent_id is None:
                continue
            effective_url = overrides.get(key, mcp.url)  # team token 覆盖 paper_trading
            try:
                result_msg = await import_mcp_direct(
                    mcp_url=effective_url,
                    agent_id=agent_id,
                    server_name=mcp.server_name,
                )
                logger.info(
                    f"[BundleHire] MCP bind for agent {agent_id} ({ba.slug}) "
                    f"<- {mcp.server_name}: {result_msg.splitlines()[0] if result_msg else 'ok'}"
                )
            except Exception as exc:
                # Surface, then let the hire-level rollback handle cleanup.
                raise BundleHireError(
                    f"MCP binding failed for agent '{ba.slug}' -> "
                    f"{mcp.server_name} ({_redact_mcp_url(effective_url)}): {exc}",
                    status_code=502,
                ) from exc

            # P1 verification: did this agent actually get real (non-placeholder) tools?
            async with _verify_session() as verify_db:
                result = await verify_db.execute(
                    select(Tool.id)
                    .join(AgentTool, AgentTool.tool_id == Tool.id)
                    .where(
                        AgentTool.agent_id == agent_id,
                        AgentTool.enabled == True,  # noqa: E712
                        Tool.mcp_server_url == effective_url,
                        Tool.mcp_tool_name.isnot(None),
                    )
                )
                real_tool_count = len(result.all())
            if real_tool_count == 0:
                raise BundleHireError(
                    f"MCP '{mcp.server_name}' ({_redact_mcp_url(effective_url)}) unreachable: import "
                    f"succeeded but no real tools discovered for agent '{ba.slug}' (only placeholder). "
                    f"Verify the MCP server is running and reachable from this stack.",
                    status_code=502,
                )
            binding_count += 1

    return binding_count


# ── Relationships ────────────────────────────────────────────────────


async def _create_relationships(
    db: AsyncSession,
    bundle: AgentBundle,
    slug_map: dict[str, uuid.UUID],
    user: User,
) -> int:
    """INSERT one AgentAgentRelationship row per bundle relationship spec."""
    count = 0
    for r in bundle.relationships:
        from_id = slug_map.get(r.from_slug)
        to_id = slug_map.get(r.to_slug)
        if from_id is None or to_id is None:
            logger.warning(
                f"[BundleHire] Bundle '{bundle.slug}': relationship references unknown "
                f"slug ({r.from_slug} -> {r.to_slug}), skipping."
            )
            continue
        db.add(AgentAgentRelationship(
            agent_id=from_id,
            target_agent_id=to_id,
            relation=r.relation,
            description=r.description,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        ))
        count += 1
    await db.flush()
    return count


async def _create_default_triggers(
    bundle: AgentBundle,
    slug_map: dict[str, uuid.UUID],
) -> int:
    """For each bundle relationship from→to, install an ``on_message`` trigger
    on the ``to`` agent so it auto-wakes when ``from`` sends an A2A message
    or file.

    Without these triggers, ``send_message_to_agent`` / ``send_file_to_agent``
    queue the message in the recipient's ``workspace/inbox/`` but never wake
    them — the chain stalls until the user manually opens that agent's chat,
    or the heartbeat fires (default 240 min). With them, a multi-agent
    decision chain (RM → chair → bull/bear/risks → trader) runs autonomously
    end-to-end with no user prodding past the initial kickoff.

    Triggers are flagged ``is_system=True`` so a tenant admin can disable
    individual ones (e.g. to silence a noisy edge) but not delete them — a
    re-hire would just re-seed.

    Done in its own session because we run AFTER the parent tx has committed
    the agent rows (FK target must already exist) and want trigger inserts
    isolated from the main tx so a single bad trigger doesn't poison the
    whole hire — partial trigger success is better than rolling back the
    full bundle.
    """
    from app.models.trigger import AgentTrigger
    from sqlalchemy import select as _select
    from app.database import async_session

    # Build slug → display name lookup (on_message config uses display name,
    # not slug — that's what the trigger daemon matches against the inbound
    # message's sender_name field).
    slug_to_name = {a.slug: a.name for a in bundle.agents}

    created = 0
    skipped_unmapped = 0
    async with async_session() as t_db:
        for r in bundle.relationships:
            to_id = slug_map.get(r.to_slug)
            if to_id is None or r.from_slug not in slug_to_name:
                skipped_unmapped += 1
                continue  # Already warned in _create_relationships

            from_name = slug_to_name[r.from_slug]
            trigger_name = f"bundle_on_msg_from_{r.from_slug}"

            # Idempotency: uq constraint is (agent_id, name).
            existing = await t_db.execute(
                _select(AgentTrigger).where(
                    AgentTrigger.agent_id == to_id,
                    AgentTrigger.name == trigger_name,
                )
            )
            if existing.scalar_one_or_none() is not None:
                continue

            t_db.add(AgentTrigger(
                agent_id=to_id,
                name=trigger_name,
                type="on_message",
                config={"from_agent_name": from_name},
                reason=f"Auto-wake when {from_name} sends a message (seeded by bundle '{bundle.slug}' hire)",
                is_enabled=True,
                is_system=True,
                # Tight chains may have multiple back-to-back messages from
                # the same sender (e.g. chair pushes data file then a kick
                # instruction). 30s is short enough to let those through
                # while still preventing accidental tight-loop wake.
                cooldown_seconds=30,
                # Hire-time bound on lifetime fires. Bundle relationships
                # are bidirectional (chair↔risks, RM↔bull/bear), so the
                # auto-derived trigger graph is also bidirectional. Without
                # a fire cap, every reply re-fires the listener back at the
                # speaker — an unbounded feedback loop (verified: in one
                # local test, RM jumped from 43 → 185 messages in 10 min,
                # trader from 2 → 86, chair from 0 → 44, all chasing each
                # other's tails through the 30s cooldown).
                #
                # max_fires=1 enforces the semantic "hire is a one-shot
                # kickoff" — chain propagates exactly one round across the
                # 12 triggers, then settles. Users wanting another run
                # re-hire (which seeds a fresh trigger set) or manually
                # poke an agent. Production may want a higher bound or a
                # converge-detector; 1 is the safe default for the demo
                # scenario where the chain is short and one-shot.
                max_fires=1,
            ))
            created += 1
        await t_db.commit()

    if skipped_unmapped:
        logger.warning(
            f"[BundleHire] _create_default_triggers skipped {skipped_unmapped} "
            f"relationship(s) for bundle '{bundle.slug}' due to missing slug→agent "
            "mapping (recipient or sender not in this hire's agent set)."
        )
    return created


# ── Cleanup ──────────────────────────────────────────────────────────


async def _cleanup_partial_bundle_hire(db: AsyncSession, agent_ids: list[uuid.UUID]) -> None:
    """Best-effort: delete partial Agent rows + child rows (FK constraints on
    agent_permissions / participants / agent_tools / agent_agent_relationships
    are not all ON DELETE CASCADE in the production migration; explicitly delete
    children first to avoid IntegrityError during rollback).

    Tool rows themselves are tenant-shared and left in place.
    """
    if not agent_ids:
        return
    from app.models.tool import AgentTool

    try:
        # Children with FK to agents.id — delete oldest constraint first.
        # Some of these tables MAY have CASCADE in their migration, but doing it
        # explicitly is idempotent + correct regardless of FK definition.
        await db.execute(delete(AgentPermission).where(AgentPermission.agent_id.in_(agent_ids)))
        await db.execute(delete(AgentTool).where(AgentTool.agent_id.in_(agent_ids)))
        await db.execute(
            delete(AgentAgentRelationship).where(
                (AgentAgentRelationship.agent_id.in_(agent_ids))
                | (AgentAgentRelationship.target_agent_id.in_(agent_ids))
            )
        )
        await db.execute(delete(Participant).where(Participant.ref_id.in_(agent_ids)))
        await db.execute(delete(Agent).where(Agent.id.in_(agent_ids)))
        await db.commit()
        logger.info(f"[BundleHire] cleanup: removed {len(agent_ids)} agents + children")
    except Exception as exc:
        logger.error(f"[BundleHire] cleanup db delete failed: {exc}")
        try:
            await db.rollback()
        except Exception:
            pass

    for aid in agent_ids:
        try:
            workspace = _agent_dir(aid)
            if workspace.exists():
                shutil.rmtree(str(workspace), ignore_errors=True)
        except Exception as exc:
            logger.error(f"[BundleHire] cleanup fs remove failed for {aid}: {exc}")


# ── Top-level orchestrator ───────────────────────────────────────────


async def hire_bundle(
    db: AsyncSession,
    slug: str,
    user: User,
    visibility: str = "only_me",
) -> dict:
    """Atomic bundle hire. Returns a dict shaped like ``BundleHireOut`` schema.

    Raises ``BundleHireError`` (incl. ``BundleHireConflict``) on failure — the
    API layer translates these to HTTPException with appropriate status.
    """
    if visibility not in ("only_me", "company", "custom"):
        raise BundleHireError(
            f"Invalid visibility '{visibility}' (must be 'only_me', 'company', or 'custom')",
            status_code=400,
        )

    bundle = await _load_bundle(db, slug)

    # 1. Quota precheck
    from app.services.quota_guard import QuotaExceeded, check_bundle_hire_quota
    try:
        await check_bundle_hire_quota(user.id, len(bundle.agents))
    except QuotaExceeded as e:
        raise BundleHireError(e.message, status_code=403) from e

    # 2. Idempotency precheck
    await _check_idempotency(db, bundle, user)

    # 3. Resolve tenant defaults once
    tenant_defaults = await _resolve_tenant_defaults(db, user)

    # 4. Tx A — create N agents
    # Generate one group_id per hire transaction so the sidebar folds this
    # cohort under a single header. If the same tenant re-hires the same
    # bundle later, the second cohort gets a distinct group_id and folds
    # separately (intentional — they're parallel teams).
    hire_group_id = uuid.uuid4()
    principal_slug = bundle.principal_slug  # may be None — then no agent is starred
    slug_map: dict[str, uuid.UUID] = {}
    created_ids: list[uuid.UUID] = []
    created_agents: list[tuple[AgentBundleAgent, Agent]] = []
    try:
        for ba in sorted(bundle.agents, key=lambda a: a.position):
            primary_model_id = await _resolve_primary_model(
                db, user, ba.primary_model_hint, tenant_defaults["tenant_default_model_id"]
            )
            agent = await _create_agent_row(
                db, ba, user, visibility, tenant_defaults, primary_model_id,
                bundle_slug=bundle.slug,
                bundle_hire_group_id=hire_group_id,
                is_principal=(principal_slug is not None and ba.slug == principal_slug),
            )
            slug_map[ba.slug] = agent.id
            created_ids.append(agent.id)
            created_agents.append((ba, agent))
        # Commit so MCP import sessions can FK-reference the new agent rows.
        await db.commit()
        for _, agent in created_agents:
            await db.refresh(agent)
    except Exception as exc:
        await db.rollback()
        raise BundleHireError(
            f"Failed to create agents: {exc}",
            status_code=500,
        ) from exc

    # 5. Workspace setup + 5b. Tool toggle profile + 6. MCP binding + 7. Relationships — wrapped in cleanup try/except
    team_provision: dict | None = None  # 交易团队的 team token 发号;失败回滚时要 revoke
    try:
        for ba, agent in created_agents:
            await _setup_agent_workspace(db, agent, ba, bundle.slug)

        # 5b. Apply per-agent builtin-tool toggle profile snapshotted from source.
        # Without this, new agents fall back to Tool.is_default (typically "most
        # tools enabled") and don't match the source bundle's intended capability
        # scope. Non-fatal on individual tool misses — logged as warnings.
        toggles_applied_total = 0
        for ba, agent in created_agents:
            toggles_applied_total += await _apply_default_tool_toggles(
                db, agent.id, dict(ba.default_tool_toggles or {})
            )

        # 6.0 交易团队:向模拟盘发号拿 team token(未配置 SIM_PROVISION_URL → None,行为不变)。
        # 绑定一律用 mcps.yaml 的共享 url —— **绝不 per-team 覆盖共享 Tool 行**
        # (Tool.mcp_server_url 全局一行,覆盖会把所有团队都重定向,已踩过)。
        # 隔离改由 6c 的 per-agent api_key(Authorization: Bearer)实现。
        team_provision = await _provision_team_token(bundle, hire_group_id)
        binding_count = await _bind_bundle_mcps(bundle, slug_map)

        # 6b. Apply per-MCP per-tool toggle profile from source. _bind_bundle_mcps
        # leaves every discovered MCP tool with enabled=True; this flips them to
        # match the source agent's snapshot (most non-trader/non-RM agents have
        # MCP tools attached-but-disabled per 3008 admin install convention).
        mcp_toggles_applied = 0
        for ba, agent in created_agents:
            mcp_toggles_applied += await _apply_default_mcp_tool_toggles(
                db, bundle, agent.id, dict(ba.default_mcp_tool_toggles or {})
            )

        # 6c. 把 team token(加密)写进各 agent 的 AgentTool.config["api_key"]。
        # 放在 6b 之后:toggles 可能补建 AgentTool 行,先 toggle 后写 key 保证全覆盖。
        if team_provision:
            keyed = await _write_team_api_keys(db, bundle, slug_map, team_provision["token"])
            if keyed == 0:
                raise BundleHireError(
                    "team token 未写入任何 agent 工具(无匹配 Tool/AgentTool 行)—— "
                    "拒绝让团队静默落到共享账户",
                    status_code=500,
                )
            logger.info(f"[BundleHire] team api_key 已写入 {keyed} 个 AgentTool 行")

        # P2 fix: mirror api.agents.create_agent — auto-bind each new agent into
        # the OKR Agent network so OKR reporting/collection covers them.
        # Non-fatal on failure (OKR is enhancement, not core to hire).
        if user.tenant_id:
            from app.services.okr_agent_hook import hook_new_agent
            for _, agent in created_agents:
                try:
                    await hook_new_agent(db, agent.id, user.tenant_id)
                except Exception as exc:
                    logger.warning(
                        f"[BundleHire] hook_new_agent failed for {agent.id}: {exc} "
                        "(non-fatal — agent will work but won't auto-bind to OKR Agent)"
                    )

        # Open a fresh session-safe path for relationships so we don't share
        # stale identity rows with the MCP sessions above.
        rel_count = await _create_relationships(db, bundle, slug_map, user)

        # Regenerate relationships.md for each new agent (writes to agent workspace).
        from app.api.relationships import _regenerate_relationships_file
        for agent_id in slug_map.values():
            try:
                await _regenerate_relationships_file(db, agent_id)
            except Exception as exc:
                logger.warning(
                    f"[BundleHire] Failed to regen relationships.md for {agent_id}: {exc} "
                    "(non-fatal, agent file will be regenerated on next save)"
                )

        await db.commit()

        # Auto-derive on_message triggers from relationships so A2A messages
        # actually wake the recipient instead of piling up in their inbox.
        # Done AFTER the main tx commits — needs the agent rows to be
        # FK-visible and own its own session so a single bad trigger doesn't
        # roll back the bundle.
        try:
            trigger_count = await _create_default_triggers(bundle, slug_map)
            logger.info(
                f"[BundleHire] Seeded {trigger_count} on_message triggers from "
                f"{len(bundle.relationships)} relationships"
            )
            # Silent-zero guard: seeding "succeeded" but produced nothing despite
            # the bundle declaring relationships. The A2A chain still works via
            # direct-wake (send_message_to_agent notifies the recipient), so the
            # seeded triggers are only the daemon-poll fallback — but a 0-count
            # here usually means slug→agent mapping drift, which is worth seeing.
            if trigger_count == 0 and bundle.relationships:
                logger.warning(
                    f"[BundleHire] Seeded 0 on_message triggers despite "
                    f"{len(bundle.relationships)} declared relationships for bundle "
                    f"'{bundle.slug}' — check slug→agent mapping. Chain auto-wake "
                    "still works via direct send_message, but the poll fallback is "
                    "absent."
                )
        except Exception:
            trigger_count = 0
            # Use exception() so the traceback lands in error logs/alerting —
            # this used to be a quiet warning that hid real seeding bugs.
            logger.exception(
                f"[BundleHire] Default-trigger seeding FAILED for bundle "
                f"'{bundle.slug}' (non-fatal — chain still propagates via direct "
                "send_message wake, but the daemon-poll fallback won't be seeded)"
            )

    except BundleHireError:
        await db.rollback()
        await _cleanup_partial_bundle_hire(db, created_ids)
        if team_provision:
            await _revoke_team_token(team_provision["idempotency_key"])
        raise
    except Exception as exc:
        await db.rollback()
        await _cleanup_partial_bundle_hire(db, created_ids)
        if team_provision:
            await _revoke_team_token(team_provision["idempotency_key"])
        raise BundleHireError(
            f"Hire failed during workspace/mcp/relationship setup: {exc}",
            status_code=500,
        ) from exc

    # Note: per-user onboarding ritual is short-circuited at the model level
    # via Agent.is_from_bundle=True (set at agent creation above). That makes
    # mark_onboarded(hire-er) redundant AND correctly extends to other org
    # members of company-visible bundle hires — both the hire-er and any
    # later org member will skip the 4-step calibration globally.

    # Push platform default skills (is_default=True) into each NEW agent's
    # workspace ONLY. The site-wide push_default_skills_to_existing_agents()
    # runs at backend startup, but hires happening AFTER startup must
    # self-push. Crucially, a hire of N agents must NOT iterate every other
    # agent in the DB and rewrite their skill files (one user's hire would
    # touch another user's agents). push_default_skills_to_agents(ids)
    # restricts the loop to slug_map's freshly-created IDs.
    try:
        from app.services.skill_seeder import push_default_skills_to_agents
        await push_default_skills_to_agents(slug_map.values())
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            f"[BundleHire] Default-skills push failed: {exc} "
            "(non-fatal — agents will work but may miss platform sys-skills "
            "until next backend restart)"
        )

    # 8. Best-effort container start (mirror api.agents.py:470)
    try:
        from app.services.agent_manager import agent_manager
        for _, agent in created_agents:
            try:
                await agent_manager.start_container(db, agent)
            except Exception as exc:
                logger.warning(
                    f"[BundleHire] start_container failed for agent {agent.id}: {exc} "
                    "(non-fatal — agent will start on first chat)"
                )
    except Exception as exc:
        logger.warning(f"[BundleHire] container-start phase swallowed error: {exc}")

    return {
        "bundle_slug": bundle.slug,
        # The point-of-contact agent's bundle-local slug (★ in the sidebar).
        # The frontend lands the user on this agent after hire, not whichever
        # agent happens to be first in position order.
        "principal_slug": bundle.principal_slug,
        "agents": [
            {
                "agent_id": agent.id,
                "slug": ba.slug,
                "name": agent.name,
            }
            for ba, agent in created_agents
        ],
        "relationship_count": rel_count,
        "mcp_attach_count": binding_count,
        "trigger_count": trigger_count,
    }
