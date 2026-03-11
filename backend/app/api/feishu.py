"""Feishu OAuth and Channel API routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_agent_access, is_agent_creator, is_agent_expired
from app.core.security import get_current_user
from app.database import get_db
from app.models.channel_config import ChannelConfig
from app.models.user import User
from app.schemas.schemas import ChannelConfigCreate, ChannelConfigOut, TokenResponse, UserOut
from app.services.feishu_service import feishu_service

router = APIRouter(tags=["feishu"])


# ─── OAuth ──────────────────────────────────────────────

@router.post("/auth/feishu/callback", response_model=TokenResponse)
async def feishu_oauth_callback(code: str, db: AsyncSession = Depends(get_db)):
    """Handle Feishu OAuth callback — exchange code for user session."""
    try:
        feishu_user = await feishu_service.exchange_code_for_user(code)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Feishu auth failed: {e}")

    user, token = await feishu_service.login_or_register(db, feishu_user)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/auth/feishu/bind")
async def bind_feishu_account(
    code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bind Feishu account to existing user."""
    user = await feishu_service.bind_feishu(db, current_user, code)
    return UserOut.model_validate(user)


# ─── Channel Config (per-agent Feishu bot) ──────────────

@router.post("/agents/{agent_id}/channel", response_model=ChannelConfigOut, status_code=status.HTTP_201_CREATED)
async def configure_channel(
    agent_id: uuid.UUID,
    data: ChannelConfigCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Configure Feishu bot credentials for a digital employee (wizard step 5)."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can configure channel")

    # Check existing
    result = await db.execute(select(ChannelConfig).where(ChannelConfig.agent_id == agent_id))
    existing = result.scalar_one_or_none()
    if existing:
        existing.app_id = data.app_id
        existing.app_secret = data.app_secret
        existing.encrypt_key = data.encrypt_key
        existing.verification_token = data.verification_token
        existing.is_configured = True
        await db.flush()
        return ChannelConfigOut.model_validate(existing)

    config = ChannelConfig(
        agent_id=agent_id,
        channel_type=data.channel_type,
        app_id=data.app_id,
        app_secret=data.app_secret,
        encrypt_key=data.encrypt_key,
        verification_token=data.verification_token,
        is_configured=True,
    )
    db.add(config)
    await db.flush()
    return ChannelConfigOut.model_validate(config)


@router.get("/agents/{agent_id}/channel", response_model=ChannelConfigOut)
async def get_channel_config(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get channel configuration for an agent."""
    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(select(ChannelConfig).where(ChannelConfig.agent_id == agent_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Channel not configured")
    return ChannelConfigOut.model_validate(config)


@router.get("/agents/{agent_id}/channel/webhook-url")
async def get_webhook_url(agent_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """Get the webhook URL for this agent's Feishu bot."""
    import os
    from app.models.system_settings import SystemSetting
    # Priority: system_settings > env var > request.base_url
    public_base = ""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "platform"))
    setting = result.scalar_one_or_none()
    if setting and setting.value.get("public_base_url"):
        public_base = setting.value["public_base_url"].rstrip("/")
    if not public_base:
        public_base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not public_base:
        public_base = str(request.base_url).rstrip("/")
    return {"webhook_url": f"{public_base}/api/channel/feishu/{agent_id}/webhook"}


