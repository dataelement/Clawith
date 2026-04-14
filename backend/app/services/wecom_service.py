"""WeCom (Enterprise WeChat) service for sending messages via Open API."""

import httpx
from loguru import logger


async def get_wecom_access_token(corp_id: str, secret: str) -> dict:
    """Get WeCom access_token using corp_id and secret.

    API: https://developer.work.weixin.qq.com/document/14403
    """
    url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    params = {
        "corpid": corp_id,
        "corpsecret": secret,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        data = resp.json()

        if data.get("errcode") == 0:
            return {
                "access_token": data.get("access_token"),
                "expires_in": data.get("expires_in"),
            }
        else:
            logger.error(f"[WeCom] Failed to get access_token: {data}")
            return {"errcode": data.get("errcode"), "errmsg": data.get("errmsg")}


async def send_wecom_message(
    corp_id: str,
    secret: str,
    user_id: str,
    message: str,
    agent_id: str = None,
) -> dict:
    """Send a text message to a WeCom user.

    API: https://developer.work.weixin.qq.com/document/14404

    Args:
        corp_id: WeCom corp ID
        secret: WeCom app secret
        user_id: Recipient's user_id
        message: Message content
        agent_id: Optional agent ID (if not specified, uses first available)

    Returns:
        Dict with errcode on success
    """
    # 1. Get access token
    token_result = await get_wecom_access_token(corp_id, secret)
    access_token = token_result.get("access_token")

    if not access_token:
        return {"errcode": token_result.get("errcode", -1), "errmsg": "Failed to get access_token"}

    # 2. Send message via API
    url = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
    params = {"access_token": access_token}

    # If agent_id is not provided, we'll try to get it from the config
    # For now, require agent_id or fail
    if not agent_id:
        return {"errcode": -1, "errmsg": "agent_id is required for WeCom messages"}

    payload = {
        "touser": user_id,
        "msgtype": "text",
        "agentid": agent_id,
        "text": {
            "content": message,
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, params=params, json=payload)
        data = resp.json()

        if data.get("errcode") == 0:
            logger.info(f"[WeCom] Message sent to {user_id}")
            return data
        else:
            logger.error(f"[WeCom] Failed to send message: {data}")
            return data