"""Platform-wide service for URL resolution and host type detection."""

import os
import re
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.system_settings import SystemSetting

class PlatformService:
    """Service to handle platform-wide settings and URL resolution."""

    def is_ip_address(self, host: str) -> bool:
        """Check if a host is an IP address (IPv4)."""
        # Strip protocol and port if present
        h = host.split("://")[-1].split(":")[0].split("/")[0]
        # Basic IPv4 regex
        ip_pattern = re.compile(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$")
        return bool(ip_pattern.match(h))

    async def get_public_base_url(self, db: AsyncSession | None = None, request: Request | None = None) -> str:
        """Resolve the platform's public base URL with priority lookup.
        
        Priority:
        1. Environment variable (PUBLIC_BASE_URL) - from .env or docker
        2. Incoming request's base URL (browser address)
        3. Hardcoded fallback (https://try.clawith.ai)
        """
        # 1. Try environment variable
        env_url = os.environ.get("PUBLIC_BASE_URL")
        if env_url:
            return env_url.rstrip("/")

        # 2. Fallback to request (browser address)
        if request:
            # Note: request.base_url might include trailing slash
            return str(request.base_url).rstrip("/")

        # 3. Absolute fallback
        return "https://try.clawith.ai"


    async def get_tenant_sso_base_url(self, db: AsyncSession, tenant, request: Request | None = None) -> str:
        """Generate the SSO base URL for a tenant based on IP/Domain logic.
        
        Priority:
        1. Explicit sso_domain stored in tenant record (if present)
        2. Auto-generated URL based on the unified public_base_url (ENV > Request > Fallback)
        """
        if tenant.sso_domain:
            return tenant.sso_domain.rstrip("/")

        base_url = await self.get_public_base_url(db, request)
        
        # Parse protocol and host
        # Example: http://1.2.3.4:8000 or http://clawith.ai
        parts = base_url.split("://")
        if len(parts) < 2:
            return base_url
            
        protocol = parts[0]
        host_port = parts[1]
        
        # Split host and port
        host_parts = host_port.split(":")
        host = host_parts[0]
        port = f":{host_parts[1]}" if len(host_parts) > 1 else ""
        
        if self.is_ip_address(host):
            # IP: No subdomain, just base URL
            return base_url
        else:
            # Domain: {tenant_slug}.{domain}
            # Special case for localhost: keep it as is or handle it
            if host == "localhost":
                return f"{protocol}://{host}{port}"
                
            # Generic logic: if host has a subdomain (e.g. try.clawith.ai), 
            # we strip the first component to form a base for tenant subdomains.
            h_parts = host.split(".")
            if len(h_parts) > 2:
                target_host = ".".join(h_parts[1:])
            else:
                target_host = host
                
            return f"{protocol}://{tenant.slug}.{target_host}{port}"


# Global instance
platform_service = PlatformService()
