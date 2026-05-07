# Agent 交互优先级管理模块 — 设计与实现文档

> **文档状态**：实现就绪（Ready for Implementation）  
> **关联规范**：`preferred_contact_channel` 字段命名标准，多端消息架构（Web/Native Message 区分原则）  
> **负责人**：覃睿  
> **创建日期**：2026-04-19

---

## 一、设计背景与目标

### 问题陈述

当前 Clawith 的 Agent 主动消息分发存在以下问题：

1. **渠道分散，无统一优先级**：Agent 需要手动选择 `send_web_message`、`send_channel_message`、`send_feishu_message` 等独立工具，缺乏统一入口
2. **无联系渠道偏好记录**：系统未在 `User` 层存储 `preferred_contact_channel`（用户偏好联系渠道），导致每次触达都是「盲投」
3. **降级链路缺失**：当 Web 用户不在线时，没有自动 fallback 到 Feishu/DingTalk 的机制
4. **优先级策略不可配置**：不同 Agent 面对不同业务场景（OKR 提醒 vs. 紧急审批 vs. 报告推送），触达优先级需要差异化配置

### 设计目标

- ✅ 在 `User` 模型新增 `preferred_contact_channel` 字段（标准命名已确认）
- ✅ 实现统一的 `InteractionPriorityManager` 服务，支持多级优先级路由
- ✅ 新增 Agent 工具 `send_priority_message`，替代分散的发送工具调用
- ✅ 优先级策略可按 Agent 和用户两个维度配置
- ✅ 完整的降级链路与发送结果记录

---

## 二、核心概念

### 2.1 渠道分类

按照已确立的**Web/Native Message 命名原则**，将渠道分为两大类：

| 类别 | 渠道标识 | 说明 |
|------|---------|------|
| **Web Channel** | `web` | Clawith Web 平台实时 WebSocket 推送 |
| **Native/APP Channel** | `feishu` | 飞书机器人消息 |
| **Native/APP Channel** | `dingtalk` | 钉钉机器人消息 |
| **Native/APP Channel** | `wecom` | 企业微信消息 |
| **Native/APP Channel** | `slack` | Slack 消息 |
| **Native/APP Channel** | `discord` | Discord 消息 |
| **Native/APP Channel** | `email` | 邮件（兜底渠道）|

### 2.2 优先级策略（Priority Policy）

```
PRIORITY_LEVEL_MAP = {
    "web_first":    ["web", "feishu", "dingtalk", "wecom", "email"],
    "native_first": ["feishu", "dingtalk", "wecom", "web", "email"],
    "feishu_only":  ["feishu"],
    "web_only":     ["web"],
    "all_channels": ["web", "feishu", "dingtalk", "wecom", "slack", "discord", "email"],
}
```

**策略说明**：
- `web_first`（默认）：先尝试 Web 实时推送（用户在线则立即送达），不在线则自动降级到 Native 渠道
- `native_first`：用于对时效性要求高的场景（如 OKR Deadline 提醒），优先走飞书等 IM 确保送达
- `feishu_only` / `web_only`：强制单渠道，用于特定集成场景
- `all_channels`：广播模式，所有渠道同时触达（谨慎使用，避免骚扰）

### 2.3 交互请求模型

```python
@dataclass
class AgentInteractionRequest:
    """Agent 发起的交互请求实体"""
    agent_id: uuid.UUID              # 发送方 Agent
    target_user_id: uuid.UUID        # 目标用户（platform User.id）
    message: str                     # 消息正文
    priority_policy: str = "web_first"   # 优先级策略
    interaction_type: str = "notify"     # notify | alert | task_delegate | approval_request
    subject: str | None = None           # 消息标题（用于邮件/通知标题）
    ref_id: uuid.UUID | None = None      # 关联对象 ID（如 Task ID、OKR ID）
    ref_type: str | None = None          # 关联类型（"task" | "okr" | "approval"）
    fallback_all: bool = True            # 首选渠道失败后是否继续尝试其他渠道
    dedupe_window_minutes: int = 0       # 去重窗口（防止同一消息在短期内重复发送，0=不去重）
```

---

## 三、数据库变更

