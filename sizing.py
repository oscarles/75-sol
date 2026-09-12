"""
Sizing pur (aucune dépendance réseau/solana) pour la stratégie "vente du dev" :
- 1ère vente du dev -> 50% d'une mise unité.
- 2e vente du dev (position encore ouverte) -> encore 50%, fondu dans la MÊME
  position (buys_done cumulé, max 2 achats par coin).
- La taille de chaque achat de 50% est elle-même multipliée par le
  multiplicateur martingale verrouillé pour CETTE position (cf. martingale_state.py).

Isolé dans son propre module (pas d'import solders/solana) pour rester
testable sans les dépendances réseau/wallet.
"""

MAX_BUYS_PER_COIN = 2
BUY_FRACTION = 0.5  # 50% d'une mise unité par achat déclenché par une vente du dev


def can_buy(buys_done: int) -> bool:
    return buys_done < MAX_BUYS_PER_COIN


def buy_size_sol(base_position_sol: float, multiplier: float) -> float:
    """Taille en SOL d'UN achat de 50%, avec le multiplicateur martingale
    verrouillé pour la position déjà appliqué."""
    return base_position_sol * BUY_FRACTION * multiplier


def total_position_fraction(buys_done: int) -> float:
    """Fraction d'une mise unité (avant multiplicateur) représentée par
    `buys_done` achats de 50% (0, 0.5 ou 1.0)."""
    return min(buys_done, MAX_BUYS_PER_COIN) * BUY_FRACTION
