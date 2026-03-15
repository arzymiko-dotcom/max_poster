"""bump_version.py — обновляет version.txt.

Использование:
  python bump_version.py patch       # 1.1.5 -> 1.1.6
  python bump_version.py minor       # 1.1.5 -> 1.2.0
  python bump_version.py major       # 1.1.5 -> 2.0.0
  python bump_version.py 1.2.3       # конкретная версия
  python bump_version.py             # показать текущую версию
"""
import sys
from pathlib import Path

VERSION_FILE = Path(__file__).parent / "version.txt"


def read_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def write_version(version: str) -> None:
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")


def bump(current: str, part: str) -> str:
    try:
        major, minor, patch = map(int, current.split("."))
    except ValueError:
        raise SystemExit(f"Не могу разобрать версию: {current!r}")
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def main() -> None:
    current = read_version()
    if len(sys.argv) < 2:
        print(f"Текущая версия: {current}")
        print("Использование: python bump_version.py [patch|minor|major|x.y.z]")
        return

    arg = sys.argv[1]
    if arg in ("patch", "minor", "major"):
        new_version = bump(current, arg)
    else:
        parts = arg.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise SystemExit(f"Неверный формат версии: {arg!r}  (ожидается x.y.z)")
        new_version = arg

    write_version(new_version)
    print(f"Версия обновлена: {current} -> {new_version}")


if __name__ == "__main__":
    main()
