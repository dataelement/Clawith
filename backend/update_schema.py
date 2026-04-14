import asyncio
import json
from app.db.session import async_session
from sqlalchemy import select, update
from app.models.plugin_tool import PluginTool

async def main():
    async with async_session() as db:
        res = await db.execute(select(PluginTool).where(PluginTool.name == 'agentbay_computer_screenshot'))
        tool = res.scalar_one_or_none()
        if not tool:
            print("Tool not found")
            return
            
        print("Old schema:", tool.schema)
        
        new_schema = {
            "type": "function",
            "function": {
                "name": "agentbay_computer_screenshot",
                "description": "Take a screenshot of the CURRENT Windows desktop cloud computer screen. Use this to verify the result of a click, type, or to read information off the screen.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "save_to_workspace": {
                            "type": "boolean",
                            "description": "CRITICAL: Set to True IF AND ONLY IF the user explicitly asked you to SHOW them a screenshot or save it (e.g. \"截图给我看\", \"发截图\", \"保存桌面截图\"). If True, the image is saved to their workspace and you get a Markdown link. Default is False (internal in-memory analysis only, completely invisible to the user).",
                            "default": False
                        }
                    }
                }
            }
        }
        
        tool.schema = new_schema
        tool.description = new_schema["function"]["description"]
        await db.commit()
        print("Updated schema successfully.")

if __name__ == "__main__":
    asyncio.run(main())
