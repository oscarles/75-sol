"""
Dérivation locale de tous les comptes pump.fun nécessaires au buy/sell V2 — zéro
appel RPC. Seeds vérifiées contre l'IDL officielle (voir trading_constants.py).
Porté de Bundle10k/pda.py.
"""
from dataclasses import dataclass
from typing import Optional

from solders.pubkey import Pubkey

import trading_constants as tc


def global_pda() -> Pubkey:
    return Pubkey.find_program_address([tc.SEED_GLOBAL], tc.PUMP_FUN_PROGRAM_ID)[0]


def bonding_curve_pda(mint: Pubkey) -> Pubkey:
    return Pubkey.find_program_address(
        [tc.SEED_BONDING_CURVE, bytes(mint)], tc.PUMP_FUN_PROGRAM_ID
    )[0]


def creator_vault_pda(creator: Pubkey) -> Pubkey:
    return Pubkey.find_program_address(
        [tc.SEED_CREATOR_VAULT, bytes(creator)], tc.PUMP_FUN_PROGRAM_ID
    )[0]


def event_authority_pda() -> Pubkey:
    return Pubkey.find_program_address([tc.SEED_EVENT_AUTHORITY], tc.PUMP_FUN_PROGRAM_ID)[0]


def global_volume_accumulator_pda() -> Pubkey:
    return Pubkey.find_program_address(
        [tc.SEED_GLOBAL_VOLUME_ACCUMULATOR], tc.PUMP_FUN_PROGRAM_ID
    )[0]


def user_volume_accumulator_pda(user: Pubkey) -> Pubkey:
    return Pubkey.find_program_address(
        [tc.SEED_USER_VOLUME_ACCUMULATOR, bytes(user)], tc.PUMP_FUN_PROGRAM_ID
    )[0]


def fee_config_pda() -> Pubkey:
    """PDA dérivée sous FEE_PROGRAM_ID (pas PUMP_FUN_PROGRAM_ID)."""
    return Pubkey.find_program_address(
        [tc.SEED_FEE_CONFIG, tc.FEE_CONFIG_SEED_2], tc.FEE_PROGRAM_ID
    )[0]


def sharing_config_pda(base_mint: Pubkey) -> Pubkey:
    return Pubkey.find_program_address(
        [tc.SEED_SHARING_CONFIG, bytes(base_mint)], tc.FEE_PROGRAM_ID
    )[0]


def associated_token_address(
    owner: Pubkey, mint: Pubkey, token_program: Pubkey = tc.TOKEN_PROGRAM_ID
) -> Pubkey:
    return Pubkey.find_program_address(
        [bytes(owner), bytes(token_program), bytes(mint)],
        tc.ASSOCIATED_TOKEN_PROGRAM_ID,
    )[0]


@dataclass(frozen=True)
class V2Accounts:
    """Les 27 comptes de buy_exact_quote_in_v2 (sell_v2 = les 26 mêmes moins
    global_volume_accumulator), dans l'ordre exact de l'IDL officielle."""
    global_: Pubkey
    base_mint: Pubkey
    quote_mint: Pubkey
    base_token_program: Pubkey
    quote_token_program: Pubkey
    associated_token_program: Pubkey
    fee_recipient: Pubkey
    associated_quote_fee_recipient: Pubkey
    buyback_fee_recipient: Pubkey
    associated_quote_buyback_fee_recipient: Pubkey
    bonding_curve: Pubkey
    associated_base_bonding_curve: Pubkey
    associated_quote_bonding_curve: Pubkey
    user: Pubkey
    associated_base_user: Pubkey
    associated_quote_user: Pubkey
    creator_vault: Pubkey
    associated_creator_vault: Pubkey
    sharing_config: Pubkey
    user_volume_accumulator: Pubkey
    associated_user_volume_accumulator: Pubkey
    fee_config: Pubkey
    fee_program: Pubkey
    system_program: Pubkey
    event_authority: Pubkey
    program: Pubkey
    global_volume_accumulator: Optional[Pubkey] = None  # buy uniquement


def derive_v2_accounts(
    base_mint: Pubkey, user: Pubkey, creator: Pubkey, base_token_program: Pubkey,
    for_buy: bool,
    fee_recipient: Pubkey = tc.DEFAULT_FEE_RECIPIENT,
    buyback_fee_recipient: Pubkey = tc.DEFAULT_BUYBACK_FEE_RECIPIENT,
) -> V2Accounts:
    quote_mint = tc.WSOL_MINT
    quote_tp = tc.WSOL_QUOTE_TOKEN_PROGRAM
    bc = bonding_curve_pda(base_mint)
    cv = creator_vault_pda(creator)
    uva = user_volume_accumulator_pda(user)
    return V2Accounts(
        global_=global_pda(),
        base_mint=base_mint,
        quote_mint=quote_mint,
        base_token_program=base_token_program,
        quote_token_program=quote_tp,
        associated_token_program=tc.ASSOCIATED_TOKEN_PROGRAM_ID,
        fee_recipient=fee_recipient,
        associated_quote_fee_recipient=associated_token_address(fee_recipient, quote_mint, quote_tp),
        buyback_fee_recipient=buyback_fee_recipient,
        associated_quote_buyback_fee_recipient=associated_token_address(buyback_fee_recipient, quote_mint, quote_tp),
        bonding_curve=bc,
        associated_base_bonding_curve=associated_token_address(bc, base_mint, base_token_program),
        associated_quote_bonding_curve=associated_token_address(bc, quote_mint, quote_tp),
        user=user,
        associated_base_user=associated_token_address(user, base_mint, base_token_program),
        associated_quote_user=associated_token_address(user, quote_mint, quote_tp),
        creator_vault=cv,
        associated_creator_vault=associated_token_address(cv, quote_mint, quote_tp),
        sharing_config=sharing_config_pda(base_mint),
        user_volume_accumulator=uva,
        associated_user_volume_accumulator=associated_token_address(uva, quote_mint, quote_tp),
        fee_config=fee_config_pda(),
        fee_program=tc.FEE_PROGRAM_ID,
        system_program=tc.SYSTEM_PROGRAM_ID,
        event_authority=event_authority_pda(),
        program=tc.PUMP_FUN_PROGRAM_ID,
        global_volume_accumulator=global_volume_accumulator_pda() if for_buy else None,
    )
