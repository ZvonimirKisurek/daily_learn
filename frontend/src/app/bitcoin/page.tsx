import type { Metadata } from "next";
import { Suspense } from "react";
import { Bitcoin, ShieldAlert } from "lucide-react";
import { ApiProblem } from "@/components/api-problem";
import { NewsFeed } from "@/components/bitcoin/news-feed";
import { PredictionCard } from "@/components/bitcoin/prediction-card";
import { PriceChart } from "@/components/bitcoin/price-chart";
import { BitcoinRefreshButton } from "@/components/bitcoin/refresh-button";
import { CitedText } from "@/components/bitcoin/cited-text";
import { Stat, TerminalPanel } from "@/components/bitcoin/terminal-panel";
import { Skeleton } from "@/components/ui/skeleton";
import { attempt, getBitcoinLatest } from "@/lib/api";
import { pct, stamp, trendClass, usd } from "@/lib/bitcoin";
import type { BitcoinLatest, BitcoinPrediction } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Bitcoin" };

/**
 * Bitcoin intelligence dashboard. Same Server/Client split as the Command Center: this page and <BitcoinDesk> render on
 * the server (the API token stays secret and the data arrives as HTML); only the chart, the expandable cards and the
 * Refresh button ship JS. The static header paints instantly while <Suspense> streams the data in.
 */
export default function BitcoinPage() {
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-white/[0.07] pb-4">
        <div className="flex items-center gap-3">
          <span className="grid size-9 place-items-center rounded-md bg-amber-400/15 text-amber-300 ring-1 ring-amber-400/30">
            <Bitcoin className="size-5" />
          </span>
          <div>
            <h1 className="font-mono text-lg font-semibold tracking-tight">BTC/USD <span className="text-muted-foreground">· Intelligence</span></h1>
            <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Daily AI forecast · 09:00 · news sentiment + technicals</p>
          </div>
        </div>
      </div>
      <Suspense fallback={<DeskSkeleton />}>
        <BitcoinDesk />
      </Suspense>
    </div>
  );
}

async function BitcoinDesk() {
  const [data, err] = await attempt(getBitcoinLatest());
  if (!data) return <ApiProblem error={err} />;
  const { price, prediction, news, disclaimer } = data;
  const citedRefs = new Map((prediction?.cited_news ?? []).map((c) => [c.url, c.ref]));

  return (
    <div className="space-y-5">
      <StatusBar data={data} />

      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[300px_minmax(0,1fr)]">
        <TerminalPanel title="Spot" meta={price?.source ?? "—"}>
          <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Bitcoin · USD</p>
          <p className="mt-1 font-mono text-[40px] font-semibold leading-none tabular-nums tracking-tight text-amber-50">{usd(price?.price_usd, true)}</p>
          {price && <p className="mt-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">As of {stamp(price.fetched_at)}</p>}
          <dl className="mt-5 grid grid-cols-3 gap-3 border-t border-white/[0.06] pt-4">
            <Stat label="24h" value={pct(prediction?.technicals.change_1d_pct)} valueClassName={trendClass(prediction?.technicals.change_1d_pct)} />
            <Stat label="7d" value={pct(prediction?.technicals.change_7d_pct)} valueClassName={trendClass(prediction?.technicals.change_7d_pct)} />
            <Stat label="30d" value={pct(prediction?.technicals.change_30d_pct)} valueClassName={trendClass(prediction?.technicals.change_30d_pct)} />
          </dl>
          <dl className="mt-4 grid grid-cols-2 gap-3">
            <Stat label="30d high" value={usd(prediction?.technicals.high_30d)} />
            <Stat label="30d low" value={usd(prediction?.technicals.low_30d)} />
          </dl>
        </TerminalPanel>

        <TerminalPanel title="Price · 30d + forecast" meta={<Legend />}>
          {prediction && prediction.history.length > 1 ? <PriceChart prediction={prediction} /> : (
            <div className="grid h-[230px] place-items-center text-sm text-muted-foreground">The chart appears after the first forecast run.</div>
          )}
        </TerminalPanel>
      </div>

      {prediction ? (
        <section aria-labelledby="forecast-heading" className="space-y-3">
          <div className="flex items-baseline justify-between">
            <h2 id="forecast-heading" className="font-mono text-[11px] font-semibold uppercase tracking-[0.16em] text-amber-300/90">AI forecast</h2>
            <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Base {usd(prediction.current_price)} · <span className="normal-case">{prediction.model}</span></p>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {prediction.horizons.map((h) => <PredictionCard key={h.key} horizon={h} current={prediction.current_price} cited={prediction.cited_news} />)}
          </div>
        </section>
      ) : (
        <EmptyForecast />
      )}

      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-5">
          {prediction && <SentimentPanel prediction={prediction} />}
          {prediction && <TechnicalsPanel prediction={prediction} />}
        </div>
        <aside className="min-w-0 lg:sticky lg:top-20 lg:self-start">
          <NewsFeed news={news} citedRefs={citedRefs} />
        </aside>
      </div>

      <p className="flex items-start gap-2 rounded-md border border-white/[0.06] bg-white/[0.02] px-4 py-3 text-xs leading-relaxed text-muted-foreground">
        <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-amber-400/80" aria-hidden />
        {disclaimer}
      </p>
    </div>
  );
}