### 3.1 `users` 表新增字段

**Alembic Migration 文件**（创建于 `backend/alembic/versions/`）：

```python
# backend/alembic/versions/xxxx_add_preferred_contact_channel_to_users.py
"""add preferred_contact_channel to users

Revision ID: add_preferred_contact_channel
Revises: <上一个 revision_id>
Create Date: 2026-04-19
"""
from alembic import op
import sqlalchemy as sa

def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "preferred_contact_channel",
            sa.String(20),
            nullable=True,
            server_default=None,
            comment="用户偏好的 Agent 联系渠道，如 web / feishu / dingtalk / wecom / email。"
                    "为 NULL 时由 Agent 按 priority_policy 自动决策。"
        ),
    )
    # 同步添加索引，供批量优先级查询使用
    op.create_index(
        "ix_users_preferred_contact_channel",
        "users",
        ["preferred_contact_channel"],
        postgresql_where=sa.text("preferred_contact_channel IS NOT NULL"),
    )

def downgrade() -> None:
    op.drop_index("ix_users_preferred_contact_channel", table_name="users")
    op.drop_column("users", "preferred_contact_channel")
```

### 3.2 `user.py` 模型新增字段

在 `backend/app/models/user.py` 的 `User` 类中，在 `registration_source` 字段下方插入：

```python
# 用户偏好联系渠道（preferred_contact_channel）
# 取值: 'web' | 'feishu' | 'dingtalk' | 'wecom' | 'slack' | 'discord' | 'email' | None
# 为 None 时，InteractionPriorityManager 按 Agent 的 priority_policy 自动决策
preferred_contact_channel: Mapped[str | None] = mapped_column(
    String(20), default=None, nullable=True, index=True
)
```

---

## 四、核心服务：`InteractionPriorityManager`

**文件路径**：`backend/app/services/interaction_priority.py`

