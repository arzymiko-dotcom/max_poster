"""
stats_panel.py — Панель «Статистика групп» MAX POST.

Загружает HTML-отчёт с report-сервера, парсит таблицу групп и отображает
в реальном времени: название, участников, время последней активности, ссылку.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd
import requests
from PyQt6.QtCore import QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

_log = logging.getLogger(__name__)

REPORT_URL = "https://bot-dev.gkh.spb.ru/gks2vyb-report.php"


def _resolve_excel_path() -> Path:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    return base / "max_address.xlsx"


def _norm_link(link: str) -> str:
    """Normalize join link for comparison."""
    link = link.strip().lower().rstrip("/")
    if "max.ru/join/" in link:
        return "join:" + link.split("max.ru/join/")[-1].strip("/")
    if "web.max.ru/" in link:
        return "web:" + link.split("web.max.ru/")[-1].strip("/")
    return link


_AUTO_REFRESH_MS  = 5 * 60 * 1000  # авто-обновление каждые 5 минут
_REQUEST_TIMEOUT  = 8              # секунд — быстрый таймаут, не вешаем UI


def _cache_path() -> Path:
    """Путь к файлу кэша последней успешной загрузки."""
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("APPDATA", Path.home())) / "MAX POST"
    else:
        base = Path(__file__).parent
    base.mkdir(parents=True, exist_ok=True)
    return base / "stats_cache.json"


def _save_cache(rows: list[dict], summary_texts: list[str]) -> None:
    path = _cache_path()
    data = {
        "ts": datetime.now().isoformat(),
        "rows": rows,
        "summary_texts": summary_texts,
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


def _load_cache() -> tuple[list[dict], list[str], datetime | None]:
    """Загружает кэш. Возвращает (rows, summary_texts, timestamp) или ([], [], None)."""
    try:
        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(raw["ts"])
        return raw["rows"], raw.get("summary_texts", []), ts
    except Exception:
        return [], [], None

# Индексы колонок
_COL_NAME    = 0
_COL_MEMBERS = 1
_COL_TIME    = 2
_COL_LINK    = 3


# ────────────────────────────────────────────────────────────────
#  HTML-парсер
# ────────────────────────────────────────────────────────────────

class _ReportParser(HTMLParser):
    """Извлекает строки таблицы и сводные данные из HTML-отчёта."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self.summary_texts: list[str] = []

        self._in_table = False
        self._in_row   = False
        self._in_cell  = False
        self._is_th    = False                        # строка-заголовок?
        self._row_cells: list[tuple[str, str]] = []  # (text, href)
        self._cell_text = ""
        self._cell_href = ""

        self._in_p    = False
        self._p_text  = ""

    # ── handlers ────────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs: list) -> None:
        d = dict(attrs)
        if tag == "table":
            self._in_table = True
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._is_th  = False
            self._row_cells = []
        elif tag == "th" and self._in_row:
            self._in_cell = True
            self._is_th   = True
            self._cell_text = ""
            self._cell_href = ""
        elif tag == "td" and self._in_row:
            self._in_cell = True
            self._cell_text = ""
            self._cell_href = ""
        elif tag == "a" and self._in_cell:
            href = d.get("href", "")
            if href and href.startswith("http"):
                self._cell_href = href
        elif tag == "p":
            self._in_p   = True
            self._p_text = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._in_table = False
        elif tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            self._row_cells.append((self._cell_text.strip(), self._cell_href.strip()))
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if not self._is_th and len(self._row_cells) >= 3:
                name    = self._row_cells[0][0]
                members = self._row_cells[1][0]
                t_event = self._row_cells[2][0]
                link_text, link_href = self._row_cells[3] if len(self._row_cells) > 3 else ("", "")
                link = link_href or link_text
                if name:
                    self.rows.append({
                        "name":       name,
                        "members":    members,
                        "last_event": t_event,
                        "link":       link,
                    })
        elif tag == "p":
            self._in_p = False
            t = self._p_text.strip()
            if t:
                self.summary_texts.append(t)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text += data
        if self._in_p:
            self._p_text += data


def _parse_html(html: str) -> tuple[list[dict], list[str]]:
    p = _ReportParser()
    p.feed(html)
    return p.rows, p.summary_texts


