import logging
import re
from dataclasses import dataclass
from pathlib import Path

from address_parser import ParsedAddress, normalize_text

_log = logging.getLogger(__name__)


def _get_cell(row, col) -> str:
    """Извлекает строковое значение ячейки, убирает '.0' и разворачивает ссылки MAX."""
    if not col:
        return ""
    raw = row.get(col)
    if raw is None:
        return ""
    v = str(raw).strip()
    if not v or v.lower() == "nan":
        return ""
    if v.endswith(".0"):
        v = v[:-2]
    if "web.max.ru/" in v:
        v = v.split("web.max.ru/")[-1].strip("/")
    return v


@dataclass
class MatchResult:
    address: str
    score: int
    chat_link: str = ""
    chat_id: str = ""


class ExcelMatcher:
    def __init__(self, excel_path: str | Path) -> None:
        self.excel_path = Path(excel_path)
        self._df = None  # кэш — читаем Excel один раз
        self._search_index: list[tuple[str, str, MatchResult]] = []
        # Инвертированный индекс: слово → список индексов в _search_index.
        # Позволяет find_matches() делать пересечение по словам улицы вместо полного скана.
        self._word_index: dict[str, list[int]] = {}

    def load_dataframe(self):
        if self._df is None:
            # Поддержка тестового режима: _rows задаётся напрямую как list[dict]
            if hasattr(self, "_rows") and self._rows is not None:
                import pandas as pd
                self._df = pd.DataFrame(self._rows)
                self._build_search_index()
                return self._df
            import pandas as pd
            try:
                self._df = pd.read_excel(self.excel_path, dtype=str)
            except FileNotFoundError:
                raise FileNotFoundError(f"Файл адресов не найден: {self.excel_path}") from None
            except Exception as exc:
                raise RuntimeError(
                    f"Не удалось открыть файл адресов '{self.excel_path}': {exc}"
                ) from exc
            self._build_search_index()
        return self._df

    def _build_search_index(self) -> None:
        """Строит индекс для быстрого поиска: list of (lower_addr, normalized_addr, MatchResult).

        normalized_addr вычисляется один раз при загрузке Excel, чтобы find_matches()
        не вызывал normalize_text() и не итерировал DataFrame при каждом запросе.
        """
        df = self._df
        address_col, link_col, id_col = self._resolve_columns(df)
        index: list[tuple[str, str, MatchResult]] = []
        for _, row in df.iterrows():
            addr = str(row.get(address_col, "")).strip()
            if not addr or addr.lower() == "nan":
                continue
            index.append((
                addr.lower(),
                normalize_text(addr),
                MatchResult(
                    address=addr,
                    score=0,
                    chat_link=_get_cell(row, link_col),
                    chat_id=_get_cell(row, id_col),
                ),
            ))
        self._search_index: list[tuple[str, str, MatchResult]] = index

        # Строим инвертированный индекс: нормализованное слово → [индексы строк]
        word_index: dict[str, list[int]] = {}
        for i, (_lower, normalized, _cached) in enumerate(index):
            for word in normalized.split():
                word_index.setdefault(word, []).append(i)
        self._word_index = word_index

    def _resolve_columns(self, df) -> tuple[str, str | None, str | None]:
        """Возвращает (колонка адреса, колонка ссылки, колонка ID)."""
        columns_map = {str(col).strip().lower(): col for col in df.columns}
        address_col = columns_map.get("адрес")
        if address_col is None:
            _log.warning("Колонка 'адрес' не найдена в Excel, используется первая колонка")
            address_col = df.columns[0]
        link_col = columns_map.get("ссылка") or (df.columns[1] if len(df.columns) > 1 else None)
        id_col = columns_map.get("id") or (df.columns[2] if len(df.columns) > 2 else None)
        return address_col, link_col, id_col

    def find_matches(self, parsed_address: ParsedAddress) -> list[MatchResult]:
        if not parsed_address.street:
            return []

        self.load_dataframe()  # гарантирует построение _search_index и _word_index

        # Используем инвертированный индекс: берём пересечение строк по всем словам улицы.
        # Вместо O(N) полного скана получаем только строки, где все слова улицы есть.
        street_words = parsed_address.street.lower().split()
        candidate_idx: set[int] | None = None
        for word in street_words:
            rows = set(self._word_index.get(word, []))
            candidate_idx = rows if candidate_idx is None else candidate_idx & rows

        if not candidate_idx:
            return []

        matches: list[MatchResult] = []

        for i in candidate_idx:
            _, normalized_address, cached = self._search_index[i]
            # Улица уже подтверждена через word_index — пропускаем повторный поиск
            score = 50
            house_matched = False

            if parsed_address.house:
                house = parsed_address.house
                # Точное совпадение — самый надёжный вариант
                if re.search(r"\bд\s+" + re.escape(house) + r"\b", normalized_address):
                    score += 100
                    house_matched = True
                elif "/" in house:
                    # Разбираем составной номер: "29/2/с" → base="29", corpus="2", litera="с"
                    #                             "31/29/а" → base="31", corpus="29", litera="а"
                    #                             "3/а"    → base="3",  corpus=None,  litera="а"
                    parts = house.split("/")
                    litera = parts[-1] if parts[-1].isalpha() else None
                    num_parts = parts[:-1] if litera else parts[:]
                    base = num_parts[0]
                    corpus = num_parts[1] if len(num_parts) > 1 else None

                    base_ok = bool(re.search(r"\bд\s+" + re.escape(base) + r"\b", normalized_address))
                    if base_ok:
                        # Корпус: ищем "корп N" ИЛИ "д base/corpus" (составной номер типа 31/29)
                        corpus_ok = corpus and bool(re.search(
                            r"\b(?:корп|стр)\s+" + re.escape(corpus) + r"\b", normalized_address
                        ))
                        composite_ok = corpus and bool(re.search(
                            r"\bд\s+" + re.escape(f"{base}/{corpus}") + r"\b", normalized_address
                        ))
                        litera_ok = litera and bool(re.search(
                            r"\bлит\s+" + re.escape(litera) + r"\b", normalized_address
                        ))

                        # Литера указана но не совпала → не матчим (разные здания)
                        if litera and not litera_ok:
                            pass
                        elif corpus and (corpus_ok or composite_ok):
                            score += 90
                            house_matched = True
                        elif corpus and not litera:
                            # Корпус не найден, литера не указана → слабый матч
                            # (реестр может не содержать инфо о корпусе)
                            score += 60
                            house_matched = True
                        elif not corpus:
                            # Нет корпуса: матч по базе + литере (или только базе)
                            score += 90 if litera_ok else 60
                            house_matched = True

            if parsed_address.raw_fragment and parsed_address.raw_fragment in normalized_address:
                score += 30

            # Если дом указан — он обязан совпасть. Иначе "Скобелевский 10" ошибочно
            # займёт "д. 16" через совпадение только по улице и вытолкнет верный "Скобелевский 16".
            if parsed_address.house and not house_matched:
                continue

            matches.append(MatchResult(
                address=cached.address,
                score=score,
                chat_link=cached.chat_link,
                chat_id=cached.chat_id,
            ))

        matches.sort(key=lambda x: (-x.score, x.address))
        return matches

    def get_all(self) -> list[MatchResult]:
        """Возвращает все адреса из реестра (без фильтрации по тексту)."""
        self.load_dataframe()
        return [r for _lower, _norm, r in self._search_index]

    def search(self, query: str, limit: int = 25) -> list[MatchResult]:
        """Ищет адреса по подстроке. Нормализует запрос и поддерживает мульти-токены.

        Примеры: "просвещения 22", "ул есенина", "22/1 скобелевский" — все токены
        должны присутствовать в нормализованном адресе (порядок не важен).
        """
        q_norm = normalize_text(query)
        if not q_norm:
            return []
        self.load_dataframe()  # гарантирует построение _search_index
        tokens = q_norm.split()
        if len(tokens) == 1:
            t = tokens[0]
            results = [r for _lower, norm, r in self._search_index if t in norm]
        else:
            results = [r for _lower, norm, r in self._search_index if all(t in norm for t in tokens)]
        return results[:limit]
