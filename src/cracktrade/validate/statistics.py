"""Statistics that say whether a result is distinguishable from noise.

Normative reference: ``docs/ENGINE_SPEC.md`` sections 12.3, 12.4 and 12.6.

The engine spends four layers making look-ahead bias inexpressible. Nothing until now addressed
**selection** bias, which is the failure mode that actually costs money in an optimizer: the
maximum Sharpe over ``N`` independent trials on *pure noise* is inflated by roughly
``sqrt(2 * ln N)`` standard errors -- about 2.9 at ``N = 5000``. A search with no edge whatever
produces an impressive-looking optimum as a matter of arithmetic.

Everything here consumes data the engine already produces. None of it needs new market data.

References, both implemented from the published formulations rather than from a library:

* Bailey & Lopez de Prado (2014), *The Deflated Sharpe Ratio*.
* Bailey, Borwein, Lopez de Prado & Zhu (2014), *The Probability of Backtest Overfitting*.
"""

from __future__ import annotations

import math
from itertools import combinations
from statistics import NormalDist
from typing import TYPE_CHECKING

import numpy as np

from cracktrade.domain import DeflatedSharpe, Interval, OverfittingProbability

if TYPE_CHECKING:
    import numpy.typing as npt

#: Euler-Mascheroni constant, from the expected-maximum-of-N-Gaussians expansion.
_EULER_MASCHERONI = 0.5772156649015329

_NORMAL = NormalDist()

#: Pearson kurtosis of a normal distribution. Used when a sample is too short to estimate it.
_NORMAL_KURTOSIS = 3.0


def per_period_sharpe(returns: npt.NDArray[np.float64]) -> float:
    """Sharpe ratio on the sampling frequency of ``returns``, not annualised.

    The deflation formula mixes the Sharpe with the observation count, so the two have to be on
    the same footing. Annualising one and not the other silently scales the statistic.
    """
    if returns.size < 2:
        return 0.0
    deviation = float(np.std(returns, ddof=1))
    if deviation == 0.0:
        return 0.0
    return float(np.mean(returns)) / deviation


def expected_maximum_sharpe(trials: int, variance: float) -> float:
    """The Sharpe a search of ``trials`` candidates would produce from no edge at all.

    The expected maximum of ``N`` independent standard normals, scaled by the dispersion of the
    trial Sharpes. This is the whole point: with enough attempts, an impressive number appears
    whether or not anything is there, so the observed value has to be compared against *this*
    rather than against zero.
    """
    if trials <= 1 or variance <= 0:
        return 0.0
    upper = _NORMAL.inv_cdf(1.0 - 1.0 / trials)
    lower = _NORMAL.inv_cdf(1.0 - 1.0 / (trials * math.e))
    return math.sqrt(variance) * ((1.0 - _EULER_MASCHERONI) * upper + _EULER_MASCHERONI * lower)


def deflated_sharpe(
    returns: npt.NDArray[np.float64],
    *,
    trials: int,
    trial_sharpes: npt.NDArray[np.float64] | None = None,
) -> DeflatedSharpe:
    """Deflate a Sharpe ratio for the number of trials that produced it.

    Args:
        returns: per-bar returns of the reported strategy.
        trials: configurations scored during the search. Understating this understates the
            deflation, so it counts variant selection too.
        trial_sharpes: the Sharpe of every candidate, when available. Their variance is the
            right measure of how widely the search ranged. A parallel search cannot report
            them, and the estimator variance is used instead.
    """
    observations = int(returns.size)
    observed = per_period_sharpe(returns)

    if observations < 3:
        return DeflatedSharpe(observed, 0.0, 0.0, trials, observations, variance_estimated=True)

    estimated = trial_sharpes is None or trial_sharpes.size < 2
    variance = (
        _estimator_variance(observed, observations)
        if trial_sharpes is None or estimated
        else float(np.var(trial_sharpes, ddof=1))
    )
    threshold = expected_maximum_sharpe(trials, variance)

    skew = _skew(returns)
    kurtosis = _kurtosis(returns)

    # Bailey & Lopez de Prado: the denominator corrects for a returns distribution that is not
    # normal. Negative skew and fat tails both make a given Sharpe less trustworthy, and a
    # trading strategy with stops has both.
    variance_term = 1.0 - skew * observed + (kurtosis - 1.0) / 4.0 * observed**2
    if variance_term <= 0:
        # Degenerate: the correction is only valid while this stays positive.
        return DeflatedSharpe(observed, threshold, 0.0, trials, observations, estimated)

    statistic = (observed - threshold) * math.sqrt(observations - 1) / math.sqrt(variance_term)
    return DeflatedSharpe(
        observed=observed,
        threshold=threshold,
        probability=_NORMAL.cdf(statistic),
        trials=trials,
        observations=observations,
        variance_estimated=estimated,
    )


