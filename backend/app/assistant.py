"""The Telegram assistant: your phone's side of the betting system.

It pings you (5m/15m signals, the morning plan), and you answer in plain messages:
    12.40          your balance now: the ledger updates and it tells you how much to set aside (the vault rule)
    in / in 2.50   you placed the bet it just suggested (with its stake, or this amount)
    skip           you did not
    /status /daily /bets /pause /resume /reset 20 /help
Anything else goes to Claude (your subscription), which answers from the same numbers and cannot place bets.
When a window you bet on resolves, it tells you whether you won and asks for your new balance.

Only the chat in TELEGRAM_CHAT_ID can use it. Until that is set, the bot answers anyone with just their chat id, so you
can copy yours into .env. The bot token lives only in .env (from @BotFather), never in the code or the chat.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone

import httpx
from psycopg.types.json import Jsonb

from app.bitcoin import bankroll
from app.config import Settings
from app.db import get_pool

log = logging.getLogger(__name__)
API = "https://api.telegram.org"
NUM = r"\$?\s*(\d+(?:[.,]\d{1,2})?)\s*\$?"
HELP = """I watch Polymarket's BTC 5-minute and 15-minute Up/Down markets and the daily 'Bitcoin above' market, and ping you when the numbers say bet.

Reply with:
- your balance, e.g. 12.40: I update your bankroll and tell you how much to set aside
- in (or in 2.50): you placed the bet I just suggested
- skip: you did not
- /status: bankroll, model, last signals
- /daily: today's daily plan now
- /bets: your open bets
- /pause, /resume: stop or restart pings
- /reset 20: start the bankroll fresh after a deposit or withdrawal
Anything else: ask me in plain words.

