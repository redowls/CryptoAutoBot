"""One-time migration: restate historical closed trades net of exchange fees.

Until 2026-09-02 ``ledger.close_position`` recorded ``pnl`` as the raw price
difference, so every closed row -- and every metric built on them -- was gross.
Reconciling the paper account exposed the gap: ledger-implied equity ran
$463.48 ahead of the broker across $112,577.94 of entries, which is 0.206% per
side, i.e. the fee bill.

This rewrites each pre-migration row as::

    pnl_gross = (exit - entry) * qty      # what the row used to call "pnl"
    fees      = FEE_RATE * (entry + exit) * qty
    pnl       = pnl_gross - fees          # what it should always have meant

Idempotent: rows that already carry ``fees`` are left alone. Historical rows
have no ``initial_stop`` (it was never persisted), so they stay ``None`` and the
doctrine scores them by exit reason rather than in R.

Usage:  python -m scripts.backfill_fees [--apply]
Without --apply it prints the restatement and writes nothing.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptoauto import config  # noqa: E402


def restate(trade, fee_rate):
    """Return a copy of `trade` with gross/fees/net split out. Idempotent."""
    if "fees" in trade:
        return dict(trade), 0.0
    qty, entry, exit_ = trade["qty"], trade["entry_price"], trade["exit_price"]
    gross = (exit_ - entry) * qty
    fees = fee_rate * (entry * qty + exit_ * qty)
    out = dict(trade)
    out["pnl_gross"] = round(gross, 2)
    out["fees"] = round(fees, 2)
    out["pnl"] = round(gross - fees, 2)
    out.setdefault("initial_stop", None)
    out.setdefault("stop", None)
    return out, fees


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    ap.add_argument("--path", default=None, help="ledger path (default: config.TRADES_DIR)")
    args = ap.parse_args()

    path = Path(args.path) if args.path else config.TRADES_DIR / "trades.json"
    led = json.loads(path.read_text())

    before = sum(t["pnl"] for t in led["closed"])
    restated, total_fees, touched = [], 0.0, 0
    for t in led["closed"]:
        new, fees = restate(t, config.FEE_RATE)
        if fees:
            touched += 1
        total_fees += fees
        restated.append(new)
    after = sum(t["pnl"] for t in restated)

    print(f"ledger              : {path}")
    print(f"closed trades       : {len(restated)}  ({touched} restated, "
          f"{len(restated) - touched} already had fees)")
    print(f"fee rate            : {config.FEE_RATE:.4%} per side")
    print(f"realized P&L before : ${before:+,.2f}  (gross)")
    print(f"fees charged        : ${total_fees:,.2f}")
    print(f"realized P&L after  : ${after:+,.2f}  (net)")

    wins_before = sum(1 for t in led["closed"] if t["pnl"] > 0)
    wins_after = sum(1 for t in restated if t["pnl"] > 0)
    print(f"trades in profit    : {wins_before} -> {wins_after}"
          f"  (fees flipped {wins_before - wins_after})")

    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply.")
        return

    backup = path.with_suffix(".json.pre-fees.bak")
    shutil.copy2(path, backup)
    led["closed"] = restated
    path.write_text(json.dumps(led, indent=2))
    print(f"\nwritten. backup at {backup}")


if __name__ == "__main__":
    main()
