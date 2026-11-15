"""Configuration centralisée, lue depuis l'environnement / le fichier .env."""

import logging
import os
import re
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)
DEV_SECRET_FILE = Path(__file__).resolve().parents[1] / ".secret_key"


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
    history_period: str = "15y"
    price_cache_seconds: int = 6 * 3600
    fundamentals_cache_days: int = 7

    # --- Analyse ---
    top_n: int = Field(default=20, ge=1, le=50)
    news_pause_seconds: float = 3.0  # entre deux appels IA : ménage les quotas gratuits
    max_per_sector: int = Field(default=4, ge=1)  # diversification du top
    fundamental_weight: float = Field(default=0.2, ge=0, le=0.5)  # 0,2 → ±10 points
    earnings_warning_days: int = 10

    # --- Automatisation ---
    scan_schedule: str = ""  # « HH:MM » heure de Paris, jours ouvrés ; vide = désactivé
    auto_follow_positions: int = Field(default=5, ge=1, le=20)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr | None = None
    smtp_from: str = ""

    # --- Fiscalité du PEA (taux 2026) ---
    pea_social_rate: float = 0.186  # prélèvements sociaux, dus à tout retrait
    pea_income_tax_rate: float = 0.128  # impôt sur le revenu, retrait avant 5 ans
    news_max_headlines: int = 12

    # --- OpenRouter ---
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str = "openrouter/free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: float = 60.0

    # --- Simulation ---
    default_initial_cash: float = 10_000.0
    order_fee_rate: float = Field(default=0.005, ge=0, le=0.05)  # 0,5 % par ordre
    order_fee_min: float = Field(default=0.0, ge=0, le=50)  # minimum par ordre, en euros
    order_fee_fixed: float = Field(default=0.0, ge=0, le=50)  # forfait par ordre, en euros

    @field_validator("secret_key", "openrouter_api_key", "smtp_password", mode="before")
    @classmethod
    def _empty_is_none(cls, v):
        return v or None  # « CLE= » dans le .env équivaut à une clé absente

    @field_validator("scan_schedule")
    @classmethod
    def _check_schedule(cls, v: str) -> str:
        if v and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError("SCAN_SCHEDULE attend le format HH:MM")
        return v

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
            self.secret_key = SecretStr(_dev_secret())
        return self.secret_key.get_secret_value()


def _dev_secret() -> str:
    """En développement, la clé est conservée dans un fichier local (non versionné, lisible par
    le seul propriétaire) pour que les sessions survivent aux redémarrages du serveur."""
    try:
        if DEV_SECRET_FILE.exists():
            key = DEV_SECRET_FILE.read_text(encoding="utf-8").strip()
            if len(key) >= 32:
                return key
        key = secrets.token_urlsafe(48)
        fd = os.open(DEV_SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key)
        log.warning("SECRET_KEY absente : clé de développement créée dans %s", DEV_SECRET_FILE.name)
        return key
    except OSError:
        log.warning("SECRET_KEY absente : clé éphémère générée (sessions perdues au redémarrage)")
        return secrets.token_urlsafe(48)


@lru_cache
def get_settings() -> Settings:
    return Settings()
