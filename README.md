# 75 SOL — dev-buy scanner pump.fun

Bot de **détection seule** (aucun achat). Il écoute en direct les nouvelles
créations de coins pump.fun et envoie une alerte Discord dès qu'un coin remplit
**une seule condition** :

> Le **dev** (le wallet qui crée le coin) **s'achète lui-même entre 71 et 78 SOL**,
> **instantanément** à la création — c.-à-d. dans les 3 secondes qui suivent le
> `CreateEvent` (dev-buy dans la tx de création, ou juste après).

Si le dev splitte son achat en plusieurs ordres dans cette fenêtre, ils sont
**additionnés**. Un dev qui achète moins de 71 ou plus de 78 SOL est ignoré.

## Comment ça marche

| Étape | Source | Coût |
|-------|--------|------|
| Détection des créations + des trades | WebSocket Solana `logsSubscribe` sur le programme pump.fun | **0** (aucune clé, aucun crédit) |
| Prix SOL/USD (affichage seul dans l'alerte) | CoinGecko public | 0 |

1. `CreateEvent` → le mint est mis en suivi (créateur, nom, symbole, timestamp).
2. `TradeEvent` → si c'est un **achat** signé par le **wallet créateur** reçu
   dans les `DEV_BUY_MAX_AGE_SEC` s suivant la création, le montant est cumulé.
3. **Fast-path** : si le dev-buy est dans la **tx de création** (cas le plus
   courant), on tranche et on alerte **immédiatement** — pas d'attente.
4. Sinon (dev-buy en tx séparée), on tranche à la fermeture de la fenêtre :
   total dans `[71 ; 78]` SOL → **alerte Discord**.
5. Chaque coin ne déclenche qu'une seule alerte, puis est oublié.

### Latence création on-chain → message Discord

| | WS public | WS dédié (Helius/QuickNode…) |
|---|---|---|
| Dev-buy dans la tx de création (fast-path) | ~1,5–4 s | **~0,5–1 s** |
| Dev-buy en tx séparée (fenêtre) | ~5–8 s | ~4 s |

Aucune autre condition. Pas de market cap, pas de funding, pas de scan Helius.

## Installation

```bash
cd "75 sol"
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env           # renseigner DISCORD_WEBHOOK_URL
```

## Lancement

```bash
python bot.py
```

## Réglages (`.env`)

| Variable | Défaut | Rôle |
|----------|--------|------|
| `DISCORD_WEBHOOK_URL` | — | Webhook des alertes (obligatoire) |
| `DISCORD_LOG_WEBHOOK_URL` | — | Webhook séparé pour les logs (optionnel) |
| `PUBLIC_WS_URLS` | mainnet-beta | WebSocket(s) de détection, séparés par des virgules |
| `DEV_BUY_MIN_SOL` / `DEV_BUY_MAX_SOL` | `71` / `78` | Fenêtre du dev-buy |
| `DEV_BUY_MAX_AGE_SEC` | `3` | Délai max création → achat du dev pour compter comme « instantané » |
| `DEV_BUY_REQUIRE_CREATOR_MATCH` | `true` | `true` = ne compter que les achats du wallet créateur ; `false` = prendre le 1er acheteur du coin |
| `DEV_BUY_FAST_PATH` | `true` | Alerte immédiate si le dev-buy est dans la tx de création (sans attendre la fenêtre) |
| `DEV_BUY_EPSILON_SOL` | `0.01` | Tolérance sur les bornes |
| `TOKEN_TTL_SEC` | `60` | Durée de mémoire d'un coin |
| `EVAL_INTERVAL` | `0.5` | Secondes entre deux passes d'évaluation |
| `RECONNECT_DELAY` | `5` | Délai de reconnexion WS |

## ⚠️ Fiabilité du WebSocket

Le WS public `wss://api.mainnet-beta.solana.com` **rate-limit et coupe souvent**
le firehose pump.fun (très bruyant). En production, mets un endpoint WS dédié
dans `PUBLIC_WS_URLS` (Helius, QuickNode, Triton, Shyft…). Le format
`logsSubscribe` est identique, seul l'URL change.

## Fichiers

| Fichier | Rôle |
|---------|------|
| `bot.py` | Orchestrateur : WS + suivi des coins + évaluation + alerte |
| `events.py` | Parsing `CreateEvent` / `TradeEvent` pump.fun |
| `discord_alert.py` | Construction + envoi de l'embed d'alerte Discord |
| `discord_log.py` | Logs de fonctionnement bufferisés vers un webhook Discord séparé |
| `sol_price.py` | Prix SOL/USD (CoinGecko) — affichage seul |
| `config.py` | Configuration (lit `.env`) |
