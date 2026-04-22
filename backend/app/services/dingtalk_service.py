"""DingTalk service for sending messages via Open API."""

import json
import httpx
from loguru import logger


async def get_dingtalk_access_token(app_id: str, app_secret: str) -> dict:
    """Get DingTalk access_token using app_id and app_secret.

    API: https://open.dingtalk.com/document/orgapp/obtain-access_token
    """
    url = "https://oapi.dingtalk.com/gettoken"
    params = {
        "appkey": app_id,
        "appsecret": app_secret,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()

            if data.get("errcode") == 0:
                return {"access_token": data.get("access_token"), "expires_in": data.get("expires_in")}
            else:
                logger.error(f"[DingTalk] Failed to get access_token: {data}")
                return {"errcode": data.get("errcode"), "errmsg": data.get("errmsg")}
        except Exception as e:
            logger.error(f"[DingTalk] Network error getting access_token: {e}")
            return {"errcode": -1, "errmsg": str(e)}


async def get_dingtalk_user_detail(app_id: str, app_secret: str, userid: str) -> dict | None:
    """Fetch user detail from DingTalk corp API by userid (staff_id).

    Returns dict with mobile, email, org_email, unionid, name, etc.
    Returns None on failure.

    API: https://open.dingtalk.com/document/orgapp/query-user-details
    """
    token_result = await get_dingtalk_access_token(app_id, app_secret)
    access_token = token_result.get("access_token")
    if not access_token:
        return None

    url = "https://oapi.dingtalk.com/topapi/v2/user/get"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(
                url,
                params={"access_token": access_token},
                json={"userid": userid},
            )
            data = resp.json()
            if data.get("errcode") == 0:
                return data.get("result", {})
            else:
                logger.warning(f"[DingTalk] user/get failed for {userid}: {data.get('errmsg')}")
                return None
        except Exception as e:
            logger.warning(f"[DingTalk] user/get error for {userid}: {e}")
            return None


async def send_dingtalk_v1_robot_oto_message(
    app_id: str,
    app_secret: str,
    user_ids: list[str],
    message: str,
    msg_type: str = "text",
    robot_code: str = None,
) -> dict:
    """Send single chat messages via Robot using modern v1.0 API (RECOMMENDED).
    
    API: /v1.0/robot/oToMessages/batchSend
    Docs: https://open.dingtalk.com/document/orgapp/batch-send-single-chat-messages
    """
    token_result = await get_dingtalk_access_token(app_id, app_secret)
    access_token = token_result.get("access_token")
    if not access_token:
        return token_result

    url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
    headers = {
        "x-acs-dingtalk-access-token": access_token,
        "Content-Type": "application/json"
    }
    
    # Map text to standard templates
    if msg_type == "markdown":
        msg_key = "sampleMarkdown"
        msg_param = json.dumps({"title": "Notification", "text": message})
    else:
        msg_key = "sampleText"
        msg_param = json.dumps({"content": message})

    payload = {
        "robotCode": robot_code or app_id,
        "userIds": user_ids,
        "msgKey": msg_key,
        "msgParam": msg_param
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(url, headers=headers, json=payload)
            data = resp.json()
            if resp.status_code == 200:
                logger.info(f"[DingTalk] Robot v1.0 OTO batch message sent to {user_ids}")
                return {"errcode": 0, "processQueryKey": data.get("processQueryKey")}
            else:
                logger.error(f"[DingTalk] Failed to send v1.0 OTO message: {data}")
                return {"errcode": resp.status_code, "errmsg": str(data)}
        except Exception as e:
            logger.error(f"[DingTalk] Network error sending v1.0 OTO message: {e}")
            return {"errcode": -1, "errmsg": str(e)}


async def send_dingtalk_corp_conversation(
    app_id: str,
    app_secret: str,
    user_id: str,
    msg_body: dict,
    agent_id: str,
) -> dict:
    """Send a work notification (工作通知).
    
    API: https://open.dingtalk.com/document/orgapp/send-asynchronous-messages-to-users
    """
    token_result = await get_dingtalk_access_token(app_id, app_secret)
    access_token = token_result.get("access_token")
    if not access_token:
        return token_result

    url = "https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2"
    params = {"access_token": access_token}

    payload = {
        "agent_id": agent_id,
        "userid_list": user_id,
        "msg": msg_body,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(url, params=params, json=payload)
            data = resp.json()
            if data.get("errcode") == 0:
                return data
            else:
                logger.error(f"[DingTalk] Failed to send corp conversation: {data}")
                return data
        except Exception as e:
            logger.error(f"[DingTalk] Network error sending corp conversation: {e}")
            return {"errcode": -1, "errmsg": str(e)}


async def send_dingtalk_message(
    app_id: str,
    app_secret: str,
    user_id: str,
    message: str,
    agent_id: str = None,
    use_robot: bool = True,
    msg_type: str = "text",
) -> dict:
    """Unified message sending method.
    
    Default behavior is sending via Robot OTO (Private Message) using v1.0 API.
    """
    if use_robot:
        # Use v1.0 API for private chat
        return await send_dingtalk_v1_robot_oto_message(
            app_id=app_id,
            app_secret=app_secret,
            user_ids=[user_id],
            message=message,
            msg_type=msg_type
        )
    else:
        # Use Work Notification
        msg_body = {
            "msgtype": msg_type,
            msg_type: {"content": message} if msg_type == "text" else {"title": "Notification", "text": message}
        }
        if not agent_id:
            agent_id = app_id
        return await send_dingtalk_corp_conversation(app_id, app_secret, user_id, msg_body, agent_id)
