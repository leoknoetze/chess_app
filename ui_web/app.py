# ui_web/app.py
import os
import sys
import time
import io
import datetime as dt
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

from flask import Flask, render_template, request, jsonify, Response

# ---------- ensure project root is importable ----------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# -------------------------------------------------------

# --- Flask app (explicit folders) ---
app = Flask(__name__, template_folder="templates", static_folder="static")

# --- chess core ---
import chess
import chess.pgn
from chess_core.game import ChessGame  # your existing class

# --- optional engine + review modules (fail-safe if missing) ---
try:
    from engine.stockfish_adapter import probe_engine, analyze_fen, play_fen
except Exception:

    def probe_engine(*_, **__):
        return {
            "available": False,
            "path": None,
            "version": None,
            "error": "adapter missing",
        }

    def analyze_fen(*_, **__):
        return {"ok": False, "error": "engine unavailable"}

    def play_fen(*_, **__):
        return {"ok": False, "error": "engine unavailable"}


try:
    from review.game_review import review_pgn, render_html_report
except Exception:
    # graceful stubs
    def review_pgn(*_, **__):
        class R:
            ok = False
            error = "reviewer unavailable"

        return R()

    def render_html_report(*_, **__):
        return "<div style='color:#e11d48'>Reviewer unavailable.</div>"


# ----------------- Global single-game state (simple MVP) -----------------
game = ChessGame()


# ----------------- Simple chess clocks -----------------
@dataclass
class Clocks:
    w_ms: int = 5 * 60 * 1000
    b_ms: int = 5 * 60 * 1000
    increment: int = 0  # per-move increment, ms
    running: bool = False
    turn: str = "w"  # 'w' or 'b' (whose clock is ticking)
    last_ts: Optional[float] = None  # time.time() when we last started
    flagged: bool = False

    def to_dict(self):
        return {
            "w_ms": self.w_ms,
            "b_ms": self.b_ms,
            "increment": self.increment,
            "running": self.running,
            "turn": self.turn,
            "flagged": self.flagged,
        }

    def _now(self) -> float:
        return time.time()

    def start(self):
        if self.flagged:
            return
        if not self.running:
            self.running = True
            self.last_ts = self._now()

    def pause(self):
        if self.running and self.last_ts is not None:
            elapsed = int((self._now() - self.last_ts) * 1000)
            if self.turn == "w":
                self.w_ms = max(0, self.w_ms - elapsed)
            else:
                self.b_ms = max(0, self.b_ms - elapsed)
            self.running = False
            self.last_ts = None
            self._check_flagged()

    def switch_turn(self):
        """Call after a successful move: apply elapsed + increment and flip turn."""
        # apply elapsed on current turn
        if self.running and self.last_ts is not None:
            elapsed = int((self._now() - self.last_ts) * 1000)
            if self.turn == "w":
                self.w_ms = max(0, self.w_ms - elapsed)
                if self.w_ms == 0:
                    self.flagged = True
            else:
                self.b_ms = max(0, self.b_ms - elapsed)
                if self.b_ms == 0:
                    self.flagged = True

        # add increment to the side that just moved
        if self.turn == "w":
            self.w_ms += self.increment
            self.turn = "b"
        else:
            self.b_ms += self.increment
            self.turn = "w"

        # restart timer on new turn
        if not self.flagged:
            self.last_ts = self._now()
            self.running = True

    def config(self, minutes: int, increment: int, turn: str = "w"):
        self.w_ms = minutes * 60 * 1000
        self.b_ms = minutes * 60 * 1000
        self.increment = increment * 1000
        self.turn = "w" if turn != "b" else "b"
        self.running = False
        self.last_ts = None
        self.flagged = False

    def _check_flagged(self):
        if self.w_ms <= 0 or self.b_ms <= 0:
            self.flagged = True


clocks = Clocks()  # default 5+0


