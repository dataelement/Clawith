"""AgentBay Take Control API — human-agent collaborative login.

Provides REST endpoints for forwarding mouse/keyboard events to an
AgentBay session and managing the Take Control lock. When locked,
the agent's automatic browser/computer tool execution is paused to
prevent human-agent input collisions.

Cookie export occurs automatically when the Take Control session ends.
"""

import asyncio
import json
import logging
import time
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
# Key: (agent_id_str, session_id_str) → (user_id, lock_timestamp)
_take_control_locks: dict[tuple[str, str], tuple[str, float]] = {}
_LOCK_TIMEOUT_SECONDS = 600  # Auto-expire stale locks after 10 minutes

# Cache of sessions that have already had browser initialization called.
# Avoids redundant _ensure_browser_initialized() on every screenshot poll.
_browser_initialized: set[tuple] = set()

# Per-session interaction locks to serialize concurrent TC interactions.
# Without this, two rapid clicks both write tc_action.js simultaneously,
# corrupting one script's execution. Each TC session gets its own Lock.
_tc_interaction_locks: dict[str, asyncio.Lock] = {}


def _get_interaction_lock(agent_id: uuid.UUID, session_id: str) -> asyncio.Lock:
    """Get or create the per-session asyncio.Lock for TC interactions."""
    key = f"{agent_id}:{session_id}"
    if key not in _tc_interaction_locks:
        _tc_interaction_locks[key] = asyncio.Lock()
    return _tc_interaction_locks[key]


def is_session_locked(agent_id: str, session_id: str) -> bool:
    """Check if a session is currently under human Take Control.

    Called by execute_tool to block automatic agentbay_* tool calls.
    Automatically clears expired locks.
    """
    key = (agent_id, session_id)
    if key not in _take_control_locks:
        return False
    _user_id, locked_at = _take_control_locks[key]
    if time.time() - locked_at > _LOCK_TIMEOUT_SECONDS:
        logger.info(f"[TakeControl] Auto-expired stale lock for session={session_id[:8]}")
        del _take_control_locks[key]
        return False
    return True


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


class DragRequest(BaseModel):
    """Mouse drag event forwarding — used for slider CAPTCHAs and drag-and-drop."""
    session_id: str
    from_x: int
    from_y: int
    to_x: int
    to_y: int
    duration_ms: int = 600  # Total drag duration in milliseconds


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
    """Retrieve the AgentBay client for the given agent + session.

    Checks the session cache for any active image type (browser, computer, code)
    rather than hardcoding 'browser'. This ensures Take Control works with
    whatever session type the agent is actively using.

    IMPORTANT: For browser sessions, this also calls _ensure_browser_initialized()
    because the browser SDK requires explicit initialization before screenshot/
    interaction APIs will work. Without this, get_browser_snapshot_base64() returns
    None ("Browser not initialized") and all CDP-based interactions fail silently.
    """
    from app.services.agentbay_client import _agentbay_sessions, _AGENTBAY_SESSION_TIMEOUT
    from datetime import datetime

    now = datetime.now()

    # First, try to find an existing cached session for this agent+session
    # across all image types (browser, computer, code)
    for image_type in ("browser", "computer", "code"):
        cache_key = (agent_id, session_id, image_type)
        if cache_key in _agentbay_sessions:
            client, last_used = _agentbay_sessions[cache_key]
            if now - last_used < _AGENTBAY_SESSION_TIMEOUT:
                # Refresh timestamp and reuse
                _agentbay_sessions[cache_key] = (client, now)
                logger.info(
                    f"[TakeControl] Found existing {image_type} session for "
                    f"agent={agent_id}, session={session_id[:8]}"
                )
                # Ensure browser is initialized for browser-type sessions
                # (only on first access — cached to avoid delay on subsequent polls)
                if image_type in ("browser", "browser_latest") and cache_key not in _browser_initialized:
                    try:
                        await client._ensure_browser_initialized()
                        _browser_initialized.add(cache_key)
                    except Exception as e:
                        logger.warning(f"[TakeControl] Browser init on cached session failed: {e}")
                return client

    # No cached session found — create a new browser session
    from app.services.agentbay_client import get_agentbay_client_for_agent

    try:
        client = await get_agentbay_client_for_agent(
            agent_id, image_type="browser", session_id=session_id
        )
        # Ensure browser is initialized for the newly created session
        try:
            await client._ensure_browser_initialized()
            _browser_initialized.add((agent_id, session_id, "browser"))
            logger.info(f"[TakeControl] Browser initialized for new session, agent={agent_id}")
        except Exception as e:
            logger.warning(f"[TakeControl] Browser init on new session failed: {e}")
        return client
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active browser session found: {e}",
        )


