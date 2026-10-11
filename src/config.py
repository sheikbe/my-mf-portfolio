"""Application configuration.

Values are resolved in this order (first wins):
1. Process environment variables
2. ``.env`` file in the working directory
3. Streamlit secrets (``.streamlit/secrets.toml`` or the Streamlit Cloud secrets UI)
4. Defaults below

The Kuvera JWT session token is deliberately NOT part of configuration: it is entered at
runtime in the UI and only ever kept in ``st.session_state``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_KUVERA_API_BASE_URL = "https://api.kuvera.in"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    kuvera_api_base_url: str = DEFAULT_KUVERA_API_BASE_URL
    kuvera_api_timeout: float = 15.0
    log_level: str = "INFO"

    @property
    def openai_enabled(self) -> bool:
        return bool(self.openai_api_key and self.openai_api_key.get_secret_value().strip())


def _streamlit_secrets() -> dict[str, Any]:
    """Return config keys found in ``st.secrets`` (empty if Streamlit/secrets are unavailable)."""
    try:
        import streamlit as st

        secrets = dict(st.secrets)
    except Exception:  # noqa: BLE001 - st.secrets raises various errors when absent
        return {}
    fields = {name.upper() for name in Settings.model_fields}
    return {key.lower(): value for key, value in secrets.items() if key.upper() in fields}


def load_settings(secrets: dict[str, Any] | None = None) -> Settings:
    """Build Settings, using Streamlit secrets only for keys not set in the environment."""
    secrets = _streamlit_secrets() if secrets is None else secrets
    overrides = {
        key: value
        for key, value in secrets.items()
        if key.upper() not in os.environ and key.lower() not in os.environ
    }
    settings = Settings()
    if not overrides:
        return settings
    # .env/env values beat secrets: only fill fields still at their default.
    unset = {k: v for k, v in overrides.items() if k not in settings.model_fields_set}
    return Settings(**{**settings.model_dump(exclude_unset=True), **unset}) if unset else settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
