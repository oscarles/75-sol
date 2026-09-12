"""
Persistance JSON des positions ouvertes — nécessaire pour reprendre après un
crash/redémarrage pendant qu'une position attend encore son 2e achat (vente du
dev) ou surveille son TP/SL. Fichier entier réécrit à chaque sauvegarde (peu de
positions ouvertes en même temps). Adapté de Bundle10k/positions_store.py (un
seul wallet ici, pas de notion de "legs" par wallet).
"""
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

FLAGS_DIR = Path(__file__).with_name("flags")
POSITIONS_FILE = FLAGS_DIR / "positions.json"


@dataclass
class Position:
    mint: str
    creator: str                       # wallet du dev surveillé
    token_program: str
    entry_ts: float
    venue: str = "bonding_curve"       # "bonding_curve" | "pamm"
    pamm_pool: Optional[str] = None
    buys_done: int = 0                 # 1 ou 2
    sol_invested: float = 0.0          # cumulé sur les 2 achats (SOL)
    tokens_held: int = 0               # cumulé (unités brutes, TOKEN_DECIMALS)
    multiplier: float = 1.0            # martingale verrouillé au 1er achat de cette position
    closed: bool = False
    closed_reason: Optional[str] = None   # "tp" | "sl" | "secours"


def _load_all() -> dict:
    if not POSITIONS_FILE.exists():
        return {}
    try:
        return json.loads(POSITIONS_FILE.read_text())
    except Exception:
        return {}


def _save_all(data: dict) -> None:
    FLAGS_DIR.mkdir(exist_ok=True)
    POSITIONS_FILE.write_text(json.dumps(data, indent=2))


def save(pos: Position) -> None:
    data = _load_all()
    data[pos.mint] = asdict(pos)
    _save_all(data)


def get(mint: str) -> Optional[Position]:
    data = _load_all()
    v = data.get(mint)
    return Position(**v) if v else None


def load_open() -> list[Position]:
    data = _load_all()
    return [Position(**v) for v in data.values() if not v.get("closed")]


def close(mint: str, reason: str) -> None:
    data = _load_all()
    if mint in data:
        data[mint]["closed"] = True
        data[mint]["closed_reason"] = reason
        _save_all(data)


def is_closed(mint: str) -> bool:
    """True seulement si le mint existe dans le store ET est marqué clôturé. Un
    store illisible/vide -> False : on préfère continuer à surveiller plutôt
    qu'abandonner une position sur un accès disque raté."""
    data = _load_all()
    return bool(data.get(mint, {}).get("closed", False))
