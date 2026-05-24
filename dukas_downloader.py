#!/usr/bin/env python3
"""Download Dukascopy tick data incrementally and prepare MT5 CSV files."""

from __future__ import annotations

import argparse
import calendar
import concurrent.futures
import csv
import datetime as dt
import json
import lzma
import math
import struct
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_INSTRUMENT = "XAUUSD"
DEFAULT_FROM = "auto"
DEFAULT_TO = "now"
DEFAULT_OUTPUT = "data"
DEFAULT_EARLIEST_SEARCH_FROM = "2003-01-01"
EARLIEST_PROBE_CACHE_NAME = "earliest_probe_cache.json"
DUKASCOPY_BASE_URL = "https://datafeed.dukascopy.com/datafeed"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) dukasDataDownloader/1.0"
PROBE_TIMEOUT_SECONDS = 5


DEFAULT_DIGITS = 2


PRICE_PRESETS: dict[str, tuple[float, int]] = {
    "XAUUSD": (1000.0, DEFAULT_DIGITS),
    "XAGUSD": (1000.0, DEFAULT_DIGITS),
    "USOIL": (1000.0, DEFAULT_DIGITS),
    "BRENTCMDUSD": (1000.0, DEFAULT_DIGITS),
}


@dataclass(frozen=True)
class HourJob:
    hour: dt.datetime
    url: str
    raw_path: Path


@dataclass(frozen=True)
class Tick:
    time: dt.datetime
    bid: float
    ask: float
    volume: int


def parse_date(value: str) -> dt.date:
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"Invalid date: {value}. Use YYYY-MM-DD.")


def utc_now_hour() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0, tzinfo=None)


def normalize_instrument(value: str) -> str:
    instrument = value.strip().upper().replace("/", "")
    if not instrument:
        raise argparse.ArgumentTypeError("Instrument cannot be empty.")
    return instrument


def default_symbol_name(instrument: str) -> str:
    return f"{instrument}_DUKAS"


def infer_price_settings(instrument: str) -> tuple[float, int]:
    instrument = instrument.upper()
    if instrument in PRICE_PRESETS:
        return PRICE_PRESETS[instrument]
    if len(instrument) == 6 and instrument.endswith("JPY"):
        return 1000.0, DEFAULT_DIGITS
    if len(instrument) == 6:
        return 100000.0, DEFAULT_DIGITS
    return 100000.0, DEFAULT_DIGITS


def dukascopy_url(instrument: str, hour: dt.datetime) -> str:
    # Dukascopy URL months are zero-based.
    return (
        f"{DUKASCOPY_BASE_URL}/{instrument.upper()}/"
        f"{hour.year:04d}/{hour.month - 1:02d}/{hour.day:02d}/"
        f"{hour.hour:02d}h_ticks.bi5"
    )


def raw_path_for(output_dir: Path, instrument: str, hour: dt.datetime) -> Path:
    return (
        output_dir
        / "raw"
        / instrument.upper()
        / f"{hour.year:04d}"
        / f"{hour.month:02d}"
        / f"{hour.day:02d}"
        / f"{hour.hour:02d}h_ticks.bi5"
    )


def marker_path_for(raw_path: Path, status: str) -> Path:
    return raw_path.with_suffix(raw_path.suffix + f".{status}")


def clear_terminal_markers(raw_path: Path) -> None:
    for status in ("empty", "missing"):
        path = marker_path_for(raw_path, status)
        if path.exists():
            path.unlink()


def month_key(value: dt.datetime) -> tuple[int, int]:
    return value.year, value.month


def month_start(value: dt.date) -> dt.date:
    return dt.date(value.year, value.month, 1)


def next_month(value: dt.date) -> dt.date:
    if value.month == 12:
        return dt.date(value.year + 1, 1, 1)
    return dt.date(value.year, value.month + 1, 1)


