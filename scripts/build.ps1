<#
Build PlanetPDF and its installer (packaging, 2026-10-04).

    powershell -ExecutionPolicy Bypass -File scripts\build.ps1            # build + installer + update files
    powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -NoUpdate  # without the signed update files
    powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -SkipAdvisoryCheck  # offline: a trial build, not a release
    powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -NoSign  # not signed: a build never published

Steps: third-party notices -> PyInstaller (the program) + cargo (the native launcher) -> staging folder laid out
as installed (launcher, versions\<version> with Ghostscript; our programs signed) -> the built program's own
self-test -> Inno Setup (Setup and its uninstaller signed) -> the signed update for
https://software.planet.net/PDF/. Signing: scripts\sign.ps1, Planet Networks' certificate through
Microsoft's Artifact Signing (az login first; user 2026-11-27).

Work files go to %LOCALAPPDATA%\PlanetPDF-build (outside Dropbox: ~2 GB of churn and file
locks); the results go to dist\: PlanetPDF-Setup-<version>.exe and update-site\.
Needs: .venv with requirements-dev.txt, Inno Setup 6, Tesseract (tessdata eng + osd),
Ghostscript 10.08.0, models\ (scripts\download_voices.py), and for the update files the
private key from scripts\release_update.py keygen.
#>
# -SkipCompile: reuse the last staging (steps 5-7 only). -SkipAdvisoryCheck: don't ask the advisory
# databases first (a trial build without a network; never for a release). -NoSign (or
# PLANETPDF_NO_SIGN=1): nothing signed, for a build on a computer without the signing tools that's
# never published.
param([switch]$NoUpdate, [switch]$SkipCompile, [switch]$SkipAdvisoryCheck, [switch]$NoSign)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
$version = (& $python -c "import sys; sys.path.insert(0, 'src'); import planetpdf; print(planetpdf.__version__)").Trim()
$work = Join-Path $env:LOCALAPPDATA "PlanetPDF-build"
$stage = Join-Path $work "stage"
$dist = Join-Path $root "dist"
$gsHome = Join-Path $env:ProgramFiles "gs\gs10.08.0"
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
function Step($n, $text) { Write-Host "`n=== Step $n of 7: $text ===" -ForegroundColor Cyan }

if (-not $iscc) { throw "Inno Setup 6 isn't installed (winget install JRSoftware.InnoSetup)." }
$cargo = @((Get-Command cargo -ErrorAction SilentlyContinue).Source, "$env:USERPROFILE\.cargo\bin\cargo.exe") |
    Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $cargo -and -not $SkipCompile) { throw "Rust isn't installed (rustup, stable MSVC; for the launcher)." }
