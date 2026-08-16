"""Transactional workflows.

Where the rules of ``docs/API.md`` live: optimistic saves, restore-as-append, promotion,
diffing, run launching. A service owns one unit of work, composes repositories inside it, and
translates engine exceptions into :mod:`cracktrade.api.errors`.

This layer and the worker are the only callers of the engine.
"""

from __future__ import annotations
