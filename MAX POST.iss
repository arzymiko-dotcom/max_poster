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

[InstallDelete]
Type: filesandordirs; Name: "{app}"

[Files]
Source: "dist\MAX POST\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".env"
Source: "dist\MAX POST\.env"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\MAX POST.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\MAX POST"; Filename: "{app}\MAX POST.exe"; IconFilename: "{app}\MAX POST.ico"
Name: "{autodesktop}\MAX POST"; Filename: "{app}\MAX POST.exe"; Tasks: desktopicon; IconFilename: "{app}\MAX POST.ico"

[Run]
Filename: "{app}\MAX POST.exe"; Description: "Запустить MAX POST"; Flags: nowait postinstall skipifsilent

[Code]
procedure KillProcesses();
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/f /im "MAX POST.exe" /t', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function InitializeSetup(): Boolean;
begin
  KillProcesses();
  Result := True;
end;

// Дописывает в AppData/.env ключи, которых там нет, из {app}/.env
procedure MergeEnvToAppData();
var
  AppEnvPath, UserEnvPath, UserEnvDir: String;
  AppLines, UserLines: TArrayOfString;
  i, j: Integer;
  AppLine, Key: String;
  Found, Modified: Boolean;
begin
  AppEnvPath  := ExpandConstant('{app}\.env');
  UserEnvDir  := ExpandConstant('{userappdata}\MAX POST');
  UserEnvPath := UserEnvDir + '\.env';

  if not FileExists(AppEnvPath) then Exit;

  // Создать папку AppData если нет
  if not DirExists(UserEnvDir) then
    CreateDir(UserEnvDir);

  // Если у пользователя .env ещё нет — просто скопировать целиком
  if not FileExists(UserEnvPath) then
  begin
    CopyFile(AppEnvPath, UserEnvPath, False);
    Exit;
  end;

  // Иначе дописать только отсутствующие ключи
  if not LoadStringsFromFile(AppEnvPath, AppLines) then Exit;
  if not LoadStringsFromFile(UserEnvPath, UserLines) then Exit;

  Modified := False;
  for i := 0 to GetArrayLength(AppLines) - 1 do
  begin
    AppLine := Trim(AppLines[i]);
    if (AppLine = '') or (Copy(AppLine, 1, 1) = '#') then Continue;
    j := Pos('=', AppLine);
    if j = 0 then Continue;
    Key := Copy(AppLine, 1, j - 1);

    Found := False;
    for j := 0 to GetArrayLength(UserLines) - 1 do
    begin
      if Pos(Key + '=', Trim(UserLines[j])) = 1 then
      begin
        Found := True;
        Break;
      end;
    end;

    if not Found then
    begin
      SetArrayLength(UserLines, GetArrayLength(UserLines) + 1);
      UserLines[GetArrayLength(UserLines) - 1] := AppLines[i];
      Modified := True;
    end;
  end;

  if Modified then
    SaveStringsToFile(UserEnvPath, UserLines, False);
end;

procedure SecureEnvFile();
var
  EnvPath, UserName: String;
  ResultCode: Integer;
begin
  EnvPath := ExpandConstant('{userappdata}\MAX POST\.env');
  UserName := ExpandConstant('{username}');
  if FileExists(EnvPath) then
    Exec(ExpandConstant('{sys}\icacls.exe'),
         '"' + EnvPath + '" /inheritance:r /grant:r "' + UserName + ':R"',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure SecureExcelFile();
var
  ExcelPath, UserName: String;
  ResultCode: Integer;
begin
  ExcelPath := ExpandConstant('{app}\max_address.xlsx');
  UserName := ExpandConstant('{username}');
  if FileExists(ExcelPath) then
    Exec(ExpandConstant('{sys}\icacls.exe'),
         '"' + ExcelPath + '" /inheritance:r /grant:r "' + UserName + ':R"',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    MergeEnvToAppData();
    SecureEnvFile();
    SecureExcelFile();
  end;
end;
