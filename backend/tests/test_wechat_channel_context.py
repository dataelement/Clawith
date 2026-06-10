from app.services.wechat_channel import (
    WECHAT_CONTEXT_CACHE_KEY,
    WECHAT_CONTEXT_CACHE_LIMIT,
    get_wechat_context_entry,
    update_wechat_context_cache,
)


def test_update_wechat_context_cache_stores_latest_entry():
    extra = update_wechat_context_cache(
        {},
        from_user_id="wx_user_123",
        context_token="ctx_abc",
        conv_id="wechat_session_1",
    )

    assert WECHAT_CONTEXT_CACHE_KEY in extra
    entry = get_wechat_context_entry(extra, from_user_id="wx_user_123")
    assert entry is not None
    assert entry["context_token"] == "ctx_abc"
    assert entry["conv_id"] == "wechat_session_1"


def test_update_wechat_context_cache_prunes_old_entries():
    extra = {}
    for idx in range(WECHAT_CONTEXT_CACHE_LIMIT + 5):
        extra = update_wechat_context_cache(
            extra,
            from_user_id=f"wx_user_{idx}",
            context_token=f"ctx_{idx}",
            conv_id=f"wechat_session_{idx}",
        )

    cache = extra[WECHAT_CONTEXT_CACHE_KEY]
    assert len(cache) == WECHAT_CONTEXT_CACHE_LIMIT
    assert get_wechat_context_entry(extra, from_user_id="wx_user_0") is None
    assert get_wechat_context_entry(extra, from_user_id=f"wx_user_{WECHAT_CONTEXT_CACHE_LIMIT + 4}") is not None
