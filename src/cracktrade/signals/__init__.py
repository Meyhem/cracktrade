"""Signals: the expression grammar, its evaluator, and causal alignment."""

from __future__ import annotations

from cracktrade.signals.alignment import causal_shift
from cracktrade.signals.evaluate import EvaluatedSignal, align_signal, evaluate, prepare_signal
from cracktrade.signals.grammar import SignalExpression, parse_expression
from cracktrade.signals.validation import available_names, validate_signals
from cracktrade.strategy import register_validator

register_validator(validate_signals)

__all__ = [
    "EvaluatedSignal",
    "SignalExpression",
    "align_signal",
    "available_names",
    "causal_shift",
    "evaluate",
    "parse_expression",
    "prepare_signal",
    "validate_signals",
]
