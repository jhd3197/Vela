; Built from the exact portable bundle that passes the server smoke test.
#ifndef AppVersion
  #error AppVersion must be supplied by build-windows-installer.py
#endif
#ifndef StartupValue
  #define StartupValue "Vela Server"
#endif
#ifndef AppIdentity
  #define AppIdentity "Vela.Server"
#endif

[Setup]
AppId={#AppIdentity}
AppName=Vela Server
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoTextVersion={#AppVersion}
AppPublisher=Vela contributors
AppPublisherURL=https://github.com/jhd3197/Vela
AppSupportURL=https://github.com/jhd3197/Vela/issues
AppUpdatesURL=https://github.com/jhd3197/Vela/releases
DefaultDirName={localappdata}\Programs\Vela
DefaultGroupName=Vela
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDirectory}
OutputBaseFilename=vela-server-{#AppVersion}-windows-x64-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Vela.exe
LicenseFile={#SourceRoot}\LICENSE
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
AppMutex=VelaServerRunning
CloseApplications=no
RestartApplications=no
UninstallDisplayName=Vela Server

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Start Vela when I sign in"; Flags: unchecked

[Files]
Source: "{#BundleDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Vela"; Filename: "{app}\Vela.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Vela"; Filename: "{app}\Vela.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#StartupValue}"; ValueData: """{app}\Vela.exe"" --no-open-browser"; Tasks: autostart

[Run]
Filename: "{app}\Vela.exe"; Description: "Open Vela"; Flags: nowait postinstall skipifsilent

; No [UninstallDelete]: apps, settings and logs in the user's .vela stay intact.

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Command: String;
begin
  if CurUninstallStep = usUninstall then begin
    if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#StartupValue}', Command) then begin
      { Also remove startup enabled later in the tray, but preserve another Vela copy's entry. }
      if Pos(Lowercase('"' + ExpandConstant('{app}\Vela.exe') + '"'), Lowercase(Command)) = 1 then
        RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#StartupValue}');
    end;
  end;
end;
