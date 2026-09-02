"""
Logs vers un webhook Discord dédié (séparé du webhook d'alertes).

`log()` ne fait qu'un print + un append en mémoire — aucune I/O réseau sur le
chemin critique. Une tâche de fond (`log_flush_loop`) vide le buffer toutes les
FLUSH_INTERVAL secondes et envoie les lignes accumulées en messages groupés.

Si DISCORD_LOG_WEBHOOK_URL n'est pas défini, `log()` se contente d'afficher.
"""

import asyncio
import sys
import time
from collections import deque

import aiohttp

from config import DISCORD_LOG_WEBHOOK_URL

# Console Windows (cp1252) : éviter tout crash d'encodage sur les caractères
# accentués / flèches présents dans les logs.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _safe_print(line: str) -> None:
    try:
        print(line)
    except Exception:
        try:
            enc = sys.stdout.encoding or "utf-8"
            print(line.encode(enc, "replace").decode(enc, "replace"))
        except Exception:
            pass

FLUSH_INTERVAL   = 4.0     # s entre deux envois groupés
MAX_CHUNK_CHARS  = 1900    # marge sous la limite Discord de 2000 caractères
MAX_BUFFER_LINES = 2000    # borne si le webhook reste indisponible longtemps

_buffer: deque = deque()


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')}  {msg}"
    _safe_print(line)
    if DISCORD_LOG_WEBHOOK_URL:
        _buffer.append(line)


def _chunks_from_lines(lines: list[str]) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > MAX_CHUNK_CHARS:
            if current:
                chunks.append(current)
            current = line[:MAX_CHUNK_CHARS]
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


async def log_flush_loop() -> None:
    """Tourne indéfiniment. Ne lève jamais — un échec d'envoi (429, réseau) remet
    les lignes en tête de buffer pour retenter au flush suivant."""
    if not DISCORD_LOG_WEBHOOK_URL:
        return
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            if not _buffer:
                continue

            lines = list(_buffer)
            _buffer.clear()
            chunks = _chunks_from_lines(lines)

            for i, chunk in enumerate(chunks):
                try:
                    async with session.post(
                        DISCORD_LOG_WEBHOOK_URL,
                        json={"content": f"```{chunk}```"},
                        timeout=aiohttp.ClientTimeout(total=8),
                    ) as resp:
                        if resp.status == 429:
                            try:
                                body = await resp.json(content_type=None)
                                await asyncio.sleep(float(body.get("retry_after", 1.0)))
                            except Exception:
                                await asyncio.sleep(1.0)
                            raise RuntimeError("429")
                        if resp.status >= 400:
                            raise RuntimeError(f"HTTP {resp.status}")
                except Exception:
                    for remaining in reversed(chunks[i:]):
                        _buffer.appendleft(remaining)
                    break
                await asyncio.sleep(0.4)

            while len(_buffer) > MAX_BUFFER_LINES:
                _buffer.popleft()
