; Inno Setup Script for PFR Sentinel
; Creates a Windows installer that supports upgrades
; Requires: Inno Setup 6.0 or later (https://jrsoftware.org/isinfo.php)
; Version is automatically synced from ../version.py by build scripts

#define MyAppName "PFR Sentinel"
#include "..\version.iss"
#define MyAppPublisher "Paul Fox-Reeks"
#define MyAppExeName "PFRSentinel.exe"
#define MyAppAssocName MyAppName + " File"
#define MyAppAssocExt ".pfrs"
#define MyAppAssocKey StringChange(MyAppAssocName, " ", "") + MyAppAssocExt

[Setup]
; NOTE: The value of AppId uniquely identifies this application.
; Do not use the same AppId value in installers for other applications.
; New GUID for renamed app - existing ASIOverlayWatchDog installs won't conflict
AppId={{7F8E9A0B-1C2D-3E4F-5A6B-7C8D9E0F1A2B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\PFRSentinel
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Output directory for installer (absolute path to avoid nesting)
OutputDir=..\installer\dist
OutputBaseFilename={#MyAppName}-{#MyAppVersion}-setup
; Compression
Compression=lzma
SolidCompression=yes
; Modern UI
WizardStyle=modern
; Privileges (run as user, not admin)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Uninstall
UninstallDisplayIcon={app}\{#MyAppExeName}
; Setup icon
SetupIconFile=..\assets\app_icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "runadmin"; Description: "Run as Administrator (recommended for USB camera recovery)"; GroupDescription: "Privileges:"; Flags: unchecked

[Files]
; Source files from PyInstaller build
Source: "..\dist\PFRSentinel\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; NOTE: Don't use "Flags: ignoreversion" on any shared system files

[Icons]
; Start Menu shortcut
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
; Desktop shortcut (optional, user-selectable)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; If user selected "Run as Administrator", set Windows compatibility flag
; This makes the EXE always prompt for UAC elevation (same as right-click > Properties > Compatibility > Run as administrator)
Root: HKCU; Subkey: "Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"; ValueType: string; ValueName: "{app}\{#MyAppExeName}"; ValueData: "RUNASADMIN"; Flags: uninsdeletevalue; Tasks: runadmin

[Run]
; Option to launch application after install
; shellexec flag allows UAC elevation if "Run as Administrator" task was selected
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent shellexec

[UninstallDelete]
; Clean up any generated files (but NOT user data in %LOCALAPPDATA%)
Type: filesandordirs; Name: "{app}\build"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
{ Customize the finished page with data location and analytics notice }
procedure CurPageChanged(CurPageID: Integer);
var
  Msg: String;
begin
  if CurPageID = wpFinished then
  begin
    Msg := 'Setup has finished installing {#MyAppName} on your computer.' + #13#10 + #13#10 +
           'User data is stored in:' + #13#10 +
           ExpandConstant('{localappdata}\PFRSentinel\') + #13#10 + #13#10 +
           'Anonymous usage analytics is enabled by default to help improve ' +
           'the app. No personal data is collected. You can disable this ' +
           'in Settings > System.';
    WizardForm.FinishedLabel.Height := WizardForm.FinishedLabel.Height + ScaleY(40);
    WizardForm.FinishedLabel.Caption := Msg;
  end;
end;

{ Detect old ASIOverlayWatchDog installation by searching registry }
function GetOldAppUninstallString: String;
var
  sUnInstPath: String;
  sUnInstallString: String;
  sDisplayName: String;
  Keys: TArrayOfString;
  i: Integer;
begin
  Result := '';
  { Search for ASIOverlayWatchDog in uninstall registry - check HKCU first (lowest privileges) }
  if RegGetSubkeyNames(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall', Keys) then
  begin
    for i := 0 to GetArrayLength(Keys) - 1 do
    begin
      sUnInstPath := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + Keys[i];
      if RegQueryStringValue(HKCU, sUnInstPath, 'DisplayName', sDisplayName) then
      begin
        if Pos('ASIOverlayWatchDog', sDisplayName) > 0 then
        begin
          RegQueryStringValue(HKCU, sUnInstPath, 'UninstallString', sUnInstallString);
          Result := sUnInstallString;
          Exit;
        end;
      end;
    end;
  end;
  { Also check HKLM }
  if RegGetSubkeyNames(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall', Keys) then
  begin
    for i := 0 to GetArrayLength(Keys) - 1 do
    begin
      sUnInstPath := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + Keys[i];
      if RegQueryStringValue(HKLM, sUnInstPath, 'DisplayName', sDisplayName) then
      begin
        if Pos('ASIOverlayWatchDog', sDisplayName) > 0 then
        begin
          RegQueryStringValue(HKLM, sUnInstPath, 'UninstallString', sUnInstallString);
          Result := sUnInstallString;
          Exit;
        end;
      end;
    end;
  end;
end;

function HasOldAppInstalled: Boolean;
begin
  Result := (GetOldAppUninstallString <> '');
end;

function UninstallOldApp: Integer;
var
  sUnInstallString: String;
  iResultCode: Integer;
begin
  Result := 0;
  sUnInstallString := GetOldAppUninstallString;
  if sUnInstallString <> '' then begin
    sUnInstallString := RemoveQuotes(sUnInstallString);
    if Exec(sUnInstallString, '/SILENT /NORESTART /SUPPRESSMSGBOXES','', SW_HIDE, ewWaitUntilTerminated, iResultCode) then
      Result := 1
    else
      Result := 2;
  end;
end;

{ Detect and handle previous installation }
function GetUninstallString: String;
var
  sUnInstPath: String;
  sUnInstallString: String;
begin
  sUnInstPath := ExpandConstant('Software\Microsoft\Windows\CurrentVersion\Uninstall\{#emit SetupSetting("AppId")}_is1');
  sUnInstallString := '';
  if not RegQueryStringValue(HKLM, sUnInstPath, 'UninstallString', sUnInstallString) then
    RegQueryStringValue(HKCU, sUnInstPath, 'UninstallString', sUnInstallString);
  Result := sUnInstallString;
end;

function IsUpgrade: Boolean;
begin
  Result := (GetUninstallString <> '');
end;

function UnInstallOldVersion: Integer;
var
  sUnInstallString: String;
  iResultCode: Integer;
begin
  { Return Values: }
  { 1 - uninstall string is empty }
  { 2 - error executing the UnInstallString }
  { 3 - successfully executed the UnInstallString }

  { default return value }
  Result := 0;

  { get the uninstall string of the old app }
  sUnInstallString := GetUninstallString;
  if sUnInstallString <> '' then begin
    sUnInstallString := RemoveQuotes(sUnInstallString);
    if Exec(sUnInstallString, '/SILENT /NORESTART /SUPPRESSMSGBOXES','', SW_HIDE, ewWaitUntilTerminated, iResultCode) then
      Result := 3
    else
      Result := 2;
  end else
    Result := 1;
end;

{ ===================================================================== }
{  PostHog Analytics — track install/upgrade/error events                }
{ ===================================================================== }

var
  PostHogDistinctId: String;   { Cached for the lifetime of the installer }
  PostHogIsUpgrade: Boolean;

function HexDigit(N: Integer): Char;
begin
  if N < 10 then
    Result := Chr(Ord('0') + N)
  else
    Result := Chr(Ord('a') + N - 10);
end;

function RandomHex(Len: Integer): String;
var
  i: Integer;
begin
  Result := '';
  for i := 1 to Len do
    Result := Result + HexDigit(Random(16));
end;

function GenerateUUID: String;
{ Generate a v4-like UUID: 8-4-4-4-12 hex }
begin
  Result := RandomHex(8) + '-' + RandomHex(4) + '-4' + RandomHex(3) + '-' +
            HexDigit(8 + Random(4)) + RandomHex(3) + '-' + RandomHex(12);
end;

function GetConfigPath: String;
begin
  Result := ExpandConstant('{localappdata}\PFRSentinel\config.json');
end;

function GetConfigDir: String;
begin
  Result := ExpandConstant('{localappdata}\PFRSentinel');
end;

function ReadConfigFile: String;
{ Load entire config.json into a single string, or '' if missing }
var
  Lines: TArrayOfString;
  i: Integer;
begin
  Result := '';
  if FileExists(GetConfigPath) then
  begin
    if LoadStringsFromFile(GetConfigPath, Lines) then
      for i := 0 to GetArrayLength(Lines) - 1 do
        Result := Result + Lines[i];
  end;
end;

function ReadJsonValue(const Json, Key: String): String;
{ Minimal JSON string-value reader — finds "key": "value" }
var
  SearchKey, Rest: String;
  StartPos, EndPos: Integer;
begin
  Result := '';
  SearchKey := '"' + Key + '"';
  StartPos := Pos(SearchKey, Json);
  if StartPos = 0 then Exit;

  Rest := Copy(Json, StartPos + Length(SearchKey), Length(Json));
  StartPos := Pos('"', Rest);
  if StartPos = 0 then Exit;
  Rest := Copy(Rest, StartPos + 1, Length(Rest));
  EndPos := Pos('"', Rest);
  if EndPos = 0 then Exit;
  Result := Copy(Rest, 1, EndPos - 1);
end;

function IsAnalyticsEnabled: Boolean;
{ Check if user has opted out via config. Default is enabled. }
var
  ConfigText: String;
begin
  Result := True;
  ConfigText := ReadConfigFile;
  if (Pos('"analytics_enabled": false', ConfigText) > 0) or
     (Pos('"analytics_enabled":false', ConfigText) > 0) then
    Result := False;
end;

function GetOrCreateDistinctId: String;
{ Read posthog_distinct_id from config, or generate a UUID and seed it }
var
  ConfigText, ConfigDir: String;
begin
  { Return cached value if already resolved }
  if PostHogDistinctId <> '' then
  begin
    Result := PostHogDistinctId;
    Exit;
  end;

  { Try to read from existing config (upgrade path) }
  ConfigText := ReadConfigFile;
  if ConfigText <> '' then
    Result := ReadJsonValue(ConfigText, 'posthog_distinct_id');

  { Generate a new UUID for fresh installs }
  if Result = '' then
  begin
    Result := GenerateUUID;

    { Seed config.json so the app picks up the same distinct_id on first launch.
      Only create the file if it doesn't exist — never overwrite an existing
      config (the app will generate its own distinct_id on first launch). }
    if not FileExists(GetConfigPath) then
    begin
      ConfigDir := GetConfigDir;
      if not DirExists(ConfigDir) then
        ForceDirectories(ConfigDir);

      SaveStringToFile(GetConfigPath,
        '{' + #13#10 +
        '    "posthog_distinct_id": "' + Result + '"' + #13#10 +
        '}' + #13#10,
        False);
      Log('PostHog: seeded distinct_id into config.json for fresh install');
    end
    else
      Log('PostHog: config.json exists but lacks distinct_id, app will generate on first launch');
  end;

  PostHogDistinctId := Result;
end;

procedure SendPostHogEvent(const EventName, PropertiesJson: String);
{ Fire-and-forget POST to PostHog capture API.
  PropertiesJson should be the inner JSON object body (no outer braces). }
var
  Http: Variant;
  Json: String;
  DistinctId: String;
begin
  if not IsAnalyticsEnabled then Exit;

  DistinctId := GetOrCreateDistinctId;
  try
    Json := '{' +
      '"api_key": "phc_yZQPicEvLtuwo4ws6uMCX2RuLc23fsJVbrh7PdSBggyt",' +
      '"event": "' + EventName + '",' +
      '"distinct_id": "' + DistinctId + '",' +
      '"properties": {' +
        '"version": "{#MyAppVersion}",' +
        '"installer": "inno_setup"' +
        PropertiesJson +
      '}' +
    '}';

    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Http.Open('POST', 'https://us.i.posthog.com/capture/', False);
    Http.SetRequestHeader('Content-Type', 'application/json');
    Http.SetTimeouts(2000, 2000, 2000, 2000);
    Http.Send(Json);
    Log('PostHog: sent ' + EventName + ' event');
  except
    Log('PostHog: failed to send event (non-critical)');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  InstallType: String;
begin
  if (CurStep=ssInstall) then
  begin
    { Cache upgrade status before anything changes }
    PostHogIsUpgrade := IsUpgrade;

    { Uninstall old ASIOverlayWatchDog if present }
    if HasOldAppInstalled then
    begin
      Log('Found old ASIOverlayWatchDog installation, uninstalling...');
      UninstallOldApp;
    end;

    if PostHogIsUpgrade then
    begin
      // Don't uninstall PFRSentinel - just overwrite files to preserve user data
      // Config.json is now stored in %LOCALAPPDATA%\PFRSentinel\
      // so it won't be affected by upgrades anyway
    end;

    { Send install_started so we can detect failed installs
      (install_started without a matching app_installed = failure) }
    if PostHogIsUpgrade then
      InstallType := 'upgrade'
    else
      InstallType := 'fresh';
    SendPostHogEvent('install_started',
      ',"install_type": "' + InstallType + '"');
  end;

  if (CurStep=ssPostInstall) then
  begin
    { Send success event after files are written }
    if PostHogIsUpgrade then
      InstallType := 'upgrade'
    else
      InstallType := 'fresh';
    SendPostHogEvent('app_installed',
      ',"install_type": "' + InstallType + '"');
  end;
end;
