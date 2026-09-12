"""Vérifie la cascade exacte du multiplicateur martingale (spec utilisateur) :
sans I/O disque, via martingale_state.multiplier_for (pure)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from martingale_state import multiplier_for  # noqa: E402


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def test_no_streak_is_1x():
    assert multiplier_for(0) == 1.0


def test_first_stop_loss_is_1_15x():
    assert approx(multiplier_for(1), 1.15)


def test_second_stop_loss_is_1_38x_cumulative():
    assert approx(multiplier_for(2), 1.15 * 1.20)
    assert approx(multiplier_for(2), 1.38)


def test_third_and_beyond_keep_cascading_by_1_20():
    assert approx(multiplier_for(3), 1.15 * 1.20 * 1.20)
    assert approx(multiplier_for(3), 1.656)
    assert approx(multiplier_for(4), 1.15 * 1.20 * 1.20 * 1.20)
    assert approx(multiplier_for(4), 1.9872)


def test_take_profit_resets_series():
    import martingale_state as m

    # Simule 2 stop-loss d'affilée puis un take-profit, sur un fichier isolé.
    m.STATE_FILE = Path(__file__).with_name("_tmp_martingale_test.json")
    try:
        if m.STATE_FILE.exists():
            m.STATE_FILE.unlink()
        m.on_position_closed(hit_stop_loss=True)
        m.on_position_closed(hit_stop_loss=True)
        assert approx(m.current_multiplier(), 1.38)
        m.on_position_closed(hit_stop_loss=False)  # take-profit -> reset
        assert m.current_multiplier() == 1.0
    finally:
        if m.STATE_FILE.exists():
            m.STATE_FILE.unlink()


if __name__ == "__main__":
    test_no_streak_is_1x()
    test_first_stop_loss_is_1_15x()
    test_second_stop_loss_is_1_38x_cumulative()
    test_third_and_beyond_keep_cascading_by_1_20()
    test_take_profit_resets_series()
    print("OK - tous les tests martingale passent")
