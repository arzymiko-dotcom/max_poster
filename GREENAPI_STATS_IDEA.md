# Идея: статистика групп через GREEN-API (независимо от бот-сервера)

## Зачем

Текущая реализация (`stats_panel.py`) зависит от внешнего сервера:
`https://bot-dev.gkh.spb.ru/gks2vyb-report.php`

Если сервер ляжет — данных нет (есть только кэш последней загрузки).
Этот файл — план реализации независимого источника через GREEN-API.

## Что нужно

- `idInstance` + `apiTokenInstance` из `.env` (уже есть для отправки)
- `max_address.xlsx` — колонка с chat_id (~190 групп, уже есть)

## GREEN-API endpoints

```
GET https://api.green-api.com/waInstance{idInstance}/getGroupData/{chatId}
    → owner, subject (название), participants (список → кол-во)

GET https://api.green-api.com/waInstance{idInstance}/lastIncomingMessages/{chatId}
    → timestamp последнего входящего сообщения
```

## Схема реализации

1. Читаем chat_id из `max_address.xlsx` (колонка `ID_chat` или аналог)
2. Для каждого chat_id запрашиваем `getGroupData` → название + кол-во участников
3. Для времени последней активности — `lastIncomingMessages` (1 сообщение)
4. Батчами по 5–10 запросов с паузой ~0.2 сек (rate limit)
5. Прогресс-бар во время загрузки (занимает ~30–60 сек на 190 групп)
6. Результат идентичен текущей таблице: название / участников / активность / ссылка

## Ограничения

- ~190 API-запросов вместо 1 → обновление ~30–60 сек
- Нужно обрабатывать ошибки на каждый запрос отдельно
- Rate limit GREEN-API: ~20 req/min на бесплатном тарифе

## Совместимость с текущим кодом

`StatsPanel._all_rows` — список dict с ключами:
```python
{"name": str, "members": str, "last_event": str, "link": str}
```
Достаточно написать новый `_FetchWorkerGreenApi(QThread)` с тем же
сигналом `finished(list, list)` — и подключить вместо `_FetchWorker`.
Всё остальное (таблица, экспорт, фильтр, кэш) останется без изменений.
