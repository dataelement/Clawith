"""Concurrency limiter for LLM API calls.

Uses asyncio.Semaphore instances keyed by group name to limit concurrent LLM
requests per group.  Groups can be custom-defined (by model / provider) or
fall back to per-provider defaults.
"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from typing import AsyncIterator

from loguru import logger


_DEFAULT_CONCURRENCY: dict[str, int] = {
    "openai": 5,
    "anthropic": 5,
    "deepseek": 3,
    "qwen": 5,
    "gemini": 5,
    "azure": 5,
    "ollama": 2,
    "vllm": 2,
    "openrouter": 5,
    "minimax": 3,
    "zhipu": 3,
    "custom": 5,
}


class LLMConcurrencyError(Exception):
    """Raised when acquiring a concurrency slot times out."""

    def __init__(self, group: str, timeout: float) -> None:
        self.group = group
        self.timeout = timeout
        super().__init__(
            f"LLM concurrency limit reached for group '{group}': "
            f"could not acquire a slot within {timeout}s. "
            f"Consider increasing max_concurrency for this group."
        )


class ConcurrencyManager:
    """Singleton-style manager that owns all concurrency semaphores."""

    _instance: ConcurrencyManager | None = None

    def __new__(cls) -> ConcurrencyManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._config: dict[str, dict] = {}
        self._default_config: dict[str, int] = dict(_DEFAULT_CONCURRENCY)
        self._active_counts: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._initialized = True

    def _api_key_hash(self, api_key: str) -> str:
        return hashlib.sha256(api_key.encode()).hexdigest()[:16]

    def resolve_group(self, provider: str, model: str, api_key: str) -> str:
        key_hash = self._api_key_hash(api_key)
        for group_name, cfg in self._config.items():
            models: list[str] = cfg.get("models", [])
            providers: list[str] = cfg.get("providers", [])
            if model in models:
                return f"{group_name}:{key_hash}"
            if provider in providers and not models:
                return f"{group_name}:{key_hash}"
        return f"{provider}:{key_hash}"

    def _get_or_create_semaphore(self, group: str) -> asyncio.Semaphore:
        if group in self._semaphores:
            return self._semaphores[group]

        group_prefix = group.split(":")[0]
        custom_cfg = self._config.get(group_prefix)
        if custom_cfg:
            max_c = custom_cfg.get("max_concurrency", _DEFAULT_CONCURRENCY.get("custom", 5))
        else:
            max_c = self._default_config.get(group_prefix, _DEFAULT_CONCURRENCY.get("custom", 5))

        sem = asyncio.Semaphore(max_c)
        self._semaphores[group] = sem
        self._active_counts.setdefault(group, 0)
        self._locks.setdefault(group, asyncio.Lock())
        logger.debug("Created semaphore for group '{}' with max_concurrency={}", group, max_c)
        return sem

    async def acquire(
        self,
        provider: str,
        model: str,
        api_key: str,
        timeout: float = 60.0,
    ) -> str:
        group = self.resolve_group(provider, model, api_key)
        sem = self._get_or_create_semaphore(group)

        try:
            await asyncio.wait_for(sem.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            raise LLMConcurrencyError(group, timeout)

        lock = self._locks.setdefault(group, asyncio.Lock())
        async with lock:
            self._active_counts[group] = self._active_counts.get(group, 0) + 1

        return group

    async def release(self, group: str) -> None:
        sem = self._semaphores.get(group)
        if sem is None:
            logger.warning("release() called for unknown group '{}'", group)
            return

        lock = self._locks.setdefault(group, asyncio.Lock())
        async with lock:
            count = self._active_counts.get(group, 0)
            if count > 0:
                self._active_counts[group] = count - 1

        sem.release()

    def configure_groups(self, configs: list[dict]) -> None:
        for cfg in configs:
            name: str = cfg["name"]
            self._config[name] = {
                "max_concurrency": cfg.get("max_concurrency", 5),
                "models": list(cfg.get("models", [])),
                "providers": list(cfg.get("providers", [])),
            }

        keys_to_reset = [
            k for k in list(self._semaphores)
            if k.split(":")[0] in self._config
        ]
        for k in keys_to_reset:
            del self._semaphores[k]
            self._active_counts.pop(k, None)
            self._locks.pop(k, None)

        logger.info(
            "Updated concurrency groups: {}",
            list(self._config.keys()),
        )

    def get_status(self) -> dict:
        groups: dict[str, dict] = {}

        for group_name, cfg in self._config.items():
            active_entries = {
                k: self._active_counts.get(k, 0)
                for k in self._semaphores
                if k.split(":")[0] == group_name
            }
            total_active = sum(active_entries.values())
            max_c = cfg.get("max_concurrency", 5)
            groups[group_name] = {
                "max_concurrency": max_c,
                "active_count": total_active,
                "models": cfg.get("models", []),
                "providers": cfg.get("providers", []),
            }

        for sem_key in self._semaphores:
            prefix = sem_key.split(":")[0]
            if prefix not in self._config:
                if prefix not in groups:
                    max_c = self._default_config.get(
                        prefix, _DEFAULT_CONCURRENCY.get("custom", 5)
                    )
                    groups[prefix] = {
                        "max_concurrency": max_c,
                        "active_count": 0,
                        "models": [],
                        "providers": [],
                    }
                groups[prefix]["active_count"] += self._active_counts.get(sem_key, 0)

        return groups

    @classmethod
    def reset(cls) -> None:
        cls._instance = None


concurrency_manager = ConcurrencyManager()


@asynccontextmanager
async def concurrency_limit(
    provider: str,
    model: str,
    api_key: str,
    timeout: float = 60.0,
) -> AsyncIterator[str]:
    group = await concurrency_manager.acquire(provider, model, api_key, timeout=timeout)
    try:
        yield group
    finally:
        await concurrency_manager.release(group)
