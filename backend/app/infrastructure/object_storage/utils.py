"""Storage path helpers."""


def normalize_storage_key(key: str) -> str:
    """Normalize a storage key and reject traversal semantics."""
    clean = (key or "").replace("\\", "/").strip().lstrip("/")
    parts: list[str] = []
    for part in clean.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("Storage keys cannot contain parent traversal segments")
        parts.append(part)
    return "/".join(parts)
