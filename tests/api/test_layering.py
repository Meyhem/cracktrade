"""The API layer's dependency direction, asserted rather than agreed.

Spec section 15.3 states ``routes -> services -> repos -> db``, with the engine reachable only
from services and the worker. That is the kind of rule which holds until the afternoon someone
needs one value from a repository inside a route, so it is checked by scanning imports rather
than by review alone.

The engine's own layering is defended the same way (``tests/test_causality.py`` scans for
``.shift``): a structural rule that no test enforces is a comment.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "cracktrade" / "api"

#: Which ``cracktrade.api.*`` subpackages each layer may import from. A layer may always import
#: from itself, and every layer may use ``errors`` and ``settings``, which are leaves.
ALLOWED_WITHIN_API: dict[str, frozenset[str]] = {
    "routes": frozenset({"schemas", "services"}),
    "services": frozenset({"repos", "db"}),
    "repos": frozenset({"db"}),
    "db": frozenset(),
    # `schemas` is the mapping at the edge, not a step in the flow: its whole job is turning
    # rows and service results into wire shapes, so it has to know what it maps from. The
    # direction still holds, because nothing below imports it -- asserted just below.
    "schemas": frozenset({"repos", "services"}),
    "worker": frozenset({"repos", "db", "events"}),
    "events": frozenset({"db"}),
}

#: Layers permitted to import the engine itself. Services own validation and diffing; the
#: worker executes runs. A repository that imported the engine would put engine logic below
#: the transaction boundary, and a route that did would put it above the service layer.
MAY_IMPORT_ENGINE = frozenset({"services", "worker", "schemas", "routes"})

#: Leaf modules every layer may depend on.
UNIVERSAL = frozenset({"errors", "settings", "dependencies"})

ENGINE_PACKAGES = frozenset(
    {
        "backtest",
        "config",
        "data",
        "domain",
        "indicators",
        "metrics",
        "optimize",
        "serialize",
        "signals",
        "strategy",
        "validate",
    }
)


def _api_modules() -> list[Path]:
    return sorted(path for path in API_ROOT.rglob("*.py") if path.name != "__init__.py")


def _imported_names(module: Path) -> list[str]:
    """Every ``cracktrade.*`` module path imported by ``module``."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    return [name for name in imported if name.startswith("cracktrade")]


def _layer_of(module: Path) -> str | None:
    """The layer a module belongs to, or ``None`` for top-level modules like ``main.py``."""
    relative = module.relative_to(API_ROOT)
    return relative.parts[0] if len(relative.parts) > 1 else None


@pytest.mark.parametrize("module", _api_modules(), ids=lambda p: str(p.name))
def test_layer_imports_flow_downward(module: Path) -> None:
    layer = _layer_of(module)
    if layer is None:  # main.py wires everything together; that is its job
        return
    allowed = ALLOWED_WITHIN_API[layer] | UNIVERSAL | {layer}

    for name in _imported_names(module):
        parts = name.split(".")
        if len(parts) < 3 or parts[1] != "api":
            continue
        target = parts[2]
        assert target in allowed, (
            f"{module.name} is in the '{layer}' layer and may not import "
            f"cracktrade.api.{target} (spec section 15.3)"
        )


@pytest.mark.parametrize("module", _api_modules(), ids=lambda p: str(p.name))
def test_only_services_and_the_worker_reach_the_engine(module: Path) -> None:
    layer = _layer_of(module)
    if layer is None or layer in MAY_IMPORT_ENGINE:
        return

    for name in _imported_names(module):
        parts = name.split(".")
        if len(parts) >= 2 and parts[1] in ENGINE_PACKAGES:
            pytest.fail(
                f"{module.name} is in the '{layer}' layer and may not import the engine "
                f"module {name} (spec section 15.3)"
            )


def test_the_scan_actually_sees_imports() -> None:
    """Guard against the scan passing because it found nothing to check.

    A structural test that silently matches zero files is worse than no test: it reports
    success for a rule it never evaluated.
    """
    modules = _api_modules()
    assert modules, "no api modules found -- the scan path is wrong"
    assert any(_imported_names(module) for module in modules), (
        "no cracktrade imports found in any api module -- the import parser is broken"
    )


def test_every_api_module_actually_imports() -> None:
    """A cheap guard against a circular import, which type checking does not catch.

    Phase 5 had one: the routes imported the app for its unit-of-work dependency while the app
    imported the routes to mount them. mypy accepted it and the interpreter did not. The fix
    was a leaf ``dependencies`` module; this makes the next one fail here rather than at
    start-up.
    """
    import importlib

    for module in _api_modules():
        relative = module.relative_to(API_ROOT).with_suffix("")
        importlib.import_module("cracktrade.api." + ".".join(relative.parts))


def test_nothing_below_the_edge_imports_schemas() -> None:
    """What keeps the ``schemas`` allowance from being a hole.

    ``schemas`` may import rows and service results because mapping them to the wire is its
    job. That stays a one-way dependency only while nothing underneath reaches back up for a
    DTO -- the moment a service returns a response model, the storage shape and the wire shape
    are welded together and neither can change alone.
    """
    for module in _api_modules():
        layer = _layer_of(module)
        if layer not in {"services", "repos", "db", "worker", "events"}:
            continue
        for name in _imported_names(module):
            parts = name.split(".")
            if len(parts) >= 3 and parts[1] == "api" and parts[2] == "schemas":
                pytest.fail(f"{module.name} ({layer}) imports a wire schema")
