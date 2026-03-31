"""AgentBay Take Control API — human-agent collaborative login.

Provides REST endpoints for forwarding mouse/keyboard events to an
AgentBay session and managing the Take Control lock. When locked,
the agent's automatic browser/computer tool execution is paused to
prevent human-agent input collisions.

Cookie export occurs automatically when the Take Control session ends.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.permissions import check_agent_access
from app.core.security import encrypt_data, get_current_user
from app.database import get_db
from app.models.agent_credential import AgentCredential
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents/{agent_id}/control", tags=["agentbay-control"])


# ── In-memory Take Control lock registry ──
# Key: (agent_id_str, session_id_str) → user_id who holds the lock
_take_control_locks: dict[tuple[str, str], str] = {}


def is_session_locked(agent_id: str, session_id: str) -> bool:
    """Check if a session is currently under human Take Control.

    Called by execute_tool to block automatic agentbay_* tool calls.
    """
    return (agent_id, session_id) in _take_control_locks


# ── Request schemas ──


class ClickRequest(BaseModel):
    """Mouse click event forwarding."""
    session_id: str
    x: int
    y: int
    button: str = "left"  # left | right | middle


class TypeRequest(BaseModel):
    """Text input event forwarding."""
    session_id: str
    text: str


class PressKeysRequest(BaseModel):
    """Keyboard key press event forwarding."""
    session_id: str
    keys: list[str]  # e.g. ["ctrl", "v"] or ["Tab"]


class ScreenshotRequest(BaseModel):
    """Request an immediate screenshot."""
    session_id: str


class LockRequest(BaseModel):
    """Enter Take Control mode."""
    session_id: str
    platform_hint: Optional[str] = None  # current page domain (for cookie export)


class UnlockRequest(BaseModel):
    """Exit Take Control mode."""
    session_id: str
    export_cookies: bool = True  # whether to export cookies on exit
    platform_hint: Optional[str] = None  # domain to associate cookies with


# ── Helpers ──


async def _get_client(agent_id: uuid.UUID, session_id: str):
    """Retrieve the AgentBay client for the given agent + session."""
    from app.services.agentbay_client import get_agentbay_client_for_agent

    try:
        client = await get_agentbay_client_for_agent(
            agent_id, image_type="browser", session_id=session_id
        )
        return client
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active browser session found: {e}",
        )


# ── Endpoints ──


@router.post("/click")
async def control_click(
    agent_id: uuid.UUID,
    data: ClickRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Forward a mouse click to the AgentBay session.

    Requires the session to be in Take Control mode (locked).
    """
    _agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_session_locked(str(agent_id), data.session_id):
        raise HTTPException(status_code=400, detail="Session is not in Take Control mode")

    client = await _get_client(agent_id, data.session_id)
    try:
        # Use browser_click with coordinates
        result = await client.computer_click(data.x, data.y, button=data.button)
        return {"status": "ok", "result": str(result)[:200]}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:500]}


@router.post("/type")
async def control_type(
    agent_id: uuid.UUID,
    data: TypeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Forward text input to the AgentBay session."""
    _agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_session_locked(str(agent_id), data.session_id):
        raise HTTPException(status_code=400, detail="Session is not in Take Control mode")

    client = await _get_client(agent_id, data.session_id)
    try:
        result = await client.computer_input_text(data.text)
        return {"status": "ok", "result": str(result)[:200]}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:500]}


@router.post("/press_keys")
async def control_press_keys(
    agent_id: uuid.UUID,
    data: PressKeysRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Forward keyboard key presses to the AgentBay session."""
    _agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_session_locked(str(agent_id), data.session_id):
        raise HTTPException(status_code=400, detail="Session is not in Take Control mode")

    client = await _get_client(agent_id, data.session_id)
    try:
        result = await client.computer_press_keys(data.keys)
        return {"status": "ok", "result": str(result)[:200]}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:500]}


@router.post("/screenshot")
async def control_screenshot(
    agent_id: uuid.UUID,
    data: ScreenshotRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get an immediate screenshot from the AgentBay session.

    Returns a base64-encoded screenshot for the Take Control panel.
    """
    _agent, _access = await check_agent_access(db, current_user, agent_id)

    client = await _get_client(agent_id, data.session_id)
    try:
        screenshot_b64 = await client.get_browser_snapshot_base64()
        return {"status": "ok", "screenshot": screenshot_b64}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:500]}


@router.post("/lock")
async def control_lock(
    agent_id: uuid.UUID,
    data: LockRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Enter Take Control mode — locks the session against automatic tool execution.

    While locked, the agent's execute_tool will return a "waiting for human"
    message instead of executing browser/computer tools.
    """
    _agent, access_level = await check_agent_access(db, current_user, agent_id)
    if access_level not in ("manage",) and current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=403, detail="Manage access required")

    key = (str(agent_id), data.session_id)
    if key in _take_control_locks:
        return {"status": "already_locked", "locked_by": _take_control_locks[key]}

    _take_control_locks[key] = str(current_user.id)
    logger.info(
        f"[TakeControl] Lock acquired: agent={agent_id}, session={data.session_id}, "
        f"user={current_user.id}"
    )
    return {"status": "locked", "locked_by": str(current_user.id)}