```python
"""Agent 交互优先级管理服务。

职责：
- 统一管理 Agent 主动发起的交互请求
- 根据 preferred_contact_channel（用户偏好）+ priority_policy（Agent策略）决定渠道顺序
- 按优先级依次尝试各渠道，首个成功则停止（fallback_all=True 时继续）
- 记录每次触达结果（成功/失败/渠道）供后续审计
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Awaitable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.agent import Agent
from app.models.user import User


# ─── 渠道优先级映射表 ─────────────────────────────────────────────────────────

CHANNEL_PRIORITY_POLICIES: dict[str, list[str]] = {
    # 默认策略：Web 在线则优先实时推送，离线降级到 Native IM
    "web_first":    ["web", "feishu", "dingtalk", "wecom", "slack", "discord", "email"],
    # Native 优先：适合 OKR Deadline、审批请求等对到达率要求高的场景
    "native_first": ["feishu", "dingtalk", "wecom", "web", "slack", "discord", "email"],
    # 单渠道强制
    "feishu_only":  ["feishu"],
    "dingtalk_only":["dingtalk"],
    "wecom_only":   ["wecom"],
    "web_only":     ["web"],
    # 广播：同时发送所有配置渠道（高优先级紧急通知用）
    "broadcast":    ["web", "feishu", "dingtalk", "wecom", "slack", "discord", "email"],
}

# 广播模式：发送所有渠道（不在首个成功时停止）
BROADCAST_POLICIES = {"broadcast", "all_channels"}


# ─── 请求数据模型 ─────────────────────────────────────────────────────────────

@dataclass
class AgentInteractionRequest:
    """Agent 发起的交互请求。"""
    agent_id: uuid.UUID
    target_user_id: uuid.UUID
    message: str
    priority_policy: str = "web_first"
    interaction_type: str = "notify"   # notify | alert | task_delegate | approval_request
    subject: str | None = None
    ref_id: uuid.UUID | None = None
    ref_type: str | None = None
    fallback_all: bool = True
    dedupe_window_minutes: int = 0


@dataclass
class ChannelDeliveryResult:
    """单渠道投递结果。"""
    channel: str
    success: bool
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class InteractionDispatchResult:
    """完整的交互分发结果，包含所有尝试记录。"""
    request: AgentInteractionRequest
    delivered_via: list[str]          # 成功送达的渠道列表
    failed_channels: list[str]        # 失败的渠道列表
    channel_results: list[ChannelDeliveryResult]
    final_status: str                 # "delivered" | "partially_delivered" | "failed"
    summary: str                      # 人类可读的结果摘要


# ─── 渠道检测器：判断用户在各渠道是否可达 ────────────────────────────────────

class ChannelAvailabilityChecker:
    """检测目标用户在各渠道的可达状态。"""

    @staticmethod
    async def is_web_online(agent_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """检查目标用户是否有活跃的 WebSocket 连接。
        
        通过 WebSocket ConnectionManager 的 active_connections 判断。
        注意：此方法检测的是 Agent 维度的连接，需在未来扩展为 User 维度。
        当前实现：如果该 Agent 有任何活跃连接，视为「可能在线」。
        """
        try:
            from app.api.websocket import manager as ws_manager
            agent_id_str = str(agent_id)
            connections = ws_manager.active_connections.get(agent_id_str, [])
            return len(connections) > 0
        except Exception as e:
            logger.debug(f"[Priority] Web online check failed: {e}")
            return False

    @staticmethod
    async def has_channel_config(
        db: AsyncSession, agent_id: uuid.UUID, channel_type: str
    ) -> bool:
        """检查 Agent 是否配置了指定渠道。"""
        from app.models.channel_config import ChannelConfig
        result = await db.execute(
            select(ChannelConfig).where(
                ChannelConfig.agent_id == agent_id,
                ChannelConfig.channel_type == channel_type,
                ChannelConfig.is_configured == True,
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def has_native_identity(
        db: AsyncSession, user_id: uuid.UUID, channel_type: str
    ) -> bool:
        """检查目标用户在指定 Native 渠道是否有可用的身份信息（open_id 等）。"""
        from app.models.org import OrgMember
        from app.models.identity import IdentityProvider
        
        result = await db.execute(
            select(OrgMember).join(
                IdentityProvider, OrgMember.provider_id == IdentityProvider.id
            ).where(
                OrgMember.user_id == user_id,
                IdentityProvider.provider_type == channel_type,
                OrgMember.status == "active",
            )
        )
        member = result.scalar_one_or_none()
        if not member:
            return False
        # 必须有至少一个可用的 ID
        return bool(member.external_id or member.open_id or member.unionid)


# ─── 渠道发送适配器 ───────────────────────────────────────────────────────────

class ChannelDispatchAdapter:
    """各渠道的发送适配器，统一调用现有服务。"""

    @staticmethod
    async def send_web(
        agent_id: uuid.UUID,
        user_id: uuid.UUID,
        request: AgentInteractionRequest,
    ) -> ChannelDeliveryResult:
        """通过 Web WebSocket 推送消息（调用现有 _send_web_message 逻辑）。"""
        try:
            from app.services.agent_tools import _send_web_message
            # 获取 username 用于 _send_web_message 查找
            async with async_session() as db:
                user = await db.get(User, user_id)
                if not user:
                    return ChannelDeliveryResult(
                        channel="web", success=False,
                        message=f"用户 {user_id} 不存在"
                    )
                username = user.display_name or user.username or str(user_id)

            result_str = await _send_web_message(
                agent_id,
                {"username": username, "message": request.message}
            )
            success = result_str.startswith("✅")
            return ChannelDeliveryResult(
                channel="web", success=success, message=result_str
            )
        except Exception as e:
            return ChannelDeliveryResult(
                channel="web", success=False, message=f"Web 发送异常: {e}"
            )

    @staticmethod
    async def send_native_channel(
        agent_id: uuid.UUID,
        user_id: uuid.UUID,
        channel_type: str,
        request: AgentInteractionRequest,
    ) -> ChannelDeliveryResult:
        """通过 Native IM 渠道（飞书/钉钉/企微）发送消息。
        
        内部通过 OrgMember 解析出 member_name，再调用现有的 _send_channel_message。
        """
        try:
            from app.services.agent_tools import _send_channel_message
            from app.models.org import OrgMember, AgentRelationship
            from app.models.identity import IdentityProvider

            async with async_session() as db:
                # 找到该用户在目标渠道的 OrgMember 记录，获取 member_name
                result = await db.execute(
                    select(OrgMember).join(
                        IdentityProvider, OrgMember.provider_id == IdentityProvider.id
                    ).where(
                        OrgMember.user_id == user_id,
                        IdentityProvider.provider_type == channel_type,
                        OrgMember.status == "active",
                    )
                )
                member = result.scalar_one_or_none()
                
                if not member:
                    return ChannelDeliveryResult(
                        channel=channel_type, success=False,
                        message=f"用户在 {channel_type} 渠道无可用身份信息"
                    )

                result_str = await _send_channel_message(
                    agent_id,
                    {
                        "member_name": member.name,
                        "message": request.message,
                        "channel": channel_type,
                    }
                )
                success = result_str.startswith("✅")
                return ChannelDeliveryResult(
                    channel=channel_type, success=success, message=result_str
                )
        except Exception as e:
            return ChannelDeliveryResult(
                channel=channel_type, success=False,
                message=f"{channel_type} 发送异常: {e}"
            )

    @staticmethod
    async def send_email(
        agent_id: uuid.UUID,
        user_id: uuid.UUID,
        request: AgentInteractionRequest,
    ) -> ChannelDeliveryResult:
        """通过邮件发送（兜底渠道）。"""
        try:
            from app.services.email_service import email_service

            async with async_session() as db:
                user = await db.get(User, user_id)
                if not user or not user.email:
                    return ChannelDeliveryResult(
                        channel="email", success=False,
                        message="用户无有效邮箱地址"
                    )
                subject = request.subject or f"[{request.interaction_type.upper()}] Agent 消息通知"
                await email_service.send_plain(
                    to=user.email,
                    subject=subject,
                    body=request.message,
                )
                return ChannelDeliveryResult(
                    channel="email", success=True,
                    message=f"✅ 邮件已发送至 {user.email}"
                )
        except Exception as e:
            return ChannelDeliveryResult(
                channel="email", success=False,
                message=f"邮件发送异常: {e}"
            )


# ─── 核心管理器 ───────────────────────────────────────────────────────────────

class InteractionPriorityManager:
    """Agent 交互优先级管理器。
    
    使用方法：
        result = await InteractionPriorityManager.dispatch(
            AgentInteractionRequest(
                agent_id=agent.id,
                target_user_id=user.id,
                message="您的 OKR Q2 进度评估已完成，请查看报告。",
                priority_policy="native_first",  # OKR 提醒优先用 IM
                interaction_type="notify",
                ref_type="okr",
            )
        )
        logger.info(result.summary)
    """

    checker = ChannelAvailabilityChecker()
    adapter = ChannelDispatchAdapter()

    @classmethod
    async def _resolve_channel_order(
        cls,
        db: AsyncSession,
        request: AgentInteractionRequest,
        user: User,
    ) -> list[str]:
        """决定本次分发的渠道顺序。
        
        优先级决策逻辑（按优先级从高到低）：
        1. 用户明确设置了 preferred_contact_channel → 强制使用该渠道（仅该渠道）
        2. 使用 request.priority_policy 映射的渠道顺序
        3. 过滤掉 Agent 未配置的渠道和用户无身份的渠道
        """
        # 规则 1：用户偏好渠道优先（用户主权）
        if user.preferred_contact_channel:
            preferred = user.preferred_contact_channel
            logger.debug(
                f"[Priority] User {user.id} has preferred_contact_channel={preferred}, using exclusively"
            )
            return [preferred]

        # 规则 2：按 policy 获取渠道列表
        policy = request.priority_policy
        channel_order = CHANNEL_PRIORITY_POLICIES.get(policy, CHANNEL_PRIORITY_POLICIES["web_first"])

        # 规则 3：可用性过滤
        available = []
        for ch in channel_order:
            if ch == "web":
                # Web 渠道总是可用（即使用户不在线，消息也会存入 session 待查）
                available.append(ch)
            elif ch == "email":
                # 邮件在用户有邮箱时可用
                if user.email:
                    available.append(ch)
            else:
                # Native 渠道：需要 Agent 已配置 + 用户有对应身份
                has_config = await cls.checker.has_channel_config(db, request.agent_id, ch)
                has_identity = await cls.checker.has_native_identity(db, request.target_user_id, ch)
                if has_config and has_identity:
                    available.append(ch)

        logger.debug(f"[Priority] Resolved channel order for policy={policy}: {available}")
        return available

    @classmethod
    async def dispatch(
        cls,
        request: AgentInteractionRequest,
    ) -> InteractionDispatchResult:
        """执行优先级分发。
        
        广播模式（broadcast）：向所有可用渠道同时发送。
        普通模式：按优先级顺序尝试，首个成功则停止（fallback_all=False 时）
                  或继续尝试其余渠道（fallback_all=True，适合重要通知）。
        """
        delivered_via: list[str] = []
        failed_channels: list[str] = []
        channel_results: list[ChannelDeliveryResult] = []

        async with async_session() as db:
            # 1. 加载目标用户
            user = await db.get(User, request.target_user_id)
            if not user:
                return InteractionDispatchResult(
                    request=request,
                    delivered_via=[],
                    failed_channels=[],
                    channel_results=[],
                    final_status="failed",
                    summary=f"❌ 目标用户 {request.target_user_id} 不存在",
                )

            # 2. 解析渠道顺序
            channel_order = await cls._resolve_channel_order(db, request, user)

            if not channel_order:
                return InteractionDispatchResult(
                    request=request,
                    delivered_via=[],
                    failed_channels=[],
                    channel_results=[],
                    final_status="failed",
                    summary="❌ 无可用联系渠道（用户无关联渠道身份）",
                )

        is_broadcast = request.priority_policy in BROADCAST_POLICIES

        # 3. 按渠道顺序尝试分发
        for channel in channel_order:
            logger.info(
                f"[Priority] Dispatching to user={request.target_user_id} via channel={channel} "
                f"(policy={request.priority_policy})"
            )

            # 调用对应渠道适配器
            if channel == "web":
                result = await cls.adapter.send_web(
                    request.agent_id, request.target_user_id, request
                )
            elif channel == "email":
                result = await cls.adapter.send_email(
                    request.agent_id, request.target_user_id, request
                )
            else:
                result = await cls.adapter.send_native_channel(
                    request.agent_id, request.target_user_id, channel, request
                )

            channel_results.append(result)

            if result.success:
                delivered_via.append(channel)
                logger.info(f"[Priority] ✅ Delivered via {channel}")

                # 非广播模式：首个成功后，如无需 fallback_all 则停止
                if not is_broadcast and not request.fallback_all:
                    break
            else:
                failed_channels.append(channel)
                logger.warning(f"[Priority] ❌ Failed via {channel}: {result.message}")

        # 4. 汇总结果
        if delivered_via:
            status = "delivered" if len(delivered_via) == len(channel_order) else "partially_delivered"
        else:
            status = "failed"

        summary_parts = []
        if delivered_via:
            summary_parts.append(f"✅ 已送达渠道：{', '.join(delivered_via)}")
        if failed_channels:
            summary_parts.append(f"⚠️ 失败渠道：{', '.join(failed_channels)}")
        summary = "；".join(summary_parts) if summary_parts else "❌ 所有渠道均发送失败"

        return InteractionDispatchResult(
            request=request,
            delivered_via=delivered_via,
            failed_channels=failed_channels,
            channel_results=channel_results,
            final_status=status,
            summary=summary,
        )

    @classmethod
    async def dispatch_from_tool_args(
        cls,
        agent_id: uuid.UUID,
        args: dict,
    ) -> str:
        """从 Agent Tool Call 参数中解析并执行优先级分发。
        
        供 execute_tool 中的 'send_priority_message' case 直接调用。
        返回人类可读的结果字符串。
        """
        username = (args.get("username") or "").strip()
        message = (args.get("message") or "").strip()
        policy = args.get("priority_policy", "web_first")
        interaction_type = args.get("interaction_type", "notify")
        subject = args.get("subject")

        if not username or not message:
            return "❌ 请提供 username 和 message 参数"

        # 解析目标用户
        from app.models.user import User as UserModel
        from sqlalchemy import or_

        async with async_session() as db:
            agent_res = await db.execute(
                __import__("sqlalchemy", fromlist=["select"]).select(
                    __import__("app.models.agent", fromlist=["Agent"]).Agent
                ).where(
                    __import__("app.models.agent", fromlist=["Agent"]).Agent.id == agent_id
                )
            )
            agent = agent_res.scalar_one_or_none()
            if not agent:
                return "❌ Agent 不存在"

            q = __import__("sqlalchemy", fromlist=["select"]).select(UserModel).where(
                or_(
                    UserModel.username == username,
                    UserModel.display_name == username,
                )
            )
            if agent.tenant_id:
                q = q.where(UserModel.tenant_id == agent.tenant_id)

            u_res = await db.execute(q)
            target_user = u_res.scalar_one_or_none()

        if not target_user:
            return f"❌ 未找到用户 '{username}'"

        request = AgentInteractionRequest(
            agent_id=agent_id,
            target_user_id=target_user.id,
            message=message,
            priority_policy=policy,
            interaction_type=interaction_type,
            subject=subject,
        )
        result = await cls.dispatch(request)
        return result.summary


# 模块级单例
interaction_priority_manager = InteractionPriorityManager()
```