def iter_months(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    current = month_start(start)
    while current <= month_start(end):
        yield current
        current = next_month(current)


def iter_hours(start_hour: dt.datetime, end_hour: dt.datetime) -> list[dt.datetime]:
    hours: list[dt.datetime] = []
    current = start_hour.replace(minute=0, second=0, microsecond=0)
    end = end_hour.replace(minute=0, second=0, microsecond=0)
    while current < end:
        hours.append(current)
        current += dt.timedelta(hours=1)
    return hours


def month_sample_hours(month: dt.date, end_hour: dt.datetime) -> Iterable[dt.datetime]:
    days_in_month = calendar.monthrange(month.year, month.month)[1]
    sample_days = sorted({1, 2, 8, 15, 22, days_in_month})
    sample_hours = (0, 6, 12, 18, 23)
    for day in sample_days:
        if day > days_in_month:
            continue
        for hour in sample_hours:
            candidate = dt.datetime(month.year, month.month, day, hour)
            if candidate < end_hour:
                yield candidate


def earliest_probe_cache_path(output_dir: Path) -> Path:
    return output_dir / EARLIEST_PROBE_CACHE_NAME


def month_cache_key(month: dt.date) -> str:
    return month.strftime("%Y-%m")


def load_earliest_probe_cache(output_dir: Path, instrument: str) -> dict[str, object]:
    path = earliest_probe_cache_path(output_dir)
    if not path.exists():
        return {}
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(cache, dict) or cache.get("instrument") != instrument:
        return {}

    month_results = cache.get("month_results")
    if not isinstance(month_results, dict):
        month_results = {}
    cache["month_results"] = {
        str(key): value for key, value in month_results.items() if isinstance(value, bool)
    }
    return cache


def write_earliest_probe_cache(output_dir: Path, instrument: str, cache: dict[str, object]) -> None:
    cache["version"] = 1
    cache["instrument"] = instrument
    cache["updated_at_utc"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    cache.setdefault("month_results", {})

    path = earliest_probe_cache_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def cache_earliest_hour(
    output_dir: Path,
    instrument: str,
    search_from: dt.date,
    earliest_hour: dt.datetime,
    cache: dict[str, object] | None = None,
) -> None:
    probe_cache = cache if cache is not None else load_earliest_probe_cache(output_dir, instrument)
    probe_cache["earliest_search_from"] = search_from.isoformat()
    probe_cache["earliest_month"] = f"{earliest_hour.year:04d}-{earliest_hour.month:02d}"
    probe_cache["earliest_hour"] = earliest_hour.isoformat()
    write_earliest_probe_cache(output_dir, instrument, probe_cache)


def cached_earliest_hour(
    cache: dict[str, object],
    search_from: dt.date,
    end_hour: dt.datetime,
) -> dt.datetime | None:
    earliest_text = cache.get("earliest_hour")
    search_from_text = cache.get("earliest_search_from")
    if not isinstance(earliest_text, str) or not isinstance(search_from_text, str):
        return None
    try:
        earliest_hour = dt.datetime.fromisoformat(earliest_text)
        cached_search_from = parse_date(search_from_text)
    except Exception:
        return None

    requested_start = dt.datetime.combine(search_from, dt.time.min)
    if cached_search_from <= search_from and requested_start <= earliest_hour < end_hour:
        return earliest_hour
    return None


def metadata_earliest_hour(
    metadata_path: Path,
    instrument: str,
    search_from: dt.date,
    end_hour: dt.datetime,
) -> dt.datetime | None:
    if not metadata_path.exists():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("instrument") != instrument or not metadata.get("earliest_hour"):
            return None
        earliest_hour = dt.datetime.fromisoformat(metadata["earliest_hour"])
    except Exception:
        return None

    requested_start = dt.datetime.combine(search_from, dt.time.min)
    if requested_start <= earliest_hour < end_hour:
        return earliest_hour
    return None


def http_has_data(url: str, timeout: int = PROBE_TIMEOUT_SECONDS) -> bool | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return bool(response.read(1))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        return None
    except Exception:
        return None


def probe_hour(instrument: str, hour: dt.datetime, probes: dict[dt.datetime, bool | None]) -> bool | None:
    if hour in probes:
        return probes[hour]
    result = http_has_data(dukascopy_url(instrument, hour))
    probes[hour] = result
    return result


def month_data_status(instrument: str, month: dt.date, end_hour: dt.datetime, probes: dict[dt.datetime, bool | None]) -> bool | None:
    saw_unknown = False
    for hour in month_sample_hours(month, end_hour):
        result = probe_hour(instrument, hour, probes)
        if result is True:
            return True
        if result is None:
            saw_unknown = True
    if saw_unknown:
        return None
    return False


def discover_earliest_hour(
    instrument: str,
    search_from: dt.date,
    end_hour: dt.datetime,
    output_dir: Path,
    probe_cache: dict[str, object],
) -> dt.datetime:
    probes: dict[dt.datetime, bool | None] = {}
    month_results = probe_cache.setdefault("month_results", {})
    if not isinstance(month_results, dict):
        month_results = {}
        probe_cache["month_results"] = month_results

    first_month: dt.date | None = None
    for month in iter_months(search_from, end_hour.date()):
        month_key_text = month_cache_key(month)
        cached_month_result = month_results.get(month_key_text)
        if isinstance(cached_month_result, bool):
            print(f"Probe earliest month: {month_key_text} (cached)", flush=True)
            if cached_month_result:
                first_month = month
                break
            continue

        print(f"Probe earliest month: {month_key_text}", flush=True)
        status = month_data_status(instrument, month, end_hour, probes)
        if status is not None:
            month_results[month_key_text] = status
            write_earliest_probe_cache(output_dir, instrument, probe_cache)
        if status is True:
            first_month = month
            break
    if first_month is None:
        raise SystemExit(f"No remote data found for {instrument} before {end_hour}.")

    days_in_month = calendar.monthrange(first_month.year, first_month.month)[1]
    for day in range(1, days_in_month + 1):
        for hour in range(24):
            candidate = dt.datetime(first_month.year, first_month.month, day, hour)
            if candidate < dt.datetime.combine(search_from, dt.time.min):
                continue
            if candidate >= end_hour:
                raise SystemExit(f"No complete hour found for {instrument} before {end_hour}.")
            result = probe_hour(instrument, candidate, probes)
            if result is True:
                cache_earliest_hour(output_dir, instrument, search_from, candidate, probe_cache)
                return candidate
    raise SystemExit(f"Could not identify earliest hour for {instrument}.")


def resolve_start_hour(args: argparse.Namespace, instrument: str, end_hour: dt.datetime) -> dt.datetime:
    value = args.from_value.strip().lower()
    if value == "auto":
        search_from = parse_date(args.earliest_search_from)
        probe_cache: dict[str, object] = {}
        metadata_path = args.output_dir / "metadata.json"
        if args.incremental and not args.refresh_earliest:
            probe_cache = load_earliest_probe_cache(args.output_dir, instrument)
            start_hour = cached_earliest_hour(probe_cache, search_from, end_hour)
            if start_hour is not None:
                print(f"Using cached earliest hour: {start_hour} ({earliest_probe_cache_path(args.output_dir)})")
                return start_hour

            start_hour = metadata_earliest_hour(metadata_path, instrument, search_from, end_hour)
            if start_hour is not None:
                print(f"Using cached earliest hour: {start_hour} ({metadata_path})")
                return start_hour

        return discover_earliest_hour(instrument, search_from, end_hour, args.output_dir, probe_cache)
    return dt.datetime.combine(parse_date(value), dt.time.min)


def resolve_end_hour(value: str) -> dt.datetime:
    text = value.strip().lower()
    now_hour = utc_now_hour()
    if text == "now":
        return now_hour
    end_date = parse_date(text)
    requested = dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time.min)
    return min(requested, now_hour)


def csv_path_for(output_dir: Path, symbol_name: str, year: int, month: int) -> Path:
    return output_dir / "mt5_ticks" / f"{symbol_name}_{year:04d}_{month:02d}_ticks.csv"


def total_csv_path_for(output_dir: Path, symbol_name: str) -> Path:
    return output_dir / "mt5_ticks" / f"{symbol_name}_ALL_ticks.csv"


def download_hour(
    job: HourJob,
    retries: int,
    sleep_seconds: float,
    incremental: bool,
    refresh_markers: bool,
) -> tuple[HourJob, str]:
    if incremental and job.raw_path.exists() and job.raw_path.stat().st_size > 0:
        return job, "cached"
    if incremental and not refresh_markers:
        if marker_path_for(job.raw_path, "empty").exists():
            return job, "cached_empty"
        if marker_path_for(job.raw_path, "missing").exists():
            return job, "cached_missing"

    job.raw_path.parent.mkdir(parents=True, exist_ok=True)
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(job.url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            if not data:
                marker_path_for(job.raw_path, "empty").write_text(job.url + "\n", encoding="utf-8")
                return job, "empty"
            tmp_path = job.raw_path.with_suffix(job.raw_path.suffix + ".tmp")
            tmp_path.write_bytes(data)
            tmp_path.replace(job.raw_path)
            clear_terminal_markers(job.raw_path)
            return job, "downloaded"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                marker_path_for(job.raw_path, "missing").write_text(job.url + "\n", encoding="utf-8")
                return job, "missing"
            last_error = f"HTTP {exc.code}"
        except Exception as exc:
            last_error = repr(exc)
        time.sleep(sleep_seconds * attempt)

    return job, f"error:{last_error}"


def parse_bi5_ticks(path: Path, hour: dt.datetime, price_scale: float, volume_mode: str) -> list[Tick]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        raw = lzma.decompress(path.read_bytes())
    except lzma.LZMAError:
        return []
    if len(raw) % 20 != 0:
        raw = raw[: len(raw) - (len(raw) % 20)]

    ticks: list[Tick] = []
    base = hour.replace(minute=0, second=0, microsecond=0)
    for offset in range(0, len(raw), 20):
        ms, ask_i, bid_i, ask_volume, bid_volume = struct.unpack(">3I2f", raw[offset : offset + 20])
        tick_time = base + dt.timedelta(milliseconds=ms)
        bid = bid_i / price_scale
        ask = ask_i / price_scale
        if bid <= 0 or ask <= 0:
            continue
        if volume_mode == "sum":
            volume = max(0, int(round((ask_volume + bid_volume) * 1_000_000)))
        else:
            volume = 0
        ticks.append(Tick(time=tick_time, bid=bid, ask=ask, volume=volume))
    return ticks


def write_month_csv(
    output_dir: Path,
    symbol_name: str,
    year: int,
    month: int,
    hour_paths: list[tuple[dt.datetime, Path]],
    price_scale: float,
    digits: int,
    volume_mode: str,
    include_header: bool,
) -> tuple[Path, int]:
    target_path = csv_path_for(output_dir, symbol_name, year, month)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    rows_written = 0
    price_format = f"{{:.{digits}f}}"

    with tmp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if include_header:
            writer.writerow(["Date", "Time", "Bid", "Ask", "Last", "Volume"])
        for hour, raw_path in sorted(hour_paths, key=lambda item: item[0]):
            for tick in parse_bi5_ticks(raw_path, hour, price_scale, volume_mode):
                writer.writerow(
                    [
                        tick.time.strftime("%Y.%m.%d"),
                        tick.time.strftime("%H:%M:%S.%f")[:-3],
                        price_format.format(tick.bid),
                        price_format.format(tick.ask),
                        "0",
                        tick.volume,
                    ]
                )
                rows_written += 1

    tmp_path.replace(target_path)
    return target_path, rows_written


def write_total_csv(month_csv_paths: list[Path], target_path: Path) -> tuple[Path, int]:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    rows_written = 0

    with tmp_path.open("w", newline="", encoding="utf-8") as output:
        for source_path in sorted(month_csv_paths):
            if not source_path.exists():
                continue
            with source_path.open("r", newline="", encoding="utf-8") as source:
                for line in source:
                    output.write(line)
                    if line.strip():
                        rows_written += 1

    tmp_path.replace(target_path)
    return target_path, rows_written


def write_metadata(
    output_dir: Path,
    instrument: str,
    symbol_name: str,
    start_hour: dt.datetime,
    end_hour: dt.datetime,
    price_scale: float,
    digits: int,
) -> None:
    metadata = {
        "instrument": instrument,
        "symbol_name": symbol_name,
        "earliest_hour": start_hour.isoformat(),
        "last_requested_end_hour": end_hour.isoformat(),
        "price_scale": price_scale,
        "digits": digits,
        "updated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def write_import_notes(output_dir: Path, symbol_name: str, instrument: str, price_scale: float, digits: int) -> None:
    notes = f"""Dukascopy MT5 tick data is ready.

Instrument: {instrument}
MT5 custom symbol suggestion: {symbol_name}
Price scale: {price_scale:g}
Digits: {digits}

Raw cache:
  {output_dir / "raw" / instrument}

Monthly MT5 CSV files:
  {output_dir / "mt5_ticks"}

Combined CSV:
  {total_csv_path_for(output_dir, symbol_name)}

CSV columns:
  Date, Time, Bid, Ask, Last, Volume

Recommended MT5 import flow:
1. Open MT5 and press Ctrl+U.
2. Create a custom symbol from a broker symbol with similar contract specs.
3. Use custom symbol name: {symbol_name}
4. Import either {symbol_name}_ALL_ticks.csv or the monthly *_ticks.csv files.
5. Show the custom symbol in Market Watch before running strategy tests.

This downloader is incremental by default. Existing .bi5 files and terminal
empty/missing markers are skipped unless --force or --refresh-markers is used.
"""
    (output_dir / "MT5_IMPORT_NOTES.txt").write_text(notes, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incrementally download Dukascopy tick data and prepare MT5 CSV files.")
    parser.add_argument("--instrument", default=DEFAULT_INSTRUMENT, type=normalize_instrument)
    parser.add_argument("--symbol-name", default=None, help="MT5 custom symbol name. Defaults to <instrument>_DUKAS.")
    parser.add_argument("--from", dest="from_value", default=DEFAULT_FROM, help="YYYY-MM-DD or auto. Default: auto.")
    parser.add_argument("--to", dest="to_value", default=DEFAULT_TO, help="YYYY-MM-DD or now. Default: now.")
    parser.add_argument("--earliest-search-from", default=DEFAULT_EARLIEST_SEARCH_FROM)
    parser.add_argument("--refresh-earliest", action="store_true", help="Ignore cached metadata.json earliest hour.")
    parser.add_argument("--output-dir", type=Path, default=Path(DEFAULT_OUTPUT))
    parser.add_argument("--price-scale", type=float, default=None, help="Dukascopy integer price divisor.")
    parser.add_argument("--digits", type=int, default=None, help="Decimal places for MT5 CSV bid/ask.")
    parser.add_argument("--download-workers", type=int, default=12)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--retry-sleep", type=float, default=1.0)
    parser.add_argument("--volume-mode", choices=["zero", "sum"], default="zero")
    parser.add_argument("--max-hours", type=int, default=0, help="Debug limit. 0 means no limit.")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--convert-only", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="Print the resolved plan and exit without downloading.")
    parser.add_argument("--skip-total", action="store_true", help="Do not rebuild the combined ALL CSV.")
    parser.add_argument("--rebuild-csv", action="store_true", help="Rebuild all monthly CSV files in the selected range.")
    parser.add_argument("--include-header", action="store_true", help="Write CSV header rows.")
    parser.add_argument("--refresh-markers", action="store_true", help="Recheck hours marked empty/missing.")
    parser.add_argument("--force", action="store_true", help="Redownload raw .bi5 files even if cached.")
    parser.set_defaults(incremental=True)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.download_workers < 1:
        raise SystemExit("--download-workers must be >= 1")
    if args.retries < 1:
        raise SystemExit("--retries must be >= 1")
    if args.price_scale is not None and args.price_scale <= 0:
        raise SystemExit("--price-scale must be > 0")
    if args.digits is not None and not 0 <= args.digits <= 10:
        raise SystemExit("--digits must be between 0 and 10")


def main() -> int:
    args = parse_args()
    validate_args(args)

    instrument = args.instrument
    symbol_name = args.symbol_name or default_symbol_name(instrument)
    preset_scale, preset_digits = infer_price_settings(instrument)
    price_scale = args.price_scale if args.price_scale is not None else preset_scale
    digits = args.digits if args.digits is not None else preset_digits
    output_dir = args.output_dir.resolve()
    args.output_dir = output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    end_hour = resolve_end_hour(args.to_value)
    start_hour = resolve_start_hour(args, instrument, end_hour)
    if end_hour <= start_hour:
        raise SystemExit(f"End hour {end_hour} must be after start hour {start_hour}.")

    hours = iter_hours(start_hour, end_hour)
    if args.max_hours > 0:
        hours = hours[: args.max_hours]

    jobs = [
        HourJob(
            hour=hour,
            url=dukascopy_url(instrument, hour),
            raw_path=raw_path_for(output_dir, instrument, hour),
        )
        for hour in hours
    ]

    print(f"Instrument: {instrument}")
    print(f"Symbol name: {symbol_name}")
    print(f"Range UTC: {start_hour} -> {end_hour} ({len(jobs)} hours)")
    print(f"Output: {output_dir}")
    print(f"Mode: {'force' if args.force else 'incremental'}")
    print(f"Price scale: {price_scale:g} | digits: {digits}")
    if args.plan_only:
        print("Plan-only complete. No files were downloaded or converted.")
        return 0

    status_counts: dict[str, int] = {}
    failed_rows: list[tuple[dt.datetime, str, str]] = []
    changed_months: set[tuple[int, int]] = set()
    incremental = args.incremental and not args.force

    if not args.convert_only:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.download_workers) as executor:
            futures = [
                executor.submit(
                    download_hour,
                    job,
                    args.retries,
                    args.retry_sleep,
                    incremental,
                    args.refresh_markers,
                )
                for job in jobs
            ]
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                job, status = future.result()
                status_counts[status] = status_counts.get(status, 0) + 1
                if status == "downloaded":
                    changed_months.add(month_key(job.hour))
                if status.startswith("error:"):
                    failed_rows.append((job.hour, job.url, status))
                if index % 250 == 0 or index == len(futures):
                    print(f"Checked {index}/{len(futures)} hours: {status_counts}", flush=True)

        failed_path = output_dir / "failed_hours.csv"
        if failed_rows:
            with failed_path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(["hour_utc", "url", "status"])
                for hour, url, status in sorted(failed_rows, key=lambda item: item[0]):
                    writer.writerow([hour.isoformat(sep=" "), url, status])
            print(f"Failed hours written to: {failed_path}")
        elif failed_path.exists():
            failed_path.unlink()

    if args.download_only:
        write_metadata(output_dir, instrument, symbol_name, start_hour, end_hour, price_scale, digits)
        write_import_notes(output_dir, symbol_name, instrument, price_scale, digits)
        print("Download-only complete.")
        return 0

    grouped: dict[tuple[int, int], list[tuple[dt.datetime, Path]]] = {}
    for job in jobs:
        if job.raw_path.exists() and job.raw_path.stat().st_size > 0:
            grouped.setdefault(month_key(job.hour), []).append((job.hour, job.raw_path))

    month_csv_paths = [csv_path_for(output_dir, symbol_name, year, month) for year, month in sorted(grouped)]
    months_written = 0
    ticks_written = 0
    for (year, month), hour_paths in sorted(grouped.items()):
        target_path = csv_path_for(output_dir, symbol_name, year, month)
        should_write = args.rebuild_csv or (year, month) in changed_months or not target_path.exists()
        if not should_write:
            continue
        target_path, rows = write_month_csv(
            output_dir=output_dir,
            symbol_name=symbol_name,
            year=year,
            month=month,
            hour_paths=hour_paths,
            price_scale=price_scale,
            digits=digits,
            volume_mode=args.volume_mode,
            include_header=args.include_header,
        )
        months_written += 1
        ticks_written += rows
        print(f"Wrote {target_path} ({rows:,} ticks)", flush=True)

    total_path = total_csv_path_for(output_dir, symbol_name)
    if not args.skip_total:
        should_write_total = args.rebuild_csv or months_written > 0 or not total_path.exists()
        if should_write_total:
            total_path, combined_ticks = write_total_csv(month_csv_paths, total_path)
            print(f"Wrote {total_path} ({combined_ticks:,} rows)")
        else:
            print(f"Combined CSV is already current: {total_path}")

    write_metadata(output_dir, instrument, symbol_name, start_hour, end_hour, price_scale, digits)
    write_import_notes(output_dir, symbol_name, instrument, price_scale, digits)
    print(f"Done. Monthly files written: {months_written}; ticks converted this run: {ticks_written:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
