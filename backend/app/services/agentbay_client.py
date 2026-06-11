"""AgentBay API client using official SDK.

This module provides a client wrapper around the official AgentBay SDK
for browser and code execution operations.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shlex
import uuid
from io import BytesIO
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional
from loguru import logger

from agentbay import AgentBay, CreateSessionParams
from app.core.logging_config import _disable_agentbay_logger_override, configure_logging

_disable_agentbay_logger_override()
configure_logging()


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
            "windows_latest": "windows_latest",
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
            logger.info("[AgentBay] Closed session")
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
            from agentbay._common.models.browser import BrowserViewport, BrowserScreen
            
            # Use high-res viewport for clearer screenshots and better layout
            options = BrowserOption(
                viewport=BrowserViewport(width=1920, height=1080),
                screen=BrowserScreen(width=1920, height=1080)
            )
            success = await asyncio.to_thread(self._session.browser.initialize, options)
            if success is False:
                raise RuntimeError("SDK failed to initialize browser (returned False).")
            self._browser_initialized = True

    async def browser_navigate(self, url: str, wait_for: str = "", screenshot: bool = False) -> dict:
        """Navigate browser to URL using SDK.

        The AgentBay SDK default navigation timeout is ~60 s. We wrap the call
        with a 40-second asyncio soft-timeout so callers receive an actionable
        error quickly rather than hanging the whole agent loop. The underlying
        SDK thread may continue briefly in the background but its result is
        discarded — the browser will eventually settle on its own.
        """
        if not self._session or self._image_type not in ("browser", "browser_latest"):
            await self.create_session("browser_latest")

        await self._ensure_browser_initialized()

        # Navigate to URL with a 40-second soft timeout.
        # asyncio.wait_for cancels the coroutine wrapper; the blocking thread
        # inside asyncio.to_thread keeps running until SDK returns, but we
        # no longer block the agent loop waiting for it.
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._session.browser.operator.navigate, url),
                timeout=40.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[AgentBay] navigate to {url!r} timed out after 40 s")
            raise RuntimeError(
                f"Navigation to '{url}' timed out (>40 s). "
                "The browser may be busy or the page is unreachable. "
                "Try calling agentbay_browser_screenshot to check the current "
                "state, or retry the navigation."
            )

        result = {"url": url, "success": True, "title": url}

        if screenshot:
            # Wait for dynamic content and SPA rendering (React/Vue) before screenshotting
            await asyncio.sleep(3)
            screenshot_data = await asyncio.to_thread(
                self._session.browser.operator.screenshot, full_page=False
            )
            result["screenshot"] = screenshot_data

        return result

    async def browser_screenshot(self) -> dict:
        """Take a screenshot of the current browser page without navigating.

        Use this after actions (click, type, form submit) to verify results
        without refreshing the page. Never call browser_navigate just to screenshot.
        """
        await self._ensure_browser_initialized()
        
        # Wait for dynamic content and SPA rendering before screenshotting
        await asyncio.sleep(3)
        
        screenshot_data = await asyncio.to_thread(
            self._session.browser.operator.screenshot, full_page=False
        )
        return {"success": True, "screenshot": screenshot_data}


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

        # Detect OTP/PIN-style inputs: short digit-only strings (4-8 chars)
        # These use segmented input boxes that auto-advance focus per digit,
        # so character-by-character typing often fails. Use paste strategy instead.
        is_otp = text.isdigit() and 4 <= len(text) <= 8

        if is_otp:
            action_msg = (
                f"The text '{text}' appears to be a verification/OTP code. "
                f"Find the verification code input area near '{selector}'. "
                f"Click on the first input box, then paste or type the full code '{text}'. "
                f"If the input is split into individual digit boxes, click the first box "
                f"and type each digit one at a time: {', '.join(text)}. "
                f"Each box should auto-advance to the next after entering a digit."
            )
        else:
            # Standard input: click to focus, then type character by character
            # to correctly trigger React/Vue input events.
            action_msg = (
                f"Click on the element matching '{selector}' to focus it, "
                f"then use the keyboard to type the text '{text}' character by character. "
                f"This ensures modern web frameworks like React register the input."
            )

        await asyncio.to_thread(self._session.browser.operator.act, ActOptions(action=action_msg))
        return {"success": True, "selector": selector, "text": text}

    async def browser_cdp_click(self, agent_id: uuid.UUID, instruction: str) -> dict:
        """Use Gemini visual grounding to click a natural-language target via CDP."""
        await self._ensure_browser_initialized()
        plan = await self._ground_browser_target_with_gemini(
            agent_id=agent_id,
            instruction=instruction,
            action="click",
        )
        try:
            result = await self._run_browser_cdp_action(
                {
                    "action": "click",
                    "x": plan["x"],
                    "y": plan["y"],
                }
            )
            return {**plan, "success": True, "cdp_result": result}
        except Exception as exc:
            return {**plan, "success": False, "error": str(exc)}

    async def browser_cdp_type(self, agent_id: uuid.UUID, instruction: str, text: str, replace: bool = True) -> dict:
        """Use Gemini visual grounding to type into a natural-language target via CDP."""
        await self._ensure_browser_initialized()
        plan = await self._ground_browser_target_with_gemini(
            agent_id=agent_id,
            instruction=instruction,
            action="type",
        )
        try:
            result = await self._run_browser_cdp_action(
                {
                    "action": "type",
                    "x": plan["x"],
                    "y": plan["y"],
                    "text": text,
                    "replace": replace,
                    "delay": 20,
                }
            )
            return {**plan, "success": True, "text": text, "replace": replace, "cdp_result": result}
        except Exception as exc:
            return {**plan, "success": False, "text": text, "replace": replace, "error": str(exc)}

    async def _ground_browser_target_with_gemini(
        self,
        *,
        agent_id: uuid.UUID,
        instruction: str,
        action: str,
    ) -> dict:
        """Ground a natural-language browser action target to pixel coordinates."""
        screenshot_data = await asyncio.to_thread(
            self._session.browser.operator.screenshot, full_page=False
        )
        mime_type, b64_data, width, height = self._parse_screenshot_data_url(screenshot_data)
        grounding = await _gemini_ground_browser_target(
            agent_id=agent_id,
            image_mime_type=mime_type,
            image_base64=b64_data,
            image_width=width,
            image_height=height,
            action=action,
            instruction=instruction,
        )

        if _grounding_target_not_found(grounding):
            raise RuntimeError(_grounding_not_found_message(instruction, grounding))

        box = grounding.get("box_2d")
        if not (isinstance(box, list) and len(box) == 4):
            raise RuntimeError(f"Gemini grounding did not return a valid box_2d: {grounding}")

        ymin, xmin, ymax, xmax, x, y = _normalized_box_center_to_pixel(box, width, height)

        return {
            "instruction": instruction,
            "action": action,
            "screenshot": screenshot_data,
            "box_2d": [int(round(v)) for v in (ymin, xmin, ymax, xmax)],
            "x": x,
            "y": y,
            "image_width": width,
            "image_height": height,
            "target": grounding.get("target") or grounding.get("label") or "",
            "confidence": grounding.get("confidence"),
            "reason": grounding.get("reason") or "",
        }

    def _parse_screenshot_data_url(self, screenshot_data: str) -> tuple[str, str, int, int]:
        """Return (mime_type, base64, width, height) from an AgentBay screenshot data URL."""
        match = re.match(r"^data:([^;]+);base64,(.+)$", screenshot_data or "", re.DOTALL)
        if not match:
            raise RuntimeError("AgentBay screenshot did not return a base64 data URL")
        mime_type = match.group(1)
        b64_data = match.group(2).strip()
        try:
            raw = base64.b64decode(b64_data)
            from PIL import Image

            with Image.open(BytesIO(raw)) as image:
                width, height = image.size
        except Exception as exc:
            raise RuntimeError(f"Could not decode AgentBay screenshot: {exc}") from exc
        if width <= 0 or height <= 0:
            raise RuntimeError(f"Invalid AgentBay screenshot size: {width}x{height}")
        return mime_type, b64_data, width, height

    async def _run_browser_cdp_action(self, payload: dict[str, Any]) -> dict:
        """Run a Playwright CDP action inside the AgentBay browser session."""
        script_name = f"clawith_cdp_action_{uuid.uuid4().hex}.js"
        script = _build_browser_cdp_action_script()
        script_b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
        payload_b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")

        write_cmd = f"printf %s {shlex.quote(script_b64)} | /usr/bin/base64 -d > {shlex.quote(script_name)}"
        write_result = await asyncio.to_thread(self._session.command.exec, write_cmd, timeout_ms=15000)
        if not getattr(write_result, "success", False):
            stderr = getattr(write_result, "stderr", "") or getattr(write_result, "error_message", "")
            raise RuntimeError(f"Failed to write CDP action script in AgentBay: {stderr}")

        run_cmd = f"node {shlex.quote(script_name)} {shlex.quote(payload_b64)}"
        run_result = await asyncio.to_thread(self._session.command.exec, run_cmd, timeout_ms=30000)
        stdout = getattr(run_result, "stdout", "") or getattr(run_result, "output", "") or ""
        stderr = getattr(run_result, "stderr", "") or ""
        cleanup_cmd = f"rm -f {shlex.quote(script_name)}"
        try:
            await asyncio.to_thread(self._session.command.exec, cleanup_cmd, timeout_ms=5000)
        except Exception:
            pass

        return _parse_cdp_action_result(run_result)

    async def browser_login(self, url: str, login_config: str) -> dict:
        """Perform an automated login using AgentBay's built-in login skill.

        This leverages AgentBay's AI-driven login capability to handle complex
        login flows including CAPTCHAs, OTP inputs, and multi-step authentication.

        Args:
            url: The login page URL to navigate to first.
            login_config: JSON string with login configuration, e.g.
                          '{"api_key": "xxx", "skill_id": "yyy"}'
        """
        if not self._session or self._image_type != "browser":
            await self.create_session("browser_latest")
        await self._ensure_browser_initialized()

        # Navigate to the login page first
        await asyncio.to_thread(self._session.browser.operator.navigate, url)

        # Execute the login skill
        result = await asyncio.to_thread(
            self._session.browser.operator.login,
            login_config,
            use_vision=True,
        )
        return {
            "success": result.success,
            "message": result.message or "",
        }

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

        if not self._session or self._image_type not in ("code", "code_latest"):
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
        
        # Wait for dynamic content and SPA rendering before extracting
        await asyncio.sleep(3)

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
        
        # Wait for dynamic content and SPA rendering before observing
        await asyncio.sleep(3)

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
        """Ensure a computer (linux or windows desktop) session is active."""
        if not self._session or self._image_type not in ("computer", "linux_latest", "windows_latest"):
            await self.create_session("linux_latest")

    async def computer_screenshot(self) -> dict:
        """Take a screenshot of the desktop.

        Tries the standard screenshot() API first, then falls back to
        beta_take_screenshot() for cloud environments that don't support
        the standard API yet.
        """
        await self._ensure_computer_session()
        
        # Wait briefly for UI animations/rendering to settle
        await asyncio.sleep(2)

        try:
            result = await asyncio.to_thread(self._session.computer.screenshot)
            # Some cloud environments return success=False with a message
            # telling us to use beta_take_screenshot() instead of throwing.
            if not result.success and "beta_take_screenshot" in (result.error_message or ""):
                logger.info("[AgentBay] screenshot() unsupported, falling back to beta_take_screenshot()")
                result = await asyncio.to_thread(self._session.computer.beta_take_screenshot)
        except Exception as e:
            # Also handle the case where it raises an exception
            if "beta_take_screenshot" in str(e):
                logger.info("[AgentBay] Falling back to beta_take_screenshot() after exception")
                result = await asyncio.to_thread(self._session.computer.beta_take_screenshot)
            else:
                raise
        return {
            "success": result.success,
            "data": getattr(result, "data", None),
            "error_message": result.error_message or "",
        }

    async def computer_click(self, x: int, y: int, button: str = "left") -> dict:
        """Click the mouse at coordinates (x, y)."""
        await self._ensure_computer_session()
        move_result = await asyncio.to_thread(self._session.computer.move_mouse, x, y)
        result = await asyncio.to_thread(self._session.computer.click_mouse, x, y, button)
        return {
            "success": result.success,
            "moved": getattr(move_result, "success", False),
            "x": x,
            "y": y,
            "button": button,
        }

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

    async def computer_get_installed_apps(
        self,
        start_menu: bool = True,
        desktop: bool = True,
        ignore_system_apps: bool = True,
    ) -> dict:
        """List installed applications and their launch commands."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(
            self._session.computer.get_installed_apps,
            start_menu,
            desktop,
            ignore_system_apps,
        )
        apps = []
        for app in (getattr(result, "data", None) or []):
            apps.append(vars(app) if hasattr(app, "__dict__") else str(app))
        return {
            "success": result.success,
            "apps": apps,
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

    async def computer_list_windows(self, timeout_ms: int = 3000) -> dict:
        """List root desktop windows with IDs and geometry."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(self._session.computer.list_root_windows, timeout_ms)
        windows = []
        for window in (getattr(result, "windows", None) or []):
            windows.append(vars(window) if hasattr(window, "__dict__") else str(window))
        return {
            "success": result.success,
            "windows": windows,
            "error_message": result.error_message or "",
        }

    async def computer_activate_window(self, window_id: int) -> dict:
        """Activate (bring to front) a window by its ID."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(self._session.computer.activate_window, window_id)
        return {"success": result.success, "window_id": window_id}

    async def computer_close_window(self, window_id: int) -> dict:
        """Close a desktop window by its ID."""
        await self._ensure_computer_session()
        result = await asyncio.to_thread(self._session.computer.close_window, window_id)
        return {
            "success": result.success,
            "window_id": window_id,
            "error_message": result.error_message or "",
        }

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

    # ─── Live Preview Support ──────────────────────────

    async def get_live_url(self) -> str | None:
        """Get the VNC/viewer URL for the current computer session.

        Calls session.get_link() which returns a shareable viewer URL
        for the cloud desktop. Returns None if no session is active
        or the API call fails.
        """
        if not self._session:
            return None
        try:
            result = await asyncio.to_thread(self._session.get_link)
            if result.success and result.data:
                logger.info(f"[AgentBay] Got live URL: {str(result.data)[:80]}...")
                return result.data
            logger.warning(f"[AgentBay] get_link() failed: {result.error_message}")
            return None
        except Exception as e:
            logger.warning(f"[AgentBay] Failed to get live URL: {e}")
            return None

    async def get_desktop_snapshot_base64(self) -> str | None:
        """Take a quick desktop screenshot and return compressed base64 JPEG.

        Used for live preview panel. Calls the same screenshot API as
        computer_screenshot() but without the sleep delay, and compresses
        the result for efficient WebSocket transfer.
        Returns data:image/jpeg;base64,... or None on failure.
        """
        if not self._session:
            return None
        try:
            # Use the same screenshot logic as computer_screenshot()
            try:
                result = await asyncio.to_thread(self._session.computer.screenshot)
                if not result.success and "beta_take_screenshot" in (result.error_message or ""):
                    result = await asyncio.to_thread(self._session.computer.beta_take_screenshot)
            except Exception as e:
                if "beta_take_screenshot" in str(e):
                    result = await asyncio.to_thread(self._session.computer.beta_take_screenshot)
                else:
                    raise

            screenshot_data = getattr(result, "data", None)
            if not screenshot_data:
                return None

            # Compress to JPEG base64 for live preview
            import base64
            from io import BytesIO
            from PIL import Image

            img = Image.open(BytesIO(screenshot_data))
            # Resize to max 1920px wide for live preview (up from 1280px to preserve details)
            if img.width > 1920:
                ratio = 1920 / img.width
                img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=80, optimize=True)
            b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"
        except Exception as e:
            logger.warning(f"[AgentBay] Desktop snapshot failed: {e}")
            return None

    async def get_browser_snapshot_base64(self) -> str | None:
        """Take a quick browser screenshot and return compressed base64 JPEG.

        Used for live preview panel — no wait/sleep since we want
        the snapshot to reflect the current state immediately.
        Returns data:image/jpeg;base64,... or None on failure.
        """
        if not self._session:
            logger.info("[AgentBay] Browser snapshot skipped: No active session")
            return None
        if not getattr(self, "_browser_initialized", False):
            logger.info("[AgentBay] Browser snapshot skipped: Browser not initialized")
            return None
        
        try:
            screenshot_data = await asyncio.to_thread(
                self._session.browser.operator.screenshot, full_page=False
            )
            if not screenshot_data:
                logger.info("[AgentBay] Browser snapshot returned empty data")
                return None

            # Compress screenshot to JPEG base64 for efficient transfer
            import base64
            from io import BytesIO
            from PIL import Image

            if isinstance(screenshot_data, str):
                # The AgentBay SDK may return a raw base64 string without proper
                # padding. Normalize by stripping whitespace and adding padding chars.
                screenshot_data = screenshot_data.strip()
                # Remove data URI prefix if present (e.g., "data:image/png;base64,")
                if "," in screenshot_data:
                    screenshot_data = screenshot_data.split(",", 1)[1]
                # Add base64 padding if missing
                missing_padding = len(screenshot_data) % 4
                if missing_padding:
                    screenshot_data += "=" * (4 - missing_padding)
                screenshot_data = base64.b64decode(screenshot_data)


            img = Image.open(BytesIO(screenshot_data))
            # Resize to max 1920px wide for live preview (up from 1280px to preserve details)
            if img.width > 1920:
                ratio = 1920 / img.width
                img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=80, optimize=True)
            b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"
        except Exception as e:
            logger.warning(f"[AgentBay] Browser snapshot failed: {e}")
            return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()


