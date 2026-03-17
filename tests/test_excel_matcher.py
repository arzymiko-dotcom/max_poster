"""Тесты для excel_matcher.py — без реального Excel файла."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock
from address_parser import ParsedAddress
from excel_matcher import ExcelMatcher, MatchResult


class TestMatchResult:
    def test_creation(self):
        r = MatchResult(address="ул. Ленина, д. 1", score=100, chat_link="https://web.max.ru/-123", chat_id="-123")
        assert r.address == "ул. Ленина, д. 1"
        assert r.score == 100
        assert r.chat_id == "-123"


class TestExcelMatcherScoring:
    """Тестируем логику скоринга без реального Excel."""

    def _make_matcher_with_data(self, rows: list[dict]) -> ExcelMatcher:
        """Создаёт ExcelMatcher с подставленными данными вместо чтения файла."""
        matcher = ExcelMatcher.__new__(ExcelMatcher)
        matcher._df = None
        matcher._rows = rows
        matcher.excel_path = Path("fake.xlsx")
        return matcher

    def test_exact_street_and_house_match(self):
        rows = [
            {"адрес": "ул. Ленина, д. 5", "ссылка": "https://web.max.ru/-100", "id": "-100"},
            {"адрес": "ул. Ленина, д. 10", "ссылка": "https://web.max.ru/-200", "id": "-200"},
        ]
        matcher = self._make_matcher_with_data(rows)
        # Используем точный стем как после normalize_text
        parsed = ParsedAddress(street="ленина", house="5")
        results = matcher.find_matches(parsed)
        assert len(results) >= 1
        assert results[0].chat_id == "-100"

    def test_wrong_house_no_match(self):
        """Если дом указан, не должен матчиться на другой дом."""
        rows = [
            {"адрес": "ул. Ленина, д. 5", "ссылка": "https://web.max.ru/-100", "id": "-100"},
        ]
        matcher = self._make_matcher_with_data(rows)
        parsed = ParsedAddress(street="ленина", house="16")
        results = matcher.find_matches(parsed)
        # С новым строгим матчингом — не должно совпасть
        assert all(r.score < 80 for r in results) or len(results) == 0

    def test_no_house_matches_street(self):
        """Без дома — матчится по улице."""
        rows = [
            {"адрес": "ул. Ленина, д. 5", "ссылка": "https://web.max.ru/-100", "id": "-100"},
        ]
        matcher = self._make_matcher_with_data(rows)
        parsed = ParsedAddress(street="ленина", house="")
        results = matcher.find_matches(parsed)
        assert len(results) >= 1
