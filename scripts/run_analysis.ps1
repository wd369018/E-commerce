<#
.SYNOPSIS
    ShopScope - run the exploratory analysis layer and regenerate all reports.

.DESCRIPTION
    Requires data\shopscope.db (build it first with .\scripts\run_sql.ps1 or
    python -m src.database). Produces:

        reports\business_metrics.md   - the 14 KPIs with formula / filters /
                                        eligible population / limitations
        reports\eda_findings.md       - evidence-backed findings (business
                                        question, method, result, period,
                                        interpretation, recommendation,
                                        limitations, correlation caveat)
        reports\analysis_summary.json - machine-readable KPIs + findings
        reports\figures\*.png         - the charts referenced by the findings

    The analysis layer only reads the database; it never modifies the raw
    sources, the cleaned CSVs, or the database. Re-running is safe.

.EXAMPLE
    .\scripts\run_analysis.ps1
    .\scripts\run_analysis.ps1 -SkipTests
    .\scripts\run_analysis.ps1 -SkipFigures
#>
[CmdletBinding()]
param(
    # Do not run the pytest suite after generating the reports.
    [switch]$SkipTests,
    # Skip chart rendering (faster; reports still written).
    [switch]$SkipFigures
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Write-Host "ShopScope Analysis :: $projectRoot" -ForegroundColor Cyan

if (-not (Test-Path "data\shopscope.db")) {
    Write-Host "data\shopscope.db not found. Build it first: python -m src.database" -ForegroundColor Red
    exit 1
}

Write-Host "`n[1/2] Generating metrics, findings and reports ..." -ForegroundColor Cyan
$insightArgs = @()
if ($SkipFigures) { $insightArgs += '--no-figures' }

# Interpreter: prefer the version-pinned py launcher, fall back to `python`.
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
function Invoke-ProjectPython {
    param([string[]]$PyArgs)
    if ($pyLauncher) { & py @('-3.14') @PyArgs } else { & python @PyArgs }
}

Invoke-ProjectPython -PyArgs (@('-m', 'src.insights') + $insightArgs)
if ($LASTEXITCODE -ne 0) {
    Write-Host "Analysis FAILED (exit $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}

if (-not $SkipTests) {
    Write-Host "`n[2/2] Running analysis-layer tests ..." -ForegroundColor Cyan
    Invoke-ProjectPython -PyArgs @('-m', 'pytest', 'tests/test_metrics_insights.py', '-q')
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Tests FAILED (exit $LASTEXITCODE)." -ForegroundColor Red
        exit $LASTEXITCODE
    }
} else {
    Write-Host "`n[2/2] Tests skipped." -ForegroundColor Yellow
}

Write-Host "`nAnalysis layer OK." -ForegroundColor Green
Write-Host "  reports\business_metrics.md"
Write-Host "  reports\eda_findings.md"
Write-Host "  reports\analysis_summary.json"
exit 0
