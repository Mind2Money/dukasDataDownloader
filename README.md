# Dukascopy Data Downloader

Standalone downloader for Dukascopy tick data. It downloads raw hourly `.bi5`
files incrementally and converts them into MetaTrader 5 tick CSV files.
The single PowerShell entrypoint calls both Python helpers:

1. `dukas_downloader.py` downloads and converts Dukascopy data.
2. `shrink_spread.py` creates the reduced-spread CSV variant.

## Default behavior

Running the PowerShell entrypoint with no arguments:

```powershell
.\run_dukascopy_download.ps1
```

defaults to:

- instrument: `XAUUSD`
- symbol name: `XAUUSD_DUKAS`
- reduced-spread symbol name: `XAUUSD_DUKAS_SPREAD10`
- start: `auto`, discovered from remote Dukascopy data
- end: current complete UTC hour
- mode: incremental
- output: `E:\Documents\Repos\Trading\dukasDataDownloader\data`
- spread multiplier: `0.10`

Existing `.bi5` files and `.empty` / `.missing` markers are skipped. Temporary
HTTP errors are retried and listed in `failed_hours.csv`.

## Common usage

Download another instrument:

```powershell
.\run_dukascopy_download.ps1 -Instrument EURUSD
```

This automatically uses `EURUSD_DUKAS` as the MT5 symbol and
`EURUSD_DUKAS_SPREAD10` as the reduced-spread output symbol.

Use an explicit MT5 custom symbol name:

```powershell
.\run_dukascopy_download.ps1 -Instrument EURUSD -SymbolName EURUSD_DUKAS
```

Download from a fixed date:

```powershell
.\run_dukascopy_download.ps1 -Instrument XAUUSD -From 2020-01-01
```

Force a complete raw redownload:

```powershell
.\run_dukascopy_download.ps1 -Instrument XAUUSD -Force
```

Rebuild CSV files from existing raw files without downloading:

```powershell
.\run_dukascopy_download.ps1 -Instrument XAUUSD -ConvertOnly -RebuildCsv
```

Check the resolved plan without downloading:

```powershell
.\run_dukascopy_download.ps1 -Instrument XAUUSD -From 2020-01-01 -To 2020-01-03 -PlanOnly
```

Only download raw `.bi5` files:

```powershell
.\run_dukascopy_download.ps1 -Instrument XAUUSD -DownloadOnly
```

Skip spread reduction:

```powershell
.\run_dukascopy_download.ps1 -Instrument XAUUSD -NoShrink
```

Use a different spread multiplier:

```powershell
.\run_dukascopy_download.ps1 -Instrument XAUUSD -SpreadMultiplier 0.25
```

Write both monthly adjusted spread files and the combined spread file:

```powershell
.\run_dukascopy_download.ps1 -Instrument XAUUSD -WriteSpreadMonthly
```

Use explicit output symbols:

```powershell
.\run_dukascopy_download.ps1 -Instrument XAUUSD -SymbolName XAUUSD_DUKAS -SpreadOutputSymbol XAUUSD_DUKAS_SPREAD10
```

## Output layout

```text
data/
  raw/<INSTRUMENT>/YYYY/MM/DD/HHh_ticks.bi5
  mt5_ticks/<SYMBOL>_YYYY_MM_ticks.csv
  mt5_ticks/<SYMBOL>_ALL_ticks.csv
  metadata.json
  MT5_IMPORT_NOTES.txt
  failed_hours.csv
  mt5_ticks_spread10/<SYMBOL>_SPREAD10_ALL_ticks.csv
```

## Price scale presets

The script has built-in presets for common instruments:

- `XAUUSD`: scale `1000`, digits `2`
- `XAGUSD`: scale `1000`, digits `2`
- normal FX pairs like `EURUSD`: scale `100000`, digits `2`
- JPY FX pairs like `USDJPY`: scale `1000`, digits `2`

Override when needed:

```powershell
.\run_dukascopy_download.ps1 -Instrument BTCUSD -PriceScale 100 -Digits 2
```

## Python entrypoint

The PowerShell file is a wrapper around:

```powershell
python .\dukas_downloader.py --instrument XAUUSD --from auto --to now
python .\shrink_spread.py --source-symbol XAUUSD_DUKAS
```

Useful Python-only flags:

```text
--instrument SYMBOL
--symbol-name MT5_SYMBOL
--from auto|YYYY-MM-DD
--to now|YYYY-MM-DD
--output-dir PATH
--download-workers N
--price-scale N
--digits N
--download-only
--convert-only
--plan-only
--skip-total
--rebuild-csv
--refresh-earliest
--refresh-markers
--force
--spread-multiplier N
--no-shrink
```