function StatusBar({ data }: { data: BitcoinLatest }) {
  const p = data.prediction;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-white/[0.07] bg-[#090a0e]/90 px-4 py-2">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span aria-hidden className={cn("size-1.5 rounded-full", p ? "bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.9)]" : "bg-muted-foreground")} />
          {p ? "Forecast live" : "No forecast"}
        </span>
        {p && <span>Generated {stamp(p.created_at)}</span>}
        {p && <span>Trigger: {p.trigger}</span>}
        <span>Sources: {new Set(data.news.map((n) => n.source_name)).size}</span>
      </div>
      <BitcoinRefreshButton latestId={p?.id ?? null} />
    </div>
  );
}

function Legend() {
  return (
    <span className="flex items-center gap-3">
      <span className="flex items-center gap-1"><span className="h-px w-3 bg-amber-500" />Close</span>
      <span className="flex items-center gap-1"><span className="h-px w-3 border-t border-dashed border-amber-300" />AI</span>
      <span className="flex items-center gap-1"><span className="h-2 w-3 rounded-[2px] bg-sky-400/20" />±1<span className="normal-case">σ</span></span>
    </span>
  );
}

function SentimentPanel({ prediction: p }: { prediction: BitcoinPrediction }) {
  const s = p.sentiment_score;
  const word = s >= 40 ? "Bullish" : s >= 10 ? "Leaning bullish" : s > -10 ? "Neutral" : s > -40 ? "Leaning bearish" : "Bearish";
  return (
    <TerminalPanel title="News sentiment" meta={`${p.cited_news.length} headlines analysed`}>
      <div className="flex flex-wrap items-center gap-4">
        <p className={cn("font-mono text-3xl font-semibold tabular-nums", trendClass(s))}>{s > 0 ? `+${s}` : s}</p>
        <div className="min-w-48 flex-1">
          <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{word}</p>
          {/* -100..+100 gauge with a centre tick */}
          <div className="relative mt-2 h-1.5 rounded-full bg-gradient-to-r from-rose-500/50 via-white/10 to-emerald-500/50" role="meter"
            aria-valuemin={-100} aria-valuemax={100} aria-valuenow={s} aria-label={`News sentiment ${s} (${word})`}>
            <span className="absolute top-1/2 left-1/2 h-3 w-px -translate-y-1/2 bg-white/30" aria-hidden />
            <span className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[#090a0e] bg-amber-300" style={{ left: `${(s + 100) / 2}%` }} aria-hidden />
          </div>
        </div>
      </div>
      <p className="mt-4 text-[13px] leading-relaxed text-foreground/85"><CitedText text={p.sentiment_summary} cited={p.cited_news} /></p>
    </TerminalPanel>
  );
}

function TechnicalsPanel({ prediction: p }: { prediction: BitcoinPrediction }) {
  const t = p.technicals;
  const rsi = t.rsi_14;
  return (
    <TerminalPanel title="Technicals" meta="computed from exchange data">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
        <Stat label="SMA 7" value={usd(t.sma_7)} />
        <Stat label="vs SMA 7" value={pct(t.price_vs_sma7_pct)} valueClassName={trendClass(t.price_vs_sma7_pct)} />
        <Stat label="SMA 30" value={usd(t.sma_30)} />
        <Stat label="vs SMA 30" value={pct(t.price_vs_sma30_pct)} valueClassName={trendClass(t.price_vs_sma30_pct)} />
        <Stat label="RSI 14" value={rsi == null ? "—" : rsi.toFixed(1)}
          valueClassName={rsi == null ? undefined : rsi >= 70 ? "text-rose-300" : rsi <= 30 ? "text-emerald-300" : undefined} />
        <Stat label={<>Daily <span className="normal-case">σ</span></>} value={t.daily_sigma == null ? "—" : `${(t.daily_sigma * 100).toFixed(2)}%`} />
        <Stat label="Ann. vol" value={t.annualised_vol_pct == null ? "—" : `${t.annualised_vol_pct.toFixed(1)}%`} />
        <Stat label={<>1<span className="normal-case">σ</span> · 7d</>} value={t.one_sigma_move ? `±${usd(t.one_sigma_move["7d"])}` : "—"} />
      </dl>
      <p className="mt-4 border-t border-white/[0.06] pt-4 text-[13px] leading-relaxed text-foreground/85">
        <CitedText text={p.technical_summary} cited={p.cited_news} />
      </p>
    </TerminalPanel>
  );
}

function EmptyForecast() {
  return (
    <div className="rounded-lg border border-dashed border-amber-400/25 bg-amber-400/[0.03] p-8 text-center">
      <Bitcoin className="mx-auto mb-3 size-6 text-amber-300" />
      <h2 className="font-mono text-sm font-semibold uppercase tracking-[0.14em]">No forecast yet</h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
        A forecast is generated every day at 09:00. Press <span className="font-mono text-amber-300">Refresh</span> above to run one now.
        It takes up to a minute.
      </p>
    </div>
  );
}

function DeskSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading Bitcoin dashboard" className="space-y-5">
      <Skeleton className="h-10 rounded-md" />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[300px_minmax(0,1fr)]"><Skeleton className="h-72 rounded-lg" /><Skeleton className="h-72 rounded-lg" /></div>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-2 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-48 rounded-lg" />)}</div>
    </div>
  );
}
