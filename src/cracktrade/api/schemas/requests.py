"""Request bodies.

Pydantic exists here to reject a malformed *request* -- a missing field, a wrong type -- and
nowhere else. Whether a configuration is a valid strategy is the engine's judgement, not a
schema's, so these models deliberately accept ``dict`` for a config and pass it down.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Body(BaseModel):
    """Base: unknown keys are rejected rather than ignored.

    The same rule the engine applies to config files (spec section 3.1). A request that sets a
    field this version does not have is far more likely to be a client expecting behaviour that
    does not exist than a harmless extra.
    """

    model_config = ConfigDict(extra="forbid")


class ValidateRequest(Body):
    """A configuration to check, in either form. Exactly one must be supplied."""

    config: dict[str, Any] | None = None
    yaml: str | None = None


class CreateStrategyRequest(Body):
    """The New-strategy dialog."""

    name: str = Field(min_length=1, max_length=200)
    ticker: str = Field(min_length=1, max_length=20)
    start_date: str
    end_date: str
    #: "minimal" seeds one indicator pair, an entry and an exit; "empty" the bare minimum.
    seed: str = Field(default="minimal", pattern="^(minimal|empty)$")


class ImportStrategyRequest(Body):
    """An uploaded configuration file, stored as written."""

    yaml: str = Field(min_length=1)
    filename: str | None = None


class ForkStrategyRequest(Body):
    """Copy an existing strategy at some version. ``version`` defaults to the head."""

    name: str = Field(min_length=1, max_length=200)
    version: int | None = Field(default=None, ge=1)


class SaveVersionRequest(Body):
    """An edit from the config editor.

    ``base_version`` is what makes the save optimistic: it states which version the edit was
    made against, so a head that moved underneath is a conflict rather than a silent overwrite.
    """

    base_version: int = Field(ge=1)
    config: dict[str, Any] | None = None
    yaml: str | None = None
    note: str | None = Field(default=None, max_length=500)


class RestoreVersionRequest(Body):
    """Append a copy of an older version at the head."""

    note: str | None = Field(default=None, max_length=500)
