"""Lecture + parsing du compte BondingCurve — un seul getAccountInfo donne
réserves + creator + statut de migration. Porté de Bundle10k/bonding_curve_state.py."""
import struct
from dataclasses import dataclass
from typing import Optional

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Processed
from solders.pubkey import Pubkey

import pda
import trading_constants as tc


@dataclass
class BondingCurveState:
    virtual_token_reserves: int
    virtual_sol_reserves: int  # = virtual_quote_reserves (coin pair SOL)
    real_token_reserves: int
    real_sol_reserves: int
    token_total_supply: int
    complete: bool
    creator: Pubkey


def parse_bonding_curve(data: bytes) -> Optional[BondingCurveState]:
    if len(data) < tc.BONDING_CURVE_ACCOUNT_LEN:
        return None
    if data[:8] != tc.BONDING_CURVE_ACCOUNT_DISC:
        return None
    (vtr,) = struct.unpack_from("<Q", data, tc.BONDING_CURVE_OFFSET_VIRTUAL_TOKEN_RESERVES)
    (vsr,) = struct.unpack_from("<Q", data, tc.BONDING_CURVE_OFFSET_VIRTUAL_QUOTE_RESERVES)
    (rtr,) = struct.unpack_from("<Q", data, tc.BONDING_CURVE_OFFSET_REAL_TOKEN_RESERVES)
    (rsr,) = struct.unpack_from("<Q", data, tc.BONDING_CURVE_OFFSET_REAL_QUOTE_RESERVES)
    (supply,) = struct.unpack_from("<Q", data, tc.BONDING_CURVE_OFFSET_TOKEN_TOTAL_SUPPLY)
    complete = data[tc.BONDING_CURVE_OFFSET_COMPLETE] != 0
    creator = Pubkey.from_bytes(data[tc.BONDING_CURVE_OFFSET_CREATOR:tc.BONDING_CURVE_OFFSET_CREATOR + 32])
    return BondingCurveState(
        virtual_token_reserves=vtr, virtual_sol_reserves=vsr,
        real_token_reserves=rtr, real_sol_reserves=rsr,
        token_total_supply=supply, complete=complete, creator=creator,
    )


async def fetch_bonding_curve(
    client: AsyncClient, mint: Pubkey, commitment=Processed,
) -> Optional[BondingCurveState]:
    bc_pda = pda.bonding_curve_pda(mint)
    try:
        resp = await client.get_account_info(bc_pda, commitment=commitment)
    except Exception:
        return None
    if resp.value is None:
        return None
    return parse_bonding_curve(bytes(resp.value.data))
