"""HTTP routes.

Deliberately thin: parse a DTO, call one service, render a DTO. A route that branches on
domain state, touches a repository, or calls the engine is in the wrong layer -- routes are the
one place with no decisions in them.
"""

from __future__ import annotations
