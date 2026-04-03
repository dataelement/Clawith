"""WeChat Gateway Manager.

Manages communication with the Node.js WeChat gateway service.
The Node.js service handles WeChat iLink protocol connections using
the @wechatbot/wechatbot SDK, while this Python service manages
agent-bot mappings and message routing.

Architecture:

    ┌─────────────────┐     HTTP      ┌─────────────────┐
    │  Python Backend │◄─────────────►│  Node.js Gateway│
    │  (FastAPI)      │               │  (wechatbot SDK)│
    └─────────────────┘               └─────────────────┘
           │                                   │
           ▼                                   ▼
    ┌─────────────────┐               ┌─────────────────┐
    │   PostgreSQL    │               │    WeChat iLink │
    │   (agents, msgs)│               │    API / QR     │
    └─────────────────┘               └─────────────────┘
"""

import asyncio
import os
import uuid
from typing import Dict, Optional

import httpx
from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.channel_config import ChannelConfig

# Gateway service URL (Node.js microservice)
WECHAT_GATEWAY_URL = os.environ.get("WECHAT_GATEWAY_URL", "http://wechat-gateway:3100")


class WeChatGatewayManager:
    """Manages WeChat gateway clients for all agents.

    The actual WeChat connections are managed by the Node.js gateway service.
    This manager handles:
    - Initiating login (QR code generation)
    - Starting/stopping bot instances
    - Checking connection status
    """

    def __init__(self):
        self._gateway_url = WECHAT_GATEWAY_URL
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for gateway communication."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def initiate_login(
        self,
        agent_id: uuid.UUID,
        storage_dir: Optional[str] = None,
        force: bool = False,
    ) -> tuple[Optional[str], Optional[str]]:
        """Initiate QR login for an agent's WeChat bot.

        Args:
            agent_id: The agent UUID
            storage_dir: Optional custom storage directory
            force: If True, force re-login even if already logged in

        Returns:
            A tuple of (qr_url, error_message). qr_url is the QR code URL that
            the user needs to scan, error_message contains any error details.
        """
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._gateway_url}/bots/{agent_id}/login",
                json={"storage_dir": storage_dir, "force": force},
            )

            data = response.json()

            if response.status_code == 200:
                qr_url = data.get("qr_url")
                if qr_url:
                    logger.info(f"[WeChat GW] Login initiated for agent {agent_id}, QR: {qr_url[:50]}...")
                    return qr_url, None
                else:
                    # Already logged in and running
                    message = data.get("message", "Already logged in")
                    logger.info(f"[WeChat GW] Agent {agent_id}: {message}")
                    return None, None
            else:
                error_msg = data.get("error", f"Gateway returned {response.status_code}")
                logger.error(f"[WeChat GW] Failed to initiate login: {response.status_code} - {error_msg}")
                return None, error_msg

        except httpx.ConnectError:
            error_msg = f"Cannot connect to gateway at {self._gateway_url}"
            logger.error(f"[WeChat GW] {error_msg}")
            return None, error_msg
        except Exception as e:
            logger.exception(f"[WeChat GW] Error initiating login: {e}")
            return None, str(e)

    async def get_qr_url(self, agent_id: uuid.UUID) -> Optional[str]:
        """Get the current QR code URL for an agent's login flow."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self._gateway_url}/bots/{agent_id}/qr")

            if response.status_code == 200:
                data = response.json()
                return data.get("qr_url")
            return None

        except Exception as e:
            logger.error(f"[WeChat GW] Error getting QR: {e}")
            return None

    async def start_client(
        self,
        agent_id: uuid.UUID,
        storage_dir: Optional[str] = None,
    ) -> bool:
        """Start the WeChat bot for an agent.

        This should be called after successful login (credentials exist).
        """
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._gateway_url}/bots/{agent_id}/start",
                json={"storage_dir": storage_dir},
            )

            if response.status_code == 200:
                logger.info(f"[WeChat GW] Bot started for agent {agent_id}")
                return True
            else:
                logger.error(f"[WeChat GW] Failed to start bot: {response.text}")
                return False

        except Exception as e:
            logger.exception(f"[WeChat GW] Error starting bot: {e}")
            return False

    async def stop_client(self, agent_id: uuid.UUID) -> bool:
        """Stop the WeChat bot for an agent."""
        try:
            client = await self._get_client()
            response = await client.post(f"{self._gateway_url}/bots/{agent_id}/stop")

            if response.status_code == 200:
                logger.info(f"[WeChat GW] Bot stopped for agent {agent_id}")
                return True
            return False

        except Exception as e:
            logger.error(f"[WeChat GW] Error stopping bot: {e}")
            return False

    async def remove_client(self, agent_id: uuid.UUID) -> bool:
        """Remove the WeChat bot completely (stops and clears credentials)."""
        try:
            client = await self._get_client()
            response = await client.delete(f"{self._gateway_url}/bots/{agent_id}")

            if response.status_code == 200:
                logger.info(f"[WeChat GW] Bot removed for agent {agent_id}")
                return True
            return False

        except Exception as e:
            logger.error(f"[WeChat GW] Error removing bot: {e}")
            return False

    async def get_status(self, agent_id: uuid.UUID) -> Dict:
        """Get the connection status of an agent's WeChat bot."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self._gateway_url}/bots/{agent_id}/status")

            if response.status_code == 200:
                return response.json()
            return {
                "is_running": False,
                "is_logged_in": False,
                "qr_url": None,
                "error": "Failed to get status from gateway",
            }

        except httpx.ConnectError:
            return {
                "is_running": False,
                "is_logged_in": False,
                "qr_url": None,
                "error": f"Gateway not reachable at {self._gateway_url}",
            }
        except Exception as e:
            return {
                "is_running": False,
                "is_logged_in": False,
                "qr_url": None,
                "error": str(e),
            }

    async def send_message(
        self,
        agent_id: uuid.UUID,
        user_id: str,
        text: str,
    ) -> bool:
        """Send a message to a WeChat user via the gateway.

        This is used for proactive messaging (agent-initiated).
        """
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._gateway_url}/bots/{agent_id}/send",
                json={"user_id": user_id, "text": text},
            )
            return response.status_code == 200

        except Exception as e:
            logger.error(f"[WeChat GW] Error sending message: {e}")
            return False

    async def send_file(
        self,
        agent_id: uuid.UUID,
        user_id: str,
        file_path,
        caption: str = "",
    ) -> bool:
        """Send a file to a WeChat user via the gateway.

        Args:
            agent_id: The agent UUID
            user_id: WeChat user ID to send to
            file_path: Path to the file (Path object or string)
            caption: Optional caption for the file

        Returns True if sent successfully.
        """
        import base64
        from pathlib import Path

        try:
            file_path = Path(file_path)
            if not file_path.exists():
                logger.error(f"[WeChat GW] File not found: {file_path}")
                return False

            # Read and encode file
            file_data = base64.b64encode(file_path.read_bytes()).decode('utf-8')

            client = await self._get_client()
            response = await client.post(
                f"{self._gateway_url}/bots/{agent_id}/send-file",
                json={
                    "user_id": user_id,
                    "file_data": file_data,
                    "file_name": file_path.name,
                    "caption": caption,
                },
                timeout=60.0,  # Longer timeout for file upload
            )

            if response.status_code == 200:
                logger.info(f"[WeChat GW] File sent: {file_path.name}")
                return True
            else:
                logger.error(f"[WeChat GW] Failed to send file: {response.text}")
                return False

        except Exception as e:
            logger.error(f"[WeChat GW] Error sending file: {e}")
            return False

    async def start_all(self):
        """Start bots for all configured WeChat channels.

        Called during application startup to restore connections.
        """
        logger.info("[WeChat GW] Initializing all active WeChat channels...")

        async with async_session() as db:
            result = await db.execute(
                select(ChannelConfig).where(
                    ChannelConfig.is_configured == True,
                    ChannelConfig.channel_type == "wechat",
                )
            )
            configs = result.scalars().all()

        for config in configs:
            extra = config.extra_config or {}
            storage_dir = extra.get("storage_dir")

            # Try to start the bot (will succeed if credentials exist)
            success = await self.start_client(config.agent_id, storage_dir)
            if success:
                logger.info(f"[WeChat GW] Restored bot for agent {config.agent_id}")
            else:
                logger.warning(
                    f"[WeChat GW] Could not restore bot for agent {config.agent_id}. "
                    "User may need to re-login via QR code."
                )

    def set_gateway_url(self, url: str):
        """Update the gateway URL (useful for testing)."""
        self._gateway_url = url


# Module-level singleton
wechat_gateway_manager = WeChatGatewayManager()
