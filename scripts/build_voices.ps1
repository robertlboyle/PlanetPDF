# Build PlanetPDF's natural reading voices installer (packaging\PlanetPDF-Voices.iss): the voice files
# in models\ (scripts\download_voices.py; only the pinned ones) -> dist\PlanetPDF-Voices-Setup-<version>.exe,
# signed, and packaging\voices-installer.json (its name, size, and SHA-256), which scripts\build.ps1
# builds into PlanetPDF's own Setup: ticking "Natural reading voices" there downloads exactly this
# file from https://software.planet.net/downloads/ and runs it (user 2026-11-27).
#   powershell -ExecutionPolicy Bypass -File scripts\build_voices.ps1
# -NoSign (or PLANETPDF_NO_SIGN=1): not signed, for a build that's never published.
# The voices change rarely: raise $version for any new build, since published Setups pin the old
# file's checksum and its name on the server must keep that content. Upload it with
# scripts\upload_update.py --voices-installer before the PlanetPDF Setup that names it.
param([switch]$NoSign)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
$version = "1.0.0"  # (the voices' own version: Kokoro 1.0, Piper Kristin and Joe)
$models = Join-Path $root "models"
$work = Join-Path $env:LOCALAPPDATA "PlanetPDF-build\voices-output"
$dist = Join-Path $root "dist"
$pin = Join-Path $root "packaging\voices-installer.json"
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 isn't installed (winget install JRSoftware.InnoSetup)." }
$file = "PlanetPDF-Voices-Setup-$version.exe"
if (Test-Path $pin) {
    $pinned = Get-Content -Raw $pin | ConvertFrom-Json
    if ($pinned.version -eq $version -and -not $NoSign) {
        throw "Voices $version is already built and pinned ($pin): raise `$version in this script for a new build."
    }
}

Write-Host "`n=== Step 1 of 3: the voice files ===" -ForegroundColor Cyan
& $python scripts\download_voices.py --check | Out-Null
if ($LASTEXITCODE -ne 0) { throw "models\ doesn't hold the pinned voice files: run scripts\download_voices.py." }
$sign = -not ($NoSign -or $env:PLANETPDF_NO_SIGN -eq "1")
$signScript = Join-Path $PSScriptRoot "sign.ps1"
$signing = @()
if ($sign) {
    & $signScript -Check
    $signing = @("/DSign", "/Splanet=powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$signScript`" `$f")
}

Write-Host "`n=== Step 2 of 3: the installer (Inno Setup) ===" -ForegroundColor Cyan
# (written outside Dropbox and the project, then copied, as build.ps1 does)
New-Item -ItemType Directory -Force $work, $dist | Out-Null
& $iscc /Q "/DVoicesVersion=$version" "/DModels=$models" "/DOutput=$work" @signing packaging\PlanetPDF-Voices.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Copy-Item -Force (Join-Path $work $file) $dist
$setup = Get-Item (Join-Path $dist $file)

Write-Host "`n=== Step 3 of 3: checks, and the pin for PlanetPDF's Setup ===" -ForegroundColor Cyan
if ($sign) {
    $signature = Get-AuthenticodeSignature $setup.FullName
    if ($signature.Status -ne "Valid" -or -not $signature.TimeStamperCertificate) {
        throw "The voices installer's signature isn't valid and timestamped: $($signature.StatusMessage)"
    }
    $hash = (Get-FileHash -Algorithm SHA256 $setup.FullName).Hash.ToLower()
    # (only a signed build is pinned: PlanetPDF's published Setup downloads what this names)
    [ordered]@{ version = $version; file = $file; size = $setup.Length; sha256 = $hash } |
        ConvertTo-Json | Set-Content -Encoding ascii $pin
    Write-Host ("Voices installer: {0} ({1:N0} MB), signed by Planet Networks; pinned in {2}" -f $setup.FullName, ($setup.Length / 1MB), $pin) -ForegroundColor Green
} else {
    Write-Host ("Voices installer: {0} ({1:N0} MB), NOT signed and not pinned: never publish it." -f $setup.FullName, ($setup.Length / 1MB)) -ForegroundColor Yellow
}
