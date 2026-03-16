import re
from dataclasses import dataclass


@dataclass
class ParsedAddress:
    street: str | None = None
    house: str | None = None
    raw_fragment: str | None = None


def normalize_text(value: str) -> str:
    text = str(value).lower().strip().replace("ё", "е")

    replacements = {
        "улица": "ул",
        "ул.": "ул",
        "проспект": "пр",
        "пр-кт.": "пр",
        "пр-кт": "пр",
        "пр-т": "пр",
        "пр.": "пр",
        "переулок": "пер",
        "пер.": "пер",
        "бульвар": "бул",
        "бул.": "бул",
        "б-р": "бул",
        "шоссе": "ш",
        "ш.": "ш",
        "набережная": "наб",
        "наб.": "наб",
        "дом": "д",
        "д.": "д",
        "корпус": "корп",
        "корп.": "корп",
        "к.": "корп",
        "литера": "лит",
        "литер": "лит",
        "лит.": "лит",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"[^а-яёa-z0-9\s/]", " ", text, flags=re.IGNORECASE | re.UNICODE)
    text = re.sub(r"\s+", " ", text, flags=re.UNICODE).strip()
    return text


# Паттерн с префиксом + номер дома: "ул есенина 22", "пр просвещения д 5"
_STREET_TYPES = r"(?:ул|пр|пер|бул|ш|наб)"

# Группа номера дома: "4", "32/1", "4 корп 1" (корпус захватывается для различения адресов)
_HOUSE_GROUP = r"(\d+[а-яa-z]?(?:/\d+[а-яa-z]?)?(?:\s+корп\s+\d+[а-яa-z]?)?)"

_PATTERN_WITH_TYPE = re.compile(
    r"\b(?:" + _STREET_TYPES + r")\s+"
    r"((?:[а-яa-z\-]+\s+){1,4}?)"
    r"(?:д\s+)?" + _HOUSE_GROUP + r"\b",
    flags=re.IGNORECASE | re.UNICODE,
)

# Паттерн с префиксом БЕЗ номера дома: "пр просвещения"
_PATTERN_WITH_TYPE_NO_NUM = re.compile(
    r"\b(?:" + _STREET_TYPES + r")\s+"
    r"([а-яa-z\-]+(?:\s+[а-яa-z\-]+){0,3})\s*$",
    flags=re.IGNORECASE | re.UNICODE,
)

# Паттерн с суффиксом типа + номер: "Сиреневый б-р, 9" → "сиреневый бул 9"
_PATTERN_TYPE_SUFFIX = re.compile(
    r"\b([а-яa-z\-]+(?:\s+[а-яa-z\-]+){0,3}?)\s+(?:" + _STREET_TYPES + r")\s+"
    r"(?:д\s+)?" + _HOUSE_GROUP + r"\b",
    flags=re.IGNORECASE | re.UNICODE,
)

# Паттерн без префикса + номер: "Есенина 22", "Народного Ополчения 5"
_PATTERN_NO_TYPE = re.compile(
    r"\b([а-яa-z\-]+(?:\s+[а-яa-z\-]+){0,3}?)\s+(?:д\s+)?" + _HOUSE_GROUP + r"\b",
    flags=re.IGNORECASE | re.UNICODE,
)

# Стоп-слова — не могут быть названием улицы
_STOP_WORDS = {
    "с", "до", "по", "от", "на", "в", "из", "за", "при", "под", "над",
    "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
    "октября", "ноября", "декабря", "января", "февраля",
    "в связи", "работ", "временное", "отключение", "водоснабжения",
    "часов", "ч", "мин", "утра", "вечера", "дня",
}


def _normalize_house(raw: str) -> str:
    """Нормализует номер дома: '4 корп 1' → '4/1'."""
    return re.sub(r"\s+корп\s+", "/", raw.strip())


