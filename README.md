# Market Valuation Panel

Daily GitHub-maintained Yahoo Finance valuation dataset for US and China equities.

## What It Does

1. Discovers Yahoo's current sector and industry taxonomy with `yfinance.Sector`.
2. Uses `yfinance.EquityQuery` to find US and China tickers by market and industry.
3. Fetches `Ticker.get_valuation_measures(freq="quarterly", periods=5)` once per ticker.
4. Maintains one table:
   - `data/market_valuation/dataset.csv`

The dataset has one row per market date and ticker. `date` is derived from each
ticker's latest regular-market quote time in its exchange timezone when available,
with the New York run date as a fallback. `regularMarketTime` is retained as the
source timestamp. Valuation measures are expanded into stable columns:

```text
<metric>_current
<metric>_q1 ... <metric>_q5
```

`q1_period_end ... q5_period_end` preserve each issuer's fiscal-period dates.

Only rows whose derived `date` equals the New York run date are written; stale
quote dates are excluded from that day's append. Reruns replace rows for the same
run date before writing, so a manual retry does not duplicate that day's snapshot.

## Local Run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
market-valuation-panel refresh --max-per-market 50
```

Use full coverage intentionally:

```bash
market-valuation-panel refresh --max-per-market 0 --sleep-seconds 0.5
```

## Data Source

This project uses Yahoo Finance data through `yfinance`. It is not affiliated with,
endorsed by, or sponsored by Yahoo. Data availability, fields, classifications, and
rate limits may change upstream.

## Data Policy

- `records` stores ticker classification and selected quote metadata.
- `dataset.records` stores the rows written into `dataset.csv`.
- `Current` is Yahoo's provider trailing time-series value, not a same-close recomputation.
- Rows with stale quote dates are discarded from the daily append instead of being
  written under older dates.
- `marketCap_current` is the maintained market capitalization field. Screener market
  capitalization is used only for sampling and is not written into `dataset.csv`.
- China symbols are limited to CNY A-share style listings; B-share code ranges and
  non-CNY listings are excluded.
- Sector and industry are Yahoo provider classifications at refresh time, not permanent taxonomy.
- This dataset is for research and tooling; it is not investment advice.

## GitHub Actions

`.github/workflows/daily-market-valuation.yml` refreshes daily at 17:00 New York time
and commits `data/market_valuation/dataset.csv` back to the repository. Because
GitHub scheduled runs can be delayed or dropped during high-load periods, the
workflow uses off-peak retry entries every 15 minutes from 17:07 through 19:07
New York time, logs both scheduled and actual start times, and skips once a
scheduled refresh has already succeeded that day. Manual runs are also supported.
If a weekend or market holiday produces no rows for the New York run date, the
workflow leaves `dataset.csv` unchanged and exits successfully.

The scheduled run uses full coverage by default (`MAX_PER_MARKET=0`). Manual runs can
override `max_per_market` when a smaller sample is useful for testing. Scheduled
runs use a `0.2` second delay between ticker valuation requests by default; manual
runs can override `sleep_seconds` when testing throughput.

## License

MIT
