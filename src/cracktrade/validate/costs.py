"""Whether the edge survives friction.

Normative reference: ``docs/ENGINE_SPEC.md`` section 12.7.

The cost model is a flat commission plus a fixed slippage fraction. That ignores everything that
makes real slippage vary -- volatility, liquidity, order size, and the fact that filling at the
open is where gaps concentrate. Modelling all of that properly is a large commitment and mostly
guesswork.

Testing *sensitivity* to it is neither. Re-running the headline at two and three times the
configured slippage costs three backtests and answers the question that matters: a strategy whose
edge disappears when costs are doubled is not tradeable, however good the headline looks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cracktrade.backtest import extract_metrics, run_variants
from cracktrade.domain import CostScenario, CostSensitivity
from cracktrade.strategy import build_strategy

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cracktrade.config import Strategy
    from cracktrade.optimize.windows import TestWindow

#: Slippage multiples applied to the configured value.
DEFAULT_MULTIPLES = (1.0, 2.0, 3.0)


def cost_sensitivity(
    strategy: Strategy,
    test: TestWindow,
    *,
    multiples: Sequence[float] = DEFAULT_MULTIPLES,
) -> CostSensitivity:
    """Re-evaluate ``strategy`` on the test window at several slippage levels.

    Only slippage is scaled. Commission is a published, knowable number; slippage is the
    estimate, so it is the one worth stress-testing.
    """
    scenarios: list[CostScenario] = []

    for multiple in multiples:
        config = strategy.model_dump(mode="python")
        config["execution"]["slippage_pct"] = strategy.execution.slippage_pct * multiple
        candidate = build_strategy(config)

        simulation = run_variants(candidate, test.data)[0]
        scenarios.append(
            CostScenario(
                multiple=multiple,
                slippage_pct=candidate.execution.slippage_pct,
                commission_pct=candidate.execution.commission_pct,
                metrics=extract_metrics(
                    simulation.portfolio,
                    risk_free_rate=candidate.execution.risk_free_rate,
                    offset=test.offset,
                ),
            )
        )

    return CostSensitivity(scenarios=tuple(scenarios))
