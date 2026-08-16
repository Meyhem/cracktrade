"""Request and response DTOs.

Pydantic lives here and nowhere else: it is an edge concern. Below this layer the code passes
frozen dataclasses, as the engine does, so that a serialisation choice cannot leak into a
service or a repository. One module per section of ``docs/API.md``.
"""

from __future__ import annotations
