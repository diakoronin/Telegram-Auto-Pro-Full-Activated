from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class BotState:
    def __init__(self, state_path: str) -> None:
        self.path = Path(state_path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_group_chat_id(self) -> str | None:
        data = self._read()
        value = str(data.get("group_chat_id", "")).strip()
        return value or None

    def set_group_chat_id(self, chat_id: str) -> None:
        data = self._read()
        data["group_chat_id"] = str(chat_id).strip()
        self._write(data)
