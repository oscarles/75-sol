"""
Constantes protocole pump.fun / PumpSwap (pAMM) — portées de Bundle10k/constants.py
(vérifiées le 2026-08-17 contre l'IDL officielle pump.fun). Toute divergence
future du protocole cassera les `assert` ci-dessous plutôt que de silencieusement
construire des transactions invalides.
"""
import hashlib

from solders.pubkey import Pubkey

PUMP_FUN_PROGRAM_ID = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
FEE_PROGRAM_ID = Pubkey.from_string("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022_PROGRAM_ID = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM_PROGRAM_ID = Pubkey.default()
WSOL_MINT = Pubkey.from_string("So11111111111111111111111111111111111111112")

JITO_TIP_ACCOUNT = Pubkey.from_string("96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5")
JITO_URLS = [
    "https://mainnet.block-engine.jito.wtf/api/v1/transactions",
    "https://ny.mainnet.block-engine.jito.wtf/api/v1/transactions",
    "https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/transactions",
    "https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/transactions",
]


def _disc(namespace: str, name: str) -> bytes:
    return hashlib.sha256(f"{namespace}:{name}".encode()).digest()[:8]


BONDING_CURVE_ACCOUNT_DISC = _disc("account", "BondingCurve")
CREATE_EVENT_DISC = _disc("event", "CreateEvent")
TRADE_EVENT_DISC = _disc("event", "TradeEvent")

# Historique (cf. Bundle10k) : les instructions "legacy" buy/buy_exact_sol_in/sell
# échouent aujourd'hui on-chain avec l'erreur programme #6062
# "BuybackFeeRecipientMissing" — vérifié par échec réel, pas par supposition. Les
# instructions V2 sont la seule voie confirmée fonctionnelle.
BUY_EXACT_QUOTE_IN_V2_DISC = _disc("global", "buy_exact_quote_in_v2")
SELL_V2_DISC = _disc("global", "sell_v2")
PAMM_SELL_DISC = _disc("global", "sell")  # même convention Anchor pour PumpSwap
PAMM_BUY_DISC = _disc("global", "buy")

assert BONDING_CURVE_ACCOUNT_DISC == bytes([23, 183, 248, 55, 96, 216, 172, 96])
assert TRADE_EVENT_DISC == bytes([189, 219, 127, 211, 78, 230, 97, 238])
assert CREATE_EVENT_DISC == bytes([27, 114, 169, 77, 222, 235, 99, 118])
assert BUY_EXACT_QUOTE_IN_V2_DISC == bytes([194, 171, 28, 70, 104, 77, 91, 47])
assert SELL_V2_DISC == bytes([93, 246, 130, 60, 231, 233, 64, 178])

# ── Seeds PDA (bonding curve) ────────────────────────────────────────────────
SEED_GLOBAL = b"global"
SEED_BONDING_CURVE = b"bonding-curve"
SEED_CREATOR_VAULT = b"creator-vault"
SEED_EVENT_AUTHORITY = b"__event_authority"
SEED_GLOBAL_VOLUME_ACCUMULATOR = b"global_volume_accumulator"
SEED_USER_VOLUME_ACCUMULATOR = b"user_volume_accumulator"
SEED_FEE_CONFIG = b"fee_config"
SEED_SHARING_CONFIG = b"sharing-config"
# 2e seed du PDA fee_config — constante littérale de l'IDL (PDA sous FEE_PROGRAM_ID).
FEE_CONFIG_SEED_2 = bytes([
    1, 86, 224, 246, 147, 102, 90, 207, 68, 219, 21, 104, 191, 23,
    91, 170, 81, 137, 203, 151, 245, 210, 255, 59, 101, 93, 43,
    182, 253, 109, 24, 176,
])

# ── Layout du compte BondingCurve (Borsh, 115 octets) ────────────────────────
#   0   discriminator            8 bytes
#   8   virtual_token_reserves   u64
#   16  virtual_quote_reserves   u64  (= virtual_sol_reserves pour un coin pair SOL)
#   24  real_token_reserves      u64
#   32  real_quote_reserves      u64
#   40  token_total_supply       u64
#   48  complete                 bool  ← signal de migration vers pAMM
#   49  creator                  pubkey (32 bytes)
BONDING_CURVE_OFFSET_VIRTUAL_TOKEN_RESERVES = 8
BONDING_CURVE_OFFSET_VIRTUAL_QUOTE_RESERVES = 16
BONDING_CURVE_OFFSET_REAL_TOKEN_RESERVES = 24
BONDING_CURVE_OFFSET_REAL_QUOTE_RESERVES = 32
BONDING_CURVE_OFFSET_TOKEN_TOTAL_SUPPLY = 40
BONDING_CURVE_OFFSET_COMPLETE = 48
BONDING_CURVE_OFFSET_CREATOR = 49
BONDING_CURVE_ACCOUNT_LEN = 115

PUMP_SUPPLY = 1_000_000_000  # supply fixe pump.fun (1 milliard, 6 décimales)
TOKEN_DECIMALS = 6
SOL_DECIMALS = 9

WSOL_QUOTE_TOKEN_PROGRAM = TOKEN_PROGRAM_ID  # WSOL utilise toujours le token program classique

# ── PumpSwap (pAMM) — post-migration ─────────────────────────────────────────
PAMM_PROGRAM_ID = Pubkey.from_string("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA")

# 8 fee recipients "normaux" + 8 buyback fee recipients (cf. idl/pump.json) — le
# protocole n'impose pas lequel utiliser, juste qu'il soit dans la liste. Choix
# arbitraire (indice 0).
FEE_RECIPIENTS = [
    Pubkey.from_string(a) for a in (
        "62qc2CNXwrYqQScmEdiZFFAnJR262PxWEuNQtxfafNgV",
        "7VtfL8fvgNfhz17qKRMjzQEXgbdpnHHHQRh54R9jP2RJ",
        "7hTckgnGnLQR6sdH7YkqFTAA7VwTfYFaZ6EhEsU3saCX",
        "9rPYyANsfQZw3DnDmKE3YCQF5E8oD89UXoHn9JFEhJUz",
        "AVmoTthdrX6tKt4nDjco2D775W2YK3sDhxPcMmzUAmTY",
        "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM",
        "FWsW1xNtWscwNmKv6wVsU1iTzRN6wmmk3MjxRP5tT7hz",
        "G5UZAVbAf46s7cKWoyKu8kYTip9DGTpbLZ2qa9Aq69dP",
    )
]
BUYBACK_FEE_RECIPIENTS = [
    Pubkey.from_string(a) for a in (
        "5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD",
        "9M4giFFMxmFGXtc3feFzRai56WbBqehoSeRE5GK7gf7",
        "GXPFM2caqTtQYC2cJ5yJRi9VDkpsYZXzYdwYpGnLmtDL",
        "3BpXnfJaUTiwXnJNe7Ej1rcbzqTTQUvLShZaWazebsVR",
        "5cjcW9wExnJJiqgLjq7DEG75Pm6JBgE1hNv4B2vHXUW6",
        "EHAAiTxcdDwQ3U4bU6YcMsQGaekdzLS3B5SmYo46kJtL",
        "5eHhjP8JaYkz83CWwvGU2uMUXefd3AazWGx4gpcuEEYD",
        "A7hAgCzFw14fejgCp387JUJRMNyz4j89JKnhtKU8piqW",
    )
]
DEFAULT_FEE_RECIPIENT = FEE_RECIPIENTS[0]
DEFAULT_BUYBACK_FEE_RECIPIENT = BUYBACK_FEE_RECIPIENTS[0]
