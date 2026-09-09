"""Explicit adapter construction; application owns clients and listener tasks."""

from types import MappingProxyType

import httpx

from app.modules.channel.adapters import SlackAdapter
from app.modules.channel.contracts import ChannelAdapters
from app.modules.channel.providers.dingtalk import DingTalkAdapter
from app.modules.channel.providers.discord import DiscordAdapter
from app.modules.channel.providers.feishu import FeishuAdapter
from app.modules.channel.providers.teams import TeamsAdapter
from app.modules.channel.providers.wechat import WeChatAdapter
from app.modules.channel.providers.wecom import WeComAdapter


def create_channel_adapters(http: httpx.AsyncClient) -> ChannelAdapters:
    feishu, wecom = FeishuAdapter(http), WeComAdapter(http)
    discord = DiscordAdapter(http)
    dingtalk, wechat = DingTalkAdapter(http), WeChatAdapter(http)
    return ChannelAdapters((SlackAdapter(http), discord, TeamsAdapter(http), feishu, wecom, dingtalk, wechat),
        MappingProxyType({"feishu": feishu, "wecom": wecom, "discord": discord,
            "dingtalk": dingtalk, "wechat": wechat}))
