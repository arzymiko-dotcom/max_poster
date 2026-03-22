@echo off
chcp 65001 >nul
echo ========================================
echo    Сборка + Установщик для MAX POST
echo ========================================

cd /d "%~dp0"

REM --- Опциональный бамп версии ---
REM Передай аргумент: "build_MAX POST.bat" patch|minor|major|1.2.3
if not "%1"=="" (
    echo [0/4] Обновляем версию: %1 ...
    python bump_version.py %1
    if errorlevel 1 ( echo Ошибка при обновлении версии! & pause & exit /b 1 )
    echo.
)

REM --- Показываем текущую версию ---
set /p APP_VER=<version.txt
echo Версия сборки: %APP_VER%
echo.

REM Очищаем артефакты прошлых сборок
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

echo [1/4] Активируем виртуальное окружение...
call .venv\Scripts\activate.bat

echo [2/4] Собираем EXE (режим папки)...
pyinstaller --clean "MAX POST.spec" --noconfirm
if errorlevel 1 (
    echo Ошибка: PyInstaller завершился с ошибкой!
    pause
    exit /b 1
)

REM Проверяем, успешно ли прошла сборка
if not exist "dist\MAX POST\MAX POST.exe" (
    echo Ошибка: EXE не создан!
    pause
    exit /b 1
)

echo [3/4] Копируем дополнительные файлы в папку с exe...

if exist version.txt (
    copy version.txt "dist\MAX POST\version.txt"
) else (
    echo Предупреждение: version.txt не найден
)

copy *.xlsx "dist\MAX POST\" 2>NUL

if exist changelog.json (
    copy changelog.json "dist\MAX POST\changelog.json"
) else (
    echo Предупреждение: changelog.json не найден
)

if exist .env (
    copy .env "dist\MAX POST\.env"
) else (
    echo Предупреждение: .env не найден - токены не будут скопированы
)

echo [4/4] Собираем установщик Inno Setup...
REM Ищем ISCC.exe в нескольких стандартных местах и в PATH
set "ISCC="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
)
if not defined ISCC (
    where ISCC.exe >nul 2>&1 && set "ISCC=ISCC.exe"
)
if not defined ISCC (
    echo Ошибка: Inno Setup не найден. Установите его или добавьте ISCC.exe в PATH.
    pause
    exit /b 1
)
"%ISCC%" "MAX POST.iss"
if errorlevel 1 (
    echo Ошибка: Inno Setup завершился с ошибкой!
    pause
    exit /b 1
)

REM --- Вычисляем SHA256 установщика и записываем в version.txt ---
echo Вычисляем SHA256 установщика...
for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash 'installer\MAX POST_setup.exe' -Algorithm SHA256).Hash.ToLower()"`) do set "INSTALLER_HASH=%%H"
if not defined INSTALLER_HASH (
    echo Предупреждение: не удалось вычислить SHA256 - version.txt не обновлён
) else (
    (echo %APP_VER%) > version.txt
    (echo sha256:%INSTALLER_HASH%) >> version.txt
    echo SHA256: %INSTALLER_HASH%
    echo Хэш записан в version.txt
    echo ВАЖНО: закоммить и запушь version.txt на GitHub!
)

echo.
echo ========================================
echo    Готово! Версия: %APP_VER%
echo    Установщик: installer\MAX POST_setup.exe
echo ========================================
pause
