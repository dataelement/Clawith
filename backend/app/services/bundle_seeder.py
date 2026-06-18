"""Seed Agent Bundles from ``backend/agent_bundles/<slug>/`` folders on startup.

Each bundle folder ships:

    backend/agent_bundles/<slug>/
        bundle.yaml             # bundle-level metadata
        agents/<agent-slug>/
            meta.yaml           # name, role_description, primary_model_hint,
                                # default_skills, default_autonomy_policy,
                                # default_mcp_attach
            soul.md             # full soul markdown (verbatim)
            skills/<name>/      # optional custom skills, copied to agent
                SKILL.md          workspace at hire time
        mcps.yaml               # bundle-level MCP servers (list)
        relationships.yaml      # bundle-internal A2A graph (list, may be [])

The seeder is idempotent: re-running upserts based on (slug, is_builtin=True).
Folder presence is the source of truth; bundles not present in the filesystem
but marked builtin are removed unless an agent currently references them.
(Bundles aren't referenced by agents directly — that link is via AgentTemplate
on AgentTemplate — so we can delete obsolete builtin bundles safely.)

Pattern mirrored from ``template_seeder._load_folder_templates`` and
``seed_agent_templates`` for consistency.
"""

from pathlib import Path

import yaml
from loguru import logger
from sqlalchemy import delete, select

from app.database import async_session
from app.models.agent_bundle import (
    AgentBundle,
    AgentBundleAgent,
    AgentBundleMcpServer,
    AgentBundleRelationship,
)


# backend/app/services/bundle_seeder.py → parents[2] is backend/
_BUNDLE_ROOT = Path(__file__).resolve().parents[2] / "agent_bundles"

_REQUIRED_BUNDLE_FIELDS = {"name", "description", "icon", "category"}
_REQUIRED_AGENT_FIELDS = {"name"}
_REQUIRED_MCP_FIELDS = {"local_key", "server_name", "url"}
_REQUIRED_REL_FIELDS = {"from_slug", "to_slug"}


