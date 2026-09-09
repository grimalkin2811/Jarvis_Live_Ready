; Inno Setup script — Jarvis (installation par utilisateur, sans admin).
;
; Compile avec :  iscc packaging/installer.iss /DVERSION=1.0.0 /DSourceDir=dist
;
; Le programme est installé dans %LOCALAPPDATA%\Jarvis (per-user) afin que le
; launcher puisse remplacer l'application à chaque mise à jour sans demander de
; droits administrateur. Les DONNÉES UTILISATEUR (config, mémoire, logs,
; routines, modèles) vivent dans le même dossier mais ne sont JAMAIS supprimées
; lors d'une mise à jour ni par l'uninstaller.

#ifndef VERSION
  #define VERSION "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist"
#endif
#ifndef IconFile
  #define IconFile "..\assets\jarvis.ico"
#endif

#define AppName "Jarvis"
#define AppPublisher "Jarvis"
#define AppExeName "JarvisLauncher.exe"
#define AppDir "{localappdata}\Jarvis"

[Setup]
AppId={{8C4B4B6E-7C3D-4B7C-9A2F-9D5E6F1A3B2C}
AppName={#AppName}
AppVersion={#VERSION}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/grimalkin2811/Jarvis_Live_Ready
DefaultDirName={#AppDir}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=JarvisSetup-{#VERSION}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={#IconFile}
ArchitecturesInstallIn64BitMode=x64compatible
; Ne pas créer de dossier de programme dans le menu Démarrer (un seul raccourci).
DisableProgramGroupPage=yes
; L'installation ne réinitialise pas les données utilisateur existantes.
; On ne supprime jamais le contenu de l'installation lors d'une mise à jour.

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le bureau"; GroupDescription: "Raccourcis:"
Name: "startmenuicon"; Description: "Créer un raccourci dans le menu Démarrer"; GroupDescription: "Raccourcis:"

[Files]
; Application (dossier app/ remplacé à chaque mise à jour).
Source: "{#SourceDir}\app\*"; DestDir: "{#AppDir}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
; Launcher (stable, non remplacé par les mises à jour in-app).
Source: "{#SourceDir}\JarvisLauncher.exe"; DestDir: "{#AppDir}"; Flags: ignoreversion
; Marqueur de version.
Source: "{#SourceDir}\version.json"; DestDir: "{#AppDir}"; Flags: ignoreversion
; Icône.
Source: "{#IconFile}"; DestDir: "{#AppDir}"; Flags: ignoreversion

[Icons]
; Menu Démarrer -> launcher.
Name: "{group}\{#AppName}"; Filename: "{#AppDir}\{#AppExeName}"; IconFilename: "{#AppDir}\jarvis.ico"; Tasks: startmenuicon
; Bureau -> launcher.
Name: "{autodesktop}\{#AppName}"; Filename: "{#AppDir}\{#AppExeName}"; IconFilename: "{#AppDir}\jarvis.ico"; Tasks: desktopicon

[Run]
; Proposer de lancer Jarvis après l'installation.
Filename: "{#AppDir}\{#AppExeName}"; Description: "Lancer {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Uniquement les fichiers que nous avons installés — jamais les données.
Type: filesandordirs; Name: "{#AppDir}\app"
Type: files; Name: "{#AppDir}\{#AppExeName}"
Type: files; Name: "{#AppDir}\version.json"
Type: files; Name: "{#AppDir}\jarvis.ico"
