"""Which tickers a candidate must survive being moved to -- spec section 19.4 and 19.6.

A strategy evolved on one ticker looks excellent on that ticker almost by construction: the
search chose it for that. The cheapest way to find out whether anything is behind the number is
to render the same genome against a *relative* and run it unchanged. A real intraday effect in
semiconductors survives the move from AMD to MU; a fit to AMD's particular noise does not.

**Families are written by hand and are part of the configuration.** Deriving them from return
correlation is more elegant and would fit an object -- an estimation window, a threshold, a
linkage rule -- inside a system whose entire purpose is to not fit objects. A family that moved
with its estimation window would silently change what a transfer result meant between two runs.
A hand-written map states an economic claim instead, and a reader can disagree with it.

**Every family carries controls**, which are instruments outside its mechanism. Without them a
transfer result cannot distinguish "this works across semiconductors" from "this is long the
market", and those are very different findings.
"""

from __future__ import annotations

from dataclasses import dataclass

from cracktrade.errors import ProspectError


@dataclass(frozen=True, slots=True)
class Family:
    """A ticker's economic relatives, and instruments that are not.

    Attributes:
        name: what the family is, for the report.
        members: tickers sharing the family's economic mechanism. A candidate is expected to
            transfer to these if it is measuring anything real.
        controls: tickers outside that mechanism. A candidate performing as well on these as on
            the members is detecting the market rather than the family, which is why they are
            reported separately and never averaged in with the members.
    """

    name: str
    members: tuple[str, ...]
    controls: tuple[str, ...]

    def siblings_of(self, home: str) -> tuple[str, ...]:
        """Family members other than ``home`` itself."""
        return tuple(ticker for ticker in self.members if ticker != home)


#: Controls for equity families. Gold and long treasuries are the two liquid instruments whose
#: mechanism is furthest from an equity momentum or mean-reversion effect, which is the whole
#: job of a control here.
_EQUITY_CONTROLS = ("GLD", "TLT")

#: Controls for non-equity families: the broad market, which is what a spurious result on a
#: commodity is most likely to actually be tracking.
_MARKET_CONTROLS = ("SPY", "QQQ")

SEMICONDUCTORS = Family(
    name="semiconductors",
    members=("AMD", "MU", "INTC", "NVDA", "AVGO", "SMCI", "SMH", "SOXL"),
    controls=_EQUITY_CONTROLS,
)

CRYPTO_PROXIES = Family(
    name="crypto proxies",
    members=("MSTR", "COIN"),
    controls=_EQUITY_CONTROLS,
)

HIGH_BETA_GROWTH = Family(
    name="high-beta growth",
    members=("PLTR", "AFRM", "RIVN", "UBER", "TSLA"),
    controls=_EQUITY_CONTROLS,
)

MEGACAP_TECH = Family(
    name="megacap tech",
    members=("AAPL", "MSFT", "GOOGL", "AMZN", "META", "NFLX"),
    controls=_EQUITY_CONTROLS,
)

BROAD_INDICES = Family(
    name="broad indices",
    members=("SPY", "QQQ", "IWM", "DIA", "TQQQ", "TNA"),
    controls=("GLD", "USO"),
)

PRECIOUS_METALS = Family(
    name="precious metals",
    members=("GDX", "SLV", "GLD"),
    controls=_MARKET_CONTROLS,
)

ENERGY = Family(
    name="energy",
    members=("XLE", "USO"),
    controls=_MARKET_CONTROLS,
)

#: Controls for European equity families: gold and eurozone government bonds, both listed in
#: euros and trading the same 09:00-17:30 CET session as the members.
#:
#: Deliberately not ``_EQUITY_CONTROLS``. GLD and TLT are more liquid, and a Sharpe is unitless
#: so the comparison would not be nonsense -- but they trade a US afternoon session against a
#: European day, so an intraday candidate would be measured on its members' clock and its
#: controls' clock at once. A control exists to answer "is this just risk appetite?", and it can
#: only answer that if it is exposed to the same hours the candidate traded.
#:
#: ``IBCL.DE`` is the better duration match for TLT -- 15-30 year euro governments against
#: 20-year treasuries -- and is rejected anyway: measured 2026-08-21 it trades a median of 25
#: shares an hour against ``VGEA.DE``'s 1,747, and a control whose price barely moves produces
#: exactly the degenerate statistic section 19.3 has to withhold. Breadth of maturity is worth
#: less here than having a price at all.
_EUROPEAN_CONTROLS = ("4GLD.DE", "VGEA.DE")

# European families are kept separate from their US counterparts rather than merged into them,
# even where the economic claim is plainly the same -- ASML and NVDA are in one industry by any
# reading. The reason is that transfer here is an *intraday* claim and the two trade different
# clocks: measured 2026-08-21, a European session yields 9 hourly bars against a US session's 7,
# which the calendar annualises as 2268 periods a year against 1764. "This effect survives the
# move from ASML to Infineon" and "this effect survives the move from ASML to NVDA" are then
# different statements, and only the first is about the industry rather than about the session.
# Merging them would also make every family's median a blend of two trading days.

EUROPEAN_SEMICONDUCTORS = Family(
    name="european semiconductors",
    members=("ASML.AS", "IFX.DE", "STMPA.PA", "ASM.AS", "BESI.AS"),
    controls=_EUROPEAN_CONTROLS,
)

