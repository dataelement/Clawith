"""Storage path helpers."""


def normalize_storage_key(key: str) -> str:
    """Normalize a storage key and reject traversal semantics."""
    clean = (key or "").replace("\\", "/").strip().lstrip("/")
    parts: list[str] = []
    for part in clean.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def agent_storage_prefix(agent_id: str) -> str:
    return normalize_storage_key(agent_id)


def tenant_storage_prefix(tenant_id: str) -> str:
    return normalize_storage_key(f"enterprise_info_{tenant_id}")
