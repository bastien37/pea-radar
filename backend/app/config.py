"""Configuration centralisée, lue depuis l'environnement / le fichier .env."""

import logging
import secrets
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["dev", "prod"] = "dev"
    database_url: str = "sqlite:///./pea_radar.db"

    # --- Sécurité ---
    secret_key: SecretStr | None = None
    access_token_minutes: int = 60
    cookie_secure: bool = False  # mettre true derrière HTTPS
    cors_origins: list[str] = ["http://localhost:5173"]
    allow_registration: bool = False  # le tout premier compte peut toujours être créé
    login_max_attempts: int = 5
    login_window_seconds: int = 300

    # --- Données de marché ---
    data_provider: Literal["yfinance", "synthetic"] = "yfinance"
    history_period: str = "3y"
    price_cache_seconds: int = 3600

    # --- Analyse ---
    top_n: int = 5
    news_max_headlines: int = 12

    # --- OpenRouter ---
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str = "openrouter/free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: float = 60.0

    # --- Simulation ---
    default_initial_cash: float = 10_000.0
    order_fee_rate: float = 0.005  # 0,5 % par ordre

    @field_validator("secret_key", "openrouter_api_key", mode="before")
    @classmethod
    def _empty_is_none(cls, v):
        return v or None  # « CLE= » dans le .env équivaut à une clé absente

    @field_validator("secret_key")
    @classmethod
    def _check_secret(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and len(v.get_secret_value()) < 32:
            raise ValueError("SECRET_KEY doit contenir au moins 32 caractères")
        return v

    def signing_key(self) -> str:
        if self.secret_key is None:
            if self.environment == "prod":
                raise RuntimeError("SECRET_KEY est obligatoire en production")
            log.warning("SECRET_KEY absente : clé éphémère générée (sessions perdues au redémarrage)")
            self.secret_key = SecretStr(secrets.token_urlsafe(48))
        return self.secret_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
