from __future__ import annotations

import pytest

from src.config import Settings, load_settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPENAI_API_KEY", "KUVERA_API_BASE_URL", "KUVERA_API_TIMEOUT", "LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.kuvera_api_base_url == "https://api.kuvera.in"
    assert s.kuvera_api_timeout == 15.0
    assert s.openai_enabled is False


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUVERA_API_TIMEOUT", "4.5")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    s = Settings(_env_file=None)
    assert s.kuvera_api_timeout == 4.5
    assert s.openai_enabled is True
    assert "sk-test" not in repr(s)


def test_streamlit_secrets_fill_gaps_but_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir("/")  # avoid picking up a developer's .env
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    s = load_settings({"openai_api_key": "sk-from-secrets", "log_level": "ERROR"})
    assert s.openai_api_key.get_secret_value() == "sk-from-secrets"
    assert s.log_level == "DEBUG"
