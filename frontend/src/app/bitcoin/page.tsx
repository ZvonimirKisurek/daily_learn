import type { Metadata } from "next";
import { Suspense } from "react";
import { ArrowDownRight, ArrowUpRight, Bitcoin, ShieldAlert } from "lucide-react";
import { ApiProblem } from "@/components/api-problem";
import { AnimatedNumber } from "@/components/bitcoin/animated-number";
import { BetAdvisor } from "@/components/bitcoin/bet-advisor";
import { CitedText } from "@/components/bitcoin/cited-text";
import { NewsFeed } from "@/components/bitcoin/news-feed";
import { PredictionCard } from "@/components/bitcoin/prediction-card";
import { PriceChart } from "@/components/bitcoin/price-chart";
import { BitcoinRefreshButton } from "@/components/bitcoin/refresh-button";
import { Standby } from "@/components/bitcoin/standby";
import { Stat, TerminalPanel } from "@/components/bitcoin/terminal-panel";
import { Reveal } from "@/components/motion";
import { ApiError, attempt, getBitcoinBet, getBitcoinLatest } from "@/lib/api";
import { pct, stamp, trendClass, usd } from "@/lib/bitcoin";
import type { BitcoinLatest, BitcoinPrediction } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Bitcoin" };

/**
 * Bitcoin terminal. Server/Client split:
 *   Server  this page, <BitcoinDesk>, the hero, stats bar and analysis panels: data arrives as HTML and the API token
 *           stays on the server.
 *   Client  only what moves or responds: the count-up price, chart, cards (hover/gauge), news ticker (in-view
 *           fade-ins), Refresh (polling), and the standby screen (radar). Each receives plain serialisable props.
 * <Suspense> lets the header paint instantly while the data streams in behind a shimmer skeleton.
 */
export default function BitcoinPage() {
  return (
    <div className="space-y-6">
      <Suspense fallback={<HeaderShell />}>
        <BitcoinDesk />
      </Suspense>
    </div>
  );
}

