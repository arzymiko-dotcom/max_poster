# MAX Poster — инструкции для Claude

## ОБЯЗАТЕЛЬНО при старте каждой сессии

1. Прочитать последние 2 коммита:
   ```
   git log -5 --oneline
   git show HEAD
   git show HEAD~1
   ```
2. Прочитать лог сессий: `C:\Users\fsb\.claude\projects\D--max-poster\memory\sessions_log.md`
3. Прочитать архитектуру проекта: `C:\Users\fsb\.claude\projects\D--max-poster\memory\project_max_poster.md`

Только после этого отвечать на вопросы и предлагать изменения.

---

## Язык

Общаться на **русском языке**. Ответы короткие и по делу.

---

## Проект

PyQt6 desktop-приложение для отправки объявлений в группы MAX мессенджера и ВКонтакте.

**Ключевые файлы:**
- `main.py` — главное окно (устарел, основная логика переехала)
- `shell_window.py` — главное окно (актуальное)
- `app.py` — точка входа, excepthook, updater
- `crash_dialog.py` — диалог отчёта об ошибке (новый)
- `env_utils.py` — работа с .env: `get_env_path()`, `load_env_safe()`, `read_env_text()`
- `ui/settings_dialog.py` — диалог настроек токенов
- `tg_notify.py` — Telegram-уведомления
- `max_sender.py`, `vk_sender.py` — отправка сообщений

---

## ВАЖНЫЕ РЕШЕНИЯ — не менять без явной просьбы

- `.env` на русской Windows может быть в cp1251 — **всегда** читать через `read_env_text()` из `env_utils.py`, никогда через `path.read_text(encoding="utf-8")` напрямую
- `load_dotenv()` заменён на `load_env_safe()` везде — не возвращать обратно
- `tg_notify.install_excepthook()` заменён на `crash_dialog.install_crash_hook()` — не менять
- `QShortcut` импортируется из `PyQt6.QtGui`, НЕ из `QtWidgets`
- AppId Inno Setup `{B7F32A14-5C8E-4D92-A1B3-F456789ABCDE}` — **никогда не менять**
- `sys.exit(0)` в Qt-слотах заменён на `QApplication.quit()`
- `blockSignals` везде обёрнут в `try/finally`

---

## УЖЕ СДЕЛАНО — не предлагать повторно

- `blockSignals` try/finally — сделано
- `QPixmap.scaled()` кэширование — сделано
- `get_stylesheet()` как константы — сделано
- `PARSE_DEBOUNCE_MS = 450ms` — сделано
- Спиннер в кнопке «Обновить» stats_panel — сделано
- Кнопка «Обновить реестр адресов» в меню — сделана
- `_FetchWorker` RuntimeError fix — сделано
- `_bot_worker` deleteLater — сделано
- Все воркеры vk_messages_panel deleteLater — сделано
- Race-condition в lambda воркеров — исправлено везде
- `closeEvent` MainWindow останавливает воркеры — сделано
- Закреплённая основная группа МАХ — сделана
- Системный трей (X → трей, Выход → закрытие) — сделано
- Просроченные посты авто-отправка — сделано
- Drag & Drop подсветка — сделано
- Счётчик символов оранжевый/красный — сделано
- Автофокус при запуске — сделано
- Тултипы на кнопках — сделано
- Диалог краша с кнопкой «Отправить отчёт» — сделан (crash_dialog.py)
- Защита от брутфорса пароля настроек — сделана
- Robust .env encoding (cp1251 fallback) — сделано в env_utils.py
- `except Exception` → конкретные типы в `_verify_pw` — сделано
- `beautifulsoup4` в requirements.txt — добавлен
- `crash_dialog` в hiddenimports PyInstaller — добавлен
- Шаблоны текста (`template_manager.py` + кнопка 📋 + подменю Файл→Шаблоны) — сделано
- `tg_notify._sanitize()` улучшен: явное редактирование значений env-переменных — сделано
- `log_setup.py`: frozen → INFO, dev → DEBUG — сделано
- `updater.py`: WARNING в лог если SHA256 отсутствует — сделано
- Напоминание сменить VK токен каждые 10 дней (balloon в трее) — сделано
- Проверка валидности `VK_USER_TOKEN` при старте (`_VkTokenCheckWorker`) — сделано
- Меню `Действия → 🔑 Сменил токены VK` — сделано
- `last_token_rotation` в `app_state.json` — сделано
- `signal.SIG_DFL` в `app.py` — фикс KeyboardInterrupt при запуске из VS Code terminal
- Размер EXE ~600MB — из-за QtWebEngine (193MB Chromium DLL). Можно срезать до ~250MB убрав WebEngine из spec, но отложено
- `pyspellchecker` → `pymorphy3` — точная проверка орфографии через морфологический словарь
- `pymorphy2` НЕ работает на Python 3.11 — использовать только `pymorphy3`
- Метод проверки: `morph.word_is_known(word)` — НЕ `tag.POS is not None`
- `ui/settings_dialog.py`: кнопка «🔗 Получить токен VK» + «✓ Проверить» для VK и MAX токенов
- VK токен: уведомление раз в день (`last_vk_invalid_warning` в `app_state.json`) — сделано
