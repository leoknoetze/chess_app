# multiplayer/manager.py
from __future__ import annotations
import secrets
from typing import Dict, Optional

from chess_core.game import ChessGame
from chess_core.clocks import ChessClocks


def _room_code() -> str:
    # 6-char URL-safe code
    return secrets.token_urlsafe(4).replace("-", "").replace("_", "")[:6].upper()


class Room:
    def __init__(self, minutes: int = 5, inc: int = 0):
        self.game = ChessGame()
        self.clocks = ChessClocks(base_ms=minutes * 60_000, inc_ms=inc * 1000, turn="w")
        self.players: Dict[str, Optional[str]] = {"w": None, "b": None}  # sid map
        self.spectators: set[str] = set()

    def to_state(self):
        return {
            "fen": self.game.get_fen(),
            "status": self.game.game_status(),
            "flags": self.game.status_flags(),
            "clocks": self.clocks.snapshot(),
        }


class GameManager:
    def __init__(self):
        self.rooms: Dict[str, Room] = {}

    def create_room(self, minutes: int = 5, inc: int = 0) -> str:
        code = _room_code()
        while code in self.rooms:
            code = _room_code()
        self.rooms[code] = Room(minutes=minutes, inc=inc)
        return code

    def get(self, code: str) -> Optional[Room]:
        return self.rooms.get(code)

    def join(self, code: str, sid: str) -> dict:
        r = self.rooms.get(code)
        if not r:
            return {"ok": False, "error": "Room not found"}
        color = None
        if r.players["w"] is None:
            r.players["w"] = sid
            color = "w"
        elif r.players["b"] is None:
            r.players["b"] = sid
            color = "b"
        else:
            r.spectators.add(sid)
            color = "s"
        return {"ok": True, "color": color, "state": r.to_state()}

    def leave(self, code: str, sid: str) -> None:
        r = self.rooms.get(code)
        if not r:
            return
        for c in ("w", "b"):
            if r.players[c] == sid:
                r.players[c] = None
        r.spectators.discard(sid)
        # keep room alive; we could gc empty rooms later
