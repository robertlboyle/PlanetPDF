# Build PlanetPDF's optional AI text reading add-on (Unlimited-OCR): assemble the component
# (scripts\build_ai_ocr.py, ~10 GB, skips what's already there), then its own installer
# (packaging\PlanetPDF-AI.iss) -> dist\PlanetPDF-AI-Setup-<version>.exe + .bin parts.
#   powershell -ExecutionPolicy Bypass -File scripts\build_ai_addon.ps1
# Setup and its uninstaller are signed with Planet Networks' certificate (scripts\sign.ps1, az login
# first; user 2026-11-27); -NoSign (or PLANETPDF_NO_SIGN=1) for a build that's never published.
param([switch]$NoSign)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
$work = Join-Path $env:LOCALAPPDATA "PlanetPDF-build"
$source = Join-Path $work "ai-ocr"
$output = Join-Path $root "dist"
$version = "1.0.0"  # (the add-on's own version: bump when the model or libraries change)
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 isn't installed (winget install JRSoftware.InnoSetup)." }
$sign = -not ($NoSign -or $env:PLANETPDF_NO_SIGN -eq "1")
$signScript = Join-Path $PSScriptRoot "sign.ps1"
$signing = @()
if ($sign) {
    & $signScript -Check
    $signing = @("/DSign", "/Splanet=powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$signScript`" `$f")
}

Write-Host "`n=== Step 1 of 2: the component (Python, PyTorch with CUDA, the model) ===" -ForegroundColor Cyan
& $python scripts\build_ai_ocr.py
if ($LASTEXITCODE) { throw "Assembling the AI component failed" }

Write-Host "`n=== Step 2 of 2: the add-on installer ===" -ForegroundColor Cyan
New-Item -ItemType Directory -Force $output | Out-Null
& $iscc /Q "/DAddonVersion=$version" "/DSource=$source" "/DOutput=$output" @signing packaging\PlanetPDF-AI.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Get-ChildItem $output -Filter "PlanetPDF-AI-Setup-$version*" | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB)}}
