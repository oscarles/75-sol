"""
Stratégie "vente du dev" — cœur de la fonctionnalité.

Achat : sur un coin déjà matché par le filtre 71-78 SOL existant (bot.py), dès
que le wallet du dev VEND, on achète 50% d'une mise unité (2 achats max, fondus
dans la même position). Le seuil de migration pump.fun (~85 SOL réels investis
dans la courbe) est au-dessus du dev-buy max (78 SOL) : un coin qu'on surveille
est donc TOUJOURS encore sur la bonding curve au moment de la vente du dev — on
n'achète donc QUE sur la bonding curve (jamais sur pAMM, cf. pamm.py). Si un
coin a migré entre-temps (plus d'acheteurs après le dev), on SKIP l'achat
plutôt que de risquer un chemin non éprouvé.

Sortie : take-profit à 100% dès $80k de MC, stop-loss à 100% dès $7k. Une
position peut migrer vers pAMM PENDANT qu'on la détient (avant TP/SL) — la
vente doit donc gérer les deux venues (bonding curve + pAMM).

Martingale : cf. martingale_state.py + sizing.py pour la partie pure/testée.
"""
import asyncio
import time
from typing import Optional

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.pubkey import Pubkey

import bonding_curve_state
import config
import discord_alert
import market_cap
import martingale_state
import pamm
import pda
import positions_store
import sizing
import sol_price
import trading_constants as tc
import tx_builder
import tx_sender
from positions_store import Position
from trading_log import log
from wallet_ctx import WalletCtx

# ── État global du module, câblé une fois au démarrage par bot.py ───────────
_client: Optional[AsyncClient] = None
_extra_clients: list = []
_ctx: Optional[WalletCtx] = None
_rpc_ws: str = ""

_mint_locks: dict[str, asyncio.Lock] = {}
_martingale_lock = asyncio.Lock()
_monitor_tasks: set = set()


def init(client: AsyncClient, extra_clients: list, ctx: Optional[WalletCtx], rpc_ws: str) -> None:
    global _client, _extra_clients, _ctx, _rpc_ws
    _client, _extra_clients, _ctx, _rpc_ws = client, extra_clients, ctx, rpc_ws


def is_active() -> bool:
    """False tant qu'aucun wallet n'est chargé (BOT_PRIVATE_KEY vide) — la
    stratégie reste alors totalement inactive, y compris en DRY_RUN (il faut au
    moins un keypair, même non fondé, pour construire des transactions)."""
    return _ctx is not None


def _mint_lock(mint: str) -> asyncio.Lock:
    lock = _mint_locks.get(mint)
    if lock is None:
        lock = asyncio.Lock()
        _mint_locks[mint] = lock
    return lock


async def _get_blockhash(client: AsyncClient):
    resp = await client.get_latest_blockhash(commitment=Confirmed)
    return resp.value.blockhash


async def _get_balance(client: AsyncClient, ata: Pubkey) -> int:
    """Solde brut de l'ATA (unités TOKEN_DECIMALS). Retente sur erreur RPC
    transitoire avant d'abandonner (0)."""
    last = None
    for attempt in range(4):
        try:
            resp = await client.get_token_account_balance(ata, commitment=Confirmed)
            return int(resp.value.amount)
        except Exception as e:
            last = e
            if "could not find account" in str(e).lower():
                return 0
            await asyncio.sleep(0.5 * (attempt + 1))
    log.warning(f"[STRATEGY] Lecture de solde impossible pour {ata} apres 4 essais : {last!r} - on suppose 0")
    return 0


def _on_monitor_done(task: "asyncio.Task") -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(f"[STRATEGY] Monitor arrete sur exception non geree : {exc!r}")


def _spawn_monitor(mint: str) -> None:
    task = asyncio.create_task(monitor_position(mint))
    _monitor_tasks.add(task)
    task.add_done_callback(_monitor_tasks.discard)
    task.add_done_callback(_on_monitor_done)


def resume_open_positions() -> None:
    """À appeler une fois au démarrage (après init()) pour reprendre le suivi
    des positions encore ouvertes après un redémarrage."""
    for pos in positions_store.load_open():
        log.info(f"[STRATEGY] Reprise du suivi de {pos.mint[:12]}... (buys_done={pos.buys_done}, venue={pos.venue})")
        _spawn_monitor(pos.mint)


# ── Entrée : vente du dev détectée ───────────────────────────────────────────

