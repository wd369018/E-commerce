<#
.SYNOPSIS
    ShopScope - run the ETL pipeline.

.DESCRIPTION
    Ingests the nine raw Olist CSVs (read-only), cleans them, builds the
    conformed tables, validates keys/grain/reconciliation against the source,
    and writes data/processed/ plus reports/.

    Re-running is safe: every output file is overwritten, never appended to,
    and a SHA-256 check proves the raw CSVs were not modified.

.EXAMPLE
    .\scripts\run_etl.ps1
    .\scripts\run_etl.ps1 -Strict
    .\scripts\run_etl.ps1 -Verbose
#>
[CmdletBinding()]
param(
    # Treat warnings as failures (exit code 1).
    [switch]$Strict,
    # Debug-level logging.
    [switch]$VerboseMode
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# Interpreter: prefer the version-pinned py launcher, fall back to `python`.
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
function Invoke-ProjectPython {
    param([string[]]$PyArgs)
    if ($pyLauncher) { & py @('-3.14') @PyArgs } else { & python @PyArgs }
}

$pythonArgs = @('-m', 'src.etl_pipeline')
if ($Strict)    { $pythonArgs += '--strict' }
if ($VerboseMode) { $pythonArgs += '--verbose' }

Write-Host "ShopScope ETL :: $projectRoot" -ForegroundColor Cyan
Invoke-ProjectPython -PyArgs $pythonArgs
$exitCode = $LASTEXITCODE

if ($exitCode -eq 0) {
    Write-Host "ETL completed successfully. See reports\data_quality_after_cleaning.md" -ForegroundColor Green
} else {
    Write-Host "ETL FAILED (exit $exitCode). See reports\etl.log" -ForegroundColor Red
}
exit $exitCode
