"use client";

import { motion } from "framer-motion";
import { Bitcoin } from "lucide-react";
import { BitcoinRefreshButton } from "@/components/bitcoin/refresh-button";
import type { BitcoinRun } from "@/lib/types";

const EASE = [0.22, 1, 0.36, 1] as const;

/**
 * "System standby": shown until the first forecast exists.
 *
 *   radar   concentric rings + a rotating conic-gradient sweep (rotate 0 -> 360, linear, repeat forever) + blips that
 *           fade in and out at fixed positions, as if the sweep were finding signals
 *   logo    the Bitcoin mark breathing (scale 1 -> 1.06 -> 1) with a glow halo pulsing out of phase with it
 *   ghost   shimmer skeletons in the exact shape of the real dashboard (price, chart, four cards), so the layout doesn't
 *           jump when data arrives
 *
 * Every animation is a framer-motion `transition={{ repeat: Infinity }}`. The root template's
 * <MotionConfig reducedMotion="user"> turns transform animations (the sweep's rotation, the breathing scale) off for
 * users who ask for reduced motion; the shimmer has its own CSS media query.
 */
export function Standby({ run }: { run: BitcoinRun | null }) {
  const working = run?.status === "queued" || run?.status === "running";
  return (
    <div className="space-y-5">
      <div className="relative overflow-hidden rounded-2xl border border-emerald-400/15 bg-[#05070a]/90">
        <div aria-hidden className="terminal-grid pointer-events-none absolute inset-0 [mask-image:radial-gradient(circle_at_50%_45%,black,transparent_75%)]" />
        <div className="relative flex flex-col items-center px-6 pt-12 pb-10 text-center">
          <Radar active={working} />

          <motion.p initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3, duration: 0.6, ease: EASE }}
            className="mt-8 font-mono text-[11px] uppercase tracking-[0.3em] text-emerald-300/80">
            {working ? "Analysis in progress" : "System standby"}
          </motion.p>
          <motion.h2 initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.45, duration: 0.6, ease: EASE }}
            className="glow-text mt-3 max-w-xl text-balance text-xl font-semibold tracking-tight text-white sm:text-2xl">
            {working ? "Reading the wires and the tape…" : "Awaiting first forecast. The neural network is warming up…"}
          </motion.h2>
          <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.6, duration: 0.6 }}
            className="mt-3 max-w-md text-sm text-muted-foreground">
            {working
              ? "Fetching spot price, 60 days of closes and the latest headlines, then asking the model for four horizons. Usually under a minute."
              : "A forecast runs automatically every day at 09:00. Start one now to bring the terminal online."}
          </motion.p>
          <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.75, duration: 0.5 }} className="mt-7">
            <BitcoinRefreshButton run={run} size="lg" label="Initialize analysis" />
          </motion.div>
          <BootLog active={working} />
        </div>
      </div>

      <GhostDashboard />
    </div>
  );
}