async function BitcoinDesk() {
  // In parallel. The bet advisor is optional: if its table is not migrated yet the rest of the desk still renders.
  const [[data, err], [bet, betErr]] = await Promise.all([attempt(getBitcoinLatest()), attempt(getBitcoinBet())]);
  if (!data) return <><HeaderShell /><ApiProblem error={err} /></>;
  const { prediction, price, news, run, disclaimer } = data;

  if (!prediction) {
    return (
      <>
        <Header data={data} />
        <Standby run={run} />
        {news.length > 0 && <NewsFeed news={news} citedRefs={{}} />}
        <Disclaimer text={disclaimer} />
      </>
    );
  }

  const citedRefs = Object.fromEntries(prediction.cited_news.map((c) => [c.url, c.ref]));
  return (
    <>
      <Header data={data} />

      <Reveal className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <SpotHero data={data} />
        <TerminalPanel title="Price · 30d + AI forecast" meta={<Legend />} bodyClassName="px-2 pt-3 pb-2">
          {prediction.history.length > 1 ? <PriceChart prediction={prediction} height={280} /> : (
            <div className="grid h-[280px] place-items-center text-sm text-muted-foreground">Not enough price history for a chart yet.</div>
          )}
        </TerminalPanel>
      </Reveal>

      <StatsBar prediction={prediction} />

      <section aria-labelledby="forecast-heading" className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="forecast-heading" className="font-mono text-[11px] font-semibold uppercase tracking-[0.2em] text-emerald-300/90">AI forecast · 4 horizons</h2>
          <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            Base {usd(prediction.current_price)} · <span className="normal-case">{prediction.model}</span> · hover a card for its reasoning
          </p>
        </div>
        {/* pb leaves room for the reasoning overlay of the bottom row on small screens */}
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4 pb-2 sm:grid-cols-2 lg:grid-cols-4">
          {prediction.horizons.map((h, i) => (
            <PredictionCard key={h.key} index={i} horizon={h} current={prediction.current_price} cited={prediction.cited_news} />
          ))}
        </div>
      </section>

      {bet ? <BetAdvisor overview={bet} spot={price?.price_usd ?? null} /> : (
        <TerminalPanel title="Bet advisor · daily up/down">
          <p className="text-sm text-muted-foreground">{betErr instanceof ApiError ? betErr.message : "The bet advisor is unavailable right now."}</p>
        </TerminalPanel>
      )}

      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-5">
          <SentimentPanel prediction={prediction} />
          <TechnicalsPanel prediction={prediction} />
        </div>
        <aside className="min-w-0 lg:sticky lg:top-20 lg:self-start">
          <NewsFeed news={news} citedRefs={citedRefs} />
        </aside>
      </div>

      <Disclaimer text={disclaimer} />
      {price == null && <p className="sr-only">No spot price stored yet.</p>}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ header + hero
function Header({ data }: { data: BitcoinLatest }) {
  const p = data.prediction;
  return (
    <div className="flex flex-wrap items-center justify-between gap-4">
      <HeaderTitle live={!!p} />
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        {p && (
          <span className="hidden font-mono text-[10px] uppercase tracking-wider text-muted-foreground md:block">
            Forecast {stamp(p.created_at)}
          </span>
        )}
        {p && <BitcoinRefreshButton run={data.run} />}
      </div>
    </div>
  );
}

function HeaderTitle({ live }: { live: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <span className="grid size-10 place-items-center rounded-xl bg-gradient-to-br from-amber-300 to-orange-500 text-[#1a0f00] shadow-[0_0_28px_-6px_rgba(251,191,36,0.7)]">
        <Bitcoin className="size-5" strokeWidth={2.4} />
      </span>
      <div>
        <h1 className="font-mono text-lg font-semibold tracking-tight text-white">BTC/USD <span className="text-muted-foreground">· Terminal</span></h1>
        <p className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          <span className={cn("size-1.5 rounded-full", live ? "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.9)]" : "bg-amber-400/80")} aria-hidden />
          {live ? "Online" : "Standby"} · Daily 09:00 · sentiment + technicals
        </p>
      </div>
    </div>
  );
}

function HeaderShell() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading Bitcoin terminal">
      <HeaderTitle live={false} />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="shimmer h-72 rounded-2xl" /><div className="shimmer h-72 rounded-2xl" />
      </div>
      <div className="shimmer h-16 rounded-xl" />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-2 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <div key={i} className="shimmer h-44 rounded-xl" />)}</div>
    </div>
  );
}