async def _gemini_ground_browser_target(
    *,
    agent_id: uuid.UUID,
    image_mime_type: str,
    image_base64: str,
    image_width: int,
    image_height: int,
    action: str,
    instruction: str,
) -> dict[str, Any]:
    """Call an OpenAI-compatible Gemini endpoint for visual grounding."""
    api_key, base_url, model_name = await _resolve_grounding_openai_config(agent_id, action)
    action_hint = (
        "clickable element/button/link/control"
        if action == "click"
        else "editable input/textarea/search box/content field"
    )
    prompt = f"""
You are a precise browser UI visual grounding engine.

Task:
- Find the single best {action_hint} for this action: {action}
- User natural-language target: {instruction}

Gemini visual grounding coordinate rules:
- Return a bounding box in normalized image coordinates from 0 to 1000.
- The box order MUST be [ymin, xmin, ymax, xmax].
- The box should tightly cover the actionable target. Prefer the smallest actual clickable/editable element.
- For text input, target the editable field itself, not just its label.
- If the target is partially visible, box the visible actionable region.
- If the requested target is not visible on the page, or the instruction is too ambiguous to identify one target, do NOT guess and do NOT return a fake box. Instead set found to false, set box_2d to null, summarize the visible page content, and ask for a clearer instruction.

Screenshot size for reference: {image_width}x{image_height} pixels.

Return ONLY valid JSON with this exact shape:
{{
  "found": true,
  "target": "short description of the selected UI target, or empty string when not found",
  "box_2d": [ymin, xmin, ymax, xmax],
  "confidence": 0.0,
  "reason": "short reason",
  "page_content": "brief summary of visible page content when not found",
  "clarification": "what clearer instruction is needed when not found"
}}
When found is false, box_2d MUST be null.
""".strip()

    import httpx

    if _is_gemini_native_models_base_url(base_url):
        try:
            payload = _build_gemini_native_grounding_payload(
                prompt=prompt,
                image_mime_type=image_mime_type,
                image_base64=image_base64,
            )
            url = _gemini_native_generate_content_url(base_url, model_name)
            async with httpx.AsyncClient(timeout=45.0, follow_redirects=True, proxy=None) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": api_key,
                    },
                )
            if response.status_code >= 400:
                raise RuntimeError(f"Grounding Gemini native API HTTP {response.status_code}: {response.text[:500]}")
            native_data = response.json()
            native_result = _normalize_grounding_result(
                _parse_grounding_json(
                    _extract_gemini_native_content(native_data) or "",
                    response_summary=_summarize_gemini_native_response(native_data),
                )
            )
            if not _grounding_result_has_usable_target(native_result):
                raise RuntimeError(f"Grounding Gemini native API returned unusable target: {native_result}")
            return native_result
        except Exception as exc:
            fallback_base_url = _fallback_openai_base_url_from_native_models_base_url(base_url)
            if not fallback_base_url:
                raise
            logger.warning(
                "[AgentBay] Gemini native grounding failed; falling back to OpenAI-compatible endpoint %s: %s",
                fallback_base_url,
                str(exc)[:300],
            )
            base_url = fallback_base_url

    payload = _build_openai_compatible_grounding_payload(
        model_name=model_name,
        prompt=prompt,
        image_mime_type=image_mime_type,
        image_base64=image_base64,
    )
    url = f"{base_url.rstrip('/')}/chat/completions"
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True, proxy=None) as client:
        response = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        if response.status_code >= 400 and "response_format" in response.text:
            payload.pop("response_format", None)
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
    if response.status_code >= 400:
        raise RuntimeError(f"Grounding OpenAI API HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    content = _extract_openai_compatible_content(data)
    return _normalize_grounding_result(
        _parse_grounding_json(
            content or "",
            response_summary=_summarize_openai_compatible_response(data),
        )
    )


def _gemini_grounding_response_schema() -> dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "found": {"type": "BOOLEAN"},
            "target": {"type": "STRING"},
            "box_2d": {
                "type": "ARRAY",
                "items": {"type": "NUMBER"},
                "nullable": True,
            },
            "confidence": {"type": "NUMBER"},
            "reason": {"type": "STRING"},
            "page_content": {"type": "STRING"},
            "clarification": {"type": "STRING"},
        },
        "required": [
            "found",
            "target",
            "box_2d",
            "confidence",
            "reason",
            "page_content",
            "clarification",
        ],
    }