def _try_extract(text: str) -> ParsedAddress | None:
    """Пробует извлечь адрес из переданного текста."""
    # Убираем префикс города: "г. Санкт-Петербург," и т.п.
    text = re.sub(r"^\s*г[.\s]+[а-яa-z\-]+\s*,\s*", "", text.strip(), flags=re.IGNORECASE | re.UNICODE)
    normalized = normalize_text(text)

    # 1. С префиксом + номер дома — самый надёжный
    match = _PATTERN_WITH_TYPE.search(normalized)
    if match:
        street = match.group(1).strip()
        # Отбрасываем мусорные совпадения вида street="д" (однобуквенные аббревиатуры)
        if street and not all(len(w) <= 2 for w in street.split()):
            return ParsedAddress(
                street=street,
                house=_normalize_house(match.group(2)),
                raw_fragment=match.group(0).strip(),
            )

    # 2. Тип-суффикс + номер — "Сиреневый б-р, 9" → нормализуется в "сиреневый бул 9"
    match = _PATTERN_TYPE_SUFFIX.search(normalized)
    if match:
        street = match.group(1).strip()
        if street and not all(len(w) <= 2 for w in street.split()):
            return ParsedAddress(
                street=street,
                house=_normalize_house(match.group(2)),
                raw_fragment=match.group(0).strip(),
            )

    # 3. С префиксом, но без номера — "пр. Просвещения"
    match = _PATTERN_WITH_TYPE_NO_NUM.search(normalized)
    if match:
        return ParsedAddress(
            street=match.group(1).strip(),
            house=None,
            raw_fragment=match.group(0).strip(),
        )

    # 4. Без префикса + номер — "Есенина 22"
    for match in _PATTERN_NO_TYPE.finditer(normalized):
        street_name = match.group(1).strip()
        house = _normalize_house(match.group(2))

        words = street_name.split()
        if any(w in _STOP_WORDS for w in words):
            continue
        if all(len(w) <= 2 for w in words):
            continue

        return ParsedAddress(
            street=street_name,
            house=house,
            raw_fragment=match.group(0).strip(),
        )

    return None


def extract_all_addresses(text: str) -> list[ParsedAddress]:
    """Извлекает все адреса из текста — парсит каждую строку отдельно.
    Обрабатывает несколько домов через ';', например: 'ул. Есенина, 32/1;36/1'.
    """
    results: list[ParsedAddress] = []
    seen: set[tuple] = set()

    for raw_line in text.splitlines():
        # Разбиваем по ';' — для случаев "ул. X, 1;2"
        parts = [p.strip().strip(".,") for p in raw_line.split(";")]
        first_street: str | None = None

        for part in parts:
            if not part:
                continue
            addr = _try_extract(part)
            if addr and addr.street:
                first_street = addr.street
                key = (addr.street.lower(), addr.house)
                if key not in seen:
                    seen.add(key)
                    results.append(addr)
            elif first_street and re.match(r"^\d", part):
                # Только номер дома после первого адреса на этой строке
                m = re.match(r"^[\da-zA-Zа-яА-ЯёЁ/]+", part)
                if m:
                    addr = ParsedAddress(
                        street=first_street,
                        house=m.group(0),
                        raw_fragment=f"{first_street} {m.group(0)}",
                    )
                    key = (addr.street.lower(), addr.house)
                    if key not in seen:
                        seen.add(key)
                        results.append(addr)

    if not results:
        addr = extract_address(text)
        if addr.street:
            results.append(addr)

    return results


def extract_address(text: str) -> ParsedAddress:
    # Шаг 1: ищем фрагмент после "адрес:" / "по адресу:" и парсим его
    addr_context = re.search(
        r"(?:по\s+)?адрес[уе]?\s*:?\s*(.{5,80})",
        text,
        flags=re.IGNORECASE | re.UNICODE,
    )
    if addr_context:
        result = _try_extract(addr_context.group(1))
        if result:
            return result

    # Шаг 2: парсим весь текст целиком
    result = _try_extract(text)
    if result:
        return result

    return ParsedAddress()
