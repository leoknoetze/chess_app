# chess_core/clocks.py
from __future__ import annotations
import time
from dataclasses import dataclass, asdict


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class ClockState:
    base_ms: int = 5 * 60_000  # default 5 minutes
    inc_ms: int = 0  # default 0 increment
    w_ms: int = 5 * 60_000
    b_ms: int = 5 * 60_000
    running: bool = False
    turn: str = "w"  # "w" or "b"
    started_at_ms: int | None = None
    flagged: str | None = None  # "w" or "b" when someone flags

    def to_dict(self):
        return asdict(self)


class ChessClocks:
    """
    Server-side authoritative chess clocks with increment.
    - Call start(turn) to begin.
    - Call on_move(moved_by='w'|'b') to commit elapsed and add increment.
    - Call pause() to pause clocks.
    - Call reset(turn='w') to reset to base.
    - Call snapshot() anytime to get up-to-date times (applies live elapsed).
    """

    def __init__(self, base_ms: int = 5 * 60_000, inc_ms: int = 0, turn: str = "w"):
        self.state = ClockState(
            base_ms=base_ms,
            inc_ms=inc_ms,
            w_ms=base_ms,
            b_ms=base_ms,
            running=False,
            turn=turn,
            started_at_ms=None,
            flagged=None,
        )

    # ----- control -----
    def configure(self, base_ms: int, inc_ms: int, turn: str = "w"):
        was_running = self.state.running
        self.pause()
        self.state = ClockState(
            base_ms=base_ms,
            inc_ms=inc_ms,
            w_ms=base_ms,
            b_ms=base_ms,
            running=False,
            turn=turn,
            started_at_ms=None,
            flagged=None,
        )
        if was_running:
            self.start(turn)

    def start(self, turn: str | None = None):
        if turn in ("w", "b"):
            self.state.turn = turn
        if self.state.flagged:
            return
        if not self.state.running:
            self.state.running = True
            self.state.started_at_ms = _now_ms()

    def pause(self):
        if not self.state.running:
            return
        self._apply_elapsed()
        self.state.running = False
        self.state.started_at_ms = None

    def reset(self, turn: str = "w"):
        self.pause()
        self.state.w_ms = self.state.base_ms
        self.state.b_ms = self.state.base_ms
        self.state.flagged = None
        self.state.turn = turn

    # ----- events -----
    def on_move(self, moved_by: str):
        """
        Commit time for the mover, add increment, then pass turn and continue running.
        """
        if self.state.flagged:
            return
        if self.state.running:
            self._apply_elapsed()
        # add increment to the side that just moved (if they haven't flagged)
        if moved_by == "w" and self.state.w_ms > 0:
            self.state.w_ms += self.state.inc_ms
        elif moved_by == "b" and self.state.b_ms > 0:
            self.state.b_ms += self.state.inc_ms

        # pass turn & continue running
        self.state.turn = "b" if moved_by == "w" else "w"
        if not self.state.flagged:
            self.state.started_at_ms = _now_ms()
            self.state.running = True

    # ----- queries -----
    def snapshot(self) -> dict:
        """
        Current state, applying live elapsed to side-to-move if running.
        Does not mutate increment; only computes view.
        """
        s = self.state
        w_ms, b_ms = s.w_ms, s.b_ms
        if s.running and s.started_at_ms is not None and not s.flagged:
            elapsed = _now_ms() - s.started_at_ms
            if s.turn == "w":
                w_ms = max(0, w_ms - elapsed)
            else:
                b_ms = max(0, b_ms - elapsed)
        flagged = s.flagged
        # If someone just hit zero, mark flagged in view (authoritatively set on _apply_elapsed)
        if not flagged:
            if w_ms <= 0:
                flagged = "w"
            elif b_ms <= 0:
                flagged = "b"
        return {
            "base_ms": s.base_ms,
            "inc_ms": s.inc_ms,
            "w_ms": w_ms,
            "b_ms": b_ms,
            "running": s.running,
            "turn": s.turn,
            "flagged": flagged,
        }

    # ----- internals -----
    def _apply_elapsed(self):
        s = self.state
        if not s.running or s.started_at_ms is None or s.flagged:
            return
        elapsed = _now_ms() - s.started_at_ms
        if s.turn == "w":
            s.w_ms -= elapsed
            if s.w_ms <= 0:
                s.w_ms = 0
                s.flagged = "w"
        else:
            s.b_ms -= elapsed
            if s.b_ms <= 0:
                s.b_ms = 0
                s.flagged = "b"
        s.started_at_ms = _now_ms()