if (-not (Test-Path "$gsHome\bin\gswin64c.exe")) { throw "Ghostscript 10.08.0 isn't in $gsHome." }
# signing (user 2026-11-27): checked now, so a missing tool or sign-in stops the build before the long steps
$sign = -not ($NoSign -or $env:PLANETPDF_NO_SIGN -eq "1")
$signScript = Join-Path $PSScriptRoot "sign.ps1"
if ($sign) { & $signScript -Check }
else { Write-Host "Not signing (-NoSign or PLANETPDF_NO_SIGN): never publish this build." -ForegroundColor Yellow }
if (-not (Test-Path "models\kokoro") -or -not (Test-Path "models\piper")) { throw "models\ is missing: run scripts\download_voices.py." }
# Only the pinned voice files are used (security review 2026-11-25): here for the self-test; they're
# shipped in their own installer (scripts\build_voices.ps1), which Setup downloads when they're ticked
& ".venv\Scripts\python.exe" scripts\download_voices.py --check | Out-Null
if ($LASTEXITCODE -ne 0) { throw "models\ doesn't hold the pinned voice files: run scripts\download_voices.py." }
if (-not (Test-Path (Join-Path $root "packaging\voices-installer.json"))) {
    throw "The voices installer isn't built: run scripts\build_voices.ps1 first (it pins the file Setup downloads)."
}
# Ghostscript's folder and the OCR language files are the pinned ones too (security list 2026-11-26,
# item 9: they went into the installer as found on this computer, unchecked)
$tessdata = if ($env:PLANETPDF_TESSDATA) { $env:PLANETPDF_TESSDATA } else { Join-Path $env:ProgramFiles "Tesseract-OCR\tessdata" }
& $python scripts\check_build_parts.py --ghostscript $gsHome --tessdata $tessdata
if ($LASTEXITCODE -ne 0) { throw "Ghostscript or the OCR language files on this computer aren't the pinned ones (see above)." }
# A build takes exactly the pinned files, never "the latest" by itself. So each build first asks the
# public advisory databases whether any pinned part has a known problem with a published fix, and
# stops when one does: that pin is then raised on purpose (requirements.txt, hash_requirements.py,
# the tests), and the build run again (user 2026-11-26; docs\BUILD-SETUP.md).
if ($SkipAdvisoryCheck) {
    Write-Host "The advisory check was skipped (-SkipAdvisoryCheck): a trial build, not one to release." -ForegroundColor Yellow
} else {
    Write-Host "Asking the advisory databases about the pinned parts (about a minute)..."
    & $python scripts\check_advisories.py
    if ($LASTEXITCODE -eq 1) { throw "A pinned part has a known security problem (listed above): raise its pin first (docs\BUILD-SETUP.md)." }
    if ($LASTEXITCODE -ne 0) { throw "The advisory databases couldn't be asked (above). Build again when online, or pass -SkipAdvisoryCheck for a trial build that won't be released." }
}
Write-Host "Building PlanetPDF $version"
$app = Join-Path $stage "app"
$program = Join-Path $stage "versions\$version"
$rootFiles = Join-Path $program "install-root"
# our own programs, signed before anything is packed (Ghostscript's are Artifex's, left as they are)
$programs = @((Join-Path $app "PlanetPDF.exe"), (Join-Path $app "PlanetPDF.com"),
              (Join-Path $app "printer\planetpdf-print-service.exe"), (Join-Path $app "printer\planetpdf-printer-setup.exe"),
              (Join-Path $program "PlanetPDF.exe"), (Join-Path $program "planetpdf-cli.exe"))
