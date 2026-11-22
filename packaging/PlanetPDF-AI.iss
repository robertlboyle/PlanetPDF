; PlanetPDF's optional AI text reading add-on (Unlimited-OCR; user 2026-10-21: optional, "put in the
; notes that an NVidia graphics card is needed"). Built by scripts\build_ai_addon.ps1, which passes
; /DAddonVersion=… /DSource=… (the folder scripts\build_ai_ocr.py made) /DOutput=….
;
; It's its own installer because it's about 10 GB (PyTorch with CUDA, and the model): more than one
; Setup.exe can hold, so it comes as Setup.exe plus .bin parts (keep them together). It goes into
; PlanetPDF's own folder, {app}\ai-ocr, where core/ai_ocr.py finds it; PlanetPDF's updates never
; touch it. Windows, NVIDIA graphics card with about 8 GB of memory.

#ifndef AddonVersion
  #error Run scripts\build_ai_addon.ps1 (AddonVersion not given)
#endif

#define Publisher "Planet Networks"
#define MainKey "Software\Planet Networks\PlanetPDF"

[Setup]
AppId={{A7C3E2D9-5B41-4F86-9E0A-3D2C71B8F604}
AppName=PlanetPDF AI Text Reading
AppVersion={#AddonVersion}
AppVerName=PlanetPDF AI Text Reading {#AddonVersion}
AppPublisher={#Publisher}
VersionInfoVersion={#AddonVersion}
VersionInfoCompany={#Publisher}
VersionInfoDescription=PlanetPDF AI Text Reading Setup
DefaultDirName={reg:HKLM\{#MainKey},InstallDir|{autopf}\PlanetPDF}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=PlanetPDF-AI-Setup-{#AddonVersion}
UninstallDisplayName=PlanetPDF AI Text Reading
WizardStyle=modern
; (the weights hardly compress; fast keeps the build hours shorter)
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=yes
DiskSliceSize=max
CloseApplications=yes
InfoBeforeFile=ai-addon-readme.txt

[Files]
Source: "{#Source}\*"; DestDir: "{app}\ai-ocr"; Excludes: "*.part"; Flags: ignoreversion recursesubdirs createallsubdirs

[UninstallDelete]
Type: filesandordirs; Name: "{app}\ai-ocr"

[Code]
{ PlanetPDF must be installed first: the add-on goes into its folder. }
function InitializeSetup(): Boolean;
var
  Folder: String;
begin
  Result := RegQueryStringValue(HKEY_LOCAL_MACHINE, '{#MainKey}', 'InstallDir', Folder) and DirExists(Folder);
  if not Result then
    MsgBox('Install PlanetPDF first: AI Text Reading is an add-on that goes into its folder.', mbError, MB_OK);
end;
