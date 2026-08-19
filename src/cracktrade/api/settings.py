"""Settings for the API server and the worker.

Separate from :class:`cracktrade.settings.Settings`, which configures the *engine*. These are
process settings for the interface layer: where the database is, what to bind, how long a
worker lease survives. Nothing here influences a computed result.

Read from the environment with the ``CRACKTRADE_API_`` prefix, or from a ``.env`` file.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from cracktrade.authoring.agent import DEFAULT_MODEL, DEFAULT_TIMEOUT_SECONDS
from cracktrade.authoring.generate import DEFAULT_MAX_ATTEMPTS

#: Default connection string. Matches the credentials in ``docker-compose.yml`` so that a
#: freshly composed development database needs no configuration at all.
DEFAULT_DATABASE_URL = "postgresql://cracktrade:cracktrade@localhost:5432/cracktrade"


class ApiSettings(BaseSettings):
    """Process-level settings for the HTTP interface and its worker."""

    model_config = SettingsConfigDict(
        env_prefix="CRACKTRADE_API_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: libpq connection string for the application database.
    database_url: str = DEFAULT_DATABASE_URL

    #: Interface the server binds to. Loopback by default: there is no authentication
    #: (spec section 15.4, D-5), so the server must not be reachable off the machine by
    #: accident.
    host: str = "127.0.0.1"
    port: int = Field(default=8000, gt=0, lt=65536)

    #: Connection-pool bounds for the API process.
    pool_min_size: int = Field(default=1, ge=0)
    pool_max_size: int = Field(default=10, ge=1)

    #: How long a request waits for a connection before failing. A request that cannot get one
    #: should say so quickly rather than hang until the client gives up, leaving a request in
    #: flight nobody is waiting on.
    pool_timeout_seconds: float = Field(default=10.0, gt=0)

    #: How often a worker refreshes the lease on the run it is executing.
    worker_heartbeat_seconds: float = Field(default=10.0, gt=0)

    #: A claimed run whose heartbeat is older than this is treated as abandoned and failed
    #: honestly rather than re-queued -- re-running would refetch data and measure something
    #: else (spec section 14.5).
    worker_lease_seconds: float = Field(default=60.0, gt=0)

    #: Idle sleep between queue polls when there is nothing to claim.
    worker_poll_seconds: float = Field(default=1.0, gt=0)

    #: Whether ``POST /config/generate`` will draft a strategy from a description. On by
    #: default because the machinery it needs -- the ``claude`` CLI, signed in -- is machinery
    #: the user of this engine already has; off is for a deployment that does not want the
    #: server spawning anything.
    generate_enabled: bool = True

    #: The model that drafts strategy files. Read from the engine's own default rather than
    #: restated, so there is one place a model choice is made.
    generate_model: str = DEFAULT_MODEL

    #: How long one draft may take. See :data:`~cracktrade.authoring.agent.DEFAULT_TIMEOUT_SECONDS`.
    generate_timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)

    #: How many drafts one request may cost. Every attempt is a paid model call against the
    #: user's own subscription, so the ceiling is a setting rather than a constant they would
    #: have to edit the source to change.
    generate_max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1, le=10)


def load_api_settings() -> ApiSettings:
    """Build API settings from the environment."""
    return ApiSettings()