def _build_openai_compatible_grounding_payload(
    *,
    model_name: str,
    prompt: str,
    image_mime_type: str,
    image_base64: str,
) -> dict[str, Any]:
    return {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_mime_type};base64,{image_base64}",
                        },
                    },
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
    }


def _build_gemini_native_grounding_payload(
    *,
    prompt: str,
    image_mime_type: str,
    image_base64: str,
) -> dict[str, Any]:
    return {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": image_mime_type,
                            "data": image_base64,
                        }
                    },
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
            "responseSchema": _gemini_grounding_response_schema(),
        },
    }


def _is_gemini_native_models_base_url(base_url: str) -> bool:
    normalized = str(base_url or "").rstrip("/")
    return normalized.endswith("/v1beta/models") or normalized.endswith("/v1/models")


def _fallback_openai_base_url_from_native_models_base_url(base_url: str) -> str:
    normalized = str(base_url or "").rstrip("/")
    for suffix in ("/v1beta/models", "/v1/models"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)] + "/v1"
    return ""


def _gemini_native_generate_content_url(base_url: str, model_name: str) -> str:
    model_path = str(model_name or "").strip().lstrip("/")
    if model_path.startswith("models/"):
        model_path = model_path[len("models/") :]
    return f"{base_url.rstrip('/')}/{model_path}:generateContent"


