import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from address_parser import ParsedAddress, normalize_text


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
        self._df: pd.DataFrame | None = None  # кэш — читаем Excel один раз

    def load_dataframe(self) -> pd.DataFrame:
        if self._df is None:
            try:
                self._df = pd.read_excel(self.excel_path, dtype=str)
            except FileNotFoundError:
                raise FileNotFoundError(f"Файл адресов не найден: {self.excel_path}") from None
            except Exception as exc:
                raise RuntimeError(
                    f"Не удалось открыть файл адресов '{self.excel_path}': {exc}"
                ) from exc
        return self._df

    def _resolve_columns(self, df: pd.DataFrame) -> tuple[str, str | None, str | None]:
        """Возвращает (колонка адреса, колонка ссылки, колонка ID)."""
        columns_map = {str(col).strip().lower(): col for col in df.columns}
        address_col = columns_map.get("адрес") or df.columns[0]
        link_col = columns_map.get("ссылка") or (df.columns[1] if len(df.columns) > 1 else None)
        id_col = columns_map.get("id") or (df.columns[2] if len(df.columns) > 2 else None)
        return address_col, link_col, id_col

    def find_matches(self, parsed_address: ParsedAddress) -> list[MatchResult]:
        if not parsed_address.street:
            return []

        df = self.load_dataframe()
        address_col, link_col, id_col = self._resolve_columns(df)

        matches: list[MatchResult] = []

        for _, row in df.iterrows():
            raw_address = str(row.get(address_col, "")).strip()
            if not raw_address:
                continue

            normalized_address = normalize_text(raw_address)
            score = 0
            street_matched = False
            house_matched = False

            if parsed_address.street:
                street_words = parsed_address.street.split()
                if all(
                    re.search(r"\b" + re.escape(w) + r"\b", normalized_address)
                    for w in street_words
                ):
                    score += 50
                    street_matched = True

            if parsed_address.house:
                house = parsed_address.house
                # Требуем "д N" — не матчим корпус/литеру по случайному совпадению числа
                if re.search(r"\bд\s+" + re.escape(house) + r"\b", normalized_address):
                    score += 100
                    house_matched = True
                elif "/" in house:
                    # "32/1" может означать "д. 32, корп. 1"
                    base, korpus = house.split("/", 1)
                    if (
                        re.search(r"\bд\s+" + re.escape(base) + r"\b", normalized_address)
                        and re.search(r"\bкорп\s+" + re.escape(korpus) + r"\b", normalized_address)
                    ):
                        score += 90
                        house_matched = True

            if parsed_address.raw_fragment and parsed_address.raw_fragment in normalized_address:
                score += 30

            # Если дом указан — он обязан совпасть. Иначе "Скобелевский 10" ошибочно
            # займёт "д. 16" через совпадение только по улице и вытолкнет верный "Скобелевский 16".
            if parsed_address.house and not house_matched:
                continue

            if score > 0 and street_matched:
                matches.append(MatchResult(
                    address=raw_address,
                    score=score,
                    chat_link=_get_cell(row, link_col),
                    chat_id=_get_cell(row, id_col),
                ))

        matches.sort(key=lambda x: (-x.score, x.address))
        return matches