# ----------------- Helpers -----------------
def flags_from_board(b: chess.Board) -> Dict[str, Any]:
    """Return UI flags used by the frontend to show check/mate/game_over etc."""
    f: Dict[str, Any] = {}
    f["check"] = b.is_check()
    f["mate"] = b.is_checkmate()
    f["game_over"] = b.is_game_over(claim_draw=True) or clocks.flagged
    f["result"] = b.result(claim_draw=True) if b.is_game_over(claim_draw=True) else None
    # find checked king square if in check
    if f["check"]:
        king_sq = b.king(b.turn)
        if king_sq is not None:
            f["check_square"] = chess.square_name(king_sq)
    return f


def san_list_from_stack(b: chess.Board) -> List[str]:
    """Return SAN strings in order from initial position."""
    temp = chess.Board()
    out: List[str] = []
    for mv in b.move_stack:
        out.append(temp.san(mv))
        temp.push(mv)
    return out


# ----------------- Routes: SPA + Board -----------------
@app.route("/")
def spa_home():
    # Splash + Main Menu SPA
    return render_template("spa.html")


@app.route("/board")
def board_screen():
    return render_template(
        "board.html", fen=game.board.fen(), status=game.game_status()
    )


# ----------------- Game actions -----------------
@app.route("/legal/<square>")
def legal(square: str):
    moves = game.legal_moves_from(square)
    return jsonify({"from": square, "legal": moves})


@app.route("/move", methods=["POST"])
def move():
    data = request.get_json(silent=True) or {}
    uci = data.get("move", "")
    promo = data.get("promotion")
    # append promotion letter if provided (e7e8q)
    if promo and len(uci) == 4:
        uci = uci + str(promo).lower()

    if clocks.flagged:
        return (
            jsonify(
                {
                    "success": False,
                    "fen": game.board.fen(),
                    "status": "Game Over (time)",
                    "flags": flags_from_board(game.board),
                    "clocks": clocks.to_dict(),
                    "error": "Game already ended on time.",
                }
            ),
            200,
        )

    ok = game.push_move(uci)
    if not ok:
        return (
            jsonify(
                {
                    "success": False,
                    "fen": game.board.fen(),
                    "status": game.game_status(),
                    "flags": flags_from_board(game.board),
                    "clocks": clocks.to_dict(),
                    "error": f"Illegal move: {uci}",
                }
            ),
            200,
        )

    # switch clocks turn after a successful move
    clocks.switch_turn()

    # if the game ended by mate/draw, pause clocks
    if game.board.is_game_over(claim_draw=True):
        clocks.pause()

    return jsonify(
        {
            "success": True,
            "fen": game.board.fen(),
            "status": game.game_status(),
            "flags": flags_from_board(game.board),
            "clocks": clocks.to_dict(),
        }
    )


@app.route("/reset", methods=["POST"])
def reset():
    global game, clocks
    game = ChessGame()
    clocks = Clocks()  # reset to default 5+0
    return jsonify(
        {
            "success": True,
            "fen": game.board.fen(),
            "status": game.game_status(),
            "flags": flags_from_board(game.board),
            "clocks": clocks.to_dict(),
        }
    )


@app.route("/state")
def state():
    return jsonify(
        {
            "fen": game.board.fen(),
            "status": game.game_status(),
            "flags": flags_from_board(game.board),
            "clocks": clocks.to_dict(),
        }
    )


# ----------------- Clocks -----------------
@app.route("/clock/config", methods=["POST"])
def clock_config():
    data = request.get_json(silent=True) or {}
    minutes = int(data.get("minutes", 5))
    increment = int(data.get("increment", 0))
    turn = data.get("turn", "w")
    clocks.config(minutes=minutes, increment=increment, turn=turn)
    return jsonify({"success": True, "clocks": clocks.to_dict()})


@app.route("/clock/start", methods=["POST"])
def clock_start():
    clocks.start()
    return jsonify({"success": True, "clocks": clocks.to_dict()})


@app.route("/clock/pause", methods=["POST"])
def clock_pause():
    clocks.pause()
    return jsonify({"success": True, "clocks": clocks.to_dict()})


