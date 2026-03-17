"""Тесты для history_manager.py"""
import sys
import json
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import history_manager


class TestHistoryManager:
    def test_add_and_load(self, tmp_path):
        original = history_manager._HISTORY_FILE
        history_manager._HISTORY_FILE = tmp_path / "history.json"
        try:
            history_manager.add_entry(
                addresses=["ул. Ленина, д. 1"],
                sent_max=True,
                sent_vk=False,
                text="Тестовая публикация",
            )
            entries = history_manager.load()
            assert len(entries) == 1
            assert entries[0]["max"] == ["ул. Ленина, д. 1"]
            assert "vk" not in entries[0] or entries[0]["vk"] is False
            assert "Тестовая" in entries[0]["text"]
        finally:
            history_manager._HISTORY_FILE = original

    def test_clear(self, tmp_path):
        original = history_manager._HISTORY_FILE
        history_manager._HISTORY_FILE = tmp_path / "history.json"
        try:
            history_manager.add_entry(["addr"], True, False, "text")
            history_manager.clear()
            assert history_manager.load() == []
        finally:
            history_manager._HISTORY_FILE = original

    def test_max_entries(self, tmp_path):
        original = history_manager._HISTORY_FILE
        original_max = history_manager._MAX_ENTRIES
        history_manager._HISTORY_FILE = tmp_path / "history.json"
        history_manager._MAX_ENTRIES = 5
        try:
            for i in range(10):
                history_manager.add_entry([f"addr{i}"], True, False, f"text{i}")
            entries = history_manager.load()
            assert len(entries) <= 5
        finally:
            history_manager._HISTORY_FILE = original
            history_manager._MAX_ENTRIES = original_max
