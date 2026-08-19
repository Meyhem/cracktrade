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


class ConfigDiffRequest(Body):
    """Two configurations to compare, each in either form.

    Deliberately not addressed by version. The version-to-version diff answers "what did I
    change"; this answers "what am I about to adopt", where one side is a configuration a
    search produced and has no version at all until the user promotes it.
    """

    from_: ValidateRequest = Field(alias="from")
    to: ValidateRequest

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CreateStrategyRequest(Body):
    """The New-strategy dialog."""

    name: str = Field(min_length=1, max_length=200)
    ticker: str = Field(min_length=1, max_length=20)
    start_date: str
    end_date: str
    #: Bar width. Not validated here -- the engine owns which values exist (spec 3.3) and
    #: rejects the rest with a message addressed to ``universe.interval``, which is the message
    #: the editor already knows how to render. A pattern here would duplicate that list and
    #: answer with a schema error instead.
    interval: str = "1d"
    #: "minimal" seeds one indicator pair, an entry and an exit; "empty" the bare minimum.
    seed: str = Field(default="minimal", pattern="^(minimal|empty)$")


class ImportStrategyRequest(Body):
    """An uploaded configuration file, stored as written."""

    yaml: str = Field(min_length=1)
    filename: str | None = None


class GenerateConfigRequest(Body):
    """A description of a strategy to write, or a change to make to one.

    ``base_yaml`` is what separates writing from revising. Present, the draft is a change to
    the file supplied and is shown to the user as a diff against it; absent, it is a new
    configuration. The server holds no session between calls, so a refinement -- "now use ATR
    stops" -- is this same request carrying the previous draft back.

    The instruction is capped generously rather than tightly. It is prose from a person
    describing a trading idea, and the failure this bound guards against is a client pasting a
    file into the wrong field, not a user writing three careful paragraphs.
    """

    instruction: str = Field(min_length=1, max_length=8000)
    base_yaml: str | None = Field(default=None, max_length=100_000)


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