function SpotHero({ data }: { data: BitcoinLatest }) {
  const { price, prediction } = data;
  const spot = price?.price_usd ?? prediction?.current_price ?? null;
  const ch = prediction?.technicals.change_1d_pct ?? null;
  const up = (ch ?? 0) >= 0;
  const Arrow = up ? ArrowUpRight : ArrowDownRight;
  const t = prediction?.technicals;
  // where the price sits inside its 30-day range, 0-100%
  const pos = t?.low_30d != null && t.high_30d != null && spot != null && t.high_30d > t.low_30d
    ? Math.min(100, Math.max(0, ((spot - t.low_30d) / (t.high_30d - t.low_30d)) * 100)) : null;

  return (
    <section className="relative min-w-0 overflow-hidden rounded-xl border border-emerald-400/15 bg-[#05070a]/90 p-6">
      <div aria-hidden className="pointer-events-none absolute -top-24 -left-20 size-72 rounded-full bg-emerald-400/10 blur-3xl" />
      <div aria-hidden className="terminal-grid pointer-events-none absolute inset-0 opacity-50 [mask-image:linear-gradient(to_bottom_right,black,transparent_60%)]" />
      <div className="relative">
        <div className="flex items-center justify-between">
          <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground">Spot · BTC/USD</p>
          {price && <span className="rounded-full border border-white/10 px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider text-muted-foreground">{price.source}</span>}
        </div>
        <p className="mt-4 font-mono text-[44px] leading-none font-bold tracking-tighter text-white tabular-nums sm:text-[56px]">
          {spot != null ? <AnimatedNumber value={spot} cents className="glow-text" /> : "—"}
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {ch != null && (
            <span className={cn("inline-flex items-center gap-1 rounded-md border px-2 py-1 font-mono text-sm font-semibold tabular-nums",
              up ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300 glow-emerald" : "border-rose-400/30 bg-rose-400/10 text-rose-300")}>
              <Arrow className="size-4" aria-hidden />{pct(ch)}
              <span className="ml-1 text-[10px] font-normal uppercase tracking-wider opacity-70">24h</span>
            </span>
          )}
          {price && <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">As of {stamp(price.fetched_at)}</span>}
        </div>
        {pos != null && t && (
          <div className="mt-6">
            <div className="flex justify-between font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              <span>30d low {usd(t.low_30d)}</span><span>high {usd(t.high_30d)}</span>
            </div>
            <div className="relative mt-2 h-1.5 rounded-full bg-gradient-to-r from-rose-500/40 via-white/10 to-emerald-500/40"
              role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pos)} aria-label={`Price at ${Math.round(pos)}% of its 30-day range`}>
              <span className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[#05070a] bg-white shadow-[0_0_10px_rgba(255,255,255,0.7)]" style={{ left: `${pos}%` }} />
            </div>
          </div>
        )}
        {prediction && (
          // At-a-glance outlook: the four forecast moves in one strip (details live in the cards below).
          <dl className="mt-6 grid grid-cols-4 gap-2 border-t border-white/[0.06] pt-4">
            {prediction.horizons.map((h) => (
              <div key={h.key} className="rounded-lg bg-white/[0.025] px-2 py-2 text-center">
                <dt className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground">{h.key}</dt>
                <dd className={cn("mt-0.5 font-mono text-xs font-semibold tabular-nums", trendClass(h.change_pct))}>{pct(h.change_pct, 1)}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </section>
  );
}

/** Glass strip of the key numbers. */
function StatsBar({ prediction: p }: { prediction: BitcoinPrediction }) {
  const t = p.technicals;
  const rsi = t.rsi_14;
  return (
    <dl className="glass grid grid-cols-2 gap-x-6 gap-y-4 rounded-xl px-5 py-4 sm:grid-cols-4 lg:grid-cols-7">
      <Stat label="7d" value={pct(t.change_7d_pct)} valueClassName={trendClass(t.change_7d_pct)} />
      <Stat label="30d" value={pct(t.change_30d_pct)} valueClassName={trendClass(t.change_30d_pct)} />
      <Stat label="vs SMA 30" value={pct(t.price_vs_sma30_pct)} valueClassName={trendClass(t.price_vs_sma30_pct)} />
      <Stat label="RSI 14" value={rsi == null ? "—" : rsi.toFixed(1)}
        valueClassName={rsi == null ? undefined : rsi >= 70 ? "text-rose-300" : rsi <= 30 ? "text-emerald-300" : "text-foreground"} />
      <Stat label={<>Daily <span className="normal-case">σ</span></>} value={t.daily_sigma == null ? "—" : `${(t.daily_sigma * 100).toFixed(2)}%`} />
      <Stat label="Ann. vol" value={t.annualised_vol_pct == null ? "—" : `${t.annualised_vol_pct.toFixed(1)}%`} />
      <Stat label="Sentiment" value={p.sentiment_score > 0 ? `+${p.sentiment_score}` : String(p.sentiment_score)} valueClassName={trendClass(p.sentiment_score)} />
    </dl>
  );
}

function Legend() {
  return (
    <span className="flex items-center gap-3">
      <span className="flex items-center gap-1"><span className="h-0.5 w-3 rounded bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.9)]" />Close</span>
      <span className="flex items-center gap-1"><span className="h-0 w-3 border-t-2 border-dashed border-cyan-300" />AI</span>
      <span className="flex items-center gap-1"><span className="h-2 w-3 rounded-[2px] bg-violet-400/30" />±1<span className="normal-case">σ</span></span>
    </span>
  );
}

// ------------------------------------------------------------------------------------------------ analysis
function SentimentPanel({ prediction: p }: { prediction: BitcoinPrediction }) {
  const s = p.sentiment_score;
  const word = s >= 40 ? "Bullish" : s >= 10 ? "Leaning bullish" : s > -10 ? "Neutral" : s > -40 ? "Leaning bearish" : "Bearish";
  return (
    <TerminalPanel title="News sentiment" meta={`${p.cited_news.length} headlines analysed`}>
      <div className="flex flex-wrap items-center gap-5">
        <p className={cn("font-mono text-4xl font-bold tabular-nums", trendClass(s), s > 0 && "glow-emerald")}>{s > 0 ? `+${s}` : s}</p>
        <div className="min-w-48 flex-1">
          <div className="flex justify-between font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            <span>Bearish</span><span className="text-foreground/80">{word}</span><span>Bullish</span>
          </div>
          <div className="relative mt-2 h-2 rounded-full bg-gradient-to-r from-rose-500/60 via-white/10 to-emerald-500/60" role="meter"
            aria-valuemin={-100} aria-valuemax={100} aria-valuenow={s} aria-label={`News sentiment ${s} (${word})`}>
            <span className="absolute top-1/2 left-1/2 h-4 w-px -translate-y-1/2 bg-white/30" aria-hidden />
            <span className="absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[#090a0e] bg-white shadow-[0_0_12px_rgba(255,255,255,0.8)]" style={{ left: `${(s + 100) / 2}%` }} aria-hidden />
          </div>
        </div>
      </div>
      <p className="mt-4 text-[13px] leading-relaxed text-foreground/85"><CitedText text={p.sentiment_summary} cited={p.cited_news} /></p>
    </TerminalPanel>
  );
}

function TechnicalsPanel({ prediction: p }: { prediction: BitcoinPrediction }) {
  const t = p.technicals;
  return (
    <TerminalPanel title="Technicals" meta="computed from exchange data">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
        <Stat label="SMA 7" value={usd(t.sma_7)} />
        <Stat label="vs SMA 7" value={pct(t.price_vs_sma7_pct)} valueClassName={trendClass(t.price_vs_sma7_pct)} />
        <Stat label="SMA 30" value={usd(t.sma_30)} />
        <Stat label="vs SMA 30" value={pct(t.price_vs_sma30_pct)} valueClassName={trendClass(t.price_vs_sma30_pct)} />
        {(["1d", "3d", "7d", "14d"] as const).map((k) => (
          <Stat key={k} label={<>1<span className="normal-case">σ</span> · {k}</>} value={t.one_sigma_move ? `±${usd(t.one_sigma_move[k])}` : "—"} />
        ))}
        {/* Older forecasts predate these indicators, so every one is optional. */}
        <Stat label="MACD hist" value={t.macd_hist == null ? "—" : `${t.macd_hist > 0 ? "+" : ""}${t.macd_hist.toFixed(0)}`}
          valueClassName={trendClass(t.macd_hist ?? null)} />
        <Stat label="Bollinger %B" value={t.bb_percent_b == null ? "—" : t.bb_percent_b.toFixed(2)} />
        <Stat label="BB width" value={t.bb_bandwidth_pct == null ? "—" : `${t.bb_bandwidth_pct.toFixed(1)}%`} />
        <Stat label="ATR 14" value={t.atr_pct == null ? "—" : `${t.atr_pct.toFixed(2)}%`} />
      </dl>
      <p className="mt-4 border-t border-white/[0.06] pt-4 text-[13px] leading-relaxed text-foreground/85">
        <CitedText text={p.technical_summary} cited={p.cited_news} />
      </p>
    </TerminalPanel>
  );
}

function Disclaimer({ text }: { text: string }) {
  return (
    <p className="flex items-start gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-4 py-3 text-xs leading-relaxed text-muted-foreground">
      <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-amber-400/80" aria-hidden />
      {text}
    </p>
  );
}
