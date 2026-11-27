"""The bankroll ledger: every balance you report (on the website or to the Telegram assistant), the VAULT and the
high-water mark. One ledger, so both places always agree on what you have and what to set aside.

THE SAVINGS RULE (betting.settle, alpha = BITCOIN_BET_VAULT_SHARE, default 0.5): whenever a win lifts your balance
ABOVE its previous best, half of the NEW part goes to the vault and is never bet again. A win that only recovers an
earlier loss saves nothing. Example: best so far $10; you win and now have $14 -> set aside $2 (half of the $4 above
$10), keep betting with the rest; the new best is $14.
"""
from __future__ import annotations

import psycopg

from app.bitcoin import betting
from app.config import Settings
from app.db import get_pool


def latest() -> dict | None:
    """The newest bankroll state: the ledger, or the website's older bet check-ins if the ledger is still empty."""
    row = None
    try:
        with get_pool().connection() as conn:
            row = conn.execute("select balance, active, vault, peak, swept, pnl, created_at, source from bankroll order by created_at desc, id desc limit 1").fetchone()
    except psycopg.errors.UndefinedTable:  # migration 14 not applied yet: the website keeps working on its own table
        pass
    if row is None:
        with get_pool().connection() as conn:
            row = conn.execute("select balance, active, vault, peak, swept, pnl, created_at, 'web' as source from bitcoin_bets order by created_at desc limit 1").fetchone()
    return None if row is None else {k: (float(v) if k in ("balance", "active", "vault", "peak", "swept") else
                                         (None if v is None else float(v)) if k == "pnl" else v) for k, v in row.items()}


def settle(balance: float, s: Settings, reset: bool = False) -> dict:
    """What reporting this balance does to the bankroll, without storing it."""
    prev = None if reset else latest()
    if prev is None:
        return {"balance": round(balance, 2), "active": round(balance, 2), "vault": 0.0, "peak": round(balance, 2), "swept": 0.0,
                "pnl": None, "is_reset": True}
    active, vault, peak, swept = betting.settle(balance, prev["vault"], prev["peak"], s.bitcoin_bet_vault_share)
    return {"balance": round(balance, 2), "active": active, "vault": vault, "peak": peak, "swept": swept,
            "pnl": round(balance - prev["balance"], 2), "is_reset": False}


def record(balance: float, s: Settings, *, source: str = "web", reset: bool = False, note: str | None = None) -> dict:
    return store(settle(balance, s, reset), source=source, note=note)


def store(st: dict, *, source: str, note: str | None = None) -> dict:
    """Write an already-settled state (the website's check-in computes it itself)."""
    with get_pool().connection() as conn:
        row = conn.execute(
            """insert into bankroll (balance, active, vault, peak, swept, pnl, source, is_reset, note)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id, created_at""",
            (st["balance"], st["active"], st["vault"], st["peak"], st["swept"], st["pnl"], source, st["is_reset"], note)).fetchone()
    return {**st, "id": row["id"], "created_at": row["created_at"]}


def explain(st: dict, alpha: float) -> str:
    """The bankroll in plain words, with the one action that matters: how much to set aside now."""
    lines = [f"Balance ${st['balance']:.2f}: ${st['active']:.2f} to bet with, ${st['vault']:.2f} saved in the vault."]
    if st.get("swept"):
        lines.append(f"New best! Set aside ${st['swept']:.2f} now ({alpha * 100:.0f}% of the gain above your previous best) "
                     "and do not bet it again.")
    elif st.get("pnl") is not None and st["pnl"] > 0:
        lines.append(f"Up ${st['pnl']:.2f}, but still below your best (${st['peak']:.2f}): nothing to set aside yet.")
    elif st.get("pnl") is not None and st["pnl"] < 0:
        lines.append(f"Down ${-st['pnl']:.2f} since last time: nothing to set aside, and the vault stays untouched.")
    return " ".join(lines)
