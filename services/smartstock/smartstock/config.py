"""Service configuration. Env-driven, 12-factor."""
from __future__ import annotations
import os
from dataclasses import dataclass


def _f(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _i(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("SMARTSTOCK_HOST", "0.0.0.0")
    port: int = _i("SMARTSTOCK_PORT", 8100)
    policy_path: str = os.getenv("SMARTSTOCK_POLICY_PATH", "/var/lib/smartstock/policy.json")
    api_key: str = os.getenv("SMARTSTOCK_API_KEY", "")
    max_items_per_request: int = _i("SMARTSTOCK_MAX_ITEMS", 50_000)
    default_lead_days: float = _f("SMARTSTOCK_DEFAULT_LEAD_DAYS", 7.0)
    default_lead_cv: float = _f("SMARTSTOCK_DEFAULT_LEAD_CV", 0.35)
    max_fit_generations: int = _i("SMARTSTOCK_MAX_GENERATIONS", 300)
    job_ttl_seconds: int = _i("SMARTSTOCK_JOB_TTL", 86_400)
    log_level: str = os.getenv("SMARTSTOCK_LOG_LEVEL", "INFO")


settings = Settings()
