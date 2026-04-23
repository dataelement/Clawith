from app.services.llm.utils import *  # noqa: F403

from dataclasses import dataclass
from typing import Optional

from app.models.llm import LLMModel
from app.services.llm import LLMClient, create_llm_client, get_model_api_key


@dataclass
class FallbackModelConfig:
    provider: str
    api_key: str
    model: str
    base_url: str
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    request_timeout: Optional[float] = None

    @classmethod
    def from_orm(cls, model: LLMModel) -> "FallbackModelConfig":
        return cls(
            provider=model.provider,
            api_key=get_model_api_key(model),
            model=model.model,
            base_url=model.base_url,
            temperature=model.temperature,
            max_output_tokens=getattr(model, "max_output_tokens", None),
            request_timeout=getattr(model, "request_timeout", None),
        )


def try_create_fallback_client(
    fallback: Optional[FallbackModelConfig],
    *,
    default_timeout: float = 120.0,
    log_prefix: str = "",
) -> Optional[LLMClient]:
    if not fallback:
        return None
    from loguru import logger

    try:
        client = create_llm_client(
            provider=fallback.provider,
            api_key=fallback.api_key,
            model=fallback.model,
            base_url=fallback.base_url,
            timeout=float(fallback.request_timeout or default_timeout),
        )
        logger.warning(f"{log_prefix}Primary model failed, switching to fallback: {fallback.model}")
        return client
    except Exception as e:
        logger.error(f"{log_prefix}Failed to create fallback LLM client: {e}")
        return None