async def on_dev_sell(tr, tok) -> None:
    """`tr` : events.TradeEvent (is_buy=False, tr.user == tok.creator).
    `tok` : bot.Tracked du coin déjà matché (71-78 SOL)."""
    if not is_active():
        return
    mint = tr.mint

    async with _mint_lock(mint):
        try:
            await _handle_dev_sell(mint, tok)
        except Exception as e:
            log.error(f"[STRATEGY] on_dev_sell({mint[:12]}...) a echoue sur exception : {e!r}")
            await discord_alert.send_execution_failed_alert(mint, tok.name, "achat (vente du dev)", repr(e))


async def _handle_dev_sell(mint: str, tok) -> None:
    pos = positions_store.get(mint)
    if pos is not None and pos.closed:
        return  # ce coin a déjà fini son cycle, on n'y retouche plus
    if pos is not None and not sizing.can_buy(pos.buys_done):
        return  # déjà 2 achats, on ignore les ventes suivantes du dev

    mint_pk = Pubkey.from_string(mint)
    creator_pk = Pubkey.from_string(tok.creator)

    state = await bonding_curve_state.fetch_bonding_curve(_client, mint_pk, commitment=Confirmed)
    if state is None:
        log.warning(f"[STRATEGY] Bonding curve introuvable pour {mint[:12]}... - achat ignore")
        return
    if state.complete:
        # Migré avant même notre 1ère vente du dev (ne devrait pas arriver : le
        # dev n'achète jamais plus que le seuil de migration) — on ne risque pas
        # un achat pAMM non éprouvé, on skip.
        log.warning(f"[STRATEGY] {mint[:12]}... deja migre vers pAMM - achat sur bonding curve impossible, ignore")
        return

    if pos is None:
        multiplier = martingale_state.current_multiplier()
        pos = Position(
            mint=mint, creator=tok.creator, token_program="",
            entry_ts=time.time(), venue="bonding_curve", multiplier=multiplier,
        )
    buy_number = pos.buys_done + 1

    size_sol = sizing.buy_size_sol(config.BASE_POSITION_SOL, pos.multiplier)
    lamports_in = int(size_sol * 10 ** tc.SOL_DECIMALS)
    if lamports_in <= 0:
        return

    token_program = await tx_builder.get_mint_token_program(_client, mint_pk)
    min_tokens_out = market_cap.quote_min_tokens_out(
        lamports_in, state.virtual_sol_reserves, state.virtual_token_reserves,
        config.SLIPPAGE_BUY_PCT, config.TOTAL_FEE_BPS,
    )
    blockhash = await _get_blockhash(_client)
    tx = tx_builder.build_buy_tx(
        _ctx, mint_pk, creator_pk, lamports_in, min_tokens_out, token_program,
        config.COMPUTE_UNIT_LIMIT, config.COMPUTE_UNIT_PRICE_BUY, config.JITO_TIP_LAMPORTS_BUY,
        blockhash,
    )
    sig, err = await tx_sender.broadcast_and_confirm(
        _client, _extra_clients, tx, f"BUY{buy_number}",
        config.TX_SPAM_RETRIES, config.TX_SPAM_INTERVAL, config.NATIVE_CONFIRM_TIMEOUT_SEC, _rpc_ws,
    )
    if err:
        log.error(f"[STRATEGY] Achat {buy_number}/2 echoue pour {mint[:12]}... : {err}")
        await discord_alert.send_execution_failed_alert(mint, tok.name, f"achat {buy_number}/2", err)
        return

    pos.token_program = str(token_program)
    pos.sol_invested += size_sol
    pos.buys_done = buy_number
    if config.DRY_RUN:
        # Rien n'atterrit vraiment on-chain en DRY_RUN : on estime le solde
        # plutôt que de lire un vrai compte token (qui resterait à 0).
        pos.tokens_held += min_tokens_out
    else:
        my_ata = pda.associated_token_address(_ctx.wallet, mint_pk, token_program)
        pos.tokens_held = await _get_balance(_client, my_ata)
    positions_store.save(pos)

    price = sol_price.get_cached()
    mc = market_cap.bonding_curve_mc_usd(state.virtual_sol_reserves, state.virtual_token_reserves, price)
    await discord_alert.send_dev_sell_buy_alert(mint, tok.name, buy_number, size_sol, pos.multiplier, mc, str(sig or ""))

    if buy_number == 1:
        _spawn_monitor(mint)


