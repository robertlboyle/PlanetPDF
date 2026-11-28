# Signs PlanetPDF's Windows programs and installers with Planet Networks' certificate, through
# Microsoft's Artifact Signing (Azure): the certificate never leaves Microsoft, and signtool asks the
# PlanetNetworks account to sign each file. Windows shows the publisher as "Planet Networks Inc".
#
#   powershell -ExecutionPolicy Bypass -File scripts\sign.ps1 FILE [FILE ...]
#
# Run by scripts\build.ps1 (the programs, before they're packed) and by Inno Setup (Setup and its
# uninstaller: the sign tool "planet"). The same account, profile, and script as Eleonora's.
# Needs, once per computer (user 2026-11-27; docs\BUILD-SETUP.md):
#   winget install Microsoft.Azure.ArtifactSigningClientTools   (the signing client)
#   winget install Microsoft.AzureCLI, then: az login           (as an account holding the
#       "Artifact Signing Certificate Profile Signer" role on the PlanetNetworks account)
#   signtool from the Windows SDK (Visual Studio Build Tools' C++ workload).
# The account's settings are in packaging\signing.json (not secret). Each signature is timestamped
# by Microsoft, so it stays valid after the short-lived certificate expires.
# PLANETPDF_NO_SIGN=1 (or build.ps1 -NoSign) skips signing: a build that's never published.
# -Check only checks that everything above is in place and signed in (build.ps1 runs it first, so a
# missing tool stops the build before the long steps, not after them).
param([switch]$Check, [Parameter(ValueFromRemainingArguments)][string[]]$Files)
$ErrorActionPreference = 'Stop'

if (-not $Check -and -not $Files) { throw "Name the files to sign: scripts\sign.ps1 FILE [FILE ...]" }
if ($env:PLANETPDF_NO_SIGN -eq '1') { Write-Output "Not signed (PLANETPDF_NO_SIGN): $($Files -join ', ')"; exit 0 }

$root = Split-Path $PSScriptRoot
$metadata = Join-Path $root 'packaging\signing.json'
$dlib = if ($env:ARTIFACT_SIGNING_DLIB) { $env:ARTIFACT_SIGNING_DLIB } else {
    Join-Path $env:LOCALAPPDATA 'Microsoft\MicrosoftArtifactSigningClientTools\Azure.CodeSigning.Dlib.dll'
}
if (-not (Test-Path $dlib)) { throw "The signing client isn't installed (winget install Microsoft.Azure.ArtifactSigningClientTools): $dlib" }
# the newest Windows SDK's x64 signtool (the client needs 10.0.22621 or newer)
$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.*\x64\signtool.exe" -ErrorAction SilentlyContinue |
    Sort-Object { [version]$_.Directory.Parent.Name } | Select-Object -Last 1 -ExpandProperty FullName
if (-not $signtool) { throw "signtool isn't installed (the Windows SDK, in Visual Studio Build Tools)." }
# the client signs in through the Azure CLI; a window opened before it was installed lacks it on PATH.
# (ProgramW6432: Inno Setup's compiler is 32-bit, so the PowerShell it starts for Setup sees
# ProgramFiles as "Program Files (x86)")
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    $programFiles = if ($env:ProgramW6432) { $env:ProgramW6432 } else { $env:ProgramFiles }
    $wbin = Join-Path $programFiles 'Microsoft SDKs\Azure\CLI2\wbin'
    if (Test-Path (Join-Path $wbin 'az.cmd')) { $env:PATH = "$wbin;$env:PATH" }
    else { throw "The Azure CLI isn't installed (winget install Microsoft.AzureCLI, then az login)." }
}
if ($Check) {
    # (Continue: Windows PowerShell turns a native program's error output into a stopping error)
    $ErrorActionPreference = 'Continue'
    & az account show --only-show-errors --output none 2>$null
    $ErrorActionPreference = 'Stop'
    if ($LASTEXITCODE -ne 0) { throw "Not signed in to Azure: run az login (an account with the signer role on PlanetNetworks)." }
    Write-Output "Signing is ready (signtool, the signing client, the Azure sign-in)."
    exit 0
}

$n = 0
foreach ($file in $Files) {
    $n++
    if (-not (Test-Path $file)) { throw "Nothing to sign at $file" }
    Write-Output "Signing $n of $($Files.Count): $(Split-Path $file -Leaf)"
    & $signtool sign /q /fd SHA256 /tr 'http://timestamp.acs.microsoft.com' /td SHA256 /dlib $dlib /dmdf $metadata $file
    if ($LASTEXITCODE -ne 0) { throw "Signing $file failed (signed in with az login, and holding the signer role?)" }
    & $signtool verify /pa /q $file
    if ($LASTEXITCODE -ne 0) { throw "$file was signed but Windows doesn't accept the signature." }
}
