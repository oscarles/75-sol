"""
Construction des instructions bonding curve pump.fun (buy_exact_quote_in_v2 /
sell_v2) et assemblage de la transaction complète (compute budget, ATA
idempotente, tip Jito). Porté de Bundle10k/tx_builder.py.

Les instructions "legacy" (buy_exact_sol_in / sell) échouent aujourd'hui
on-chain avec l'erreur programme #6062 "BuybackFeeRecipientMissing" — les
instructions V2 sont la seule voie confirmée fonctionnelle.
"""
import struct

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

import pda
import trading_constants as tc
from wallet_ctx import WalletCtx


async def get_mint_token_program(client: AsyncClient, mint: Pubkey) -> Pubkey:
    """Classique SPL Token ou Token-2022 — vérifié on-chain (owner du compte
    mint), jamais supposé (certains coins pump.fun sont Token-2022)."""
    try:
        resp = await client.get_account_info(mint, commitment=Confirmed)
    except Exception:
        return tc.TOKEN_PROGRAM_ID
    if resp.value is None:
        return tc.TOKEN_PROGRAM_ID
    owner = resp.value.owner
    return tc.TOKEN_2022_PROGRAM_ID if owner == tc.TOKEN_2022_PROGRAM_ID else tc.TOKEN_PROGRAM_ID


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


def _v2_metas(accs: pda.V2Accounts, for_buy: bool) -> list:
    metas = [
        AccountMeta(pubkey=accs.global_, is_signer=False, is_writable=False),
        AccountMeta(pubkey=accs.base_mint, is_signer=False, is_writable=False),
        AccountMeta(pubkey=accs.quote_mint, is_signer=False, is_writable=False),
        AccountMeta(pubkey=accs.base_token_program, is_signer=False, is_writable=False),
        AccountMeta(pubkey=accs.quote_token_program, is_signer=False, is_writable=False),
        AccountMeta(pubkey=accs.associated_token_program, is_signer=False, is_writable=False),
        AccountMeta(pubkey=accs.fee_recipient, is_signer=False, is_writable=True),
        AccountMeta(pubkey=accs.associated_quote_fee_recipient, is_signer=False, is_writable=True),
        AccountMeta(pubkey=accs.buyback_fee_recipient, is_signer=False, is_writable=True),
        AccountMeta(pubkey=accs.associated_quote_buyback_fee_recipient, is_signer=False, is_writable=True),
        AccountMeta(pubkey=accs.bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=accs.associated_base_bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=accs.associated_quote_bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=accs.user, is_signer=True, is_writable=True),
        AccountMeta(pubkey=accs.associated_base_user, is_signer=False, is_writable=True),
        AccountMeta(pubkey=accs.associated_quote_user, is_signer=False, is_writable=True),
        AccountMeta(pubkey=accs.creator_vault, is_signer=False, is_writable=True),
        AccountMeta(pubkey=accs.associated_creator_vault, is_signer=False, is_writable=True),
        AccountMeta(pubkey=accs.sharing_config, is_signer=False, is_writable=False),
    ]
    if for_buy:
        metas.append(AccountMeta(pubkey=accs.global_volume_accumulator, is_signer=False, is_writable=False))
    metas.append(AccountMeta(pubkey=accs.user_volume_accumulator, is_signer=False, is_writable=True))
    metas.append(AccountMeta(pubkey=accs.associated_user_volume_accumulator, is_signer=False, is_writable=True))
    metas.append(AccountMeta(pubkey=accs.fee_config, is_signer=False, is_writable=False))
    metas.append(AccountMeta(pubkey=accs.fee_program, is_signer=False, is_writable=False))
    metas.append(AccountMeta(pubkey=accs.system_program, is_signer=False, is_writable=False))
    metas.append(AccountMeta(pubkey=accs.event_authority, is_signer=False, is_writable=False))
    metas.append(AccountMeta(pubkey=accs.program, is_signer=False, is_writable=False))
    return metas


def build_buy_v2_ix(
    mint: Pubkey, user: Pubkey, creator: Pubkey,
    spendable_quote_in: int, min_tokens_out: int, base_token_program: Pubkey = tc.TOKEN_PROGRAM_ID,
) -> Instruction:
    accs = pda.derive_v2_accounts(mint, user, creator, base_token_program, for_buy=True)
    data = tc.BUY_EXACT_QUOTE_IN_V2_DISC + struct.pack("<QQ", spendable_quote_in, min_tokens_out)
    return Instruction(program_id=tc.PUMP_FUN_PROGRAM_ID, accounts=_v2_metas(accs, for_buy=True), data=data)


def build_sell_v2_ix(
    mint: Pubkey, user: Pubkey, creator: Pubkey,
    amount: int, min_sol_output: int, base_token_program: Pubkey = tc.TOKEN_PROGRAM_ID,
) -> Instruction:
    accs = pda.derive_v2_accounts(mint, user, creator, base_token_program, for_buy=False)
    data = tc.SELL_V2_DISC + struct.pack("<QQ", amount, min_sol_output)
    return Instruction(program_id=tc.PUMP_FUN_PROGRAM_ID, accounts=_v2_metas(accs, for_buy=False), data=data)


def build_buy_tx(
    ctx: WalletCtx, mint: Pubkey, creator: Pubkey,
    spendable_sol_in: int, min_tokens_out: int, token_program: Pubkey,
    compute_unit_limit: int, compute_unit_price: int, jito_tip_lamports: int,
    blockhash,
) -> VersionedTransaction:
    buy_ix = build_buy_v2_ix(mint, ctx.wallet, creator, spendable_sol_in, min_tokens_out, token_program)
    instructions = [
        set_compute_unit_limit(compute_unit_limit),
        set_compute_unit_price(compute_unit_price),
        _create_ata_idempotent(ctx.wallet, ctx.wallet, mint, token_program),
        buy_ix,
    ]
    if jito_tip_lamports > 0:
        instructions.append(transfer(TransferParams(
            from_pubkey=ctx.wallet, to_pubkey=tc.JITO_TIP_ACCOUNT, lamports=jito_tip_lamports,
        )))
    msg = MessageV0.try_compile(payer=ctx.wallet, instructions=instructions,
                                 address_lookup_table_accounts=[], recent_blockhash=blockhash)
    return VersionedTransaction(msg, [ctx.keypair])


def build_sell_tx(
    ctx: WalletCtx, mint: Pubkey, creator: Pubkey,
    amount: int, min_sol_output: int, token_program: Pubkey,
    compute_unit_limit: int, compute_unit_price: int, jito_tip_lamports: int,
    blockhash,
) -> VersionedTransaction:
    sell_ix = build_sell_v2_ix(mint, ctx.wallet, creator, amount, min_sol_output, token_program)
    instructions = [
        set_compute_unit_limit(compute_unit_limit),
        set_compute_unit_price(compute_unit_price),
        sell_ix,
    ]
    if jito_tip_lamports > 0:
        instructions.append(transfer(TransferParams(
            from_pubkey=ctx.wallet, to_pubkey=tc.JITO_TIP_ACCOUNT, lamports=jito_tip_lamports,
        )))
    msg = MessageV0.try_compile(payer=ctx.wallet, instructions=instructions,
                                 address_lookup_table_accounts=[], recent_blockhash=blockhash)
    return VersionedTransaction(msg, [ctx.keypair])
