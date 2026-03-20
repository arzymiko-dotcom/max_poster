"""
Пересчитывает SHA256 установщика и обновляет version.txt.

Запускать после каждой сборки installer:
    python update_hash.py
"""
import hashlib
from pathlib import Path

ROOT = Path(__file__).parent
INSTALLER = ROOT / "installer" / "MAX POST_setup.exe"
VERSION_FILE = ROOT / "version.txt"


def main() -> None:
    if not INSTALLER.exists():
        print(f"[ERROR] Установщик не найден: {INSTALLER}")
        return

    data = INSTALLER.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    size_mb = len(data) / 1024 / 1024

    lines = VERSION_FILE.read_text(encoding="utf-8").strip().splitlines()
    version = lines[0].strip() if lines else "0.0.0"

    # Оставляем только строку версии + новый хэш
    new_content = f"{version}\nsha256:{sha}\n"
    VERSION_FILE.write_text(new_content, encoding="utf-8")

    print(f"Версия : {version}")
    print(f"Файл   : {INSTALLER.name}  ({size_mb:.1f} МБ)")
    print(f"SHA256 : {sha}")
    print()
    print("version.txt обновлён.")
    print("Теперь залей на GitHub (git commit + push) и загрузи установщик на Яндекс.Диск.")


if __name__ == "__main__":
    main()
