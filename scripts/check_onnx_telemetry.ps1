# Checks that ONNX Runtime (the natural voices' engine) writes no telemetry events while PlanetPDF
# loads both voices (security review 2026-11-25). Run it in an administrator PowerShell (starting
# an event trace needs one):
#
#   powershell -ExecutionPolicy Bypass -File scripts\check_onnx_telemetry.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\check_onnx_telemetry.ps1 -Program "C:\Program Files\PlanetPDF\PlanetPDF.exe"
#
# It traces ONNX Runtime's event provider (Microsoft.ML.ONNXRuntime) while the program's self-test
# makes speech with Piper and Kokoro, then counts the provider's events. 0 = telemetry stayed off.
param(
    [string]$Program = ""
)
$ErrorActionPreference = "Stop"
$provider = "{3a26b1ff-7484-7484-7484-15261f42614d}"  # Microsoft.ML.ONNXRuntime (TraceLogging)
$work = Join-Path $env:TEMP "planetpdf-onnx-trace"
New-Item -ItemType Directory -Force $work | Out-Null
$etl = Join-Path $work "onnx.etl"
$csv = Join-Path $work "onnx.csv"
$report = Join-Path $work "self-test.txt"
Remove-Item $etl, $csv, $report -ErrorAction SilentlyContinue

Write-Host "Step 1 of 3: starting the trace"
logman stop PlanetPDFOnnxCheck -ets 2>$null | Out-Null
logman start PlanetPDFOnnxCheck -p $provider 0xFFFFFFFFFFFFFFFF 0xFF -o $etl -ets | Out-Null
try {
    Write-Host "Step 2 of 3: loading both voices (PlanetPDF's self-test, about a minute)"
    if ($Program) {
        & $Program --self-test $report | Out-Null
    } else {
        $root = Split-Path $PSScriptRoot -Parent
        & (Join-Path $root ".venv\Scripts\python.exe") -m planetpdf --self-test $report | Out-Null
    }
} finally {
    logman stop PlanetPDFOnnxCheck -ets | Out-Null
}
Write-Host "Step 3 of 3: counting ONNX Runtime's events"
tracerpt $etl -o $csv -of CSV -y | Out-Null
$events = @(Import-Csv $csv | Where-Object { $_.'Event Name' -and $_.'Event Name' -notmatch 'EventTrace' })
Get-Content $report | Select-String "Read Out Loud" | ForEach-Object { Write-Host "  $_" }
if ($events.Count -eq 0) {
    Write-Host "No ONNX Runtime telemetry events: it stayed off." -ForegroundColor Green
} else {
    Write-Host "$($events.Count) ONNX Runtime event(s) were written:" -ForegroundColor Yellow
    $events | Group-Object 'Event Name' | ForEach-Object { Write-Host "  $($_.Count) x $($_.Name)" }
}
