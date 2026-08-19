"""Writing a strategy file from a description of what it should do.

A user who knows what they want to trade but not how to spell it in this schema is otherwise
stuck: the schema is small but exact, the signal grammar is deliberately narrower than Python,
and the indicator catalogue is sixty-odd entries deep. This package turns an instruction --
"buy NVDA pullbacks in an uptrend, get out on a trailing stop" -- into a configuration the
engine accepts, or reports honestly that it could not.

Two rules shape everything here:

* **Nothing is created.** A proposal is text plus its validation verdict. Whether it becomes a
  strategy is the caller's decision, and in the interfaces it is the user's.
* **The engine is the judge.** The model does not decide whether its own output is valid; a
  :class:`Reviewer` supplied by the caller does, and the loop retries against its errors. That
  is why the validity reported on a proposal is worth reading.
"""

from cracktrade.authoring.brief import build_brief
from cracktrade.authoring.generate import (
    Critique,
    Draft,
    Drafter,
    Proposal,
    Request,
    Reviewer,
    propose,
)

__all__ = [
    "Critique",
    "Draft",
    "Drafter",
    "Proposal",
    "Request",
    "Reviewer",
    "build_brief",
    "propose",
]
