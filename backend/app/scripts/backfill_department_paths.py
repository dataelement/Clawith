"""Backfill department paths from the department tree and refresh member paths.

Usage:
  Docker: docker exec clawith-backend-1 python3 -m app.scripts.backfill_department_paths
  Source: cd backend && python3 -m app.scripts.backfill_department_paths
"""

import asyncio

from loguru import logger


async def main():
    from app.database import async_session
    from app.models import (  # noqa: F401
        activity_log, agent, audit, channel_config, chat_session,
        gateway_message, identity, invitation_code, llm, notification, org,
        participant, plaza, schedule, skill, system_settings, task,
        tenant, tenant_setting, tool, trigger, user,
    )
    from app.models.identity import IdentityProvider
    from app.models.org import OrgDepartment, OrgMember
    from app.services.org_sync_adapter import build_department_path_map
    from sqlalchemy import select

    async with async_session() as db:
        provider_result = await db.execute(select(IdentityProvider.id))
        provider_ids = [pid for pid in provider_result.scalars().all()]
        logger.info(f"Found {len(provider_ids)} providers to backfill")

        updated_depts = 0
        updated_members = 0

        for provider_id in provider_ids:
            dept_result = await db.execute(
                select(OrgDepartment).where(OrgDepartment.provider_id == provider_id)
            )
            departments = dept_result.scalars().all()
            if not departments:
                continue

            path_map = build_department_path_map(departments)
            for dept in departments:
                new_path = path_map.get(dept.id, (dept.name or "").strip())
                if dept.path != new_path:
                    dept.path = new_path
                    updated_depts += 1

            member_result = await db.execute(
                select(OrgMember).where(OrgMember.provider_id == provider_id)
            )
            members = member_result.scalars().all()
            for member in members:
                new_path = path_map.get(member.department_id, "") if member.department_id else ""
                if member.department_path != new_path:
                    member.department_path = new_path
                    updated_members += 1

        await db.commit()
        logger.info(
            f"Department path backfill complete. Updated {updated_depts} departments and {updated_members} members."
        )


if __name__ == "__main__":
    asyncio.run(main())
