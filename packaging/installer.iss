; Inno Setup script — Jarvis (installation par utilisateur, sans admin).
;
; Compile avec :  iscc packaging/installer.iss /DVERSION=1.1.2 /DSourceDir=..\\dist
; Le programme est installé dans %LOCALAPPDATA%\\Jarvis (per-user) afin que le
; launcher puisse remplacer l'application à chaque mise à jour sans demander de
; droits administrateur. Les DONNÉES UTILISATEUR (config, mémoire, logs,
; routines, modèles) vivent dans le même dossier mais ne sont JAMAIS supprimées
; lors d'une mise à jour ni par l'uninstaller.
;
; ARCHITECTURE CRITIQUE (PyInstaller 6.x) :
;   dist/app/ contient :
;     Jarvis.exe
;     _internal/
;       python311.dll
;       base_library.zip
;       PySide6/
;       ...
;   Cette structure DOIT être préservée exactement par l'installateur.
;   Si python311.dll se retrouve à la racine de app/ au lieu de app/_internal/,
;   l'application échoue avec :
;     [PYI-...:ERROR] Failed to load Python DLL '...app\_internal\python311.dll'
;
;   Pour préserver _internal/, on utilise recursesubdirs + createallsubdirs.
;   NE PAS utiliser de wildcard qui pourrait aplatir la structure.

#ifndef VERSION
  #define VERSION "1.1.2"
#endif
#ifndef SourceDir
  #define SourceDir "..\\dist"
#endif
#ifndef IconFile
  #define IconFile "..\\assets\\jarvis.ico"
#endif

#define AppName "Jarvis"
#define AppPublisher "Jarvis"
#define AppExeName "JarvisLauncher.exe"
; Dossier d'installation PAR DÉFAUT (per-user). Uniquement destiné à
; DefaultDirName. NE JAMAIS l'utiliser comme DestDir/Filename dans les
; sections runtime : {#AppDir} est une constante de PRÉPROCESSEUR qui
; s'étend en la chaîne littérale "{localappdata}\Jarvis", que Inno résout
; alors à l'exécution SANS tenir compte du dossier réellement choisi
; (/DIR=..., page de destination, ...). Bug historique : l'uninstaller
; était écrit dans {app} mais les fichiers partaient dans
; %LOCALAPPDATA%\Jarvis. Les sections [Files]/[Icons]/[Run]/
; [UninstallDelete]/[Code] ci-dessous utilisent donc la constante runtime
; {app}, qui reflète toujours le dossier d'installation réel.
#define AppDir "{localappdata}\\Jarvis"

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
; L'installation ne réinitialise pas les données utilisateur existantes.
; On ne supprime jamais le contenu de l'installation lors d'une mise à jour.

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le bureau"; GroupDescription: "Raccourcis:"
Name: "startmenuicon"; Description: "Créer un raccourci dans le menu Démarrer"; GroupDescription: "Raccourcis:"

[Files]
; Application (dossier app/ remplacé à chaque mise à jour).
; CRITIQUE : recursesubdirs + createallsubdirs préservent _internal/
; Sans ces flags, python311.dll pourrait être aplati et l'app ne démarrerait plus.
Source: "{#SourceDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
; Launcher (stable, non remplacé par les mises à jour in-app).
Source: "{#SourceDir}\JarvisLauncher.exe"; DestDir: "{app}"; Flags: ignoreversion
; Marqueur de version.
Source: "{#SourceDir}\version.json"; DestDir: "{app}"; Flags: ignoreversion
; Icône.
Source: "{#IconFile}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Menu Démarrer -> launcher.
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\jarvis.ico"; Tasks: startmenuicon
; Bureau -> launcher.
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\jarvis.ico"; Tasks: desktopicon

[Run]
; Proposer de lancer Jarvis après l'installation.
Filename: "{app}\{#AppExeName}"; Description: "Lancer {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Uniquement les fichiers que nous avons installés — jamais les données.
Type: filesandordirs; Name: "{app}\app"
Type: files; Name: "{app}\{#AppExeName}"
Type: files; Name: "{app}\version.json"
Type: files; Name: "{app}\jarvis.ico"

[Code]
// Validation après installation : vérifie que _internal/python311.dll existe
// Si ce fichier manque, l'installation est corrompue (flattening bug)
procedure CurStepChanged(CurStep: TSetupStep);
var
  InternalDll: String;
begin
  if CurStep = ssPostInstall then
  begin
    InternalDll := ExpandConstant('{app}\app\_internal\python311.dll');
    if not FileExists(InternalDll) then
    begin
      // Ne bloque pas l'installation, mais avertit
      // Le launcher fera aussi une validation au démarrage
      Log('ATTENTION: _internal/python311.dll manquant après installation: ' + InternalDll);
      Log('Cela indique un bug de packaging (flattening)');
    end
    else
    begin
      Log('Validation OK: _internal/python311.dll présent: ' + InternalDll);
    end;
  end;
end;
