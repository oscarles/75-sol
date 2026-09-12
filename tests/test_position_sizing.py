"""Vérifie l'exemple donné par l'utilisateur : base 0.15 SOL, 1er achat (50%)
avec un bonus martingale x1.38 en cours -> 0.075 x 1.38 = 0.1035 SOL, soit 69%
d'une mise unité. Et le plafond à 2 achats par coin."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sizing import buy_size_sol, can_buy, total_position_fraction  # noqa: E402
from martingale_state import multiplier_for  # noqa: E402

BASE_POSITION_SOL = 0.15


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def test_first_buy_is_half_of_base_with_no_multiplier():
    size = buy_size_sol(BASE_POSITION_SOL, multiplier=1.0)
    assert approx(size, 0.075)


def test_user_example_69_percent_with_1_38_multiplier():
    mult = multiplier_for(2)  # 2 stop-loss d'affilée -> x1.38
    assert approx(mult, 1.38)
    size = buy_size_sol(BASE_POSITION_SOL, mult)
    assert approx(size, 0.075 * 1.38)
    assert approx(size, 0.1035)
    # 0.1035 SOL / 0.15 SOL de base = 69% d'une mise unité
    assert approx(size / BASE_POSITION_SOL, 0.69)


def test_second_buy_adds_another_half_same_multiplier():
    mult = 1.0
    buy1 = buy_size_sol(BASE_POSITION_SOL, mult)
    buy2 = buy_size_sol(BASE_POSITION_SOL, mult)
    total = buy1 + buy2
    assert approx(total, BASE_POSITION_SOL)  # 50% + 50% = 100% de la mise unité


def test_max_two_buys_per_coin():
    assert can_buy(0) is True
    assert can_buy(1) is True
    assert can_buy(2) is False
    assert can_buy(3) is False


def test_total_position_fraction():
    assert total_position_fraction(0) == 0.0
    assert approx(total_position_fraction(1), 0.5)
    assert approx(total_position_fraction(2), 1.0)


if __name__ == "__main__":
    test_first_buy_is_half_of_base_with_no_multiplier()
    test_user_example_69_percent_with_1_38_multiplier()
    test_second_buy_adds_another_half_same_multiplier()
    test_max_two_buys_per_coin()
    test_total_position_fraction()
    print("OK - tous les tests de sizing passent")
