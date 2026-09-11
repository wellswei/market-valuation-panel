from __future__ import annotations

import csv
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yfinance as yf
from yfinance import EquityQuery
from yfinance.const import SECTOR_INDUSTY_MAPPING_LC


PAGE_SIZE = 250
DEFAULT_MAX_PER_MARKET = 750
DEFAULT_SLEEP_SECONDS = 0.5
DEFAULT_PERIODS = 5
DEFAULT_SCREEN_RETRIES = 3
DEFAULT_SCREEN_RETRY_SLEEP_SECONDS = 3.0
RUN_TIMEZONE = ZoneInfo("America/New_York")
SORT_MARKET_CAP_FIELD = "_sort_market_cap"
US_EXCHANGES = ("NMS", "NYQ", "ASE")
CN_EXCHANGES = ("SHH", "SHZ")

VALUATION_MEASURE_TO_METRIC = {
    "Market Cap": "marketCap",
    "Enterprise Value": "enterpriseValue",
    "Trailing P/E": "trailingPE",
    "Forward P/E": "forwardPE",
    "PEG Ratio (5yr expected)": "pegRatio5y",
    "Price/Sales": "priceToSales",
    "Price/Book": "priceToBook",
    "Enterprise Value/Revenue": "enterpriseToRevenue",
    "Enterprise Value/EBITDA": "enterpriseToEbitda",
}

SCREENER_AUX_FIELDS = (
    "fullExchangeName",
    "exchangeTimezoneName",
    "financialCurrency",
    "regularMarketPrice",
    "regularMarketPreviousClose",
    "regularMarketTime",
    "sharesOutstanding",
    "impliedSharesOutstanding",
    "epsTrailingTwelveMonths",
    "epsForward",
    "epsCurrentYear",
    "bookValue",
    "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow",
    "fiftyTwoWeekChangePercent",
    "averageDailyVolume3Month",
    "earningsTimestamp",
    "averageAnalystRating",
)

DATASET_BASE_COLUMNS = (
    "date",
    "market",
    "exchange",
    "symbol",
    "name",
    "sector",
    "industry",
    "currency",
    *SCREENER_AUX_FIELDS,
)


@dataclass(frozen=True)
class OutputPaths:
    dataset_csv: Path


def installed_yfinance_version() -> str | None:
    try:
        return package_version("yfinance")
    except PackageNotFoundError:
        return None


def safe_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def safe_positive_number(value: object) -> float | None:
    number = safe_number(value)
    return number if number is not None and number > 0 else None


def compact_number(value: float | None) -> float | None:
    if value is None:
        return None
    return float(f"{value:.8g}")


def clean_metric(value: object, *, positive_only: bool = False) -> float | None:
    number = safe_positive_number(value) if positive_only else safe_number(value)
    return compact_number(number)


def slug_key(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def equity_query_industry_values() -> dict[str, str]:
    raw_values = EquityQuery("eq", ["region", "us"]).valid_values.get("industry") or {}
    if isinstance(raw_values, dict):
        iterable = (item for group in raw_values.values() for item in group)
    else:
        iterable = iter(raw_values)
    return {slug_key(industry): str(industry) for industry in iterable}


def sector_industry_catalog() -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    query_industry_values = equity_query_industry_values()
    for sector_key in sorted(SECTOR_INDUSTY_MAPPING_LC):
        try:
            sector = yf.Sector(sector_key)
            sector_name = str(sector.name or "").strip()
            industries = sector.industries
        except Exception as exc:
            warnings.append(f"{sector_key}: sector industry fetch {type(exc).__name__}")
            continue
        if industries is None or industries.empty:
            warnings.append(f"{sector_key}: no industries")
            continue
        for industry_key, row in industries.iterrows():
            industry_name = str(row.get("name") or "").strip()
            if not industry_name:
                continue
            records.append({
                "sector_key": sector_key,
                "sector": sector_name,
                "industry_key": str(industry_key),
                "industry": industry_name,
                "industry_query": query_industry_values.get(str(industry_key), industry_name),
                "industry_symbol": row.get("symbol"),
                "industry_market_weight": clean_metric(row.get("market weight")),
            })
    return records, warnings


def market_industry_query(market: str, industry: str) -> EquityQuery:
    if market == "US":
        return EquityQuery("and", [
            EquityQuery("eq", ["region", "us"]),
            EquityQuery("is-in", ["exchange", *US_EXCHANGES]),
            EquityQuery("eq", ["industry", industry]),
        ])
    if market == "CN":
        return EquityQuery("and", [
            EquityQuery("eq", ["region", "cn"]),
            EquityQuery("is-in", ["exchange", *CN_EXCHANGES]),
            EquityQuery("eq", ["industry", industry]),
        ])
    raise ValueError(f"Unsupported market: {market}")


def include_symbol(market: str, symbol: str, exchange: str | None) -> bool:
    if market == "US":
        return bool(re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol)) and exchange in US_EXCHANGES
    if market == "CN":
        if not re.fullmatch(r"\d{6}\.(SS|SZ)", symbol):
            return False
        return not symbol.startswith(("900", "200", "201"))
    return False


