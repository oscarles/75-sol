"""
75 SOL — dev-buy scanner pump.fun
=================================

Un seul but : repérer, en direct, les nouveaux coins pump.fun où **le dev
(= le wallet créateur) s'achète lui-même entre 71 et 78 SOL instantanément à la
création**, et envoyer une alerte Discord.

Comment ça marche (aucun crédit RPC, aucune clé API) :

1. On s'abonne au WebSocket Solana sur le programme pump.fun (`logsSubscribe`).
2. `CreateEvent`  → on met le mint en suivi (créateur, nom, timestamp).
3. `TradeEvent`   → si c'est un ACHAT signé par le wallet créateur et reçu dans
   les `DEV_BUY_MAX_AGE_SEC` s suivant la création, on cumule son montant en SOL.
   (Certains bots splittent le dev-buy en plusieurs ordres → on additionne.)
4. À la fermeture de la fenêtre :
      • total dans [DEV_BUY_MIN_SOL ; DEV_BUY_MAX_SOL]  → ALERTE Discord
      • sinon                                          → coin ignoré
5. Chaque coin ne déclenche qu'une seule alerte.

C'est le seul filtre. Rien d'autre.
"""

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass

import aiohttp
import websockets
from solana.rpc.async_api import AsyncClient

import config
import positions_store
import strategy
import wallet_ctx
from config import (
    DISCORD_WEBHOOK_URL,
    DISCORD_LOG_WEBHOOK_URL,
    PUBLIC_WS_URLS,
    PUMPFUN_PROGRAM,
    DEV_BUY_MIN_SOL,
    DEV_BUY_MAX_SOL,
    DEV_BUY_MAX_AGE_SEC,
    DEV_BUY_REQUIRE_CREATOR_MATCH,
    DEV_BUY_EPSILON_SOL,
    DEV_BUY_FAST_PATH,
    DEV_SKIP_IF_MULTI_TOKEN,
    DEV_MAX_TOKENS_BEFORE_SKIP,
    DEV_SELL_WATCH_TTL_SEC,
    TOKEN_TTL_SEC,
    MAX_TRACKED,
    EVAL_INTERVAL,
    RECONNECT_DELAY,
)
from discord_alert import send_dev_buy_alert
from discord_log import log, log_flush_loop, _safe_print as safe_print
from events import parse_log_line, try_parse_create, try_parse_trade
from sol_price import get_sol_price_usd

LAMPORTS_PER_SOL = 1_000_000_000
SOL_PRICE = 170.0  # rafraîchi par price_loop (CoinGecko, gratuit)


# ── État ────────────────────────────────────────────────────────────────────

@dataclass
class Tracked:
    mint:           str
    creator:        str
    name:           str
    symbol:         str
    created_at:     float
    create_sig:     str = ""
    dev_wallet:      str = ""      # wallet dont on cumule les achats
    dev_buy_lamports: int = 0      # cumul des achats du dev dans la fenêtre
    dev_buy_count:   int = 0
    first_buy_age:   float = 0.0   # délai création → 1er achat du dev
    dev_buy_in_create_tx: bool = False  # dev-buy vu dans la tx de création
    alerted:         bool = False
    done:            bool = False   # évalué (alerté ou rejeté) → à retirer
    dev_sell_watch_until: float = 0.0  # si alerted : surveille la vente du dev jusqu'à ce timestamp


class State:
    def __init__(self) -> None:
        self.tokens: dict[str, Tracked] = {}
        self.creator_token_count: dict[str, int] = {}

    def add(self, t: Tracked) -> None:
        if len(self.tokens) >= MAX_TRACKED:
            oldest = min(self.tokens.values(), key=lambda x: x.created_at)
            self.tokens.pop(oldest.mint, None)
        self.tokens[t.mint] = t

    def drop(self, mint: str) -> None:
        self.tokens.pop(mint, None)


# ── WS : détection créations + achats ───────────────────────────────────────

_seen: deque = deque(maxlen=4000)
_seen_set: set = set()


