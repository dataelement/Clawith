"""通用群聊激活逻辑(跨渠道复用)。

提供:
- resolve_effective_mode:解析生效模式与关键词(群级覆盖优先 → 通道级 → 默认 mention)
- extract_after_mention:从原始文本提取 @机器人 之后到下一个 @ 之前的内容
- prepare_llm_user_text:按模式决定给 LLM 的内容(mention 取@后 / keyword 命中整句 / always 整句)
- evaluate_group_activation:群聊激活门禁判断(是否放行)
"""

import re


def resolve_effective_mode(
    extra_config: dict | None,
    chat_id: str,
) -> tuple[str, list]:
    """解析生效的激活模式与关键词(群级覆盖优先 → 通道级 → 默认 mention)。

    返回 (mode, keywords)。
    """
    extra = extra_config or {}
    group_cfg = (extra.get("group_overrides") or {}).get(chat_id) or {}
    mode = group_cfg.get("activation_mode", extra.get("activation_mode", "mention"))
    keywords = group_cfg.get("keywords", extra.get("keywords", []))
    return mode, keywords or []


def extract_after_mention(raw_text: str, bot_tokens: list[str]) -> str | None:
    """从含 @ 的原始文本中,提取第一个机器人 @ 标记之后、到下一个 @ 之前的内容。

    例如 raw_text="@user_1 内容A @机器人 内容B @user_2 内容C",
        bot_tokens=["@机器人", "@_user_1"] → 返回 "内容B"。

    返回提取到的内容(可能为空串,表示纯@无内容);
    找不到任何 bot token 时返回 None,调用方退化为整句。
    """
    if not raw_text or not bot_tokens:
        return None
    for token in bot_tokens:
        if not token:
            continue
        at_token = token if token.startswith("@") else "@" + token
        idx = raw_text.find(at_token)
        if idx == -1:
            continue
        rest = raw_text[idx + len(at_token):]
        nxt = re.search(r"@[^\s@]+", rest)
        rest = rest[:nxt.start()] if nxt else rest
        return rest.strip()
    return None


def prepare_llm_user_text(
    *,
    mode: str,
    keywords: list,
    raw_text: str,
    full_text: str,
    bot_tokens: list[str],
) -> str:
    """按激活模式决定给 LLM 的内容。

    - mention:只取 @机器人 之后的召唤内容
    - keyword:关键词命中整句 → 整句;未命中但被@ → 取 @机器人 之后内容;都无 → 整句兜底
    - always:整句(silent 不会走到这里)
    """
    if mode in ("mention", "keyword"):
        extracted = extract_after_mention(raw_text, bot_tokens)
        if mode == "mention":
            return extracted if extracted is not None else full_text
        # keyword
        hit = bool(keywords) and any(k in full_text for k in keywords)
        if hit:
            return full_text
        return extracted if extracted is not None else full_text
    return full_text  # always


async def evaluate_group_activation(
    *,
    extra_config: dict | None,
    chat_type: str,
    chat_id: str,
    user_text: str,
    is_mentioned: bool,
) -> tuple[bool, str | None]:
    """通用群聊激活门禁判断。

    返回 (should_process, reason):
      - should_process=True  → 放行进 agent 管线
      - should_process=False → 静默,reason 是跳过原因(用于日志/响应 msg)
    """
    if chat_type != "group":
        return True, None  # 私聊始终处理

    mode, keywords = resolve_effective_mode(extra_config, chat_id)

    if mode == "silent":
        return False, "silent_mode"
    if mode == "mention":
        return (is_mentioned, None if is_mentioned else "not_mentioned")
    if mode == "keyword":
        if is_mentioned:
            return True, None  # 被@也算召唤
        if keywords and not any(k in user_text for k in keywords):
            return False, "keyword_not_matched"
        return True, None
    return True, None  # always
