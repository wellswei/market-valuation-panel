from __future__ import annotations

import csv
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from market_valuation_panel.cli import scheduled_snapshot_date
from market_valuation_panel.panel import current_date_rows, dataset_snapshot_rows, write_outputs


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from daily_refresh import find_today_run, run_target_date, validate_dataset, quality_is_ok  # noqa: E402


class DailyFilesTest(unittest.TestCase):
    def test_current_schedule_date_in_standard_and_daylight_time(self) -> None:
        workflow = (ROOT / ".github/workflows/daily-market-valuation.yml").read_text()
        cron = re.search(r'^\s*- cron: "([^"]+)"', workflow, re.MULTILINE)
        self.assertIsNotNone(cron)
        for created_at, expected in (
            ("2026-11-25T21:36:59Z", "2026-11-24"),
            ("2026-11-25T21:37:00Z", "2026-11-25"),
            ("2026-11-26T06:12:21Z", "2026-11-25"),
            ("2026-07-13T20:36:59Z", "2026-07-12"),
            ("2026-07-13T20:37:00Z", "2026-07-13"),
            ("2026-07-14T05:12:21Z", "2026-07-13"),
        ):
            with self.subTest(created_at=created_at):
                run = {"event": "schedule", "createdAt": created_at}
                self.assertEqual(run_target_date(run), expected)
                self.assertEqual(
                    scheduled_snapshot_date(datetime.fromisoformat(created_at), cron[1]).isoformat(),
                    expected,
                )

    def test_legacy_schedule_rerun_still_uses_utc_slot(self) -> None:
        created_at = datetime.fromisoformat("2026-11-24T21:45:00Z")
        self.assertEqual(scheduled_snapshot_date(created_at, "30 22 * * *").isoformat(), "2026-11-23")

    def test_rerun_replaces_only_the_current_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            columns = ["date", "market", "symbol", "currency", "regularMarketTime", "exchangeTimezoneName"]

            def payload(day: str, symbol: str) -> dict:
                return {
                    "date": day,
                    "dataset": {
                        "columns": columns,
                        "records": [{"date": day, "market": "US", "symbol": symbol}],
                    },
                }

            first = write_outputs(output, payload("2026-10-05", "FIRST"))
            write_outputs(output, payload("2026-10-06", "NEXT"))
            write_outputs(output, payload("2026-10-06", "REPLACED"))
            with first.daily_csv.open(newline="") as handle:
                self.assertEqual([row["symbol"] for row in csv.DictReader(handle)], ["FIRST"])
            with (output / "daily" / "2026-10-06.csv").open(newline="") as handle:
                self.assertEqual([row["symbol"] for row in csv.DictReader(handle)], ["REPLACED"])

            stale = {"date": "2026-10-07", "dataset": {
                "columns": columns, "records": [], "excluded_stale_date_records": 1,
            }}
            write_outputs(output, stale)
            self.assertFalse((output / "daily" / "2026-10-07.csv").exists())

    def test_delayed_schedule_keeps_the_previous_quote_date(self) -> None:
        run = {
            "event": "schedule", "createdAt": "2026-11-24T06:12:21Z",
            "headBranch": "main", "status": "completed", "conclusion": "success",
        }
        self.assertEqual(find_today_run([run], ny_date="2026-11-23", branch="main"), run)
        self.assertIsNone(find_today_run([run], ny_date="2026-11-24", branch="main"))
        quote_time = int(datetime.fromisoformat("2026-11-23T16:00:00-05:00").timestamp())
        _, rows = dataset_snapshot_rows(
            records=[
                {"market": "US", "symbol": "US", "regularMarketTime": quote_time,
                 "exchangeTimezoneName": "America/New_York"},
                {"market": "CN", "symbol": "CN", "regularMarketTime": quote_time,
                 "exchangeTimezoneName": "Asia/Shanghai"},
                {"market": "US", "symbol": "UNKNOWN"},
            ],
            valuation_rows=[], fallback_date=None, periods=5,
        )
        selected, excluded = current_date_rows(rows, snapshot_date="2026-11-23")
        self.assertEqual([row["symbol"] for row in selected], ["US"])
        self.assertEqual(excluded, 2)

    def test_dated_retry_preserves_existing_market_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            day = "2026-11-24"
            columns = ["date", "market", "symbol"]

            def payload(market: str) -> dict:
                return {"date": day, "dataset": {"columns": columns, "records": [
                    {"date": day, "market": market, "symbol": market},
                ]}}

            paths = write_outputs(output, payload("CN"))
            write_outputs(output, payload("US"), preserve_existing=True)
            with paths.daily_csv.open(newline="") as handle:
                self.assertEqual([row["market"] for row in csv.DictReader(handle)], ["CN", "US"])

            before = paths.daily_csv.read_bytes()
            stale = {"date": day, "dataset": {
                "columns": columns, "records": [], "excluded_stale_date_records": 1,
            }}
            write_outputs(output, stale, preserve_existing=True)
            self.assertEqual(paths.daily_csv.read_bytes(), before)

    def test_quality_checks_partition_dates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            columns = ["date", "market", "symbol", "currency", "regularMarketTime", "exchangeTimezoneName"]
            write_outputs(output, {"date": "2026-10-05", "dataset": {
                "columns": columns,
                "records": [{"date": "2026-10-05", "market": "US", "symbol": "TEST"}],
            }})
            quality = validate_dataset(output / "daily", expected_date="2026-10-06")
            self.assertEqual(quality["rows_total"], 1)
            self.assertEqual(quality["expected_date_rows"], 0)
            self.assertTrue(quality_is_ok(quality))

            (output / "daily" / "2026-10-05.csv").rename(output / "daily" / "2026-10-04.csv")
            quality = validate_dataset(output / "daily", expected_date="2026-10-06")
            self.assertEqual(quality["partition_date_mismatches"], 1)
            self.assertFalse(quality_is_ok(quality))

    def test_migration_keeps_all_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data" / "market_valuation" / "dataset.csv"
            source.parent.mkdir(parents=True)
            with source.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["date", "market", "symbol"])
                writer.writeheader()
                writer.writerows([
                    {"date": "2026-10-05", "market": "US", "symbol": "A"},
                    {"date": "2026-10-05", "market": "CN", "symbol": "B"},
                    {"date": "2026-10-06", "market": "US", "symbol": "C"},
                ])
            subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate_dataset.py")],
                           cwd=root, check=True, capture_output=True, text=True)
            daily = source.parent / "daily"
            self.assertEqual(sorted(path.name for path in daily.glob("*.csv")),
                             ["2026-10-05.csv", "2026-10-06.csv"])
            migrated_rows = 0
            for path in daily.glob("*.csv"):
                with path.open(newline="") as handle:
                    migrated_rows += sum(1 for _ in csv.DictReader(handle))
            self.assertEqual(migrated_rows, 3)
            self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
