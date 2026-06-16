# CryptoAutoBot

Automated crypto analysis. Hourly it captures Alpaca OHLCV + indicator snapshots
(`data/snapshots/`); once daily a Claude routine analyzes the day, writes
`memory/`, and sends a Telegram digest. No trading in Phase A — analysis only.
Phase B (paper-trading bot) is designed separately once ~1 month of data exists.

## Universe
BTC ETH SOL XRP DOGE AVAX LINK DOT LTC UNI (Alpaca-tradable)

## Run manually
    .venv/bin/python -m cryptoauto.snapshot   # hourly capture
    .venv/bin/python -m cryptoauto.digest     # print today's summary JSON

## Tests
    .venv/bin/python -m pytest -q

## Schedule (UTC)
- `5 * * * *`  hourly snapshot
- `0 17 * * *` daily Claude digest (= 00:00 WIB) via claude-routines/run-routine.sh cryptoauto-daily