def _already_seen(sig: str) -> bool:
    if not sig:
        return False
    if sig in _seen_set:
        return True
    _seen.append(sig)
    _seen_set.add(sig)
    while len(_seen_set) > _seen.maxlen:
        _seen_set.discard(_seen.popleft())
    return False


def _handle_trade(state: State, tr, sig: str, now: float) -> None:
    tok = state.tokens.get(tr.mint)
    if tok is None:
        return

    # Coin déjà matché (dev-buy 71-78 SOL) : on ne surveille plus sa fenêtre de
    # création (déjà tranchée) mais on continue de guetter une vente du dev,
    # qui déclenche la stratégie d'achat — indépendant de `tok.done` ci-dessous.
    if tok.alerted:
        if (not tr.is_buy and tr.user == tok.creator
                and now <= tok.dev_sell_watch_until
                and not positions_store.is_closed(tr.mint)):
            asyncio.create_task(strategy.on_dev_sell(tr, tok))
        return

    if tok.done:
        return
    if not tr.is_buy:
        return
    age = now - tok.created_at
    if age > DEV_BUY_MAX_AGE_SEC:
        return

    # Quel wallet compte comme « le dev » pour ce coin ?
    if DEV_BUY_REQUIRE_CREATOR_MATCH:
        if tr.user != tok.creator:
            return
    else:
        # mode « premier acheteur » : le tout premier achat de la fenêtre fixe
        # le wallet ; on ne cumule ensuite que ses achats à lui.
        if not tok.dev_wallet:
            tok.dev_wallet = tr.user
        if tr.user != tok.dev_wallet:
            return

    tok.dev_buy_lamports += max(tr.sol_amount, 0)
    tok.dev_buy_count += 1
    if tok.dev_buy_count == 1:
        tok.first_buy_age = age
    if sig and sig == tok.create_sig:
        tok.dev_buy_in_create_tx = True

    sol = tok.dev_buy_lamports / LAMPORTS_PER_SOL
    if sol > DEV_BUY_MAX_SOL + DEV_BUY_EPSILON_SOL:
        # le dev a déjà dépassé 78 SOL → ne reviendra jamais dans la fenêtre
        log(f"[✗] {tok.name} ({tok.symbol}) : dev-buy {sol:.2f} SOL > "
            f"{DEV_BUY_MAX_SOL:g} — trop gros, ignoré")
        tok.done = True


async def _handle_ws_message(raw: str, state: State) -> None:
    msg = json.loads(raw)
    if msg.get("method") != "logsNotification":
        return
    value = msg["params"]["result"]["value"]
    if value.get("err"):
        return
    sig = value.get("signature", "")
    if _already_seen(sig):
        return

    now = time.time()
    creates, trades = [], []
    for line in value.get("logs", []):
        data = parse_log_line(line)
        if data is None:
            continue
        ev = try_parse_create(data)
        if ev is not None:
            creates.append(ev)
            continue
        tr = try_parse_trade(data)
        if tr is not None:
            trades.append(tr)

    # 1) créations d'abord (le dev-buy peut être dans la même tx)
    for ev in creates:
        if ev.mint in state.tokens:
            continue
        prior = state.creator_token_count.get(ev.creator, 0)
        state.creator_token_count[ev.creator] = prior + 1
        if DEV_SKIP_IF_MULTI_TOKEN and prior >= DEV_MAX_TOKENS_BEFORE_SKIP:
            safe_print(f"[skip] {ev.name} ({ev.symbol}) | dev {ev.creator[:6]}… "
                       f"a déjà {prior + 1} tokens — ignoré")
            continue
        state.add(Tracked(
            mint=ev.mint, creator=ev.creator, name=ev.name or "?",
            symbol=ev.symbol or "?", created_at=now, create_sig=sig,
        ))
        safe_print(f"[+] Création : {ev.name} ({ev.symbol}) | {ev.mint[:10]}… | dev {ev.creator[:6]}…")

    # 2) puis les achats
    for tr in trades:
        _handle_trade(state, tr, sig, now)

    # 3) fast-path : dev-buy dans la tx de création → on tranche tout de suite,
    #    sans attendre la fermeture de la fenêtre (~3 s gagnées).
    if DEV_BUY_FAST_PATH:
        for tr in trades:
            tok = state.tokens.get(tr.mint)
            if tok is not None and not tok.done and tok.dev_buy_in_create_tx:
                await _finalize(tok, fast=True)