def _truncate_for_log(value: Any, limit: int = 500) -> Any:
    if isinstance(value, str):
        return value[:limit]
    return value


def _extract_openai_compatible_content(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
        return "\n".join(texts)
    return ""


def _summarize_openai_compatible_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"response_type": type(data).__name__}
    choices = data.get("choices") or []
    first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first_choice.get("message") if isinstance(first_choice, dict) else {}
    message = message if isinstance(message, dict) else {}
    content = message.get("content")
    return {
        "response_keys": sorted(data.keys()),
        "choice_count": len(choices) if isinstance(choices, list) else 0,
        "finish_reason": first_choice.get("finish_reason") or first_choice.get("finishReason"),
        "first_choice_keys": sorted(first_choice.keys()) if isinstance(first_choice, dict) else [],
        "message_keys": sorted(message.keys()),
        "content_type": type(content).__name__,
        "content_preview": _truncate_for_log(content, 300) if isinstance(content, str) else "",
        "usage": data.get("usage") or data.get("usageMetadata"),
    }


def _summarize_gemini_native_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"response_type": type(data).__name__}
    candidates = data.get("candidates") or []
    first_candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    content = first_candidate.get("content") if isinstance(first_candidate, dict) else {}
    content = content if isinstance(content, dict) else {}
    parts = content.get("parts") if isinstance(content, dict) else []
    part_summaries = []
    if isinstance(parts, list):
        for part in parts[:5]:
            if not isinstance(part, dict):
                part_summaries.append({"type": type(part).__name__})
                continue
            item = {"keys": sorted(part.keys())}
            if isinstance(part.get("text"), str):
                item["text_preview"] = part["text"][:300]
            part_summaries.append(item)
    return {
        "response_keys": sorted(data.keys()),
        "candidate_count": len(candidates) if isinstance(candidates, list) else 0,
        "first_candidate_keys": sorted(first_candidate.keys()) if isinstance(first_candidate, dict) else [],
        "finishReason": first_candidate.get("finishReason"),
        "content_keys": sorted(content.keys()) if isinstance(content, dict) else [],
        "part_count": len(parts) if isinstance(parts, list) else 0,
        "parts": part_summaries,
        "promptFeedback": data.get("promptFeedback"),
        "usageMetadata": data.get("usageMetadata"),
    }