---

## 五、Agent 工具定义：`send_priority_message`

在 `backend/app/services/agent_tools.py` 的工具列表（`get_tools()` 函数）中，在 `send_web_message` 工具定义之后插入以下工具定义：

```python
{
    "type": "function",
    "function": {
        "name": "send_priority_message",
        "description": (
            "向指定用户发送消息，系统自动根据优先级策略选择最佳渠道投递。"
            "相比 send_web_message 和 send_channel_message，此工具会：\n"
            "1. 优先遵守用户设置的 preferred_contact_channel（联系偏好）\n"
            "2. 按 priority_policy 顺序尝试多个渠道，首个成功即停止\n"
            "3. 自动降级：Web 不在线则尝试飞书/钉钉/企微\n"
            "建议：OKR提醒/审批请求使用 native_first；日常Web通知使用 web_first（默认）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "目标用户的 display_name 或 username，需为本租户内已注册用户",
                },
                "message": {
                    "type": "string",
                    "description": "消息正文",
                },
                "priority_policy": {
                    "type": "string",
                    "enum": ["web_first", "native_first", "feishu_only", "web_only", "broadcast"],
                    "description": (
                        "渠道优先级策略：\n"
                        "- web_first（默认）：先推 Web，不在线则降级到 IM 渠道\n"
                        "- native_first：先推飞书/钉钉/企微，再推 Web（高到达率场景）\n"
                        "- feishu_only：仅飞书\n"
                        "- web_only：仅 Web\n"
                        "- broadcast：同时发送所有可用渠道"
                    ),
                    "default": "web_first",
                },
                "interaction_type": {
                    "type": "string",
                    "enum": ["notify", "alert", "task_delegate", "approval_request"],
                    "description": (
                        "交互类型（影响消息标题和通知分类）：\n"
                        "- notify：常规通知\n"
                        "- alert：紧急提醒\n"
                        "- task_delegate：任务委派\n"
                        "- approval_request：审批请求"
                    ),
                    "default": "notify",
                },
                "subject": {
                    "type": "string",
                    "description": "消息标题（可选，用于邮件主题或通知标题）",
                },
            },
            "required": ["username", "message"],
        },
    },
},
```

