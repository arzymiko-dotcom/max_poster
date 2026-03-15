; Inno Setup Script — max_poster

[Setup]
AppName=max_poster
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
AppPublisher=MaxPoster
SetupIconFile="assets\max_poster.ico"
DefaultDirName={autopf}\max_poster
DefaultGroupName=max_poster
OutputBaseFilename=max_poster_setup
OutputDir=installer
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "dist\max_poster\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\max_poster.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\max_poster"; Filename: "{app}\max_poster.exe"; IconFilename: "{app}\max_poster.ico"
Name: "{autodesktop}\max_poster"; Filename: "{app}\max_poster.exe"; Tasks: desktopicon; IconFilename: "{app}\max_poster.ico"

[Run]
Filename: "{app}\max_poster.exe"; Description: "Запустить max_poster"; Flags: nowait postinstall skipifsilent