# ── Sortie : suivi de market cap (TP/SL) ─────────────────────────────────────

async def _current_mc(pos: Position) -> tuple[Optional[float], Optional[object]]:
    """Retourne (mc_usd, bonding_curve_state|None). Bascule pos.venue vers
    "pamm" et sauvegarde si une migration est détectée en cours de route."""
    mint_pk = Pubkey.from_string(pos.mint)
    price = sol_price.get_cached()
    if pos.venue == "bonding_curve":
        state = await bonding_curve_state.fetch_bonding_curve(_client, mint_pk, commitment=Confirmed)
        if state is None:
            return None, None
        if state.complete:
            pool = await pamm.find_pool(_client, mint_pk)
            if pool is not None:
                log.warning(f"[STRATEGY] {pos.mint[:12]}... a migre vers pAMM (pool {pool}) - bascule du suivi")
                pos.venue = "pamm"
                pos.pamm_pool = str(pool)
                positions_store.save(pos)
                mc = await _pamm_mc(pos, pool, price)
                return mc, None
            return None, state  # migration détectée mais pool pas encore indexée
        mc = market_cap.bonding_curve_mc_usd(state.virtual_sol_reserves, state.virtual_token_reserves, price)
        return mc, state
    else:
        pool = Pubkey.from_string(pos.pamm_pool)
        mc = await _pamm_mc(pos, pool, price)
        return mc, None


async def _pamm_mc(pos: Position, pool: Pubkey, price: float) -> Optional[float]:
    mint_pk = Pubkey.from_string(pos.mint)
    balance = await _current_token_balance(pos)
    if balance > 0:
        blockhash = await _get_blockhash(_client)
        mc = await pamm.simulate_mc_usd(_client, mint_pk, pool, _ctx, balance, price, blockhash)
        if mc is not None:
            return mc
    return await pamm.get_mc_usd(_client, mint_pk, pool, _ctx.wallet, price)


async def _current_token_balance(pos: Position) -> int:
    """En DRY_RUN, rien n'a jamais été réellement acheté on-chain — le solde
    réel serait toujours 0. On utilise alors notre bookkeeping (pos.tokens_held).
    En live, on relit le vrai solde (plus fiable que du bookkeeping cumulatif)."""
    if config.DRY_RUN:
        return pos.tokens_held
    mint_pk = Pubkey.from_string(pos.mint)
    token_program = Pubkey.from_string(pos.token_program)
    my_ata = pda.associated_token_address(_ctx.wallet, mint_pk, token_program)
    return await _get_balance(_client, my_ata)


async def _sell_all(pos: Position, state) -> tuple[Optional[str], float]:
    """Vend 100% du solde courant (venue courante). Retourne (signature|None,
    slippage_pct utilisé)."""
    amount = await _current_token_balance(pos)
    if amount <= 0:
        return None, 0.0
    mint_pk = Pubkey.from_string(pos.mint)
    creator_pk = Pubkey.from_string(pos.creator)
    token_program = Pubkey.from_string(pos.token_program)

    if pos.venue == "bonding_curve":
        expected_sol = 0.0
        if state is not None and state.virtual_token_reserves > 0:
            expected_sol = amount * (state.virtual_sol_reserves / state.virtual_token_reserves)
        min_sol_output = max(1, int(expected_sol * (1 - config.SLIPPAGE_SELL_PCT_SL)))
        if config.DRY_RUN:
            sig, err = _fake_dry_run_sig(f"SELL-BC-{pos.mint[:8]}")
        else:
            blockhash = await _get_blockhash(_client)
            tx = tx_builder.build_sell_tx(
                _ctx, mint_pk, creator_pk, amount, min_sol_output, token_program,
                config.COMPUTE_UNIT_LIMIT, config.COMPUTE_UNIT_PRICE_SELL, config.JITO_TIP_LAMPORTS_SELL,
                blockhash,
            )
            sig, err = await tx_sender.broadcast_and_confirm(
                _client, _extra_clients, tx, "SELL",
                config.TX_SPAM_RETRIES, config.TX_SPAM_INTERVAL, config.NATIVE_CONFIRM_TIMEOUT_SEC, _rpc_ws,
            )
    else:
        pool = Pubkey.from_string(pos.pamm_pool)
        if config.DRY_RUN:
            sig, err = _fake_dry_run_sig(f"SELL-PAMM-{pos.mint[:8]}")
        else:
            reserves = await pamm.pool_reserves_ui(_client, mint_pk, pool, _ctx.wallet)
            expected_sol_lamports = 0
            if reserves:
                sol_in_pool, tokens_in_pool = reserves
                amount_ui = amount / 10 ** tc.TOKEN_DECIMALS
                if tokens_in_pool > 0:
                    expected_sol_lamports = int((amount_ui * (sol_in_pool / tokens_in_pool)) * 10 ** tc.SOL_DECIMALS)
            min_sol_output = max(1, int(expected_sol_lamports * (1 - config.SLIPPAGE_SELL_PCT_SL)))
            blockhash = await _get_blockhash(_client)
            sig, err = await pamm.sell(
                _client, _extra_clients, _ctx, mint_pk, pool, amount, min_sol_output, blockhash, _rpc_ws,
            )

    if err:
        log.error(f"[STRATEGY] Vente echouee pour {pos.mint[:12]}... : {err}")
        return None, config.SLIPPAGE_SELL_PCT_SL
    return str(sig) if sig else "", config.SLIPPAGE_SELL_PCT_SL


