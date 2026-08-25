"""Provider-bound tests for app-identity Feishu contact search."""

from __future__ import annotations

import httpx
import pytest

from app.services.feishu_contact_search import (
    resolve_feishu_contacts_by_exact_names,
    search_feishu_contacts,
)
from app.services.feishu_service import FeishuAPIError


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


@pytest.mark.asyncio
async def test_searches_visible_departments_with_tenant_token(monkeypatch) -> None:
    calls: list[tuple[str, dict, dict]] = []

    class Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, **kwargs):
            calls.append((url, kwargs["params"], kwargs["headers"]))
            if url.endswith("/users"):
                return FakeResponse(
                    {"code": 0, "data": {"items": [], "has_more": False}}
                )
            if url.endswith("/departments"):
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "items": [{"open_department_id": "0"}],
                            "has_more": False,
                        },
                    }
                )
            if url.endswith("/departments/0/children"):
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "items": [{"open_department_id": "od-engineering"}],
                            "has_more": False,
                        },
                    }
                )
            department_id = kwargs["params"]["department_id"]
            items = (
                [
                    {
                        "open_id": "ou-private-zhou",
                        "name": "周逸飞",
                        "en_name": "Yifei Zhou",
                        "title": "Engineer",
                    }
                ]
                if department_id == "od-engineering"
                else []
            )
            return FakeResponse(
                {"code": 0, "data": {"items": items, "has_more": False}}
            )

    monkeypatch.setattr(httpx, "AsyncClient", Client)

    matches, has_more = await search_feishu_contacts(
        "tenant-token",
        "周逸飞",
        limit=20,
        offset=0,
    )

    assert has_more is False
    assert len(matches) == 1
    assert matches[0].open_id == "ou-private-zhou"
    assert matches[0].display_name == "周逸飞"
    assert matches[0].title == "Engineer"
    assert all(headers == {"Authorization": "Bearer tenant-token"} for _, _, headers in calls)
    assert any(url.endswith("/users/find_by_department") for url, _, _ in calls)


@pytest.mark.asyncio
async def test_searches_users_granted_directly_in_app_scope(monkeypatch) -> None:
    class Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, **_kwargs):
            if url.endswith("/users"):
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "items": [
                                {
                                    "open_id": "ou-private-zhou",
                                    "name": "周逸飞",
                                }
                            ],
                            "has_more": False,
                        },
                    }
                )
            if url.endswith("/departments"):
                return FakeResponse(
                    {"code": 0, "data": {"items": [], "has_more": False}}
                )
            raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(httpx, "AsyncClient", Client)

    matches, has_more = await search_feishu_contacts(
        "tenant-token",
        "周逸飞",
        limit=20,
        offset=0,
        exact_name=True,
    )

    assert has_more is False
    assert [(match.display_name, match.open_id) for match in matches] == [
        ("周逸飞", "ou-private-zhou")
    ]


@pytest.mark.asyncio
async def test_resolves_multiple_exact_names_in_one_provider_traversal(monkeypatch) -> None:
    calls: list[str] = []

    class Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, **_kwargs):
            calls.append(url)
            if url.endswith("/users"):
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "items": [
                                {"open_id": "ou-alice", "name": "Alice"},
                                {"open_id": "ou-bob", "name": "Bob"},
                                {"open_id": "ou-charlie-a", "name": "Charlie"},
                            ],
                            "has_more": False,
                        },
                    }
                )
            if url.endswith("/departments"):
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "items": [{"open_department_id": "od-engineering"}],
                            "has_more": False,
                        },
                    }
                )
            if url.endswith("/users/find_by_department"):
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "items": [
                                {"open_id": "ou-alice", "name": "Alice"},
                                {"open_id": "ou-charlie-b", "name": "Charlie"},
                            ],
                            "has_more": False,
                        },
                    }
                )
            raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(httpx, "AsyncClient", Client)

    resolved = await resolve_feishu_contacts_by_exact_names(
        "tenant-token",
        ["Alice", "Bob", "Charlie"],
    )

    assert resolved == {
        "Alice": "ou-alice",
        "Bob": "ou-bob",
        "Charlie": None,
    }
    assert sum(url.endswith("/users") for url in calls) == 1
    assert sum(url.endswith("/departments") for url in calls) == 1


@pytest.mark.asyncio
async def test_provider_rejection_is_not_converted_to_empty_results(monkeypatch) -> None:
    class Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url, **_kwargs):
            return FakeResponse(
                {"code": 40060, "msg": "no department authority"},
                status_code=400,
            )

    monkeypatch.setattr(httpx, "AsyncClient", Client)

    with pytest.raises(FeishuAPIError) as raised:
        await search_feishu_contacts(
            "tenant-token",
            "周逸飞",
            limit=20,
            offset=0,
        )

    assert raised.value.code == 40060