EUROPEAN_LUXURY = Family(
    name="european luxury",
    members=("MC.PA", "KER.PA", "RMS.PA", "EL.PA", "MONC.MI"),
    controls=_EUROPEAN_CONTROLS,
)

EUROPEAN_BANKS = Family(
    name="european banks",
    members=("BNP.PA", "DBK.DE", "SAN.MC", "UCG.MI", "ISP.MI", "INGA.AS", "GLE.PA", "BBVA.MC"),
    controls=_EUROPEAN_CONTROLS,
)

EUROPEAN_DEFENCE = Family(
    name="european defence",
    members=("RHM.DE", "HO.PA", "LDO.MI", "SAF.PA"),
    controls=_EUROPEAN_CONTROLS,
)

EUROPEAN_SOFTWARE = Family(
    name="european software",
    members=("SAP.DE", "ADYEN.AS", "DSY.PA", "CAP.PA"),
    controls=_EUROPEAN_CONTROLS,
)

EUROPEAN_ENERGY = Family(
    name="european energy",
    members=("TTE.PA", "ENI.MI", "REP.MC"),
    controls=_EUROPEAN_CONTROLS,
)

#: Every family, in a fixed order. Order is not load-bearing the way section 16.2's block order
#: is -- nothing stores a family index -- but a stable order keeps reports diffable.
FAMILIES: tuple[Family, ...] = (
    SEMICONDUCTORS,
    CRYPTO_PROXIES,
    HIGH_BETA_GROWTH,
    MEGACAP_TECH,
    BROAD_INDICES,
    PRECIOUS_METALS,
    ENERGY,
    EUROPEAN_SEMICONDUCTORS,
    EUROPEAN_LUXURY,
    EUROPEAN_BANKS,
    EUROPEAN_DEFENCE,
    EUROPEAN_SOFTWARE,
    EUROPEAN_ENERGY,
)

#: The default prospecting universe, screened per section 19.6 on median intraday range against
#: the configured round trip, with a liquidity floor. Twenty tickers: enough breadth that the
#: sweep is not one instrument's opinion, few enough that a round-robin pass completes in a
#: sitting.
#:
#: Deliberately *not* led by SPY and QQQ. Measured 2026-08-20 they have the lowest range-to-cost
#: ratios in the whole screen -- 10.9 and 15.6 against SOXL's 91.7 -- because a 0.08% round trip
#: consumes a tenth of SPY's entire median day. The instruments most associated with day trading
#: are the ones this engine has least room on.
DEFAULT_UNIVERSE: tuple[str, ...] = (
    "SOXL",
    "MSTR",
    "INTC",
    "COIN",
    "SMCI",
    "MU",
    "AMD",
    "PLTR",
    "TQQQ",
    "AVGO",
    "TSLA",
    "NVDA",
    "AFRM",
    "RIVN",
    "TNA",
    "GDX",
    "UBER",
    "NFLX",
    "SLV",
    "SMH",
)

#: Inverse and volatility instruments, kept in their own bucket and never ranked against the
#: universe above (section 19.5).
#:
#: They are retained rather than dropped because section 7.5 pins ``direction='longonly'`` and
#: short signals are not expressible, so buying one of these is the only bearish position the
#: engine can hold. They are segregated because their buy-and-hold decays structurally --
#: measured over the year to 2026-08-20: SOXS -99.8%, UVXY -73.2%, TZA -64.0%, SQQQ -52.3% --
#: which makes section 8's benchmark check clearable by holding cash. Both facts are true at
#: once and the bucket is how neither is allowed to hide the other.
INVERSE_BUCKET: tuple[str, ...] = ("SOXS", "SQQQ", "TZA", "SPXU", "UVXY")

_BY_TICKER: dict[str, Family] = {ticker: family for family in FAMILIES for ticker in family.members}


def family_of(ticker: str) -> Family:
    """The family ``ticker`` belongs to.

    Raises:
        ProspectError: the ticker is in no family. Transfer is the cheapest rejection the ladder
            has and a candidate that skipped it would be ranked against candidates that did not,
            so an unmapped ticker is refused rather than waved through.
    """
    family = _BY_TICKER.get(ticker)
    if family is None:
        msg = (
            f"{ticker} belongs to no family, so a candidate found on it cannot be transfer-"
            f"tested. Add it to a family in cracktrade.prospect.families, or drop it from the "
            f"universe"
        )
        raise ProspectError(msg)
    return family


def is_inverse(ticker: str) -> bool:
    """Whether ``ticker`` is a structurally decaying instrument (section 19.5)."""
    return ticker in INVERSE_BUCKET


__all__ = [
    "BROAD_INDICES",
    "CRYPTO_PROXIES",
    "DEFAULT_UNIVERSE",
    "ENERGY",
    "EUROPEAN_BANKS",
    "EUROPEAN_DEFENCE",
    "EUROPEAN_ENERGY",
    "EUROPEAN_LUXURY",
    "EUROPEAN_SEMICONDUCTORS",
    "EUROPEAN_SOFTWARE",
    "FAMILIES",
    "HIGH_BETA_GROWTH",
    "INVERSE_BUCKET",
    "MEGACAP_TECH",
    "PRECIOUS_METALS",
    "SEMICONDUCTORS",
    "Family",
    "family_of",
    "is_inverse",
]
