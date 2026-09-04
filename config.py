"""
Configuration du bot « 75 SOL » — détecteur de dev-buy pump.fun.

Toutes les valeurs sont lisibles / surchargeables via un fichier `.env` posé
à côté de ce fichier (voir `.env.example`).
"""

import os
from pathlib import Path


def _load_env() -> None:
    """Mini-loader `.env` — aucune dépendance externe requise."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_env()


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


# ── Discord ──────────────────────────────────────────────────────────────────
# Webhook où partent les alertes « dev buy 70–80 SOL » (obligatoire).
DISCORD_WEBHOOK_URL     = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
# Webhook séparé pour les logs de fonctionnement (optionnel — vide = console seule).
DISCORD_LOG_WEBHOOK_URL = os.environ.get("DISCORD_LOG_WEBHOOK_URL", "").strip()

# ── WebSocket Solana : DÉTECTION (créations + trades) ────────────────────────
# Tout se joue ici : aucune autre source n'est nécessaire, aucun crédit RPC.
# Le WS public mainnet-beta fonctionne mais rate-limit / drop souvent le
# firehose pump.fun — un WS dédié (Helius, QuickNode, Triton…) est vivement
# recommandé en production. On peut en lister plusieurs (bascule au reconnect).
PUBLIC_WS_URLS = _list(
    "PUBLIC_WS_URLS",
    "wss://api.mainnet-beta.solana.com",
)

# ── Adresse on-chain du programme pump.fun ──────────────────────────────────
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# ─────────────────────────────────────────────────────────────────────────────
# LE FILTRE — rien d'autre.
# ─────────────────────────────────────────────────────────────────────────────
# À la création du coin, le dev (= le wallet créateur) doit acheter lui-même
# entre DEV_BUY_MIN_SOL et DEV_BUY_MAX_SOL, « instantanément », c.-à-d. dans les
# DEV_BUY_MAX_AGE_SEC secondes qui suivent le CreateEvent.
DEV_BUY_MIN_SOL     = float(os.environ.get("DEV_BUY_MIN_SOL", 70.0))
DEV_BUY_MAX_SOL     = float(os.environ.get("DEV_BUY_MAX_SOL", 80.0))
DEV_BUY_MAX_AGE_SEC = float(os.environ.get("DEV_BUY_MAX_AGE_SEC", 3.0))

# On additionne tous les achats du wallet créateur reçus dans la fenêtre
# ci-dessus (certains bots splittent le dev-buy en plusieurs ordres).
# true  → ne compter que les achats signés par le wallet créateur (recommandé).
# false → considérer le tout premier achat du coin comme le dev-buy, quel que
#         soit l'acheteur (utile si le dev achète depuis un autre wallet).
DEV_BUY_REQUIRE_CREATOR_MATCH = os.environ.get(
    "DEV_BUY_REQUIRE_CREATOR_MATCH", "true"
).strip().lower() in ("1", "true", "yes", "on")

# Tolérance en SOL sur les bornes (arrondis de lamports / frais).
DEV_BUY_EPSILON_SOL = float(os.environ.get("DEV_BUY_EPSILON_SOL", 0.01))

# Fast-path : si le dev-buy est DANS la tx de création (même signature), on
# évalue et on alerte immédiatement sans attendre la fin de la fenêtre de
# DEV_BUY_MAX_AGE_SEC. Cas le plus courant → alerte ~3 s plus tôt.
DEV_BUY_FAST_PATH = os.environ.get(
    "DEV_BUY_FAST_PATH", "true"
).strip().lower() in ("1", "true", "yes", "on")

# Skip serial deployers : si le wallet créateur a déjà créé plus de
# DEV_MAX_TOKENS_BEFORE_SKIP coin(s) depuis le démarrage du bot, on ignore ses
# créations suivantes (pas de suivi, pas d'alerte possible). Vise les devs qui
# spam plusieurs tokens (souvent des rugs en série).
DEV_SKIP_IF_MULTI_TOKEN = os.environ.get(
    "DEV_SKIP_IF_MULTI_TOKEN", "true"
).strip().lower() in ("1", "true", "yes", "on")
DEV_MAX_TOKENS_BEFORE_SKIP = int(os.environ.get("DEV_MAX_TOKENS_BEFORE_SKIP", 1))

# ── Blague : ping + spam sur l'alerte pour réveiller les distraits ─────────
# JOKE_PING_USER_ID = ID Discord numérique du pote (dev mode → clic droit →
# « Copier l'identifiant »). Renseigné → vrai ping (notif + son). Vide → le
# texte « @pilo0. » s'affiche sans notifier.
JOKE_PING_ENABLED  = os.environ.get("JOKE_PING_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
JOKE_PING_USER_ID  = os.environ.get("JOKE_PING_USER_ID", "").strip()
JOKE_PING_NAME     = os.environ.get("JOKE_PING_NAME", "@pilo0.").strip()
# GIF optionnel ajouté sous la ligne « ALERTE » (URL média directe .gif/.mp4,
# ou lien tenor.com/view/…). Vide = pas de GIF.
JOKE_GIF_URL       = os.environ.get("JOKE_GIF_URL", "").strip()

# ── Réglages de suivi (pas des conditions) ─────────────────────────────────
TOKEN_TTL_SEC = float(os.environ.get("TOKEN_TTL_SEC", 60))    # on oublie un coin après X s
MAX_TRACKED   = int(os.environ.get("MAX_TRACKED", 20000))     # garde-fou mémoire
EVAL_INTERVAL = float(os.environ.get("EVAL_INTERVAL", 0.5))   # s entre deux passes d'évaluation
RECONNECT_DELAY = float(os.environ.get("RECONNECT_DELAY", 5))
