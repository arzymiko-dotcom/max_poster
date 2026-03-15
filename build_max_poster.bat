@echo off
chcp 65001 >/dev/null
echo ========================================
echo    Сборка + Установщик для max_poster
echo ========================================

cd /d "%~dp0"

REM --- Опциональный бамп версии ---
REM Передай аргумент: build_max_poster.bat patch|minor|major|1.2.3
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
del /q *.spec 2>/dev/null

echo [1/4] Активируем виртуальное окружение...
call .venv\Scripts\activate.bat

echo [2/4] Собираем EXE (режим папки)...
pyinstaller --clean --windowed --name "max_poster" ^
    --icon="assets\max_poster.ico" ^
    --add-data "twemoji;twemoji" ^
    --add-data "assets;assets" ^
    --hidden-import=dotenv ^
    --hidden-import=dotenv.main ^
    main.py --noconfirm

REM Проверяем, успешно ли прошла сборка
if not exist dist\max_poster\max_poster.exe (
    echo Ошибка: EXE не создан!
    pause
    exit /b 1
)

echo [3/4] Копируем дополнительные файлы в папку с exe...

if exist version.txt (
    copy version.txt dist\max_poster\version.txt
) else (
    echo Предупреждение: version.txt не найден
)

copy *.xlsx dist\max_poster\ 2>/dev/null

if exist .env (
    copy .env dist\max_poster\.env
) else (
    echo Предупреждение: .env не найден - токены не будут скопированы
)

echo [4/4] Собираем установщик Inno Setup...
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" max_poster.iss

echo.
echo ========================================
echo    Готово! Версия: %APP_VER%
echo    Установщик: installer\max_poster_setup.exe
echo ========================================
pause
