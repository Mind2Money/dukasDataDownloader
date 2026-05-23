param(
  [string]$Instrument = "XAUUSD",
  [string]$SymbolName = "",
  [string]$From = "auto",
  [string]$To = "now",
  [string]$OutputDir = "",
  [int]$DownloadWorkers = 12,
  [double]$PriceScale = 0,
  [int]$Digits = -1,
  [switch]$RefreshEarliest,
  [switch]$RefreshMarkers,
  [switch]$Force,
  [switch]$DownloadOnly,
  [switch]$ConvertOnly,
  [switch]$PlanOnly,
  [switch]$SkipTotal,
  [switch]$RebuildCsv,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

if (-not $OutputDir) {
  $OutputDir = Join-Path $ScriptDir "data"
}

$ArgsList = @(
  "--instrument", $Instrument,
  "--from", $From,
  "--to", $To,
  "--output-dir", $OutputDir,
  "--download-workers", "$DownloadWorkers"
)

if ($SymbolName) {
  $ArgsList += @("--symbol-name", $SymbolName)
}
if ($PriceScale -gt 0) {
  $ArgsList += @("--price-scale", "$PriceScale")
}
if ($Digits -ge 0) {
  $ArgsList += @("--digits", "$Digits")
}
if ($RefreshEarliest) {
  $ArgsList += "--refresh-earliest"
}
if ($RefreshMarkers) {
  $ArgsList += "--refresh-markers"
}
if ($Force) {
  $ArgsList += "--force"
}
if ($DownloadOnly) {
  $ArgsList += "--download-only"
}
if ($ConvertOnly) {
  $ArgsList += "--convert-only"
}
if ($PlanOnly) {
  $ArgsList += "--plan-only"
}
if ($SkipTotal) {
  $ArgsList += "--skip-total"
}
if ($RebuildCsv) {
  $ArgsList += "--rebuild-csv"
}
if ($RemainingArgs) {
  $ArgsList += $RemainingArgs
}

python (Join-Path $ScriptDir "dukas_downloader.py") @ArgsList
exit $LASTEXITCODE
