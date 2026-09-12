"""
Post-migration : PumpSwap (pAMM). Un coin migre automatiquement de la bonding
curve vers ce programme une fois complète.

Précision utilisateur : le seuil de migration pump.fun est ~85 SOL réels
investis dans la courbe, et le dev n'achète au plus que 71-78 SOL à la
création — donc au moment où notre stratégie surveille un coin, il est
TOUJOURS encore sur la bonding curve. On n'achète donc JAMAIS sur pAMM (cf.
strategy.py, qui skip l'achat si `complete` est déjà vrai au moment de la vente
du dev, plutôt que de tenter un chemin d'achat pAMM non éprouvé).

En revanche, une position déjà ouverte peut migrer PENDANT qu'on la détient
(plus d'acheteurs après nous font grimper le solde au-delà du seuil), avant
d'atteindre notre take-profit ou notre stop-loss — la VENTE post-migration
reste donc nécessaire. Ce module ne fait donc QUE la vente pAMM (+ le calcul de
market cap), porté tel quel de Bundle10k/pamm.py (déjà en production).

Le layout des comptes pAMM n'est PAS dérivable localement comme la bonding
curve (pool/vaults/creator_vault_ata ne sont pas des PDA simples) — on clone
les comptes d'une vraie vente récente sur la pool, en substituant nos propres
comptes (wallet, ATAs).
"""
import asyncio
import struct
from typing import Optional

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
try:
    from solana.rpc.types import MemcmpOpts
except ImportError:
    from solders.rpc.filter import Memcmp as _Memcmp

    def MemcmpOpts(offset: int, bytes: str):  # noqa: A002
        return _Memcmp(offset=offset, bytes_=bytes)
from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

import config
import market_cap
import pda
import trading_constants as tc
import tx_builder
import tx_sender
from trading_log import log
from wallet_ctx import WalletCtx

_ALT_HEADER_SIZE = 56

_pool_cache: dict = {}
_sell_template_cache: dict = {}


