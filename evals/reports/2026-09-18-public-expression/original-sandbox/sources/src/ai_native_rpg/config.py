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

import logging
import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr

from .llm.base import LLMClient

logger = logging.getLogger(__name__)

#: Repo root, i.e. the directory holding ``.env``.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

#: Per-provider env var prefix and fallbacks. Both providers speak the same
#: OpenAI-compatible ``chat/completions`` surface, so switching does not select a
#: different client — only a different set of variables to read. That the vendor
#: choice costs one enum entry rather than a second implementation is the payoff of
#: talking HTTP directly instead of through a vendor SDK (docs/06 §5).
#:
#: An OpenAI-compatible relay needs its own base_url and has no sensible default,
#: so ``openai`` deliberately falls back to nothing: an unset URL must read as
#: "not configured" and degrade to the mock, not silently hit api.openai.com with a
#: relay's key.
_PROVIDERS: dict[str, tuple[str, str | None, str | None]] = {
    # provider -> (env prefix, default base_url, default model)
    "deepseek": ("DEEPSEEK", DEFAULT_BASE_URL, DEFAULT_MODEL),
    "openai": ("OPENAI", None, None),
}

DEFAULT_PROVIDER = "deepseek"

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


def _warn_if_suspicious_model(name: str | None, *, var: str) -> None:
    """Log once when a model name looks like it picked up stray text.

    ``_clean`` only strips the ends, so interior junk survives into the request body.
    Found in a real ``.env``: ``OPENAI_MODEL=gpt-5.6-sol   4   ...   v``, from a stray
    paste. The relay answered anyway — presumably falling back to some default — so a
    whole session ran against an unknown model while every trace recorded the dirty
    string as ``model_used``. Nothing failed, which is what made it hard to notice.

    A warning rather than an error: a model name is a vendor string, and this cannot
    know which ones are valid. Refusing to start on an unrecognised shape would break
    legitimately odd names (dated snapshots, org-prefixed deployments) for a guess.
    Same stance the scenario loader takes toward silent content bugs, one notch softer
    because here the remote endpoint is the real authority.
    """
    if not name:
        return
    if any(ch.isspace() for ch in name) or any(ord(ch) < 32 for ch in name):
        logger.warning(
            "%s looks malformed: %r contains whitespace or control characters. "
            "It is sent to the API verbatim, so the model actually serving requests "
            "may not be the one you intended.",
            var,
            name,
        )


class Settings(BaseModel):
    """Resolved runtime configuration."""

    provider: str = Field(
        default=DEFAULT_PROVIDER, description="which key/url/model set was read; see _PROVIDERS"
    )
    public_expression: bool = Field(
        default=False,
        description="Use the public-expression boundary in real interactive game sessions",
    )
    api_key: SecretStr | None = Field(
        default=None, description="the provider's API key; None means 'no real backend available'"
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

        requested = (_clean(layered.get("LLM_PROVIDER")) or DEFAULT_PROVIDER).lower()
        # An unknown name falls back rather than raising: this runs at demo startup,
        # and a typo should not be the difference between a running demo and a
        # traceback. It degrades to the mock anyway if the fallback has no key.
        prefix, default_url, default_model = _PROVIDERS.get(requested, _PROVIDERS[DEFAULT_PROVIDER])
        provider = requested if requested in _PROVIDERS else DEFAULT_PROVIDER

        key = _clean(layered.get(f"{prefix}_API_KEY"))
        # A key that is still the shipped placeholder is not a key.
        if key is not None and key.startswith("sk-your-key"):
            key = None

        base_url = _clean(layered.get(f"{prefix}_BASE_URL")) or default_url
        model = _clean(layered.get(f"{prefix}_MODEL")) or default_model

        _warn_if_suspicious_model(model, var=f"{prefix}_MODEL")
        judge_model = _clean(layered.get("JUDGE_MODEL")) or DEFAULT_MODEL
        _warn_if_suspicious_model(judge_model, var="JUDGE_MODEL")

        # A provider with no URL or model to call is not configured, whatever key it
        # was given. Treating that as "no backend" keeps the failure at startup
        # ("running on the mock") instead of a 404 mid-conversation.
        usable = key is not None and bool(base_url) and bool(model)

        return cls(
            provider=provider,
            api_key=SecretStr(key) if usable else None,
            base_url=base_url or DEFAULT_BASE_URL,
            model=model or DEFAULT_MODEL,
            judge_model=judge_model,
            # Nothing to call means mock regardless of what was asked.
            use_mock=True if not usable else _as_bool(layered.get("USE_MOCK_LLM")),
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

    from .llm.openai_compatible import OpenAICompatibleClient

    assert settings.api_key is not None  # guaranteed by has_real_backend
    return OpenAICompatibleClient(
        api_key=settings.api_key,
        base_url=settings.base_url,
        model=settings.model,
    )
