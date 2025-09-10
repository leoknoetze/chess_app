from chess_core.game import ChessGame

SCHOLARS_MATE_PGN = """
[Event "Test"]
[Site "?"]
[Date "2025.01.01"]
[Round "-"]
[White "White"]
[Black "Black"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
""".strip()


def test_set_fen_roundtrip():
    g = ChessGame()
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    assert g.set_fen(start) is True
    assert g.get_fen().startswith("rnbqkbnr/pppppppp/8/")
    bad = "not-a-fen"
    assert g.set_fen(bad) is False


def test_import_pgn_scholars_mate():
    g = ChessGame()
    assert g.import_pgn(SCHOLARS_MATE_PGN) is True
    # After Qxf7#, it should be checkmate
    assert g.board.is_checkmate() is True
    assert g.game_status() == "Checkmate"


def test_export_pgn_contains_moves():
    g = ChessGame()
    g.push_move("e2e4")
    g.push_move("e7e5")
    pgn = g.export_pgn()
    assert "1." in pgn
    assert "e4" in pgn and "e5" in pgn
