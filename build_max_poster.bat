@echo off
chcp 65001 >nul
echo ========================================
echo    Сборка + Установщик для max_poster
echo ========================================

cd /d "%~dp0"

REM Очищаем артефакты прошлых сборок
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
del /q *.spec 2>nul

echo [1/3] Активируем виртуальное окружение...
call .venv\Scripts\activate.bat

echo [2/3] Собираем EXE (режим папки)...
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

echo [3/3] Копируем дополнительные файлы в папку с exe...

REM Копируем version.txt
if exist version.txt (
    copy version.txt dist\max_poster\version.txt
) else (
    echo Предупреждение: version.txt не найден
)

REM Копируем все .xlsx файлы (если нужны)
copy *.xlsx dist\max_poster\ 2>nul

REM Копируем .env рядом с exe (токены должны быть доступны пользователю)
if exist .env (
    copy .env dist\max_poster\.env
) else (
    echo Предупреждение: .env не найден - токены не будут скопированы
)

echo Готово.

echo [4/3] Собираем установщик Inno Setup...
REM Укажи правильный путь к ISCC.exe
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" max_poster.iss

echo.
echo ========================================
echo    Готово! Установщик: installer\max_poster_setup.exe
echo ========================================
pause