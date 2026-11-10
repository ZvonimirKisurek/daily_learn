"use client";

import { useState, useTransition } from "react";
import { AlertTriangle, ArrowDownRight, ArrowUpRight, Ban, Loader2, PauseCircle, Vault, Wallet } from "lucide-react";
import { adviseBetAction, checkBetAction } from "@/app/actions";
import { usd } from "@/lib/bitcoin";
import type { BetAdvice, BetCheck, BetOverview } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Stat, TerminalPanel } from "./terminal-panel";

const cents = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(0)}¢`);
const pc = (v: number | null | undefined, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
/** "0.55" and "55" (cents) both mean 55 cents per share. */
const sharePrice = (raw: string) => { const n = Number(raw.replace(",", ".")); return n > 1 ? n / 100 : n; };
const num = (raw: string) => Number(raw.replace(/[$,\s]/g, ""));
const day = (iso: string) => new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" }).format(new Date(iso));

const field = "w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-sm tabular-nums outline-none transition-colors placeholder:text-white/25 focus:border-emerald-400/50";
const Label = ({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) => (
  <label className="block min-w-0">
    <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-emerald-300/80">{title}</span>
    {children}
    <span className="mt-1 block text-[11px] leading-snug text-muted-foreground">{hint}</span>
  </label>
);

/**
 * The daily bet calculator. You report two things from the prediction site (your balance and the price of the bet);
 * the server does the arithmetic (betting.py): model probability vs market price, quarter-Kelly size with a hard cap,
 * and the vault sweep. It never places a bet: it tells you what the rules say.
 */
export function BetAdvisor({ overview, spot }: { overview: BetOverview; spot: number | null }) {
  const [form, setForm] = useState({ balance: "", strike: "", ask: "", bid: "", hours: String(overview.defaults.hours_to_settle), reset: false });
  const [fresh, setFresh] = useState<BetAdvice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const advice = fresh ?? overview.last;
  const st = advice?.bankroll ?? overview.state;
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    start(async () => {
      const res = await adviseBetAction({
        balance: num(form.balance), strike: num(form.strike), yes_ask: sharePrice(form.ask),
        yes_bid: form.bid.trim() ? sharePrice(form.bid) : undefined, hours_to_settle: form.hours.trim() ? Number(form.hours) : undefined, reset: form.reset,
      });
      if (!res.ok) return setError(res.error);
      setFresh(res.data);
      setForm((f) => ({ ...f, reset: false }));
    });
  }

  return (
    <TerminalPanel title="Bet advisor · daily up/down" meta={`settles ${overview.defaults.settle_label}`}>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <form onSubmit={submit} className="space-y-4">
          <p className="text-sm leading-relaxed text-muted-foreground">
            Once a day, copy two things from the prediction site: <span className="text-foreground">your balance</span> and <span className="text-foreground">the bet&apos;s price</span>.
            You get: bet UP, bet DOWN or no bet, and how much.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Label title="Your balance ($)" hint="Everything you have on the prediction site right now.">
              <input className={field} inputMode="decimal" required placeholder="200" value={form.balance} onChange={set("balance")} />
            </Label>
            <Label title="Target BTC price ($)" hint={`The bet: "BTC above this price at settlement".${spot ? ` Now: ${usd(spot)}.` : ""}`}>
              <input className={field} inputMode="decimal" required placeholder={spot ? String(Math.round(spot)) : "75000"} value={form.strike} onChange={set("strike")} />
            </Label>
            <Label title="YES price (buy)" hint="Cost of one YES/UP share. 0.55 or 55 both mean 55 cents.">
              <input className={field} inputMode="decimal" required placeholder="0.55" value={form.ask} onChange={set("ask")} />
            </Label>
            <Label title="YES price (sell) · optional" hint="What you get selling one YES share. Empty = same as buy.">
              <input className={field} inputMode="decimal" placeholder="0.53" value={form.bid} onChange={set("bid")} />
            </Label>
            <Label title="Hours to settlement" hint={`Pre-filled: time until the next ${overview.defaults.settle_label}.`}>
              <input className={field} inputMode="decimal" value={form.hours} onChange={set("hours")} />
            </Label>
            <label className="flex items-start gap-2 self-center text-[12px] leading-snug text-muted-foreground">
              <input type="checkbox" className="mt-0.5 accent-emerald-400" checked={form.reset} onChange={(e) => setForm((f) => ({ ...f, reset: e.target.checked }))} />
              I deposited or withdrew money: start the bankroll fresh from this balance (vault back to $0).
            </label>
          </div>
          {error && <p role="alert" className="flex items-start gap-2 rounded-lg border border-rose-400/30 bg-rose-400/5 px-3 py-2 text-sm text-rose-300"><AlertTriangle className="mt-0.5 size-4 shrink-0" />{error}</p>}
          <button type="submit" disabled={pending} className="inline-flex items-center gap-2 rounded-lg bg-emerald-400/15 px-4 py-2 font-mono text-xs font-semibold uppercase tracking-[0.14em] text-emerald-300 ring-1 ring-emerald-400/30 transition-colors hover:bg-emerald-400/25 disabled:opacity-50">
            {pending && <Loader2 className="size-3.5 animate-spin" />} {pending ? "Calculating" : "What should I bet?"}
          </button>
          <Bankroll st={st} share={overview.params.vault_share} />
        </form>

        <div className="min-w-0 space-y-4">
          {advice ? <Verdict a={advice} /> : (
            <div className="grid h-full min-h-40 place-items-center rounded-lg border border-dashed border-white/10 px-6 text-center text-sm text-muted-foreground">
              Fill in the form to get today&apos;s recommendation. Your first balance becomes the starting bankroll.
            </div>
          )}
          {advice && overview.open_position && (advice.action === "BUY_YES" || advice.action === "BUY_NO") && <PositionCheck side={advice.action} />}
        </div>
      </div>

      {overview.history.length > 0 && <History rows={overview.history} />}
      <Rules p={overview.params} />
    </TerminalPanel>
  );
}

function Bankroll({ st, share }: { st: { balance: number; active: number; vault: number; peak: number } | null; share: number }) {
  if (!st) return null;
  return (
    <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
      <dl className="grid grid-cols-3 gap-3">
        <Stat label={<span className="inline-flex items-center gap-1"><Wallet className="size-3" /> Active</span>} value={usd(st.active, true)} valueClassName="text-emerald-300" />
        <Stat label={<span className="inline-flex items-center gap-1"><Vault className="size-3" /> Vault</span>} value={usd(st.vault, true)} valueClassName="text-amber-300" />
        <Stat label="Best balance" value={usd(st.peak, true)} />
      </dl>
      <p className="mt-2 text-[11px] leading-snug text-muted-foreground">
        Bets are sized from <span className="text-emerald-300">Active</span> only. Each time your balance beats its best, {pc(share)} of the gain is locked in the
        <span className="text-amber-300"> Vault</span> and never bet again.
      </p>
    </div>
  );
}

const VERDICT = {
  BUY_YES: { icon: ArrowUpRight, tone: "border-emerald-400/40 bg-emerald-400/[0.07] text-emerald-300", head: "Bet UP" },
  BUY_NO: { icon: ArrowDownRight, tone: "border-rose-400/40 bg-rose-400/[0.07] text-rose-300", head: "Bet DOWN" },
  HOLD_CASH: { icon: PauseCircle, tone: "border-white/15 bg-white/[0.03] text-foreground", head: "No bet today" },
  HALT: { icon: Ban, tone: "border-amber-400/40 bg-amber-400/[0.07] text-amber-300", head: "Stop trading" },
} as const;

function Verdict({ a }: { a: BetAdvice }) {
  const v = VERDICT[a.action];
  const Icon = v.icon;
  const bet = a.action === "BUY_YES" || a.action === "BUY_NO";
  const side = a.action === "BUY_YES" ? "YES" : "NO";
  return (
    <div className="space-y-3">
      <div className={cn("rounded-lg border p-4", v.tone)}>
        <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.16em] opacity-80">
          <Icon className="size-4" /> BTC above {usd(a.inputs.strike)} · {day(a.created_at)}
        </div>
        <p className="mt-1.5 text-2xl font-semibold tracking-tight">
          {v.head}{bet && <span className="text-foreground">: buy {a.shares} {side} share{a.shares === 1 ? "" : "s"} at {cents(a.entry_price)} or less</span>}
        </p>
        <p className="mt-1.5 text-sm leading-relaxed text-foreground/85">
          {bet && <>Cost <span className="font-mono">{usd(a.cost, true)}</span> ({pc(a.fraction, 1)} of your active bankroll). </>}{a.reason}
        </p>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border border-white/[0.07] bg-white/[0.02] p-3 sm:grid-cols-4">
        <Stat label="Model: above target" value={pc(a.model.p_yes)} />
        <Stat label="Market says" value={`${cents(a.inputs.yes_bid)}–${cents(a.inputs.yes_ask)}`} />
        <Stat label="Edge UP / DOWN" value={`${(a.edge_yes * 100).toFixed(0)} / ${(a.edge_no * 100).toFixed(0)} pts`} />
        <Stat label="BTC now" value={usd(a.market.spot)} />
        {bet && <>
          <Stat label="If you win" value={`+${usd(a.max_profit, true)}`} valueClassName="text-emerald-300" />
          <Stat label="If you lose" value={`-${usd(a.cost, true)}`} valueClassName="text-rose-300" />
          <Stat label="Sell early at (profit)" value={`${cents(a.take_profit)} or more`} />
          <Stat label="Sell early at (stop)" value={`${cents(a.stop_loss)} or less`} />
        </>}
      </dl>

      <p className="text-[12px] leading-relaxed text-muted-foreground">
        The forecast said {pc(a.model.stated_up_1d)} chance of UP over a day; {pc(a.model.calibrated_up_1d)} is used ({a.model.calibration_note}).
        {a.bankroll.pnl != null && <> Since your last check-in: <span className={a.bankroll.pnl >= 0 ? "text-emerald-300" : "text-rose-300"}>{a.bankroll.pnl >= 0 ? "+" : "-"}{usd(Math.abs(a.bankroll.pnl), true)}</span>.</>}
        {a.bankroll.swept > 0 && <> <span className="text-amber-300">{usd(a.bankroll.swept, true)} moved to the vault.</span></>}
      </p>
      {a.warnings.map((w) => (
        <p key={w} className="flex items-start gap-2 rounded-lg border border-amber-400/25 bg-amber-400/[0.05] px-3 py-2 text-[12px] leading-snug text-amber-200/90">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />{w}
        </p>
      ))}
    </div>
  );
}

function PositionCheck({ side }: { side: "BUY_YES" | "BUY_NO" }) {
  const [price, setPrice] = useState("");
  const [res, setRes] = useState<BetCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const name = side === "BUY_YES" ? "YES" : "NO";
  function run(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    start(async () => {
      const r = await checkBetAction(sharePrice(price));
      if (!r.ok) return setError(r.error);
      setRes(r.data);
    });
  }
  return (
    <form onSubmit={run} className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-emerald-300/80">Already bought? Check before settlement</div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input className={cn(field, "max-w-[9rem]")} inputMode="decimal" placeholder="0.70" aria-label={`Current sell price of one ${name} share`} value={price} onChange={(e) => setPrice(e.target.value)} />
        <button type="submit" disabled={pending || !price.trim()} className="inline-flex items-center gap-2 rounded-lg px-3 py-2 font-mono text-[11px] font-semibold uppercase tracking-wider text-emerald-300 ring-1 ring-emerald-400/30 hover:bg-emerald-400/10 disabled:opacity-50">
          {pending && <Loader2 className="size-3.5 animate-spin" />} Sell or hold?
        </button>
        <span className="text-[11px] text-muted-foreground">Enter what the site pays now for one {name} share.</span>
      </div>
      {error && <p role="alert" className="mt-2 text-sm text-rose-300">{error}</p>}
      {res && (
        <p className={cn("mt-2 text-sm leading-relaxed", res.action === "SELL" ? "text-amber-300" : "text-emerald-300")}>
          <span className="font-semibold">{res.action === "SELL" ? "Sell now" : "Hold"}.</span> <span className="text-foreground/85">{res.reason}</span>{" "}
          <span className="font-mono text-xs text-muted-foreground">({res.unrealized >= 0 ? "+" : "-"}{usd(Math.abs(res.unrealized), true)} on {res.shares} shares · {res.hours_left.toFixed(1)} h left)</span>
        </p>
      )}
    </form>
  );
}

const SHORT: Record<string, string> = { BUY_YES: "UP", BUY_NO: "DOWN", HOLD_CASH: "no bet", HALT: "stop" };

function History({ rows }: { rows: BetOverview["history"] }) {
  return (
    <div className="mt-5 overflow-x-auto">
      <table className="w-full min-w-[30rem] text-left font-mono text-xs tabular-nums">
        <thead className="text-[10px] uppercase tracking-wider text-muted-foreground">
          <tr><th className="pb-1.5 font-normal">Day</th><th className="font-normal">Balance</th><th className="font-normal">Change</th><th className="font-normal">Target</th><th className="font-normal">Advice</th><th className="font-normal">Vault</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-white/[0.05]">
              <td className="py-1.5">{day(r.created_at)}{r.is_reset && <span className="ml-1 text-muted-foreground">start</span>}</td>
              <td>{usd(r.balance, true)}</td>
              <td className={r.pnl == null ? "text-muted-foreground" : r.pnl >= 0 ? "text-emerald-300" : "text-rose-300"}>{r.pnl == null ? "—" : `${r.pnl >= 0 ? "+" : "-"}${usd(Math.abs(r.pnl), true)}`}</td>
              <td>{usd(r.strike)}</td>
              <td className={r.action === "BUY_YES" ? "text-emerald-300" : r.action === "BUY_NO" ? "text-rose-300" : "text-muted-foreground"}>
                {SHORT[r.action]}{r.shares > 0 && ` · ${r.shares} @ ${cents(r.entry_price)}`}
              </td>
              <td className="text-amber-300/90">{usd(r.vault, true)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Rules({ p }: { p: BetOverview["params"] }) {
  return (
    <details className="mt-4 text-[12px] leading-relaxed text-muted-foreground">
      <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-[0.14em] hover:text-foreground">How it decides</summary>
      <ul className="mt-2 list-disc space-y-1 pl-5">
        <li>It turns the 1-day forecast into a probability that BTC ends above your target, using the measured daily volatility and the time left. The forecast&apos;s confidence is first scaled down by how often past 1-day forecasts were actually right.</li>
        <li>It bets only when that probability beats the market price by at least {pc(p.min_edge)}: UP (buy YES) or DOWN (buy NO). Otherwise: no bet.</li>
        <li>Size = {pc(p.kelly)} of the Kelly fraction, never more than {pc(p.max_fraction)} of the active bankroll, and never below the ${p.min_trade.toFixed(2)} minimum order.</li>
        <li>Sell early when the price reaches {pc(p.take_profit)} of the possible profit, when the position has lost {pc(p.stop_loss)} of its cost, or when the market pays more than the model thinks a share is worth.</li>
        <li>{pc(p.vault_share)} of every new best balance goes to the vault for good. If the active bankroll falls below the minimum order, it says stop.</li>
      </ul>
    </details>
  );
}
