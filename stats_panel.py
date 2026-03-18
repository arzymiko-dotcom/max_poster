"""
stats_panel.py — Панель «Статистика групп» MAX POST.

Загружает данные о группах напрямую через GREEN-API (независимо от внешнего сервера):
  - lastIncomingMessages → время последней активности (1 запрос на все группы)
  - getGroupData          → название + кол-во участников (~190 запросов батчами)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from PyQt6.QtCore import QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QProgressBar, QPushButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEnginePage
    _WEB_ENGINE_AVAILABLE = True
except Exception:
    _WEB_ENGINE_AVAILABLE = False

_WEB_REPORT_URL = "http://bot-dev.gkh.spb.ru/gks2vyb-report.php"

from env_utils import get_env_path

_log = logging.getLogger(__name__)

_AUTO_REFRESH_MS        = 5 * 60 * 1000   # авто-обновление каждые 5 минут
_REQUEST_DELAY          = 1.1             # секунд между запросами (лимит GREEN-API: 1 req/s)
_ACTIVITY_WINDOW_MIN    = 43200           # 30 дней в минутах для lastIncomingMessages
_GROUP_CACHE_TTL        = 3600            # секунд — как долго кэшируем данные группы (1 час)

# Индексы колонок таблицы
_COL_NAME    = 0
_COL_MEMBERS = 1
_COL_TIME    = 2
_COL_LINK    = 3


def _extract_chat_id(raw: str) -> str:
    """Извлекает числовой chat_id из URL или возвращает строку как есть.

    Примеры:
      https://web.max.ru/-69098384919255  → -69098384919255
      https://web.max.ru/-69098384919255/ → -69098384919255
      -69098384919255                     → -69098384919255  (без изменений)
    """
    if raw.startswith("http"):
        m = re.search(r"(-\d+)/?$", raw)
        if m:
            return m.group(1)
    return raw


def _resolve_excel_path() -> Path:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    return base / "max_address.xlsx"


def _cache_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("APPDATA", Path.home())) / "MAX POST"
    else:
        base = Path(__file__).parent
    base.mkdir(parents=True, exist_ok=True)
    return base / "stats_cache.json"


def _save_cache(rows: list[dict], summary_texts: list[str],
                group_cache: dict | None = None) -> None:
    path = _cache_path()
    data = {
        "ts":           datetime.now().isoformat(),
        "rows":         rows,
        "summary_texts": summary_texts,
        "group_cache":  group_cache or {},
    }
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)
    except Exception as exc:
        _log.warning("stats cache write error: %s", exc)


def _load_cache() -> tuple[list[dict], list[str], datetime | None, dict]:
    """Возвращает (rows, summary_texts, ts, group_cache).

    group_cache: {chat_id: {"name": str, "members": str, "cached_at": int}}
    """
    try:
        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(raw["ts"])
        return raw["rows"], raw.get("summary_texts", []), ts, raw.get("group_cache", {})
    except Exception:
        return [], [], None, {}


# ────────────────────────────────────────────────────────────────
#  Фоновый поток — GREEN-API
# ────────────────────────────────────────────────────────────────

class _FetchWorker(QThread):
    finished = pyqtSignal(list, list, dict)  # (rows, summary_texts, group_cache)
    failed   = pyqtSignal(str)
    progress = pyqtSignal(str)               # текст для строки статуса

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop = False

    def run(self) -> None:
        load_dotenv(get_env_path())
        api_url   = os.getenv("MAX_API_URL", "https://api.green-api.com")
        id_inst   = os.getenv("MAX_ID_INSTANCE", "")
        api_token = os.getenv("MAX_API_TOKEN", "")

        if not id_inst or not api_token:
            self.failed.emit(
                "Не заданы MAX_ID_INSTANCE или MAX_API_TOKEN.\n"
                "Откройте Настройки подключений (🔑) и заполните данные GREEN-API."
            )
            return

        # ── 1. Читаем chat_id из Excel ───────────────────────────
        excel_path = _resolve_excel_path()
        if not excel_path.exists():
            self.failed.emit(f"Файл {excel_path.name} не найден рядом с программой.")
            return

        try:
            df = pd.read_excel(excel_path, dtype=str).fillna("")
        except Exception as exc:
            self.failed.emit(f"Ошибка чтения {excel_path.name}: {exc}")
            return

        cols     = {str(c).strip().lower(): c for c in df.columns}
        addr_col = cols.get("адрес") or df.columns[0]
        link_col = cols.get("ссылка") or (df.columns[1] if len(df.columns) > 1 else None)
        id_col   = cols.get("id")    or (df.columns[2] if len(df.columns) > 2 else None)

        entries: list[tuple[str, str, str]] = []   # (chat_id, link, address)
        for _, row in df.iterrows():
            raw_id  = str(row.get(id_col, "")).strip() if id_col else ""
            link    = str(row.get(link_col, "")).strip() if link_col else ""
            address = str(row.get(addr_col, "")).strip()

            # Убираем .0 (числовые ID из Excel читаются как float)
            if raw_id.endswith(".0"):
                raw_id = raw_id[:-2]

            # Пробуем получить числовой ID: из колонки ID или из ссылки
            chat_id = _extract_chat_id(raw_id) if raw_id and raw_id.lower() not in ("nan", "") \
                      else _extract_chat_id(link)

            if not chat_id or chat_id.lower() in ("nan", "") or not chat_id.lstrip("-").isdigit():
                continue
            entries.append((chat_id, link, address))

        if not entries:
            self.failed.emit(f"В файле {excel_path.name} не найдены ID групп (колонка «ID»).")
            return

        # ── 2. Получаем активность одним запросом ────────────────
        self.progress.emit("Получение активности групп…")
        last_activity: dict[str, int] = {}   # chat_id → unix-timestamp
        try:
            url  = (
                f"{api_url}/waInstance{id_inst}/lastIncomingMessages/{api_token}"
                f"?minutes={_ACTIVITY_WINDOW_MIN}"
            )
            resp = requests.get(url, timeout=30)
            if resp.ok:
                msgs = resp.json()
                if isinstance(msgs, list):
                    for msg in msgs:
                        cid = str(msg.get("chatId", "")).strip()
                        ts  = msg.get("timestamp", 0)
                        if cid and ts and ts > last_activity.get(cid, 0):
                            last_activity[cid] = ts
        except Exception as exc:
            _log.warning("lastIncomingMessages error: %s", exc)
            # Не критично — продолжаем без данных об активности

        # ── 3. Загружаем данные групп (умный кэш + 1 req/s) ─────
        _, _, _, group_cache = _load_cache()
        now_ts  = int(time.time())
        rows: list[dict] = []
        total   = len(entries)

        # Удаляем из кэша записи групп, которых нет в текущем Excel
        current_ids = {cid for cid, _, _ in entries}
        stale_keys  = [k for k in group_cache if k not in current_ids]
        for k in stale_keys:
            del group_cache[k]

        # Считаем сколько групп нужно запросить (не в кэше или кэш устарел)
        need_fetch = sum(
            1 for cid, _, _ in entries
            if now_ts - group_cache.get(cid, {}).get("cached_at", 0) > _GROUP_CACHE_TTL
        )
        from_cache = total - need_fetch
        if from_cache > 0:
            est_min = round(need_fetch * _REQUEST_DELAY / 60, 1)
            self.progress.emit(
                f"Загрузка {need_fetch} групп (~{est_min} мин), "
                f"{from_cache} из кэша…  0 / {need_fetch}"
            )
        else:
            est_min = round(total * _REQUEST_DELAY / 60, 1)
            self.progress.emit(f"Загрузка {total} групп (~{est_min} мин)…  0 / {total}")

        fetched = 0   # счётчик реальных запросов (не кэш)
        first_request = True

        for chat_id, link, address in entries:
            if self._stop:
                return
            cached = group_cache.get(chat_id, {})
            cache_age = now_ts - cached.get("cached_at", 0)

            if cache_age <= _GROUP_CACHE_TTL and cached.get("name"):
                # ── Берём из кэша ──────────────────────────────────
                name    = cached["name"]
                members = cached["members"]
            else:
                # ── Запрашиваем у GREEN-API ────────────────────────
                if not first_request:
                    time.sleep(_REQUEST_DELAY)   # 1 req/s лимит GREEN-API
                first_request = False
                fetched += 1
                self.progress.emit(
                    f"Загрузка групп… {fetched} / {need_fetch}"
                    + (f"  (кэш: {from_cache})" if from_cache > 0 else "")
                )

                try:
                    url  = f"{api_url}/waInstance{id_inst}/getGroupData/{api_token}"
                    resp = requests.post(url, json={"chatId": chat_id}, timeout=8)

                    if resp.ok:
                        data    = resp.json()
                        name    = data.get("subject") or address
                        members = str(data.get("size", 0))
                        # Обновляем кэш группы
                        group_cache[chat_id] = {
                            "name":      name,
                            "members":   members,
                            "cached_at": now_ts,
                        }
                    else:
                        name    = address
                        members = "—"

                except Exception as exc:
                    _log.warning("getGroupData %s: %s", chat_id, exc)
                    name    = address
                    members = "—"

            ts         = last_activity.get(chat_id)
            last_event = (
                datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
                if ts else "Нет данных"
            )
            if members == "—":
                last_event = "Нет доступа"

            rows.append({
                "name":       name,
                "members":    members,
                "last_event": last_event,
                "link":       link,
            })

        if not rows:
            self.failed.emit("Не удалось получить данные ни по одной группе.")
            return

        self.finished.emit(rows, [], group_cache)


# ────────────────────────────────────────────────────────────────
#  Виджет панели
# ────────────────────────────────────────────────────────────────

class StatsPanel(QWidget):
    """Панель «Статистика групп» — таблица с реалтайм-данными из GREEN-API."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: _FetchWorker | None = None
        self._all_rows: list[dict] = []
        self._missing_rows: list[dict] = []
        self._dead_only: bool = False
        self._last_refresh: datetime | None = None

        self._spin_frame: int = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(100)
        self._spin_timer.timeout.connect(self._spin_step)

        self._build_ui()
        self._apply_styles()

        # Авто-обновление каждые 5 минут
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(_AUTO_REFRESH_MS)
        self._auto_timer.timeout.connect(self.refresh)
        self._auto_timer.start()

        QApplication.instance().aboutToQuit.connect(self._shutdown)

        # Первая загрузка
        QTimer.singleShot(0, self.refresh)

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # ── Переключатель режимов (pill) ─────────────────────────
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(0)

        self._btn_web   = QPushButton("🌐  WEB версия")
        self._btn_smart = QPushButton("⚡  Smart версия")
        for btn in (self._btn_web, self._btn_smart):
            btn.setCheckable(True)
            btn.setFixedHeight(34)
            btn.setObjectName("statsToggleBtn")
        self._btn_smart.setObjectName("statsToggleBtnActive")
        self._btn_web.setChecked(False)
        self._btn_smart.setChecked(True)
        self._btn_web.clicked.connect(lambda: self._switch_mode(0))
        self._btn_smart.clicked.connect(lambda: self._switch_mode(1))

        toggle_row.addStretch()
        toggle_row.addWidget(self._btn_web)
        toggle_row.addWidget(self._btn_smart)
        toggle_row.addStretch()
        root.addLayout(toggle_row)

        # ── Стек страниц ─────────────────────────────────────────
        self._stack = QStackedWidget()
        root.addWidget(self._stack)

        # ── Страница 0: WEB версия ────────────────────────────────
        if _WEB_ENGINE_AVAILABLE:
            self._web_view = QWebEngineView()
            _page = QWebEnginePage(self._web_view)
            _page.certificateError.connect(lambda err: err.acceptCertificate())
            self._web_view.setPage(_page)
            self._web_view.setUrl(QUrl(_WEB_REPORT_URL))
            self._stack.addWidget(self._web_view)
        else:
            no_web = QLabel(
                "⚠️  Для WEB версии требуется пакет PyQt6-WebEngine.\n"
                "Установите его командой: pip install PyQt6-WebEngine"
            )
            no_web.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_web.setObjectName("statsStatus")
            self._stack.addWidget(no_web)

        # ── Страница 1: Smart версия (наша) ──────────────────────
        smart_page = QWidget()
        smart_layout = QVBoxLayout(smart_page)
        smart_layout.setContentsMargins(0, 0, 0, 0)
        smart_layout.setSpacing(10)

        # ── Заголовок ────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel("Статистика групп")
        title.setObjectName("statsPanelTitle")
        hdr.addWidget(title)
        hdr.addStretch()

        self._last_lbl = QLabel("")
        self._last_lbl.setObjectName("statsLastRefresh")
        hdr.addWidget(self._last_lbl)

        self._export_btn = QPushButton("📥 Excel")
        self._export_btn.setObjectName("statsExportBtn")
        self._export_btn.setFixedHeight(32)
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export_excel)
        hdr.addWidget(self._export_btn)

        self._refresh_btn = QPushButton("⟳  Обновить")
        self._refresh_btn.setObjectName("statsRefreshBtn")
        self._refresh_btn.setFixedHeight(32)
        self._refresh_btn.clicked.connect(self.refresh)
        hdr.addWidget(self._refresh_btn)
        smart_layout.addLayout(hdr)

        # ── Сводка ───────────────────────────────────────────────
        self._summary_frame = QFrame()
        self._summary_frame.setObjectName("statsSummary")
        sf_layout = QHBoxLayout(self._summary_frame)
        sf_layout.setContentsMargins(14, 8, 14, 8)
        sf_layout.setSpacing(28)

        self._lbl_groups  = self._make_stat_lbl("—", "Групп")
        self._lbl_members = self._make_stat_lbl("—", "Участников")
        self._lbl_active  = self._make_stat_lbl("—", "Активны сегодня")
        self._lbl_missing = self._make_stat_lbl("—", "Нет доступа")

        for w in (self._lbl_groups, self._lbl_members, self._lbl_active, self._lbl_missing):
            sf_layout.addWidget(w)
        sf_layout.addStretch()
        smart_layout.addWidget(self._summary_frame)

        # ── Баннер кэша (скрыт по умолчанию) ────────────────────
        self._cache_banner = QLabel()
        self._cache_banner.setObjectName("statsCacheBanner")
        self._cache_banner.setWordWrap(True)
        self._cache_banner.hide()
        smart_layout.addWidget(self._cache_banner)

        # ── Прогресс-бар загрузки (скрыт по умолчанию) ──────────
        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("statsProgressBar")
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.hide()
        smart_layout.addWidget(self._progress_bar)

        # ── Поиск ────────────────────────────────────────────────
        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍  Поиск по названию...")
        self._search.setObjectName("statsSearch")
        self._search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self._search)

        self._dead_btn = QPushButton("🔴 Нет доступа")
        self._dead_btn.setObjectName("statsDeadBtn")
        self._dead_btn.setCheckable(True)
        self._dead_btn.setEnabled(False)
        self._dead_btn.toggled.connect(self._on_dead_toggled)
        search_row.addWidget(self._dead_btn)
        smart_layout.addLayout(search_row)

        # ── Таблица ───────────────────────────────────────────────
        self._table = QTableWidget(0, 4)
        self._table.setObjectName("statsTable")
        self._table.setHorizontalHeaderLabels(
            ["Название", "Участников", "Последняя активность", "Ссылка"]
        )
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(1, 110)
        self._table.setColumnWidth(2, 180)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSortIndicatorShown(True)
        self._table.cellDoubleClicked.connect(self._on_double_click)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        smart_layout.addWidget(self._table)

        # ── Статус-строка ─────────────────────────────────────────
        self._status_lbl = QLabel("Загрузка…")
        self._status_lbl.setObjectName("statsStatus")
        smart_layout.addWidget(self._status_lbl)

        self._stack.addWidget(smart_page)
        self._stack.setCurrentIndex(1)  # по умолчанию Smart версия

    @staticmethod
    def _make_stat_lbl(value: str, label: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        val_lbl = QLabel(value)
        val_lbl.setObjectName("statsStatValue")
        lbl_lbl = QLabel(label)
        lbl_lbl.setObjectName("statsStatLabel")
        lay.addWidget(val_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(lbl_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)
        w._val_lbl = val_lbl  # type: ignore[attr-defined]
        return w

    def _switch_mode(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._btn_web.setChecked(index == 0)
        self._btn_smart.setChecked(index == 1)
        self._btn_web.setObjectName("statsToggleBtnActive" if index == 0 else "statsToggleBtn")
        self._btn_smart.setObjectName("statsToggleBtnActive" if index == 1 else "statsToggleBtn")
        # Переприменяем стиль чтобы Qt подхватил новый objectName
        self._btn_web.style().unpolish(self._btn_web)
        self._btn_web.style().polish(self._btn_web)
        self._btn_smart.style().unpolish(self._btn_smart)
        self._btn_smart.style().polish(self._btn_smart)

    # ── Логика обновления ────────────────────────────────────────

    _SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def _spin_step(self) -> None:
        self._spin_frame = (self._spin_frame + 1) % len(self._SPINNER)
        self._refresh_btn.setText(f"{self._SPINNER[self._spin_frame]}  Загрузка…")

    def refresh(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._spin_frame = 0
        self._spin_timer.start()
        self._refresh_btn.setText(f"{self._SPINNER[0]}  Загрузка…")

        # Сразу показываем кэш, чтобы пользователь видел данные во время загрузки
        cached_rows, _, cached_ts, _ = _load_cache()
        if cached_rows and not self._all_rows:
            self._all_rows = cached_rows
            self._apply_filter()
            ts_str = cached_ts.strftime("%d.%m.%Y %H:%M") if cached_ts else "ранее"
            self._cache_banner.setText(f"🔄  Обновление данных… показаны данные от {ts_str}")
            self._cache_banner.show()
        else:
            self._cache_banner.hide()

        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._status_lbl.setText("Подключение к GREEN-API…")

        self._worker = _FetchWorker(self)
        self._worker.finished.connect(self._on_data)
        self._worker.failed.connect(self._on_error)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._worker.finished.connect(lambda *_: setattr(self, "_worker", None))
        self._worker.failed.connect(lambda *_: setattr(self, "_worker", None))
        self._worker.start()

    def _on_progress(self, text: str) -> None:
        self._status_lbl.setText(text)
        # Парсим "Загрузка групп… X / Y" для прогресс-бара
        if "/" in text:
            try:
                parts = text.split("…")[-1].strip().split("/")
                current = int(parts[0].strip().split()[0])
                total   = int(parts[1].strip().split()[0])
                if total > 0:
                    self._progress_bar.setValue(int(current / total * 100))
            except (ValueError, IndexError):
                pass

    def _on_data(self, rows: list[dict], summary_texts: list[str], group_cache: dict) -> None:
        self._spin_timer.stop()
        self._progress_bar.setValue(100)
        self._progress_bar.hide()
        self._all_rows = rows
        self._last_refresh = datetime.now()
        self._last_lbl.setText(f"Обновлено в {self._last_refresh.strftime('%H:%M:%S')}")
        self._cache_banner.hide()
        _save_cache(rows, summary_texts, group_cache)
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("⟳  Обновить")
        self._export_btn.setEnabled(True)

        # Сводка
        total = len(rows)
        total_members = 0
        active_today = 0
        today = datetime.now().date()
        for r in rows:
            try:
                total_members += int(r["members"])
            except (ValueError, TypeError):
                pass
            try:
                ev_dt = datetime.strptime(r["last_event"][:10], "%d.%m.%Y")
                if ev_dt.date() == today:
                    active_today += 1
            except ValueError:
                pass

        self._lbl_groups._val_lbl.setText(str(total))   # type: ignore[attr-defined]
        self._lbl_members._val_lbl.setText(f"{total_members:,}".replace(",", " "))  # type: ignore[attr-defined]
        self._lbl_active._val_lbl.setText(str(active_today))  # type: ignore[attr-defined]

        # "Нет доступа" — группы, у которых members == "—"
        self._missing_rows = [r for r in rows if r["members"] == "—"]
        missing_count = len(self._missing_rows)
        missing_val_lbl = self._lbl_missing._val_lbl  # type: ignore[attr-defined]
        missing_val_lbl.setText(str(missing_count))
        missing_val_lbl.setStyleSheet(
            "color: #dc2626; font-size: 24px; font-weight: 700;"
            if missing_count > 0 else ""
        )

        self._dead_btn.setEnabled(True)
        self._apply_filter()
        self._status_lbl.setText(
            f"Загружено {total} групп · "
            f"обновлено {self._last_refresh.strftime('%d.%m.%Y %H:%M:%S')}"
        )

    def _on_error(self, msg: str) -> None:
        self._spin_timer.stop()
        self._progress_bar.hide()
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("⟳  Обновить")
        cached_rows, cached_summary, cached_ts, _ = _load_cache()
        if cached_rows:
            self._all_rows = cached_rows
            ts_str = cached_ts.strftime("%d.%m.%Y %H:%M") if cached_ts else "неизвестно"
            self._cache_banner.setText(
                f"⚠️  Ошибка загрузки: {msg}  ·  Показаны данные от {ts_str}"
            )
            self._cache_banner.show()
            self._export_btn.setEnabled(True)
            self._apply_filter()
            self._status_lbl.setText(f"Кэш от {ts_str} · {len(cached_rows)} групп")
        else:
            self._cache_banner.hide()
            self._status_lbl.setText(f"Ошибка: {msg}  ·  Кэш недоступен")

    def _on_dead_toggled(self, checked: bool) -> None:
        self._dead_only = checked
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self._search.text().strip().lower()
        if self._dead_only:
            rows = [r for r in self._missing_rows if not query or query in r["name"].lower()]
            self._fill_table(rows, dead_mode=True)
            shown = len(rows)
            total = len(self._missing_rows)
            self._status_lbl.setText(
                f"Нет доступа: показано {shown} из {total}"
                + (f" · фильтр: «{query}»" if query else "")
            )
        else:
            rows = [r for r in self._all_rows if not query or query in r["name"].lower()]
            self._fill_table(rows)
            if self._all_rows:
                shown = len(rows)
                total = len(self._all_rows)
                self._status_lbl.setText(
                    f"Показано {shown} из {total} групп"
                    + (f" · фильтр: «{query}»" if query else "")
                )

    def _fill_table(self, rows: list[dict], *, dead_mode: bool = False) -> None:
        today = datetime.now().date()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))

        dead_bg = QColor("#fff0f0")

        for row_idx, r in enumerate(rows):
            name    = r["name"]
            members = r["members"]
            t_event = r["last_event"]
            link    = r["link"]

            if dead_mode:
                row_bg = dead_bg
            else:
                fresh_today = False
                fresh_week  = False
                try:
                    ev_dt = datetime.strptime(t_event[:10], "%d.%m.%Y").date()
                    fresh_today = (ev_dt == today)
                    fresh_week  = (ev_dt >= today - timedelta(days=7))
                except ValueError:
                    pass
                if fresh_today:
                    row_bg = QColor("#f0faf2")
                elif fresh_week:
                    row_bg = QColor("#fffbee")
                else:
                    row_bg = None

            items: list[QTableWidgetItem] = [
                QTableWidgetItem(name),
                _NumItem(members),
                QTableWidgetItem(t_event),
                QTableWidgetItem(link),
            ]
            for col, item in enumerate(items):
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if row_bg:
                    item.setBackground(row_bg)
                self._table.setItem(row_idx, col, item)

        self._table.setSortingEnabled(True)
        self._table.resizeRowsToContents()

    # ── Взаимодействие с таблицей ────────────────────────────────

    def _get_row_link(self, row: int) -> str:
        item = self._table.item(row, _COL_LINK)
        return item.text().strip() if item else ""

    def _on_double_click(self, row: int, _col: int) -> None:
        link = self._get_row_link(row)
        if link.startswith("http"):
            QDesktopServices.openUrl(QUrl(link))

    def _show_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        link = self._get_row_link(row)
        name_item = self._table.item(row, _COL_NAME)
        name = name_item.text() if name_item else ""

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#ffffff; border:1px solid #e4eaf0;
                    border-radius:8px; padding:4px; font-size:13px; }
            QMenu::item { padding:6px 18px 6px 10px; border-radius:5px; }
            QMenu::item:selected { background:#dbeafe; color:#1d4ed8; }
            QMenu::separator { height:1px; background:#f0f4f8; margin:3px 6px; }
        """)

        act_open      = menu.addAction("🌐  Открыть в браузере")
        act_open.setEnabled(link.startswith("http"))
        act_copy_link = menu.addAction("📋  Копировать ссылку")
        act_copy_link.setEnabled(bool(link))
        menu.addSeparator()
        act_copy_name = menu.addAction("📝  Копировать название")
        act_copy_name.setEnabled(bool(name))

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == act_open and link.startswith("http"):
            QDesktopServices.openUrl(QUrl(link))
        elif chosen == act_copy_link and link:
            QApplication.clipboard().setText(link)
        elif chosen == act_copy_name and name:
            QApplication.clipboard().setText(name)

    # ── Экспорт в Excel ──────────────────────────────────────────

    def _export_excel(self) -> None:
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment
        except ImportError:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Экспорт", "Библиотека openpyxl не установлена.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить статистику", "статистика_групп.xlsx", "Excel (*.xlsx)"
        )
        if not path:
            return

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Статистика групп"

        headers = ["Название", "Участников", "Последняя активность", "Ссылка"]
        header_font = Font(bold=True, size=11)
        header_fill = PatternFill("solid", fgColor="F1F5F9")

        for col_idx, hdr in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=hdr)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        row_count = self._table.rowCount()
        for row_idx in range(row_count):
            for col_idx in range(4):
                item = self._table.item(row_idx, col_idx)
                value = item.text() if item else ""
                ws.cell(row=row_idx + 2, column=col_idx + 1, value=value)

        for col in ws.columns:
            max_len = max((len(str(c.value)) if c.value else 0) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)

        try:
            wb.save(path)
            self._status_lbl.setText(f"Экспортировано {row_count} строк → {path}")
        except Exception as exc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Экспорт", f"Ошибка сохранения: {exc}")

    # ── Тема / закрытие ──────────────────────────────────────────

    def _shutdown(self) -> None:
        self._auto_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker._stop = True
            self._worker.quit()
            self._worker.wait(10000)

    def closeEvent(self, event) -> None:
        self._shutdown()
        super().closeEvent(event)

    def set_dark(self, dark: bool) -> None:
        self._apply_styles(dark=dark)

    def _apply_styles(self, dark: bool = False) -> None:
        if dark:
            self.setStyleSheet("""
                StatsPanel { background: #1e1e2e; }
                QPushButton#statsToggleBtn {
                    font-size: 13px; font-weight: 600; padding: 6px 22px;
                    border: 1px solid #3a3a55; background: #2d2d45; color: #7878aa;
                    border-radius: 0px;
                }
                QPushButton#statsToggleBtn:first-child { border-radius: 8px 0px 0px 8px; }
                QPushButton#statsToggleBtn:last-child  { border-radius: 0px 8px 8px 0px; }
                QPushButton#statsToggleBtnActive {
                    font-size: 13px; font-weight: 700; padding: 6px 22px;
                    border: 1px solid #4a6cf7; background: #1e2a5a; color: #8899ff;
                    border-radius: 0px;
                }
                QPushButton#statsToggleBtnActive:first-child { border-radius: 8px 0px 0px 8px; }
                QPushButton#statsToggleBtnActive:last-child  { border-radius: 0px 8px 8px 0px; }
                QLabel#statsPanelTitle { font-size: 18px; font-weight: 700; color: #d0d0f0; }
                QLabel#statsLastRefresh { font-size: 11px; color: #6868aa; padding-right: 8px; }
                QPushButton#statsRefreshBtn {
                    min-height: 0; font-size: 13px; font-weight: 600; padding: 4px 16px;
                    border-radius: 7px; border: 1px solid #3a3a55; background: #2d2d45; color: #c8c8e0;
                }
                QPushButton#statsRefreshBtn:hover { background: #1e2a5a; border-color: #4a6cf7; color: #8899ff; }
                QPushButton#statsRefreshBtn:disabled { color: #5a5a88; }
                QFrame#statsSummary { background: #252535; border: 1px solid #3a3a55; border-radius: 10px; }
                QLabel#statsStatValue { font-size: 24px; font-weight: 700; color: #a0c0ff; }
                QLabel#statsStatLabel { font-size: 11px; color: #6868aa; font-weight: 500; }
                QLineEdit#statsSearch {
                    font-size: 13px; padding: 7px 12px; border: 1px solid #3a3a55;
                    border-radius: 8px; background: #2a2a3e; color: #d0d0f0;
                }
                QTableWidget#statsTable {
                    border: 1px solid #3a3a55; border-radius: 8px; background: #252535;
                    alternate-background-color: #222232; gridline-color: #2d2d45;
                    font-size: 12px; color: #d0d0f0;
                }
                QTableWidget#statsTable QHeaderView::section {
                    background: #222232; color: #7878aa; font-size: 11px; font-weight: 700;
                    padding: 6px 10px; border: none; border-bottom: 2px solid #3a3a55;
                    text-transform: uppercase; letter-spacing: 0.4px;
                }
                QTableWidget#statsTable::item:selected { background: #1e3a8a; color: #e0e0ff; }
                QLabel#statsStatus { font-size: 11px; color: #6868aa; padding: 2px 0; }
                QPushButton#statsExportBtn {
                    min-height: 0; font-size: 13px; font-weight: 600; padding: 4px 14px;
                    border-radius: 7px; border: 1px solid #2a5a3e; background: #1a3a2e; color: #4ade80;
                }
                QPushButton#statsExportBtn:hover { background: #1e4a36; border-color: #22c55e; }
                QPushButton#statsExportBtn:disabled { color: #5a5a88; background: #222232; border-color: #333344; }
                QPushButton#statsDeadBtn {
                    min-height: 0; font-size: 12px; padding: 5px 12px; border-radius: 7px;
                    border: 1px solid #6b2020; background: #2a1a1a; color: #f87171; font-weight: 600;
                }
                QPushButton#statsDeadBtn:checked { background: #dc2626; color: #ffffff; border-color: #dc2626; }
                QPushButton#statsDeadBtn:disabled { color: #5a5a88; background: #222232; border-color: #333344; }
                QLabel#statsCacheBanner {
                    font-size: 12px; font-weight: 600; color: #fcd34d;
                    background: #3a2a00; border: 1px solid #78580a;
                    border-radius: 7px; padding: 6px 12px;
                }
                QProgressBar#statsProgressBar {
                    border: none; border-radius: 3px; background: #2d2d45;
                }
                QProgressBar#statsProgressBar::chunk {
                    border-radius: 3px; background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:0,
                        stop:0 #4a6cf7, stop:1 #7c3aed);
                }
            """)
            return
        self.setStyleSheet("""
            StatsPanel { background: #f3f4f6; }
            QPushButton#statsToggleBtn {
                font-size: 13px; font-weight: 600; padding: 6px 22px;
                border: 1px solid #c7d0db; background: #eef2f7; color: #94a3b8;
                border-radius: 0px;
            }
            QPushButton#statsToggleBtn:first-child { border-radius: 8px 0px 0px 8px; }
            QPushButton#statsToggleBtn:last-child  { border-radius: 0px 8px 8px 0px; }
            QPushButton#statsToggleBtnActive {
                font-size: 13px; font-weight: 700; padding: 6px 22px;
                border: 1px solid #2d6cdf; background: #dbeafe; color: #1d4ed8;
                border-radius: 0px;
            }
            QPushButton#statsToggleBtnActive:first-child { border-radius: 8px 0px 0px 8px; }
            QPushButton#statsToggleBtnActive:last-child  { border-radius: 0px 8px 8px 0px; }
            QLabel#statsPanelTitle { font-size: 18px; font-weight: 700; color: #1a1a2e; }
            QLabel#statsLastRefresh { font-size: 11px; color: #9ca3af; padding-right: 8px; }
            QPushButton#statsRefreshBtn {
                min-height: 0; font-size: 13px; font-weight: 600; padding: 4px 16px;
                border-radius: 7px; border: 1px solid #c7d0db; background: #eef2f7; color: #334155;
            }
            QPushButton#statsRefreshBtn:hover { background: #dbeafe; border-color: #2d6cdf; color: #1d4ed8; }
            QPushButton#statsRefreshBtn:disabled { color: #9ca3af; }
            QFrame#statsSummary { background: #ffffff; border: 1px solid #e4eaf0; border-radius: 10px; }
            QLabel#statsStatValue { font-size: 24px; font-weight: 700; color: #1e3a5f; }
            QLabel#statsStatLabel { font-size: 11px; color: #9ca3af; font-weight: 500; }
            QLineEdit#statsSearch {
                font-size: 13px; padding: 7px 12px; border: 1px solid #c7d0db;
                border-radius: 8px; background: #ffffff; color: #1a1a2e;
            }
            QTableWidget#statsTable {
                border: 1px solid #e4eaf0; border-radius: 8px; background: #ffffff;
                alternate-background-color: #f8fafc; gridline-color: #f0f4f8;
                font-size: 12px; color: #1a1a2e;
            }
            QTableWidget#statsTable QHeaderView::section {
                background: #f1f5f9; color: #64748b; font-size: 11px; font-weight: 700;
                padding: 6px 10px; border: none; border-bottom: 2px solid #e2e8f0;
                text-transform: uppercase; letter-spacing: 0.4px;
            }
            QTableWidget#statsTable::item:selected { background: #dbeafe; color: #1e3a5f; }
            QLabel#statsStatus { font-size: 11px; color: #9ca3af; padding: 2px 0; }
            QPushButton#statsExportBtn {
                min-height: 0; font-size: 13px; font-weight: 600; padding: 4px 14px;
                border-radius: 7px; border: 1px solid #a7f3d0; background: #ecfdf5; color: #065f46;
            }
            QPushButton#statsExportBtn:hover { background: #d1fae5; border-color: #34d399; }
            QPushButton#statsExportBtn:disabled { color: #9ca3af; background: #f3f4f6; border-color: #e5e7eb; }
            QPushButton#statsDeadBtn {
                min-height: 0; font-size: 12px; padding: 5px 12px; border-radius: 7px;
                border: 1px solid #f5c6c6; background: #fff5f5; color: #dc2626; font-weight: 600;
            }
            QPushButton#statsDeadBtn:checked { background: #dc2626; color: #ffffff; border-color: #dc2626; }
            QPushButton#statsDeadBtn:disabled { color: #ccc; background: #f9f9f9; border-color: #e5e7eb; }
            QLabel#statsCacheBanner {
                font-size: 12px; font-weight: 600; color: #92400e;
                background: #fef3c7; border: 1px solid #fcd34d;
                border-radius: 7px; padding: 6px 12px;
            }
            QProgressBar#statsProgressBar {
                border: none; border-radius: 3px; background: #e2e8f0;
            }
            QProgressBar#statsProgressBar::chunk {
                border-radius: 3px; background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #3b82f6, stop:1 #8b5cf6);
            }
        """)


class _NumItem(QTableWidgetItem):
    """Элемент таблицы с числовой сортировкой."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        try:
            return int(self.text()) < int(other.text())
        except ValueError:
            return super().__lt__(other)
