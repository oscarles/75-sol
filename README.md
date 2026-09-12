# 75 SOL — dev-buy scanner pump.fun + stratégie "vente du dev"

Il écoute en direct les nouvelles créations de coins pump.fun et envoie une
alerte Discord dès qu'un coin remplit **une seule condition** :

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

Aucune autre condition côté détection. Pas de funding check, pas de scan Helius.

## Stratégie d'achat/vente "vente du dev"

Sur les coins déjà matchés ci-dessus, le bot continue de surveiller le wallet
du dev. Dès qu'il **vend**, ça déclenche un achat natif (bonding curve pump.fun) :

1. **Entrée** : 1ère vente du dev → achète 50 % d'une mise unité
   (`BASE_POSITION_SOL`, 0.15 SOL par défaut → 0.075 SOL). Si le dev revend
   pendant que la position est encore ouverte, un 2e achat de 50 % vient
   s'ajouter à la **même** position (fusion). Maximum 2 achats par coin — le
   seuil de migration pump.fun (~85 SOL réels) étant au-dessus du dev-buy max
   (78 SOL), le coin est toujours encore sur la bonding curve à ce stade.
2. **Sortie** : take-profit à **$80 000** de market cap → vend 100 % d'un coup.
   Stop-loss à **$7 000** → vente d'urgence. Une position peut migrer vers le
   PAMM (PumpSwap) pendant qu'elle est détenue — la vente gère alors les deux
   venues automatiquement.
3. **Martingale** : après une clôture en stop-loss, le **prochain coin acheté**
   voit sa taille multipliée : ×1.15 après 1 SL d'affilée, ×1.38 après 2
   (`1.15×1.20`), puis ×1.20 en cascade indéfiniment. Un take-profit casse la
   série (retour à ×1).

**Sécurité** : `DRY_RUN=true` par défaut — tout le pipeline tourne (détection,
sizing, martingale, alertes Discord) mais aucune transaction n'est envoyée.
Sans `BOT_PRIVATE_KEY` renseigné, la stratégie reste désactivée et le bot se
comporte exactement comme avant (détection seule).

Fichiers dédiés : `strategy.py` (logique), `sizing.py` /
`martingale_state.py` (sizing pur, testé dans `tests/`), `positions_store.py`
(persistance), `tx_builder.py` / `pda.py` / `trading_constants.py` (instructions
natives bonding curve V2), `pamm.py` (vente post-migration), `tx_sender.py`
(envoi/confirmation).

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
| `DRY_RUN` | `true` | `false` pour envoyer réellement les transactions (cf. stratégie ci-dessus) |
| `BOT_PRIVATE_KEY` | — | Clé privée base58 du wallet de trading — vide = stratégie désactivée |
| `RPC_HTTP` / `RPC_WS` | — | RPC dédié pour envoyer/confirmer les transactions (requis dès que `BOT_PRIVATE_KEY` est renseigné) |
| `BASE_POSITION_SOL` | `0.15` | Mise unité (100 %) de la stratégie vente-du-dev |
| `TP_MCAP_USD` / `SL_MCAP_USD` | `80000` / `7000` | Seuils de sortie en market cap |
| `DEV_SELL_WATCH_TTL_SEC` | `21600` | Durée de surveillance du dev après un match |

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
| `sol_price.py` | Prix SOL/USD (CoinGecko), cache 60s |
| `config.py` | Configuration (lit `.env`) |
| `strategy.py` | Stratégie vente-du-dev : entrée, sortie TP/SL, boucle de suivi par position |
| `sizing.py` | Sizing pur (50 %/100 %, plafond 2 achats) — testé, sans dépendance réseau |
| `martingale_state.py` | Multiplicateur après stop-loss (persisté `flags/martingale.json`) — testé |
| `positions_store.py` | Persistance JSON des positions ouvertes (`flags/positions.json`) |
| `wallet_ctx.py` | Chargement du keypair de trading (`BOT_PRIVATE_KEY`) |
| `trading_constants.py` / `pda.py` | Constantes protocole + dérivation locale des comptes pump.fun |
| `bonding_curve_state.py` | Lecture des réserves + statut de migration de la bonding curve |
| `market_cap.py` | Formules de market cap (bonding curve + PAMM) et de slippage |
| `tx_builder.py` | Construction des instructions buy/sell natives (bonding curve V2) |
| `pamm.py` | Vente native post-migration (PumpSwap/PAMM) |
| `tx_sender.py` | Envoi (RPC + Jito) et confirmation de transaction, respecte `DRY_RUN` |
