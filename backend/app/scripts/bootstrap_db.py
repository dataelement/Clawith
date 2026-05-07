"""Bootstrap database tables and additive schema patches for container startup."""

import asyncio

from sqlalchemy import text

from app.database import Base, engine

# Import all models so Base.metadata is fully populated before create_all.
import app.models.activity_log  # noqa: F401
import app.models.agent  # noqa: F401
import app.models.audit  # noqa: F401
import app.models.channel_config  # noqa: F401
import app.models.chat_session  # noqa: F401
import app.models.gateway_message  # noqa: F401
import app.models.invitation_code  # noqa: F401
import app.models.llm  # noqa: F401
import app.models.notification  # noqa: F401
import app.models.org  # noqa: F401
import app.models.participant  # noqa: F401
import app.models.plaza  # noqa: F401
import app.models.schedule  # noqa: F401
import app.models.skill  # noqa: F401
import app.models.system_settings  # noqa: F401
import app.models.task  # noqa: F401
import app.models.tenant  # noqa: F401
import app.models.tool  # noqa: F401
import app.models.trigger  # noqa: F401
import app.models.user  # noqa: F401


PATCHES = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_message_limit INTEGER DEFAULT 50",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_message_period VARCHAR(20) DEFAULT 'permanent'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_messages_used INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_period_start TIMESTAMPTZ",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_max_agents INTEGER DEFAULT 2",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_agent_ttl_hours INTEGER DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_expired BOOLEAN DEFAULT FALSE",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_calls_today INTEGER DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_llm_calls_per_day INTEGER DEFAULT 1000",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_calls_reset_at TIMESTAMPTZ",
    "ALTER TABLE agent_tools ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'system'",
    "ALTER TABLE agent_tools ADD COLUMN IF NOT EXISTS installed_by_agent_id UUID",
    "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS source_channel VARCHAR(20) NOT NULL DEFAULT 'web'",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_daily_reset TIMESTAMPTZ",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_monthly_reset TIMESTAMPTZ",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS tokens_used_total INTEGER DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS cache_read_tokens_today INTEGER DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS cache_read_tokens_month INTEGER DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS cache_read_tokens_total INTEGER DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS cache_creation_tokens_today INTEGER DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS cache_creation_tokens_month INTEGER DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS cache_creation_tokens_total INTEGER DEFAULT 0",
    "ALTER TABLE daily_token_usage ADD COLUMN IF NOT EXISTS input_tokens INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE daily_token_usage ADD COLUMN IF NOT EXISTS output_tokens INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE daily_token_usage ADD COLUMN IF NOT EXISTS cache_read_tokens INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE daily_token_usage ADD COLUMN IF NOT EXISTS cache_creation_tokens INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE daily_token_usage ADD COLUMN IF NOT EXISTS estimated_tokens INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS agent_type VARCHAR(20) NOT NULL DEFAULT 'native'",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS api_key_hash VARCHAR(128)",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS openclaw_last_seen TIMESTAMPTZ",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS sso_enabled BOOLEAN DEFAULT FALSE",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS sso_domain VARCHAR(255)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_tenants_sso_domain ON tenants(sso_domain) WHERE sso_domain IS NOT NULL",
]


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[entrypoint] Tables created/verified", flush=True)

    patch_timeout_sql = text("SET lock_timeout = '2000ms'")
    for sql in PATCHES:
        try:
            async with engine.begin() as conn:
                await conn.execute(patch_timeout_sql)
                await conn.execute(text(sql))
                print(f"[entrypoint] Patch applied: {sql}", flush=True)
        except Exception as exc:  # pragma: no cover - startup best-effort path
            print(f"[entrypoint] Patch skipped: {sql} ({exc})", flush=True)

    await engine.dispose()
    print("[entrypoint] Column patches applied", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