if ($SkipCompile) {
    if (-not (Test-Path (Join-Path $program "PlanetPDF.exe"))) { throw "No staged build of $version to reuse." }
    Write-Host "Reusing the staged build in $stage (steps 1-4 skipped)."
} else {

Step 1 "third-party notices"
& $python scripts\update_notices.py
if ($LASTEXITCODE) { throw "update_notices.py failed" }

Step 2 "compiling (PyInstaller: the program; cargo: the launcher)"
New-Item -ItemType Directory -Force "$root\build" | Out-Null
$parts = ($version -split '\.') + @(0, 0, 0, 0) | Select-Object -First 4
$tuple = ($parts | ForEach-Object { [int]$_ }) -join ", "
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($tuple), prodvers=($tuple), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Planet Networks'), StringStruct('FileDescription', 'PlanetPDF'),
      StringStruct('FileVersion', '$version'), StringStruct('ProductName', 'PlanetPDF'),
      StringStruct('ProductVersion', '$version'), StringStruct('LegalCopyright', 'Planet Networks (internal use)'),
      StringStruct('OriginalFilename', 'PlanetPDF.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"@ | Set-Content -Encoding utf8 "$root\build\version_info.txt"
& $python -m PyInstaller packaging\planetpdf.spec --noconfirm --clean --log-level WARN `
    --distpath "$work\dist" --workpath "$work\work"
if ($LASTEXITCODE) { throw "PyInstaller failed" }
# the launcher: native (launcher\, Rust; 2026-10-07), its icon and version from the project
$env:CARGO_TARGET_DIR = Join-Path $work "launcher-target"
& $cargo build --release --quiet --manifest-path launcher\Cargo.toml
$cargoFailed = $LASTEXITCODE
Remove-Item Env:CARGO_TARGET_DIR
if ($cargoFailed) { throw "Building the launcher failed (cargo)" }
# the PlanetPDF printer: its service + setup program (printer\, Rust; 2026-10-18)
$env:CARGO_TARGET_DIR = Join-Path $work "printer-target"
& $cargo build --release --quiet --manifest-path printer\Cargo.toml
$cargoFailed = $LASTEXITCODE
Remove-Item Env:CARGO_TARGET_DIR
if ($cargoFailed) { throw "Building the PlanetPDF printer failed (cargo)" }

Step 3 "staging the install layout"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $app, $program | Out-Null
Copy-Item (Join-Path $work "launcher-target\release\planetpdf-launcher.exe") (Join-Path $app "PlanetPDF.exe")
# the command-line launcher: command prompts run PlanetPDF.com before PlanetPDF.exe (2026-10-18)
Copy-Item (Join-Path $work "launcher-target\release\planetpdf-console.exe") (Join-Path $app "PlanetPDF.com")
$printer = Join-Path $app "printer"
New-Item -ItemType Directory -Force $printer | Out-Null
foreach ($part in "planetpdf-print-service.exe", "planetpdf-printer-setup.exe") {
    Copy-Item (Join-Path $work "printer-target\release\$part") $printer
}
Copy-Item "src\planetpdf\resources\icons\planetpdf.ico" $app
Set-Content -Encoding ascii (Join-Path $app "current.txt") $version
Copy-Item -Recurse "$work\dist\PlanetPDF\*" $program
# signed now: before the self-test runs them, and before the copies below, the installer, and the update
if ($sign) { & $signScript @programs }
# copies the update carries, which it puts in the install folder (core\update.py update_root_files;
# security review 2026-11-25: updates replaced only the program, never the print service or launchers)
New-Item -ItemType Directory -Force (Join-Path $rootFiles "printer") | Out-Null
Copy-Item (Join-Path $app "PlanetPDF.exe"), (Join-Path $app "PlanetPDF.com") $rootFiles
Copy-Item (Join-Path $printer "*") (Join-Path $rootFiles "printer")
$gs = Join-Path $program "ghostscript"
New-Item -ItemType Directory -Force "$gs\doc" | Out-Null
foreach ($part in "bin", "lib", "Resource", "iccprofiles") { Copy-Item -Recurse "$gsHome\$part" $gs }
Copy-Item "$gsHome\doc\COPYING" "$gs\doc"
Copy-Item "THIRD-PARTY-NOTICES.txt" $program
Copy-Item "THIRD-PARTY-NOTICES.txt" $app
# the manual as a PDF, made fresh for this build (2026-10-18)
& $python scripts\make_manual.py (Join-Path $app "PlanetPDF Manual.pdf")
if ($LASTEXITCODE) { throw "Making the manual's PDF failed" }

Step 4 "self-test of the built program (every library, OCR, Ghostscript, voices)"
# the staging folder laid out as installed (the launcher + current.txt beside versions\, put
# there just for this): the launcher must find and start the program, which runs its self-test
# (the voices from models\ via PLANETPDF_MODELS). No junction: when Claude runs the build, Windows
# keeps its %LOCALAPPDATA% files in the Claude app's own folder, and a junction pointed at the
# usual path, so the launcher found nothing (2026-10-07).
$trialFiles = "PlanetPDF.exe", "current.txt"
foreach ($f in $trialFiles) { Copy-Item (Join-Path $app $f) (Join-Path $stage $f) }
$report = Join-Path $work "self-test.txt"
if (Test-Path $report) { Remove-Item $report }
$env:PLANETPDF_MODELS = Join-Path $root "models"
# (not -Wait: an error box from the launcher would hold the build forever)
$launcher = Start-Process -FilePath (Join-Path $stage "PlanetPDF.exe") -ArgumentList "--self-test", "`"$report`"" -PassThru
Remove-Item Env:PLANETPDF_MODELS
# (the launcher returns at once, the native one before the program has even made the report:
#  so wait for the file to exist before reading it; Select-String on a missing file stops the build)
# (6 minutes: a just-built program's first start can be slow while Windows checks it; 0.1.6's
#  build gave up at 3 minutes though the program then passed in under one)
for ($i = 0; $i -lt 360 -and -not ((Test-Path $report) -and (Select-String -Path $report -Pattern "^RESULT" -Quiet)); $i++) {
    Start-Sleep -Seconds 1
}
if (-not $launcher.HasExited) { Stop-Process -Id $launcher.Id -Force }  # (still showing an error)
foreach ($f in $trialFiles) { Remove-Item (Join-Path $stage $f) }
if (-not (Test-Path $report)) { throw "The launcher didn't start the program (no self-test report)." }
Get-Content $report | ForEach-Object { if ($_ -like "FAIL*") { Write-Host $_ -ForegroundColor Red } else { Write-Host $_ } }
if (-not (Select-String -Path $report -Pattern "^RESULT: all passed" -Quiet)) { throw "The built program failed its self-test (see above)." }
}
# every program the installer and the update carry is signed, the update's copies too; a staging
# reused from a build that wasn't signed (-SkipCompile after -NoSign) is signed here
if ($sign) {
    $carried = @(Get-ChildItem $rootFiles -Recurse -Include *.exe, *.com -File | ForEach-Object FullName)
    $unsigned = @($programs + $carried | Where-Object { (Get-AuthenticodeSignature $_).Status -ne "Valid" })
    if ($unsigned.Count) { & $signScript @unsigned }
}

Step 5 "installer (Inno Setup)"
New-Item -ItemType Directory -Force $dist | Out-Null
# (written outside Dropbox, then copied: Dropbox opens a new file at once, and Inno Setup's
#  last step, writing the setup's resources, then failed with EndUpdateResource error 110)
$output = Join-Path $work "output"
New-Item -ItemType Directory -Force $output | Out-Null
# Setup and its uninstaller are signed by Inno Setup through the sign tool "planet" (= sign.ps1;
# Inno Setup puts the file's quoted name for $f)
$signing = @()
if ($sign) { $signing = @("/DSign", "/Splanet=powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$signScript`" `$f") }
# the natural voices aren't in Setup: ticking them downloads the signed voices installer that
# scripts\build_voices.ps1 pinned (its name and SHA-256; user 2026-11-27)
$voices = Get-Content -Raw (Join-Path $root "packaging\voices-installer.json") | ConvertFrom-Json
$voicesArgs = @("/DVoicesFile=$($voices.file)", "/DVoicesSha256=$($voices.sha256)",
                "/DVoicesMB=$([math]::Round($voices.size / 1000000))")  # (MB as the app counts them: 354 MB)
& $iscc /Q "/DAppVersion=$version" "/DStage=$stage" "/DOutput=$output" @voicesArgs @signing packaging\PlanetPDF.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Copy-Item -Force "$output\PlanetPDF-Setup-$version.exe" $dist

Step 6 "update files for https://software.planet.net/PDF/"
if ($NoUpdate) { Write-Host "Skipped (-NoUpdate)." }
else {
    & $python scripts\release_update.py publish --build $program --out "$dist\update-site" --notes "PlanetPDF $version"
    if ($LASTEXITCODE) { throw "release_update.py failed" }
}

Step 7 "done"
$setup = Get-Item "$dist\PlanetPDF-Setup-$version.exe"
Write-Host ("Installer: {0} ({1:N0} MB)" -f $setup.FullName, ($setup.Length / 1MB)) -ForegroundColor Green
if ($sign) {
    $signature = Get-AuthenticodeSignature $setup.FullName
    if ($signature.Status -ne "Valid") { throw "The installer's signature isn't valid: $($signature.StatusMessage)" }
    if (-not $signature.TimeStamperCertificate) { throw "The installer's signature has no timestamp." }
    Write-Host "Signed by $($signature.SignerCertificate.GetNameInfo('SimpleName', $false)), timestamped" -ForegroundColor Green
} else { Write-Host "NOT signed (-NoSign): never publish this build." -ForegroundColor Yellow }
if (-not $NoUpdate) { Write-Host "Update files: $dist\update-site (upload the zip first, then latest.json and latest.json.sig)" -ForegroundColor Green }
