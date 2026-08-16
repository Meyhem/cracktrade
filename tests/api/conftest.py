"""Fixtures for the API layer's tests."""

from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from cracktrade.api.settings import ApiSettings


class IsolatedApiSettings(ApiSettings):
    """``ApiSettings`` that ignores any ``.env`` file in the working directory.

    A developer's local ``.env`` legitimately points at their own database. Reading it here
    would make the defaults tests pass or fail depending on whose machine ran them, so the
    tests read the environment only -- which they control through ``monkeypatch``.

    Subclassing rather than passing ``_env_file=None``: the private keyword is untyped, and
    strict mypy is right to reject it.
    """

    model_config = SettingsConfigDict(
        env_prefix="CRACKTRADE_API_",
        env_file=None,
        extra="ignore",
    )
