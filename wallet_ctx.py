"""Chargement du keypair de trading depuis BOT_PRIVATE_KEY (.env). Porté de
Bundle10k/wallet_ctx.py."""
from dataclasses import dataclass

from solders.keypair import Keypair
from solders.pubkey import Pubkey


@dataclass(frozen=True)
class WalletCtx:
    keypair: Keypair
    wallet: Pubkey


def load_wallet(base58_private_key: str) -> WalletCtx:
    kp = Keypair.from_base58_string(base58_private_key)
    return WalletCtx(keypair=kp, wallet=kp.pubkey())
