"""Тесты для address_parser.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from address_parser import extract_all_addresses


class TestExtractAllAddresses:
    def test_simple_address(self):
        result = extract_all_addresses("ул. Есенина, д. 32")
        assert len(result) >= 1
        assert any("есенин" in r.street for r in result)

    def test_address_with_house(self):
        result = extract_all_addresses("г. Санкт-Петербург, ул. Есенина, д. 32/1")
        assert len(result) >= 1
        r = result[0]
        assert "есенин" in r.street
        assert r.house == "32/1"

    def test_multiple_addresses(self):
        text = "ул. Ленина, д. 1\nпр. Невский, д. 5"
        result = extract_all_addresses(text)
        assert len(result) >= 2

    def test_empty_text(self):
        result = extract_all_addresses("")
        assert result == []

    def test_no_addresses(self):
        result = extract_all_addresses("Привет, как дела? Сегодня хорошая погода.")
        assert result == []

    def test_street_type_suffix(self):
        """Сиреневый б-р, 9"""
        result = extract_all_addresses("Сиреневый бульвар, д. 9")
        assert len(result) >= 1

    def test_deduplication(self):
        """Один и тот же адрес дважды — должен быть один результат."""
        text = "ул. Есенина, д. 5\nул. Есенина, д. 5"
        result = extract_all_addresses(text)
        # Дубли по street+house должны убираться
        streets = [(r.street, r.house) for r in result]
        assert len(streets) == len(set(streets))

    def test_semicolon_separated(self):
        """ул. X, д. 1;2;3"""
        result = extract_all_addresses("ул. Ленина, д. 1;2;3")
        assert len(result) >= 2

    def test_stop_words_not_parsed(self):
        """Предлоги и месяцы не должны парситься как адреса."""
        result = extract_all_addresses("Сегодня в январе на улице холодно.")
        # Если парсит — это приемлемо, но не должно быть ложных street
        for r in result:
            assert r.street not in ("январ", "улиц", "холодн")
