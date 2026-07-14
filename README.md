# CryptoAutoBot

Automated crypto analysis + paper trading. Hourly it captures Alpaca OHLCV +
indicator snapshots (`data/snapshots/`); once daily a Claude routine analyzes
the day, writes `memory/`, and sends a Telegram digest.

**Phase B (live on paper since 2026-07-14):** an hourly trader whose rules are
distilled from the insights accumulated by Phase A (`memory/insights.md`).
Deterministic engine (BTC regime gate, ADX-first entry filter, ATR
stop/trail/TP exits, 1.5% risk sizing, 24h circuit breaker) plus a daily
Claude policy overlay (`memory/policy.json`) that can only make it more
conservative. Trades a dedicated Alpaca **paper** account — the paper URL is
hardcoded; no live-trading path exists. Ledger in `data/trades/trades.json`,
reconciled against Alpaca every cycle. See
`docs/superpowers/specs/2026-07-14-cryptoautobot-phase-b-design.md`.

## Universe
BTC ETH SOL XRP DOGE AVAX LINK DOT LTC UNI (Alpaca-tradable)

## Run manually
    .venv/bin/python -m cryptoauto.snapshot          # hourly capture
    .venv/bin/python -m cryptoauto.digest            # print today's summary JSON
    .venv/bin/python -m cryptoauto.trader --dry-run  # preview trade decisions
    .venv/bin/python -m cryptoauto.trader            # one live paper cycle

## Config
Secrets live in `.env` (gitignored): `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`
(paper account), `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `TRADING_ENABLED`.
Strategy constants are in `cryptoauto/config.py`.

## Tests
    .venv/bin/python -m pytest -q

## Schedule (UTC)
- `5 * * * *`  hourly snapshot
- `12 * * * *` hourly paper-trader cycle (`logs/trader.log`)
- `0 17 * * *` daily Claude digest + policy.json (= 00:00 WIB) via claude-routines/run-routine.sh cryptoauto-daily