async def _connect_and_subscribe(ws_url: str):
    while True:
        try:
            ws = await websockets.connect(
                ws_url, ping_interval=20, ping_timeout=30, max_size=16 * 1024 * 1024,
            )
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "logsSubscribe",
                "params": [{"mentions": [PUMPFUN_PROGRAM]}, {"commitment": "processed"}],
            }))
            await ws.recv()
            log(f"[WS] Abonné → {ws_url}")
            return ws
        except Exception as e:
            log(f"[WS] Échec connexion ({ws_url}) : {e} — retry dans {RECONNECT_DELAY:g}s")
            await asyncio.sleep(RECONNECT_DELAY)


async def listen(state: State) -> None:
    idx = 0
    while True:
        ws_url = PUBLIC_WS_URLS[idx % len(PUBLIC_WS_URLS)]
        idx += 1
        ws = await _connect_and_subscribe(ws_url)
        try:
            async for raw in ws:
                try:
                    await _handle_ws_message(raw, state)
                except Exception as e:
                    safe_print(f"[WS] Erreur traitement message : {e}")
        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            log(f"[WS] Connexion perdue ({e}) — reconnexion…")
            await asyncio.sleep(1)


# ── Évaluation ─────────────────────────────────────────────────────────────

async def _finalize(tok: "Tracked", *, fast: bool = False) -> None:
    """Tranche un coin : dev-buy cumulé dans [MIN ; MAX] → alerte, sinon rejet.
    Marque le coin comme traité. `fast` = déclenché par le fast-path (dev-buy
    dans la tx de création), sinon = fenêtre fermée."""
    if tok.done or tok.alerted:
        return
    lo = DEV_BUY_MIN_SOL - DEV_BUY_EPSILON_SOL
    hi = DEV_BUY_MAX_SOL + DEV_BUY_EPSILON_SOL
    sol = tok.dev_buy_lamports / LAMPORTS_PER_SOL
    if tok.dev_buy_count == 0:
        tok.done = True
        return
    dev = tok.dev_wallet or tok.creator
    if lo <= sol <= hi:
        tok.alerted = tok.done = True
        tok.dev_sell_watch_until = time.time() + DEV_SELL_WATCH_TTL_SEC
        tag = "MATCH⚡" if fast else "MATCH"
        log(f"[✓] {tag} : {tok.name} ({tok.symbol}) | dev-buy {sol:.2f} SOL "
            f"({tok.dev_buy_count} ordre(s)) en {tok.first_buy_age:.1f}s | "
            f"dev {dev[:6]}… → alerte envoyée")
        try:
            await send_dev_buy_alert(
                mint=tok.mint, name=tok.name, symbol=tok.symbol,
                creator=dev, dev_buy_sol=sol,
                dev_buy_count=tok.dev_buy_count, age_sec=tok.first_buy_age,
                create_sig=tok.create_sig, sol_price_usd=SOL_PRICE,
            )
        except Exception as e:
            log(f"[Discord] Échec envoi alerte {tok.name} : {e}")
    else:
        log(f"[·] {tok.name} ({tok.symbol}) : dev-buy {sol:.2f} SOL "
            f"hors fenêtre {DEV_BUY_MIN_SOL:g}–{DEV_BUY_MAX_SOL:g} — ignoré")
        tok.done = True


async def eval_loop(state: State) -> None:
    """Filet pour les dev-buys en tx séparée : on tranche à la fermeture de la
    fenêtre. Le cas « dev-buy dans la tx de création » est déjà traité en direct
    par le fast-path."""
    while True:
        await asyncio.sleep(EVAL_INTERVAL)
        now = time.time()
        for tok in list(state.tokens.values()):
            if tok.done or tok.alerted:
                continue
            if now - tok.created_at < DEV_BUY_MAX_AGE_SEC:
                continue  # fenêtre encore ouverte
            await _finalize(tok)


