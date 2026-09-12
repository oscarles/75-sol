"""
Multiplicateur de taille de position après une série de stop-loss d'affilée.

Règle (spécifiée par l'utilisateur) :
  - base = 1.0 tant qu'aucun stop-loss n'est en cours de série.
  - après 1 stop-loss d'affilée -> x1.15.
  - après 2 -> x1.15 x1.20 = x1.38 (cumulatif, pas x1.20 tout seul).
  - après 3 et plus -> continue de cumuler x1.20 à chaque stop-loss
    supplémentaire, indéfiniment (x1.656, x1.9872, ...).
  - un take-profit (même partiel) casse la série -> retour à x1.0.

Persisté dans flags/martingale.json, relu à chaque appel (pas de cache mémoire)
pour ne jamais diverger entre plusieurs process ou après un redémarrage — même
pattern que positions_store.py.
"""
import json
from pathlib import Path

FLAGS_DIR = Path(__file__).with_name("flags")
STATE_FILE = FLAGS_DIR / "martingale.json"

FIRST_STEP = 1.15
STEP = 1.20


def _read() -> dict:
    if not STATE_FILE.exists():
        return {"consecutive_stop_losses": 0}
    try:
        data = json.loads(STATE_FILE.read_text())
        data.setdefault("consecutive_stop_losses", 0)
        return data
    except Exception:
        return {"consecutive_stop_losses": 0}


def _write(data: dict) -> None:
    FLAGS_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2))


def multiplier_for(consecutive_stop_losses: int) -> float:
    """Calcule le multiplicateur pour N stop-loss d'affilée déjà comptabilisés
    (pure fonction, sans I/O — utilisée aussi par les tests)."""
    if consecutive_stop_losses <= 0:
        return 1.0
    mult = FIRST_STEP
    for _ in range(consecutive_stop_losses - 1):
        mult *= STEP
    return mult


def current_multiplier() -> float:
    return multiplier_for(_read()["consecutive_stop_losses"])


def on_position_closed(hit_stop_loss: bool) -> None:
    data = _read()
    data["consecutive_stop_losses"] = (
        data["consecutive_stop_losses"] + 1 if hit_stop_loss else 0
    )
    _write(data)
