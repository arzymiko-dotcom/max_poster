@echo off
cd /d D:\max_poster

:: Проверяем незакоммиченные изменения
git status --porcelain > "%TEMP%\git_check.txt" 2>&1
set "HAS_CHANGES="
for /f "usebackq" %%L in ("%TEMP%\git_check.txt") do set HAS_CHANGES=1
del "%TEMP%\git_check.txt"

if not defined HAS_CHANGES exit /b 0

:: Показываем уведомление через PowerShell (balloon tip — не блокирует работу)
powershell -NoProfile -WindowStyle Hidden -Command ^
  "Add-Type -AssemblyName System.Windows.Forms;" ^
  "$n = New-Object System.Windows.Forms.NotifyIcon;" ^
  "$n.Icon = [System.Drawing.SystemIcons]::Warning;" ^
  "$n.Visible = $true;" ^
  "$n.ShowBalloonTip(15000, 'Git Reminder — max_poster', 'Есть незакоммиченные изменения! Не забудь сделать git commit.', [System.Windows.Forms.ToolTipIcon]::Warning);" ^
  "Start-Sleep -Seconds 16;" ^
  "$n.Dispose()"