# ── Session-aware input helpers ──
# Browser sessions use CDP (Chrome DevTools Protocol) via Playwright to
# interact directly with Chrome. Desktop sessions use the SDK's computer API.


import asyncio


def _is_browser_session(client) -> bool:
    """Check if the client's active session is a browser image."""
    return getattr(client, "_image_type", "") in ("browser", "browser_latest")


async def _cdp_exec(client, script: str, timeout_ms: int = 15000) -> dict:
    """Execute a Playwright CDP script inside the AgentBay container.

    Uses the AgentBayClient.command_exec wrapper which properly handles
    the SDK call and returns a dict with {success, stdout, stderr, ...}.
    """
    # Write script to temp file inside the container
    write_result = await client.command_exec(
        f"cat > /tmp/_tc_action.js << 'TCEOF'\n{script}\nTCEOF",
        timeout_ms=5000,
    )
    if not write_result.get("success"):
        logger.error(f"[TakeControl] Failed to write CDP script: {write_result}")
        return {"success": False, "output": "Failed to write script", "stderr": str(write_result)[:200]}

    result = await client.command_exec(
        "node /tmp/_tc_action.js",
        timeout_ms=timeout_ms,
    )
    stdout = result.get("stdout", "") or result.get("output", "") or ""
    stderr = result.get("stderr", "") or result.get("error_message", "") or ""
    cmd_success = result.get("success", False)
    tc_success = "TC_OK" in stdout

    logger.info(
        f"[TakeControl] CDP exec: cmd_success={cmd_success}, tc_ok={tc_success}, "
        f"stdout={stdout[:200]}, stderr={stderr[:200]}, exit_code={result.get('exit_code', 'N/A')}"
    )
    return {"success": tc_success, "output": stdout[:500], "stderr": stderr[:200]}


async def _eval_cdp_script(client, script_body: str) -> dict:
    """Evaluate a Node.js Playwright CDP script in the browser container."""
    import base64
    try:
        # Base64 encode the script to avoid shell escaping issues inside the container
        script_b64 = base64.b64encode(script_body.encode('utf-8')).decode('ascii')
        
        # Write base64 to file and decode it to tc_action.js (in current working dir, since /tmp might be restricted)
        cmd_write = f"echo '{script_b64}' | /usr/bin/base64 -d > tc_action.js"
        await asyncio.to_thread(client._session.command.exec, cmd_write)
        
        # Execute the script
        result = await asyncio.to_thread(client._session.command.exec, "node tc_action.js")
        
        success = getattr(result, 'success', False)
        output = getattr(result, 'output', '') or getattr(result, 'stdout', '') or ''
        stderr = getattr(result, 'stderr', '') or ''
        
        if not success:
            logger.error(f"[TakeControl] CDP execution failed. Output: {output}, Stderr: {stderr}")
            return {"success": False, "output": f"Node error: {stderr[:200]}"}
            
        return {"success": True, "output": output}
    except Exception as e:
        logger.error(f"[TakeControl] CDP exception: {e}")
        return {"success": False, "output": str(e)}


