"""Configuration: environment -> ``Settings`` -> an ``LLMClient``.

The single place that reads credentials, so the rules about them live in one
place too (docs/06_NPC_Agent_Spec.md#5):

  * the key is read from the environment only — never a literal in Python, never
    a function default;
  * it is held as a ``SecretStr``, so ``repr``/``str``/``model_dump`` mask it and
    an accidental log line or traceback cannot leak it;
  * ``build_llm_client`` fails *closed* toward not touching the network: with
    ``USE_MOCK_LLM=1``, or with no usable key at all, it returns the mock. A
    missing key is a configuration state, not an exception — degrading to the
    mock keeps tests and no-key checkouts runnable, and makes an unauthenticated
    real call unreachable rather than merely unlikely.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr

from .llm.base import LLMClient

#: Repo root, i.e. the directory holding ``.env``.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

#: Spellings accepted as true for boolean env vars. Anything else is false, so a
#: typo reads as "off" for a flag whose "on" state enables network access.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _as_bool(raw: str | None, *, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def _clean(raw: str | None) -> str | None:
    """Normalise an env value, treating blank as absent.

    ``.env.example`` ships placeholders and a half-filled ``.env`` is common, so
    whitespace must not be mistaken for a configured value.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


class Settings(BaseModel):
    """Resolved runtime configuration."""

    api_key: SecretStr | None = Field(
        default=None, description="DEEPSEEK_API_KEY; None means 'no real backend available'"
    )
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    judge_model: str = DEFAULT_MODEL
    use_mock: bool = Field(
        default=True,
        description="True disables all network access; forced True when api_key is None",
    )

    @classmethod
    def from_env(
        cls,
        *,
        load_dotenv: bool = True,
        dotenv_path: str | Path | None = None,
    ) -> Settings:
        """Build settings from ``os.environ``, optionally filling gaps from ``.env``.

        ``os.environ`` takes precedence: an explicitly exported variable is a
        deliberate override (CI, a one-off run) and must not be silently replaced
        by a file on disk. Passing ``dotenv_path`` implies loading it.
        """
        layered: dict[str, str | None] = {}
        if load_dotenv or dotenv_path is not None:
            path = Path(dotenv_path) if dotenv_path is not None else _PROJECT_ROOT / ".env"
            if path.is_file():
                layered.update(dotenv_values(path))
        layered.update(os.environ)

        key = _clean(layered.get("DEEPSEEK_API_KEY"))
        # A key that is still the shipped placeholder is not a key.
        if key is not None and key.startswith("sk-your-key"):
            key = None

        return cls(
            api_key=SecretStr(key) if key is not None else None,
            base_url=_clean(layered.get("DEEPSEEK_BASE_URL")) or DEFAULT_BASE_URL,
            model=_clean(layered.get("DEEPSEEK_MODEL")) or DEFAULT_MODEL,
            judge_model=_clean(layered.get("JUDGE_MODEL")) or DEFAULT_MODEL,
            # No key means nothing to call, so mock regardless of what was asked.
            use_mock=True if key is None else _as_bool(layered.get("USE_MOCK_LLM")),
        )

    @property
    def has_real_backend(self) -> bool:
        return self.api_key is not None and not self.use_mock


def build_llm_client(settings: Settings | None = None, **mock_kwargs) -> LLMClient:
    """Return the client the current configuration allows.

    ``mock_kwargs`` is forwarded to ``MockLLMClient`` (e.g. a scripted response
    list) so a demo can run the full chain with no key.
    """
    settings = settings or Settings.from_env()

    if not settings.has_real_backend:
        from .llm.mock import MockLLMClient

        return MockLLMClient(**mock_kwargs)

    from .llm.deepseek import DeepSeekClient

    assert settings.api_key is not None  # guaranteed by has_real_backend
    return DeepSeekClient(
        api_key=settings.api_key,
        base_url=settings.base_url,
        model=settings.model,
    )
