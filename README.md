# Market Valuation Panel

Daily GitHub-maintained Yahoo Finance valuation dataset for US and China equities.

## What It Does

1. Discovers Yahoo's current sector and industry taxonomy with `yfinance.Sector`.
2. Uses `yfinance.EquityQuery` to find US and China tickers by market and industry.
3. Fetches `Ticker.get_valuation_measures(freq="quarterly", periods=5)` once per ticker.
4. Maintains one table:
   - `data/market_valuation/dataset.csv`

The dataset has one row per refresh date and ticker. Valuation measures are expanded
into stable columns:

```text
<metric>_current
<metric>_q1 ... <metric>_q5
```

`q1_period_end ... q5_period_end` preserve each issuer's fiscal-period dates.

Reruns replace rows for the same `date` before writing, so a manual retry does not
duplicate that day's snapshot.

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

- `records` stores ticker classification and screener metadata.
- `dataset.records` stores the rows written into `dataset.csv`.
- `Current` is Yahoo's provider trailing time-series value, not a same-close recomputation.
- Sector and industry are Yahoo provider classifications at refresh time, not permanent taxonomy.
- This dataset is for research and tooling; it is not investment advice.

## GitHub Actions

`.github/workflows/daily-market-valuation.yml` refreshes daily at 18:00 New York time
and commits `data/market_valuation/dataset.csv` back to the repository. GitHub cron
runs in UTC, so the workflow wakes at both 22:00 and 23:00 UTC and only proceeds when
`America/New_York` is actually 18:00. Manual runs are also supported.

The default sample size is controlled by `MAX_PER_MARKET` in the workflow.

## License

MIT
