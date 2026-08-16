"""HTTP interface and persistence -- spec sections 14 and 15.

The second consumer of the library, alongside the CLI. It parses requests, calls the engine or
the database, and renders responses; it owns no engine logic of its own. Every number it
returns is the engine's own, serialised by :mod:`cracktrade.serialize` and passed through
unaltered (spec section 15.1).

Layering, strictly downward and asserted by ``tests/api/test_layering.py``::

    routes -> services -> repos -> db

Routes never import repositories, repositories never import the engine. The engine is called
from ``services`` (validation, diffing, metadata) and from the worker (runs), and nowhere else.
"""

from __future__ import annotations
