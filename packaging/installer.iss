; ============================================================================
;  PhotoFlow installer — Inno Setup script
;  Samiksha Technologies
;
;  Build order:
;     1. python packaging/make_version_info.py
;     2. pyinstaller packaging/photoflow.spec --noconfirm
;     3. iscc packaging/installer.iss
;
;  Produces: packaging/output/PhotoFlow-Setup-<version>.exe
;
;  Inno Setup is free: https://jrsoftware.org/isdl.php
;  Run `iscc` from the Inno Setup install directory, or add it to PATH.
; ============================================================================

; MyAppVersion is passed in by build.bat (read from utils/version.py) so the
; installer version can never drift from the application version. The default
; here is only a fallback for running iscc by hand.
#ifndef MyAppVersion
  #define MyAppVersion "0.9.0"
#endif

#define MyAppName "PhotoFlow"
#define MyCompany "Samiksha Technologies"
#define MyAppExeName "PhotoFlow.exe"
#define MyAppURL "https://samikshatech.com"

[Setup]
; A stable AppId is what lets an upgrade replace the previous install instead of
; installing a second copy alongside it. NEVER change this value between
; releases -- generate a new GUID only if you ever ship a genuinely separate
; product.
AppId={{8F3C2A7E-5B41-4E29-9C7D-6A1E4D8B2F30}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyCompany}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/support.html
AppUpdatesURL={#MyAppURL}/download.html
VersionInfoVersion={#MyAppVersion}

DefaultDirName={autopf}\{#MyCompany}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=output
OutputBaseFilename=PhotoFlow-Setup-{#MyAppVersion}
SetupIconFile=photoflow.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

; LZMA2/ultra matters here: the payload is a few hundred MB of Qt and OpenCV
; DLLs, and download size is the difference between someone trying the app and
; giving up.
Compression=lzma2/ultra64
SolidCompression=yes
InternalCompressLevel=ultra64

; 64-bit only, matching the PyInstaller build.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Per-machine install when the user is an admin, per-user otherwise. This avoids
; forcing a UAC prompt on a machine where the studio's staff aren't admins.
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The whole PyInstaller one-folder build. recursesubdirs picks up the bundled
; models, fonts, templates and Qt plugins.
Source: "..\dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only remove things the application generated inside its own folder. The user's
; settings and presets live in %LOCALAPPDATA% and are deliberately LEFT ALONE on
; uninstall, so reinstalling doesn't lose their saved house styles. Removing
; those is what "clean uninstall" tools are for, not us.
Type: filesandordirs; Name: "{app}\logs"

[Code]
function InitializeSetup(): Boolean;
var
  Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);
  { Windows 10 is build 10240 and up. Anything older can't run the Qt6 runtime,
    and failing here with a clear message beats a cryptic DLL error later. }
  if (Version.Major < 10) then
  begin
    MsgBox('PhotoFlow requires Windows 10 or Windows 11.' + #13#10 +
           'This computer is running an older version of Windows.',
           mbCriticalError, MB_OK);
    Result := False;
    exit;
  end;
  Result := True;
end;
