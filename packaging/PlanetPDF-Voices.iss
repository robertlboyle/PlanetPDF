; PlanetPDF's natural reading voices for Read Out Loud (Kokoro, and Piper's Kristin and Joe), as an
; installer of their own (user 2026-11-27: "Keep it a separate download though. The install is too
; damn big"; "Most users probably won't use it. Make it part of the install up front and allow
; install later too"). Built by scripts\build_voices.ps1, which passes /DVoicesVersion=…
; /DModels=… (the project's models folder) /DOutput=… (and, to sign, /DSign /Splanet=…).
;
; Two ways in: PlanetPDF's own Setup downloads this file and runs it silently when "Natural reading
; voices" is ticked (its SHA-256 is pinned in that Setup: packaging\voices-installer.json), or it is
; downloaded and run by itself later. Per machine, into PlanetPDF's folder ({app}\models, where every
; user's PlanetPDF finds them: core/neural_speech.py models_dir), so a terminal server needs one copy
; for everyone. Its uninstaller sits in {app}\models; PlanetPDF's own uninstaller removes the voices
; and this entry too. A new build of the voices needs a new VoicesVersion: Setups already published
; pin the old file's checksum.

#ifndef VoicesVersion
  #error Run scripts\build_voices.ps1 (VoicesVersion not given)
#endif

#define Publisher "Planet Networks"
#define MainKey "Software\Planet Networks\PlanetPDF"

[Setup]
#ifdef Sign
; Setup and its uninstaller signed with Planet Networks' certificate (scripts\sign.ps1, passed by
; scripts\build_voices.ps1 as the sign tool "planet")
SignTool=planet
SignedUninstaller=yes
#endif
; (PlanetPDF.iss names this id too, to remove this entry when PlanetPDF is uninstalled)
AppId={{E5E58D41-A0E3-486C-9EA8-D134A62351FD}
AppName=PlanetPDF Natural Voices
AppVersion={#VoicesVersion}
AppVerName=PlanetPDF Natural Voices {#VoicesVersion}
AppPublisher={#Publisher}
VersionInfoVersion={#VoicesVersion}
VersionInfoCompany={#Publisher}
VersionInfoDescription=PlanetPDF Natural Voices Setup
DefaultDirName={reg:HKLM\{#MainKey},InstallDir|{autopf}\PlanetPDF}
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=no
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=PlanetPDF-Voices-Setup-{#VoicesVersion}
SetupIconFile=..\src\planetpdf\resources\icons\planetpdf.ico
UninstallDisplayIcon={app}\PlanetPDF.exe
UninstallDisplayName=PlanetPDF Natural Voices
UninstallFilesDir={app}\models
WizardStyle=modern
WizardImageFile=images\wizard-164x314.png,images\wizard-192x386.png,images\wizard-246x459.png,images\wizard-273x556.png,images\wizard-328x604.png,images\wizard-355x700.png,images\wizard-410x797.png
WizardSmallImageFile=images\small-55x55.png,images\small-64x68.png,images\small-69x69.png,images\small-83x80.png,images\small-92x97.png,images\small-110x106.png,images\small-119x123.png,images\small-138x140.png
Compression=lzma2/normal
SolidCompression=no
; (the voice files are only read; nothing of PlanetPDF's needs closing)
CloseApplications=no

[Files]
Source: "{#Models}\kokoro\*"; DestDir: "{app}\models\kokoro"; Excludes: "*.part"; Flags: ignoreversion
Source: "{#Models}\piper\*"; DestDir: "{app}\models\piper"; Excludes: "*.part"; Flags: ignoreversion

[UninstallDelete]
Type: dirifempty; Name: "{app}\models\kokoro"
Type: dirifempty; Name: "{app}\models\piper"

[Code]
{ PlanetPDF must be installed first: the voices go into its folder. }
function InitializeSetup(): Boolean;
var
  Folder: String;
begin
  Result := RegQueryStringValue(HKEY_LOCAL_MACHINE, '{#MainKey}', 'InstallDir', Folder) and DirExists(Folder);
  if not Result then
    SuppressibleMsgBox('Install PlanetPDF first: its natural reading voices go into its folder.', mbError, MB_OK, IDOK);
end;