### execute_tool 集成点

在 `execute_tool()` 函数的 `elif` 分支中添加：

```python
elif tool_name == "send_priority_message":
    from app.services.interaction_priority import InteractionPriorityManager
    result = await InteractionPriorityManager.dispatch_from_tool_args(agent_id, arguments)
```

---

## 六、用户偏好渠道 API

### 6.1 更新用户偏好（PUT /users/me）

在 `backend/app/api/users.py` 的用户更新 schema 中增加字段：

```python
# backend/app/schemas/schemas.py — UserUpdate 类中新增：
preferred_contact_channel: str | None = None
# 合法值：'web' | 'feishu' | 'dingtalk' | 'wecom' | 'slack' | 'discord' | 'email' | null

# 可选验证器：
@validator("preferred_contact_channel")
def validate_channel(cls, v):
    valid = {"web", "feishu", "dingtalk", "wecom", "slack", "discord", "email", None}
    if v not in valid:
        raise ValueError(f"preferred_contact_channel must be one of {valid}")
    return v
```

### 6.2 前端设置入口

在 Agent 详情页的「用户偏好设置」面板（或用户个人设置页）中，增加：

```
联系渠道偏好
[下拉选择框]
  • 自动（由 Agent 策略决定）← 默认
  • 优先 Web（Clawith 平台）
  • 优先飞书
  • 优先钉钉
  • 优先企业微信
  • 仅邮件
```

