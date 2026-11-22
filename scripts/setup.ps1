# Creates (or rebuilds) the project virtual environment on this machine.
# Run once per computer, and again if the app or tests fail to start after switching machines:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Set-DropboxIgnored([string]$dir) {
    # .venv and .pytest_cache stay local; the marker is per machine (NTFS alternate stream).
    New-Item -ItemType Directory -Force $dir | Out-Null
    Set-Content -Path $dir -Stream com.dropbox.ignored -Value 1
}

if (Test-Path .venv) {
    Write-Host 'Removing existing .venv ...'
    Remove-Item -Recurse -Force .venv
}
Set-DropboxIgnored '.venv'
Set-DropboxIgnored '.pytest_cache'

Write-Host 'Creating .venv with Python 3.14 ...'
py -3.14 -m venv .venv
$python = Join-Path $root '.venv\Scripts\python.exe'

# No pip self-upgrade: it is the step most likely to be broken by file locks.
& $python -m pip install --no-compile -r requirements-dev.txt
& $python -m pip install --no-compile --no-deps -e .
& $python -m pip check
Write-Host 'Done. Run tests with:  .venv\Scripts\python.exe -m pytest'
Write-Host 'Run the app with:      .venv\Scripts\python.exe -m planetpdf'
