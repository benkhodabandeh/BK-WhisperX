#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
#endif
#define MyAppName "BK WhisperX"
#define MyAppPublisher "Ben Khodabandeh"
#define MyAppURL "https://github.com/benkhodabandeh/BK-WhisperX"
#define MyAppExeName "BK-WhisperX-CLI.exe"

[Setup]
AppId={{DB16F478-67E4-4ED6-95DF-D8165B216F8D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases/latest
DefaultDirName={localappdata}\Programs\BK WhisperX
DefaultGroupName=BK WhisperX
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=BK-WhisperX-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Local WhisperX transcription CLI launcher
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "clicontext"; Description: "Create a Start Menu shortcut for the CLI"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\portable\BK-WhisperX-CLI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\BK WhisperX CLI"; Filename: "{app}\BK-WhisperX-CLI.exe"; WorkingDir: "{app}"; Tasks: clicontext
Name: "{group}\Uninstall BK WhisperX"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\BK-WhisperX-CLI.exe"; Description: "Launch BK WhisperX CLI"; Flags: nowait postinstall skipifsilent