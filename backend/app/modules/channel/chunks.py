"""One delivery attempt may send ordered text fragments without replaying partial effects."""

import json
from collections.abc import Awaitable, Callable

from app.modules.channel.contracts import SendOutcome


async def send_chunks(text: str, *, max_characters: int, max_bytes: int,
        send: Callable[[str, int], Awaitable[SendOutcome]]) -> SendOutcome:
    if max_characters < 1 or max_bytes < 4:
        raise ValueError("Channel fragment bounds must fit a Unicode code point")
    try:
        if not text or len(text.encode()) > 262144:
            return SendOutcome("failed", error="channel_text_exceeds_delivery_bound")
    except UnicodeError:
        return SendOutcome("failed", error="channel_text_is_invalid")
    start, index = 0, 0
    first: str | None = None
    last: str | None = None
    reply_ids: list[str] = []
    while start < len(text):
        if index >= 256:
            return SendOutcome("uncertain", last, "channel_fragment_count_exceeds_bound", tuple(reply_ids))
        end = min(start + max_characters, len(text))
        # Tighten the source slice by complete code points, never split UTF-8 bytes.
        if len(text[start:end].encode()) > max_bytes:
            low, high = start + 1, end
            while low < high:
                middle = (low + high + 1) // 2
                if len(text[start:middle].encode()) <= max_bytes:
                    low = middle
                else:
                    high = middle - 1
            end = low
        result = await send(text[start:end], index)
        reply_ids.extend(identity for identity in result.provider_reply_ids if identity not in reply_ids)
        if result.status != "delivered":
            if index == 0:
                return result
            return SendOutcome("uncertain", acknowledgement=last, error="partial_channel_delivery_not_confirmed",
                provider_reply_ids=tuple(reply_ids))
        if index == 0:
            first = result.acknowledgement
        last = result.acknowledgement
        start, index = end, index + 1
    if index == 1:
        return SendOutcome("delivered", acknowledgement=last, provider_reply_ids=tuple(reply_ids))
    acknowledgement = json.dumps({"first":first,"last":last}, separators=(",", ":"))
    if len(acknowledgement.encode()) > 512:
        acknowledgement = last
    return SendOutcome("delivered", acknowledgement=acknowledgement, provider_reply_ids=tuple(reply_ids))
