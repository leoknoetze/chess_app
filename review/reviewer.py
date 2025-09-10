# review/reviewer.py
from __future__ import annotations
import io
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Tuple

import chess
import chess.pgn

from engine.stockfish_adapter import analyze_fen


# ---- Tunable thresholds (centipawns) ----
THRESHOLDS = {
    "inaccuracy": 50,  # cp loss ≥ 50
    "mistake": 100,  # cp loss ≥ 100
    "blunder": 300,  # cp loss ≥ 300
}


@dataclass
class MoveReview:
    ply: int  # 1-based ply index
    move_uci: str
    move_san: str
    side: str  # "White" or "Black"
    cp_loss: Optional[int]  # None if mate scenario / unknown
    verdict: str  # "Best/Good/Inaccuracy/Mistake/Blunder/Mate"
    eval_before: Optional[Dict[str, Any]]
    eval_after: Optional[Dict[str, Any]]
    bestmove_uci: Optional[str]
    pv_san: List[str]


@dataclass
class ReviewSummary:
    moves: List[MoveReview]
    counts: Dict[str, int]
    accuracy_percent: Optional[float]
    engine: Optional[str]
    ok: bool
    error: Optional[str]


def _cp_from_eval(eval_obj: Optional[Dict[str, Any]]) -> Optional[int]:
    if not eval_obj:
        return None
    if eval_obj.get("type") == "cp":
        return eval_obj.get("value")
    # treat mate as None here; handled specially
    return None


def _classify(
    cp_loss: Optional[int],
    before_eval: Optional[Dict[str, Any]],
    after_eval: Optional[Dict[str, Any]],
) -> str:
    # If there’s a mate score involved, call it "Mate" (user can infer win/loss from sign)
    if (before_eval and before_eval.get("type") == "mate") or (
        after_eval and after_eval.get("type") == "mate"
    ):
        return "Mate"
    if cp_loss is None:
        return "Good"
    loss = abs(cp_loss)
    if loss >= THRESHOLDS["blunder"]:
        return "Blunder"
    if loss >= THRESHOLDS["mistake"]:
        return "Mistake"
    if loss >= THRESHOLDS["inaccuracy"]:
        return "Inaccuracy"
    return "Good"


def _score_from_cp_loss(cp_loss: Optional[int]) -> float:
    """
    Convert cp loss to 0..1 score for accuracy.
    Simple piecewise:
      0 -> 1.0
      50 -> 0.9
      100 -> 0.8
      200 -> 0.65
      300 -> 0.5
      500+ -> 0.2
    """
    if cp_loss is None:
        return 0.9
    x = abs(cp_loss)
    if x <= 0:
        return 1.0
    if x <= 50:
        return 0.9
    if x <= 100:
        return 0.8
    if x <= 200:
        return 0.65
    if x <= 300:
        return 0.5
    if x <= 500:
        return 0.3
    return 0.2


def _pov_cp(score: Optional[int], side_to_move_is_white: bool) -> Optional[int]:
    """
    Ensure eval is from side-to-move POV in cp. Our adapter already povs by board.turn,
    but we keep this helper for clarity/extensibility.
    """
    return score