async def _tc_browser_cleanup(agent_id: uuid.UUID, session_id: str) -> None:
    """Best-effort cleanup immediately after Take Control exits.

    Uses the AgentBay SDK's own browser.operator.navigate() to navigate to
    about:blank. This goes through the SERVICE'S Playwright instance (not a
    new connectOverCDP connection), so there's no competing CDP session,
    no Target.attachToTarget/detachFromTarget events, and no risk of confusing
    the service's internal page state.

    IMPORTANT: Previous approaches that used connectOverCDP + browser.close()
    for cleanup were sending Target.detachFromTarget events to Chrome while
    navigation was in progress. The AgentBay service's Playwright received
    these detach events mid-navigation, which put its internal state machine
    into a 60-second recovery loop before it could accept the next page.goto().
    """
    from app.services.agentbay_client import _agentbay_sessions

    cleanup_client = None
    for img_type in ("browser", "browser_latest"):
        ck = (agent_id, session_id, img_type)
        if ck in _agentbay_sessions:
            cleanup_client = _agentbay_sessions[ck][0]
            break
    if not cleanup_client:
        return

    try:
        # Cleanup strategy: stop all in-flight page navigations, then navigate
        # the active content page to about:blank.
        #
        # WHY multi-step:
        # 1. stopLoading on all pages: a TC click may have opened a NEW TAB
        #    (target=_blank link on baidu) that is still loading a heavy article.
        #    Page.stopLoading kills that load immediately so Chrome's DevTools
        #    is no longer blocked draining a multi-MB response.
        # 2. Page.navigate to about:blank on the active page: gives the AgentBay
        #    service's page.goto() a clean starting point. about:blank commits in
        #    <10ms; the service no longer has to wait for tieba/zhihu/baidu to drain.
        # 3. Wait for Page.loadEventFired before process.exit(): ensures Chrome has
        #    fully settled at about:blank before we disconnect. This means Chrome
        #    emits Target.detachedFromTarget (from our WebSocket close) while the
        #    page is in a stable, loaded state — not mid-navigation — so the
        #    service's Playwright state machine doesn't enter a 60-second recovery.
        # 4. No browser.close(): we let Node.js exit naturally. Chrome handles
        #    the WebSocket close without an explicit Target.detachFromTarget CDP
        #    command that races with other async CDP events.
        cleanup_script = """
const { chromium } = require('/usr/local/lib/node_modules/playwright');
(async () => {
    try {
        const browser = await chromium.connectOverCDP('http://localhost:9222');
        const context = browser.contexts()[0];
        const allPages = context.pages();

        // Stop all loading pages so Chrome is not draining heavy responses.
        // tc clicks frequently open new tabs (target=_blank) that stay loading
        // for 20-40s; stopping them is critical for fast post-TC recovery.
        for (const p of allPages) {
            try {
                const cdp = await context.newCDPSession(p);
                await cdp.send('Page.stopLoading');
                await cdp.detach();
            } catch(_) {}
        }

        // Navigate the active content page (last non-blank) to about:blank.
        // Use raw CDP Page.navigate — the AgentBay SDK rejects about:blank
        // ("must start with http or https") but Chrome's CDP has no such rule.
        const contentPage = allPages.slice().reverse().find(p => p.url() !== 'about:blank')
                            || allPages[allPages.length - 1];
        const cdp = await context.newCDPSession(contentPage);

        // Navigate and wait for loadEventFired so about:blank is fully settled.
        await new Promise((resolve) => {
            cdp.on('Page.loadEventFired', () => resolve());
            cdp.send('Page.navigate', { url: 'about:blank' }).catch(() => resolve());
            setTimeout(resolve, 800);  // Fallback: about:blank always loads in <100ms
        });

        console.log('CLEANUP_OK');
    } catch(e) {
        console.error('CLEANUP_FAIL: ' + e.message);
    }
    // No browser.close() — let Chrome handle WebSocket close gracefully after
    // the page is in a stable loaded state (about:blank).
    process.exit(0);
})();
"""
        res = await _eval_cdp_script(cleanup_client, cleanup_script)
        logger.info(
            f"[TakeControl] Cleanup: {res.get('output', 'no output')[:100]} "
            f"for session={session_id[:8]}"
        )
    except Exception as e:
        logger.warning(f"[TakeControl] Cleanup failed (non-fatal): {e}")


