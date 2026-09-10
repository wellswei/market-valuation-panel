# Market Valuation Panel

Daily Yahoo Finance valuation panel for US and China equities.

## What It Does

1. Discovers Yahoo's current sector and industry taxonomy with `yfinance.Sector`.
2. Uses `yfinance.EquityQuery` to find US and China tickers by market and industry.
3. Fetches `Ticker.get_valuation_measures(freq="quarterly", periods=5)` once per ticker.
4. Writes:
   - `data/market_valuation/latest.json`
   - `data/market_valuation/latest_wide.csv`
   - `data/market_valuation/latest_long.csv`
   - `data/market_valuation/daily/<YYYY-MM-DD>.json`
   - `data/market_valuation/daily/<YYYY-MM-DD>_wide.csv`
   - `data/market_valuation/daily/<YYYY-MM-DD>_long.csv`

The wide table has one row per ticker per refresh date. Valuation columns use:

```text
<metric>_current
<metric>_q1 ... <metric>_q5
```

`q1_period_end ... q5_period_end` preserve each issuer's fiscal-period dates.

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

## Data Policy

- `records` stores ticker classification and screener metadata.
- `valuation_measures.long` stores provider valuation data in long form.
- `valuation_measures.wide` stores one-row-per-ticker data for daily analysis.
- `Current` is Yahoo's provider trailing time-series value, not a same-close recomputation.
- Sector and industry are Yahoo provider classifications at refresh time, not permanent taxonomy.

## GitHub Actions

`.github/workflows/daily-market-valuation.yml` runs daily and commits updated data files back to the repository. The default sample size is controlled by `MAX_PER_MARKET` in the workflow.

