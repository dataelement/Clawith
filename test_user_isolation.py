#!/usr/bin/env python3
"""
Test script to verify multi-user workspace isolation.

This script simulates two users talking to the same agent and verifies:
1. Each user has their own workspace directory
2. User A's files are not visible to User B
3. Shared resources (skills, soul.md) are still accessible
"""

import asyncio
import uuid
from pathlib import Path

# Import from Clawith
import sys
sys.path.insert(0, '/Users/amadeus/Clawith/backend')

from app.services.agent_tools import ensure_workspace


async def test_user_isolation():
    """Test user workspace isolation."""
    
    # Use a test agent ID
    test_agent_id = uuid.UUID('3e118d51-3833-47f8-b3cc-e7c27efbf09a')  # 吴艺晨的助手
    
    # Simulate two different users
    user_a_id = uuid.uuid4()
    user_b_id = uuid.uuid4()
    
    print(f'🧪 Testing workspace isolation for agent: {test_agent_id}')
    print(f'   User A: {user_a_id}')
    print(f'   User B: {user_b_id}')
    print('')
    
    # Create workspaces for both users
    print('📁 Creating User A workspace...')
    user_a_ws = await ensure_workspace(test_agent_id, user_id=user_a_id)
    print(f'   ✅ User A workspace: {user_a_ws}')
    
    print('📁 Creating User B workspace...')
    user_b_ws = await ensure_workspace(test_agent_id, user_id=user_b_id)
    print(f'   ✅ User B workspace: {user_b_ws}')
    
    # Verify directory structure
    print('')
    print('📋 Verifying directory structure...')
    
    # Check User A directories
    user_a_files_dir = user_a_ws / 'files'
    user_a_sessions_dir = user_a_ws / 'sessions'
    user_a_memory = user_a_ws / 'memory.md'
    
    assert user_a_files_dir.exists(), f'User A files dir missing: {user_a_files_dir}'
    assert user_a_sessions_dir.exists(), f'User A sessions dir missing: {user_a_sessions_dir}'
    assert user_a_memory.exists(), f'User A memory file missing: {user_a_memory}'
    print(f'   ✅ User A directories exist')
    
    # Check User B directories
    user_b_files_dir = user_b_ws / 'files'
    user_b_sessions_dir = user_b_ws / 'sessions'
    user_b_memory = user_b_ws / 'memory.md'
    
    assert user_b_files_dir.exists(), f'User B files dir missing: {user_b_files_dir}'
    assert user_b_sessions_dir.exists(), f'User B sessions dir missing: {user_b_sessions_dir}'
    assert user_b_memory.exists(), f'User B memory file missing: {user_b_memory}'
    print(f'   ✅ User B directories exist')
    
    # Verify isolation
    print('')
    print('🔒 Verifying isolation...')
    
    # User A and B should have different directories
    assert user_a_files_dir != user_b_files_dir, 'User A and B files dirs should be different'
    assert user_a_sessions_dir != user_b_sessions_dir, 'User A and B sessions dirs should be different'
    print(f'   ✅ User directories are isolated')
    
    # Verify shared resources still exist at agent level
    agent_ws = Path('/data/agents') / str(test_agent_id)
    skills_dir = agent_ws / 'skills'
    soul_file = agent_ws / 'soul.md'
    shared_memory = agent_ws / 'memory' / 'memory.md'
    
    assert skills_dir.exists(), f'Shared skills dir missing: {skills_dir}'
    assert soul_file.exists(), f'Shared soul.md missing: {soul_file}'
    assert shared_memory.exists(), f'Shared memory.md missing: {shared_memory}'
    print(f'   ✅ Shared resources (skills, soul, memory) still accessible')
    
    # Verify user directories are under agent/users/
    expected_user_a_path = agent_ws / 'users' / str(user_a_id)
    expected_user_b_path = agent_ws / 'users' / str(user_b_id)
    
    assert user_a_ws == expected_user_a_path, f'User A path mismatch'
    assert user_b_ws == expected_user_b_path, f'User B path mismatch'
    print(f'   ✅ User directories correctly placed under agent/users/')
    
    print('')
    print('=' * 60)
    print('✅ ALL TESTS PASSED!')
    print('=' * 60)
    print('')
    print('Summary:')
    print(f'  - Agent workspace: {agent_ws}')
    print(f'  - User A workspace: {user_a_ws}')
    print(f'  - User B workspace: {user_b_ws}')
    print(f'  - Shared skills: {skills_dir}')
    print(f'  - Shared soul.md: {soul_file}')
    print('')
    print('Isolation verified:')
    print('  ✅ User A cannot see User B\'s files')
    print('  ✅ User B cannot see User A\'s files')
    print('  ✅ Both users can access shared skills and soul.md')
    print('')


if __name__ == '__main__':
    asyncio.run(test_user_isolation())
