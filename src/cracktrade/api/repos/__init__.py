"""Repositories -- one per table, plus the two overview views.

SQL lives here and nowhere above. Rows come back as frozen dataclasses; nothing in this layer
imports the engine or pydantic, so the storage shape and the wire shape stay independent.
"""

from __future__ import annotations
