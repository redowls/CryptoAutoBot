"""Stop-exit doctrine accounting (IMP-B02).

The standing rule for this book: **a stop-triggered exit is a FAILED trade
whatever the P&L sign.** A +0.0% break-even stop is not a win. Scoring a trade
as ``pnl > 0`` hides exactly the trades that matter -- it counts a stop that
happened to close green as a success, so a strategy with no demonstrated edge
reports a respectable win rate.

On the live book (35 closed trades, 2026-09-02) the gap is concrete: 17 rows
close in profit, so ``pnl > 0`` reports a 49% win rate. The doctrine's verdict
is 13 WIN against 18 stop-driven exits -- one of which banked +$127.27 and was
being counted as a win.

The port from ``USTradeBot/bot/doctrine.py`` is deliberately simpler here:
CryptoAutoBot writes an unambiguous exit reason (``stop`` / ``tp`` / ``time``),
so there is no broker catch-all row to attribute by fill price.

Anti-gaming: never widen or remove a stop to flatter these numbers. The metric
is designed to indict the entry, not to be tuned.
"""
from dataclasses import dataclass

# profit_R at or below which a stop-driven exit is a FAIL rather than a SCRATCH.
# +0.25R is "the thesis went nowhere": a break-even stop, a scratched trail, or
# a full stop all land here.
FAIL_MAX_R = 0.25
# profit_R at or above which any exit counts as a WIN on its own merits.
WIN_MIN_R = 1.0
# A non-stop exit below this gave back real money: a FAIL, not a SCRATCH.
SCRATCH_MIN_R = -0.25
# A FAIL at or below this took (close to) the original 1R stop -- a "full stop".
# Above it the stop had already ratcheted up: a break-even or scratched trail.
FULL_STOP_MAX_R = -0.75

WIN = "WIN"
SCRATCH = "SCRATCH"
FAIL = "FAIL"

FULL_STOP = "full-stop"
BE_SCRATCH = "BE-scratch"
# Rows written before IMP-B01 never persisted initial_stop, so they cannot be
# scored in R at all -- the doctrine still calls a stop a failure, but it cannot
# say which kind.
UNKNOWN_STOP = "unknown-R"


@dataclass(frozen=True)
class Verdict:
    """How the doctrine scores one closed trade."""

    symbol: str
    bucket: str          # WIN / SCRATCH / FAIL
    profit_r: float | None   # None for pre-IMP-B01 rows with no 1R anchor
    stop_driven: bool
    fail_kind: str       # FULL_STOP / BE_SCRATCH / UNKNOWN_STOP, "" when not a FAIL
    reason: str
    pnl: float

    @property
    def headline_win(self) -> bool:
        """What the old ``pnl > 0`` test would have said. Kept to show the gap."""
        return self.pnl > 0


@dataclass(frozen=True)
class StopExitSummary:
    """The block every review of this book has to report."""

    trades: int
    stops: int           # stop-driven exits, any P&L sign
    wins: int
    scratches: int
    fails: int
    full_stops: int
    be_scratches: int
    unknown_r: int
    headline_wins: int   # pnl > 0

    @property
    def stop_rate(self) -> float:
        return self.stops / self.trades if self.trades else 0.0

    @property
    def true_win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def headline_win_rate(self) -> float:
        return self.headline_wins / self.trades if self.trades else 0.0

    @property
    def fail_scratch_rate(self) -> float:
        """The escalation metric: a high reading indicts the entry, not the exit."""
        return (self.fails + self.scratches) / self.trades if self.trades else 0.0


def profit_r(trade) -> float | None:
    """Return the trade's result in R, or None when the 1R anchor is missing.

    1R is measured from the ORIGINAL stop set at entry, never the trailed one --
    the trail moving up must not shrink the yardstick and flatter the result.
    """
    initial_stop = trade.get("initial_stop")
    entry = trade.get("entry_price")
    if initial_stop is None or entry is None:
        return None
    r = entry - initial_stop
    if r <= 0:
        return None
    return (trade["exit_price"] - entry) / r


def classify(trade) -> Verdict:
    """Bucket one closed trade WIN / SCRATCH / FAIL per the doctrine.

    Deliberately independent of the sign of ``pnl`` -- that is carried only so
    the summary can show how far the headline number drifts from the true one.
    """
    reason = trade.get("reason", "")
    stop_driven = reason == "stop"
    r = profit_r(trade)
    pnl = trade.get("pnl", 0.0)

    if r is None:
        # No 1R anchor: score by the exit reason alone. A stop is still a failure.
        if reason == "tp":
            bucket = WIN
        elif stop_driven:
            bucket = FAIL
        else:
            bucket = SCRATCH if pnl >= 0 else FAIL
        fail_kind = UNKNOWN_STOP if bucket == FAIL else ""
        return Verdict(
            symbol=trade.get("symbol", ""),
            bucket=bucket,
            profit_r=None,
            stop_driven=stop_driven,
            fail_kind=fail_kind,
            reason=reason,
            pnl=pnl,
        )

    if reason == "tp" or r >= WIN_MIN_R:
        bucket = WIN
    elif stop_driven:
        bucket = FAIL if r <= FAIL_MAX_R else SCRATCH
    elif r < SCRATCH_MIN_R:      # a time stop that gave back real money
        bucket = FAIL
    else:
        bucket = SCRATCH

    fail_kind = ""
    if bucket == FAIL:
        fail_kind = FULL_STOP if r <= FULL_STOP_MAX_R else BE_SCRATCH

    return Verdict(
        symbol=trade.get("symbol", ""),
        bucket=bucket,
        profit_r=round(r, 4),
        stop_driven=stop_driven,
        fail_kind=fail_kind,
        reason=reason,
        pnl=pnl,
    )


def verdicts_for(trades) -> list[Verdict]:
    return [classify(t) for t in trades]


def summarize(verdicts) -> StopExitSummary:
    """Roll verdicts up into the block the doctrine requires."""
    verdicts = list(verdicts)
    return StopExitSummary(
        trades=len(verdicts),
        stops=sum(1 for v in verdicts if v.stop_driven),
        wins=sum(1 for v in verdicts if v.bucket == WIN),
        scratches=sum(1 for v in verdicts if v.bucket == SCRATCH),
        fails=sum(1 for v in verdicts if v.bucket == FAIL),
        full_stops=sum(1 for v in verdicts if v.fail_kind == FULL_STOP),
        be_scratches=sum(1 for v in verdicts if v.fail_kind == BE_SCRATCH),
        unknown_r=sum(1 for v in verdicts if v.fail_kind == UNKNOWN_STOP),
        headline_wins=sum(1 for v in verdicts if v.headline_win),
    )


def format_stop_exits(s: StopExitSummary) -> str:
    """The two lines that must travel beside any headline win rate."""
    if not s.trades:
        return "🛑 stop rate: n/a — no closed trades"
    unknown = f" · {s.unknown_r} unscored (pre-fee-fix rows)" if s.unknown_r else ""
    return (
        f"🛑 stop rate: {s.stops}/{s.trades} ({s.stop_rate * 100:.0f}%) — "
        f"FAIL {s.fails} (full {s.full_stops} / BE-scratch {s.be_scratches}) · "
        f"SCRATCH {s.scratches} · WIN {s.wins}{unknown}\n"
        f"✅ true win rate: {s.true_win_rate * 100:.0f}% "
        f"(headline {s.headline_win_rate * 100:.0f}%)"
    )
