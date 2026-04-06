import asyncio
from sqlalchemy.future import select
from sqlalchemy import delete
from app.db.session import async_session_maker
from app.models.tool import Tool

async def run():
    async with async_session_maker() as session:
        result = await session.execute(select(Tool).where(Tool.name == "generate_image"))
        tool = result.scalar_one_or_none()
        if tool:
            await session.execute(
                f"DELETE FROM agent_tools WHERE tool_id = '{tool.id}'"
            )
            await session.delete(tool)
            await session.commit()
            print("Successfully deleted old 'generate_image' tool and its references.")
        else:
            print("Tool 'generate_image' not found in database.")

if __name__ == "__main__":
    asyncio.run(run())
