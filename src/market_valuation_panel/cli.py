from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from .panel import (
    DEFAULT_MAX_PER_MARKET,
    DEFAULT_PERIODS,
    DEFAULT_SLEEP_SECONDS,
    build_panel,
    write_outputs,
)


def scheduled_snapshot_date(created_at: datetime) -> date:
    # The UTC schedule is 22:30 in daily-market-valuation.yml.
    return (created_at.astimezone(timezone.utc) - timedelta(hours=22, minutes=30)).date()


def scheduled_run_date() -> date | None:
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        return None
    api = os.environ["GITHUB_API_URL"]
    repo = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"]
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "market-valuation-panel"}
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(f"{api}/repos/{repo}/actions/runs/{run_id}", headers=headers), timeout=30) as response:
        created_at = datetime.fromisoformat(json.load(response)["created_at"])
    return scheduled_snapshot_date(created_at)


def command_refresh(args: argparse.Namespace) -> None:
    target_date = args.date or scheduled_run_date()
    max_per_market = None if args.max_per_market == 0 else args.max_per_market
    payload = build_panel(
        markets=tuple(args.market),
        max_per_market=max_per_market,
        sleep_seconds=args.sleep_seconds,
        periods=args.periods,
        target_date=target_date,
    )
    paths = write_outputs(args.output_dir, payload, preserve_existing=target_date is not None)
    no_current_date_rows = (
        len(payload["dataset"]["records"]) == 0
        and payload["dataset"]["excluded_stale_date_records"] > 0
    )
    print(json.dumps({
        "generated_at": payload["generated_at"],
        "date": payload["date"],
        "provider_version": payload["provider_version"],
        "records": len(payload["records"]),
        "dataset_rows": len(payload["dataset"]["records"]),
        "excluded_stale_date_records": payload["dataset"]["excluded_stale_date_records"],
        "raw_valuation_rows": payload["raw_valuation_rows"],
        "industry_count": payload["classification"]["industry_count"],
        "universe": {
            "accepted_symbols": payload["universe"]["accepted_symbols"],
            "stats": [
                {
                    "market": item.get("market"),
                    "reported_total": item.get("reported_total"),
                    "accepted": item.get("accepted"),
                    "queried_industries": item.get("queried_industries"),
                    "sample_policy": item.get("sample_policy"),
                }
                for item in payload["universe"].get("stats", [])
            ],
        },
        "outputs": {
            "daily_csv": str(paths.daily_csv),
            "skipped_write": no_current_date_rows,
            "skip_reason": (
                "no rows matched the target quote date"
                if no_current_date_rows else None
            ),
        },
        "warning_count": payload["warning_count"],
    }, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="market-valuation-panel")
    commands = parser.add_subparsers(dest="command", required=True)
    refresh = commands.add_parser("refresh", help="Refresh Yahoo market valuation panel")
    refresh.add_argument(
        "--market",
        choices=("US", "CN"),
        action="append",
        default=None,
        help="Market to refresh. Repeat for multiple markets. Default: US and CN.",
    )
    refresh.add_argument(
        "--max-per-market",
        type=int,
        default=DEFAULT_MAX_PER_MARKET,
        help="Maximum accepted symbols per market; use 0 for full available universe.",
    )
    refresh.add_argument(
        "--sleep-seconds",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help="Delay between ticker valuation requests.",
    )
    refresh.add_argument(
        "--periods",
        type=int,
        default=DEFAULT_PERIODS,
        help="Quarterly period columns after Current. Default: 5.",
    )
    refresh.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Target quote date (YYYY-MM-DD); explicit dates require verified quote timestamps.",
    )
    refresh.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/market_valuation"),
        help="Output directory for date-partitioned daily CSV files.",
    )
    refresh.set_defaults(
        handler=lambda args: (
            setattr(args, "market", args.market or ["US", "CN"]) or command_refresh(args)
        )
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