def review_pgn(
    pgn_text: str,
    movetime_ms: int = 200,
    depth: Optional[int] = None,
    skill_level: Optional[int] = None,
) -> ReviewSummary:
    # Parse PGN
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return ReviewSummary([], {}, None, None, ok=False, error="Could not parse PGN")

    board = game.board()
    moves_data: List[MoveReview] = []
    engine_name: Optional[str] = None

    ply = 0
    for mv in game.mainline_moves():
        ply += 1
        side = "White" if board.turn else "Black"

        # Analyze BEFORE the move
        a_before = analyze_fen(
            fen=board.fen(),
            movetime_ms=movetime_ms,
            depth=depth,
            skill_level=skill_level,
        )
        if not a_before.get("ok"):
            # Engine unavailable or error -> graceful return with partial/no data
            return ReviewSummary(
                moves=[],
                counts={},
                accuracy_percent=None,
                engine=None,
                ok=False,
                error=a_before.get("error") or "Engine unavailable",
            )
        engine_name = a_before["engine"]["version"]
        eval_before = a_before.get("eval")
        best_before_uci = a_before.get("bestmove")
        pv_san_before = a_before.get("pv_san", [])

        # Apply actual move
        san = board.san(mv)
        board.push(mv)

        # Analyze AFTER the move
        a_after = analyze_fen(
            fen=board.fen(),
            movetime_ms=movetime_ms,
            depth=depth,
            skill_level=skill_level,
        )
        if not a_after.get("ok"):
            return ReviewSummary(
                moves=[],
                counts={},
                accuracy_percent=None,
                engine=None,
                ok=False,
                error=a_after.get("error") or "Engine unavailable",
            )
        eval_after = a_after.get("eval")

        # Compute cp loss from mover's perspective:
        # before: eval from mover POV at pre-move
        # after:  eval is from side-to-move (opponent) POV; invert to mover POV
        cp_before = _pov_cp(
            _cp_from_eval(eval_before), side_to_move_is_white=(side == "White")
        )
        cp_after_raw = _cp_from_eval(eval_after)
        cp_after_for_mover = -cp_after_raw if cp_after_raw is not None else None

        cp_loss: Optional[int] = None
        if (cp_before is not None) and (cp_after_for_mover is not None):
            cp_loss = cp_before - cp_after_for_mover

        verdict = _classify(cp_loss, eval_before, eval_after)

        moves_data.append(
            MoveReview(
                ply=ply,
                move_uci=mv.uci(),
                move_san=san,
                side=side,
                cp_loss=cp_loss,
                verdict=verdict,
                eval_before=eval_before,
                eval_after=eval_after,
                bestmove_uci=best_before_uci,
                pv_san=pv_san_before,
            )
        )

    # Summaries
    counts = {
        "Best": 0,
        "Good": 0,
        "Inaccuracy": 0,
        "Mistake": 0,
        "Blunder": 0,
        "Mate": 0,
    }
    # We label "Good" for mild cp losses; "Best" if cp_loss ~ 0 and move==engine best.
    for m in moves_data:
        if m.verdict == "Mate":
            counts["Mate"] += 1
        elif (
            m.cp_loss is not None
            and abs(m.cp_loss) <= 10
            and m.bestmove_uci == m.move_uci
        ):
            counts["Best"] += 1
        else:
            counts[m.verdict] += 1 if m.verdict in counts else 0

    # Accuracy: average of side-to-move scores
    if moves_data:
        scores = [_score_from_cp_loss(m.cp_loss) for m in moves_data]
        accuracy = round(sum(scores) / len(scores) * 100.0, 1)
    else:
        accuracy = None

    return ReviewSummary(
        moves=moves_data,
        counts=counts,
        accuracy_percent=accuracy,
        engine=engine_name,
        ok=True,
        error=None,
    )


def render_html_report(summary: ReviewSummary, title: str = "Game Review") -> str:
    if not summary.ok:
        return f"<h3>{title}</h3><p style='color:red'>Error: {summary.error}</p>"

    rows = []
    for m in summary.moves:
        eval_b = m.eval_before
        eval_a = m.eval_after

        def fmt_eval(e):
            if not e:
                return ""
            if e["type"] == "mate":
                return f"Mate in {e['value']}"
            if e["type"] == "cp" and e["value"] is not None:
                return f"{e['value']/100:.2f}"
            return ""

        rows.append(
            f"<tr>"
            f"<td>{m.ply}</td>"
            f"<td>{m.side}</td>"
            f"<td><code>{m.move_san}</code> <small>({m.move_uci})</small></td>"
            f"<td>{fmt_eval(eval_b)}</td>"
            f"<td>{fmt_eval(eval_a)}</td>"
            f"<td>{'' if m.cp_loss is None else m.cp_loss}</td>"
            f"<td><b>{m.verdict}</b></td>"
            f"<td><small>{' '.join(m.pv_san[:6])}</small></td>"
            f"</tr>"
        )
    counts_str = " • ".join([f"{k}: {v}" for k, v in summary.counts.items()])
    engine_str = summary.engine or "(unknown engine)"
    acc_str = (
        f"{summary.accuracy_percent:.1f}%"
        if summary.accuracy_percent is not None
        else "N/A"
    )

    return f"""
    <div style="max-width:900px">
      <h3>{title}</h3>
      <p><b>Engine:</b> {engine_str} &nbsp; | &nbsp; <b>Accuracy:</b> {acc_str}</p>
      <p><b>Counts:</b> {counts_str}</p>
      <div style="overflow:auto">
        <table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse; width:100%">
          <thead>
            <tr>
              <th>Ply</th><th>Side</th><th>Move</th>
              <th>Eval Before</th><th>Eval After</th><th>CP Loss</th><th>Verdict</th><th>Best Line (PV)</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows)}
          </tbody>
        </table>
      </div>
    </div>
    """