async def _perform_click(client, x: int, y: int, button: str = "left"):
    """Click at (x, y) on the remote session.

    Browser sessions use connectOverCDP because the Computer API's click_mouse
    tool is only available in the computer image type, not browser_latest.
    Each CDP script uses try/catch/finally with browser.close() to ensure a
    graceful disconnect so Chrome's DevTools session does not leak.
    """
    image_type = getattr(client, '_image_type', 'unknown')
    logger.info(f"[TakeControl] Click at ({x}, {y}), button={button}, image_type={image_type}")

    if _is_browser_session(client):
        script = f"""
const {{ chromium }} = require('/usr/local/lib/node_modules/playwright');
(async () => {{
    let ok = false;
    try {{
        const browser = await chromium.connectOverCDP('http://localhost:9222');
        const context = browser.contexts()[0];
        const pages = context.pages();

        // Page selection: prefer the last page with a committed non-blank URL.
        // When a tc click opens a new tab (target=_blank), the new tab briefly
        // has url() === 'about:blank' before its navigation commits. During that
        // window, we correctly target the ORIGINAL content page (the one the user
        // sees in the TC screenshot). The NEXT click, after the new tab has settled,
        // will naturally pick the new tab because its URL will be non-blank by then.
        const page = pages.slice().reverse().find(p => p.url() !== 'about:blank')
                     || pages[pages.length - 1];
        const initialUrl = page.url();
        const initialPageCount = pages.length;
        console.log('TARGET_PAGE:' + initialUrl);

        await page.mouse.click({x}, {y}, {{ button: '{button}' }});
        console.log('CLICK_OK');
        ok = true;

        // Wait 2 seconds for any triggered navigation to commit before releasing
        // the interaction lock. This covers both cases:
        //   A) Same-tab navigation: URL commits in ~0.5-1s
        //   B) New-tab navigation (target=_blank): new tab URL transitions from
        //      about:blank to the target URL in ~1-2s
        //
        // WHY a fixed sleep instead of polling context.pages() every 200ms:
        // Polling makes ~20 CDP calls while Chrome is loading a heavy new tab.
        // Under that combined load, Chrome's DevTools HTTP server stops responding,
        // causing the NEXT connectOverCDP to time out with a 30-second error.
        // A passive sleep has zero CDP overhead and achieves the same goal.
        await new Promise(r => setTimeout(r, 2000));
    }} catch (e) {{
        console.error('CLICK_FAIL:' + e.message);
    }}
    // No browser.close() — avoid explicit Target.detachFromTarget.
    // Chrome handles the WebSocket close gracefully.
    process.exit(ok ? 0 : 1);
}})();
"""
        res = await _eval_cdp_script(client, script)
        return {"success": res.get("success", False) and "CLICK_OK" in res.get("output", ""), "method": "cdp_click", "output": "Clicked" if "CLICK_OK" in res.get("output", "") else res.get("output", "Unknown error")}

    # Desktop session — use Computer API
    try:
        result = await asyncio.to_thread(
            client._session.computer.click_mouse, x, y, button
        )
        success = getattr(result, 'success', False)
        logger.info(f"[TakeControl] Computer click at ({x}, {y}): success={success}")
        return {"success": success, "method": "computer_click", "output": f"Clicked at ({x}, {y})"}
    except Exception as e:
        logger.warning(f"[TakeControl] Computer click failed: {e}")
        return {"success": False, "output": f"Click failed: {str(e)[:200]}"}




async def _perform_type(client, text: str):
    """Type text into the remote session.

    Browser sessions use CDP keyboard API; desktop sessions use computer.input_text.
    """
    image_type = getattr(client, '_image_type', 'unknown')
    logger.info(f"[TakeControl] Type text: '{text[:30]}', image_type={image_type}")

    if _is_browser_session(client):
        import urllib.parse
        encoded_text = urllib.parse.quote(text)
        script = f"""
const {{ chromium }} = require('/usr/local/lib/node_modules/playwright');
(async () => {{
    let ok = false;
    try {{
        const browser = await chromium.connectOverCDP('http://localhost:9222');
        const context = browser.contexts()[0];
        const pages = context.pages();
        const page = pages.slice().reverse().find(p => p.url() !== 'about:blank') || pages[pages.length - 1];
        const textToType = decodeURIComponent('{encoded_text}');
        await page.keyboard.type(textToType);
        console.log('TYPE_OK');
        ok = true;
    }} catch (e) {{
        console.error('TYPE_FAIL:' + e.message);
    }}
    // No browser.close() — avoid Target.detachFromTarget mid-navigation.
    process.exit(ok ? 0 : 1);
}})();
"""
        res = await _eval_cdp_script(client, script)
        return {"success": res.get("success", False) and "TYPE_OK" in res.get("output", ""), "method": "cdp_type", "output": "Text typed" if "TYPE_OK" in res.get("output", "") else res.get("output", "Unknown error")}

    try:
        result = await asyncio.to_thread(
            client._session.computer.input_text, text
        )
        success = getattr(result, 'success', False)
        logger.info(f"[TakeControl] Computer input_text: success={success}")
        return {"success": success, "method": "computer_input", "output": "Text typed"}
    except Exception as e:
        logger.warning(f"[TakeControl] Computer input_text failed: {e}")
        return {"success": False, "output": f"Type failed: {str(e)[:200]}"}




