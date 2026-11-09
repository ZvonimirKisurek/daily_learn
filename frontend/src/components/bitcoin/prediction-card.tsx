"use client";

import { useId, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { BrainCircuit, TrendingDown, TrendingUp } from "lucide-react";
import { CitedText } from "@/components/bitcoin/cited-text";
import { confidenceTone, pct, shortDate, trendClass, usd, type Tone } from "@/lib/bitcoin";
import type { BitcoinHorizon, BitcoinPrediction } from "@/lib/types";
import { cn } from "@/lib/utils";

const EASE = [0.22, 1, 0.36, 1] as const;

/**
 * One forecast horizon. Anatomy:
 *   - a 1px GRADIENT BORDER: the card is a padded wrapper whose background is a gradient in the confidence colour, with an
 *     opaque inner panel on top. Only the 1px of padding shows, so the border appears to glow. The wrapper's box-shadow
 *     adds the bloom.
 *   - a RADIAL GAUGE: an SVG circle whose stroke-dashoffset framer-motion animates from empty to the confidence value.
 *   - the REASONING PANEL: opens on hover (desktop), on keyboard focus, or on tap (touch toggles it pinned). It is
 *     absolutely positioned below the card, so opening it overlays the content beneath instead of pushing the whole grid
 *     down, which would make neighbouring cards jump while you move the mouse.
 */
export function PredictionCard({ horizon: h, current, cited, index }: {
  horizon: BitcoinHorizon; current: number; cited: BitcoinPrediction["cited_news"]; index: number;
}) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  const open = hovered || focused || pinned;
  const panelId = useId();
  const tone = confidenceTone(h.confidence);
  const Arrow = h.change_pct > 0 ? TrendingUp : TrendingDown;
  const sigmas = h.one_sigma_usd ? Math.abs(h.predicted_price - current) / h.one_sigma_usd : null;

  return (
    <motion.div
      className="relative"
      style={{ zIndex: open ? 30 : 1 }}
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: EASE, delay: 0.08 * index }}
      onHoverStart={() => setHovered(true)}
      onHoverEnd={() => setHovered(false)}
      onFocus={() => setFocused(true)}
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false); }}
    >
      <motion.article
        animate={{ scale: open ? 1.03 : 1 }}
        transition={{ type: "spring", stiffness: 320, damping: 26 }}
        className="rounded-xl p-px"
        style={{
          background: `linear-gradient(140deg, ${tone.from}, transparent 45%, transparent 60%, ${tone.to})`,
          boxShadow: open ? `0 0 0 1px ${tone.glow}, 0 18px 50px -16px ${tone.glow}` : `0 10px 40px -24px ${tone.glow}`,
        }}
      >
        <div className="relative overflow-hidden rounded-[11px] bg-[#07090d]/95 px-4 pt-3 pb-4">
          {/* faint terminal grid + a corner glow in the confidence colour */}
          <div aria-hidden className="terminal-grid pointer-events-none absolute inset-0 opacity-40 [mask-image:linear-gradient(to_bottom,black,transparent_70%)]" />
          <div aria-hidden className="pointer-events-none absolute -top-16 -right-16 size-36 rounded-full blur-3xl" style={{ background: tone.glow }} />

          <header className="relative flex items-center justify-between">
            <h3 className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-foreground/80">{h.label}</h3>
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground/70">T+{h.days} · {shortDate(h.target_date)}</span>
          </header>

          <div className="relative mt-4 flex items-end justify-between gap-3">
            <div className="min-w-0">
              <p className="font-mono text-[26px] leading-none font-semibold tabular-nums tracking-tight text-white">{usd(h.predicted_price)}</p>
              <p className={cn("mt-2 flex items-center gap-1.5 font-mono text-xs tabular-nums", trendClass(h.change_pct))}>
                <Arrow className="size-3.5" aria-hidden />
                {pct(h.change_pct)}
                {sigmas != null && (
                  <span className="text-muted-foreground/70" title="Size of the call relative to a typical random-walk move over this horizon">· {sigmas.toFixed(1)}σ</span>
                )}
              </p>
            </div>
            <ConfidenceGauge value={h.confidence} tone={tone} delay={0.25 + 0.08 * index} />
          </div>

          <button
            type="button"
            onClick={() => setPinned((p) => !p)}
            aria-expanded={open}
            aria-controls={panelId}
            className="relative mt-4 flex w-full items-center gap-2 rounded-md border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground transition-colors hover:text-foreground"
          >
            <BrainCircuit className="size-3.5" style={{ color: tone.stroke }} aria-hidden />
            AI reasoning
            <span className="ml-auto normal-case tracking-normal text-muted-foreground/60">{pinned ? "pinned" : "hover or tap"}</span>
          </button>
        </div>
      </motion.article>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id={panelId}
            key="reasoning"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.28, ease: EASE }}
            // Same 1.03 scale as the card so the panel's edges line up with the card's scaled edges.
            className="absolute inset-x-0 top-full mt-1.5 overflow-hidden rounded-xl border bg-[#0a0d12]/95 shadow-2xl backdrop-blur-xl"
            style={{ borderColor: tone.glow, scale: 1.03, transformOrigin: "top" }}
          >
            <p className="px-4 py-3 text-[13px] leading-relaxed text-foreground/85">
              <CitedText text={h.reasoning} cited={cited} />
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

/**
 * 0-100 ring with a tick at 50 (bottom of the ring = "coin flip"): confidence is the probability the DIRECTION is right,
 * so 50 is the floor of meaningful values. pathLength={1} normalises the circle's length to 1, so the visible fraction is
 * simply value / 100. The SVG is rotated -90° so the arc starts at 12 o'clock.
 */
function ConfidenceGauge({ value, tone, delay }: { value: number; tone: Tone; delay: number }) {
  const gid = useId();
  return (
    <div className="relative size-[68px] shrink-0" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value}
      aria-label={`${tone.label} confidence: ${value}% that the direction is right`}>
      <svg viewBox="0 0 68 68" className="size-full -rotate-90" aria-hidden>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={tone.from} />
            <stop offset="100%" stopColor={tone.to} />
          </linearGradient>
        </defs>
        <circle cx="34" cy="34" r="28" fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth="5" />
        <motion.circle
          cx="34" cy="34" r="28" fill="none" stroke={`url(#${gid})`} strokeWidth="5" strokeLinecap="round" pathLength={1}
          strokeDasharray="1 1"
          initial={{ strokeDashoffset: 1 }}
          animate={{ strokeDashoffset: 1 - value / 100 }}
          transition={{ duration: 1.1, ease: EASE, delay }}
          style={{ filter: `drop-shadow(0 0 4px ${tone.glow})` }}
        />
        {/* 9 o'clock before the -90° rotation = 6 o'clock on screen = the 50% point */}
        <line x1="1" y1="34" x2="10" y2="34" stroke="rgba(255,255,255,0.4)" strokeWidth="1.5" />
      </svg>
      <div className="absolute inset-0 grid place-items-center">
        <div className="text-center leading-none">
          <span className={cn("font-mono text-base font-semibold tabular-nums", tone.text)}>{value}</span>
          <span className={cn("font-mono text-[9px]", tone.text)}>%</span>
          <span className="mt-0.5 block font-mono text-[7px] uppercase tracking-[0.18em] text-muted-foreground">conf</span>
        </div>
      </div>
    </div>
  );
}
