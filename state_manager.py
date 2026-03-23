import json
import shutil
import tempfile
import threading
import time
import warnings
from pathlib import Path
from typing import Any


class StateManager:
    def __init__(self, file_path: str | Path = "app_state.json") -> None:
        self.file_path = Path(file_path)
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return {}

        try:
            with open(self.file_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except json.JSONDecodeError:
            backup = self.file_path.with_suffix(".corrupted.json")
            try:
                shutil.copy2(self.file_path, backup)
                warnings.warn(
                    f"Файл состояния повреждён ({self.file_path}). "
                    f"Резервная копия сохранена в {backup}. Состояние сброшено.",
                    stacklevel=2,
                )
            except OSError:
                warnings.warn(
                    f"Файл состояния повреждён ({self.file_path}) и не удалось создать резервную копию. "
                    f"Состояние сброшено.",
                    stacklevel=2,
                )
            return {}
        except OSError:
            return {}

    def save(self, data: dict[str, Any]) -> None:
        dir_ = self.file_path.parent
        with self._lock:
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=dir_, delete=False, suffix=".tmp"
                ) as tmp:
                    json.dump(data, tmp, ensure_ascii=False, indent=2)
                    tmp_path = Path(tmp.name)
                # Retry: Windows Defender может держать .tmp файл на момент rename
                for attempt in range(3):
                    try:
                        tmp_path.replace(self.file_path)
                        break
                    except OSError:
                        if attempt < 2:
                            time.sleep(0.15)
                        else:
                            tmp_path.unlink(missing_ok=True)
                            raise
            except OSError as exc:
                warnings.warn(
                    f"Не удалось сохранить состояние приложения в {self.file_path}: {exc}",
                    stacklevel=2,
                )
