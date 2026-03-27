"""AgentBay API client using official SDK.

This module provides a client wrapper around the official AgentBay SDK
for browser and code execution operations.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger

from agentbay import AgentBay, BrowserOption, CreateSessionParams


@dataclass
class AgentBaySession:
    """AgentBay session info."""
    session_id: str
    image: str
    created_at: datetime
    expires_at: Optional[datetime] = None


class AgentBayClient:
    """Client for AgentBay SDK interactions."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._sdk = AgentBay(api_key=api_key)
        self._session = None
        self._image_type = None

    async def create_session(self, image: str = "linux_latest") -> AgentBaySession:
        """Create a new session using SDK.

        Closes any existing session first to prevent leaked sessions
        on the AgentBay API side.
        """
        # Close existing session to prevent leaking concurrent sessions
        if self._session:
            logger.info("[AgentBay] Closing existing session before creating new one")
            await self.close_session()

        image_id_map = {
            "browser_latest": "browser_latest",
            "code_latest": "linux_latest",
            "linux_latest": "linux_latest",
        }
        image_id = image_id_map.get(image, image)
        self._image_type = image

        result = await asyncio.to_thread(self._sdk.create, CreateSessionParams(image_id=image_id))
        if not result.success:
            raise RuntimeError(f"Failed to create session: {result.error_message}")

        self._session = result.session
        self._browser_initialized = False
        logger.info(f"[AgentBay] Created session with image {image_id}")
        return AgentBaySession(
            session_id=self._session.session_id,
            image=image,
            created_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=1),
        )

    async def close_session(self):
        """Release the current session."""
        if not self._session:
            return
        try:
            await asyncio.to_thread(self._session.delete)
            logger.info(f"[AgentBay] Closed session")
        except Exception as e:
            logger.warning(f"[AgentBay] Failed to close session: {e}")
        finally:
            self._session = None
            self._browser_initialized = False

    # ─── Browser Operations ──────────────────────────

    async def _ensure_browser_initialized(self):
        """Ensure the browser is initialized for the current session."""
        if not self._session:
            raise RuntimeError("No active browser session")
        if not getattr(self, "_browser_initialized", False):
            from agentbay import BrowserOption
            success = await asyncio.to_thread(self._session.browser.initialize, BrowserOption())
            if success is False:
                raise RuntimeError("SDK failed to initialize browser (returned False).")
            self._browser_initialized = True

    async def browser_navigate(self, url: str, wait_for: str = "", screenshot: bool = False) -> dict:
        """Navigate browser to URL using SDK."""
        if not self._session or self._image_type != "browser":
            await self.create_session("browser_latest")

        await self._ensure_browser_initialized()

        # Navigate to URL
        await asyncio.to_thread(self._session.browser.operator.navigate, url)

        result = {"url": url, "success": True, "title": url}

        if screenshot:
            screenshot_data = await asyncio.to_thread(
                self._session.browser.operator.screenshot, full_page=False
            )
            result["screenshot"] = screenshot_data

        return result

    async def browser_click(self, selector: str) -> dict:
        """Click element by CSS selector using SDK."""
        await self._ensure_browser_initialized()

        from agentbay import ActOptions
        await asyncio.to_thread(self._session.browser.operator.act, ActOptions(action=f"click on {selector}"))
        return {"success": True, "selector": selector}

    async def browser_type(self, selector: str, text: str) -> dict:
        """Type text into element using SDK."""
        await self._ensure_browser_initialized()

        from agentbay import ActOptions
        # Explicitly instruct the agent to click and use keyboard, to correctly trigger React/Vue events.
        action_msg = f"Click on the element matching '{selector}' to focus it, then use the keyboard to type the text '{text}' character by character. This ensures modern web frameworks like React register the input."
        await asyncio.to_thread(self._session.browser.operator.act, ActOptions(action=action_msg))
        return {"success": True, "selector": selector, "text": text}

    # ─── Code Operations ──────────────────────────

    async def code_execute(self, language: str, code: str, timeout: int = 30) -> dict:
        """Execute code in code space using SDK."""
        lang_map = {
            "python": "python",
            "bash": "bash",
            "shell": "bash",
            "node": "node",
            "javascript": "node",
        }
        sdk_lang = lang_map.get(language.lower(), "python")

        if not self._session or self._image_type != "code":
            await self.create_session("code_latest")

        result = await asyncio.to_thread(self._session.code.run_code, code, sdk_lang)

        return {
            "stdout": result.result if result.success else "",
            "stderr": result.error_message if not result.success else "",
            "exit_code": 0 if result.success else 1,
            "success": result.success,
        }

    # ─── Browser: Extract & Observe ───────────────────

    async def browser_extract(self, instruction: str, selector: str = "") -> dict:
        """Extract structured data from current page using natural language instruction."""
        await self._ensure_browser_initialized()

        from agentbay._common.models.browser_operator import ExtractOptions
        # Use a generic dict schema since we cannot define a Pydantic model at runtime
        options = ExtractOptions(
            instruction=instruction,
            schema=dict,
            selector=selector or None,
        )
        success, data = await asyncio.to_thread(
            self._session.browser.operator.extract, options
        )
        return {"success": success, "data": data}

    async def browser_observe(self, instruction: str, selector: str = "") -> dict:
        """Observe the current page state and return interactive elements."""
        await self._ensure_browser_initialized()

        from agentbay._common.models.browser_operator import ObserveOptions
        options = ObserveOptions(
            instruction=instruction,
            selector=selector or None,
        )
        success, results = await asyncio.to_thread(
            self._session.browser.operator.observe, options
        )
        # Convert ObserveResult objects to dicts for serialization
        result_dicts = []
        for r in (results or []):
            result_dicts.append(vars(r) if hasattr(r, "__dict__") else str(r))
        return {"success": success, "elements": result_dicts}

    # ─── Command (Shell) Operations ──────────────────

    async def command_exec(self, command: str, timeout_ms: int = 50000, cwd: str = "") -> dict:
        """Execute a shell command in the AgentBay environment."""
        if not self._session:
            await self.create_session("linux_latest")

        result = await asyncio.to_thread(
            self._session.command.exec,
            command,
            timeout_ms=timeout_ms,
            cwd=cwd or None,
        )
        return {
            "success": result.success,
            "stdout": getattr(result, "stdout", "") or getattr(result, "output", "") or "",
            "stderr": getattr(result, "stderr", "") or "",
            "exit_code": getattr(result, "exit_code", -1),
            "error_message": result.error_message or "",
        }

    # ─── Computer Operations ──────────────────────────

    async def _ensure_computer_session(self):
        """Ensure a computer (linux desktop) session is active."""
        if not self._session or self._image_type not in ("computer", "linux_latest"):
            await self.create_session("linux_latest")

    async def computer_screenshot(self) -> dict:
        """Take a screenshot of the desktop."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(self._session.computer.screenshot)
        return {
            "success": result.success,
            "data": getattr(result, "data", None),
            "error_message": result.error_message or "",
        }

    async def computer_click(self, x: int, y: int, button: str = "left") -> dict:
        """Click the mouse at coordinates (x, y)."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(self._session.computer.click_mouse, x, y, button)
        return {"success": result.success, "x": x, "y": y, "button": button}

    async def computer_input_text(self, text: str) -> dict:
        """Input text at the current cursor position."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(self._session.computer.input_text, text)
        return {"success": result.success, "text": text}

    async def computer_press_keys(self, keys: list, hold: bool = False) -> dict:
        """Press keyboard keys (e.g. ['ctrl', 'c'] for Ctrl+C)."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(self._session.computer.press_keys, keys, hold=hold)
        return {"success": result.success, "keys": keys, "hold": hold}

    async def computer_scroll(self, x: int, y: int, direction: str = "down", amount: int = 1) -> dict:
        """Scroll the screen at position (x, y)."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(
            self._session.computer.scroll, x, y, direction=direction, amount=amount
        )
        return {"success": result.success, "direction": direction, "amount": amount}

    async def computer_move_mouse(self, x: int, y: int) -> dict:
        """Move mouse to coordinates (x, y) without clicking."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(self._session.computer.move_mouse, x, y)
        return {"success": result.success, "x": x, "y": y}

    async def computer_drag_mouse(
        self, from_x: int, from_y: int, to_x: int, to_y: int, button: str = "left"
    ) -> dict:
        """Drag mouse from (from_x, from_y) to (to_x, to_y)."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(
            self._session.computer.drag_mouse, from_x, from_y, to_x, to_y, button=button
        )
        return {"success": result.success, "from": [from_x, from_y], "to": [to_x, to_y]}

    async def computer_get_screen_size(self) -> dict:
        """Get the screen resolution."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(self._session.computer.get_screen_size)
        return {
            "success": result.success,
            "data": getattr(result, "data", None),
            "error_message": result.error_message or "",
        }

    async def computer_start_app(self, cmd: str, work_dir: str = "") -> dict:
        """Start an application by its command."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(
            self._session.computer.start_app, cmd, work_directory=work_dir
        )
        return {
            "success": result.success,
            "data": getattr(result, "data", None),
            "error_message": result.error_message or "",
        }

    async def computer_get_cursor_position(self) -> dict:
        """Get current cursor position."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(self._session.computer.get_cursor_position)
        return {
            "success": result.success,
            "data": getattr(result, "data", None),
            "error_message": result.error_message or "",
        }

    async def computer_get_active_window(self) -> dict:
        """Get info about the currently active window."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(self._session.computer.get_active_window)
        window = getattr(result, "window", None)
        return {
            "success": result.success,
            "window": vars(window) if window and hasattr(window, "__dict__") else str(window),
            "error_message": result.error_message or "",
        }

    async def computer_activate_window(self, window_id: int) -> dict:
        """Activate (bring to front) a window by its ID."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(self._session.computer.activate_window, window_id)
        return {"success": result.success, "window_id": window_id}

    async def computer_list_visible_apps(self) -> dict:
        """List currently visible/running applications."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(self._session.computer.list_visible_apps)
        data = getattr(result, "data", [])
        # Convert process objects to dicts
        apps = []
        for p in (data or []):
            apps.append(vars(p) if hasattr(p, "__dict__") else str(p))
        return {
            "success": result.success,
            "apps": apps,
            "error_message": result.error_message or "",
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()


# ─── Session Cache for Tool Executions ──────────────────────────
# Key: (agent_id, image_type) so browser and code sessions coexist.
# Previously keyed by agent_id only, which caused browser session
# to be destroyed when code session was created and vice versa.

_agentbay_sessions: dict[tuple[uuid.UUID, str], tuple[AgentBayClient, datetime]] = {}
_AGENTBAY_SESSION_TIMEOUT = timedelta(minutes=5)


AGENTBAY_API_URL = "https://api.agentbay.ai/v1"


async def get_agentbay_api_key_for_agent(agent_id: uuid.UUID, db=None) -> Optional[str]:
    """Return the configured AgentBay API key for the given agent.

    Resolution order:
    1. Per-agent ChannelConfig (channel_type='agentbay') — set via Agent detail page
    2. Global Tool.config.api_key (category='agentbay') — set via Company Settings
    """
    from app.models.channel_config import ChannelConfig
    from app.models.tool import Tool
    from sqlalchemy import select
    from app.database import async_session
    from app.core.security import decrypt_data
    from app.config import get_settings

    async def _fetch(session):
        # 1) Check per-agent ChannelConfig first (highest priority)
        result = await session.execute(
            select(ChannelConfig).where(
                ChannelConfig.agent_id == agent_id,
                ChannelConfig.channel_type == "agentbay",
                ChannelConfig.is_configured == True,
            )
        )
        config = result.scalar_one_or_none()
        if config and config.app_secret:
            # Try to decrypt, fallback to plaintext if it fails
            try:
                return decrypt_data(config.app_secret, get_settings().SECRET_KEY)
            except Exception:
                return config.app_secret

        # 2) Fallback: check global Tool.config.api_key for any agentbay tool
        tool_result = await session.execute(
            select(Tool).where(
                Tool.category == "agentbay",
                Tool.enabled == True,
            ).limit(1)
        )
        tool = tool_result.scalar_one_or_none()
        if tool and tool.config and tool.config.get("api_key"):
            api_key = tool.config["api_key"]
            # Try to decrypt (global config is encrypted via _encrypt_sensitive_fields)
            try:
                return decrypt_data(api_key, get_settings().SECRET_KEY)
            except Exception:
                return api_key

        return None

    if db:
        return await _fetch(db)
    async with async_session() as session:
        return await _fetch(session)


async def test_agentbay_channel(agent_id: uuid.UUID, current_user, db) -> dict:
    """Test AgentBay connectivity."""
    key = await get_agentbay_api_key_for_agent(agent_id, db)
    if not key:
        return {"ok": False, "error": "AgentBay not configured"}
    try:
        from agentbay import AgentBay, CreateSessionParams
        sdk = AgentBay(api_key=key)
        result = await asyncio.to_thread(sdk.create, CreateSessionParams(image_id="linux_latest"))
        if result.success:
            if result.session:
                await asyncio.to_thread(result.session.delete)
            return {"ok": True, "message": "✅ Successfully connected to AgentBay API"}
        return {"ok": False, "error": result.error_message}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def get_agentbay_client_for_agent(agent_id: uuid.UUID, image_type: str) -> AgentBayClient:
    """Get or create AgentBay client for agent.

    Sessions are cached per (agent_id, image_type) so that an agent
    can hold both a browser and a code session simultaneously.
    Switching between browser and code operations no longer destroys
    the other session.
    """

    now = datetime.now()
    cache_key = (agent_id, image_type)

    if cache_key in _agentbay_sessions:
        client, last_used = _agentbay_sessions[cache_key]
        if now - last_used < _AGENTBAY_SESSION_TIMEOUT:
            # Session still valid, refresh timestamp and reuse
            _agentbay_sessions[cache_key] = (client, now)
            return client
        else:
            # Session expired, close and remove
            logger.info(f"[AgentBay] Session expired for {image_type}, closing")
            await client.close_session()
            del _agentbay_sessions[cache_key]

    from app.services.agent_tools import _get_tool_config

    tool_config = await _get_tool_config(agent_id, "agentbay_browser_navigate")
    api_key = None

    if tool_config and tool_config.get("api_key"):
        api_key = tool_config.get("api_key")
        from app.core.security import decrypt_data
        from app.config import get_settings
        try:
            api_key = decrypt_data(api_key, get_settings().SECRET_KEY)
        except Exception:
            pass  # Fallback if it's somehow plaintext
    else:
        api_key = await get_agentbay_api_key_for_agent(agent_id)

    if not api_key:
        raise RuntimeError("AgentBay not configured for this agent. Please configure in Tools > AgentBay.")

    client = AgentBayClient(api_key)

    if image_type == "browser":
        await client.create_session("browser_latest")
    elif image_type == "computer":
        await client.create_session("linux_latest")
    else:
        await client.create_session("code_latest")

    _agentbay_sessions[cache_key] = (client, now)
    return client


async def cleanup_agentbay_sessions():
    """Clean up expired AgentBay sessions."""
    now = datetime.now()
    expired = [
        cache_key for cache_key, (client, last_used) in _agentbay_sessions.items()
        if now - last_used > _AGENTBAY_SESSION_TIMEOUT
    ]
    for cache_key in expired:
        client, _ = _agentbay_sessions.pop(cache_key)
        agent_id, image_type = cache_key
        logger.info(f"[AgentBay] Cleaning up expired {image_type} session for agent {agent_id}")
        await client.close_session()