async def _perform_press_keys(client, keys: list[str]):
    """Press key combination on the remote session.

    Browser sessions use CDP keyboard API; desktop sessions use computer.press_keys.
    """
    key_desc = "+".join(keys)
    logger.info(f"[TakeControl] Press keys: {key_desc}")

    if _is_browser_session(client):
        # Convert key names to the Playwright format (e.g. 'ctrl' → 'Control')
        key_map = {
            'ctrl': 'Control', 'alt': 'Alt', 'shift': 'Shift', 'meta': 'Meta',
            'enter': 'Enter', 'backspace': 'Backspace', 'esc': 'Escape', 'tab': 'Tab',
        }
        playwright_keys = [key_map.get(k.lower(), k.upper() if len(k) == 1 else k) for k in keys]
        combined = "+".join(playwright_keys)
        script = f"""
const {{ chromium }} = require('/usr/local/lib/node_modules/playwright');
(async () => {{
    let ok = false;
    try {{
        const browser = await chromium.connectOverCDP('http://localhost:9222');
        const context = browser.contexts()[0];
        const pages = context.pages();
        const page = pages.slice().reverse().find(p => p.url() !== 'about:blank') || pages[pages.length - 1];
        await page.keyboard.press('{combined}');
        console.log('PRESS_OK');
        ok = true;
    }} catch (e) {{
        console.error('PRESS_FAIL:' + e.message);
    }}
    // No browser.close() — avoid Target.detachFromTarget mid-navigation.
    process.exit(ok ? 0 : 1);
}})();
"""
        res = await _eval_cdp_script(client, script)
        return {"success": res.get("success", False) and "PRESS_OK" in res.get("output", ""), "method": "cdp_press", "output": f"Pressed {key_desc}" if "PRESS_OK" in res.get("output", "") else res.get("output", "Unknown error")}

    try:
        result = await asyncio.to_thread(
            client._session.computer.press_keys, keys
        )
        success = getattr(result, 'success', False)
        logger.info(f"[TakeControl] Computer press_keys: success={success}")
        return {"success": success, "method": "computer_keys", "output": f"Pressed {key_desc}"}
    except Exception as e:
        logger.warning(f"[TakeControl] Computer press_keys failed: {e}")
        return {"success": False, "output": f"Key press failed: {str(e)[:200]}"}




async def _perform_drag(
    client, from_x: int, from_y: int, to_x: int, to_y: int, duration_ms: int = 600
) -> dict:
    """Simulate a human-like mouse drag using a Bezier curve trajectory.

    Browser sessions use CDP to send precise mouse events with a Bezier
    curve trajectory and sub-pixel jitter for CAPTCHA bypass.
    Desktop sessions use the Computer API move_mouse sequence.
    All CDP scripts use browser.close() for graceful disconnect.
    """
    logger.info(
        f"[TakeControl] Drag: ({from_x},{from_y}) -> ({to_x},{to_y}), "
        f"duration={duration_ms}ms"
    )

    if _is_browser_session(client):
        script = f"""
const {{ chromium }} = require('/usr/local/lib/node_modules/playwright');
let browser;
(async () => {{
    let ok = false;
    try {{
        browser = await chromium.connectOverCDP('http://localhost:9222');
        const context = browser.contexts()[0];
        const pages = context.pages();
        const page = pages.slice().reverse().find(p => p.url() !== 'about:blank') || pages[pages.length - 1];

        const steps = 30;
        const duration = {duration_ms};
        const x0 = {from_x}, y0 = {from_y};
        const x3 = {to_x},  y3 = {to_y};
        const dx = x3 - x0, dy = y3 - y0;
        const perpX = -dy * 0.15, perpY = dx * 0.15;
        const x1 = x0 + dx * 0.3 + perpX, y1 = y0 + dy * 0.3 + perpY;
        const x2 = x0 + dx * 0.7 - perpX, y2 = y0 + dy * 0.7 - perpY;
        const bezier = (t) => {{
            const u = 1 - t;
            return {{ x: u*u*u*x0+3*u*u*t*x1+3*u*t*t*x2+t*t*t*x3, y: u*u*u*y0+3*u*u*t*y1+3*u*t*t*y2+t*t*t*y3 }};
        }};
        await page.mouse.move(x0, y0);
        await page.mouse.down();
        for (let i = 1; i <= steps; i++) {{
            const pt = bezier(i / steps);
            const jx = (Math.random() - 0.5) * 2;
            const jy = (Math.random() - 0.5) * 2;
            await page.mouse.move(Math.round(pt.x + jx), Math.round(pt.y + jy));
            await new Promise(r => setTimeout(r, duration / steps));
        }}
        await page.mouse.move(x3, y3);
        await page.mouse.up();
        console.log('TC_OK: drag complete');
        ok = true;
    }} catch (e) {{
        console.error('TC_FAIL: ' + e.message);
    }}
    // No browser.close() — avoid Target.detachFromTarget mid-navigation.
    process.exit(ok ? 0 : 1);
}})();
"""
        res = await _eval_cdp_script(client, script)
        return {
            "success": res.get("success", False) and "TC_OK" in res.get("output", ""),
            "method": "cdp_drag",
            "output": f"Dragged ({from_x},{from_y}) -> ({to_x},{to_y})" if "TC_OK" in res.get("output", "") else res.get("output", "Unknown error"),
        }