字段名：`preferred_contact_channel`，值映射：
- 自动 → `null`
- 优先 Web → `"web"`  
- 优先飞书 → `"feishu"`
- 优先钉钉 → `"dingtalk"`
- 优先企业微信 → `"wecom"`
- 仅邮件 → `"email"`

---

## 七、OKR Agent 集成示例

以下为 OKR Agent 主动推送 Q2 进度提醒时，调用 `send_priority_message` 的典型 Prompt 设计：

```
你是 Clawith OKR 管理 Agent。

当需要向用户推送 OKR 进度提醒时，请使用 send_priority_message 工具，参数建议：
- priority_policy: "native_first"  ← OKR 提醒时效性要求高，优先 IM 渠道确保送达
- interaction_type: "notify"
- subject: "Q2 OKR 进度提醒"

示例调用：
send_priority_message(
    username="张三",
    message="您的 Q2 OKR「提升客户满意度至 90%」当前进度为 72%，距目标还差 18%。建议本周重点跟进客服响应速度专项。",
    priority_policy="native_first",
    interaction_type="notify",
    subject="Q2 OKR 进度提醒"
)
```

---

## 八、多账户用户的渠道优先级处理

对于通过飞书单点登录（SSO）进入平台的多账户用户（同一自然人在系统中有多个 `User` 记录，分属不同 `Tenant`），优先级决策规则：