def _fake_dry_run_sig(label: str) -> tuple:
    log.info(f"[{label}] DRY_RUN actif - vente NON envoyee")
    return f"DRY_RUN-{label}", ""


async def _close_position(pos: Position, reason: str, mc: float, state=None) -> bool:
    """reason: "tp" | "sl" | "secours". `state` : BondingCurveState déjà lu par
    l'appelant si venue == bonding_curve (évite une 2e lecture RPC juste avant
    de vendre). Retourne True si la position a été effectivement clôturée
    (vente réussie ou solde déjà nul) — False si la vente a échoué avec des
    tokens encore en poche, auquel cas la position reste ouverte pour retenter
    au prochain tick de monitor_position plutôt que de perdre le bag sans
    surveillance (cf. Bundle10k position_monitor.py, même garde-fou)."""
    sig, _ = await _sell_all(pos, state)

    if not config.DRY_RUN:
        remaining = await _current_token_balance(pos)
        if remaining > 0:
            log.error(f"[STRATEGY] Sortie {reason} echouee pour {pos.mint[:12]}... - "
                      f"{remaining} tokens non vendus, position LAISSEE OUVERTE")
            await discord_alert.send_execution_failed_alert(
                pos.mint, pos.mint[:8], f"sortie {reason}",
                f"Vente echouee, {remaining} tokens non vendus. Position laissee ouverte, retentative au prochain tick.",
            )
            return False

    hit_stop_loss = reason in ("sl", "secours")
    async with _martingale_lock:
        martingale_state.on_position_closed(hit_stop_loss=hit_stop_loss)
    next_mult = martingale_state.current_multiplier()

    positions_store.close(pos.mint, reason)

    name = pos.mint[:8]
    if reason == "tp":
        await discord_alert.send_take_profit_alert(pos.mint, name, mc, sig or "", next_mult)
    else:
        await discord_alert.send_stop_loss_alert(pos.mint, name, mc, sig or "", next_mult, reason=reason)
    return True


async def monitor_position(mint: str) -> None:
    pos = positions_store.get(mint)
    if pos is None or pos.closed:
        return
    log.info(f"[STRATEGY] Suivi demarre pour {mint[:12]}... venue={pos.venue}")

    unknown_since: Optional[float] = None
    while True:
        await asyncio.sleep(config.MC_MONITOR_INTERVAL)

        pos = positions_store.get(mint)
        if pos is None or pos.closed:
            return

        try:
            mc, state = await _current_mc(pos)
        except Exception as e:
            log.warning(f"[STRATEGY] Lecture MC {mint[:12]}... en echec : {e!r}")
            mc, state = None, None

        if mc is None:
            unknown_since = unknown_since or time.time()
            if time.time() - unknown_since >= config.MC_UNKNOWN_TIMEOUT_SEC:
                log.error(f"[STRATEGY] MC introuvable depuis {config.MC_UNKNOWN_TIMEOUT_SEC:.0f}s pour {mint[:12]}... - vente de secours")
                if await _close_position(pos, "secours", 0.0, state):
                    return
                continue  # vente échouée, tokens encore en poche -> retenter au prochain tick
            continue
        unknown_since = None

        if mc >= config.TP_MCAP_USD:
            if await _close_position(pos, "tp", mc, state):
                return
            continue
        if mc <= config.SL_MCAP_USD:
            if await _close_position(pos, "sl", mc, state):
                return
            continue
