"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "cracktrade"


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Repository root."""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def src_root() -> Path:
    """Root of the importable package, for source-scanning conformance tests."""
    return SRC_ROOT
