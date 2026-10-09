# Market Valuation Panel

Daily GitHub-maintained Yahoo Finance valuation dataset for US and China equities.

## What It Does

1. Discovers Yahoo's current sector and industry taxonomy with `yfinance.Sector`.
2. Uses `yfinance.EquityQuery` to find US and China tickers by market and industry.
3. Fetches `Ticker.get_valuation_measures(freq="quarterly", periods=5)` once per ticker.
4. Maintains one wide table, partitioned into daily files:
   - `data/market_valuation/daily/YYYY-MM-DD.csv`

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
quote dates are excluded. Reruns replace that date's CSV without changing older
files, so a manual retry does not duplicate the snapshot.

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
- `dataset.records` stores the rows written into the run date's CSV.
- `Current` is Yahoo's provider trailing time-series value, not a same-close recomputation.
- Rows with stale quote dates are discarded from the daily append instead of being
  written under older dates.
- `marketCap_current` is the maintained market capitalization field. Screener market
  capitalization is used only for sampling and is not written into daily CSV files.
- China symbols are limited to CNY A-share style listings; B-share code ranges and
  non-CNY listings are excluded.
- Sector and industry are Yahoo provider classifications at refresh time, not permanent taxonomy.
- This dataset is for research and tooling; it is not investment advice.

## GitHub Actions

`.github/workflows/daily-market-valuation.yml` runs on manual dispatch and commits
the run date's CSV back to the repository. The maintainer trigger below dispatches
it each day at 17:30 New York time. If a weekend or market holiday produces no
rows for the New York run date, the workflow leaves existing daily files unchanged
and exits successfully.

The workflow uses full coverage by default (`MAX_PER_MARKET=0`) and a `0.2` second
delay between ticker valuation requests. Manual runs can override those inputs.

## Maintainer Daily Trigger

For a compact daily manual trigger plus quality check, use:

```bash
scripts/daily_refresh.py --wait
```

The script skips duplicate dispatch if today's manual run already exists, triggers
`daily-market-valuation.yml` when needed, waits for completion when `--wait` is
supplied, fast-forwards the local checkout after a successful run, validates all
daily files and the latest partition, and prints one compact `STATUS=...` line.

Use `--json` when the full machine-readable result is needed. Warning status
`coverage_gap` means Yahoo returned no valuation measures for some accepted
symbols; this is classified from the workflow log by checking whether
`warning_count == 2 * (accepted_symbols - records)`.

## License

MIT