@router.post("/unlock")
async def control_unlock(
    agent_id: uuid.UUID,
    data: UnlockRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Exit Take Control mode — unlock session and optionally export cookies.

    If export_cookies is True and platform_hint is provided, the current
    browser cookies will be exported and stored (encrypted) in the
    agent_credentials table.
    """
    _agent, _access = await check_agent_access(db, current_user, agent_id)

    key = (str(agent_id), data.session_id)
    if key not in _take_control_locks:
        return {"status": "not_locked"}

    exported = False
    export_count = 0

    # Export cookies if requested
    if data.export_cookies and data.platform_hint:
        try:
            client = await _get_client(agent_id, data.session_id)
            export_count = await _export_cookies_from_session(
                client, agent_id, data.platform_hint, db
            )
            exported = True
            logger.info(
                f"[TakeControl] Cookies exported: agent={agent_id}, "
                f"platform={data.platform_hint}, count={export_count}"
            )
        except Exception as e:
            logger.warning(f"[TakeControl] Cookie export failed: {e}")

    # Release the lock
    del _take_control_locks[key]
    logger.info(
        f"[TakeControl] Lock released: agent={agent_id}, session={data.session_id}"
    )

    return {
        "status": "unlocked",
        "cookies_exported": exported,
        "cookie_count": export_count,
    }


async def _export_cookies_from_session(
    client, agent_id: uuid.UUID, platform_hint: str, db: AsyncSession
) -> int:
    """Export cookies from the current browser session via CDP and store encrypted.

    Uses Playwright's connectOverCDP to read all browser cookies, then upserts
    into the agent_credentials table for the matching platform.

    Returns the number of cookies exported.
    """
    # Build and execute a Node.js script to export cookies via CDP
    export_script = """
const { chromium } = require('playwright');
(async () => {
    try {
        const browser = await chromium.connectOverCDP('http://localhost:9222');
        const context = browser.contexts()[0];
        const cookies = await context.cookies();
        console.log('COOKIES_EXPORT:' + JSON.stringify(cookies));
    } catch (e) {
        console.error('EXPORT_FAIL:' + e.message);
    }
})();
"""
    # Write script to temp file to avoid shell quoting issues
    await client.command_exec("cat > /tmp/_export_cookies.js << 'SCRIPT_EOF'\n" + export_script + "\nSCRIPT_EOF")
    result = await client.command_exec("node /tmp/_export_cookies.js", timeout_ms=15000)
    stdout = result.get("stdout", "")

    if "COOKIES_EXPORT:" not in stdout:
        logger.warning(f"[TakeControl] Cookie export script failed: {stdout}")
        return 0

    # Parse the exported cookies JSON
    cookies_line = [line for line in stdout.split("\n") if "COOKIES_EXPORT:" in line]
    if not cookies_line:
        return 0

    cookies_json_str = cookies_line[0].split("COOKIES_EXPORT:", 1)[1].strip()
    try:
        cookies = json.loads(cookies_json_str)
    except json.JSONDecodeError:
        logger.warning("[TakeControl] Failed to parse exported cookies JSON")
        return 0

    if not cookies:
        return 0

    # Encrypt and store
    settings = get_settings()
    encrypted_cookies = encrypt_data(cookies_json_str, settings.SECRET_KEY)

    # Try to find existing credential for this platform
    result = await db.execute(
        select(AgentCredential).where(
            AgentCredential.agent_id == agent_id,
            AgentCredential.platform == platform_hint,
        )
    )
    existing = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if existing:
        # Update existing credential
        existing.cookies_json = encrypted_cookies
        existing.cookies_updated_at = now
        existing.last_login_at = now
        existing.status = "active"
    else:
        # Create new credential
        new_cred = AgentCredential(
            agent_id=agent_id,
            credential_type="website",
            platform=platform_hint,
            display_name=platform_hint,
            cookies_json=encrypted_cookies,
            cookies_updated_at=now,
            last_login_at=now,
            status="active",
        )
        db.add(new_cred)

    await db.commit()
    return len(cookies)
