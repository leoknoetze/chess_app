import io
import datetime as dt
import chess
import chess.pgn


class ChessGame:
    """Core chess game logic using python-chess."""

    def __init__(self):
        self.board = chess.Board()

    # ----------------------- Selection helpers -----------------------
    def legal_moves_from(self, square_name: str) -> list[str]:
        """Return a list of legal destination squares from a given source square."""
        try:
            from_square = chess.parse_square(square_name)
        except ValueError:
            return []
        moves = []
        for mv in self.board.legal_moves:
            if mv.from_square == from_square:
                moves.append(chess.square_name(mv.to_square))
        return moves

    # ----------------------- Core move API -----------------------
    def is_move_legal(self, move_uci: str) -> bool:
        try:
            move = chess.Move.from_uci(move_uci)
        except ValueError:
            return False
        return move in self.board.legal_moves

    def push_move(self, move_uci: str) -> bool:
        """Apply a legal move in UCI. Disallow moves after game over."""
        if self.board.is_game_over():
            return False
        if self.is_move_legal(move_uci):
            self.board.push(chess.Move.from_uci(move_uci))
            return True
        return False

    def san_move_list(self) -> list[str]:
        """Return SAN strings for all moves in the current game (from the start)."""
        temp = chess.Board()
        sans = []
        for mv in self.board.move_stack:
            sans.append(temp.san(mv))
            temp.push(mv)
        return sans

    # ----------------------- Status / Flags -----------------------
    def status_flags(self) -> dict:
        """
        Machine-readable flags for the frontend:
          - check: bool
          - mate: bool
          - game_over: bool
          - turn: "w" | "b"
          - check_square: "e1"/"e8"/None (king in check)
          - result: "1-0"/"0-1"/"1/2-1/2"/None
        """
        in_check = self.board.is_check()
        in_mate = self.board.is_checkmate()
        over = self.board.is_game_over()
        turn_white = self.board.turn
        check_square = None
        if in_check:
            ks = self.board.king(chess.WHITE if turn_white else chess.BLACK)
            if ks is not None:
                check_square = chess.square_name(ks)
        return {
            "check": in_check,
            "mate": in_mate,
            "game_over": over,
            "turn": "w" if turn_white else "b",
            "check_square": check_square,
            "result": self.board.result(claim_draw=True) if over else None,
        }

    def game_status(self) -> str:
        """Human-readable status line."""
        if self.board.is_checkmate():
            return "Checkmate"
        if self.board.is_stalemate():
            return "Stalemate"
        if self.board.is_insufficient_material():
            return "Draw by insufficient material"
        if self.board.is_seventyfive_moves() or self.board.is_fivefold_repetition():
            return "Draw by rule"
        if self.board.is_game_over():
            return "Game Over"
        if self.board.is_check():
            return "Check"
        return "Ongoing"

    # --------- Rendering helper (legacy, still useful for tests) ----------
    def get_board_unicode(self):
        """Return board as 2D list of Unicode chess pieces for rendering."""
        piece_unicode = {
            "P": "♙",
            "N": "♘",
            "B": "♗",
            "R": "♖",
            "Q": "♕",
            "K": "♔",
            "p": "♟",
            "n": "♞",
            "b": "♝",
            "r": "♜",
            "q": "♛",
            "k": "♚",
        }
        board_rows = []
        for rank in range(8, 0, -1):
            row = []
            for file in range(1, 9):
                square = chess.square(file - 1, rank - 1)
                piece = self.board.piece_at(square)
                symbol = piece_unicode.get(piece.symbol(), " ") if piece else " "
                row.append(symbol)
            board_rows.append(row)
        return board_rows

    # ----------------------- FEN / PGN -----------------------
    def get_fen(self) -> str:
        return self.board.fen()

    def set_fen(self, fen: str) -> bool:
        try:
            self.board.set_fen(fen)
            return True
        except Exception:
            return False

    def import_pgn(self, pgn_text: str) -> bool:
        """
        Load a PGN (e.g., exported from chess.com). Replaces current board with the PGN's final position.
        Returns True on success.
        """
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_text))
            if game is None:
                return False
            board = game.board()
            for mv in game.mainline_moves():
                board.push(mv)
            self.board = board
            return True
        except Exception:
            return False

    def export_pgn(self) -> str:
        """
        Build a PGN from the current move stack. If the game is over, include the result header; otherwise '*'.
        """
        game = chess.pgn.Game()
        game.headers["Event"] = "Local Game"
        game.headers["Site"] = "chess_app"
        game.headers["Date"] = dt.date.today().strftime("%Y.%m.%d")
        game.headers["White"] = game.headers.get("White", "White")
        game.headers["Black"] = game.headers.get("Black", "Black")
        game.headers["Result"] = (
            self.board.result(claim_draw=True)
            if self.board.is_game_over(claim_draw=True)
            else "*"
        )

        node = game
        temp = chess.Board()
        for mv in self.board.move_stack:
            node = node.add_variation(mv)
            temp.push(mv)

        out = io.StringIO()
        exporter = chess.pgn.StringExporter(
            headers=True, variations=False, comments=False
        )
        out.write(game.accept(exporter))
        return out.getvalue()
