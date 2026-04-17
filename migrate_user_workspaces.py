#!/usr/bin/env python3
"""
Migrate user workspace directories from Feishu open_id (ou_xxx) to platform user ID (UUID).

Usage: python3 migrate_user_workspaces.py
"""

import asyncio
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.org import OrgMember
from app.config import get_settings

settings = get_settings()
AGENT_DATA_DIR = Path(settings.AGENT_DATA_DIR)


async def migrate_agent(agent_id: str):
    """Migrate user directories for a single agent."""
    agent_dir = AGENT_DATA_DIR / agent_id / "users"
    
    if not agent_dir.exists():
        print(f"  Skipping {agent_id}: no users directory")
        return
    
    print(f"\nProcessing agent: {agent_id}")
    
    # Find all open_id directories (ou_xxx)
    open_id_dirs = [d for d in agent_dir.iterdir() if d.is_dir() and d.name.startswith("ou_")]
    
    if not open_id_dirs:
        print(f"  No open_id directories to migrate")
        return
    
    # Build mapping from open_id to platform user ID
    async with async_session() as db:
        # Get all org members with external_id (open_id)
        result = await db.execute(select(OrgMember))
        members = result.scalars().all()
        
        # Build mapping: open_id -> platform user_id
        open_id_to_uuid = {}
        for member in members:
            if member.external_id and member.external_id.startswith("ou_"):
                open_id_to_uuid[member.external_id] = str(member.id)
                print(f"  Mapped: {member.external_id} -> {member.id} ({member.name})")
    
    # Migrate directories
    for open_id_dir in open_id_dirs:
        open_id = open_id_dir.name
        
        # Try to find corresponding platform user
        platform_user_id = open_id_to_uuid.get(open_id)
        
        if not platform_user_id:
            print(f"  ⚠️  No platform user found for {open_id}, skipping...")
            continue
        
        # Target directory
        target_dir = agent_dir / platform_user_id
        
        if target_dir.exists():
            # Merge: move files from open_id dir to platform user dir
            print(f"  Merging {open_id} -> {platform_user_id}")
            
            for subdir in open_id_dir.iterdir():
                if subdir.is_dir():
                    # Move directory (files, sessions, etc.)
                    target_subdir = target_dir / subdir.name
                    target_subdir.mkdir(parents=True, exist_ok=True)
                    
                    for file in subdir.rglob("*"):
                        if file.is_file():
                            rel_path = file.relative_to(subdir)
                            target_file = target_subdir / rel_path
                            target_file.parent.mkdir(parents=True, exist_ok=True)
                            file.rename(target_file)
                    
                    # Remove empty source directory
                    try:
                        subdir.rmdir()
                    except:
                        pass
            
            # Remove open_id directory
            open_id_dir.rmdir()
            print(f"    ✅ Merged and removed {open_id}")
        else:
            # Rename directory
            print(f"  Renaming {open_id} -> {platform_user_id}")
            open_id_dir.rename(target_dir)
            print(f"    ✅ Renamed")


async def main():
    """Main entry point."""
    print("=" * 60)
    print("Migrating user workspace directories")
    print("From: open_id (ou_xxx)")
    print("To:   platform user ID (UUID)")
    print("=" * 60)
    
    # Find all agent directories
    if not AGENT_DATA_DIR.exists():
        print(f"Agent data directory not found: {AGENT_DATA_DIR}")
        return
    
    agent_dirs = [d for d in AGENT_DATA_DIR.iterdir() if d.is_dir()]
    
    for agent_dir in agent_dirs:
        try:
            # Validate UUID format
            import uuid
            uuid.UUID(agent_dir.name)
            await migrate_agent(agent_dir.name)
        except ValueError:
            # Not a UUID, skip
            pass
    
    print("\n" + "=" * 60)
    print("Migration complete!")
    print("=" * 60)


if __name__ == "__main__":
    from app.database import async_session
    asyncio.run(main())
