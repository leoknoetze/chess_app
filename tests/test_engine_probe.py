from engine.stockfish_adapter import probe_engine


def test_probe_does_not_crash_without_engine():
    info = probe_engine(preferred="Z:/nope/nowhere/stockfish.exe")  # guaranteed missing
    assert (
        "available" in info and "path" in info and "version" in info and "error" in info
    )
    assert info["available"] is False  # forced-missing path → should be False
