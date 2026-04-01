import asyncio
from sqlalchemy import select
from app.database import async_session_maker
from app.models.user import User
from app.api.agents import get_agent

async def main():
    async with async_session_maker() as db:
        user_res = await db.execute(select(User).where(User.username == "bisheng"))
        user = user_res.scalar_one_or_none()
        if not user:
            print("User bisheng not found!")
            return
        
        import uuid
        agent_id = uuid.UUID("4b75233d-0d26-49f9-a1ec-e90a9b7a25d6")
        
        try:
            agent_data = await get_agent(agent_id=agent_id, current_user=user, db=db)
            print("SUCCESS:", agent_data["name"], agent_data["access_level"])
        except Exception as e:
            print("FAILED:", type(e), str(e))

asyncio.run(main())
