from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from market_valuation_panel.panel import write_outputs


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from daily_refresh import validate_dataset, quality_is_ok  # noqa: E402


class DailyFilesTest(unittest.TestCase):
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