PAPER signals come from a model that has not proven itself yet: practice only, no real money."""


# ----------------------------------------------------------------------------------------------- state
def get_state(key: str):
    with get_pool().connection() as conn:
        r = conn.execute("select value from assistant_state where key = %s", (key,)).fetchone()
    return r["value"] if r else None


def set_state(key: str, value) -> None:
    with get_pool().connection() as conn:
        if value is None:
            conn.execute("delete from assistant_state where key = %s", (key,))
        else:
            conn.execute("""insert into assistant_state (key, value) values (%s, %s)
                            on conflict (key) do update set value = excluded.value, updated_at = now()""", (key, Jsonb(value)))


# ----------------------------------------------------------------------------------------------- Telegram
class Bot:
    def __init__(self, s: Settings, http: httpx.Client | None = None) -> None:
        self.s = s
        self.base = f"{API}/bot{s.telegram_bot_token}"
        self.http = http or httpx.Client(timeout=40)

    def send(self, chat_id: str | int, text: str) -> None:
        r = self.http.post(f"{self.base}/sendMessage", json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True})
        r.raise_for_status()

    def notify(self, text: str) -> None:
        if self.s.telegram_chat_id:
            self.send(self.s.telegram_chat_id, text)

    def notify_signal(self, text: str, payload: dict) -> None:
        if get_state("paused"):
            return
        if payload.get("proven"):
            set_state("pending", {k: payload[k] for k in ("id", "slug", "side", "cost", "stake", "end", "label")} | {"kind": "updown"})
        self.notify(text)

    def updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        r = self.http.get(f"{self.base}/getUpdates", params={"timeout": timeout, **({"offset": offset} if offset else {})}, timeout=timeout + 10)
        r.raise_for_status()
        return r.json().get("result", [])


def from_settings(s: Settings, http: httpx.Client | None = None) -> Bot | None:
    return Bot(s) if s.telegram_bot_token else None


def poll_forever(s: Settings, bot: Bot, stop: threading.Event) -> None:
    offset = get_state("offset")
    while not stop.is_set():
        try:
            for u in bot.updates(offset):
                offset = u["update_id"] + 1
                set_state("offset", offset)
                msg = u.get("message") or u.get("edited_message")
                if not msg or "text" not in msg:
                    continue
                chat = str(msg["chat"]["id"])
                if not s.telegram_chat_id:
                    bot.send(chat, f"Hi! Your chat id is {chat}. Put TELEGRAM_CHAT_ID={chat} in the server's .env and restart the "
                                   "updown service; after that I only talk to this chat.")
                    continue
                if chat != str(s.telegram_chat_id):
                    continue  # strangers get no answer at all
                try:
                    reply = handle(s, msg["text"])
                except Exception:  # noqa: BLE001
                    log.exception("assistant could not handle %r", msg["text"][:80])
                    reply = "Something went wrong on my side. Try again, or send /help."
                if reply:
                    bot.send(chat, reply)
        except httpx.HTTPError as exc:
            log.warning("telegram polling: %r", exc)
            stop.wait(5)


# ----------------------------------------------------------------------------------------------- conversation
def _num(v: str) -> float:
    return float(v.replace(",", "."))


def handle(s: Settings, text: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    t = text.strip().lower()
    if t in ("/start", "/help", "help", "?"):
        return HELP
    if t in ("/status", "status"):
        return status_text(s)
    if t in ("/daily", "daily", "plan", "/plan"):
        from app import polymarket as pm
        from app.updown import daily
        with pm.client(s) as http:
            return daily.text(s, daily.refresh(s, http))
    if t in ("/pause", "pause", "stop"):
        set_state("paused", True)
        return "Paused: no pings until you send /resume. Your bankroll and bets are kept."
    if t in ("/resume", "resume", "start"):
        set_state("paused", None)
        return "Pings are back on."
    if t in ("/bets", "bets"):
        return bets_text()
    if m := re.fullmatch(rf"/?reset\s+{NUM}", t):
        st = bankroll.record(_num(m.group(1)), s, source="telegram", reset=True, note="reset from Telegram")
        return f"Fresh start: ${st['balance']:.2f}, all of it active, vault at $0. Your best is now ${st['peak']:.2f}."
    if m := re.fullmatch(rf"(?:/?balance|/?bal|i have|balance is|now)?\s*{NUM}", t):
        st = bankroll.record(_num(m.group(1)), s, source="telegram", note="reported in Telegram")
        if st["is_reset"]:
            return f"Got it: starting your bankroll at ${st['balance']:.2f}. That is also your best so far; half of every gain above it will be set aside."
        return bankroll.explain(st, s.bitcoin_bet_vault_share)
    if m := re.fullmatch(rf"(?:in|placed|done|bet|ok|yes)(?:\s+{NUM})?", t):
        return place(s, m.group(1), now)
    if t in ("skip", "no", "pass", "/skip"):
        set_state("pending", None)
        return "Skipped. I will keep watching."
    return ask_claude(s, text)


def place(s: Settings, amount: str | None, now: datetime) -> str:
    p = get_state("pending")
    if not p:
        return "There is no open signal to place. I will ping you when there is one."
    if p["end"] < now.timestamp():
        set_state("pending", None)
        return "That window has already closed, so I did not record the bet."
    stake = _num(amount) if amount else (p["stake"] or s.updown_min_bet)
    with get_pool().connection() as conn:
        conn.execute("insert into bets_placed (market, side, stake, price, signal_id) values (%s,%s,%s,%s,%s)",
                     (p["slug"], p["side"], stake, p["cost"], p.get("id")))
    set_state("pending", None)
    return (f"Noted: ${stake:.2f} on {p['side'].upper()} ({p['label']}) at {p['cost']:.2f}. If it wins you get about "
            f"${stake / p['cost']:.2f} back. I will tell you the result when the window closes.")


def on_resolved(s: Settings, slug: str, outcome: str, label: str, notify) -> None:
    """A window ended: settle the bets you placed on it, and report how the signals on it did."""
    with get_pool().connection() as conn:
        bets = conn.execute("select id, side, stake, price from bets_placed where market = %s and status = 'open'", (slug,)).fetchall()
        sigs = conn.execute("select side, price, proven, notified from updown_signals where slug = %s", (slug,)).fetchall()
        for b in bets:
            won = b["side"] == outcome
            conn.execute("update bets_placed set status = %s, payout = %s, settled_at = now() where id = %s",
                         ("won" if won else "lost", round(float(b["stake"]) / float(b["price"]), 2) if won else 0, b["id"]))
    if bets:
        profit = sum((float(b["stake"]) / float(b["price"]) - float(b["stake"])) if b["side"] == outcome else -float(b["stake"]) for b in bets)
        st = bankroll.latest()
        guess = f" Your balance should now be about ${st['balance'] + profit:.2f}." if st else ""
        notify(f"{label}: {outcome.upper()} won. You {'won' if profit >= 0 else 'lost'} ${abs(profit):.2f}.{guess} "
               "Reply with your exact balance and I will update the savings.")
    elif any(g["notified"] for g in sigs):
        g = next(g for g in sigs if g["notified"])
        won = g["side"] == outcome
        kind = "Signal" if g["proven"] else "Paper signal"
        notify(f"{kind} {g['side'].upper()} for {label}: {'right' if won else 'wrong'} "
               f"({'+' + format(1 / float(g['price']) - 1, '.2f') if won else '-1.00'} per $1).")


def bets_text() -> str:
    with get_pool().connection() as conn:
        rows = conn.execute("select market, side, stake, price, created_at from bets_placed where status = 'open' order by created_at").fetchall()
    if not rows:
        return "No open bets."
    return "Open bets:\n" + "\n".join(f"- ${float(r['stake']):.2f} on {r['side'].upper()} at {float(r['price']):.2f} ({r['market']})" for r in rows)


def status_text(s: Settings) -> str:
    from app.updown import live
    st = bankroll.latest()
    info = live.status(s)
    lines = [bankroll.explain(st | {"swept": 0, "pnl": None}, s.bitcoin_bet_vault_share) if st else "No balance yet: reply with your balance, e.g. 12.40."]
    lines.append("Live loop: running." if info["running"] else "Live loop: NOT running (check the updown container).")
    lines.append("Model: " + (info["model"]["summary"] if info["model"] else "not trained yet."))
    r = info["record"]
    if r["settled"]:
        lines.append(f"Signals in the last 30 days: {r['wins']} right of {r['settled']}, {r['pnl_per_dollar']:+.2f} per $1.")
    if get_state("paused"):
        lines.append("Pings are PAUSED (send /resume).")
    return "\n".join(lines)


def ask_claude(s: Settings, question: str) -> str:
    """Free questions go to Claude with the current numbers. It explains; it never places bets or promises profit."""
    from app.curriculum.llm import make_llm
    from app.updown import live

    try:
        info = live.status(s)
        st = bankroll.latest()
        context = {"bankroll": st and {k: st[k] for k in ("balance", "active", "vault", "peak")}, "pending": get_state("pending"),
                   "model": info["model"] and {"summary": info["model"]["summary"], "proven": info["model"]["proven"]},
                   "recent_signals": [{k: str(v) for k, v in g.items() if k in ("created_at", "side", "p_model", "price", "proven", "outcome")}
                                      for g in info["signals"][:5]],
                   "daily": info["daily"] and {k: info["daily"].get(k) for k in ("date", "best", "forecast", "error")}, "rule": info["rule"]}
        system = ("You are the betting assistant inside the user's MLE-Sprint app, answering in Telegram. You see their bankroll, the "
                  "short-term BTC model's validation report, recent signals and today's daily plan (JSON below). Answer briefly and "
                  "concretely in plain words (no markdown tables). You cannot place bets. Never promise profit; say plainly when the "
                  "model is not proven. The savings rule: half of every gain above the previous best balance goes to the vault.")
        out = make_llm(s).generate_text(system=system, user=f"<context>{context}</context>\n\nQuestion: {question[:800]}",
                                        check=lambda t: [] if 0 < len(t) <= 2500 else ["answer in under 2,500 characters"],
                                        purpose="Assistant", temperature=0.3, max_tokens=800)
        return out.strip()
    except Exception:  # noqa: BLE001
        log.exception("assistant: Claude answer failed")
        return "I did not understand that, and could not reach Claude just now. Send /help for what I can do."