def _load_folder_bundles() -> list[dict]:
    """Walk ``backend/agent_bundles/<slug>/`` and load each bundle into a dict.

    Returns a list of bundle dicts shaped like the seeded row + nested
    children (``agents``, ``mcp_servers``, ``relationships``). Bundles with
    invalid layouts are skipped with a warning so a broken bundle never blocks
    startup.
    """
    if not _BUNDLE_ROOT.exists():
        return []

    bundles: list[dict] = []
    for slug_dir in sorted(p for p in _BUNDLE_ROOT.iterdir() if p.is_dir()):
        slug = slug_dir.name
        bundle_yaml = slug_dir / "bundle.yaml"
        agents_dir = slug_dir / "agents"
        mcps_yaml = slug_dir / "mcps.yaml"
        rels_yaml = slug_dir / "relationships.yaml"

        if not bundle_yaml.exists():
            logger.warning(f"[BundleSeeder] {slug}: no bundle.yaml, skipping")
            continue
        if not agents_dir.exists() or not any(agents_dir.iterdir()):
            logger.warning(f"[BundleSeeder] {slug}: no agents/ subfolder, skipping")
            continue

        try:
            bundle_meta = yaml.safe_load(bundle_yaml.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.error(f"[BundleSeeder] {slug}/bundle.yaml parse error: {exc}")
            continue

        missing = _REQUIRED_BUNDLE_FIELDS - bundle_meta.keys()
        if missing:
            logger.error(f"[BundleSeeder] {slug}: bundle.yaml missing {sorted(missing)}, skipping")
            continue

        # P2 fix: skip bundles marked is_test (or is_disabled). Local smoke-test
        # bundles can live in the folder without polluting the user's Talent Market.
        if bundle_meta.get("is_test") or bundle_meta.get("is_disabled"):
            logger.info(f"[BundleSeeder] {slug}: is_test/is_disabled set, skipping seed")
            continue

        # Load nested agents
        agents = []
        for agent_dir in sorted(p for p in agents_dir.iterdir() if p.is_dir()):
            agent_slug = agent_dir.name
            meta_path = agent_dir / "meta.yaml"
            soul_path = agent_dir / "soul.md"
            if not meta_path.exists():
                logger.warning(f"[BundleSeeder] {slug}/{agent_slug}: no meta.yaml, skipping agent")
                continue
            if not soul_path.exists():
                logger.warning(f"[BundleSeeder] {slug}/{agent_slug}: no soul.md, skipping agent")
                continue
            try:
                agent_meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                logger.error(f"[BundleSeeder] {slug}/{agent_slug}/meta.yaml parse error: {exc}")
                continue
            if _REQUIRED_AGENT_FIELDS - agent_meta.keys():
                logger.error(f"[BundleSeeder] {slug}/{agent_slug}: meta.yaml missing 'name', skipping")
                continue
            agents.append({
                "slug": agent_slug,
                "position": int(agent_meta.get("position", len(agents))),
                "name": agent_meta["name"],
                "role_description": agent_meta.get("role_description", ""),
                "soul_md": soul_path.read_text(encoding="utf-8"),
                "primary_model_hint": agent_meta.get("primary_model_hint"),
                "default_skills": list(agent_meta.get("default_skills", [])),
                "default_autonomy_policy": dict(agent_meta.get("default_autonomy_policy", {})),
                "default_mcp_attach": list(agent_meta.get("default_mcp_attach", [])),
                # Per-builtin-tool {name: enabled} snapshot from source agent.
                # Applied at hire to upsert AgentTool rows matching source state.
                "default_tool_toggles": dict(agent_meta.get("default_tool_toggles", {})),
                # Per-MCP per-tool {local_key: {mcp_tool_name: enabled}} snapshot.
                # Applied at hire after MCP binding to flip per-tool enable state.
                "default_mcp_tool_toggles": dict(agent_meta.get("default_mcp_tool_toggles", {})),
            })

        if not agents:
            logger.warning(f"[BundleSeeder] {slug}: zero valid agents, skipping bundle")
            continue

        # Load MCPs (optional)
        mcp_servers = []
        if mcps_yaml.exists():
            try:
                raw_mcps = yaml.safe_load(mcps_yaml.read_text(encoding="utf-8")) or []
            except yaml.YAMLError as exc:
                logger.error(f"[BundleSeeder] {slug}/mcps.yaml parse error: {exc}")
                raw_mcps = []
            for m in raw_mcps:
                if _REQUIRED_MCP_FIELDS - m.keys():
                    logger.warning(
                        f"[BundleSeeder] {slug}: mcp entry missing "
                        f"{sorted(_REQUIRED_MCP_FIELDS - m.keys())}, skipping"
                    )
                    continue
                mcp_servers.append({
                    "local_key": m["local_key"],
                    "server_name": m["server_name"],
                    "url": m["url"],
                    "transport": m.get("transport", "streamable-http"),
                })

        # Load relationships (optional; required to be present even if empty)
        rels = []
        if rels_yaml.exists():
            try:
                raw_rels = yaml.safe_load(rels_yaml.read_text(encoding="utf-8")) or []
            except yaml.YAMLError as exc:
                logger.error(f"[BundleSeeder] {slug}/relationships.yaml parse error: {exc}")
                raw_rels = []
            for r in raw_rels:
                if _REQUIRED_REL_FIELDS - r.keys():
                    logger.warning(
                        f"[BundleSeeder] {slug}: relationship entry missing "
                        f"{sorted(_REQUIRED_REL_FIELDS - r.keys())}, skipping"
                    )
                    continue
                rels.append({
                    "from_slug": r["from_slug"],
                    "to_slug": r["to_slug"],
                    "relation": r.get("relation", "collaborator"),
                    "description": r.get("description", ""),
                })

        # Optional English-language counterparts. Authors may ship zh-only
        # bundles (these stay None) — the frontend falls back to the primary
        # CN fields when *_en is missing.
        name_en = bundle_meta.get("name_en")
        description_en = bundle_meta.get("description_en")
        capability_bullets_en = bundle_meta.get("capability_bullets_en")
        if capability_bullets_en is not None:
            capability_bullets_en = list(capability_bullets_en)

        # Optional principal slug — references one of agents[].slug. Validated
        # below: if author named a slug that doesn't exist in agents/, log a
        # warning and drop the field rather than fail the seed (graceful).
        principal_slug = bundle_meta.get("principal_slug")
        if principal_slug is not None:
            agent_slugs = {a["slug"] for a in agents}
            if principal_slug not in agent_slugs:
                logger.warning(
                    f"[BundleSeeder] {slug}: principal_slug '{principal_slug}' "
                    f"not in agents/ ({sorted(agent_slugs)}); ignoring"
                )
                principal_slug = None

        # Author-declared content language. Defaults to "zh" (legacy bundles
        # are all CN-native). Authors of an EN-native bundle MUST set
        # ``language: en`` in bundle.yaml — there is no auto-detect.
        language = str(bundle_meta.get("language", "zh")).lower()
        if language not in {"zh", "en"}:
            logger.warning(
                f"[BundleSeeder] {slug}: language='{language}' not in (zh|en); "
                "coercing to 'zh'"
            )
            language = "zh"

        bundles.append({
            "slug": slug,
            "name": bundle_meta["name"],
            "description": bundle_meta["description"],
            "name_en": name_en,
            "description_en": description_en,
            "icon": bundle_meta["icon"],
            "category": bundle_meta["category"],
            "capability_bullets": list(bundle_meta.get("capability_bullets", [])),
            "capability_bullets_en": capability_bullets_en,
            "principal_slug": principal_slug,
            "version": str(bundle_meta.get("version", "0.1.0")),
            "language": language,
            "is_builtin": True,
            "agents": agents,
            "mcp_servers": mcp_servers,
            "relationships": rels,
        })
        logger.debug(
            f"[BundleSeeder] Loaded bundle '{slug}': {len(agents)} agents, "
            f"{len(mcp_servers)} mcp, {len(rels)} relationships"
        )

    return bundles


async def seed_agent_bundles() -> None:
    """Upsert all folder-shipped bundles into the DB. Safe to call on every startup."""
    bundles = _load_folder_bundles()

    async with async_session() as db:
        with db.no_autoflush:
            current_slugs = {b["slug"] for b in bundles}

            # Remove old builtin bundles no longer in folder (idempotent cleanup).
            # We use a bare DELETE rather than ORM delete so cascade='delete-orphan'
            # doesn't trigger lazy-load of children inside an async session
            # (which raises greenlet_spawn). FK ondelete=CASCADE in the migration
            # handles row-level child removal at the DB layer.
            existing_result = await db.execute(
                select(AgentBundle.id, AgentBundle.slug).where(
                    AgentBundle.is_builtin == True  # noqa: E712
                )
            )
            for bid, slug in existing_result.all():
                if slug not in current_slugs:
                    await db.execute(delete(AgentBundle).where(AgentBundle.id == bid))
                    logger.info(f"[BundleSeeder] Removed obsolete bundle: {slug}")

            # Upsert. We avoid ORM relationship-collection assignment (which would
            # trigger lazy-load of existing children in async). Children are
            # replaced via explicit DELETE-by-bundle_id + INSERT.
            for b in bundles:
                result = await db.execute(
                    select(AgentBundle).where(
                        AgentBundle.slug == b["slug"],
                        AgentBundle.is_builtin == True,  # noqa: E712
                    )
                )
                bundle_row = result.scalar_one_or_none()
                if bundle_row is None:
                    bundle_row = AgentBundle(
                        slug=b["slug"],
                        is_builtin=True,
                        name=b["name"],
                        description=b["description"],
                        name_en=b["name_en"],
                        description_en=b["description_en"],
                        icon=b["icon"],
                        category=b["category"],
                        capability_bullets=b["capability_bullets"],
                        capability_bullets_en=b["capability_bullets_en"],
                        principal_slug=b["principal_slug"],
                        version=b["version"],
                        language=b["language"],
                    )
                    db.add(bundle_row)
                    await db.flush()  # populate bundle_row.id for FK on children
                    created = True
                else:
                    bundle_row.name = b["name"]
                    bundle_row.description = b["description"]
                    bundle_row.name_en = b["name_en"]
                    bundle_row.description_en = b["description_en"]
                    bundle_row.icon = b["icon"]
                    bundle_row.category = b["category"]
                    bundle_row.capability_bullets = b["capability_bullets"]
                    bundle_row.capability_bullets_en = b["capability_bullets_en"]
                    bundle_row.principal_slug = b["principal_slug"]
                    bundle_row.version = b["version"]
                    bundle_row.language = b["language"]
                    # Wipe old children explicitly — bypasses ORM cascade lazy-load.
                    await db.execute(
                        delete(AgentBundleAgent).where(AgentBundleAgent.bundle_id == bundle_row.id)
                    )
                    await db.execute(
                        delete(AgentBundleMcpServer).where(AgentBundleMcpServer.bundle_id == bundle_row.id)
                    )
                    await db.execute(
                        delete(AgentBundleRelationship).where(AgentBundleRelationship.bundle_id == bundle_row.id)
                    )
                    created = False

                # Insert children via explicit FK (no relationship-collection magic).
                for a in b["agents"]:
                    db.add(AgentBundleAgent(
                        bundle_id=bundle_row.id,
                        slug=a["slug"],
                        position=a["position"],
                        name=a["name"],
                        role_description=a["role_description"],
                        soul_md=a["soul_md"],
                        primary_model_hint=a["primary_model_hint"],
                        default_skills=a["default_skills"],
                        default_autonomy_policy=a["default_autonomy_policy"],
                        default_mcp_attach=a["default_mcp_attach"],
                        default_tool_toggles=a["default_tool_toggles"],
                        default_mcp_tool_toggles=a["default_mcp_tool_toggles"],
                    ))
                for m in b["mcp_servers"]:
                    db.add(AgentBundleMcpServer(
                        bundle_id=bundle_row.id,
                        local_key=m["local_key"],
                        server_name=m["server_name"],
                        url=m["url"],
                        transport=m["transport"],
                    ))
                for r in b["relationships"]:
                    db.add(AgentBundleRelationship(
                        bundle_id=bundle_row.id,
                        from_slug=r["from_slug"],
                        to_slug=r["to_slug"],
                        relation=r["relation"],
                        description=r["description"],
                    ))

                if created:
                    logger.info(
                        f"[BundleSeeder] Created bundle '{b['slug']}': "
                        f"{len(b['agents'])} agents, {len(b['mcp_servers'])} mcp, "
                        f"{len(b['relationships'])} relationships"
                    )

            await db.commit()
            logger.info(f"[BundleSeeder] Seeded {len(bundles)} bundles")
