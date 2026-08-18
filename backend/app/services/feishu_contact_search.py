"""Bounded app-identity search over an Agent's visible Feishu contacts."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from app.services.feishu_service import feishu_service

_API_BASE = "https://open.feishu.cn/open-apis/contact/v3"
_PAGE_SIZE = 50
_MAX_DEPARTMENTS = 1_000
_MAX_USER_PAGES = 2_000
_CONCURRENCY = 10


@dataclass(frozen=True, slots=True)
class FeishuContactMatch:
    """One private Provider match; raw IDs must not enter model-visible output."""

    open_id: str
    display_name: str
    title: str = ""


class FeishuContactSearchLimitError(RuntimeError):
    """The Provider-visible directory exceeded the bounded search window."""


def _body(payload: Mapping[str, object], *, stage: str) -> Mapping[str, object]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ValueError(f"Feishu {stage} returned an invalid data object")
    return data


def _items(data: Mapping[str, object], *, stage: str) -> list[Mapping[str, object]]:
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        raise ValueError(f"Feishu {stage} returned an invalid item list")
    return [item for item in raw_items if isinstance(item, Mapping)]


def _next_page_token(data: Mapping[str, object]) -> str | None:
    if data.get("has_more") is not True:
        return None
    token = data.get("page_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Feishu pagination omitted page_token")
    return token


def _department_id(item: Mapping[str, object]) -> str | None:
    value = item.get("open_department_id") or item.get("department_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _contact(
    item: Mapping[str, object],
    query: str,
    *,
    exact_name: bool,
) -> FeishuContactMatch | None:
    display_name = str(item.get("name") or "").strip()
    searchable = (
        display_name,
        str(item.get("en_name") or "").strip(),
        str(item.get("email") or "").strip(),
    )
    if exact_name:
        matched = display_name.casefold() == query
    else:
        matched = any(
            query in value.casefold()
            for value in searchable
            if value
        )
    if not matched:
        return None
    open_id = item.get("open_id") or item.get("user_id")
    if not isinstance(open_id, str) or not open_id.strip() or not display_name:
        return None
    return FeishuContactMatch(
        open_id=open_id.strip(),
        display_name=display_name,
        title=str(item.get("title") or "").strip(),
    )


async def _get(
    client: httpx.AsyncClient,
    token: str,
    url: str,
    *,
    params: dict[str, object],
    stage: str,
) -> Mapping[str, object]:
    response = await client.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
    )
    payload = feishu_service._parse_api_response(response, stage=stage)
    if not isinstance(payload, Mapping):
        raise ValueError(f"Feishu {stage} returned an invalid response")
    return payload


async def _visible_department_ids(
    client: httpx.AsyncClient,
    token: str,
) -> list[str]:
    department_ids: list[str] = []
    page_token: str | None = None
    while True:
        params: dict[str, object] = {
            "department_id_type": "open_department_id",
            "fetch_child": "true",
            "page_size": _PAGE_SIZE,
        }
        if page_token:
            params["page_token"] = page_token
        payload = await _get(
            client,
            token,
            f"{_API_BASE}/departments",
            params=params,
            stage="contact_departments",
        )
        data = _body(payload, stage="contact_departments")
        for item in _items(data, stage="contact_departments"):
            department_id = _department_id(item)
            if department_id and department_id not in department_ids:
                department_ids.append(department_id)
        if len(department_ids) > _MAX_DEPARTMENTS:
            raise FeishuContactSearchLimitError(
                "Feishu visible department count exceeded the search limit"
            )
        page_token = _next_page_token(data)
        if page_token is None:
            break

    if "0" in department_ids:
        page_token = None
        while True:
            params = {
                "department_id_type": "open_department_id",
                "fetch_child": "true",
                "page_size": _PAGE_SIZE,
            }
            if page_token:
                params["page_token"] = page_token
            payload = await _get(
                client,
                token,
                f"{_API_BASE}/departments/0/children",
                params=params,
                stage="contact_department_children",
            )
            data = _body(payload, stage="contact_department_children")
            for item in _items(data, stage="contact_department_children"):
                department_id = _department_id(item)
                if department_id and department_id not in department_ids:
                    department_ids.append(department_id)
            if len(department_ids) > _MAX_DEPARTMENTS:
                raise FeishuContactSearchLimitError(
                    "Feishu visible department count exceeded the search limit"
                )
            page_token = _next_page_token(data)
            if page_token is None:
                break
    return department_ids


async def _department_matches(
    client: httpx.AsyncClient,
    token: str,
    department_id: str,
    query: str,
    page_budget: list[int],
    *,
    exact_name: bool,
) -> list[FeishuContactMatch]:
    matches: list[FeishuContactMatch] = []
    page_token: str | None = None
    while True:
        page_budget[0] += 1
        if page_budget[0] > _MAX_USER_PAGES:
            raise FeishuContactSearchLimitError(
                "Feishu visible user pages exceeded the search limit"
            )
        params: dict[str, object] = {
            "department_id": department_id,
            "department_id_type": "open_department_id",
            "user_id_type": "open_id",
            "page_size": _PAGE_SIZE,
        }
        if page_token:
            params["page_token"] = page_token
        payload = await _get(
            client,
            token,
            f"{_API_BASE}/users/find_by_department",
            params=params,
            stage="contact_users",
        )
        data = _body(payload, stage="contact_users")
        for item in _items(data, stage="contact_users"):
            contact = _contact(item, query, exact_name=exact_name)
            if contact is not None:
                matches.append(contact)
        page_token = _next_page_token(data)
        if page_token is None:
            return matches


async def _independent_matches(
    client: httpx.AsyncClient,
    token: str,
    query: str,
    page_budget: list[int],
    *,
    exact_name: bool,
) -> list[FeishuContactMatch]:
    """Read users granted directly in the app contact scope.

    Feishu's legacy list endpoint is the only app-identity endpoint that
    exposes independently authorized users when no department is in scope.
    """
    matches: list[FeishuContactMatch] = []
    page_token: str | None = None
    while True:
        page_budget[0] += 1
        if page_budget[0] > _MAX_USER_PAGES:
            raise FeishuContactSearchLimitError(
                "Feishu visible user pages exceeded the search limit"
            )
        params: dict[str, object] = {
            "department_id_type": "open_department_id",
            "user_id_type": "open_id",
            "page_size": 100,
        }
        if page_token:
            params["page_token"] = page_token
        payload = await _get(
            client,
            token,
            f"{_API_BASE}/users",
            params=params,
            stage="contact_independent_users",
        )
        data = _body(payload, stage="contact_independent_users")
        for item in _items(data, stage="contact_independent_users"):
            contact = _contact(item, query, exact_name=exact_name)
            if contact is not None:
                matches.append(contact)
        page_token = _next_page_token(data)
        if page_token is None:
            return matches


async def search_feishu_contacts(
    token: str,
    query: str,
    *,
    limit: int,
    offset: int,
    exact_name: bool = False,
) -> tuple[list[FeishuContactMatch], bool]:
    """Search the Agent app's visible Feishu directory without exposing IDs."""
    normalized_query = query.strip().casefold()
    if not normalized_query:
        return [], False
    target_count = offset + limit + 1
    found_by_open_id: dict[str, FeishuContactMatch] = {}
    page_budget = [0]
    async with httpx.AsyncClient(timeout=20) as client:
        for contact in await _independent_matches(
            client,
            token,
            normalized_query,
            page_budget,
            exact_name=exact_name,
        ):
            found_by_open_id.setdefault(contact.open_id, contact)
        department_ids = await _visible_department_ids(client, token)
        for start in range(0, len(department_ids), _CONCURRENCY):
            batch = department_ids[start : start + _CONCURRENCY]
            batch_matches = await asyncio.gather(
                *(
                    _department_matches(
                        client,
                        token,
                        department_id,
                        normalized_query,
                        page_budget,
                        exact_name=exact_name,
                    )
                    for department_id in batch
                )
            )
            for matches in batch_matches:
                for contact in matches:
                    found_by_open_id.setdefault(contact.open_id, contact)
            if len(found_by_open_id) >= target_count:
                break
    found = list(found_by_open_id.values())
    return found[offset : offset + limit], len(found) > offset + limit


__all__ = [
    "FeishuContactMatch",
    "FeishuContactSearchLimitError",
    "search_feishu_contacts",
]
