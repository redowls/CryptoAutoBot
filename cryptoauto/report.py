"""Performance reporting for the closed book (IMP-B02).

Every win rate printed here travels with the stop-exit doctrine's true win rate
beside it -- see :mod:`cryptoauto.doctrine`. Reporting a bare ``pnl > 0`` figure
is the failure mode this module exists to prevent.

    python -m cryptoauto.report            # whole book
    python -m cryptoauto.report --last 10  # trailing window
"""
import argparse
import json
from datetime import datetime, timezone

from . import doctrine, ledger


def performance(trades):
    """Headline economics for a run of closed trades.

    ``profit_factor`` and ``payoff`` are None when undefined (no losers, or an
    empty book) rather than infinity, so callers must decide how to show it.
    """
    trades = list(trades)
    n = len(trades)
    net = [t.get("pnl", 0.0) for t in trades]
    gross = [t.get("pnl_gross", t.get("pnl", 0.0)) for t in trades]
    fees = sum(t.get("fees", 0.0) for t in trades)
    net_total, gross_total = sum(net), sum(gross)

    wins = [p for p in net if p > 0]
    losses = [p for p in net if p <= 0]
    gross_won, gross_lost = sum(wins), abs(sum(losses))

    ranked = sorted(net, reverse=True)
    out = {
        "trades": n,
        "net_pnl": round(net_total, 2),
        "gross_pnl": round(gross_total, 2),
        "fees": round(fees, 2),
        "fee_drag_pct": (fees / gross_total * 100) if gross_total else 0.0,
        "profit_factor": (gross_won / gross_lost) if gross_lost else None,
        "payoff": ((gross_won / len(wins)) / (gross_lost / len(losses)))
                  if wins and losses and gross_lost else None,
        "expectancy": (net_total / n) if n else 0.0,
        "top1_share_pct": (ranked[0] / net_total * 100) if n and net_total else 0.0,
        "top5_share_pct": (sum(ranked[:5]) / net_total * 100) if n and net_total else 0.0,
        "without_top3": round(net_total - sum(ranked[:3]), 2) if n else 0.0,
        "doctrine": doctrine.summarize(doctrine.verdicts_for(trades)),
    }
    return out


def _pct(v):
    return "n/a" if v is None else f"{v:.2f}"


def render(trades):
    """The text block: economics, then the doctrine, then the fragility check."""
    trades = list(trades)
    if not trades:
        return "CryptoAutoBot — no closed trades to report."
    p = performance(trades)
    d = p["doctrine"]
    lines = [
        f"CryptoAutoBot — {p['trades']} closed trades",
        "",
        f"net P&L      ${p['net_pnl']:+,.2f}   (gross ${p['gross_pnl']:+,.2f} "
        f"− fees ${p['fees']:,.2f} = {p['fee_drag_pct']:.1f}% drag)",
        f"profit factor {_pct(p['profit_factor'])}    payoff {_pct(p['payoff'])}"
        f"    expectancy ${p['expectancy']:+,.2f}/trade",
        "",
        doctrine.format_stop_exits(d),
        "",
        f"⚠️  concentration: top 1 = {p['top1_share_pct']:.0f}% of profit, "
        f"top 5 = {p['top5_share_pct']:.0f}%; "
        f"without the top 3 the book made ${p['without_top3']:+,.2f}",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--last", type=int, default=None,
                    help="report only the N most recent closed trades")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    closed = ledger.load()["closed"]
    if args.last:
        closed = closed[-args.last:]

    if args.json:
        p = performance(closed)
        d = p.pop("doctrine")
        p["stop_rate"] = d.stop_rate
        p["true_win_rate"] = d.true_win_rate
        p["headline_win_rate"] = d.headline_win_rate
        p["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(json.dumps(p, indent=2))
    else:
        print(render(closed))


if __name__ == "__main__":
    main()
