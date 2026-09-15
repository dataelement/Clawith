"""Private Channel data access; configuration and delivery records have one owner."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channel.models import AgentChannelConfigurationRecord, ChannelDeliveryRecord


class ChannelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def channel(self, tenant_id: UUID, channel_id: UUID, *, lock: bool = False):
        query = select(AgentChannelConfigurationRecord).where(AgentChannelConfigurationRecord.tenant_id == tenant_id,
            AgentChannelConfigurationRecord.id == channel_id)
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query.execution_options(populate_existing=True))

    async def list_channels(self, tenant_id: UUID, agent_id: UUID, *, limit: int, offset: int):
        return tuple(await self.session.scalars(select(AgentChannelConfigurationRecord).where(
            AgentChannelConfigurationRecord.tenant_id == tenant_id, AgentChannelConfigurationRecord.agent_id == agent_id)
            .order_by(AgentChannelConfigurationRecord.id).limit(limit).offset(offset)))

    async def delivery(self, tenant_id: UUID, delivery_id: UUID, *, lock: bool = False):
        query = select(ChannelDeliveryRecord).where(ChannelDeliveryRecord.tenant_id == tenant_id,
            ChannelDeliveryRecord.id == delivery_id)
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query.execution_options(populate_existing=True))

    async def delivery_by_key(self, tenant_id: UUID, channel_id: UUID, key: str):
        return await self.session.scalar(select(ChannelDeliveryRecord).where(ChannelDeliveryRecord.tenant_id == tenant_id,
            ChannelDeliveryRecord.channel_configuration_id == channel_id, ChannelDeliveryRecord.delivery_key == key))
