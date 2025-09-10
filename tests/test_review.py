from review.reviewer import review_pgn

SCHOLARS = """
[Event "Test"]
[Site "?"]
[Date "2025.01.01"]
[Round "-"]
[White "White"]
[Black "Black"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
""".strip()


def test_review_handles_missing_engine_gracefully():
    # We can't force adapter to miss the engine from here, but if engine is missing on machine,
    # review_pgn should return ok=False and meaningful error (no crash).
    result = review_pgn(SCHOLARS, movetime_ms=50)
    # Accept both with/without engine: must not crash
    assert "ok" in result.__dict__
    assert (result.ok is True) or (result.ok is False and result.error is not None)
