"""Tests for the configuration layer: env -> Settings -> LLM client.

Strict TDD (deterministic module). Two properties matter beyond plain parsing:

  * the API key must never be printable — a key that leaks into a log line or a
    traceback is leaked, and this is the one place that can guarantee it doesn't;
  * the mock/real switch must fail *closed* toward "no network": a missing key
    degrades to the mock client rather than raising, so no code path can
    accidentally attempt an unauthenticated real call.
"""

from __future__ import annotations

import pytest

from ai_native_rpg.config import Settings, build_llm_client
from ai_native_rpg.llm.mock import MockLLMClient

DEEPSEEK_VARS = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "JUDGE_MODEL",
    "USE_MOCK_LLM",
)


@pytest.fixture
def clean_env(monkeypatch):
    """A pristine environment: no DeepSeek vars, and .env loading disabled.

    Without disabling .env the developer's real key would bleed into these tests
    and the assertions below would depend on whose machine runs them.
    """
    for var in DEEPSEEK_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


class TestSettingsParsing:
    def test_reads_values_from_environment(self, clean_env):
        clean_env.setenv("DEEPSEEK_API_KEY", "sk-abc123")
        clean_env.setenv("DEEPSEEK_BASE_URL", "https://example.invalid")
        clean_env.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

        settings = Settings.from_env(load_dotenv=False)

        assert settings.api_key is not None
        assert settings.api_key.get_secret_value() == "sk-abc123"
        assert settings.base_url == "https://example.invalid"
        assert settings.model == "deepseek-v4-flash"

    def test_defaults_when_unset(self, clean_env):
        settings = Settings.from_env(load_dotenv=False)

        assert settings.api_key is None
        assert settings.base_url == "https://api.deepseek.com"
        assert settings.model == "deepseek-v4-flash"
        # no key means there is nothing to call, so mock is the only safe default
        assert settings.use_mock is True

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("1", True), ("0", False), ("true", True), ("TRUE", True), ("false", False), ("", False)],
    )
    def test_use_mock_accepts_common_spellings(self, clean_env, raw, expected):
        clean_env.setenv("DEEPSEEK_API_KEY", "sk-abc123")
        clean_env.setenv("USE_MOCK_LLM", raw)
        assert Settings.from_env(load_dotenv=False).use_mock is expected

    def test_dotenv_does_not_override_real_environment(self, clean_env, tmp_path):
        """os.environ wins over .env: an explicitly exported var is a deliberate
        override (CI, a one-off run) and must not be silently replaced by a file."""
        env_file = tmp_path / ".env"
        env_file.write_text("DEEPSEEK_MODEL=from-dotenv\n", encoding="utf-8")
        clean_env.setenv("DEEPSEEK_MODEL", "from-environ")

        settings = Settings.from_env(dotenv_path=env_file)

        assert settings.model == "from-environ"

    def test_dotenv_fills_what_environment_lacks(self, clean_env, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("DEEPSEEK_API_KEY=sk-from-dotenv\nUSE_MOCK_LLM=0\n", encoding="utf-8")

        settings = Settings.from_env(dotenv_path=env_file)

        assert settings.api_key.get_secret_value() == "sk-from-dotenv"
        assert settings.use_mock is False


class TestSecretHandling:
    def test_repr_and_str_mask_the_key(self, clean_env):
        clean_env.setenv("DEEPSEEK_API_KEY", "sk-supersecret-value")
        settings = Settings.from_env(load_dotenv=False)

        assert "sk-supersecret-value" not in repr(settings)
        assert "sk-supersecret-value" not in str(settings)

    def test_model_dump_masks_the_key(self, clean_env):
        """Serialisation is how a key would reach a trace file or a log record."""
        clean_env.setenv("DEEPSEEK_API_KEY", "sk-supersecret-value")
        settings = Settings.from_env(load_dotenv=False)

        assert "sk-supersecret-value" not in str(settings.model_dump())
        assert "sk-supersecret-value" not in settings.model_dump_json()

    def test_key_is_still_retrievable_for_the_client(self, clean_env):
        """Masking must not make the key unusable — the client needs the real value."""
        clean_env.setenv("DEEPSEEK_API_KEY", "sk-supersecret-value")
        settings = Settings.from_env(load_dotenv=False)

        assert settings.api_key.get_secret_value() == "sk-supersecret-value"


class TestClientFactory:
    def test_mock_when_use_mock_is_set(self, clean_env):
        clean_env.setenv("DEEPSEEK_API_KEY", "sk-abc123")
        clean_env.setenv("USE_MOCK_LLM", "1")

        client = build_llm_client(Settings.from_env(load_dotenv=False))

        assert isinstance(client, MockLLMClient)

    def test_mock_when_key_is_missing_even_if_mock_disabled(self, clean_env):
        """Fail closed: no key must not mean "try anyway and 401"."""
        clean_env.setenv("USE_MOCK_LLM", "0")

        client = build_llm_client(Settings.from_env(load_dotenv=False))

        assert isinstance(client, MockLLMClient)

    def test_real_client_when_key_present_and_mock_disabled(self, clean_env):
        from ai_native_rpg.llm.deepseek import DeepSeekClient

        clean_env.setenv("DEEPSEEK_API_KEY", "sk-abc123")
        clean_env.setenv("USE_MOCK_LLM", "0")

        client = build_llm_client(Settings.from_env(load_dotenv=False))

        assert isinstance(client, DeepSeekClient)

    def test_blank_key_counts_as_missing(self, clean_env):
        """.env.example ships a placeholder; whitespace or empty is not a key."""
        clean_env.setenv("DEEPSEEK_API_KEY", "   ")
        clean_env.setenv("USE_MOCK_LLM", "0")

        settings = Settings.from_env(load_dotenv=False)

        assert settings.api_key is None
        assert isinstance(build_llm_client(settings), MockLLMClient)


class TestTestSuiteIsOffline:
    def test_conftest_forces_mock_regardless_of_developer_env(self):
        """The autouse fixture in conftest must pin USE_MOCK_LLM=1, so that running
        the suite on a machine with a real key still makes no network call."""
        import os

        assert os.environ["USE_MOCK_LLM"] == "1"
        assert Settings.from_env(load_dotenv=False).use_mock is True