def _extract_gemini_native_content(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    candidates = data.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        return ""
    content = candidates[0].get("content") or {}
    parts = content.get("parts") if isinstance(content, dict) else []
    texts = []
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return "\n".join(texts)


def _normalize_grounding_result(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    if "target" not in normalized and isinstance(normalized.get("label"), str):
        normalized["target"] = normalized["label"]
    if "found" not in normalized:
        box = normalized.get("box_2d")
        normalized["found"] = isinstance(box, list) and len(box) == 4
    for key in ("target", "reason", "page_content", "clarification"):
        normalized.setdefault(key, "")
    normalized.setdefault("confidence", None)
    return normalized


async def _resolve_grounding_openai_config(agent_id: uuid.UUID, action: str) -> tuple[str, str, str]:
    """Resolve OpenAI-compatible grounding config from the CDP tool config."""
    from app.services.agent_tools import _get_tool_config

    tool_name = "agentbay_browser_cdp_type" if action == "type" else "agentbay_browser_cdp_click"
    config = await _get_tool_config(agent_id, tool_name) or {}
    api_key = str(config.get("api_key") or "").strip()
    base_url = str(config.get("base_url") or "").strip()
    model_name = str(config.get("model_name") or config.get("model") or "").strip()

    if not api_key or not base_url or not model_name:
        missing = [
            name
            for name, value in {
                "api_key": api_key,
                "base_url": base_url,
                "model_name": model_name,
            }.items()
            if not value
        ]
        raise RuntimeError(
            f"{tool_name} grounding config missing: {', '.join(missing)}. "
            "The evaluation platform should write per-agent tool config when creating the agent."
        )
    if base_url.rstrip("/").endswith("/chat/completions"):
        base_url = base_url.rstrip("/")[: -len("/chat/completions")]
    if base_url.rstrip("/").endswith("/v1"):
        return api_key, base_url.rstrip("/"), model_name
    return api_key, base_url.rstrip("/"), model_name


def _parse_grounding_json(content: str, response_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise RuntimeError(_grounding_non_json_error(content, response_summary)) from exc
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as nested_exc:
            raise RuntimeError(_grounding_non_json_error(content, response_summary)) from nested_exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Gemini grounding returned non-object JSON: {data}")
    return data


def _grounding_non_json_error(content: str, response_summary: dict[str, Any] | None = None) -> str:
    content_preview = (content or "")[:300]
    if not response_summary:
        return f"Gemini grounding returned non-JSON content: {content_preview}"
    try:
        summary = json.dumps(response_summary, ensure_ascii=False, default=str)[:1500]
    except Exception:
        summary = str(response_summary)[:1500]
    return f"Gemini grounding returned non-JSON content: {content_preview}; response_summary={summary}"


def _grounding_result_has_usable_target(grounding: dict[str, Any]) -> bool:
    if _grounding_target_not_found(grounding):
        return True
    box = grounding.get("box_2d")
    return isinstance(box, list) and len(box) == 4


def _grounding_target_not_found(grounding: dict[str, Any]) -> bool:
    found = grounding.get("found")
    if found is False:
        return True
    if isinstance(found, str):
        return found.strip().lower() in {"false", "no", "not_found", "not found", "missing", "0"}
    return False


def _grounding_not_found_message(instruction: str, grounding: dict[str, Any]) -> str:
    page_content = str(
        grounding.get("page_content")
        or grounding.get("visible_page_content")
        or grounding.get("visible_content")
        or ""
    ).strip()
    reason = str(grounding.get("reason") or "").strip()
    clarification = str(grounding.get("clarification") or "").strip()
    parts = [
        "Gemini grounding could not find the requested target on the current page, so no CDP action was performed.",
        f"Requested target: {instruction}",
    ]
    if page_content:
        parts.append(f"Visible page content: {page_content[:1000]}")
    if reason:
        parts.append(f"Reason: {reason[:500]}")
    if clarification:
        parts.append(f"Clarification needed: {clarification[:500]}")
    else:
        parts.append("Clarification needed: Please provide a more specific instruction that matches a visible element on the current page.")
    return "\n".join(parts)


def _normalized_box_center_to_pixel(box: list[Any], width: int, height: int) -> tuple[float, float, float, float, int, int]:
    """Convert Gemini [ymin, xmin, ymax, xmax] 0-1000 box to a clamped pixel center."""
    try:
        ymin, xmin, ymax, xmax = [float(v) for v in box]
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Gemini grounding returned non-numeric box_2d: {box}") from exc

    x = int(round(((xmin + xmax) / 2.0) / 1000.0 * width))
    y = int(round(((ymin + ymax) / 2.0) / 1000.0 * height))
    x = max(0, min(width - 1, x))
    y = max(0, min(height - 1, y))
    return ymin, xmin, ymax, xmax, x, y


def _parse_cdp_action_result(run_result: Any) -> dict[str, Any]:
    stdout = getattr(run_result, "stdout", "") or getattr(run_result, "output", "") or ""
    stderr = getattr(run_result, "stderr", "") or ""
    success = bool(getattr(run_result, "success", False))
    error_message = getattr(run_result, "error_message", "") or ""
    try:
        data = json.loads(stdout.strip().splitlines()[-1])
    except Exception as exc:
        if not success:
            raise RuntimeError(stderr or error_message or "CDP action failed") from exc
        raise RuntimeError(f"CDP action returned invalid JSON: stdout={stdout[:500]} stderr={stderr[:500]}") from exc
    if not success and data.get("success"):
        logger.warning(
            "[AgentBay] CDP action command reported failure but stdout contained success JSON: %s",
            error_message or stderr,
        )
        return data
    if not success:
        raise RuntimeError(stderr or error_message or "CDP action failed")
    if not data.get("success"):
        raise RuntimeError(data.get("error") or "CDP action failed")
    return data


def _build_browser_cdp_action_script() -> str:
    """Return a Node.js Playwright script that operates the current AgentBay browser page."""
    return r"""
const { chromium } = require('/usr/local/lib/node_modules/playwright');

function decodePayload() {
  const arg = process.argv[2] || '';
  return JSON.parse(Buffer.from(arg, 'base64').toString('utf8'));
}

(async () => {
  let browser;
  let exitCode = 0;
  try {
    const payload = decodePayload();
    browser = await chromium.connectOverCDP('http://localhost:9222');
    const context = browser.contexts()[0];
    if (!context) throw new Error('No browser context found');
    const pages = context.pages();
    const page = pages[pages.length - 1] || await context.newPage();
    const x = Number(payload.x);
    const y = Number(payload.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      throw new Error('Invalid CDP coordinates');
    }

    await page.mouse.click(x, y);
    await page.waitForTimeout(150);

    if (payload.action === 'type') {
      if (payload.replace !== false) {
        await page.keyboard.press('Control+A');
        await page.keyboard.press('Backspace');
      }
      await page.keyboard.type(String(payload.text || ''), { delay: Number(payload.delay || 20) });
    }

    await page.waitForTimeout(300);
    const title = await page.title().catch(() => '');
    process.stdout.write(JSON.stringify({
      success: true,
      action: payload.action,
      x,
      y,
      url: page.url(),
      title
    }) + '\n');
  } catch (e) {
    exitCode = 1;
    console.error(e && e.stack ? e.stack : String(e));
    process.stdout.write(JSON.stringify({ success: false, error: e && e.message ? e.message : String(e) }) + '\n');
  } finally {
    if (browser) {
      const disconnectPromise = typeof browser.disconnect === 'function' ? browser.disconnect() : browser.close();
      await Promise.race([
        disconnectPromise,
        new Promise((resolve) => setTimeout(resolve, 1000)),
      ]).catch(() => {});
    }
    process.exit(exitCode);
  }
})();
"""


# ─── Session Cache for Tool Executions ──────────────────────────
# Key: (agent_id, session_id, image_type) so each ChatSession gets
# its own independent AgentBay instance for browser/computer/code.
# Previously keyed by (agent_id, image_type) which meant all users
# of the same Agent shared one browser/desktop — causing conflicts.

_AgentBayCacheKey = tuple[uuid.UUID, str, str]

_agentbay_sessions: dict[_AgentBayCacheKey, tuple[AgentBayClient, datetime]] = {}
_agentbay_create_locks: dict[_AgentBayCacheKey, asyncio.Lock] = {}
_agentbay_sessions_lock = asyncio.Lock()
_AGENTBAY_SESSION_TIMEOUT = timedelta(minutes=5)
_AGENTBAY_CLEANUP_INTERVAL_SECONDS = 60


AGENTBAY_API_URL = "https://api.agentbay.ai/v1"


def get_cached_agentbay_client_for_agent(
    agent_id: uuid.UUID,
    image_type: str,
    session_id: str = "",
) -> Optional[AgentBayClient]:
    """Return a cached AgentBay client without creating a new remote session."""
    entry = _agentbay_sessions.get((agent_id, session_id, image_type))
    return entry[0] if entry else None


async def _get_agentbay_create_lock(cache_key: _AgentBayCacheKey) -> asyncio.Lock:
    """Return a per-cache-key lock so concurrent tool calls share one session."""
    async with _agentbay_sessions_lock:
        lock = _agentbay_create_locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            _agentbay_create_locks[cache_key] = lock
        return lock


def _is_plausible_agentbay_api_key(value: str | None) -> bool:
    """AgentBay API keys use an akm-* token format.

    This keeps encrypted blobs that failed to decrypt from being treated as
    plaintext keys and sent to AgentBay, where they surface as
    "invalid apiKey or token".
    """
    return bool(isinstance(value, str) and value.strip().startswith("akm-"))


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
                candidate = decrypt_data(config.app_secret, get_settings().SECRET_KEY)
            except Exception:
                candidate = config.app_secret
            if _is_plausible_agentbay_api_key(candidate):
                return candidate

        # 2) Fallback: check global Tool.config.api_key for agentbay tools.
        #
        # Only agentbay_browser_navigate (the "primary" AgentBay tool) has a
        # config_schema with an api_key field, so it is the only tool whose
        # config is ever populated with a key via the Company Settings UI.
        # We therefore query it first, then fall back to scanning all agentbay
        # tools — this prevents a non-deterministic .limit(1) from returning a
        # tool with an empty config (e.g. agentbay_computer_screenshot), which
        # would silently return None even when a key IS configured.
        candidate_tools: list[Tool] = []
        tool_result = await session.execute(
            select(Tool).where(
                Tool.name == "agentbay_browser_navigate",
                Tool.enabled == True,
            ).limit(1)
        )
        tool = tool_result.scalar_one_or_none()
        if tool:
            candidate_tools.append(tool)

        # Also scan all agentbay tools in case the key was stored on a
        # different category representative by an older UI.
        all_result = await session.execute(
            select(Tool).where(
                Tool.category == "agentbay",
                Tool.enabled == True,
            ).order_by(Tool.name)
        )
        candidate_tools.extend(
            candidate
            for candidate in all_result.scalars().all()
            if not tool or candidate.id != tool.id
        )

        for candidate_tool in candidate_tools:
            if not (candidate_tool.config and candidate_tool.config.get("api_key")):
                continue
            api_key = candidate_tool.config["api_key"]
            try:
                candidate = decrypt_data(api_key, get_settings().SECRET_KEY)
            except Exception:
                candidate = api_key
            if _is_plausible_agentbay_api_key(candidate):
                return candidate

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
        # Using linux_latest instead of browser_latest. AgentBay tokens may be
        # scoped/bound to specific instance types, and requesting browser_latest
        # might trigger an 'InvalidParameter.Authorization' error for this key.
        result = await asyncio.to_thread(sdk.create, CreateSessionParams(image_id="linux_latest"))
        if result.success:
            if result.session:
                await asyncio.to_thread(result.session.delete)
            return {"ok": True, "message": "✅ Successfully connected to AgentBay API"}
        return {"ok": False, "error": result.error_message}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def get_agentbay_client_for_agent(agent_id: uuid.UUID, image_type: str, session_id: str = "") -> AgentBayClient:
    """Get or create AgentBay client for agent.

    Sessions are cached per (agent_id, session_id, image_type) so that each
    ChatSession gets its own independent AgentBay instance. Multiple users
    chatting with the same Agent will each have isolated browser/desktop/code
    environments.

    Args:
        agent_id: The agent UUID.
        image_type: One of 'browser', 'computer', 'code'.
        session_id: The ChatSession ID. Defaults to '' for backward compat
                    (e.g. test_agentbay_channel, single-session callers).
    """

    cache_key = (agent_id, session_id, image_type)
    await cleanup_agentbay_sessions()

    create_lock = await _get_agentbay_create_lock(cache_key)
    async with create_lock:
        now = datetime.now()
        if cache_key in _agentbay_sessions:
            client, last_used = _agentbay_sessions[cache_key]
            if now - last_used < _AGENTBAY_SESSION_TIMEOUT:
                # Session still valid, refresh timestamp and reuse
                _agentbay_sessions[cache_key] = (client, now)
                return client

            # Session expired, close and remove
            logger.info(f"[AgentBay] Session expired for {image_type} (session={session_id[:8]}), closing")
            async with _agentbay_sessions_lock:
                _agentbay_sessions.pop(cache_key, None)
            await client.close_session()

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
            if not _is_plausible_agentbay_api_key(api_key):
                api_key = None

        if not api_key:
            api_key = await get_agentbay_api_key_for_agent(agent_id)

        if not api_key:
            raise RuntimeError("AgentBay not configured for this agent. Please configure in Tools > AgentBay.")

        client = AgentBayClient(api_key)

        try:
            if image_type == "browser":
                await client.create_session("browser_latest")
                # Inject stored cookies after browser initialization
                await _inject_credentials(client, agent_id)
                from app.services.webarena_agentbay_artifacts import maybe_start_webarena_recorder
                await maybe_start_webarena_recorder(agent_id, session_id, client)
            elif image_type == "computer":
                # Read OS preference from tool config (default: windows)
                os_type = (tool_config or {}).get("os_type", "windows")
                computer_image = "windows_latest" if os_type == "windows" else "linux_latest"
                logger.info(f"[AgentBay] Creating computer session with OS: {os_type} (image: {computer_image}) for session={session_id[:8]}")
                await client.create_session(computer_image)
            else:
                await client.create_session("code_latest")
        except Exception:
            await client.close_session()
            raise

        async with _agentbay_sessions_lock:
            _agentbay_sessions[cache_key] = (client, datetime.now())
        return client


async def cleanup_agentbay_sessions():
    """Clean up expired AgentBay sessions."""
    now = datetime.now()
    async with _agentbay_sessions_lock:
        expired = [
            cache_key for cache_key, (client, last_used) in _agentbay_sessions.items()
            if now - last_used > _AGENTBAY_SESSION_TIMEOUT
        ]
        clients_to_close = [
            (cache_key, _agentbay_sessions.pop(cache_key)[0])
            for cache_key in expired
            if cache_key in _agentbay_sessions
        ]
        for cache_key in expired:
            _agentbay_create_locks.pop(cache_key, None)

    for cache_key, client in clients_to_close:
        agent_id, session_id, image_type = cache_key
        logger.info(f"[AgentBay] Cleaning up expired {image_type} session for agent {agent_id} (session={session_id[:8]})")
        await client.close_session()


async def close_all_agentbay_sessions(reason: str = "shutdown"):
    """Close every cached AgentBay session, used during process shutdown."""
    async with _agentbay_sessions_lock:
        clients_to_close = list(_agentbay_sessions.items())
        _agentbay_sessions.clear()
        _agentbay_create_locks.clear()

    for cache_key, (client, _last_used) in clients_to_close:
        agent_id, session_id, image_type = cache_key
        logger.info(
            f"[AgentBay] Closing cached {image_type} session for agent {agent_id} "
            f"(session={session_id[:8]}, reason={reason})"
        )
        await client.close_session()


async def run_agentbay_session_cleanup_loop():
    """Periodically release idle AgentBay sessions so API-key concurrency is freed."""
    while True:
        try:
            await cleanup_agentbay_sessions()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"[AgentBay] Periodic session cleanup failed: {exc}")
        await asyncio.sleep(_AGENTBAY_CLEANUP_INTERVAL_SECONDS)


async def _inject_credentials(client: AgentBayClient, agent_id: uuid.UUID):
    """Inject stored cookies into the browser via CDP after initialization.

    Reads all 'active' credentials with cookies from the agent_credentials table,
    decrypts cookies_json, and injects them via a Playwright Node.js script that
    connects to Chrome's CDP port (localhost:9222).

    This runs automatically after every browser session creation. If no credentials
    exist or injection fails, it logs a warning but does not block the session.
    """
    import json
    from app.database import async_session as async_session_factory
    from app.models.agent_credential import AgentCredential
    from sqlalchemy import select
    from app.core.security import decrypt_data
    from app.config import get_settings

    settings = get_settings()

    # Fetch active credentials with stored cookies
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(AgentCredential).where(
                    AgentCredential.agent_id == agent_id,
                    AgentCredential.status == "active",
                    AgentCredential.cookies_json.isnot(None),
                )
            )
            credentials = result.scalars().all()
    except Exception as e:
        logger.warning(f"[AgentBay] Failed to query credentials for injection: {e}")
        return

    if not credentials:
        return  # No cookies to inject

    # Collect and decrypt all cookies
    all_cookies = []
    for cred in credentials:
        try:
            raw = decrypt_data(cred.cookies_json, settings.SECRET_KEY)
            cookies = json.loads(raw)
            if isinstance(cookies, list):
                all_cookies.extend(cookies)
        except Exception as e:
            logger.warning(f"[AgentBay] Failed to decrypt cookies for {cred.platform}: {e}")

    if not all_cookies:
        return

    # Ensure browser is initialized before injection (Chrome must be running)
    try:
        await client._ensure_browser_initialized()
    except Exception as e:
        logger.warning(f"[AgentBay] Cannot inject cookies — browser not initialized: {e}")
        return

    # Build Node.js injection script.
    # Use base64 encoding to write the script to the current working dir (not /tmp,
    # which may lack write permissions in the Wuying browser sandbox).
    #
    # Cookies stored in DB were already sanitized at export time (sameSite title-cased,
    # expires:-1 removed, domain without leading dot), so we only do a defensive
    # re-sanitize here in case older records were stored before the fix.
    import base64 as _base64
    cookies_json_str = json.dumps(all_cookies)
    inject_script = r"""
const { chromium } = require('/usr/local/lib/node_modules/playwright');
(async () => {
    try {
        const browser = await chromium.connectOverCDP('http://localhost:9222');
        const context = browser.contexts()[0];
        const rawCookies = """ + cookies_json_str + r""";

        // Defensive sanitize: normalize sameSite casing and strip invalid expires
        const sameSiteMap = { none: 'None', lax: 'Lax', strict: 'Strict' };
        const cookies = rawCookies.map(c => {
            const out = { ...c };
            if (out.sameSite != null) {
                out.sameSite = sameSiteMap[String(out.sameSite).toLowerCase()] || 'Lax';
            }
            if (out.expires != null && out.expires <= 0) {
                delete out.expires;
            }
            // Ensure domain has leading dot for subdomain matching
            if (out.domain && !out.domain.startsWith('.')) {
                out.domain = '.' + out.domain;
            }
            return out;
        });

        let injected = 0;
        let failed = 0;
        // Inject one at a time so a single bad cookie doesn't break the rest
        for (const cookie of cookies) {
            try {
                await context.addCookies([cookie]);
                injected++;
            } catch (e) {
                failed++;
                if (failed <= 3) {
                    // Log first few failures to aid debugging
                    console.error('INJECT_SKIP:' + e.message + ' cookie=' + JSON.stringify(cookie).slice(0, 200));
                }
            }
        }
        console.log('INJECT_OK:' + injected + ' injected, ' + failed + ' skipped');
        process.exit(0);
    } catch (e) {
        console.error('INJECT_FAIL:' + e.message);
        process.exit(1);
    }
})();
"""


    try:
        # Write script via base64 decode to avoid shell quoting issues and /tmp permission errors
        script_b64 = _base64.b64encode(inject_script.encode('utf-8')).decode('ascii')
        write_result = await asyncio.to_thread(
            client._session.command.exec,
            f"echo '{script_b64}' | /usr/bin/base64 -d > tc_inject_cookies.js",
        )
        write_ok = getattr(write_result, 'success', False)
        logger.info(f"[AgentBay] Cookie inject script write: success={write_ok}")

        # Execute the injection script
        exec_result = await asyncio.to_thread(
            client._session.command.exec,
            "node tc_inject_cookies.js",
            timeout_ms=15000,
        )
        stdout = getattr(exec_result, 'stdout', '') or getattr(exec_result, 'output', '') or ''
        stderr = getattr(exec_result, 'stderr', '') or ''

        if "INJECT_OK" in stdout:
            logger.info(f"[AgentBay] Cookie injection successful for agent {agent_id}: {stdout.strip()[:100]}")
            # Update last_injected_at for all injected credentials
            try:
                from datetime import timezone as tz
                now = datetime.now(tz.utc)
                async with async_session_factory() as db:
                    for cred in credentials:
                        cred.last_injected_at = now
                        db.add(cred)
                    await db.commit()
            except Exception as e:
                logger.warning(f"[AgentBay] Failed to update last_injected_at: {e}")
        else:
            logger.warning(f"[AgentBay] Cookie injection may have failed: stdout={stdout[:200]}, stderr={stderr[:200]}")
    except Exception as e:
        logger.warning(f"[AgentBay] Cookie injection error: {e}")