function Radar({ active }: { active: boolean }) {
  const speed = active ? 2.2 : 4.5; // the sweep speeds up while a run is in flight
  const blips = [[22, 30], [70, 24], [64, 72], [30, 68], [48, 14]];
  return (
    <div className="relative size-52 sm:size-60" aria-hidden>
      {[1, 0.74, 0.48].map((s) => (
        <div key={s} className="absolute inset-0 m-auto rounded-full border border-emerald-400/20" style={{ width: `${s * 100}%`, height: `${s * 100}%` }} />
      ))}
      <div className="absolute inset-x-0 top-1/2 h-px bg-emerald-400/10" />
      <div className="absolute inset-y-0 left-1/2 w-px bg-emerald-400/10" />
      <motion.div className="absolute inset-0 rounded-full"
        style={{ background: "conic-gradient(from 0deg, rgba(52,211,153,0.45), rgba(52,211,153,0.08) 18%, transparent 30%)" }}
        animate={{ rotate: 360 }} transition={{ duration: speed, ease: "linear", repeat: Infinity }} />
      {blips.map(([x, y], i) => (
        <motion.span key={i} className="absolute size-1.5 rounded-full bg-cyan-300 shadow-[0_0_10px_2px_rgba(103,232,249,0.7)]"
          style={{ left: `${x}%`, top: `${y}%` }}
          animate={{ opacity: [0, 1, 0], scale: [0.6, 1.2, 0.6] }}
          transition={{ duration: 2.4, repeat: Infinity, delay: i * 0.7, ease: "easeInOut" }} />
      ))}
      <div className="absolute inset-0 grid place-items-center">
        <motion.div className="absolute size-20 rounded-full bg-amber-400/25 blur-2xl"
          animate={{ opacity: [0.35, 0.8, 0.35], scale: [0.9, 1.15, 0.9] }} transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }} />
        <motion.div className="relative grid size-16 place-items-center rounded-2xl bg-gradient-to-br from-amber-300 to-orange-500 text-[#1a0f00] shadow-[0_0_40px_-4px_rgba(251,191,36,0.7)] ring-1 ring-amber-200/50"
          animate={{ scale: [1, 1.06, 1] }} transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}>
          <Bitcoin className="size-9" strokeWidth={2.4} />
        </motion.div>
      </div>
    </div>
  );
}

/** Terminal-style boot lines that type in one after another. Purely decorative, so hidden from screen readers. */
function BootLog({ active }: { active: boolean }) {
  const lines = active
    ? ["> spot feed ............ coingecko / coinbase", "> indicators ........... sma · rsi · σ", "> news wire ............ 5 sources", "> model ................ forecasting 1d · 3d · 7d · 14d"]
    : ["> price feeds .......... standby", "> news wire ............ standby", "> forecast engine ...... idle"];
  return (
    <div aria-hidden className="mt-8 w-full max-w-md rounded-lg border border-white/[0.06] bg-black/40 px-4 py-3 text-left font-mono text-[11px] leading-6 text-emerald-300/70">
      {lines.map((l, i) => (
        <motion.div key={`${active}-${i}`} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.9 + i * 0.35, duration: 0.3 }}>
          {l}
        </motion.div>
      ))}
      <motion.span className="inline-block h-3.5 w-2 translate-y-0.5 bg-emerald-300/80" animate={{ opacity: [1, 0, 1] }} transition={{ duration: 1, repeat: Infinity }} />
    </div>
  );
}

/** Shimmer placeholders in the shape of the live dashboard. */
function GhostDashboard() {
  const block = "shimmer rounded-xl border border-white/[0.05]";
  return (
    <div aria-hidden className="space-y-5 opacity-80">
      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <div className={`${block} space-y-4 p-5`}>
          <div className="h-3 w-24 rounded bg-white/[0.06]" />
          <div className="h-12 w-4/5 rounded bg-white/[0.07]" />
          <div className="h-3 w-1/2 rounded bg-white/[0.05]" />
          <div className="grid grid-cols-3 gap-3 pt-2">{[0, 1, 2].map((i) => <div key={i} className="h-8 rounded bg-white/[0.05]" />)}</div>
        </div>
        <div className={`${block} relative h-56 overflow-hidden p-5`}>
          <svg viewBox="0 0 400 120" preserveAspectRatio="none" className="absolute inset-x-5 bottom-6 h-28 w-[calc(100%-2.5rem)] text-white/[0.07]">
            <path d="M0,90 C40,70 60,95 100,72 S170,40 210,58 S280,92 320,50 S380,30 400,40" fill="none" stroke="currentColor" strokeWidth="2" />
          </svg>
        </div>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className={`${block} flex items-end justify-between p-4`} style={{ animationDelay: `${i * 0.15}s` }}>
            <div className="space-y-2"><div className="h-3 w-16 rounded bg-white/[0.06]" /><div className="h-7 w-24 rounded bg-white/[0.07]" /><div className="h-3 w-14 rounded bg-white/[0.05]" /></div>
            <div className="size-14 rounded-full border-4 border-white/[0.06]" />
          </div>
        ))}
      </div>
    </div>
  );
}
