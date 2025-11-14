<#  deploy.ps1 — verbose sync to CIRCUITPY

Usage examples:
  pwsh -f tools/deploy.ps1 -Detect
  pwsh -f tools/deploy.ps1 -Dst E:\ -DryRun
  pwsh -f tools/deploy.ps1 -Src .\src -Detect -Retry 2 -Wait 1

Notes:
  - Uses ROBOCOPY. Exit codes 0–7 are success; ≥8 are failures.
  - Auto-detects the CIRCUITPY drive with -Detect (by VolumeName).
#>

[CmdletBinding()]
param(
  [string]$Src = (Resolve-Path "$PSScriptRoot\..\src").Path,
  [string]$Dst,
  [switch]$Detect,           # auto-detect CIRCUITPY volume
  [switch]$DryRun,           # don't write, just show what would happen
  [switch]$NoMirror,         # use /E (no deletions) instead of /MIR
  [int]$Retry = 1,           # robocopy /R
  [int]$Wait  = 1,           # robocopy /W
  [switch]$NoLog             # don't write a log file
)

function Get-CircuitPyDrive {
  try {
    $candidates = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=2" |
      Where-Object { $_.VolumeName -eq 'CIRCUITPY' }
    if ($candidates) { return ($candidates | Select-Object -First 1).DeviceID + "\" }
  } catch {}
  return $null
}

# Resolve destination
if ($Detect) {
  $auto = Get-CircuitPyDrive
  if ($auto) {
    $Dst = $auto
  } else {
    Write-Error "CIRCUITPY drive not found. Plug the board in (mass storage enabled) or pass -Dst."
    exit 1
  }
}

if ([string]::IsNullOrWhiteSpace($Dst)) {
  Write-Error "Destination (-Dst) is required (or use -Detect)."
  exit 1
}

# Normalize paths
$Src = (Resolve-Path $Src).Path
if (-not (Test-Path $Src)) { Write-Error "Source not found: $Src"; exit 1 }
if (-not (Test-Path $Dst)) { Write-Error "Destination not found: $Dst"; exit 1 }

# Preflight info
$srcCount = (Get-ChildItem -Force -Recurse -File $Src -ErrorAction SilentlyContinue | Measure-Object).Count
$dstCount = (Get-ChildItem -Force -Recurse -File $Dst -ErrorAction SilentlyContinue | Measure-Object).Count
$ts = (Get-Date).ToString('yyyy-MM-dd_HH-mm-ss')
$logPath = Join-Path $env:TEMP "circuitpy_deploy_$ts.log"

Write-Host "=== CIRCUITPY Deploy ===" -ForegroundColor Cyan
Write-Host "Source:      $Src"
Write-Host "Destination: $Dst"
Write-Host "Detect:      $Detect"
Write-Host "DryRun:      $DryRun"
Write-Host "Mode:        " -NoNewline
if ($NoMirror) { Write-Host "/E (no deletions)" } else { Write-Host "/MIR (mirror, will delete extras at destination)" }
Write-Host "Retry/Wait:  $Retry / $Wait"
if (-not $NoLog) { Write-Host "Log file:    $logPath" } else { Write-Host "Log file:    (disabled)" }
Write-Host "Src files:   $srcCount"
Write-Host "Dst files:   $dstCount"
Write-Host ""

# Build robocopy args
$robocopyArgs = @()
$robocopyArgs += @("$Src", "$Dst")

# Mode: mirror or copy tree
if ($NoMirror) { $robocopyArgs += "/E" } else { $robocopyArgs += "/MIR" }

# Robust FAT-friendly flags
$robocopyArgs += @(
  "/FFT",                   # FAT file time granularity
  "/R:$Retry", "/W:$Wait",  # retry/wait
  "/TEE"                    # tee to console + log
)

# Exclude junk and caches
$robocopyArgs += @(
  "/XD", ".git", "__pycache__", ".mypy_cache", ".ruff_cache", ".venv",
  "/XF", "*.pyc", "Thumbs.db", ".DS_Store"
)

# Logging
if (-not $NoLog) {
  $robocopyArgs += "/LOG:`"$logPath`""
}

# Dry-run
if ($DryRun) { $robocopyArgs += "/L" }

# Show the exact command
Write-Host "ROBOCOPY $($robocopyArgs -join ' ')" -ForegroundColor DarkGray
Write-Host ""

# Invoke
$start = Get-Date
& robocopy @robocopyArgs
$rc = $LASTEXITCODE
$elapsed = (Get-Date) - $start

Write-Host ""
Write-Host ("Elapsed: {0:c}" -f $elapsed)
Write-Host ("Robocopy exit code: {0}" -f $rc)

# Interpret robocopy exit code (0–7 success; ≥8 error)
# 0: No files copied | 1: Some files copied | 2: Extra files deleted
# 3–7: Other non-fatal conditions (mismatch, retries, etc.)
if ($rc -ge 8) {
  if (-not $NoLog -and (Test-Path $logPath)) {
    Write-Host "See log: $logPath" -ForegroundColor Yellow
  }
  exit $rc
}

# Post counts
$newDstCount = (Get-ChildItem -Force -Recurse -File $Dst -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "Dst files after: $newDstCount"

# Optional: quick list of changed files (from the log) when not DryRun
if (-not $DryRun -and -not $NoLog -and (Test-Path $logPath)) {
  Write-Host ""
  Write-Host "Recent changes (from log tail):" -ForegroundColor Cyan
  Get-Content $logPath -Tail 40
}

exit $rc
