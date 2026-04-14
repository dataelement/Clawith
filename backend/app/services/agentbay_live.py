"""AgentBay live preview helpers.

Provides utility functions for fetching live preview data
(screenshots) from active AgentBay sessions. These are used
by the WebSocket handler to push real-time preview updates
to the frontend.

Note: get_link() (VNC URL) requires a paid AgentBay subscription
(Pro/Ultra), so we use screenshot-based preview for all environments.
"""

import uuid
from typing import Optional

from loguru import logger


async def get_desktop_screenshot(agent_id: uuid.UUID, session_id: str = "") -> Optional[str]:
    """Get a base64-encoded screenshot of an agent's active computer session.

    Uses computer_screenshot() to capture the current desktop state,
    then compresses to JPEG base64 for efficient WebSocket transfer.
    Returns data:image/jpeg;base64,... string or None on failure.

    Lookup strategy (dual fallback):
      1. Exact match: (agent_id, session_id, "computer")
      2. Fuzzy match ignoring session_id: (agent_id, *, "computer")
         This handles backward-compat and single-session callers.
    """
    from app.services.agentbay_client import _agentbay_sessions

    # Phase 1: exact match with session_id
    cache_key = (agent_id, session_id, "computer")
    if cache_key not in _agentbay_sessions:
        # Try UUID type mismatch (str vs UUID)
        for key in _agentbay_sessions:
            key_agent_id, key_sid, key_type = key
            if str(key_agent_id) == str(agent_id) and key_sid == session_id and key_type == "computer":
                cache_key = key
                break
        else:
            # Phase 2: fuzzy fallback — match any session for this agent + type
            for key in _agentbay_sessions:
                key_agent_id, _, key_type = key
                if str(key_agent_id) == str(agent_id) and key_type == "computer":
                    cache_key = key
                    logger.info(f"[AgentBay_DEBUG] Fuzzy-matched computer session for agent {agent_id}")
                    break
            else:
                logger.info(f"[AgentBay_DEBUG] No computer session for agent {agent_id}. Keys available: {list(_agentbay_sessions.keys())}")
                return None

    logger.info(f"[AgentBay_DEBUG] Found computer session for {agent_id}!")
    client, _last_used = _agentbay_sessions[cache_key]
    return await client.get_desktop_snapshot_base64()


async def get_browser_snapshot(agent_id: uuid.UUID, session_id: str = "") -> Optional[str]:
    """Get a base64-encoded screenshot of an agent's active browser session.

    Returns data:image/jpeg;base64,... string or None if no browser
    session is active or the screenshot fails.

    Lookup strategy: same dual fallback as get_desktop_screenshot.
    """
    from app.services.agentbay_client import _agentbay_sessions

    # Phase 1: exact match with session_id
    cache_key = (agent_id, session_id, "browser")
    if cache_key not in _agentbay_sessions:
        for key in _agentbay_sessions:
            key_agent_id, key_sid, key_type = key
            if str(key_agent_id) == str(agent_id) and key_sid == session_id and key_type == "browser":
                cache_key = key
                break
        else:
            # Phase 2: fuzzy fallback
            for key in _agentbay_sessions:
                key_agent_id, _, key_type = key
                if str(key_agent_id) == str(agent_id) and key_type == "browser":
                    cache_key = key
                    logger.info(f"[AgentBay_DEBUG] Fuzzy-matched browser session for agent {agent_id}")
                    break
            else:
                logger.info(f"[AgentBay_DEBUG] No browser session for agent {agent_id} (searchstr: {str(agent_id)}). Keys available: {list(_agentbay_sessions.keys())}")
                return None

    logger.info(f"[AgentBay_DEBUG] Found browser session for {agent_id}! Calling get_browser_snapshot_base64...")
    client, _last_used = _agentbay_sessions[cache_key]
    return await client.get_browser_snapshot_base64()


def detect_agentbay_env(tool_name: str) -> Optional[str]:
    """Detect which AgentBay environment a tool belongs to.

    Returns 'desktop', 'browser', 'code', or None if not an AgentBay tool.
    """
    if tool_name.startswith("agentbay_computer_"):
        return "desktop"
    if tool_name.startswith("agentbay_browser_"):
        return "browser"
    if tool_name in ("agentbay_code_execute", "agentbay_command_exec"):
        return "code"
    return None
