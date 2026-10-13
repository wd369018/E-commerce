<#
.SYNOPSIS
    ShopScope - build the SQLite database and validate the SQL layer.

.DESCRIPTION
    Rebuilds data\shopscope.db from the cleaned CSVs in data\processed\
    (schema + primary/foreign keys + indexes + analytical views), then runs the
    pytest validation suite (SQL results checked against independent Python
    calculations) and prints a KPI snapshot.

    The database is an output: the cleaned CSVs are read-only and the raw
    sources are never touched. Re-running is safe - the database file is
    recreated from scratch every time.

.EXAMPLE
    .\scripts\run_sql.ps1
    .\scripts\run_sql.ps1 -SkipTests
    .\scripts\run_sql.ps1 -FreshTests
#>
[CmdletBinding()]
param(
    # Only rebuild the database; do not run the test suite.
    [switch]$SkipTests,
    # Force the test suite to rebuild its cached database.
    [switch]$FreshTests
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

Write-Host "ShopScope SQL :: $projectRoot" -ForegroundColor Cyan

Write-Host "`n[1/3] Building data\shopscope.db ..." -ForegroundColor Cyan
Invoke-ProjectPython -PyArgs @('-m', 'src.database')
if ($LASTEXITCODE -ne 0) {
    Write-Host "Database build FAILED (exit $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}

if (-not $SkipTests) {
    Write-Host "`n[2/3] Running SQL validation tests ..." -ForegroundColor Cyan
    if ($FreshTests) { $env:SHOPSCOPE_TEST_DB_FRESH = '1' }
    Invoke-ProjectPython -PyArgs @('-m', 'pytest', '-q')
    $testExit = $LASTEXITCODE
    Remove-Item Env:\SHOPSCOPE_TEST_DB_FRESH -ErrorAction SilentlyContinue
    if ($testExit -ne 0) {
        Write-Host "Tests FAILED (exit $testExit)." -ForegroundColor Red
        exit $testExit
    }
} else {
    Write-Host "`n[2/3] Tests skipped." -ForegroundColor Yellow
}

Write-Host "`n[3/3] KPI snapshot ..." -ForegroundColor Cyan
Invoke-ProjectPython -PyArgs @('-m', 'scripts.sql_kpi_snapshot')

Write-Host "`nSQL layer OK. Database: data\shopscope.db" -ForegroundColor Green
exit 0