def _create_ata_idempotent(payer: Pubkey, owner: Pubkey, mint: Pubkey, token_program: Pubkey) -> Instruction:
    ata = pda.associated_token_address(owner, mint, token_program)
    return Instruction(
        program_id=tc.ASSOCIATED_TOKEN_PROGRAM_ID,
        accounts=[
            AccountMeta(pubkey=payer, is_signer=True, is_writable=True),
            AccountMeta(pubkey=ata, is_signer=False, is_writable=True),
            AccountMeta(pubkey=owner, is_signer=False, is_writable=False),
            AccountMeta(pubkey=mint, is_signer=False, is_writable=False),
            AccountMeta(pubkey=tc.SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(pubkey=token_program, is_signer=False, is_writable=False),
        ],
        data=bytes([0x01]),
    )


def _close_token_account(account: Pubkey, destination: Pubkey, authority: Pubkey) -> Instruction:
    return Instruction(
        program_id=tc.TOKEN_PROGRAM_ID,
        accounts=[
            AccountMeta(pubkey=account, is_signer=False, is_writable=True),
            AccountMeta(pubkey=destination, is_signer=False, is_writable=True),
            AccountMeta(pubkey=authority, is_signer=True, is_writable=False),
        ],
        data=bytes([9]),
    )


def _parse_alt_addresses(raw: bytes) -> list:
    return [Pubkey.from_bytes(raw[o:o + 32]) for o in range(_ALT_HEADER_SIZE, len(raw) - 31, 32)]


async def _resolve_accounts(client: AsyncClient, message) -> tuple:
    static_keys = list(message.account_keys)
    h = message.header
    n_static = len(static_keys)
    n_signed_wr = h.num_required_signatures - h.num_readonly_signed_accounts
    n_unsigned_wr = n_static - h.num_required_signatures - h.num_readonly_unsigned_accounts
    writable = set(range(n_signed_wr)) | set(
        range(h.num_required_signatures, h.num_required_signatures + n_unsigned_wr)
    )
    accounts = list(static_keys)
    alt_accounts = []
    if not hasattr(message, "address_table_lookups"):
        return accounts, alt_accounts, writable
    offset = n_static
    for lookup in message.address_table_lookups:
        resp = await client.get_account_info(lookup.account_key)
        if resp.value is None:
            raise RuntimeError(f"ALT introuvable : {lookup.account_key}")
        all_addrs = _parse_alt_addresses(bytes(resp.value.data))
        alt_accounts.append(AddressLookupTableAccount(key=lookup.account_key, addresses=all_addrs))
        for idx in lookup.writable_indexes:
            accounts.append(all_addrs[idx])
            writable.add(offset)
            offset += 1
        for idx in lookup.readonly_indexes:
            accounts.append(all_addrs[idx])
            offset += 1
    return accounts, alt_accounts, writable


async def find_pool(client: AsyncClient, mint: Pubkey) -> Optional[Pubkey]:
    """Pool pAMM pour ce mint — offset 43 = base_mint dans le layout de compte
    pAMM (memcmp)."""
    if mint in _pool_cache:
        return _pool_cache[mint]
    try:
        resp = await client.get_program_accounts(
            tc.PAMM_PROGRAM_ID, filters=[MemcmpOpts(offset=43, bytes=str(mint))],
            encoding="base64", commitment=Confirmed,
        )
    except Exception as e:
        log.warning(f"[PAMM] Recherche pool echouee pour {mint} : {e}")
        return None
    if not resp.value:
        return None
    pool = resp.value[0].pubkey
    _pool_cache[mint] = pool
    return pool


def _encode_sell_data(base_amount_in: int, min_quote_amount_out: int) -> bytes:
    return tc.PAMM_SELL_DISC + struct.pack("<QQ", base_amount_in, min_quote_amount_out)


def _find_pamm_sell_ix(message, resolved, meta):
    for raw_ix in message.instructions:
        try:
            if resolved[raw_ix.program_id_index] == tc.PAMM_PROGRAM_ID:
                if bytes(raw_ix.data)[:8] == tc.PAMM_SELL_DISC and len(raw_ix.accounts) >= 22:
                    return raw_ix
        except Exception:
            continue
    if meta and meta.inner_instructions:
        for group in meta.inner_instructions:
            for ix in group.instructions:
                try:
                    if resolved[ix.program_id_index] == tc.PAMM_PROGRAM_ID:
                        raw = bytes(ix.data) if not isinstance(ix.data, str) else None
                        if raw and raw[:8] == tc.PAMM_SELL_DISC and len(ix.accounts) >= 22:
                            return ix
                except Exception:
                    continue
    return None


async def _resolve_sell_template(client: AsyncClient, mint: Pubkey, pool: Pubkey, wallet: Pubkey) -> tuple:
    """Clone les comptes d'une vraie sell-tx récente sur cette pool, en
    substituant (user, base_ata, wsol_ata) par les nôtres. Renvoie
    (accounts_metas, alt_accounts) ou ([], []) si aucune sell-tx trouvée.

    Cache PAR (mint, wallet) : les metas contiennent des comptes propres au
    wallet — une clé sur le mint seul ferait qu'un 1er wallet résolu
    poisonnerait le template d'un autre wallet."""
    cache_key = (str(mint), str(wallet))
    if cache_key in _sell_template_cache:
        return _sell_template_cache[cache_key]

    my_wsol_ata = pda.associated_token_address(wallet, tc.WSOL_MINT, tc.TOKEN_PROGRAM_ID)

    for _attempt in range(8):
        try:
            sigs = await client.get_signatures_for_address(pool, limit=15, commitment=Confirmed)
        except Exception:
            await asyncio.sleep(1.0)
            continue
        if not sigs.value:
            await asyncio.sleep(1.0)
            continue
        for sig_info in sigs.value:
            if sig_info.err is not None:
                continue
            try:
                tx_resp = await client.get_transaction(
                    sig_info.signature, max_supported_transaction_version=0,
                    commitment=Confirmed, encoding="base64",
                )
            except Exception:
                continue
            if tx_resp.value is None:
                continue
            try:
                ref_msg = tx_resp.value.transaction.transaction.message
                ref_meta = tx_resp.value.transaction.meta
                ref_resolved, ref_alts, ref_writable = await _resolve_accounts(client, ref_msg)
            except Exception:
                continue
            ref_ix = _find_pamm_sell_ix(ref_msg, ref_resolved, ref_meta)
            if ref_ix is None:
                continue

            ref_user = ref_resolved[ref_ix.accounts[1]]
            ref_base_ata = ref_resolved[ref_ix.accounts[5]]
            ref_wsol_ata = ref_resolved[ref_ix.accounts[6]]
            base_token_program = await tx_builder.get_mint_token_program(client, mint)
            my_ata = pda.associated_token_address(wallet, mint, base_token_program)

            metas = []
            for acc_idx in ref_ix.accounts:
                pk = ref_resolved[acc_idx]
                is_sig = pk == ref_user
                is_wr = acc_idx in ref_writable
                if pk == ref_user:
                    pk, is_sig, is_wr = wallet, True, True
                elif pk == ref_base_ata:
                    pk, is_wr = my_ata, True
                elif pk == ref_wsol_ata:
                    pk, is_wr = my_wsol_ata, True
                metas.append(AccountMeta(pubkey=pk, is_signer=is_sig, is_writable=is_wr))

            _sell_template_cache[cache_key] = (metas, ref_alts)
            log.info(f"[PAMM] Template sell clonee depuis {sig_info.signature} ({len(metas)} comptes)")
            return metas, ref_alts
        await asyncio.sleep(1.0)

    log.warning(f"[PAMM] Aucune sell-tx de reference trouvee sur la pool {pool} pour {mint}")
    return [], []


async def resolve_vaults(client: AsyncClient, mint: Pubkey, pool: Pubkey, wallet: Pubkey) -> Optional[tuple]:
    """Resout (base_vault, quote_vault) via la template sell clonee (indices 7
    et 8, identiques pour buy et sell dans le layout pAMM)."""
    metas, _ = await _resolve_sell_template(client, mint, pool, wallet)
    if not metas or len(metas) < 9:
        return None
    return metas[7].pubkey, metas[8].pubkey


async def pool_reserves_ui(client: AsyncClient, mint: Pubkey, pool: Pubkey, wallet: Pubkey) -> Optional[tuple]:
    """(sol_in_pool, tokens_in_pool) en unites UI (pas brutes) — lecture directe
    des vaults de la pool."""
    vaults = await resolve_vaults(client, mint, pool, wallet)
    if vaults is None:
        return None
    base_vault, quote_vault = vaults
    try:
        tok_resp, sol_resp = await asyncio.gather(
            client.get_token_account_balance(base_vault, commitment=Confirmed),
            client.get_token_account_balance(quote_vault, commitment=Confirmed),
        )
    except Exception as e:
        log.warning(f"[PAMM] Lecture vaults echouee pour {mint} : {e}")
        return None
    tokens_in_pool = float(tok_resp.value.ui_amount or 0)
    sol_in_pool = float(sol_resp.value.ui_amount or 0)
    if tokens_in_pool <= 0 or sol_in_pool <= 0:
        return None
    return sol_in_pool, tokens_in_pool


async def get_mc_usd(client: AsyncClient, mint: Pubkey, pool: Pubkey, wallet: Pubkey, sol_price_usd: float) -> Optional[float]:
    reserves = await pool_reserves_ui(client, mint, pool, wallet)
    if reserves is None:
        return None
    sol_in_pool, tokens_in_pool = reserves
    return market_cap.pamm_mc_usd(sol_in_pool, tokens_in_pool, sol_price_usd)


async def simulate_mc_usd(
    client: AsyncClient, mint: Pubkey, pool: Pubkey, ctx: WalletCtx,
    token_amount: int, sol_price_usd: float, blockhash,
) -> Optional[float]:
    """MC calculee depuis le prix REELLEMENT executable (simulation de la vraie
    instruction de vente), pas depuis le ratio brut des vaults x un facteur
    empirique fixe. Necessite une position reelle (token_amount > 0)."""
    metas, alts = await _resolve_sell_template(client, mint, pool, ctx.wallet)
    if not metas or len(metas) < 9:
        return None
    quote_vault = metas[8].pubkey

    sell_ix = Instruction(program_id=tc.PAMM_PROGRAM_ID, accounts=metas,
                           data=_encode_sell_data(token_amount, 1))
    instructions = [
        set_compute_unit_limit(config.COMPUTE_UNIT_LIMIT),
        set_compute_unit_price(config.COMPUTE_UNIT_PRICE_SELL),
        sell_ix,
    ]

    try:
        msg = MessageV0.try_compile(payer=ctx.wallet, instructions=instructions,
                                     address_lookup_table_accounts=alts, recent_blockhash=blockhash)
        tx = VersionedTransaction(msg, [ctx.keypair])
        pre = await client.get_token_account_balance(quote_vault, commitment=Confirmed)
        pre_amount = int(pre.value.amount)
        resp = await client.simulate_transaction(
            tx, sig_verify=False, replace_recent_blockhash=True,
            accounts_addresses=[quote_vault], accounts_encoding="base64",
        )
    except Exception as e:
        log.debug(f"[PAMM] Simulation MC echouee pour {mint} : {e}")
        return None
    if resp.value.err is not None:
        log.debug(f"[PAMM] Simulation sell echouee pour {mint} : {resp.value.err}")
        return None
    accs = resp.value.accounts
    if not accs or accs[0] is None or len(accs[0].data) < 72:
        return None
    post_amount = struct.unpack_from("<Q", accs[0].data, 64)[0]
    proceeds_lamports = pre_amount - post_amount
    if proceeds_lamports <= 0 or token_amount <= 0:
        return None
    price_sol = (proceeds_lamports / 1e9) / (token_amount / 10 ** tc.TOKEN_DECIMALS)
    return price_sol * tc.PUMP_SUPPLY * sol_price_usd


async def sell(
    client: AsyncClient, extra_clients: list, ctx: WalletCtx, mint: Pubkey, pool: Pubkey,
    token_amount: int, min_sol_output: int, blockhash, rpc_ws: str,
) -> tuple:
    """Vend token_amount de mint sur la pool pAMM. Retourne (signature|None, error)."""
    metas, alts = await _resolve_sell_template(client, mint, pool, ctx.wallet)
    if not metas:
        return None, "Template sell pAMM introuvable - vente impossible"

    my_wsol_ata = pda.associated_token_address(ctx.wallet, tc.WSOL_MINT, tc.TOKEN_PROGRAM_ID)
    sell_ix = Instruction(program_id=tc.PAMM_PROGRAM_ID, accounts=metas,
                           data=_encode_sell_data(token_amount, min_sol_output))
    instructions = [
        set_compute_unit_limit(config.COMPUTE_UNIT_LIMIT),
        set_compute_unit_price(config.COMPUTE_UNIT_PRICE_SELL),
        _create_ata_idempotent(ctx.wallet, ctx.wallet, tc.WSOL_MINT, tc.TOKEN_PROGRAM_ID),
        sell_ix,
        _close_token_account(my_wsol_ata, ctx.wallet, ctx.wallet),
    ]
    if config.JITO_TIP_LAMPORTS_SELL > 0:
        instructions.append(transfer(TransferParams(
            from_pubkey=ctx.wallet, to_pubkey=tc.JITO_TIP_ACCOUNT, lamports=config.JITO_TIP_LAMPORTS_SELL,
        )))
    try:
        msg = MessageV0.try_compile(payer=ctx.wallet, instructions=instructions,
                                     address_lookup_table_accounts=alts, recent_blockhash=blockhash)
        tx = VersionedTransaction(msg, [ctx.keypair])
    except Exception as e:
        return None, f"construction tx sell pAMM impossible : {e!r}"
    return await tx_sender.broadcast_and_confirm(
        client, extra_clients, tx, "PAMM-SELL",
        config.TX_SPAM_RETRIES, config.TX_SPAM_INTERVAL, config.NATIVE_CONFIRM_TIMEOUT_SEC, rpc_ws,
    )