def include_quote(market: str, item: dict[str, Any]) -> bool:
    symbol = str(item.get("symbol") or "").strip().upper()
    exchange = item.get("exchange")
    if not symbol or not include_symbol(market, symbol, exchange):
        return False
    if market == "CN" and item.get("currency") != "CNY":
        return False
    return True


def exchange_local_date(record: dict[str, Any], *, fallback_date: str) -> str:
    timestamp = safe_number(record.get("regularMarketTime"))
    timezone_name = str(record.get("exchangeTimezoneName") or "").strip()
    if timestamp is None or not timezone_name:
        return fallback_date
    try:
        return datetime.fromtimestamp(timestamp, ZoneInfo(timezone_name)).date().isoformat()
    except Exception:
        return fallback_date


def screen_with_retries(query: EquityQuery, *, offset: int) -> tuple[dict[str, Any] | None, str | None]:
    last_warning = None
    for attempt in range(1, DEFAULT_SCREEN_RETRIES + 1):
        try:
            result = yf.screen(query, offset=offset, size=PAGE_SIZE, sortField="intradaymarketcap", sortAsc=False)
        except Exception as exc:
            last_warning = (
                f"screen offset {offset} attempt {attempt}/{DEFAULT_SCREEN_RETRIES}: "
                f"{type(exc).__name__}"
            )
            if attempt < DEFAULT_SCREEN_RETRIES:
                time.sleep(DEFAULT_SCREEN_RETRY_SLEEP_SECONDS)
            continue
        return result, None
    return None, last_warning


