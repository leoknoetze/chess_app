# ui_web/app.py
import os, sys
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response

# --- import path ---
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from chess_core.game import ChessGame
from chess_core.clocks import ChessClocks
from engine.stockfish_adapter import analyze_fen, play_fen, probe_engine
from review.reviewer import review_pgn, render_html_report
from multiplayer.manager import GameManager
from storage.store import save_room_state

from flask_socketio import SocketIO, join_room, leave_room, emit

HERE = Path(__file__).parent
TEMPLATES = HERE / "templates"

app = Flask(__name__, template_folder=str(TEMPLATES))
app.config["SECRET_KEY"] = "dev-secret"  # for Socket.IO
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# Socket.IO (no eventlet needed)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Single-player state stays for local play
game = ChessGame()
clocks = ChessClocks(base_ms=5 * 60_000, inc_ms=0, turn="w")

# Multiplayer manager
GM = GameManager()


# ---------- Pages ----------
@app.route("/")
def index():
    return render_template(
        "board.html", fen=game.board.fen(), status=game.game_status()
    )


@app.route("/multi")
def multi():
    # Simple lobby + shared board
    return render_template("multi.html")


# ---------- Single-player actions ----------
@app.route("/move", methods=["POST"])
def move():
    data = request.get_json(silent=True) or {}
    uci = data.get("move", "")
    promo = data.get("promotion")
    if promo and len(uci) == 4:
        uci += promo.lower()

    if game.board.is_game_over() or clocks.snapshot().get("flagged"):
        return jsonify(
            {
                "success": False,
                "fen": game.board.fen(),
                "status": game.game_status(),
                "flags": game.status_flags(),
                "clocks": clocks.snapshot(),
                "error": "Game is over",
            }
        )

    side = "w" if game.board.turn else "b"
    ok = game.push_move(uci)
    if not ok:
        return jsonify(
            {
                "success": False,
                "fen": game.board.fen(),
                "status": game.game_status(),
                "flags": game.status_flags(),
                "clocks": clocks.snapshot(),
                "error": f"Illegal move: {uci}",
            }
        )

    clocks.on_move(moved_by=side)
    snap = clocks.snapshot()
    if snap.get("flagged"):
        result = "0-1" if snap["flagged"] == "w" else "1-0"
        return jsonify(
            {
                "success": True,
                "fen": game.board.fen(),
                "status": "Game Over (time)",
                "flags": {**game.status_flags(), "game_over": True, "result": result},
                "clocks": snap,
            }
        )

    return jsonify(
        {
            "success": True,
            "fen": game.board.fen(),
            "status": game.game_status(),
            "flags": game.status_flags(),
            "clocks": snap,
        }
    )


@app.route("/legal/<square>")
def legal(square: str):
    return jsonify({"from": square, "legal": game.legal_moves_from(square)})


@app.route("/reset", methods=["POST"])
def reset():
    game.board.reset()
    clocks.reset(turn="w")
    return jsonify(
        {
            "success": True,
            "fen": game.board.fen(),
            "status": game.game_status(),
            "flags": game.status_flags(),
            "clocks": clocks.snapshot(),
        }
    )


@app.route("/state")
def state():
    return jsonify(
        {
            "fen": game.board.fen(),
            "status": game.game_status(),
            "flags": game.status_flags(),
            "clocks": clocks.snapshot(),
        }
    )


# ---------- FEN/PGN ----------
@app.route("/set_fen", methods=["POST"])
def set_fen():
    data = request.get_json(silent=True) or {}
    fen = data.get("fen", "")
    ok = game.set_fen(fen)
    if not ok:
        return jsonify({"success": False, "error": "Invalid FEN"}), 200
    return jsonify(
        {
            "success": True,
            "fen": game.get_fen(),
            "status": game.game_status(),
            "flags": game.status_flags(),
        }
    )


@app.route("/import_pgn", methods=["POST"])
def import_pgn():
    data = request.get_json(silent=True) or {}
    pgn = data.get("pgn", "")
    ok = game.import_pgn(pgn)
    if not ok:
        return jsonify({"success": False, "error": "Invalid PGN"}), 200
    return jsonify(
        {
            "success": True,
            "fen": game.get_fen(),
            "status": game.game_status(),
            "flags": game.status_flags(),
        }
    )