async def expire_loop(state: State) -> None:
    while True:
        await asyncio.sleep(10)
        now = time.time()
        to_drop = []
        for m, t in state.tokens.items():
            if t.alerted:
                # Coin matché : on le garde tant que la vente du dev peut encore
                # se produire ET que sa position (si ouverte) n'est pas clôturée.
                if now > t.dev_sell_watch_until or positions_store.is_closed(m):
                    to_drop.append(m)
            elif t.done or now - t.created_at > TOKEN_TTL_SEC:
                to_drop.append(m)
        for m in to_drop:
            state.drop(m)


async def price_loop() -> None:
    global SOL_PRICE
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                SOL_PRICE = await get_sol_price_usd(session)
            except Exception:
                pass
            # Aligné sur le TTL de sol_price.py (60s) : des seuils de market cap
            # (TP/SL) veulent un prix SOL/USD frais, pas jusqu'à 5 min de retard.
            await asyncio.sleep(60)


async def heartbeat_loop(state: State) -> None:
    while True:
        await asyncio.sleep(600)
        pending = sum(1 for t in state.tokens.values() if not t.done)
        log(f"[♥] En vie — {len(state.tokens)} coins en mémoire, {pending} en fenêtre | SOL ${SOL_PRICE:.2f}")


# ── main ────────────────────────────────────────────────────────────────────

async def main() -> None:
    log("=== 75 SOL — dev-buy scanner pump.fun — démarrage ===")
    log(f"    Filtre : dev-buy {DEV_BUY_MIN_SOL:g} – {DEV_BUY_MAX_SOL:g} SOL "
        f"dans les {DEV_BUY_MAX_AGE_SEC:g}s suivant la création")
    log(f"    Achats comptés : {'wallet créateur uniquement' if DEV_BUY_REQUIRE_CREATOR_MATCH else 'premier acheteur (tout wallet)'}")
    if DEV_SKIP_IF_MULTI_TOKEN:
        log(f"    Skip serial devs : dev avec > {DEV_MAX_TOKENS_BEFORE_SKIP} token(s) créé(s) → ignoré")
    log(f"    WS : {', '.join(PUBLIC_WS_URLS)}")
    if not DISCORD_WEBHOOK_URL:
        log("[!] DISCORD_WEBHOOK_URL non défini — les alertes ne partiront pas.")
    if not DISCORD_LOG_WEBHOOK_URL:
        safe_print("[i] DISCORD_LOG_WEBHOOK_URL non défini — logs en console uniquement.")

    _init_strategy()

    state = State()
    await asyncio.gather(
        log_flush_loop(),
        price_loop(),
        listen(state),
        eval_loop(state),
        expire_loop(state),
        heartbeat_loop(state),
    )


def _init_strategy() -> None:
    """Câble la stratégie "vente du dev" (achat/vente natif) si un wallet est
    configuré. Sans BOT_PRIVATE_KEY, le scanner tourne en détection seule comme
    avant — aucune régression du comportement existant."""
    if config.DRY_RUN:
        log("[STRATEGIE] DRY_RUN actif — aucune transaction ne sera réellement envoyée.")
    if not config.BOT_PRIVATE_KEY:
        log("[STRATEGIE] BOT_PRIVATE_KEY non défini — stratégie vente-du-dev désactivée (détection seule).")
        return
    if not config.RPC_HTTP:
        log("[STRATEGIE] RPC_HTTP non défini — stratégie vente-du-dev désactivée (détection seule).")
        return

    ctx = wallet_ctx.load_wallet(config.BOT_PRIVATE_KEY)
    client = AsyncClient(config.RPC_HTTP)
    extra_clients = [AsyncClient(url) for url in config.RPC_HTTP_EXTRA_URLS]
    strategy.init(client, extra_clients, ctx, config.RPC_WS)
    strategy.resume_open_positions()
    log(f"[STRATEGIE] Active — wallet {str(ctx.wallet)[:6]}… | "
        f"mise de base {config.BASE_POSITION_SOL:g} SOL | "
        f"TP ${config.TP_MCAP_USD:,.0f} | SL ${config.SL_MCAP_USD:,.0f}")


if __name__ == "__main__":
    asyncio.run(main())
