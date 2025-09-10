# storage/store.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict

DATA_FILE = Path(__file__).resolve().parent / "games.json"
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)


def _read() -> Dict[str, Any]:
    if not DATA_FILE.exists():
        return {}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(obj: Dict[str, Any]) -> None:
    DATA_FILE.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def save_room_state(code: str, state: Dict[str, Any]) -> None:
    db = _read()
    db[code] = state
    _write(db)


def load_room_state(code: str) -> Dict[str, Any] | None:
    db = _read()
    return db.get(code)
