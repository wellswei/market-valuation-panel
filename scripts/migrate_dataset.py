#!/usr/bin/env python3
"""Split the historical wide dataset into one CSV per market date."""

from __future__ import annotations

import csv
import tempfile
from datetime import date
from pathlib import Path


SOURCE = Path("data/market_valuation/dataset.csv")
TARGET = Path("data/market_valuation/daily")


def migrate(source: Path = SOURCE, target: Path = TARGET) -> tuple[int, int]:
    if target.exists():
        raise FileExistsError(f"Refusing to replace existing partitions: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    total_rows = 0
    file_count = 0
    with source.open("r", encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        if not reader.fieldnames or "date" not in reader.fieldnames:
            raise ValueError("Historical dataset has no date column")
        with tempfile.TemporaryDirectory(dir=target.parent) as temporary_dir:
            temporary = Path(temporary_dir)
            current_date = ""
            output_file = None
            try:
                for row in reader:
                    row_date = row["date"]
                    if date.fromisoformat(row_date).isoformat() != row_date:
                        raise ValueError(f"Invalid market date: {row_date}")
                    if row_date < current_date:
                        raise ValueError("Historical dataset is not sorted by date")
                    if row_date != current_date:
                        if output_file is not None:
                            output_file.close()
                        output_file = (temporary / f"{row_date}.csv").open(
                            "w", encoding="utf-8", newline=""
                        )
                        writer = csv.DictWriter(
                            output_file, fieldnames=reader.fieldnames, lineterminator="\n"
                        )
                        writer.writeheader()
                        current_date = row_date
                        file_count += 1
                    writer.writerow(row)
                    total_rows += 1
            finally:
                if output_file is not None:
                    output_file.close()
            if total_rows == 0:
                raise ValueError("Historical dataset is empty")
            temporary.rename(target)
    return total_rows, file_count


if __name__ == "__main__":
    rows, files = migrate()
    print(f"Migrated {rows} rows into {files} daily CSV files; original file retained.")
