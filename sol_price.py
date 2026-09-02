"""Prix SOL/USD — CoinGecko (API publique gratuite, aucune clé). Cache 5 min.

Sert uniquement à afficher la contre-valeur USD du dev-buy dans l'alerte.
Si l'appel échoue, on garde la dernière valeur connue (défaut 170).
"""

import time

import aiohttp

_price: float = 170.0
_last: float = 0.0
_TTL = 300.0


async def get_sol_price_usd(session: aiohttp.ClientSession) -> float:
    global _price, _last
    if time.time() - _last < _TTL:
        return _price
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
            data = await resp.json(content_type=None)
        _price = float(data["solana"]["usd"])
        _last = time.time()
    except Exception:
        pass
    return _price
