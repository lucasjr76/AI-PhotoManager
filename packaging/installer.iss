; Windows installer (Inno Setup 6). Built by CI:  iscc /DAppVersion=0.1.0 packaging\installer.iss
; Per-user install (no admin), Start menu + optional desktop shortcut, clean uninstall.
; The indexed data lives in %LOCALAPPDATA%\AI-PhotoDocsManager and is kept on uninstall.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6F3C1E2A-8B7D-4C5E-9A1F-2D4B6E8C0A13}
AppName=AI-PhotoDocsManager
AppVersion={#AppVersion}
AppPublisher=Luiz Carlos da Silveira Junior
DefaultDirName={localappdata}\Programs\AI-PhotoDocsManager
DefaultGroupName=AI-PhotoDocsManager
PrivilegesRequired=lowest
OutputDir=..\build\installer
OutputBaseFilename=AI-PhotoDocsManager-{#AppVersion}-windows-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\AI-PhotoDocsManager.exe
Compression=lzma2/fast
SolidCompression=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; Flags: unchecked

[Files]
Source: "..\build\dist\AI-PhotoDocsManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AI-PhotoDocsManager"; Filename: "{app}\AI-PhotoDocsManager.exe"
Name: "{group}\Desinstalar AI-PhotoDocsManager"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AI-PhotoDocsManager"; Filename: "{app}\AI-PhotoDocsManager.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AI-PhotoDocsManager.exe"; Description: "Abrir o AI-PhotoDocsManager"; Flags: nowait postinstall skipifsilent
