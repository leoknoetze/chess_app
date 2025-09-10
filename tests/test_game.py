from chess_core.game import ChessGame


def test_initial_status():
    game = ChessGame()
    assert game.game_status() == "Ongoing"


def test_legal_move():
    game = ChessGame()
    assert game.push_move("e2e4") is True


def test_illegal_move():
    game = ChessGame()
    assert game.push_move("e2e5") is False


import pytest
from chess_core.game import ChessGame


def test_start_position_status():
    game = ChessGame()
    assert game.game_status() == "Ongoing"
    # FEN should start with white to move
    assert game.board.turn is True  # True = white


def test_legal_moves_from_start():
    game = ChessGame()
    # pawns on rank 2 should be able to move
    moves = game.legal_moves_from("e2")
    assert "e3" in moves
    assert "e4" in moves
    # knight should move
    knight_moves = game.legal_moves_from("g1")
    assert set(knight_moves) == {"h3", "f3"}


def test_push_move_and_status():
    game = ChessGame()
    assert game.push_move("e2e4")  # legal
    assert not game.push_move("e2e5")  # illegal
    assert game.board.fullmove_number == 1  # still first move pair
    assert game.board.turn is False  # black to move now
