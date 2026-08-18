"""Process-level settings.

Read from the environment with the ``CRACKTRADE_`` prefix, or from a ``.env`` file. Nothing in
the engine reads ``os.environ`` directly; everything arrives through here so that the API
interface can construct settings per request instead of per process.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Trading days per year. Used for annualising returns and for converting the annual
#: risk-free rate to a per-period rate. Daily bars only -- see spec section 7.5.
TRADING_DAYS_PER_YEAR = 252


class LLMSettings(BaseSettings):
    """OpenAI-compatible endpoint used by strategy generation (a later phase).

    Deliberately provider-agnostic: any endpoint speaking the OpenAI chat-completions protocol
    works by pointing ``base_url`` at it.
    """

    model_config = SettingsConfigDict(env_prefix="CRACKTRADE_LLM_", extra="ignore")

    base_url: str = "https://api.openai.com/v1"
    api_key: SecretStr | None = None
    model: str = "gpt-4o-mini"
    timeout_seconds: float = 120.0


class Settings(BaseSettings):
    """Engine-wide settings."""

    model_config = SettingsConfigDict(
        env_prefix="CRACKTRADE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: Seed for every stochastic component. Mandatory for reproducibility -- defect D12.
    seed: int = 0

    #: Worker processes for the optimizer and the evolution search. ``-1`` means "all cores".
    #: Results are identical regardless of this value: differential evolution runs with
    #: ``updating='deferred'`` (spec section 9.2), and the genetic algorithm breeds each
    #: generation in full before scoring any of it (spec section 16.4).
    workers: int = -1

    #: Fraction of history reserved for the out-of-sample test window (spec section 9.4).
    train_fraction: float = Field(default=0.8, gt=0.5, lt=1.0)

    #: Bars required beyond the longest indicator warm-up before a window is considered usable.
    min_bars_beyond_warmup: int = 30

    #: Largest share of forward-filled bars the engine will backtest on. A filled bar
    #: duplicates the previous session's whole row: zero close-to-close return, plus a
    #: high/low range copied from a date it did not belong to, which can trigger a stop that
    #: never happened. Past a small fraction the history is invented rather than repaired
    #: (audit finding A3).
    max_filled_fraction: float = Field(default=0.01, ge=0.0, le=1.0)

    #: Market-data cache. Off by default: this project stores nothing unless asked.
    cache_enabled: bool = False
    cache_dir: Path = Path(".cracktrade-cache")

    llm: LLMSettings = Field(default_factory=LLMSettings)


def load_settings() -> Settings:
    """Build settings from the environment."""
    return Settings()
