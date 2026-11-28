"""Keep secrets out of logs. The HTTP client logs every request URL at INFO, and Telegram's API puts the bot token in
the URL (https://api.telegram.org/bot<id>:<secret>/sendMessage), so without this the token lands in `docker logs`."""
from __future__ import annotations

import logging
import re

_TELEGRAM = re.compile(r"(/bot)\d+:[A-Za-z0-9_-]{20,}")


def redact(text: str) -> str:
    return _TELEGRAM.sub(r"\1<redacted>", text)


class Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        return True


def install() -> None:
    """Attach the filter to every root handler (records from all loggers, httpx included, pass through them)."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, Redact) for f in handler.filters):
            handler.addFilter(Redact())
