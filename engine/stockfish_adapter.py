import os
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List

import chess
import chess.engine


def _pv_to_san_safe(board: chess.Board, pv_moves) -> list[str]:
    """
    Convert a PV (list of moves in either python-chess Move objects or UCI strings)
    into SAN moves safely. If a PV move is illegal for the current position,
    we stop and return whatever we have so far.
    """
    tmp = board.copy()
    san_list = []
    for mv in pv_moves:
        try:
            # Support both Move objects and UCI strings
            move = mv if isinstance(mv, chess.Move) else chess.Move.from_uci(str(mv))
            if move not in tmp.legal_moves:
                break
            san_list.append(tmp.san(move))
            tmp.push(move)
        except Exception:
            # Any parsing or SAN issue: stop converting PV (don’t crash analysis)
            break
    return san_list


def find_engine_path(
    preferred: Optional[str] = None, strict: bool = False
) -> Optional[str]:
    """
    Try to find a Stockfish engine binary.

    If 'preferred' is provided:
      - if it exists, use it
      - if it does NOT exist and strict=True, return None (no fallback)
      - if it does NOT exist and strict=False, continue searching elsewhere

    Otherwise, try:
      1) env var STOCKFISH_PATH
      2) common relative locations under project (./engines, ./engine, ./bin)
      3) PATH via shutil.which("stockfish")
    """
    # 1) preferred
    if preferred:
        if Path(preferred).exists():
            return preferred
        if strict:
            return None

    # 2) env var
    env_path = os.getenv("STOCKFISH_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    # 3) common relative dirs
    here = Path(__file__).resolve().parents[1]  # project root (chess_app)
    exe_names = ["stockfish.exe", "stockfish", "stockfish-windows-x86-64-avx2.exe"]
    for d in ["engines", "engine", "bin"]:
        for name in exe_names:
            p = here / d / name
            if p.exists():
                return str(p)

    # 4) PATH
    which = shutil.which("stockfish")
    if which:
        return which

    return None


def probe_engine(preferred: Optional[str] = None) -> Dict[str, Any]:
    """
    Check if we can launch Stockfish. Never raises; returns:
      { available: bool, path: str|None, version: str|None, error: str|None }

    If 'preferred' is provided and missing, do NOT fall back elsewhere.
    """
    path = find_engine_path(preferred=preferred, strict=bool(preferred))
    if not path:
        return {
            "available": False,
            "path": None,
            "version": None,
            "error": "Stockfish not found",
        }

    try:
        with chess.engine.SimpleEngine.popen_uci(path) as eng:
            info = eng.id
            version = (
                f"{info.get('name', 'Stockfish')} {info.get('version', '')}".strip()
            )
            return {"available": True, "path": path, "version": version, "error": None}
    except Exception as e:
        return {"available": False, "path": path, "version": None, "error": str(e)}


def _score_to_dict(score: chess.engine.PovScore, board: chess.Board) -> Dict[str, Any]:
    s = score.pov(board.turn)
    if s.is_mate():
        return {"type": "mate", "value": s.mate()}
    # centipawns (may be None)
    cp = s.score()
    return {"type": "cp", "value": cp}


def analyze_fen(
    fen: str,
    movetime_ms: int = 300,
    depth: int | None = None,
    skill_level: int | None = None,
) -> dict:
    path = find_engine_path()
    if not path:
        return {"ok": False, "error": "Stockfish not found"}

    try:
        board = chess.Board(fen)
    except Exception as e:
        return {"ok": False, "error": f"Bad FEN: {e}"}

    try:
        with chess.engine.SimpleEngine.popen_uci(path) as eng:
            if skill_level is not None:
                try:
                    eng.configure({"Skill Level": int(skill_level)})
                except Exception:
                    pass

            limit = (
                chess.engine.Limit(time=movetime_ms / 1000.0)
                if movetime_ms
                else chess.engine.Limit(depth=depth or 12)
            )
            info = eng.analyse(board, limit)

            # Bestmove via a short go
            best = eng.play(
                board, chess.engine.Limit(time=min(0.05, (movetime_ms or 300) / 1000.0))
            )
            bestmove = best.move.uci() if best and best.move else None

            # Eval (pov to side-to-move already by python-chess)
            eval_cp = None
            eval_mate = None
            if "score" in info:
                score = info["score"].pov(board.turn)
                if score.is_mate():
                    eval_mate = score.mate()
                else:
                    eval_cp = score.score(mate_score=32000)

            # PV handling (python-chess returns a list of Moves under "pv"; fallback if absent)
            pv_moves = info.get("pv", [])
            pv_san = _pv_to_san_safe(board, pv_moves)

            return {
                "ok": True,
                "engine": {
                    "path": path,
                    "version": getattr(eng, "id", {}).get("name", "Stockfish"),
                },
                "bestmove": bestmove,
                "eval": (
                    {"type": "mate", "value": eval_mate}
                    if eval_mate is not None
                    else {"type": "cp", "value": eval_cp}
                ),
                "pv_san": pv_san,
            }
    except Exception as e:
        # Never crash UI: return clean error
        return {"ok": False, "error": str(e)}


def play_fen(
    fen: str,
    movetime_ms: int = 300,
    depth: int | None = None,
    skill_level: int | None = None,
) -> dict:
    path = find_engine_path()
    if not path:
        return {"ok": False, "error": "Stockfish not found"}

    try:
        board = chess.Board(fen)
    except Exception as e:
        return {"ok": False, "error": f"Bad FEN: {e}"}

    try:
        with chess.engine.SimpleEngine.popen_uci(path) as eng:
            if skill_level is not None:
                try:
                    eng.configure({"Skill Level": int(skill_level)})
                except Exception:
                    pass

            limit = (
                chess.engine.Limit(time=movetime_ms / 1000.0)
                if movetime_ms
                else chess.engine.Limit(depth=depth or 12)
            )
            result = eng.play(board, limit)
            if not result or not result.move:
                return {"ok": False, "error": "Engine did not return a move"}

            # Guard: ensure engine’s move is legal
            if result.move not in board.legal_moves:
                return {
                    "ok": False,
                    "error": f"Engine move illegal in this position: {result.move.uci()}",
                }

            # Make the move
            board.push(result.move)

            # Optional brief analysis after the move for PV/eval
            info = eng.analyse(
                board, chess.engine.Limit(time=min(0.05, (movetime_ms or 300) / 1000.0))
            )
            eval_cp = None
            eval_mate = None
            if "score" in info:
                score = info["score"].pov(board.turn)
                if score.is_mate():
                    eval_mate = score.mate()
                else:
                    eval_cp = score.score(mate_score=32000)
            pv_san = _pv_to_san_safe(board, info.get("pv", []))

            return {
                "ok": True,
                "move": result.move.uci(),
                "fen": board.fen(),
                "analysis": {
                    "engine": {
                        "path": path,
                        "version": getattr(eng, "id", {}).get("name", "Stockfish"),
                    },
                    "eval": (
                        {"type": "mate", "value": eval_mate}
                        if eval_mate is not None
                        else {"type": "cp", "value": eval_cp}
                    ),
                    "pv_san": pv_san,
                },
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}
