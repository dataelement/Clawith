"""One-off cleanup: revoke the deprecated Plaza social tools from all agents.

Batch 1 of the Plaza → experience library改造 stops seeding and dispatching the
`plaza_*` tools, but agents provisioned earlier still carry the authorization
rows. Run this once (ops-owned) to disable them globally and detach them from
agents so no agent can auto-post anymore.

    python -m app.scripts.disable_plaza_social_tools

Idempotent — safe to re-run.
"""

import asyncio

from sqlalchemy import delete, select, update

from app.database import async_session
from app.models.tool import Tool, AgentTool

DEPRECATED_TOOLS = ("plaza_get_new_posts", "plaza_create_post", "plaza_add_comment")


async def main() -> None:
    async with async_session() as db:
        tool_ids = (
            await db.execute(select(Tool.id).where(Tool.name.in_(DEPRECATED_TOOLS)))
        ).scalars().all()
        if not tool_ids:
            print("No plaza_* tools found; nothing to do.")
            return

        detached = (
            await db.execute(delete(AgentTool).where(AgentTool.tool_id.in_(tool_ids)))
        ).rowcount
        await db.execute(
            update(Tool)
            .where(Tool.id.in_(tool_ids))
            .values(enabled=False, is_default=False)
        )
        await db.commit()
        print(f"Disabled {len(tool_ids)} plaza tool(s); detached {detached} agent authorization row(s).")


if __name__ == "__main__":
    asyncio.run(main())
