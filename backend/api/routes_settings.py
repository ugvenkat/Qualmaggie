"""
Settings routes.

GET  /api/settings   — Return editable trading parameters from settings.json.
POST /api/settings   — Update trading parameters in settings.json and reload cache.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.config.settings import PROJECT_ROOT, get_settings

router = APIRouter()

_SETTINGS_PATH = PROJECT_ROOT / "settings.json"

# Keys excluded from the editable API (connection strings and folder paths)
_EXCLUDED_KEYS = {"DatabaseUrl", "DataInputFolder", "DataFolder", "ExportFolder", "CacheFolder"}


@router.get("")
def get_settings_endpoint() -> dict[str, Any]:
    """Return all editable trading parameters from settings.json."""
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read settings.json: {exc}") from exc
    return {k: v for k, v in data.items() if k not in _EXCLUDED_KEYS}


@router.post("")
def save_settings_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist updated trading parameters to settings.json and invalidate the settings cache."""
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            current = json.load(f)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read settings.json: {exc}") from exc

    for key, value in payload.items():
        if key not in _EXCLUDED_KEYS:
            current[key] = value

    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write settings.json: {exc}") from exc

    # Clear lru_cache so the next request loads the updated values
    get_settings.cache_clear()

    return {k: v for k, v in current.items() if k not in _EXCLUDED_KEYS}