# ----------------- FEN / PGN -----------------
@app.route("/set_fen", methods=["POST"])
def set_fen():
    data = request.get_json(silent=True) or {}
    fen = data.get("fen", "")
    ok = game.set_fen(fen)
    if not ok:
        return jsonify({"success": False, "error": "Invalid FEN"}), 200
    # after setting a position, pause and reset clock turn to side-to-move
    clocks.pause()
    clocks.turn = "w" if game.board.turn else "b"
    return jsonify(
        {
            "success": True,
            "fen": game.get_fen(),
            "status": game.game_status(),
            "flags": flags_from_board(game.board),
            "clocks": clocks.to_dict(),
        }
    )


@app.route("/import_pgn", methods=["POST"])
def import_pgn():
    data = request.get_json(silent=True) or {}
    pgn = data.get("pgn", "")
    ok = game.import_pgn(pgn)
    if not ok:
        return jsonify({"success": False, "error": "Invalid PGN"}), 200
    clocks.pause()
    clocks.turn = "w" if game.board.turn else "b"
    return jsonify(
        {
            "success": True,
            "fen": game.get_fen(),
            "status": game.game_status(),
            "flags": flags_from_board(game.board),
            "clocks": clocks.to_dict(),
        }
    )


@app.route("/export_pgn")
def export_pgn():
    pgn_text = game.export_pgn()
    return Response(
        pgn_text,
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment; filename=game.pgn"},
    )


@app.route("/movelist")
def movelist():
    moves = san_list_from_stack(game.board)
    # collapse into move-numbered pairs on the client; we just send a flat SAN list
    return jsonify({"success": True, "moves": moves})


# ----------------- AI (Stockfish) -----------------
@app.route("/ai/probe")
def ai_probe():
    return jsonify(probe_engine())


@app.route("/ai/suggest", methods=["POST"])
def ai_suggest():
    data = request.get_json(silent=True) or {}
    result = analyze_fen(
        fen=game.board.fen(),
        movetime_ms=int(data.get("movetime_ms", 500)),
        depth=data.get("depth"),
        skill_level=data.get("skill"),
    )
    if not result.get("ok"):
        return jsonify({"success": False, "error": result.get("error")}), 200
    return jsonify({"success": True, "suggestion": result})


@app.route("/ai/play", methods=["POST"])
def ai_play():
    data = request.get_json(silent=True) or {}
    result = play_fen(
        fen=game.board.fen(),
        movetime_ms=int(data.get("movetime_ms", 500)),
        depth=data.get("depth"),
        skill_level=data.get("skill"),
    )
    if not result.get("ok"):
        return jsonify({"success": False, "error": result.get("error")}), 200

    new_fen = result["fen"]
    # update server board and clocks
    try:
        game.board.set_fen(new_fen)
        # engine moved for the side-to-move; switch clock as a real move
        clocks.switch_turn()
        if game.board.is_game_over(claim_draw=True):
            clocks.pause()
    except Exception:
        pass

    return jsonify(
        {
            "success": True,
            "fen": new_fen,
            "ai": result,
            "flags": flags_from_board(game.board),
            "clocks": clocks.to_dict(),
        }
    )


# ----------------- Reviewer -----------------
@app.route("/review", methods=["POST"])
def review_endpoint():
    data = request.get_json(silent=True) or {}
    pgn = data.get("pgn") or game.export_pgn()
    movetime_ms = int(data.get("movetime_ms", 200))
    depth = data.get("depth")
    skill = data.get("skill")

    summary = review_pgn(
        pgn_text=pgn, movetime_ms=movetime_ms, depth=depth, skill_level=skill
    )
    if not getattr(summary, "ok", False):
        return (
            jsonify(
                {"success": False, "error": getattr(summary, "error", "review failed")}
            ),
            200,
        )
    html = render_html_report(summary, title="Game Review")
    return jsonify({"success": True, "html": html}), 200


# --------------- Dev entrypoint ---------------
if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