```
多账户渠道解析优先级
─────────────────────────────────────────────────────────
1. 当前操作 Tenant 下的 User.preferred_contact_channel（若已设置）
2. OrgMember 所属渠道（优先当前 Tenant 的 OrgMember）
3. 跨 Tenant 匹配（通过 Identity.email / Identity.phone 关联）
4. 兜底：Web 渠道（始终可用）
```

**实现要点**：`_resolve_channel_order` 已通过 `OrgMember.user_id == user_id` 约束在当前 `User`（即特定 Tenant 下的账户）范围内查找，天然隔离多租户场景，无需额外处理。

---

## 九、架构文档补充说明（ARCHITECTURE_SPEC_EN.md 集成内容）

以下内容准备集成至 `ARCHITECTURE_SPEC_EN.md` 的 **Module 7（Omni-Channel Integration）** 章节末尾：

---

### 7.2 Agent Interaction Priority Management

The **Interaction Priority Manager** (`backend/app/services/interaction_priority.py`) is the unified dispatcher for all Agent-initiated outbound messages. It supersedes the need for Agents to manually select between `send_web_message`, `send_channel_message`, or `send_feishu_message`.

**Key Design Decisions:**

1. **`preferred_contact_channel` — User Sovereignty**: Each `User` record stores an optional `preferred_contact_channel` field (e.g., `"feishu"`, `"web"`). When set, the Manager **always** honors the user's preference, regardless of the Agent's policy. This field was standardized as the canonical naming convention for cross-platform contact routing.

2. **Priority Policy — Agent-Level Strategy**: When a user has no preference set, the dispatching order is governed by the Agent's `priority_policy`:
   - `web_first` (default): Routes to Web first (real-time WebSocket); falls back to Native channels if the user is offline.
   - `native_first`: Routes to Feishu/DingTalk/WeCom first (for high-delivery-rate scenarios like OKR reminders and approval requests).
   - `broadcast`: Sends to all configured channels simultaneously.

3. **Graceful Fallback Chain**: On channel failure (no config, no identity, or send error), the Manager automatically tries the next channel in priority order. The `fallback_all=True` flag enables sending to multiple channels for critical interactions.

