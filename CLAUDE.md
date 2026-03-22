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
- Миниатюра фото в левой панели (`_photo_thumb` QLabel, `_update_photo_thumb()`) — сделано
- Счётчик адресов `(n/total)` в заголовке раздела — сделано
- Двойной клик на адрес — снять/поставить галочку (`_toggle_addr_item`) — сделано
- Лог отправки реал-тайм ✓/✗ (`_send_log_list`, `SendWorker.address_result` сигнал) — сделано
- Кнопка changelog `upd.ico` в сайдбаре над btn_auth (`_UpdBtn`, `_ChangelogPopup`) — сделано
- `changelog.json` в корне — формат `[{version, changes[]}]`, добавить новую версию сверху
- Фикс: программа не завершалась после выхода — `QApplication.quit()` в `ShellWindow.closeEvent`
- `_addr_search_timer` + `_parse_timer` останавливаются в `closeEvent` — сделано
- `_CombinedHighlighter._morph` кэшируется в `__init__` — сделано
- `itemChanged` × 2 → `_on_addr_item_changed` — сделано
- Папка `fonts/` пуста — программа использует системный Segoe UI (Windows default)
- Claude Code auto-approve: `"defaultMode": "acceptEdits"` в settings.json — настроено
- `QCursor`, `QRect`, `QPoint` в `shell_window.py` — в топ-левел импортах (не внутри `_check_cursor`)
- `from updater import _local_version` в `_ChangelogPopup.__init__` — оставлен локальным (circular import)
- `template_manager.apply_variables(text, address)` — подстановка `{{адрес}}`, `{{дата}}`, `{{месяц}}`, `{{год}}` в шаблоны
- `SEND_DELAY_SEC` в `.env` — дополнительная пауза между отправками (настраивается в settings_dialog)
- `SendWorker` принимает `dry_run: bool` и `extra_delay: int` — не менять сигнатуру без нужды
- `_pending_dry_run: bool` в MainWindow — флаг для dry-run, устанавливается `_send_dry_run()`, сбрасывается в `send_post()`
- `_send_log_results: list[tuple[str, bool, str]]` — (адрес, успех, время), собирается в `_on_address_result`
- `_save_report_btn` — показывается после MAX-рассылки если есть результаты, скрывается в `clear_form`
- `_select_all_btn` — ☑ в заголовке адресов, выбирает/снимает все (кроме pinned)
- `_hist_search_timer` останавливается в `closeEvent` — сделано (crash fix)
- `_toggle_select_all` вызывает `_on_addr_item_changed()` — тултип кнопки ☑ обновляется корректно
- `_refresh_history` читает `self._hist_search.text()` напрямую (не через `getattr`)
- `_update_photo_thumb`: убран лишний `exists()` перед `QPixmap()` (TOCTOU)
- Balloon + звук по завершении рассылки (`_notify_send_done`) — `winsound.MessageBeep` + `_tray_notify`; только реальная отправка (не dry-run)
- `PasteAddressesDialog` — вставка нескольких адресов сразу (кнопка 📋 в заголовке адресов)
- `_recent_photos: list[str]` (макс. 5) — галерея последних фото под превью, кнопки 48×48, сохраняются в `app_state.json`
- `btn_theme` (🌙/☀️) в `_SideBar` — переключает тему без входа в настройки; `_SideBar.set_dark(dark)` обновляет иконку
- `_photo_pinned: bool` + кнопка 📌 рядом с «Загрузить фото» — `clear_form` пропускает сброс фото если закреплено
- `_excel_watch_timer` (10с) — следит за `st_mtime` Excel; при изменении показывает `_excel_changed_bar` (жёлтый тостер) + balloon; `_reload_excel_silent` перезагружает без диалога
- `QSplitter` между левой и правой панелью — размеры сохраняются в `app_state.json` как `splitter_sizes`
- `_excel_watch_timer` останавливается в `closeEvent`
