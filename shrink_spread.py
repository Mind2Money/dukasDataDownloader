#!/usr/bin/env python3
"""Create reduced-spread MT5 tick CSV files from Dukascopy monthly CSV files."""

from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path


DEFAULT_DATA_DIR = Path("data")
DEFAULT_DIGITS = 2


def output_symbol_default(source_symbol: str, spread_multiplier: float) -> str:
    pct = int(round(spread_multiplier * 100))
    return f"{source_symbol}_SPREAD{pct}"


def find_month_files(input_dir: Path, source_symbol: str) -> list[Path]:
    pattern = re.compile(rf"^{re.escape(source_symbol)}_\d{{4}}_\d{{2}}_ticks\.csv$", re.IGNORECASE)
    return sorted(path for path in input_dir.iterdir() if path.is_file() and pattern.match(path.name))


def output_month_name(source_name: str, source_symbol: str, output_symbol: str) -> str:
    return output_symbol + source_name[len(source_symbol) :]


def adjust_row(line: str, multiplier: float, digits: int) -> tuple[str, bool]:
    parts = line.rstrip("\r\n").split(",")
    if len(parts) != 6:
        return line if line.endswith("\n") else line + "\n", False

    if parts[0].strip().lower() == "date":
        return line if line.endswith("\n") else line + "\n", False

    try:
        bid = float(parts[2])
        ask = float(parts[3])
    except ValueError:
        return line if line.endswith("\n") else line + "\n", False

    spread = ask - bid
    if spread > 0:
        ask = bid + spread * multiplier

    parts[3] = f"{ask:.{digits}f}"
    return ",".join(parts) + "\n", True


def convert_file(source_path: Path, output_handle, multiplier: float, digits: int) -> tuple[int, int]:
    rows = 0
    adjusted = 0
    with source_path.open("r", encoding="utf-8", newline="") as input_handle:
        for line in input_handle:
            row, changed = adjust_row(line, multiplier, digits)
            output_handle.write(row)
            rows += 1
            if changed:
                adjusted += 1
    return rows, adjusted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Shrink MT5 tick CSV spread.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--input-dir", type=Path, default=None)
    parser.add_argument("--source-symbol", default="XAUUSD_DUKAS")
    parser.add_argument("--output-symbol", default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--spread-multiplier", type=float, default=0.10)
    parser.add_argument("--digits", type=int, default=DEFAULT_DIGITS)
    parser.add_argument("--write-monthly", action="store_true", help="Also write adjusted monthly CSV files.")
    parser.add_argument("--skip-total", action="store_true", help="Only write monthly files. Implies --write-monthly.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0 <= args.spread_multiplier <= 1:
        raise SystemExit("--spread-multiplier must be between 0 and 1")
    if not 0 <= args.digits <= 10:
        raise SystemExit("--digits must be between 0 and 10")

    data_dir = args.data_dir.resolve()
    input_dir = (args.input_dir or (data_dir / "mt5_ticks")).resolve()
    output_symbol = args.output_symbol or output_symbol_default(args.source_symbol, args.spread_multiplier)
    output_dir = (args.output_dir or (data_dir / f"mt5_ticks_spread{int(round(args.spread_multiplier * 100))}")).resolve()
    write_monthly = args.write_monthly or args.skip_total

    if not input_dir.exists():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    month_files = find_month_files(input_dir, args.source_symbol)
    if not month_files:
        raise SystemExit(f"No monthly tick CSV files found in: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    total_output = output_dir / f"{output_symbol}_ALL_ticks.csv"
    total_tmp = total_output.with_name(total_output.name + ".tmp")
    if total_tmp.exists():
        total_tmp.unlink()

    print(f"Input dir: {input_dir}")
    print(f"Output dir: {output_dir}")
    print(f"Source symbol: {args.source_symbol}")
    print(f"Output symbol: {output_symbol}")
    print(f"Files: {len(month_files)}")
    print(f"Range: {month_files[0].name} -> {month_files[-1].name}")
    print(f"Spread multiplier: {args.spread_multiplier:g}")
    print(f"Digits: {args.digits}")
    if not args.skip_total:
        print(f"Total output: {total_output}")

    started = time.time()
    total_rows = 0
    adjusted_rows = 0

    total_handle = None if args.skip_total else total_tmp.open("w", encoding="utf-8", newline="")
    try:
        for index, source_path in enumerate(month_files, 1):
            month_rows = 0
            month_adjusted = 0

            if write_monthly:
                month_output = output_dir / output_month_name(source_path.name, args.source_symbol, output_symbol)
                month_tmp = month_output.with_name(month_output.name + ".tmp")
                with month_tmp.open("w", encoding="utf-8", newline="") as month_handle:
                    with source_path.open("r", encoding="utf-8", newline="") as input_handle:
                        for line in input_handle:
                            adjusted, changed = adjust_row(line, args.spread_multiplier, args.digits)
                            if total_handle is not None:
                                total_handle.write(adjusted)
                            month_handle.write(adjusted)
                            month_rows += 1
                            if changed:
                                month_adjusted += 1
                os.replace(month_tmp, month_output)
            else:
                assert total_handle is not None
                month_rows, month_adjusted = convert_file(
                    source_path,
                    total_handle,
                    args.spread_multiplier,
                    args.digits,
                )

            total_rows += month_rows
            adjusted_rows += month_adjusted
            elapsed = max(0.001, time.time() - started)
            print(
                f"[{index:03d}/{len(month_files):03d}] {source_path.name} "
                f"({month_rows:,} rows, {total_rows / elapsed:,.0f} rows/s)"
            )
    finally:
        if total_handle is not None:
            total_handle.close()

    if not args.skip_total:
        os.replace(total_tmp, total_output)
        print(f"Done: {total_output}")
        print(f"Output size: {total_output.stat().st_size / (1024 ** 3):.3f} GB")

    print(f"Rows written: {total_rows:,}")
    print(f"Rows adjusted: {adjusted_rows:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
