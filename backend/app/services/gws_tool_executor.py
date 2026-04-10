"""Google Workspace CLI (gws) tool executor.

Executes gws CLI commands with automatic OAuth credential injection.
"""

import asyncio
import os
import shutil
import uuid

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.core.security import decrypt_data
from app.database import async_session
from app.models.gws_oauth_token import GwsOAuthToken
from app.services.gws_service import refresh_access_token

settings = get_settings()


async def execute_gws_command(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    command: str,
    timeout: int = 60,
) -> dict:
    async with async_session() as db:
        result = await db.execute(
            select(GwsOAuthToken).where(
                GwsOAuthToken.agent_id == agent_id,
                GwsOAuthToken.user_id == user_id,
                GwsOAuthToken.status == "active",
            )
        )
        token_record = result.scalar_one_or_none()

    if not token_record:
        return {
            "error": "Google Workspace not authorized. Ask the user to connect their Google account in agent settings."
        }

    access_token = await _get_valid_access_token(token_record)

    gws_path = await _ensure_gws_installed()
    if not gws_path:
        return {
            "error": "Google Workspace CLI is not available. Please contact your administrator to install @googleworkspace/cli, or wait a moment while it system attempts automatic installation."
        }

    full_command = f"{gws_path} {command}"

    safe_env = dict(os.environ)
    safe_env["GOOGLE_WORKSPACE_CLI_TOKEN"] = access_token

    try:
        proc = await asyncio.create_subprocess_shell(
            full_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"error": f"Command timed out after {timeout}s"}

        stdout_str = stdout.decode("utf-8", errors="replace")
        stderr_str = stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            return {
                "output": stdout_str[:10000],
                "error": stderr_str[:5000] or f"Exit code: {proc.returncode}",
                "exit_code": proc.returncode,
            }

        return {"output": stdout_str[:10000]}

    except Exception as e:
        logger.exception(f"[GWS] Execution failed: {e}")
        return {"error": f"Execution error: {str(e)[:200]}"}


async def _get_valid_access_token(token_record: GwsOAuthToken) -> str:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    needs_refresh = (
        token_record.token_expiry is None
        or token_record.token_expiry <= now + timedelta(minutes=5)
    )

    if needs_refresh:
        if not token_record.tenant_id:
            raise ValueError("Cannot refresh token: tenant_id is missing")

        logger.info(f"[GWS] Refreshing expired token for agent {token_record.agent_id}")
        return await refresh_access_token(token_record, token_record.tenant_id)

    return decrypt_data(token_record.access_token, settings.SECRET_KEY)


def _find_gws_cli() -> str | None:
    gws = shutil.which("gws")
    if gws:
        return gws

    common_paths = [
        "/usr/local/bin/gws",
        "/usr/bin/gws",
        os.path.expanduser("~/.npm-global/bin/gws"),
        os.path.expanduser("~/node_modules/.bin/gws"),
    ]

    for path in common_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    return None


async def _ensure_gws_installed() -> str | None:
    gws_path = _find_gws_cli()
    if gws_path:
        return gws_path

    logger.info("[GWS] gws CLI not found, attempting on-demand installation...")

    try:
        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "-g", "@googleworkspace/cli",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        if proc.returncode == 0:
            logger.info("[GWS] Successfully installed @googleworkspace/cli")
            return _find_gws_cli()
        else:
            stderr_str = stderr.decode('utf-8', errors='replace') if stderr else ''
            logger.error(f"[GWS] Installation failed with code {proc.returncode}: {stderr_str[:500]}")
            return None
    except asyncio.TimeoutError:
        logger.error("[GWS] Installation timed out after 120s")
        return None
    except Exception as e:
        logger.error(f"[GWS] Failed to install gws CLI: {e}")
        return None
