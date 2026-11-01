from pathlib import Path


def test_shared_tick_policy():
    root = Path(__file__).parents[1]
    for relative in ("core/main.py", "backtest/backtest_rebalance.py"):
        source = (root / relative).read_text(encoding="utf-8")
        assert "select_tick_actions" in source
        assert "def select_tick_actions" not in source


if __name__ == "__main__":
    test_shared_tick_policy()
    print("PASS: shared tick policy wiring")
