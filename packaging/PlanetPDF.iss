; PlanetPDF installer (Inno Setup 6; packaging, 2026-10-04). Built by scripts\build.ps1,
; which passes /DAppVersion=… /DStage=… /DModels=… /DOutput=….
;
; Layout (core/update.py):  {app}\PlanetPDF.exe       the launcher (native, launcher\: Rust)
;                           {app}\current.txt         the version it starts
;                           {app}\versions\<version>  the program (+ Ghostscript, OCR files)
;                           {app}\models              Read Out Loud voices (optional, shared)
; Per machine (admin), Start menu shortcut, "Open with" for .pdf, and a question whether
; PlanetPDF should open PDF files: Windows doesn't let an installer make itself the default,
; so a yes opens Windows' own "How do you want to open .pdf files?" chooser, where the user
; picks PlanetPDF (user 2026-10-10; the chooser since 2026-10-18). Silent install for IT: /VERYSILENT
; /SUPPRESSMSGBOXES (add /COMPONENTS="main" to leave out the voices).

#ifndef AppVersion
  #error Run scripts\build.ps1 (AppVersion not given)
#endif

#define AppName "PlanetPDF"
#define Publisher "Planet Networks"
#define ProgId "PlanetPDF.Document"

[Setup]
AppId={{6F3C2A51-8E4B-4C1D-9A57-2B7D0E9C4F18}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Publisher}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#Publisher}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=PlanetPDF-Setup-{#AppVersion}
SetupIconFile={#Stage}\app\PlanetPDF.ico
UninstallDisplayIcon={app}\PlanetPDF.exe
UninstallDisplayName={#AppName}
WizardStyle=modern
; PlanetPDF's pictures instead of Inno Setup's own (scripts\make_installer_images.py; Setup picks
; the size for the screen's scaling)
WizardImageFile=images\wizard-164x314.png,images\wizard-192x386.png,images\wizard-246x459.png,images\wizard-273x556.png,images\wizard-328x604.png,images\wizard-355x700.png,images\wizard-410x797.png
WizardSmallImageFile=images\small-55x55.png,images\small-64x68.png,images\small-69x69.png,images\small-83x80.png,images\small-92x97.png,images\small-110x106.png,images\small-119x123.png,images\small-138x140.png
Compression=lzma2/normal
SolidCompression=no
CloseApplications=yes
RestartApplications=no
ChangesAssociations=yes
ChangesEnvironment=yes
; every task starts ticked on every install, the desktop shortcut too (user 2026-10-24: "default to
; creating a desktop shortcut every time"), not as last time's answers
UsePreviousTasks=no

[Types]
Name: "full"; Description: "Full installation (with natural reading voices)"
Name: "compact"; Description: "Without the natural reading voices (smaller)"
Name: "custom"; Description: "Custom"; Flags: iscustom

[Components]
Name: "main"; Description: "PlanetPDF (with OCR and Ghostscript for PDF/A, PDF/X, PDF/E)"; Types: full compact custom; Flags: fixed
Name: "voices"; Description: "Natural reading voices for Read Out Loud (about 460 MB; without them it uses the Windows voices)"; Types: full
Name: "printer"; Description: "The PlanetPDF printer: print from any program to a PDF that opens in PlanetPDF (PlanetPDF asks where to save it)"; Types: full compact

[Files]
; the launcher, at the top
Source: "{#Stage}\app\*"; DestDir: "{app}"; Excludes: "PlanetPDF.ico,printer\*"; Components: main; Flags: ignoreversion recursesubdirs createallsubdirs
; the PlanetPDF printer's service and its setup program (run below)
Source: "{#Stage}\app\printer\*"; DestDir: "{app}\printer"; Components: printer; Flags: ignoreversion
; the program, in its version folder
Source: "{#Stage}\versions\{#AppVersion}\*"; DestDir: "{app}\versions\{#AppVersion}"; Components: main; Flags: ignoreversion recursesubdirs createallsubdirs
; the voices, shared by every version
Source: "{#Models}\*"; DestDir: "{app}\models"; Components: voices; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; (AppUserModelID = windows_shell.APP_USER_MODEL_ID: the taskbar takes its icon from this shortcut)
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\PlanetPDF.exe"; Comment: "Open, edit, sign and protect PDF files"; AppUserModelID: "PlanetNetworks.PlanetPDF"
; the manual as a PDF (Help ▸ PlanetPDF Help shows the same in the program)
Name: "{autoprograms}\{#AppName} Manual"; Filename: "{app}\PlanetPDF Manual.pdf"; Comment: "The PlanetPDF user manual"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\PlanetPDF.exe"; Tasks: desktopicon; AppUserModelID: "PlanetNetworks.PlanetPDF"

[Tasks]
Name: "defaultpdf"; Description: "Make PlanetPDF the default app for opening PDF files (Windows asks you to confirm)"
Name: "desktopicon"; Description: "Create a desktop shortcut"
Name: "addtopath"; Description: "Let command prompts and scripts run planetpdf (adds PlanetPDF to the PATH)"

[Registry]
; the command line: planetpdf in any command prompt runs {app}\PlanetPDF.com (removed again on uninstall)
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NotInPath(ExpandConstant('{app}'))
; "Open with" and Windows' Default apps list for .pdf (each user chooses the default themselves)
Root: HKLM; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueName: ""; ValueData: "PDF Document"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\PlanetPDF.exe,0"
Root: HKLM; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\PlanetPDF.exe"" ""%1"""
Root: HKLM; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\Applications\PlanetPDF.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\Applications\PlanetPDF.exe\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""
Root: HKLM; Subkey: "Software\Classes\Applications\PlanetPDF.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\PlanetPDF.exe"" ""%1"""
Root: HKLM; Subkey: "Software\{#Publisher}\{#AppName}\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\{#Publisher}\{#AppName}\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Open, edit, sign and protect PDF files"
Root: HKLM; Subkey: "Software\{#Publisher}\{#AppName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "{#ProgId}"
; where PlanetPDF is (the PlanetPDF Print Service starts the launcher from here)
Root: HKLM; Subkey: "Software\{#Publisher}\{#AppName}"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#AppName}"; ValueData: "Software\{#Publisher}\{#AppName}\Capabilities"; Flags: uninsdeletevalue

[Run]
; Every entry first points current.txt at this version (SetCurrentVersion): Inno runs these
; entries BEFORE CurStepChanged(ssPostInstall), so the launcher (PlanetPDF.exe) would otherwise start
; the version installed before (user 2026-10-24: installing 0.1.8 started 0.1.0)
; the PlanetPDF printer (the PlanetPDF Print Service, its pipe port, the printer)
Filename: "{app}\printer\planetpdf-printer-setup.exe"; Parameters: "install"; Components: printer; Flags: runhidden waituntilterminated; StatusMsg: "Adding the PlanetPDF printer..."; BeforeInstall: SetCurrentVersion
; Windows' "How do you want to open .pdf files?" chooser (PlanetPDF.exe --choose-pdf-app), as the
; user who ran Setup, not the administrator
Filename: "{app}\PlanetPDF.exe"; Parameters: "--choose-pdf-app"; Tasks: defaultpdf; Flags: nowait runasoriginaluser skipifsilent; StatusMsg: "Asking Windows which app opens PDF files..."; BeforeInstall: SetCurrentVersion
Filename: "{app}\PlanetPDF.exe"; Description: "Start PlanetPDF"; Flags: nowait postinstall skipifsilent runasoriginaluser; BeforeInstall: SetCurrentVersion

[UninstallRun]
Filename: "{app}\printer\planetpdf-printer-setup.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemovePlanetPDFPrinter"

[InstallDelete]
; the Python launcher's runtime folder (before 2026-10-07; the launcher is now one native exe)
Type: filesandordirs; Name: "{app}\_internal"

[UninstallDelete]
; every version folder (also ones added by updates) and the voices
Type: filesandordirs; Name: "{app}\versions"
Type: filesandordirs; Name: "{app}\models"
Type: files; Name: "{app}\current.txt"
; what the updater's administrator step wrote (update-status: security review 2026-11-21)
Type: filesandordirs; Name: "{app}\update-status"
Type: dirifempty; Name: "{app}"

[Code]
const EnvironmentKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';

{ The taskbar button's icon (user 2026-10-25: "the taskbar icon is a generic program file icon").
  Setup runs its wizard from a temporary "<name>.tmp" copy, and Windows 11's taskbar draws the icon
  of that file, the generic one, although the window itself has PlanetPDF's (its title bar shows it).
  With an app identity of its own, the taskbar uses the window's icon instead. }
function SetCurrentProcessExplicitAppUserModelID(AppID: String): HRESULT;
  external 'SetCurrentProcessExplicitAppUserModelID@shell32.dll stdcall';

function InitializeSetup: Boolean;
begin
  SetCurrentProcessExplicitAppUserModelID('PlanetNetworks.PlanetPDF.Setup');
  Result := True;
end;

function InitializeUninstall: Boolean;
begin
  SetCurrentProcessExplicitAppUserModelID('PlanetNetworks.PlanetPDF.Setup');
  Result := True;
end;

{ Whether Folder is already one of the PATH's entries. }
function NotInPath(Folder: String): Boolean;
var
  Path: String;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, EnvironmentKey, 'Path', Path) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Folder) + ';', ';' + Uppercase(Path) + ';') = 0;
end;

{ Uninstalling takes PlanetPDF's folder out of the PATH again. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Path, Folder: String;
  P: Integer;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, EnvironmentKey, 'Path', Path) then
    exit;
  Folder := ExpandConstant('{app}');
  P := Pos(';' + Uppercase(Folder) + ';', ';' + Uppercase(Path) + ';');
  if P = 0 then
    exit;
  Delete(Path, P - 1, Length(Folder) + 1);
  if (Length(Path) > 0) and (Path[1] = ';') then
    Delete(Path, 1, 1);
  RegWriteExpandStringValue(HKEY_LOCAL_MACHINE, EnvironmentKey, 'Path', Path);
end;

// The PlanetPDF Print Service runs from {app}\printer: stop it before its files are replaced
// (setup's "install" step starts it again).
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Setup: String;
  Code: Integer;
begin
  Result := '';
  Setup := ExpandConstant('{app}\printer\planetpdf-printer-setup.exe');
  if FileExists(Setup) then
    Exec(Setup, 'stop', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

{ After a yes to "Make PlanetPDF the default app", say what Windows will ask. }
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine + MemoTypeInfo + NewLine + NewLine + MemoComponentsInfo;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + NewLine + MemoTasksInfo;
  if WizardIsTaskSelected('defaultpdf') then
    Result := Result + NewLine + NewLine + 'After installing, Windows asks how you want to open PDF files:'
      + NewLine + Space + 'choose PlanetPDF (and Always, if Windows offers it).';
end;

{ Older version folders nobody is running any more go (a running one can't be renamed:
  it's left, and removed by a later update or install). }
procedure RemoveOldVersions;
var
  Find: TFindRec;
  Folder, Name: String;
begin
  Folder := ExpandConstant('{app}\versions\');
  if FindFirst(Folder + '*', Find) then
  try
    repeat
      Name := Find.Name;
      if (Find.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0) and (Name <> '.') and (Name <> '..')
         and (Name <> '{#AppVersion}') then
      begin
        if RenameFile(Folder + Name, Folder + Name + '.old') or (Pos('.old', Name) > 0) then
        begin
          if Pos('.old', Name) > 0 then
            DelTree(Folder + Name, True, True, True)
          else
            DelTree(Folder + Name + '.old', True, True, True);
        end;
      end;
    until not FindNext(Find);
  finally
    FindClose(Find);
  end;
end;

// "Did you know?" tips on the Installing page (user 2026-10-23: "tips and tricks and features as
// you run through the installation of files"): a new one every TipSeconds while files are copied,
// or at once with "Next tip".
const TipSeconds = 9;

var
  Tips: array of String;
  CurrentTip: Integer;
  TipShownAt: DWORD;
  TipHeading, TipText, TipCounter, TipNext: TNewStaticText;

function GetTickCount: DWORD; external 'GetTickCount@kernel32.dll stdcall';

procedure AddTip(Text: String);
begin
  SetArrayLength(Tips, GetArrayLength(Tips) + 1);
  Tips[GetArrayLength(Tips) - 1] := Text;
end;

procedure ShowTip(Index: Integer);
begin
  CurrentTip := Index mod GetArrayLength(Tips);
  TipText.Caption := Tips[CurrentTip];
  TipCounter.Caption := Format('Tip %d of %d', [CurrentTip + 1, GetArrayLength(Tips)]);
  TipShownAt := GetTickCount;
end;

procedure NextTipClick(Sender: TObject);
begin
  ShowTip(CurrentTip + 1);
end;

procedure InitializeWizard;
var
  Page: TNewNotebookPage;
  Gauge: TNewProgressBar;
begin
  AddTip('Press F1 in PlanetPDF for the full user manual, with contents, an index and search.');
  AddTip('Open several PDFs at once: each gets its own tab. Drag a tab out of the window to give that document a window of its own.');
  AddTip('Fill & Sign: draw, type or import your signature once, then place it on any document with one click.');
  AddTip('Redact removes text for good instead of just covering it. Mark words, Social Security numbers, phone numbers or whole areas, then Apply: PlanetPDF checks the result before you share it.');
  AddTip('Scan & OCR ▸ Recognize Text turns scanned pages into text you can search, select and copy.');
  AddTip('Optimize Scans straightens, cleans and shrinks scanned pages, and Compress PDF makes large files much smaller.');
  AddTip('Edit PDF: click a line of text to select it, then click it again to change the words. Drag it to move it, even onto another page.');
  AddTip('Prepare Form finds the blanks on a page (lines, boxes, "Name:" labels) and turns them into fields people can fill in.');
  AddTip('Drag pages from the Pages panel into another document''s window to copy them there.');
  AddTip('Compare shows what changed between two versions of a document, word by word.');
  AddTip('Comment: highlight words and type a note. Others can reply under it, just as in Acrobat.');
  AddTip('View ▸ Read Out Loud reads the page to you in a natural voice.');
  AddTip('Print from any program to the PlanetPDF printer to make a PDF that opens straight in PlanetPDF.');
  AddTip('The keyboard shortcuts are Adobe Acrobat''s, so the ones you know already work. Help ▸ Keyboard Shortcuts lists them all.');
  AddTip('Everything PlanetPDF does can be scripted: type "planetpdf help" in a command prompt to see the commands.');

  Page := WizardForm.InstallingPage;
  Gauge := WizardForm.ProgressGauge;

  TipHeading := TNewStaticText.Create(WizardForm);
  TipHeading.Parent := Page;
  TipHeading.Left := Gauge.Left;
  TipHeading.Top := Gauge.Top + Gauge.Height + ScaleY(28);
  TipHeading.Caption := 'Did you know?';
  TipHeading.Font.Style := [fsBold];

  TipText := TNewStaticText.Create(WizardForm);
  TipText.Parent := Page;
  TipText.ShowAccelChar := False;  { ("Fill & Sign", "Scan & OCR": an "&" is text, not a shortcut mark) }
  TipText.Left := Gauge.Left;
  TipText.Top := TipHeading.Top + TipHeading.Height + ScaleY(6);
  TipText.AutoSize := False;
  TipText.WordWrap := True;
  TipText.Width := Gauge.Width;
  TipText.Height := ScaleY(64);

  TipCounter := TNewStaticText.Create(WizardForm);
  TipCounter.Parent := Page;
  TipCounter.Left := Gauge.Left;
  TipCounter.Top := TipText.Top + TipText.Height + ScaleY(4);
  TipCounter.Font.Color := clGray;

  TipNext := TNewStaticText.Create(WizardForm);
  TipNext.Parent := Page;
  TipNext.Caption := 'Next tip ›';
  TipNext.Font.Color := clBlue;
  TipNext.Font.Style := [fsUnderline];
  TipNext.Cursor := crHand;
  TipNext.OnClick := @NextTipClick;
  TipNext.Top := TipCounter.Top;
  TipNext.Left := Gauge.Left + Gauge.Width - TipNext.Width;

  ShowTip(0);
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
begin
  if GetTickCount - TipShownAt >= TipSeconds * 1000 then
    ShowTip(CurrentTip + 1);
end;

{ The launcher starts the version current.txt names: this one, from the moment its files are in. }
procedure SetCurrentVersion;
begin
  SaveStringToFile(ExpandConstant('{app}\current.txt'), '{#AppVersion}' + #13#10, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    ShowTip(0);
  if CurStep = ssPostInstall then
  begin
    SetCurrentVersion;  { (again: with no [Run] entry chosen, this is the first time) }
    RemoveOldVersions;
  end;
end;