@app.route("/export_pgn", methods=["GET"])
def export_pgn():
    pgn_text = game.export_pgn()
    return Response(
        pgn_text,
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment; filename=game.pgn"},
    )


# ---------- AI ----------
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
    game.board.set_fen(new_fen)
    return jsonify({"success": True, "fen": new_fen, "ai": result})


# ui_web/app.py  (add this route)
@app.route("/movelist")
def movelist():
    try:
        return jsonify({"success": True, "moves": game.san_move_list()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 200


# ---------- Reviewer ----------
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
    if not summary.ok:
        return jsonify({"success": False, "error": summary.error}), 200
    html = render_html_report(summary, title="Game Review")
    return jsonify({"success": True, "html": html}), 200


# ---------- Multiplayer (Socket.IO) ----------
@socketio.on("create", namespace="/mp")
def mp_create(data):
    minutes = int(data.get("minutes", 5))
    inc = int(data.get("increment", 0))
    code = GM.create_room(minutes=minutes, inc=inc)
    join_room(code)
    # return your seat (first joiner is white)
    state = GM.join(code, request.sid)
    emit("created", {"room": code, "color": state["color"], "state": state["state"]})


@socketio.on("join", namespace="/mp")
def mp_join(data):
    code = str(data.get("room", "")).upper()
    state = GM.join(code, request.sid)
    if not state["ok"]:
        emit("error", {"error": state["error"]})
        return
    join_room(code)
    emit("joined", {"room": code, "color": state["color"], "state": state["state"]})
    # tell others someone joined
    emit("seats", {"players": list(GM.get(code).players.items())}, room=code)


@socketio.on("start", namespace="/mp")
def mp_start(data):
    code = str(data.get("room", "")).upper()
    r = GM.get(code)
    if not r:
        emit("error", {"error": "Room not found"})
        return
    r.clocks.start(turn="w")
    st = r.to_state()
    save_room_state(code, st)
    emit("state", {"room": code, **st}, room=code)


@socketio.on("pause", namespace="/mp")
def mp_pause(data):
    code = str(data.get("room", "")).upper()
    r = GM.get(code)
    if not r:
        emit("error", {"error": "Room not found"})
        return
    r.clocks.pause()
    st = r.to_state()
    save_room_state(code, st)
    emit("state", {"room": code, **st}, room=code)


@socketio.on("move", namespace="/mp")
def mp_move(data):
    code = str(data.get("room", "")).upper()
    uci = data.get("move", "")
    promo = data.get("promotion")
    r = GM.get(code)
    if not r:
        emit("error", {"error": "Room not found"})
        return

    if promo and len(uci) == 4:
        uci += promo.lower()

    # if over, reject
    if r.game.board.is_game_over() or r.clocks.snapshot().get("flagged"):
        st = r.to_state()
        emit("state", {"room": code, **st})
        return

    side = "w" if r.game.board.turn else "b"
    ok = r.game.push_move(uci)
    if not ok:
        emit("illegal", {"move": uci})
        return

    r.clocks.on_move(moved_by=side)
    st = r.to_state()
    save_room_state(code, st)
    emit("state", {"room": code, **st}, room=code)


@socketio.on("reset", namespace="/mp")
def mp_reset(data):
    code = str(data.get("room", "")).upper()
    r = GM.get(code)
    if not r:
        emit("error", {"error": "Room not found"})
        return
    r.game.board.reset()
    r.clocks.reset(turn="w")
    st = r.to_state()
    save_room_state(code, st)
    emit("state", {"room": code, **st}, room=code)


@socketio.on("sync", namespace="/mp")
def mp_sync(data):
    code = str(data.get("room", "")).upper()
    r = GM.get(code)
    if not r:
        emit("error", {"error": "Room not found"})
        return
    st = r.to_state()
    emit("state", {"room": code, **st})


@socketio.on("disconnect", namespace="/mp")
def mp_disconnect():
    # We don't delete rooms; just free the seat
    for code, room in GM.rooms.items():
        if request.sid in room.spectators or request.sid in room.players.values():
            room.spectators.discard(request.sid)
            for c in ("w", "b"):
                if room.players[c] == request.sid:
                    room.players[c] = None
            break


# ---------- Debug ----------
@app.route("/_debug")
def _debug():
    return {
        "template_folder": app.template_folder,
        "cwd": os.getcwd(),
        "fen": game.board.fen(),
        "status": game.game_status(),
        "flags": game.status_flags(),
    }


if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=5000)
