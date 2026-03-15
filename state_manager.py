
import json
from pathlib import Path
from typing import Any


class StateManager:
    def __init__(self, file_path: str | Path = "app_state.json") -> None:
        self.file_path = Path(file_path)

    def load(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return {}

        try:
            with open(self.file_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, data: dict[str, Any]) -> None:
        try:
            with open(self.file_path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
        except OSError:
            pass