# ── Endpoints ──


class CurrentUrlRequest(BaseModel):
    """Request to get the current page URL from the browser session."""
    session_id: str


@router.post("/current-url")
async def control_current_url(
    agent_id: uuid.UUID,
    data: CurrentUrlRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current page URL from the active browser session via CDP.

    Called by the Take Control panel on mount to auto-populate the cookie
    domain field, so the user doesn't have to type the domain manually.
    """
    _agent, _access = await check_agent_access(db, current_user, agent_id)

    client = await _get_client(agent_id, data.session_id)

    script = """
const { chromium } = require('/usr/local/lib/node_modules/playwright');
let browser;
(async () => {
    let ok = false;
    try {
        browser = await chromium.connectOverCDP('http://localhost:9222');
        const context = browser.contexts()[0];
        const page = context.pages()[0];
        const url = page.url();
        console.log('URL_OK:' + url);
        ok = true;
    } catch (e) {
        console.error('URL_FAIL:' + e.message);
    } finally {
        if (browser) await browser.close().catch(() => {});
    }
    process.exit(ok ? 0 : 1);
})();
"""
    try:
        res = await _eval_cdp_script(client, script)
        output = res.get("output", "")
        if "URL_OK:" in output:
            url = output.split("URL_OK:", 1)[1].strip()
            return {"status": "ok", "url": url}
        return {"status": "ok", "url": ""}
    except Exception as e:
        logger.warning(f"[TakeControl] current-url failed: {e}")
        return {"status": "ok", "url": ""}  # Non-fatal — return empty URL



@router.post("/click")
async def control_click(
    agent_id: uuid.UUID,
    data: ClickRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Forward a mouse click to the AgentBay session.

    Requires the session to be in Take Control mode (locked).
    Returns {status: 'ok'|'error', detail: str} so the frontend knows if it worked.
    """
    _agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_session_locked(str(agent_id), data.session_id):
        raise HTTPException(status_code=400, detail="Session is not in Take Control mode")

    client = await _get_client(agent_id, data.session_id)
    # Serialize interactions per-session: rapid clicks would otherwise overwrite
    # tc_action.js concurrently, causing the second script to read wrong content.
    async with _get_interaction_lock(agent_id, data.session_id):
        try:
            result = await _perform_click(client, data.x, data.y, data.button)
            if result.get("success"):
                return {"status": "ok", "detail": f"Clicked at ({data.x}, {data.y})"}
            else:
                detail = result.get("stderr") or result.get("output") or "Click operation failed"
                return {"status": "error", "detail": detail[:500]}
        except Exception as e:
            logger.error(f"[TakeControl] Click exception: {e}")
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
    async with _get_interaction_lock(agent_id, data.session_id):
        try:
            result = await _perform_type(client, data.text)
            if result.get("success"):
                return {"status": "ok", "detail": "Text sent"}
            else:
                detail = result.get("stderr") or result.get("output") or "Type operation failed"
                return {"status": "error", "detail": detail[:500]}
        except Exception as e:
            logger.error(f"[TakeControl] Type exception: {e}")
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
    async with _get_interaction_lock(agent_id, data.session_id):
        try:
            result = await _perform_press_keys(client, data.keys)
            if result.get("success"):
                return {"status": "ok", "detail": f"Pressed: {'+'.join(data.keys)}"}
            else:
                detail = result.get("stderr") or result.get("output") or "Key press failed"
                return {"status": "error", "detail": detail[:500]}
        except Exception as e:
            logger.error(f"[TakeControl] Press keys exception: {e}")
            return {"status": "error", "detail": str(e)[:500]}


@router.post("/drag")
async def control_drag(
    agent_id: uuid.UUID,
    data: DragRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Simulate a human-like mouse drag in the AgentBay session.

    Used for slider CAPTCHAs and drag-and-drop interactions.
    The drag follows a Bezier curve trajectory with random jitter to
    mimic natural mouse movement, which is required to bypass bot detection.
    """
    _agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_session_locked(str(agent_id), data.session_id):
        raise HTTPException(status_code=400, detail="Session is not in Take Control mode")

    client = await _get_client(agent_id, data.session_id)
    async with _get_interaction_lock(agent_id, data.session_id):
        try:
            result = await _perform_drag(
                client,
                data.from_x, data.from_y,
                data.to_x, data.to_y,
                data.duration_ms,
            )
            if result.get("success"):
                return {"status": "ok", "detail": result.get("output", "Drag complete")}
            else:
                return {"status": "error", "detail": result.get("output", "Drag failed")[:500]}
        except Exception as e:
            logger.error(f"[TakeControl] Drag exception: {e}")
            return {"status": "error", "detail": str(e)[:500]}


@router.post("/screenshot")
async def control_screenshot(
    agent_id: uuid.UUID,
    data: ScreenshotRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get an immediate screenshot from the AgentBay session.

    Automatically detects the session type (browser/desktop) and uses
    the appropriate snapshot method. Returns a base64 data URI and
    the screen size for coordinate mapping.
    """
    _agent, _access = await check_agent_access(db, current_user, agent_id)

    client = await _get_client(agent_id, data.session_id)
    try:
        # Try browser snapshot first, then desktop
        screenshot_b64 = await client.get_browser_snapshot_base64()
        if not screenshot_b64:
            screenshot_b64 = await client.get_desktop_snapshot_base64()
        if not screenshot_b64:
            logger.warning(f"[TakeControl] Screenshot returned None for agent={agent_id}")

        # Also fetch screen size for coordinate mapping between
        # screenshot dimensions and computer.click_mouse() coordinates
        screen_size = None
        try:
            size_result = await asyncio.to_thread(
                client._session.computer.get_screen_size
            )
            if size_result.success and getattr(size_result, 'data', None):
                screen_size = size_result.data
        except Exception:
            pass  # Non-critical — TC still works without it

        return {
            "status": "ok",
            "screenshot": screenshot_b64,
            "screen_size": screen_size,
        }
    except Exception as e:
        logger.warning(f"[TakeControl] Screenshot failed: {e}")
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
    # Allow any user with access (manage or use) — Take Control is part of
    # the normal interaction flow, not an admin-only operation.

    key = (str(agent_id), data.session_id)
    existing = _take_control_locks.get(key)
    if existing:
        existing_user_id, locked_at = existing
        if existing_user_id != str(current_user.id):
            # Check if the lock has expired
            if time.time() - locked_at > _LOCK_TIMEOUT_SECONDS:
                logger.info(f"[TakeControl] Cleared expired lock held by {existing_user_id}")
            else:
                return {"status": "already_locked", "locked_by": existing_user_id}

    # Acquire or refresh lock with current timestamp
    _take_control_locks[key] = (str(current_user.id), time.time())
    is_reentry = existing is not None
    logger.info(
        f"[TakeControl] Lock acquired: agent={agent_id}, session={data.session_id}, "
        f"user={current_user.id}, re_entry={is_reentry}"
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
        logger.info(f"[TakeControl] Unlock called but no lock found: agent={agent_id}, session={data.session_id}")
        return {"status": "not_locked"}

    exported = False
    export_count = 0

    try:
        # Export cookies if requested (non-critical — lock is released regardless)
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
                logger.warning(f"[TakeControl] Cookie export failed (non-fatal): {e}")
    finally:
        # ALWAYS release the lock, even if cookie export fails
        _take_control_locks.pop(key, None)
        logger.info(
            f"[TakeControl] Lock released: agent={agent_id}, session={data.session_id}"
        )
        # Reset browser initialization flag so the next agentbay browser tool
        # call re-initializes the SDK's browser.operator. This clears any stale
        # page references left by TC's CDP interactions that would otherwise
        # cause browser.operator.navigate to hang indefinitely.
        from app.services.agentbay_client import _agentbay_sessions
        for _img_type in ("browser", "browser_latest"):
            _ck = (agent_id, data.session_id, _img_type)
            if _ck in _agentbay_sessions:
                _tc_client, _ts = _agentbay_sessions[_ck]
                _tc_client._browser_initialized = False
                logger.info(
                    f"[TakeControl] Reset _browser_initialized after TC unlock "
                    f"for session={data.session_id[:8]}"
                )
        # Clear from the control-layer initialization tracking set as well
        _browser_initialized.discard((agent_id, data.session_id, "browser"))
        _browser_initialized.discard((agent_id, data.session_id, "browser_latest"))

    # Post-unlock CDP cleanup: cancel any in-progress navigations and release
    # held mouse buttons before the agent resumes its browser tool calls.
    await _tc_browser_cleanup(agent_id, data.session_id)

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
    # Build and execute a Node.js script to export ALL cookies via CDP.
    #
    # Key design decisions:
    # 1. We call context.cookies() WITHOUT a URL filter, which returns every cookie
    #    in the browser profile regardless of which page is currently open.
    # 2. We sanitize each cookie object before exporting:
    #    - Normalize 'sameSite' to the exact casing Playwright addCookies() expects
    #      ('Strict' | 'Lax' | 'None'). CDP returns lowercase; Playwright wants title-case.
    #    - Strip 'expires: -1' (session cookies) — Playwright will reject negative expiry.
    #    - Ensure 'domain' does NOT have a leading dot for addCookies() compatibility.
    #      (Playwright's addCookies prefers 'example.com' not '.example.com'.)
    import base64
    export_script = r"""
const { chromium } = require('/usr/local/lib/node_modules/playwright');
let browser;
(async () => {
    let ok = false;
    try {
        browser = await chromium.connectOverCDP('http://localhost:9222');
        const context = browser.contexts()[0];
        // Fetch ALL cookies from the browser profile (no URL filter = full export)
        const rawCookies = await context.cookies();

        // Sanitize cookies so they can be re-injected by Playwright's addCookies()
        const sameSiteMap = { none: 'None', lax: 'Lax', strict: 'Strict' };
        const cookies = rawCookies.map(c => {
            const out = { ...c };
            // Normalize sameSite casing
            if (out.sameSite != null) {
                out.sameSite = sameSiteMap[String(out.sameSite).toLowerCase()] || 'Lax';
            }
            // Remove negative or zero expires (session cookies) — addCookies rejects them
            if (out.expires != null && out.expires <= 0) {
                delete out.expires;
            }
            // Ensure domain has leading dot so it matches subdomains.
            // Playwright's context.cookies() strips the leading dot from
            // domain cookies, turning them into host-only. Chrome's CDP
            // Network.setCookie needs the dot to match subdomains (e.g.,
            // ".xiaohongshu.com" matches www.xiaohongshu.com).
            if (out.domain && !out.domain.startsWith('.')) {
                out.domain = '.' + out.domain;
            }
            return out;
        });

        console.log('COOKIES_EXPORT:' + JSON.stringify(cookies));
        ok = true;
    } catch (e) {
        console.error('EXPORT_FAIL:' + e.message);
    } finally {
        if (browser) await browser.close().catch(() => {});
    }
    process.exit(ok ? 0 : 1);
})();
"""
    # Use base64 encoding to write script to current directory (not /tmp, which may lack write perms)
    script_b64 = base64.b64encode(export_script.encode('utf-8')).decode('ascii')
    write_result = await client.command_exec(
        f"echo '{script_b64}' | /usr/bin/base64 -d > tc_export_cookies.js"
    )
    logger.info(f"[TakeControl] Cookie export script write: success={write_result.get('success')}, stderr={write_result.get('stderr', '')[:100]}")
    
    result = await client.command_exec("node tc_export_cookies.js", timeout_ms=15000)
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")
    logger.info(f"[TakeControl] Cookie export script exec: success={result.get('success')}, stdout_len={len(stdout)}, stderr={stderr[:200]}")

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
