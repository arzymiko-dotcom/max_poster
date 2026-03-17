; Inno Setup Script — MAX POST

[Setup]
AppName=MAX POST
AppId={{B7F32A14-5C8E-4D92-A1B3-F456789ABCDE}
; Чтение версии из файла version.txt с помощью препроцессора
#define FileHandle
#define FileLine
#define AppVersion
#define FileHandle = FileOpen("version.txt")
#if FileHandle
  #define FileLine = FileRead(FileHandle)
  #expr FileClose(FileHandle)
  #define AppVersion = FileLine
#else
  #define AppVersion = "1.1.5"
#endif
AppVersion={#AppVersion}
; Если нужна ручная версия, закомментируй всё, что выше, и раскомментируй следующую строку:
; AppVersion=1.1.5
AppPublisher=MAX POST
SetupIconFile="assets\MAX POST.ico"
DefaultDirName={autopf}\MAX POST
DefaultGroupName=MAX POST
OutputBaseFilename=MAX POST_setup
OutputDir=installer
Compression=lzma
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=*.exe

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "dist\MAX POST\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".env"
Source: "dist\MAX POST\.env"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "assets\MAX POST.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\MAX POST"; Filename: "{app}\MAX POST.exe"; IconFilename: "{app}\MAX POST.ico"
Name: "{autodesktop}\MAX POST"; Filename: "{app}\MAX POST.exe"; Tasks: desktopicon; IconFilename: "{app}\MAX POST.ico"

[Run]
Filename: "{app}\MAX POST.exe"; Description: "Запустить MAX POST"; Flags: nowait postinstall skipifsilent
