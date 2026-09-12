"""
Petit adaptateur pour réutiliser telles quelles les modules portés depuis
Bundle10k (qui appellent `log.info(...)` / `log.warning(...)` / `log.error(...)`
/ `log.debug(...)`) par-dessus le système de log existant du projet
(`discord_log.log(msg)` — une seule fonction, pas de niveaux, bufferisée vers un
webhook Discord dédié). `debug()` n'écrit qu'en console (pas de spam Discord).
"""
from discord_log import log as _log_line
from discord_log import _safe_print as _console


class _Log:
    def info(self, msg: str) -> None:
        _log_line(msg)

    def warning(self, msg: str) -> None:
        _log_line(msg)

    def error(self, msg: str) -> None:
        _log_line(msg)

    def debug(self, msg: str) -> None:
        _console(msg)


log = _Log()
