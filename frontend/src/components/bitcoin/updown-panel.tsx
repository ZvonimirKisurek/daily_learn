import { CheckCircle2, Circle, GraduationCap, Send, XCircle } from "lucide-react";
import { usd } from "@/lib/bitcoin";
import type { UpDownStatus, UpDownWindow } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Stat, TerminalPanel } from "./terminal-panel";

const pc = (v: number | null | undefined, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const hm = (iso: string) => new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin" }).format(new Date(iso));

/**
 * The 5m / 15m Polymarket Up/Down markets as the always-on loop sees them (backend app/updown): the current windows,
 * the model's report card (PROVEN or PAPER ONLY), today's daily plan and the recent signals with their results.
 * A Server Component: it re-renders with the page's live pulse, no client state.
 */
export function UpDownPanel({ s }: { s: UpDownStatus }) {
  const m = s.model;
  return (
    <TerminalPanel title="Short-term · BTC 5m / 15m Up/Down"
      meta={<span className="inline-flex items-center gap-3">
        <span className="inline-flex items-center gap-1.5"><Circle className={cn("size-2 fill-current", s.running ? "text-emerald-400" : "text-rose-400")} />{s.running ? "live" : "loop off"}</span>
        <span className="inline-flex items-center gap-1"><Send className="size-3" />{s.telegram ? "Telegram on" : "Telegram off"}</span>
      </span>}>
      {!s.running && (
        <p className="mb-4 rounded-lg border border-amber-400/30 bg-amber-400/[0.06] px-3 py-2 text-[13px] leading-snug text-amber-100">
          The live loop is not running, so nothing is being watched. Start the <span className="font-mono">updown</span> service on the server.
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {[5, 15].map((min) => <WindowCard key={min} minutes={min} w={s.windows.find((w) => w.minutes === min)} />)}
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-emerald-300/80">The model</span>
            {m && <span className={cn("rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider",
              m.proven ? "border-emerald-400/40 text-emerald-300" : "border-amber-400/40 text-amber-300")}>{m.proven ? "proven" : "paper only"}</span>}
          </div>
          <p className="text-[13px] leading-relaxed text-foreground/85">
            {m ? m.summary : `Not trained yet. ${s.data.windows_resolved} finished windows collected; training runs every 3 hours once there are 60.`}
          </p>
          {m?.review && (
            <details className="mt-2 text-[12px] leading-relaxed text-muted-foreground">
              <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-[0.14em] hover:text-foreground">
                <GraduationCap className="mr-1 inline size-3" />Claude&apos;s review
              </summary>
              <p className="mt-1.5">{m.review}</p>
            </details>
          )}
          <p className="mt-2 text-[11px] text-muted-foreground">
            Pings when it is at least {pc(s.rule.prob)} sure of one side and that beats the price (fee included) by {pc(s.rule.min_edge)}, with at least {s.rule.min_secs} s left.
          </p>
        </div>

        <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
          <div className="mb-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-emerald-300/80">Daily plan {s.daily?.date && `· ${s.daily.date}`}</div>
          {!s.daily ? <p className="text-[13px] text-muted-foreground">Made every hour by the live loop; the full plan goes to Telegram each morning.</p>
            : s.daily.error ? <p className="text-[13px] text-muted-foreground">{s.daily.error}</p>
            : s.daily.best ? (
              <p className="text-[13px] leading-relaxed text-foreground/85">
                <span className="font-semibold">{s.daily.best.side.toUpperCase()}</span> on &quot;BTC above {usd(s.daily.best.strike)}&quot; at {s.daily.best.cost.toFixed(2)} (pays {s.daily.best.multiplier.toFixed(2)}x).
                Model {pc(s.daily.best.p_win)} vs price {pc(s.daily.best.cost)}: edge +{(s.daily.best.edge * 100).toFixed(0)} pts.
                {s.daily.best.stake > 0 ? <> Stake <span className="font-mono">{usd(s.daily.best.stake, true)}</span>.</> : <> {s.daily.best.advice}</>}
              </p>
            ) : <p className="text-[13px] text-foreground/85">No daily bet: every target price is priced at or beyond what the forecast thinks it is worth.</p>}
          {s.daily?.forecast && <p className="mt-2 text-[11px] text-muted-foreground">Forecast: {pc(s.daily.forecast.calibrated_up_1d)} Up over a day ({s.daily.forecast.note}).</p>}
        </div>
      </div>

      {s.signals.length > 0 && (
        <div className="mt-4">
          <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-emerald-300/80">Recent signals</span>
            {s.record.settled > 0 && <span className="font-mono text-[11px] text-muted-foreground">30 days: {s.record.wins}/{s.record.settled} right · {s.record.pnl_per_dollar >= 0 ? "+" : ""}{s.record.pnl_per_dollar.toFixed(2)} per $1</span>}
          </div>
          <ul className="divide-y divide-white/[0.05] text-[12.5px]">
            {s.signals.slice(0, 8).map((g) => (
              <li key={g.id} className="flex items-center gap-2 py-1.5">
                {g.outcome == null ? <Circle className="size-3.5 shrink-0 text-muted-foreground" /> : g.outcome === g.side
                  ? <CheckCircle2 className="size-3.5 shrink-0 text-emerald-400" /> : <XCircle className="size-3.5 shrink-0 text-rose-400" />}
                <span className="min-w-0 flex-1 truncate">{g.detail.label ?? g.slug}</span>
                <span className={cn("font-mono", g.side === "up" ? "text-emerald-300" : "text-rose-300")}>{g.side.toUpperCase()} {pc(g.p_model)}</span>
                <span className="hidden font-mono text-muted-foreground sm:inline">@ {Number(g.price).toFixed(2)}</span>
                {!g.proven && <span className="rounded border border-amber-400/30 px-1 font-mono text-[9px] uppercase text-amber-300">paper</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </TerminalPanel>
  );
}

function WindowCard({ minutes, w }: { minutes: number; w?: UpDownWindow }) {
  if (!w) return (
    <div className="rounded-lg border border-dashed border-white/10 p-3 text-[13px] text-muted-foreground">{minutes}-minute market: no live data yet.</div>
  );
  const gap = w.price_to_beat ? w.btc_index - w.price_to_beat : null;
  return (
    <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <span className="truncate text-sm font-medium">{w.label}</span>
        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">ends {hm(w.end_at)}</span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2">
        <Stat label="Price to beat" value={w.price_to_beat ? usd(w.price_to_beat) : "waiting"} />
        <Stat label="BTC now (Chainlink est.)" value={<>{usd(w.btc_index)}{gap != null && <span className={cn("ml-1 text-[11px]", gap >= 0 ? "text-emerald-300" : "text-rose-300")}>{gap >= 0 ? "+" : ""}{gap.toFixed(0)}</span>}</>} />
        <Stat label="Market: Up" value={pc(w.up_price)} />
        <Stat label="Model: Up" value={pc(w.p_model)} valueClassName={w.p_model == null ? "text-muted-foreground" : w.p_model >= 0.5 ? "text-emerald-300" : "text-rose-300"} />
      </dl>
    </div>
  );
}