def collect_industry_quotes(
    *,
    market: str,
    industry_record: dict[str, Any],
    complete: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    industry = industry_record["industry"]
    query_industry = industry_record.get("industry_query") or industry
    query = market_industry_query(market, query_industry)
    records: list[dict[str, Any]] = []
    warnings: list[str] = []

    def add_quotes(quotes: list[dict[str, Any]]) -> None:
        for item in quotes:
            symbol = str(item.get("symbol") or "").strip().upper()
            exchange = item.get("exchange")
            if not include_quote(market, item):
                continue
            record = {
                "symbol": symbol,
                "market": market,
                "exchange": exchange,
                "name": item.get("shortName") or item.get("longName") or "",
                "currency": item.get("currency"),
                "sector_key": industry_record["sector_key"],
                "sector": industry_record["sector"],
                "industry_key": industry_record["industry_key"],
                "industry": industry,
                SORT_MARKET_CAP_FIELD: clean_metric(
                    item.get("marketCap") or item.get("intradaymarketcap"),
                    positive_only=True,
                ),
            }
            for field in SCREENER_AUX_FIELDS:
                record[field] = item.get(field)
            records.append(record)

    first, warning = screen_with_retries(query, offset=0)
    if warning:
        warnings.append(f"{market} {industry}: {warning}")
    if first is None:
        return records, {
            "market": market,
            "sector": industry_record["sector"],
            "industry": industry,
            "industry_query": query_industry,
            "reported_total": 0,
            "accepted": 0,
            "requested_offsets": [0],
            "sample_policy": "complete" if complete else "industry_top_market_cap_page",
            "warnings": warnings,
        }
    total = int(first.get("total") or 0)
    add_quotes(first.get("quotes") or [])
    offsets: list[int] = []
    if complete and total > PAGE_SIZE:
        offsets = list(range(PAGE_SIZE, total, PAGE_SIZE))
        for offset in offsets:
            result, warning = screen_with_retries(query, offset=offset)
            if warning:
                warnings.append(f"{market} {industry}: {warning}")
            if result is None:
                break
            quotes = result.get("quotes") or []
            if not quotes:
                break
            add_quotes(quotes)
    return records, {
        "market": market,
        "sector": industry_record["sector"],
        "industry": industry,
        "industry_query": query_industry,
        "reported_total": total,
        "accepted": len(records),
        "requested_offsets": [0, *offsets],
        "sample_policy": "complete" if complete else "industry_top_market_cap_page",
        "warnings": warnings,
    }


def collect_universe(
    market: str,
    *,
    max_symbols: int | None,
    industry_catalog: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records_by_symbol: dict[str, dict[str, Any]] = {}
    industry_stats: list[dict[str, Any]] = []
    warnings: list[str] = []
    complete = max_symbols is None
    for industry_record in industry_catalog:
        industry_records, stats = collect_industry_quotes(
            market=market,
            industry_record=industry_record,
            complete=complete,
        )
        industry_stats.append(stats)
        warnings.extend(stats.get("warnings") or [])
        for record in industry_records:
            symbol = record["symbol"]
            existing = records_by_symbol.get(symbol)
            if existing is None:
                records_by_symbol[symbol] = record
                continue
            existing_cap = safe_number(existing.get(SORT_MARKET_CAP_FIELD)) or 0
            new_cap = safe_number(record.get(SORT_MARKET_CAP_FIELD)) or 0
            if new_cap > existing_cap:
                records_by_symbol[symbol] = record

    records = sorted(
        records_by_symbol.values(),
        key=lambda item: safe_number(item.get(SORT_MARKET_CAP_FIELD)) or 0,
        reverse=True,
    )
    if max_symbols is not None:
        records = records[:max_symbols]
    return records, {
        "market": market,
        "reported_total": sum(int(item.get("reported_total") or 0) for item in industry_stats),
        "accepted": len(records),
        "queried_industries": len(industry_catalog),
        "industry_stats": industry_stats,
        "sample_policy": "complete_by_industry" if complete else "largest_market_cap_sample_from_industry_top_pages",
        "warnings": warnings,
    }


def period_end(label: object) -> str | None:
    text = str(label or "").strip()
    if text == "Current":
        return None
    try:
        return datetime.strptime(text, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def valuation_measure_rows(
    ticker: yf.Ticker,
    base: dict[str, Any],
    *,
    snapshot_date: str,
    periods: int,
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        frame = ticker.get_valuation_measures(freq="quarterly", periods=periods)
    except Exception as exc:
        return [], f"{base['symbol']}: valuation_measures {type(exc).__name__}"
    if frame is None or frame.empty:
        return [], f"{base['symbol']}: valuation_measures missing"

    rows: list[dict[str, Any]] = []
    row_date = exchange_local_date(base, fallback_date=snapshot_date)
    for measure, series in frame.iterrows():
        measure_name = str(measure).strip()
        metric = VALUATION_MEASURE_TO_METRIC.get(measure_name)
        if metric is None:
            continue
        for period_label, raw_value in series.items():
            value = clean_metric(raw_value)
            if value is None:
                continue
            rows.append({
                "date": row_date,
                "market": base["market"],
                "exchange": base.get("exchange"),
                "symbol": base["symbol"],
                "name": base.get("name"),
                "sector": base["sector"],
                "industry": base["industry"],
                "currency": base.get("currency"),
                "financialCurrency": base.get("financialCurrency"),
                "period_label": str(period_label),
                "period_end": period_end(period_label),
                "measure": measure_name,
                "metric": metric,
                "value": value,
            })
    return rows, None if rows else f"{base['symbol']}: valuation_measures empty after cleaning"


def fetch_ticker_valuation(
    record: dict[str, Any],
    *,
    snapshot_date: str,
    periods: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    symbol = record["symbol"]
    try:
        ticker = yf.Ticker(symbol)
    except Exception as exc:
        return None, [], [f"{symbol}: {type(exc).__name__}"]

    result = dict(record)
    rows, warning = valuation_measure_rows(
        ticker,
        result,
        snapshot_date=snapshot_date,
        periods=periods,
    )
    warnings = [warning] if warning else []
    if not rows:
        warnings.append(f"{symbol}: no valuation measures")
        return None, [], warnings
    return result, rows, warnings


def dataset_columns(periods: int) -> list[str]:
    columns = list(DATASET_BASE_COLUMNS)
    columns.extend(f"q{index}_period_end" for index in range(1, periods + 1))
    suffixes = ["current", *(f"q{index}" for index in range(1, periods + 1))]
    for metric in VALUATION_MEASURE_TO_METRIC.values():
        columns.extend(f"{metric}_{suffix}" for suffix in suffixes)
    return columns


def dataset_snapshot_rows(
    *,
    records: list[dict[str, Any]],
    valuation_rows: list[dict[str, Any]],
    snapshot_date: str,
    periods: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    columns = dataset_columns(periods)
    record_by_symbol = {record["symbol"]: record for record in records}
    rows_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valuation_rows:
        rows_by_symbol[row["symbol"]].append(row)

    output: list[dict[str, Any]] = []
    for symbol in sorted(record_by_symbol):
        record = record_by_symbol[symbol]
        item = {column: record.get(column) for column in DATASET_BASE_COLUMNS}
        item["date"] = exchange_local_date(record, fallback_date=snapshot_date)
        symbol_rows = rows_by_symbol.get(symbol) or []
        dated_periods = sorted(
            {row["period_end"] for row in symbol_rows if row.get("period_end")},
            reverse=True,
        )[:periods]
        period_to_suffix = {None: "current"}
        for index, period in enumerate(dated_periods, start=1):
            item[f"q{index}_period_end"] = period
            period_to_suffix[period] = f"q{index}"
        for row in symbol_rows:
            suffix = period_to_suffix.get(row.get("period_end"))
            metric = row.get("metric")
            if suffix and metric:
                item[f"{metric}_{suffix}"] = row.get("value")
        output.append(item)
    return columns, output


def build_panel(
    *,
    markets: tuple[str, ...] = ("US", "CN"),
    max_per_market: int | None = DEFAULT_MAX_PER_MARKET,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    periods: int = DEFAULT_PERIODS,
) -> dict[str, Any]:
    started_at_datetime = datetime.now(timezone.utc)
    started_at = started_at_datetime.isoformat(timespec="seconds")
    snapshot_date = started_at_datetime.astimezone(RUN_TIMEZONE).date().isoformat()
    industry_catalog, catalog_warnings = sector_industry_catalog()

    universe: list[dict[str, Any]] = []
    universe_stats = []
    universe_warnings: list[str] = []
    for market in markets:
        records, stats = collect_universe(
            market,
            max_symbols=max_per_market,
            industry_catalog=industry_catalog,
        )
        universe.extend(records)
        universe_stats.append(stats)
        universe_warnings.extend(stats.get("warnings") or [])

    records: list[dict[str, Any]] = []
    valuation_rows: list[dict[str, Any]] = []
    warnings: list[str] = [*universe_warnings]
    for index, record in enumerate(universe):
        valuation, rows, ticker_warnings = fetch_ticker_valuation(
            record,
            snapshot_date=snapshot_date,
            periods=periods,
        )
        if valuation is not None:
            records.append(valuation)
        valuation_rows.extend(rows)
        warnings.extend(ticker_warnings)
        if sleep_seconds > 0 and index < len(universe) - 1:
            time.sleep(sleep_seconds)

    dataset_header, dataset_records = dataset_snapshot_rows(
        records=records,
        valuation_rows=valuation_rows,
        snapshot_date=snapshot_date,
        periods=periods,
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "started_at": started_at,
        "date": snapshot_date,
        "provider": "yfinance",
        "provider_version": installed_yfinance_version(),
        "policy": {
            "classification_source": "Yahoo Sector.industries plus EquityQuery industry screens",
            "universe_source": "Yahoo EquityQuery per market and industry",
            "valuation_measures_source": "Yahoo fundamentals-timeseries via get_valuation_measures",
            "markets": list(markets),
            "max_per_market": max_per_market,
            "periods": periods,
            "bounded_sample_policy": (
                "largest market capitalizations after each industry top page"
                if max_per_market is not None else None
            ),
            "notes": [
                "Sector/industry classification is a provider snapshot for this refresh, not a permanent taxonomy.",
                "date is the exchange-local date derived from regularMarketTime when available; otherwise it falls back to the New York run date.",
                "marketCap_current is the maintained market capitalization field; screener market cap is used only for sampling.",
                "valuation_measures Current is Yahoo's provider trailing time-series value, not a same-close recomputation.",
                "dataset.csv is one row per market date and ticker; reruns replace the market dates present in the new snapshot before writing.",
            ],
        },
        "classification": {
            "industries": industry_catalog,
            "industry_count": len(industry_catalog),
            "warnings": catalog_warnings,
        },
        "universe": {"stats": universe_stats, "accepted_symbols": len(universe)},
        "records": records,
        "dataset": {
            "columns": dataset_header,
            "records": dataset_records,
        },
        "raw_valuation_rows": len(valuation_rows),
        "warnings": [*catalog_warnings, *warnings][:500],
        "warning_count": len(catalog_warnings) + len(warnings),
    }


def output_paths(output_dir: Path) -> OutputPaths:
    return OutputPaths(dataset_csv=output_dir / "dataset.csv")


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def read_existing_dataset(path: Path, *, replace_dates: set[str]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle) if row.get("date") not in replace_dates]


def dataset_sort_key(row: dict[str, Any]) -> tuple[str, str, str, str, str, str, str]:
    return (
        str(row.get("date") or ""),
        str(row.get("market") or ""),
        str(row.get("sector") or ""),
        str(row.get("industry") or ""),
        str(row.get("symbol") or ""),
        str(row.get("exchange") or ""),
        str(row.get("name") or ""),
    )


def write_outputs(output_dir: Path, payload: dict[str, Any]) -> OutputPaths:
    paths = output_paths(output_dir)
    columns = payload["dataset"]["columns"]
    current_rows = payload["dataset"]["records"]
    if not current_rows:
        raise RuntimeError("No dataset rows were fetched; leaving dataset.csv unchanged.")
    replace_dates = {str(row.get("date") or "") for row in current_rows if row.get("date")}
    existing_rows = read_existing_dataset(paths.dataset_csv, replace_dates=replace_dates)
    write_csv(paths.dataset_csv, columns, sorted([*existing_rows, *current_rows], key=dataset_sort_key))
    return paths
