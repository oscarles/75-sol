"""
Parsing des events on-chain pump.fun émis dans les logs sous la forme
`Program data: <base64>` (via emit_cpi!).

On ne garde que :
  - CreateEvent → détection d'une nouvelle création de coin (mint + wallet dev)
  - TradeEvent  → montant en SOL de chaque achat/vente (pour repérer le dev-buy)
"""

import base64
import hashlib
import struct
from dataclasses import dataclass
from typing import Optional

import base58


def _discriminator(event_name: str) -> bytes:
    return hashlib.sha256(f"event:{event_name}".encode()).digest()[:8]


DISC_CREATE = _discriminator("CreateEvent")
DISC_TRADE  = _discriminator("TradeEvent")


@dataclass
class CreateEvent:
    name:          str
    symbol:        str
    uri:           str
    mint:          str  # base58
    bonding_curve: str  # base58
    creator:       str  # base58 — wallet dev (signataire de la création)


@dataclass
class TradeEvent:
    mint:                   str
    sol_amount:             int   # lamports échangés sur ce trade
    token_amount:           int
    is_buy:                 bool
    user:                   str   # base58 — auteur du trade
    timestamp:              int
    virtual_sol_reserves:   int
    virtual_token_reserves: int


def _read_pubkey(data: bytes, offset: int) -> tuple[str, int]:
    return base58.b58encode(data[offset:offset + 32]).decode(), offset + 32


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    length = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    text = data[offset:offset + length].decode("utf-8", errors="replace")
    return text, offset + length


def _read_u64(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from("<Q", data, offset)[0], offset + 8


def _read_i64(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from("<q", data, offset)[0], offset + 8


def parse_log_line(log_line: str) -> Optional[bytes]:
    """Extrait les bytes bruts d'une ligne `Program data: <base64>`."""
    if not log_line.startswith("Program data:"):
        return None
    b64 = log_line.split("Program data: ", 1)[1].strip()
    try:
        return base64.b64decode(b64)
    except Exception:
        return None


def try_parse_create(data: bytes) -> Optional[CreateEvent]:
    if len(data) < 8 or data[:8] != DISC_CREATE:
        return None
    try:
        offset = 8
        name,    offset = _read_string(data, offset)
        symbol,  offset = _read_string(data, offset)
        uri,     offset = _read_string(data, offset)
        mint,    offset = _read_pubkey(data, offset)
        bc,      offset = _read_pubkey(data, offset)
        creator, _      = _read_pubkey(data, offset)
        return CreateEvent(name, symbol, uri, mint, bc, creator)
    except Exception:
        return None


def try_parse_trade(data: bytes) -> Optional[TradeEvent]:
    if len(data) < 8 or data[:8] != DISC_TRADE:
        return None
    try:
        offset = 8
        mint,      offset = _read_pubkey(data, offset)
        sol_amt,   offset = _read_u64(data, offset)
        tok_amt,   offset = _read_u64(data, offset)
        is_buy = bool(data[offset]); offset += 1
        user,      offset = _read_pubkey(data, offset)
        ts,        offset = _read_i64(data, offset)
        v_sol,     offset = _read_u64(data, offset)
        v_tok,     _      = _read_u64(data, offset)
        return TradeEvent(mint, sol_amt, tok_amt, is_buy, user, ts, v_sol, v_tok)
    except Exception:
        return None