def _estimator_variance(sharpe: float, observations: int) -> float:
    """Variance of a Sharpe estimate under normal returns.

    Fallback for when the per-trial Sharpes are not available. It measures the uncertainty in
    *one* estimate rather than the spread across trials, so it is usually the smaller of the
    two and therefore the more conservative deflation. Flagged in the result either way.
    """
    if observations < 2:
        return 0.0
    return (1.0 + 0.5 * sharpe**2) / (observations - 1)


def _skew(values: npt.NDArray[np.float64]) -> float:
    deviation = float(np.std(values, ddof=0))
    if deviation == 0.0:
        return 0.0
    return float(np.mean(((values - np.mean(values)) / deviation) ** 3))


def _kurtosis(values: npt.NDArray[np.float64]) -> float:
    """Pearson (non-excess) kurtosis. A normal sample gives 3."""
    deviation = float(np.std(values, ddof=0))
    if deviation == 0.0:
        return _NORMAL_KURTOSIS
    return float(np.mean(((values - np.mean(values)) / deviation) ** 4))


def probability_of_backtest_overfitting(
    performance: npt.NDArray[np.float64],
) -> OverfittingProbability:
    """CSCV over a slices-by-configurations performance matrix.

    ``performance[s, n]`` is configuration ``n``'s result on time slice ``s``. The slices are
    split every possible way into equal halves; on each split the configuration that ranked best
    on the training half has its rank measured on the testing half. If selection carried real
    information the winner would usually stay near the top; if it were fitting noise it would
    land anywhere, and below the median about half the time.

    Args:
        performance: a 2-D array with at least four slices and two configurations.
    """
    slices, configurations = performance.shape
    if slices < 4 or configurations < 2:
        return OverfittingProbability(probability=0.0, combinations=0, median_logit=0.0)

    half = slices // 2
    logits: list[float] = []

    for train_slices in combinations(range(slices), half):
        test_slices = tuple(index for index in range(slices) if index not in train_slices)

        train_score = performance[list(train_slices)].mean(axis=0)
        test_score = performance[list(test_slices)].mean(axis=0)

        best = int(np.argmax(train_score))
        # Relative rank of the in-sample winner among all configurations, out of sample.
        rank = float((test_score <= test_score[best]).sum()) / (configurations + 1)
        rank = min(max(rank, 1e-9), 1 - 1e-9)
        logits.append(math.log(rank / (1.0 - rank)))

    if not logits:
        return OverfittingProbability(probability=0.0, combinations=0, median_logit=0.0)

    below = sum(1 for value in logits if value <= 0)
    return OverfittingProbability(
        probability=below / len(logits),
        combinations=len(logits),
        median_logit=float(np.median(logits)),
    )


def block_bootstrap_interval(
    returns: npt.NDArray[np.float64],
    *,
    statistic: str = "mean",
    resamples: int = 2000,
    block_size: int | None = None,
    confidence: float = 0.95,
    seed: int = 0,
) -> Interval:
    """A confidence interval for a return statistic, by stationary block bootstrap.

    Blocks rather than individual observations, because trading returns are serially dependent:
    positions persist across bars, and resampling bar by bar would destroy that structure and
    produce an interval far too narrow to be honest.

    Args:
        returns: per-bar returns.
        statistic: ``"mean"`` for mean per-bar return, ``"total"`` for compounded total return.
        resamples: bootstrap replications.
        block_size: bars per block. Defaults to roughly ``n ** (1/3)``, the usual rule of thumb.
        confidence: interval width.
        seed: for reproducibility, like everything else in the engine.
    """
    observations = returns.size
    if observations < 2:
        return Interval(point=0.0, low=0.0, high=0.0, confidence=confidence)

    length = block_size or max(1, round(observations ** (1.0 / 3.0)))
    blocks = math.ceil(observations / length)
    rng = np.random.default_rng(seed)

    starts = rng.integers(0, observations, size=(resamples, blocks))
    offsets = np.arange(length)
    # Wrap around the end so every bar is equally likely to be sampled, which is what makes the
    # bootstrap "stationary" rather than biased against the tail of the series.
    indices = (starts[:, :, None] + offsets[None, None, :]) % observations
    samples = returns[indices.reshape(resamples, -1)[:, :observations]]

    values = samples.mean(axis=1) if statistic == "mean" else np.prod(1.0 + samples, axis=1) - 1.0

    tail = (1.0 - confidence) / 2.0
    point = float(np.mean(returns)) if statistic == "mean" else float(np.prod(1.0 + returns) - 1.0)
    return Interval(
        point=point,
        low=float(np.quantile(values, tail)),
        high=float(np.quantile(values, 1.0 - tail)),
        confidence=confidence,
    )
