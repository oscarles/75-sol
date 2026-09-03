"""Envoi de l'alerte « dev-buy 70–80 SOL détecté » sur le webhook Discord."""

import asyncio
import datetime as _dt

import aiohttp

from config import (
    DISCORD_WEBHOOK_URL, DEV_BUY_MIN_SOL, DEV_BUY_MAX_SOL,
    JOKE_PING_ENABLED, JOKE_PING_USER_ID, JOKE_PING_NAME, JOKE_GIF_URL,
)


def _short(addr: str) -> str:
    return f"{addr[:4]}…{addr[-4:]}" if len(addr) > 8 else addr


def _joke_content(name: str, symbol: str, dev_buy_sol: float) -> str:
    """Ping + « ALERTE », pour un pote qui rate les notifs.

    `JOKE_GIF_URL` (optionnel) ajoute un GIF sous la ligne s'il est renseigné."""
    if not JOKE_PING_ENABLED:
        return ""
    who = f"<@{JOKE_PING_USER_ID}>" if JOKE_PING_USER_ID else JOKE_PING_NAME
    content = f"# 🚨 ALERTE {who}"
    if JOKE_GIF_URL:
        content += f"\n{JOKE_GIF_URL}"
    return content


async def _post(session: aiohttp.ClientSession, payload: dict, retries: int = 3) -> bool:
    delay = 1.0
    for attempt in range(1, retries + 1):
        try:
            async with session.post(
                DISCORD_WEBHOOK_URL, json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status in (200, 204):
                    return True
                if resp.status == 429:
                    try:
                        body = await resp.json(content_type=None)
                        delay = max(delay, float(body.get("retry_after", delay)))
                    except Exception:
                        pass
                else:
                    print(f"[Discord] Erreur {resp.status} : {(await resp.text())[:200]}")
        except Exception as e:
            print(f"[Discord] Exception (tentative {attempt}/{retries}) : {e}")
        if attempt < retries:
            await asyncio.sleep(delay)
            delay *= 2
    print("[Discord] Abandon de l'alerte après retries")
    return False


async def send_dev_buy_alert(
    mint: str,
    name: str,
    symbol: str,
    creator: str,
    dev_buy_sol: float,
    dev_buy_count: int,
    age_sec: float,
    create_sig: str = "",
    sol_price_usd: float = 0.0,
) -> None:
    if not DISCORD_WEBHOOK_URL:
        print("[Discord] DISCORD_WEBHOOK_URL vide — alerte non envoyée")
        return

    usd = f"\n`≈ ${dev_buy_sol * sol_price_usd:,.0f}`" if sol_price_usd else ""
    split = "" if dev_buy_count <= 1 else f" `({dev_buy_count} ordres)`"
    tx_line = (
        f" • [tx création](https://solscan.io/tx/{create_sig})" if create_sig else ""
    )

    embed = {
        "title": "🐳 Dev-buy 70–80 SOL détecté",
        "color": 0x1ABC9C,
        "description": f"**{name}** ({symbol})\n`{mint}`",
        "fields": [
            {
                "name": "💸 Achat du dev",
                "value": f"**{dev_buy_sol:.2f} SOL**{split}{usd}\n"
                         f"`fenêtre {DEV_BUY_MIN_SOL:g} – {DEV_BUY_MAX_SOL:g} SOL`",
                "inline": True,
            },
            {
                "name": "⚡ Délai",
                "value": f"**{age_sec:.1f} s** après création",
                "inline": True,
            },
            {
                "name": "👤 Wallet dev",
                "value": f"[`{_short(creator)}`](https://solscan.io/account/{creator})",
                "inline": True,
            },
            {
                "name": "🔗 Liens",
                "value": (
                    f"[pump.fun](https://pump.fun/coin/{mint}) • "
                    f"[DexScreener](https://dexscreener.com/solana/{mint}) • "
                    f"[Solscan](https://solscan.io/token/{mint})"
                    f"{tx_line}"
                ),
                "inline": False,
            },
        ],
        "footer": {"text": "75 SOL — dev-buy scanner pump.fun"},
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }

    payload = {"username": "75 SOL Scanner", "embeds": [embed]}
    content = _joke_content(name, symbol, dev_buy_sol)
    if content:
        payload["content"] = content
        payload["allowed_mentions"] = {"parse": ["users"]}
    async with aiohttp.ClientSession() as session:
        await _post(session, payload)
