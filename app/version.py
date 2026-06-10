from __future__ import annotations

import json
from pathlib import Path

from app.config import settings


def version_file_path() -> Path:
    return Path(settings.database_path).resolve().parent / "app_version.json"


def current_version() -> dict[str, str]:
    defaults = {
        "status": "unknown",
        "current_version": "unknown",
        "previous_version": "",
        "started_at": "",
        "finished_at": "",
        "message": "Информация о версии появится после установки или обновления.",
    }
    try:
        with version_file_path().open(encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        return defaults
    return {key: str(data.get(key, value) or value) for key, value in defaults.items()}
