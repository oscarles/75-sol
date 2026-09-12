"""
Broadcast + confirmation de transaction — porté de Bundle10k/tx_sender.py :
envoi parallèle RPC(s) + Jito, spam de retry pendant la confirmation,
confirmation en course WS + poll HTTP.

Respecte config.DRY_RUN : si actif, construit/logue la tx sans jamais l'envoyer
réellement (sécurité par défaut).
"""
import asyncio
import base64
import json
from typing import Optional

import httpx
import websockets
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts
from solders.signature import Signature
from solders.transaction import VersionedTransaction

import config
import trading_constants as tc
from trading_log import log

_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    """Client HTTP partagé et réutilisé (connexions gardées ouvertes) — un
    nouveau httpx.AsyncClient par appel forcerait une négociation TCP/TLS à
    froid vers chaque endpoint Jito à chaque envoi."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=5.0)
    return _http_client


async def _post_jito_one(url: str, tx_b64: str) -> Optional[Signature]:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
        "params": [tx_b64, {"encoding": "base64", "skipPreflight": True}],
    })
    try:
        r = await _get_http_client().post(url, content=payload, headers={"Content-Type": "application/json"})
        data = r.json()
        if "result" in data:
            return Signature.from_string(data["result"])
        log.debug(f"[JITO] {url} -> HTTP {r.status_code} : {str(data)[:200]}")
    except Exception as e:
        log.debug(f"[JITO] {url} exception : {e}")
    return None


async def broadcast_and_confirm(
    client: AsyncClient,
    extra_clients: list,
    tx: VersionedTransaction,
    log_prefix: str,
    spam_retries: int,
    spam_interval: float,
    confirm_timeout_sec: float,
    rpc_ws: str,
) -> tuple:
    """Retourne (signature | None, error_str). error_str vide = succès confirmé."""
    tx_bytes = bytes(tx)

    if config.DRY_RUN:
        fake_sig = tx.signatures[0]
        log.info(f"[{log_prefix}] DRY_RUN actif - tx NON envoyee ({len(tx_bytes)} bytes, sig locale {fake_sig})")
        return fake_sig, ""

    opts = TxOpts(skip_preflight=True, preflight_commitment=Confirmed)
    rpc_clients = [(client, "RPC1")] + [(c2, f"RPC{i + 2}") for i, c2 in enumerate(extra_clients)]
    tx_b64 = base64.b64encode(tx_bytes).decode()

    async def _send_all() -> tuple:
        """Course entre tous les canaux d'envoi (RPC(s) + les 4 endpoints Jito
        individuellement) — renvoie dès le PREMIER succès sans attendre les
        autres."""
        task_labels: dict = {}
        for c2, label in rpc_clients:
            t = asyncio.create_task(c2.send_raw_transaction(tx_bytes, opts=opts))
            task_labels[t] = label
        for url in tc.JITO_URLS:
            t = asyncio.create_task(_post_jito_one(url, tx_b64))
            task_labels[t] = f"Jito-{url.split('//')[1].split('.')[0]}"

        errors = []
        pending = set(task_labels.keys())
        winner_sig, winner_label = None, None
        while pending and winner_sig is None:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                label = task_labels[t]
                try:
                    r = t.result()
                except Exception as e:
                    errors.append(f"{label}: {type(e).__name__}: {str(e)[:120]}")
                    continue
                sig_val = r.value if hasattr(r, "value") else r
                if sig_val is not None:
                    winner_sig, winner_label = sig_val, label
                    break
                errors.append(f"{label}: pas de signature retournee")
        for t in pending:
            t.cancel()

        if winner_sig is not None:
            log.info(f"[{log_prefix}] Tx envoyee via {winner_label}")
            return winner_sig, ""
        return None, " | ".join(errors) or "Tous les envois ont echoue"

    sig, send_error = await _send_all()
    if sig is None:
        log.error(f"[{log_prefix}] Tous les RPC ont echoue - {send_error}")
        return None, send_error or "Tous les RPC ont echoue"

    log.info(f"[{log_prefix}] Tx broadcast : {sig}")
    done = asyncio.Event()

    async def _spam_loop() -> None:
        for _ in range(spam_retries):
            await asyncio.sleep(spam_interval)
            if done.is_set():
                return
            await _send_all()

    spam_task = asyncio.create_task(_spam_loop())

    async def _confirm_ws() -> tuple:
        if not rpc_ws:
            return False, ""
        try:
            async with websockets.connect(rpc_ws, open_timeout=5, ping_interval=None) as ws:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "signatureSubscribe",
                    "params": [str(sig), {"commitment": "confirmed"}],
                }))
                ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                if "error" in ack:
                    return False, ""
                deadline = asyncio.get_event_loop().time() + confirm_timeout_sec
                while asyncio.get_event_loop().time() < deadline:
                    rem = max(1.0, deadline - asyncio.get_event_loop().time())
                    try:
                        data = json.loads(await asyncio.wait_for(ws.recv(), timeout=rem))
                    except asyncio.TimeoutError:
                        break
                    if data.get("method") == "signatureNotification":
                        val = data["params"]["result"]["value"]
                        if isinstance(val, dict) and val.get("err"):
                            return True, f"Rejete on-chain: `{val['err']}`"
                        log.info(f"[{log_prefix}] Tx CONFIRMEE (WS) : {sig}")
                        return True, ""
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        return False, ""

    async def _confirm_poll() -> tuple:
        deadline = asyncio.get_event_loop().time() + confirm_timeout_sec
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.4)
            try:
                statuses = await client.get_signature_statuses([sig], search_transaction_history=True)
                st = statuses.value[0] if statuses.value else None
                if st and st.err:
                    return True, f"Rejete on-chain: `{st.err}`"
                if st and st.confirmation_status in ("confirmed", "finalized"):
                    log.info(f"[{log_prefix}] Tx CONFIRMEE (poll) : {sig}")
                    return True, ""
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        return False, ""

    confirmed, onchain_error = False, ""
    pending = {asyncio.create_task(_confirm_ws()), asyncio.create_task(_confirm_poll())}
    while pending and not confirmed and not onchain_error:
        finished, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for t in finished:
            try:
                cf, er = t.result()
                if cf or er:
                    confirmed, onchain_error = cf, er
            except Exception:
                pass
    for t in pending:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    if not confirmed and not onchain_error:
        for attempt in range(3):
            try:
                tx_resp = await client.get_transaction(
                    sig, max_supported_transaction_version=0, commitment=Confirmed,
                )
                if tx_resp.value is not None:
                    meta = tx_resp.value.transaction.meta
                    if meta and meta.err:
                        onchain_error = f"Rejete on-chain: `{meta.err}`"
                    else:
                        confirmed = True
                    break
            except Exception:
                pass
            if attempt < 2:
                await asyncio.sleep(2.0)

    done.set()
    spam_task.cancel()
    try:
        await spam_task
    except (asyncio.CancelledError, Exception):
        pass

    if onchain_error:
        return sig, onchain_error
    if not confirmed:
        return sig, f"Non confirmee apres {confirm_timeout_sec:.0f}s (peut encore passer)"
    return sig, ""
