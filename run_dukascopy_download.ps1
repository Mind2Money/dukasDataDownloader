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
  [switch]$NoShrink,
  [double]$SpreadMultiplier = 0.10,
  [string]$SpreadOutputSymbol = "",
  [string]$SpreadOutputDir = "",
  [int]$SpreadDigits = -1,
  [switch]$WriteSpreadMonthly,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

if (-not $OutputDir) {
  $OutputDir = Join-Path $ScriptDir "data"
}

$Instrument = $Instrument.Trim().ToUpperInvariant().Replace("/", "")
if (-not $SymbolName) {
  $SymbolName = "${Instrument}_DUKAS"
}

$ArgsList = @(
  "--instrument", $Instrument,
  "--symbol-name", $SymbolName,
  "--from", $From,
  "--to", $To,
  "--output-dir", $OutputDir,
  "--download-workers", "$DownloadWorkers"
)

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
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

if ($PlanOnly -or $DownloadOnly -or $NoShrink) {
  exit 0
}

$SpreadPct = [int][Math]::Round($SpreadMultiplier * 100)
if (-not $SpreadOutputSymbol) {
  $SpreadOutputSymbol = "${SymbolName}_SPREAD$SpreadPct"
}

$ShrinkArgs = @(
  "--source-symbol", $SymbolName,
  "--output-symbol", $SpreadOutputSymbol,
  "--data-dir", $OutputDir,
  "--spread-multiplier", "$SpreadMultiplier"
)

if ($SpreadDigits -ge 0) {
  $ShrinkArgs += @("--digits", "$SpreadDigits")
}
elseif ($Digits -ge 0) {
  $ShrinkArgs += @("--digits", "$Digits")
}
else {
  $ShrinkArgs += @("--digits", "2")
}

if ($SpreadOutputDir) {
  $ShrinkArgs += @("--output-dir", $SpreadOutputDir)
}
if ($WriteSpreadMonthly) {
  $ShrinkArgs += "--write-monthly"
}

python (Join-Path $ScriptDir "shrink_spread.py") @ShrinkArgs
exit $LASTEXITCODE
