"use client"; // only for the expand/collapse state of the reasoning

import { useId, useState } from "react";
import { ChevronDown, Info, TrendingDown, TrendingUp } from "lucide-react";
import { CitedText } from "@/components/bitcoin/cited-text";
import { confidenceTone, pct, shortDate, trendClass, usd } from "@/lib/bitcoin";
import type { BitcoinHorizon, BitcoinPrediction } from "@/lib/types";
import { cn } from "@/lib/utils";

/** One forecast horizon: target price, move vs now, a confidence badge, and the AI's reasoning (expandable). */
export function PredictionCard({ horizon: h, current, cited }: { horizon: BitcoinHorizon; current: number; cited: BitcoinPrediction["cited_news"] }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const tone = confidenceTone(h.confidence);
  const up = h.change_pct > 0;
  const Arrow = up ? TrendingUp : TrendingDown;
  // How big is this call relative to normal noise? |move| / 1-sigma. Below 1 = within a typical random-walk wiggle.
  const sigmas = h.one_sigma_usd ? Math.abs(h.predicted_price - current) / h.one_sigma_usd : null;

  return (
    <article className="group flex min-w-0 flex-col rounded-lg border border-white/[0.08] bg-[#090a0e]/90 transition-colors hover:border-amber-400/30">
      <header className="flex items-center justify-between border-b border-white/[0.06] px-4 py-2">
        <h3 className="font-mono text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">{h.label}</h3>
        <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground/70">T+{h.days} · {shortDate(h.target_date)}</span>
      </header>

      <div className="flex-1 space-y-3 px-4 py-4">
        <div>
          <p className="font-mono text-2xl font-semibold tabular-nums tracking-tight text-foreground">{usd(h.predicted_price)}</p>
          <p className={cn("mt-1 flex items-center gap-1.5 font-mono text-xs tabular-nums", trendClass(h.change_pct))}>
            <Arrow className="size-3.5" aria-hidden />
            {pct(h.change_pct)}
            {sigmas != null && <span className="text-muted-foreground/70" title="Size of the call relative to a typical random-walk move over this horizon">· {sigmas.toFixed(1)}σ</span>}
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between">
            <span className={cn("inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-[11px] font-semibold tabular-nums", tone.className)}>
              {h.confidence}% Confidence
            </span>
            <span className="group/tip relative">
              <Info className="size-3.5 text-muted-foreground/60" aria-hidden />
              <span role="tooltip"
                className="pointer-events-none absolute right-0 bottom-6 z-10 w-56 rounded-md border border-white/10 bg-[#0d0f14] p-2.5 text-[11px] leading-relaxed text-muted-foreground opacity-0 shadow-xl transition-opacity group-hover/tip:opacity-100">
                Probability that the price on {shortDate(h.target_date)} is on the same side of today&apos;s price as this forecast. 50% = coin flip.
              </span>
            </span>
          </div>
          {/* Bar spans 50-100: confidence below 50 is meaningless for a direction call. */}
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/[0.07]" role="meter" aria-valuemin={50} aria-valuemax={100} aria-valuenow={h.confidence}
            aria-label={`${tone.label} confidence: ${h.confidence}%`}>
            <div className={cn("h-full rounded-full", tone.bar)} style={{ width: `${Math.max(2, ((h.confidence - 50) / 50) * 100)}%` }} />
          </div>
        </div>
      </div>

      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls={panelId}
        className="flex items-center justify-between border-t border-white/[0.06] px-4 py-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground transition-colors hover:text-amber-300">
        AI reasoning
        <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      <div id={panelId} hidden={!open} className="border-t border-white/[0.06] bg-white/[0.015] px-4 py-3 text-[13px] leading-relaxed text-foreground/85">
        <CitedText text={h.reasoning} cited={cited} />
      </div>
    </article>
  );
}
