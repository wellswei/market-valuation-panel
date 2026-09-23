#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


NY_TZ = ZoneInfo("America/New_York")
DEFAULT_REPO = "wellswei/market-valuation-panel"
DEFAULT_WORKFLOW = "daily-market-valuation.yml"
DEFAULT_BRANCH = "main"
DEFAULT_DATASET = Path("data/market_valuation/dataset.csv")


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str


def run_command(args: list[str], *, check: bool = True) -> CommandResult:
    result = subprocess.run(args, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return CommandResult(stdout=result.stdout, stderr=result.stderr)


def gh_json(args: list[str]) -> Any:
    result = run_command(args)
    return json.loads(result.stdout or "null")


def current_ny_date() -> str:
    return datetime.now(timezone.utc).astimezone(NY_TZ).date().isoformat()


def run_created_ny_date(run: dict[str, Any]) -> str | None:
    created_at = run.get("createdAt")
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(NY_TZ).date().isoformat()


def list_runs(repo: str, workflow: str, *, limit: int = 20) -> list[dict[str, Any]]:
    return gh_json([
        "gh",
        "run",
        "list",
        "--repo",
        repo,
        "--workflow",
        workflow,
        "--limit",
        str(limit),
        "--json",
        "databaseId,displayTitle,status,conclusion,createdAt,updatedAt,event,url,headBranch",
    ])


def find_today_run(runs: list[dict[str, Any]], *, ny_date: str, branch: str) -> dict[str, Any] | None:
    eligible = [
        run for run in runs
        if run_created_ny_date(run) == ny_date
        and run.get("headBranch") == branch
        and run.get("event") == "workflow_dispatch"
    ]
    if not eligible:
        return None
    priority = {"in_progress": 0, "queued": 1, "waiting": 2, "requested": 3, "completed": 4}
    eligible.sort(key=lambda run: (priority.get(str(run.get("status")), 9), str(run.get("createdAt") or "")))
    successful = [run for run in eligible if run.get("status") == "completed" and run.get("conclusion") == "success"]
    return successful[-1] if successful else eligible[0]


def trigger_workflow(repo: str, workflow: str, branch: str, *, max_per_market: str, sleep_seconds: str) -> None:
    run_command([
        "gh",
        "workflow",
        "run",
        workflow,
        "--repo",
        repo,
        "--ref",
        branch,
        "-f",
        f"max_per_market={max_per_market}",
        "-f",
        f"sleep_seconds={sleep_seconds}",
    ])


def wait_for_run(repo: str, run_id: int, *, timeout_seconds: int, poll_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = gh_json([
            "gh",
            "run",
            "view",
            str(run_id),
            "--repo",
            repo,
            "--json",
            "databaseId,status,conclusion,createdAt,updatedAt,url",
        ])
        if last.get("status") == "completed":
            return last
        time.sleep(poll_seconds)
    raise TimeoutError(f"run {run_id} did not complete within {timeout_seconds} seconds")


def strip_log_prefix(line: str) -> str:
    stripped = re.sub(r"^\S+\t[^\t]+\t\S+\s", "", line)
    return re.sub(r"\x1b\[[0-9;]*m", "", stripped)


def extract_refresh_summary(log_text: str) -> dict[str, Any] | None:
    payload_lines: list[str] = []
    collecting = False
    depth = 0
    for raw_line in log_text.splitlines():
        message = strip_log_prefix(raw_line)
        if not collecting and message.strip() == "{":
            collecting = True
        if not collecting:
            continue
        payload_lines.append(message)
        depth += message.count("{") - message.count("}")
        if collecting and depth == 0:
            candidate = "\n".join(payload_lines)
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                payload_lines = []
                collecting = False
                continue
            if "dataset_rows" in payload and "warning_count" in payload:
                return payload
            payload_lines = []
            collecting = False
    return None


def run_log_summary(repo: str, run_id: int) -> dict[str, Any] | None:
    result = run_command(["gh", "run", "view", str(run_id), "--repo", repo, "--log"], check=True)
    return extract_refresh_summary(result.stdout)


def git_pull(branch: str) -> None:
    run_command(["git", "fetch", "origin", branch, "--quiet"])
    run_command(["git", "pull", "--ff-only", "origin", branch])


def to_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def exchange_local_date(row: dict[str, str]) -> str | None:
    timestamp = to_float(row.get("regularMarketTime"))
    timezone_name = (row.get("exchangeTimezoneName") or "").strip()
    if timestamp is None or not timezone_name:
        return None
    try:
        return datetime.fromtimestamp(timestamp, ZoneInfo(timezone_name)).date().isoformat()
    except Exception:
        return None


def coverage_rate(rows: list[dict[str, str]], column: str) -> float:
    if not rows:
        return 0.0
    return round(sum(1 for row in rows if row.get(column) not in ("", None)) / len(rows), 4)


def validate_dataset(path: Path, *, expected_date: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    date_counts = Counter(row["date"] for row in rows)
    expected_rows = [row for row in rows if row.get("date") == expected_date]
    key_counts = Counter((row.get("date"), row.get("market"), row.get("symbol")) for row in rows)
    duplicate_keys = sum(1 for count in key_counts.values() if count > 1)
    b_shares = [
        row["symbol"] for row in rows
        if row.get("market") == "CN"
        and row.get("symbol", "").startswith(("900", "200", "201"))
    ]
    cn_non_cny = [
        (row.get("symbol"), row.get("currency"))
        for row in rows
        if row.get("market") == "CN" and row.get("currency") != "CNY"
    ]
    date_mismatches = [
        row.get("symbol") for row in expected_rows
        if exchange_local_date(row) != row.get("date")
    ]
    coverage_columns = [
        "marketCap_current",
        "enterpriseValue_current",
        "trailingPE_current",
        "forwardPE_current",
        "priceToSales_current",
        "priceToBook_current",
        "enterpriseToEbitda_current",
    ]
    return {
        "rows_total": len(rows),
        "columns": len(rows[0]) if rows else 0,
        "date_counts": dict(sorted(date_counts.items())),
        "expected_date": expected_date,
        "expected_date_rows": len(expected_rows),
        "expected_market_counts": dict(Counter(row.get("market") for row in expected_rows)),
        "duplicate_date_market_symbol": duplicate_keys,
        "bshare_count": len(b_shares),
        "cn_non_cny_count": len(cn_non_cny),
        "expected_date_regular_market_time_mismatches": len(date_mismatches),
        "coverage": {column: coverage_rate(expected_rows, column) for column in coverage_columns},
    }


def warning_status(summary: dict[str, Any] | None) -> dict[str, Any]:
    if not summary:
        return {"kind": "unknown", "reason": "refresh summary not found in run log"}
    accepted = int(summary.get("universe", {}).get("accepted_symbols") or 0)
    records = int(summary.get("records") or 0)
    warning_count = int(summary.get("warning_count") or 0)
    missing_valuation = max(accepted - records, 0)
    expected_warning_count = missing_valuation * 2
    return {
        "kind": "coverage_gap" if warning_count == expected_warning_count else "mixed_or_unclassified",
        "accepted_symbols": accepted,
        "records": records,
        "missing_valuation_symbols": missing_valuation,
        "warning_count": warning_count,
        "expected_coverage_gap_warnings": expected_warning_count,
    }


def quality_is_ok(quality: dict[str, Any]) -> bool:
    if not quality:
        return True
    return (
        quality.get("rows_total", 0) > 0
        and quality.get("duplicate_date_market_symbol") == 0
        and quality.get("bshare_count") == 0
        and quality.get("cn_non_cny_count") == 0
        and quality.get("expected_date_regular_market_time_mismatches") == 0
    )


def status_line(result: dict[str, Any]) -> str:
    quality = result.get("quality") or {}
    warnings = result.get("warnings") or {}
    run = result.get("run") or {}
    fields = [
        f"STATUS={result.get('status')}",
        f"date={quality.get('expected_date') or result.get('ny_date')}",
        f"run={run.get('databaseId')}",
        f"run_status={run.get('status')}",
        f"conclusion={run.get('conclusion')}",
        f"rows={quality.get('expected_date_rows')}",
        f"total_rows={quality.get('rows_total')}",
        f"dups={quality.get('duplicate_date_market_symbol')}",
        f"bshares={quality.get('bshare_count')}",
        f"cn_non_cny={quality.get('cn_non_cny_count')}",
        f"date_mismatch={quality.get('expected_date_regular_market_time_mismatches')}",
        f"warnings={warnings.get('kind')}",
        f"missing_valuation={warnings.get('missing_valuation_symbols')}",
    ]
    if run.get("url"):
        fields.append(f"url={run.get('url')}")
    return " ".join(fields)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trigger and validate the daily market valuation GitHub refresh.")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--max-per-market", default="0")
    parser.add_argument("--sleep-seconds", default="0.2")
    parser.add_argument("--wait", action="store_true", help="Wait until the run completes, pull, and validate the dataset.")
    parser.add_argument("--timeout-minutes", type=int, default=120)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--json", action="store_true", help="Print full JSON after the one-line status.")
    parser.add_argument("--no-pull", action="store_true", help="Skip git pull after completion.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    ny_date = current_ny_date()
    result: dict[str, Any] = {"ny_date": ny_date, "triggered": False}
    runs = list_runs(args.repo, args.workflow)
    run = find_today_run(runs, ny_date=ny_date, branch=args.branch)
    if run is None:
        trigger_workflow(
            args.repo,
            args.workflow,
            args.branch,
            max_per_market=args.max_per_market,
            sleep_seconds=args.sleep_seconds,
        )
        time.sleep(5)
        run = find_today_run(list_runs(args.repo, args.workflow), ny_date=ny_date, branch=args.branch)
        result["triggered"] = True
    if run is None:
        raise RuntimeError("workflow dispatch returned but no current-day run was found")

    result["run"] = run
    if args.wait and run.get("status") != "completed":
        run = wait_for_run(
            args.repo,
            int(run["databaseId"]),
            timeout_seconds=args.timeout_minutes * 60,
            poll_seconds=args.poll_seconds,
        )
        result["run"] = run

    if run.get("status") == "completed":
        summary = run_log_summary(args.repo, int(run["databaseId"]))
        result["refresh_summary"] = summary
        result["warnings"] = warning_status(summary)
        if run.get("conclusion") == "success" and not args.no_pull:
            git_pull(args.branch)
        if args.dataset.exists():
            result["quality"] = validate_dataset(args.dataset, expected_date=ny_date)

    quality = result.get("quality") or {}
    run_ok = result["run"].get("status") != "completed" or result["run"].get("conclusion") == "success"
    quality_ok = quality_is_ok(quality)
    result["status"] = "ok" if run_ok and quality_ok else "needs_attention"

    print(status_line(result))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"STATUS=error error={type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