@router.delete("/agents/{agent_id}/channel", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel_config(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove Feishu bot configuration for an agent."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can remove channel")
    result = await db.execute(select(ChannelConfig).where(ChannelConfig.agent_id == agent_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Channel not configured")
    await db.delete(config)



# ─── Feishu Event Webhook ───────────────────────────────

# Simple in-memory dedup to avoid processing retried events
_processed_events: set[str] = set()


@router.post("/channel/feishu/{agent_id}/webhook")
async def feishu_event_webhook(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Feishu event callback for a specific agent's bot."""
    body = await request.json()
    import json as _json
    print(f"[Feishu] Webhook received for {agent_id}: event_type={body.get('header', {}).get('event_type', 'N/A')}, body_keys={list(body.keys())}")

    # Handle verification challenge
    if "challenge" in body:
        return {"challenge": body["challenge"]}

    # Deduplicate — Feishu retries on slow responses
    event_id = body.get("header", {}).get("event_id", "")
    if event_id in _processed_events:
        return {"code": 0, "msg": "already processed"}
    if event_id:
        _processed_events.add(event_id)
        # Keep set bounded
        if len(_processed_events) > 1000:
            _processed_events.clear()

    # Get channel config
    result = await db.execute(select(ChannelConfig).where(ChannelConfig.agent_id == agent_id))
    config = result.scalar_one_or_none()
    if not config:
        return {"code": 1, "msg": "Channel not found"}

    # Handle events
    event = body.get("event", {})
    event_type = body.get("header", {}).get("event_type", "")

    if event_type == "im.message.receive_v1":
        message = event.get("message", {})
        sender = event.get("sender", {}).get("sender_id", {})
        sender_open_id = sender.get("open_id", "")
        msg_type = message.get("message_type", "text")
        chat_type = message.get("chat_type", "p2p")  # p2p or group
        chat_id = message.get("chat_id", "")

        print(f"[Feishu] Received {msg_type} message, chat_type={chat_type}, from={sender_open_id}")

        # ── Normalize post (rich text) → extract text + schedule image downloads ──
        if msg_type == "post":
            import json as _json_post
            _post_body = _json_post.loads(message.get("content", "{}"))
            # Feishu post content: {"title": "...", "content": [[{"tag":"text","text":"..."},...],...]}
            # The content may be nested under a locale key like "zh_cn"
            _paragraphs = _post_body.get("content", [])
            if not _paragraphs:
                # Try locale keys (zh_cn, en_us, etc.)
                for _locale_key, _locale_val in _post_body.items():
                    if isinstance(_locale_val, dict) and "content" in _locale_val:
                        _paragraphs = _locale_val["content"]
                        break
            _text_parts = []
            _post_image_keys = []
            for _para in _paragraphs:
                _line_parts = []
                for _elem in _para:
                    _tag = _elem.get("tag")
                    if _tag == "text":
                        _line_parts.append(_elem.get("text", ""))
                    elif _tag == "a":
                        _href = _elem.get("href", "")
                        _link_text = _elem.get("text", "")
                        _line_parts.append(f"{_link_text} ({_href})" if _href else _link_text)
                    elif _tag == "img":
                        _ik = _elem.get("image_key", "")
                        if _ik:
                            _post_image_keys.append(_ik)
                if _line_parts:
                    _text_parts.append("".join(_line_parts))
            _extracted_text = "\n".join(_text_parts).strip()
            # Download images and embed as base64 for vision-capable models
            _image_markers = []
            if _post_image_keys:
                import base64 as _b64
                _msg_id = message.get("message_id", "")
                from pathlib import Path as _PostPath
                from app.config import get_settings as _post_gs
                _post_settings = _post_gs()
                _upload_dir = _PostPath(_post_settings.AGENT_DATA_DIR) / str(agent_id) / "workspace" / "uploads"
                _upload_dir.mkdir(parents=True, exist_ok=True)
                for _ik in _post_image_keys:
                    try:
                        _img_bytes = await feishu_service.download_message_resource(
                            config.app_id, config.app_secret, _msg_id, _ik, "image"
                        )
                        # Save to workspace
                        _save_path = _upload_dir / f"image_{_ik[-8:]}.jpg"
                        _save_path.write_bytes(_img_bytes)
                        print(f"[Feishu] Saved post image to {_save_path} ({len(_img_bytes)} bytes)")
                        # Embed as base64 marker for vision models
                        _b64_data = _b64.b64encode(_img_bytes).decode("ascii")
                        _image_markers.append(f"[image_data:data:image/jpeg;base64,{_b64_data}]")
                    except Exception as _dl_err:
                        print(f"[Feishu] Failed to download post image {_ik}: {_dl_err}")
            # Build final text with embedded images
            if not _extracted_text and _image_markers:
                _extracted_text = "[用户发送了图片，请看图片内容]"
            _final_content = _extracted_text
            if _image_markers:
                _final_content += "\n" + "\n".join(_image_markers)
            # Rewrite as text message so existing handler processes it
            message["content"] = _json_post.dumps({"text": _final_content})
            msg_type = "text"
            print(f"[Feishu] Normalized post → text='{_extracted_text[:100]}', images={len(_image_markers)}")

        if msg_type in ("file", "image"):
            import asyncio as _asyncio
            _asyncio.create_task(_handle_feishu_file(db, agent_id, config, message, sender_open_id, chat_type, chat_id))
            return {"code": 0, "msg": "ok"}

        if msg_type == "text":
            import json
            import re
            content = json.loads(message.get("content", "{}"))
            user_text = content.get("text", "")

            # Strip @mention tags (e.g. @_user_1) from group messages
            user_text = re.sub(r'@_user_\d+', '', user_text).strip()

            if not user_text:
                return {"code": 0, "msg": "empty message after stripping mentions"}

            print(f"[Feishu] User text: {user_text[:100]}")

            # Detect task creation intent
            task_match = re.search(
                r'(?:创建|新建|添加|建一个|帮我建)(?:一个)?(?:任务|待办|todo)[，,：:\s]*(.+)',
                user_text, re.IGNORECASE
            )

            # Determine conversation_id for history isolation
            # Group chats: use chat_id; P2P chats: use sender_open_id
            if chat_type == "group" and chat_id:
                conv_id = f"feishu_group_{chat_id}"
            else:
                conv_id = f"feishu_p2p_{sender_open_id}"

            # Load recent conversation history via session (session UUID may already exist)
            from app.models.audit import ChatMessage
            from app.models.agent import Agent as AgentModel
            from app.services.channel_session import find_or_create_channel_session
            agent_r = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
            agent_obj = agent_r.scalar_one_or_none()
            creator_id = agent_obj.creator_id if agent_obj else agent_id
            ctx_size = agent_obj.context_window_size if agent_obj else 100

            # Pre-resolve session so history lookup uses the UUID  (session created later if new)
            _pre_sess_r = await db.execute(
                select(__import__('app.models.chat_session', fromlist=['ChatSession']).ChatSession).where(
                    __import__('app.models.chat_session', fromlist=['ChatSession']).ChatSession.agent_id == agent_id,
                    __import__('app.models.chat_session', fromlist=['ChatSession']).ChatSession.external_conv_id == conv_id,
                )
            )
            _pre_sess = _pre_sess_r.scalar_one_or_none()
            _history_conv_id = str(_pre_sess.id) if _pre_sess else conv_id
            history_result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id == _history_conv_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(ctx_size)
            )
            history_msgs = history_result.scalars().all()
            history = [{"role": m.role, "content": m.content} for m in reversed(history_msgs)]

            # --- Resolve Feishu sender identity & find/create platform user ---
            import uuid as _uuid
            import httpx as _httpx

            sender_name = ""
            sender_user_id_feishu = ""  # tenant-level user_id (consistent across apps)
            platform_user_id = creator_id  # fallback

            try:
                async with _httpx.AsyncClient() as _client:
                    _tok_resp = await _client.post(
                        "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
                        json={"app_id": config.app_id, "app_secret": config.app_secret},
                    )
                    _app_token = _tok_resp.json().get("app_access_token", "")
                    if _app_token:
                        _user_resp = await _client.get(
                            f"https://open.feishu.cn/open-apis/contact/v3/users/{sender_open_id}",
                            params={"user_id_type": "open_id"},
                            headers={"Authorization": f"Bearer {_app_token}"},
                        )
                        _user_data = _user_resp.json()
                        print(f"[Feishu] Sender resolve: code={_user_data.get('code')}, msg={_user_data.get('msg', '')}")
                        if _user_data.get("code") == 0:
                            _user_info = _user_data.get("data", {}).get("user", {})
                            sender_name = _user_info.get("name", "")
                            sender_user_id_feishu = _user_info.get("user_id", "")
                            print(f"[Feishu] Resolved sender: {sender_name} (user_id={sender_user_id_feishu})")
            except Exception as e:
                print(f"[Feishu] Failed to resolve sender: {e}")

            # Look up platform user by feishu_user_id or feishu_open_id
            if sender_user_id_feishu:
                u_result = await db.execute(
                    select(User).where(User.feishu_user_id == sender_user_id_feishu)
                )
                found_user = u_result.scalar_one_or_none()
                if found_user:
                    platform_user_id = found_user.id
                    print(f"[Feishu] Matched user by feishu_user_id: {found_user.username}")

            if platform_user_id == creator_id and sender_open_id:
                # Try by feishu_open_id (if same app ID was used for SSO)
                u_result2 = await db.execute(
                    select(User).where(User.feishu_open_id == sender_open_id)
                )
                found_user2 = u_result2.scalar_one_or_none()
                if found_user2:
                    platform_user_id = found_user2.id
                    print(f"[Feishu] Matched user by feishu_open_id: {found_user2.username}")

            # Auto-create user if not found and we have sender info
            if platform_user_id == creator_id and sender_name:
                from app.core.security import hash_password
                new_username = f"feishu_{sender_user_id_feishu or sender_open_id[:16]}"
                new_user = User(
                    username=new_username,
                    email=f"{new_username}@feishu.local",
                    password_hash=hash_password(_uuid.uuid4().hex),  # random password
                    display_name=sender_name,
                    role="member",
                    feishu_open_id=sender_open_id,
                    feishu_user_id=sender_user_id_feishu or None,
                    tenant_id=agent_obj.tenant_id if agent_obj else None,
                )
                db.add(new_user)
                await db.flush()
                platform_user_id = new_user.id
                print(f"[Feishu] Auto-created user: {sender_name} -> {new_username}")

            # ── Find-or-create a ChatSession via external_conv_id (DB-based, no cache needed) ──
            from datetime import datetime as _dt, timezone as _tz
            _sess = await find_or_create_channel_session(
                db=db,
                agent_id=agent_id,
                user_id=platform_user_id,
                external_conv_id=conv_id,
                source_channel="feishu",
                first_message_title=user_text,
            )
            session_conv_id = str(_sess.id)

            # Save user message
            db.add(ChatMessage(agent_id=agent_id, user_id=platform_user_id, role="user", content=user_text, conversation_id=session_conv_id))
            _sess.last_message_at = _dt.now(_tz.utc)
            await db.commit()


            # Prepend sender identity so the agent knows who is talking
            llm_user_text = user_text
            if sender_name:
                id_part = f" (ID: {sender_user_id_feishu})" if sender_user_id_feishu else ""
                llm_user_text = f"[发送者: {sender_name}{id_part}] {user_text}"

            # Set channel_file_sender contextvar so the agent can send files back via Feishu
            from app.services.agent_tools import channel_file_sender as _cfs
            _reply_to_id = chat_id if chat_type == "group" else sender_open_id
            _rid_type = "chat_id" if chat_type == "group" else "open_id"
            async def _feishu_file_sender(file_path, msg: str = ""):
                try:
                    await feishu_service.upload_and_send_file(
                        config.app_id, config.app_secret,
                        _reply_to_id, file_path,
                        receive_id_type=_rid_type,
                        accompany_msg=msg,
                    )
                except Exception as _upload_err:
                    # Fallback: send a download link when upload permission is not granted
                    from pathlib import Path as _P
                    from app.config import get_settings as _gs_fallback
                    _fs = _gs_fallback()
                    _base_url = getattr(_fs, 'BASE_URL', '').rstrip('/') or ''
                    _fp = _P(file_path)
                    _ws_root = _P(_fs.AGENT_DATA_DIR)
                    try:
                        _rel = str(_fp.relative_to(_ws_root / str(agent_id)))
                    except ValueError:
                        _rel = _fp.name
                    _fallback_parts = []
                    if msg:
                        _fallback_parts.append(msg)
                    if _base_url:
                        _dl_url = f"{_base_url}/api/agents/{agent_id}/files/download?path={_rel}"
                        _fallback_parts.append(f"📎 {_fp.name}\n🔗 {_dl_url}")
                    _fallback_parts.append(
                        f"⚠️ 文件直接发送失败（{_upload_err}）\n"
                        "如需 Agent 直接发飞书文件，请在飞书开放平台为应用开启 "
                        "`im:resource`（即 `im:resource:upload`）权限并发布版本。"
                    )
                    await feishu_service.send_message(
                        config.app_id, config.app_secret,
                        _reply_to_id, "text",
                        json.dumps({"text": "\n\n".join(_fallback_parts)}),
                        receive_id_type=_rid_type,
                    )
            _cfs_token = _cfs.set(_feishu_file_sender)

            # Call LLM with history
            reply_text = await _call_agent_llm(db, agent_id, llm_user_text, history=history, user_id=platform_user_id)
            _cfs.reset(_cfs_token)
            print(f"[Feishu] LLM reply: {reply_text[:100]}")

            # Log activity
            from app.services.activity_logger import log_activity
            await log_activity(agent_id, "chat_reply", f"回复了飞书消息: {reply_text[:80]}", detail={"channel": "feishu", "user_text": user_text[:200], "reply": reply_text[:500]})

            # If task creation detected, create a real Task record
            if task_match:
                task_title = task_match.group(1).strip()
                if task_title:
                    try:
                        from app.models.task import Task as TaskModel
                        from app.models.agent import Agent as AgentModel
                        from app.services.task_executor import execute_task
                        import asyncio as _asyncio

                        # Find the agent's creator to use as task creator
                        agent_r = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
                        agent_obj = agent_r.scalar_one_or_none()
                        creator_id = agent_obj.creator_id if agent_obj else agent_id

                        task_obj = TaskModel(
                            agent_id=agent_id,
                            title=task_title,
                            created_by=creator_id,
                            status="pending",
                            priority="medium",
                        )
                        db.add(task_obj)
                        await db.commit()
                        await db.refresh(task_obj)
                        _asyncio.create_task(execute_task(task_obj.id, agent_id))
                        reply_text += f"\n\n📋 已同步创建任务到任务面板：【{task_title}】"
                        print(f"[Feishu] Created task: {task_title}")
                    except Exception as e:
                        print(f"[Feishu] Failed to create task: {e}")

            # Save assistant reply to history (use platform_user_id so messages stay in one session)
            db.add(ChatMessage(agent_id=agent_id, user_id=platform_user_id, role="assistant", content=reply_text, conversation_id=session_conv_id))
            _sess.last_message_at = _dt.now(_tz.utc)
            await db.commit()
            # Send reply via Feishu
            try:
                if chat_type == "group" and chat_id:
                    await feishu_service.send_message(
                        config.app_id, config.app_secret,
                        chat_id, "text",
                        json.dumps({"text": reply_text}),
                        receive_id_type="chat_id",
                    )
                else:
                    await feishu_service.send_message(
                        config.app_id, config.app_secret,
                        sender_open_id, "text",
                        json.dumps({"text": reply_text}),
                    )
            except Exception as e:
                print(f"[Feishu] Failed to send message: {e}")

    return {"code": 0, "msg": "ok"}


IMPORT_RE = None  # lazy sentinel
_FILE_ACK_MESSAGES = [
    "收到你的文件，请问有什么需要帮忙的？",
    "文件收到了！你想让我怎么处理它？",
    "好的，我已经收到这份文件，请告诉我你的需求~",
    "已收到文件，随时准备好为你处理！",
    "收到！请问希望我对这份文件做什么？",
]


async def _handle_feishu_file(db, agent_id, config, message, sender_open_id, chat_type, chat_id):
    """Handle incoming file or image messages from Feishu (runs as a background task)."""
    import asyncio, random, json
    from pathlib import Path
    from app.config import get_settings
    from app.models.audit import ChatMessage
    from app.models.agent import Agent as AgentModel
    from app.models.user import User as UserModel
    from app.services.channel_session import find_or_create_channel_session
    from app.core.security import hash_password
    from app.database import async_session as _async_session
    from datetime import datetime as _dt, timezone as _tz
    import uuid as _uuid
    from sqlalchemy import select as _select

    msg_type = message.get("message_type", "file")
    message_id = message.get("message_id", "")
    content = json.loads(message.get("content", "{}"))

    # Extract file key and name
    if msg_type == "image":
        file_key = content.get("image_key", "")
        filename = f"image_{file_key[-8:]}.jpg" if file_key else "image.jpg"
        res_type = "image"
    else:
        file_key = content.get("file_key", "")
        filename = content.get("file_name") or f"file_{file_key[-8:]}.bin"
        res_type = "file"

    if not file_key:
        print(f"[Feishu] No file_key in {msg_type} message")
        return

    # Resolve workspace upload dir
    settings = get_settings()
    upload_dir = Path(settings.AGENT_DATA_DIR) / str(agent_id) / "workspace" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    save_path = upload_dir / filename

    # Download the file
    try:
        file_bytes = await feishu_service.download_message_resource(
            config.app_id, config.app_secret, message_id, file_key, res_type
        )
        save_path.write_bytes(file_bytes)
        print(f"[Feishu] Saved {msg_type} to {save_path} ({len(file_bytes)} bytes)")
    except Exception as e:
        print(f"[Feishu] Failed to download {msg_type}: {e}")
        err_tip = "抱歉，文件下载失败。可能原因：机器人缺少 `im:resource` 权限（文件读取）。\n请在飞书开放平台 → 权限管理 → 批量导入权限 JSON → 重新发布机器人版本后重试。"
        try:
            import json as _j
            if chat_type == "group" and chat_id:
                await feishu_service.send_message(config.app_id, config.app_secret, chat_id, "text", _j.dumps({"text": err_tip}), receive_id_type="chat_id")
            else:
                await feishu_service.send_message(config.app_id, config.app_secret, sender_open_id, "text", _j.dumps({"text": err_tip}))
        except Exception as e2:
            print(f"[Feishu] Also failed to send error tip: {e2}")
        return

    # Resolve platform user and session using a fresh db session
    async with _async_session() as db:
        agent_r = await db.execute(_select(AgentModel).where(AgentModel.id == agent_id))
        agent_obj = agent_r.scalar_one_or_none()

        _un = f"feishu_{sender_open_id[:16]}"
        _ur = await db.execute(_select(UserModel).where(UserModel.username == _un))
        _pu = _ur.scalar_one_or_none()
        if not _pu:
            _pu = UserModel(
                username=_un, email=f"{_un}@feishu.local",
                password_hash=hash_password(_uuid.uuid4().hex),
                display_name=f"Feishu {sender_open_id[:8]}",
                role="member", feishu_open_id=sender_open_id,
                tenant_id=agent_obj.tenant_id if agent_obj else None,
            )
            db.add(_pu)
            await db.flush()
        platform_user_id = _pu.id

        # Conv ID
        if chat_type == "group" and chat_id:
            conv_id = f"feishu_group_{chat_id}"
        else:
            conv_id = f"feishu_p2p_{sender_open_id}"

        # Find-or-create session
        _sess = await find_or_create_channel_session(
            db=db, agent_id=agent_id, user_id=platform_user_id,
            external_conv_id=conv_id, source_channel="feishu",
            first_message_title=f"[文件] {filename}",
        )
        session_conv_id = str(_sess.id)

        # Store user message as file path
        user_msg_content = f"[file:{filename}]"
        db.add(ChatMessage(agent_id=agent_id, user_id=platform_user_id, role="user",
                           content=user_msg_content, conversation_id=session_conv_id))
        _sess.last_message_at = _dt.now(_tz.utc)
        await db.commit()

    # Wait 1-2s for "human feel" (outside db session)
    await asyncio.sleep(random.uniform(1.0, 2.0))

    # Send random ack via Feishu first
    ack = random.choice(_FILE_ACK_MESSAGES)
    try:
        if chat_type == "group" and chat_id:
            await feishu_service.send_message(
                config.app_id, config.app_secret, chat_id, "text",
                json.dumps({"text": ack}), receive_id_type="chat_id",
            )
        else:
            await feishu_service.send_message(
                config.app_id, config.app_secret, sender_open_id, "text",
                json.dumps({"text": ack}),
            )
    except Exception as e:
        print(f"[Feishu] Failed to send ack: {e}")

    # Store ack in DB
    async with _async_session() as db2:
        db2.add(ChatMessage(agent_id=agent_id, user_id=platform_user_id, role="assistant",
                            content=ack, conversation_id=session_conv_id))
        await db2.commit()



async def _download_post_images(agent_id, config, message_id, image_keys):
    """Download images embedded in a Feishu post message to the agent's workspace."""
    from pathlib import Path
    from app.config import get_settings
    settings = get_settings()
    upload_dir = Path(settings.AGENT_DATA_DIR) / str(agent_id) / "workspace" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    for ik in image_keys:
        try:
            file_bytes = await feishu_service.download_message_resource(
                config.app_id, config.app_secret, message_id, ik, "image"
            )
            save_path = upload_dir / f"image_{ik[-8:]}.jpg"
            save_path.write_bytes(file_bytes)
            print(f"[Feishu] Saved post image to {save_path} ({len(file_bytes)} bytes)")
        except Exception as e:
            print(f"[Feishu] Failed to download post image {ik}: {e}")


async def _call_agent_llm(db: AsyncSession, agent_id: uuid.UUID, user_text: str, history: list[dict] | None = None, user_id=None) -> str:
    """Call the agent's configured LLM model with conversation history.
    
    Reuses the same call_llm function as the WebSocket chat endpoint so that
    all providers (OpenRouter, Qwen, etc.) work identically on both channels.
    """
    from app.models.agent import Agent
    from app.models.llm import LLMModel
    from app.api.websocket import call_llm

    # Load agent and model
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        return "⚠️ 数字员工未找到"

    if is_agent_expired(agent):
        return "This Agent has expired and is off duty. Please contact your admin to extend its service."

    if not agent.primary_model_id:
        return f"⚠️ {agent.name} 未配置 LLM 模型，请在管理后台设置。"

    model_result = await db.execute(select(LLMModel).where(LLMModel.id == agent.primary_model_id))
    model = model_result.scalar_one_or_none()
    if not model:
        return "⚠️ 配置的模型不存在"

    # Build conversation messages (without system prompt — call_llm adds it)
    messages: list[dict] = []
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_text})

    # Use actual user_id so the system prompt knows who it's chatting with
    effective_user_id = user_id or agent_id

    try:
        reply = await call_llm(
            model,
            messages,
            agent.name,
            agent.role_description or "",
            agent_id=agent_id,
            user_id=effective_user_id,
            supports_vision=getattr(model, 'supports_vision', False),
        )
        return reply
    except Exception as e:
        import traceback
        traceback.print_exc()
        error_msg = str(e) or repr(e)
        print(f"[LLM] Error: {error_msg}")
        return f"⚠️ 调用模型出错: {error_msg[:150]}"


