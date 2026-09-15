import pytest

from app.modules.auth.crypto import create_password_verifier, token_digest, verify_login_password


@pytest.mark.asyncio
async def test_password_verifier_is_salted_versioned_and_does_not_contain_password() -> None:
    first = await create_password_verifier("correct horse battery staple")
    second = await create_password_verifier("correct horse battery staple")

    assert first != second
    assert "correct horse" not in first
    assert await verify_login_password("correct horse battery staple", first)
    assert not await verify_login_password("wrong", first)


def test_token_digest_never_contains_raw_token() -> None:
    token = "opaque-token"
    digest = token_digest(token)
    assert token not in digest
    assert len(digest) == 64