4. **Tool Integration**: The `send_priority_message` Agent tool is the single recommended entry point for proactive Agent-to-Human communication. The underlying channel selection logic is fully opaque to the LLM — the Agent simply declares intent, the Manager handles routing.

**Data Flow:**
```
Agent Tool Call: send_priority_message(username, message, priority_policy)
    ↓
InteractionPriorityManager.dispatch_from_tool_args()
    ↓
_resolve_channel_order()  ← checks User.preferred_contact_channel first
    ↓                     ← then applies priority_policy filter
    ↓                     ← then filters by ChannelConfig + OrgMember availability
channel_order = ["feishu", "web", ...]
    ↓
For each channel (in order):
  ├── ChannelDispatchAdapter.send_web()          → _send_web_message()
  ├── ChannelDispatchAdapter.send_native_channel() → _send_channel_message()
  └── ChannelDispatchAdapter.send_email()         → email_service.send_plain()
    ↓
InteractionDispatchResult (delivered_via, failed_channels, summary)
```

---

## 十、实现清单（Checklist）

### 后端

- [ ] **新建文件**：`backend/app/services/interaction_priority.py`（本文档第四节代码）
- [ ] **数据库迁移**：创建 Alembic migration，为 `users` 表添加 `preferred_contact_channel` 字段（第三节）
- [ ] **模型变更**：`backend/app/models/user.py` 中新增 `preferred_contact_channel` 字段（第三节）
- [ ] **工具注册**：`backend/app/services/agent_tools.py` 中添加 `send_priority_message` 工具定义（第五节）
- [ ] **工具路由**：`execute_tool()` 中添加 `send_priority_message` 的 elif 分支（第五节）
- [ ] **Schema 更新**：`backend/app/schemas/schemas.py` 的 `UserUpdate` 中添加 `preferred_contact_channel` 字段（第六节）
- [ ] **API 处理**：`backend/app/api/users.py` 中将 `preferred_contact_channel` 写入 `User` 记录

### 前端

- [ ] **用户设置 UI**：个人设置页添加「联系渠道偏好」下拉选择器
- [ ] **API 绑定**：调用 `PUT /users/me` 更新 `preferred_contact_channel`

### 文档

- [ ] **架构文档更新**：将第九节内容添加至 `ARCHITECTURE_SPEC_EN.md` Module 7 末尾

---

## 十一、测试场景

### 场景 1：Web 用户在线
```
用户 A（preferred_contact_channel=null），在线中
Agent 调用：send_priority_message(username="A", message="...", priority_policy="web_first")

预期：channel_order = ["web", "feishu", ...] → 发送 web → ✅ 成功 → 停止
结果：delivered_via = ["web"]
```

### 场景 2：Web 用户不在线，有飞书账号
```
用户 B（preferred_contact_channel=null），不在线，有飞书 OrgMember
Agent 调用：send_priority_message(username="B", priority_policy="web_first")

预期：channel_order = ["web", "feishu"] 
→ web 发送成功（消息存入 session 待用户上线查看）→ ✅ 停止
注意：web 渠道即使用户不在线也视为「发送成功」（消息持久化到 ChatSession）
结果：delivered_via = ["web"]
```

### 场景 3：用户有明确偏好
```
用户 C（preferred_contact_channel="feishu"）
Agent 调用：send_priority_message(username="C", priority_policy="web_first")

预期：忽略 web_first policy，强制使用 feishu
结果：delivered_via = ["feishu"]（或失败）
```

### 场景 4：OKR 紧急提醒（native_first）
```
用户 D（无偏好），有飞书和钉钉账号
Agent 调用：send_priority_message(username="D", priority_policy="native_first", interaction_type="alert")

预期：channel_order = ["feishu", "dingtalk", "web"] → 飞书成功 → 停止
结果：delivered_via = ["feishu"]
```

### 场景 5：广播模式
```
用户 E（无偏好），有飞书 + Web
Agent 调用：send_priority_message(username="E", priority_policy="broadcast")

预期：向 web 和 feishu 同时发送
结果：delivered_via = ["web", "feishu"]
```

---

*文档版本：v1.0 | 最后更新：2026-04-19*
