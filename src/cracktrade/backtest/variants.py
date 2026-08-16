"""Expanding a strategy into the independent simulations it describes.

Normative reference: ``docs/ENGINE_SPEC.md`` section 7.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    from cracktrade.config import EntryVariant, ExitVariant, Strategy

#: Integer bar positions at which positions were opened.
type BarPositions = npt.NDArray[np.int64]


@dataclass(frozen=True, slots=True)
class VariantPair:
    """One entry variant crossed with one exit variant."""

    entry: EntryVariant
    exit: ExitVariant

    @property
    def label(self) -> str:
        """How this pair is named in output."""
        return f"{self.entry.name} / {self.exit.name}"


def expand_variants(strategy: Strategy) -> tuple[VariantPair, ...]:
    """The cartesian product of entry and exit variants.

    ``E`` entries by ``X`` exits gives ``E*X`` independent simulations, each reported
    separately.
    """
    return tuple(
        VariantPair(entry=entry, exit=exit_)
        for entry in strategy.entry_variants
        for exit_ in strategy.exit_variants
    )