# ────────────────────────────────────────────────────────────────
#  Фоновый поток загрузки
# ────────────────────────────────────────────────────────────────

class _FetchWorker(QThread):
    finished = pyqtSignal(list, list)   # (rows, summary_texts)
    failed   = pyqtSignal(str)

    def run(self) -> None:
        try:
            resp = requests.get(REPORT_URL, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            rows, summary = _parse_html(resp.text)
            if not rows:
                self.failed.emit("Сервер вернул пустой ответ — возможно, изменилась структура страницы")
                return
            self.finished.emit(rows, summary)
        except requests.exceptions.Timeout:
            self.failed.emit("Сервер не ответил за 8 секунд (таймаут)")
        except requests.exceptions.ConnectionError:
            self.failed.emit("Нет соединения с сервером")
        except Exception as exc:
            _log.warning("stats fetch error: %s", exc)
            self.failed.emit(str(exc))


# ────────────────────────────────────────────────────────────────
#  Виджет панели
# ────────────────────────────────────────────────────────────────

class StatsPanel(QWidget):
    """Панель «Статистика групп» — таблица с реалтайм-данными."""

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

        # Первая загрузка
        QTimer.singleShot(0, self.refresh)

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

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
        root.addLayout(hdr)

        # ── Сводка ───────────────────────────────────────────────
        self._summary_frame = QFrame()
        self._summary_frame.setObjectName("statsSummary")
        sf_layout = QHBoxLayout(self._summary_frame)
        sf_layout.setContentsMargins(14, 8, 14, 8)
        sf_layout.setSpacing(28)

        self._lbl_groups  = self._make_stat_lbl("—", "Групп")
        self._lbl_members = self._make_stat_lbl("—", "Участников")
        self._lbl_active  = self._make_stat_lbl("—", "Активны сегодня")
        self._lbl_missing = self._make_stat_lbl("—", "Нет в отчёте")

        for w in (self._lbl_groups, self._lbl_members, self._lbl_active, self._lbl_missing):
            sf_layout.addWidget(w)
        sf_layout.addStretch()
        root.addWidget(self._summary_frame)

        # ── Баннер кэша (скрыт по умолчанию) ────────────────────
        self._cache_banner = QLabel()
        self._cache_banner.setObjectName("statsCacheBanner")
        self._cache_banner.setWordWrap(True)
        self._cache_banner.hide()
        root.addWidget(self._cache_banner)

        # ── Поиск ────────────────────────────────────────────────
        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍  Поиск по названию...")
        self._search.setObjectName("statsSearch")
        self._search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self._search)

        self._dead_btn = QPushButton("🔴 Отсутствующие")
        self._dead_btn.setObjectName("statsDeadBtn")
        self._dead_btn.setCheckable(True)
        self._dead_btn.setEnabled(False)
        self._dead_btn.toggled.connect(self._on_dead_toggled)
        search_row.addWidget(self._dead_btn)
        root.addLayout(search_row)

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
        root.addWidget(self._table)

        # ── Статус-строка ─────────────────────────────────────────
        self._status_lbl = QLabel("Загрузка…")
        self._status_lbl.setObjectName("statsStatus")
        root.addWidget(self._status_lbl)

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
        self._status_lbl.setText("Получение данных…")
        self._worker = _FetchWorker(self)
        self._worker.finished.connect(self._on_data)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._worker.finished.connect(lambda *_: setattr(self, "_worker", None))
        self._worker.failed.connect(lambda *_: setattr(self, "_worker", None))
        self._worker.start()

    def _on_data(self, rows: list[dict], summary_texts: list[str]) -> None:
        self._spin_timer.stop()
        self._all_rows = rows
        self._last_refresh = datetime.now()
        self._last_lbl.setText(f"Обновлено в {self._last_refresh.strftime('%H:%M:%S')}")
        self._cache_banner.hide()          # сервер ответил — баннер не нужен
        _save_cache(rows, summary_texts)   # сохраняем кэш для резервного показа
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
            except ValueError:
                pass
            # Считаем активными — последнее событие сегодня
            try:
                ev_dt = datetime.strptime(r["last_event"][:10], "%d.%m.%Y")
                if ev_dt.date() == today:
                    active_today += 1
            except ValueError:
                pass

        self._lbl_groups._val_lbl.setText(str(total))  # type: ignore[attr-defined]
        self._lbl_members._val_lbl.setText(f"{total_members:,}".replace(",", " "))  # type: ignore[attr-defined]
        self._lbl_active._val_lbl.setText(str(active_today))  # type: ignore[attr-defined]

        # Мёртвые группы: загружаем Excel и ищем отсутствующие в отчёте
        self._missing_rows = []
        try:
            excel_path = _resolve_excel_path()
            if excel_path.exists():
                df = pd.read_excel(excel_path, usecols=[0, 1], dtype=str)
                df = df.fillna("")
                # Нормализованные ссылки из отчёта
                report_links: set[str] = set()
                for r in rows:
                    lnk = r.get("link", "").strip()
                    if lnk:
                        report_links.add(_norm_link(lnk))
                for _, row_data in df.iterrows():
                    addr = str(row_data.iloc[0]).strip()
                    link = str(row_data.iloc[1]).strip()
                    if not link:
                        continue
                    if _norm_link(link) not in report_links:
                        self._missing_rows.append({
                            "name":       addr,
                            "members":    "—",
                            "last_event": "Нет данных",
                            "link":       link,
                        })
        except Exception as exc:
            _log.warning("dead groups check error: %s", exc)

        missing_count = len(self._missing_rows)
        missing_val_lbl = self._lbl_missing._val_lbl  # type: ignore[attr-defined]
        missing_val_lbl.setText(str(missing_count) if missing_count >= 0 else "—")
        if missing_count > 0:
            missing_val_lbl.setStyleSheet(
                "color: #dc2626; font-size: 24px; font-weight: 700;"
            )
        else:
            missing_val_lbl.setStyleSheet("")

        self._dead_btn.setEnabled(True)

        self._apply_filter()
        self._status_lbl.setText(
            f"Загружено {total} групп · "
            f"обновлено {self._last_refresh.strftime('%d.%m.%Y %H:%M:%S')}"
        )

    def _on_error(self, msg: str) -> None:
        self._spin_timer.stop()
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("⟳  Обновить")
        cached_rows, cached_summary, cached_ts = _load_cache()
        if cached_rows:
            self._all_rows = cached_rows
            ts_str = cached_ts.strftime("%d.%m.%Y %H:%M") if cached_ts else "неизвестно"
            self._cache_banner.setText(
                f"⚠️  Сервер недоступен: {msg}  ·  Показаны данные от {ts_str}"
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
            rows = [
                r for r in self._missing_rows
                if not query or query in r["name"].lower()
            ]
            self._fill_table(rows, dead_mode=True)
            shown = len(rows)
            total = len(self._missing_rows)
            self._status_lbl.setText(
                f"Отсутствующих: показано {shown} из {total}"
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
                # Определяем «свежесть» события для окраски
                fresh_today = False
                fresh_week  = False
                try:
                    ev_dt = datetime.strptime(t_event[:10], "%d.%m.%Y").date()
                    fresh_today = (ev_dt == today)
                    fresh_week  = (ev_dt >= today - timedelta(days=7))
                except ValueError:
                    pass

                # Цвет строки
                if fresh_today:
                    row_bg = QColor("#f0faf2")  # зеленоватый — активны сегодня
                elif fresh_week:
                    row_bg = QColor("#fffbee")  # желтоватый — активны на этой неделе
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
        """Двойной клик по строке — открывает ссылку группы в браузере."""
        link = self._get_row_link(row)
        if link.startswith("http"):
            QDesktopServices.openUrl(QUrl(link))

    def _show_context_menu(self, pos) -> None:
        """Правый клик — контекстное меню: открыть в браузере / копировать ссылку."""
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

        act_open = menu.addAction("🌐  Открыть в браузере")
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
        """Экспортирует текущее содержимое таблицы в .xlsx через openpyxl."""
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment
        except ImportError:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Экспорт", "Библиотека openpyxl не установлена.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить статистику", "статистика_групп.xlsx",
            "Excel (*.xlsx)"
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

        # Автоширина колонок
        for col in ws.columns:
            max_len = max((len(str(c.value)) if c.value else 0) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)

        try:
            wb.save(path)
            self._status_lbl.setText(f"Экспортировано {row_count} строк → {path}")
        except Exception as exc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Экспорт", f"Ошибка сохранения: {exc}")

    # ── Стили ────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._auto_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(2000)
        super().closeEvent(event)

    def set_dark(self, dark: bool) -> None:
        """Переключает тёмную/светлую тему панели."""
        self._apply_styles(dark=dark)

    def _apply_styles(self, dark: bool = False) -> None:
        if dark:
            self.setStyleSheet("""
                StatsPanel { background: #1e1e2e; }
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
            """)
            return
        self.setStyleSheet("""
            StatsPanel {
                background: #f3f4f6;
            }
            QLabel#statsPanelTitle {
                font-size: 18px;
                font-weight: 700;
                color: #1a1a2e;
            }
            QLabel#statsLastRefresh {
                font-size: 11px;
                color: #9ca3af;
                padding-right: 8px;
            }
            QPushButton#statsRefreshBtn {
                min-height: 0;
                font-size: 13px;
                font-weight: 600;
                padding: 4px 16px;
                border-radius: 7px;
                border: 1px solid #c7d0db;
                background: #eef2f7;
                color: #334155;
            }
            QPushButton#statsRefreshBtn:hover {
                background: #dbeafe;
                border-color: #2d6cdf;
                color: #1d4ed8;
            }
            QPushButton#statsRefreshBtn:disabled {
                color: #9ca3af;
            }
            QFrame#statsSummary {
                background: #ffffff;
                border: 1px solid #e4eaf0;
                border-radius: 10px;
            }
            QLabel#statsStatValue {
                font-size: 24px;
                font-weight: 700;
                color: #1e3a5f;
            }
            QLabel#statsStatLabel {
                font-size: 11px;
                color: #9ca3af;
                font-weight: 500;
            }
            QLineEdit#statsSearch {
                font-size: 13px;
                padding: 7px 12px;
                border: 1px solid #c7d0db;
                border-radius: 8px;
                background: #ffffff;
                color: #1a1a2e;
            }
            QTableWidget#statsTable {
                border: 1px solid #e4eaf0;
                border-radius: 8px;
                background: #ffffff;
                alternate-background-color: #f8fafc;
                gridline-color: #f0f4f8;
                font-size: 12px;
                color: #1a1a2e;
            }
            QTableWidget#statsTable QHeaderView::section {
                background: #f1f5f9;
                color: #64748b;
                font-size: 11px;
                font-weight: 700;
                padding: 6px 10px;
                border: none;
                border-bottom: 2px solid #e2e8f0;
                text-transform: uppercase;
                letter-spacing: 0.4px;
            }
            QTableWidget#statsTable::item:selected {
                background: #dbeafe;
                color: #1e3a5f;
            }
            QLabel#statsStatus {
                font-size: 11px;
                color: #9ca3af;
                padding: 2px 0;
            }
            QPushButton#statsExportBtn {
                min-height: 0;
                font-size: 13px;
                font-weight: 600;
                padding: 4px 14px;
                border-radius: 7px;
                border: 1px solid #a7f3d0;
                background: #ecfdf5;
                color: #065f46;
            }
            QPushButton#statsExportBtn:hover {
                background: #d1fae5;
                border-color: #34d399;
            }
            QPushButton#statsExportBtn:disabled {
                color: #9ca3af;
                background: #f3f4f6;
                border-color: #e5e7eb;
            }
            QPushButton#statsDeadBtn {
                min-height: 0;
                font-size: 12px;
                padding: 5px 12px;
                border-radius: 7px;
                border: 1px solid #f5c6c6;
                background: #fff5f5;
                color: #dc2626;
                font-weight: 600;
            }
            QPushButton#statsDeadBtn:checked {
                background: #dc2626;
                color: #ffffff;
                border-color: #dc2626;
            }
            QPushButton#statsDeadBtn:disabled {
                color: #ccc;
                background: #f9f9f9;
                border-color: #e5e7eb;
            }
            QLabel#statsCacheBanner {
                font-size: 12px;
                font-weight: 600;
                color: #92400e;
                background: #fef3c7;
                border: 1px solid #fcd34d;
                border-radius: 7px;
                padding: 6px 12px;
            }
        """)


class _NumItem(QTableWidgetItem):
    """Элемент таблицы с числовой сортировкой."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        try:
            return int(self.text()) < int(other.text())
        except ValueError:
            return super().__lt__(other)
