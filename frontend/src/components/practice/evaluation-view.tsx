"use client";

import { motion } from "framer-motion";
import { CheckCircle2, FlaskConical, Lightbulb, ShieldAlert, Sparkles, XCircle } from "lucide-react";
import { Markdown } from "@/components/markdown";
import { skillLabel } from "@/lib/skills";
import type { Evaluation, SubmissionResult } from "@/lib/types";
import { cn } from "@/lib/utils";

function ScoreRing({ score, passed }: { score: number; passed: boolean }) {
  const r = 46, c = 2 * Math.PI * r;
  const color = passed ? "#34d399" : score >= 4 ? "#fbbf24" : "#f87171";
  return (
    <div className="relative grid size-32 shrink-0 place-items-center" role="img" aria-label={`Score ${score} out of 10`}>
      <svg viewBox="0 0 110 110" className="absolute inset-0 -rotate-90">
        <circle cx="55" cy="55" r={r} fill="none" stroke="rgba(255,255,255,0.09)" strokeWidth="8" />
        <motion.circle cx="55" cy="55" r={r} fill="none" stroke={color} strokeWidth="8" strokeLinecap="round" strokeDasharray={c}
          initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c * (1 - score / 10) }} transition={{ duration: 1.1, ease: [0.22, 1, 0.36, 1], delay: 0.15 }}
          style={{ filter: `drop-shadow(0 0 8px ${color}88)` }} />
      </svg>
      <div className="text-center"><span className="text-4xl font-semibold tabular-nums">{score}</span><span className="text-sm text-muted-foreground">/10</span></div>
    </div>
  );
}

const List = ({ items, tone, icon: Icon }: { items: string[]; tone: string; icon: typeof CheckCircle2 }) => (
  <ul className="space-y-2">{items.map((t) => <li key={t} className="flex gap-2.5 text-[15px] leading-relaxed"><Icon className={cn("mt-1 size-4 shrink-0", tone)} /><span>{t}</span></li>)}</ul>
);

/** The professor's verdict: green when passed, red when not, with the full rubric breakdown. */
export function EvaluationView({ ev, result }: { ev: Evaluation; result?: SubmissionResult | null }) {
  const passed = ev.passed;
  return (
    <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }} className="space-y-5" aria-live="polite">
      <div className={cn("relative overflow-hidden rounded-2xl border p-5", passed ? "border-emerald-400/30 bg-emerald-400/[0.07]" : "border-red-400/30 bg-red-400/[0.06]")}>
        <div aria-hidden className={cn("pointer-events-none absolute -right-10 -top-10 size-44 rounded-full blur-3xl", passed ? "bg-emerald-400/20" : "bg-red-400/15")} />
        <div className="relative flex flex-wrap items-center gap-5">
          <ScoreRing score={ev.score} passed={passed} />
          <div className="min-w-0 flex-1">
            <div className={cn("eyebrow mb-1 flex items-center gap-1.5 !text-xs", passed ? "!text-emerald-300" : "!text-red-300")}>
              {passed ? <CheckCircle2 className="size-4" /> : <XCircle className="size-4" />} {passed ? "Passed" : "Not yet"}
            </div>
            <p className="text-[17px] font-medium leading-snug">{ev.summary}</p>
            {result && (
              <p className="mt-2 text-sm text-muted-foreground">
                {result.skill ? <span className="inline-flex items-center gap-1.5 font-medium text-primary"><Sparkles className="size-4" /> {skillLabel(result.skill.slug)} +{result.skill.delta.toFixed(2)} → {result.skill.level.toFixed(2)}</span>
                  : passed ? "Already counted." : "Skill matrix unchanged: pass to earn points."}
                {" "}· attempt {result.attempts_used} of {result.attempts_max}
              </p>
            )}
          </div>
        </div>
      </div>

      {ev.tests && (
        <section className="glass rounded-2xl p-5">
          <h3 className="eyebrow mb-3 flex items-center gap-2"><FlaskConical className="size-3.5" /> Acceptance tests: {ev.tests.passed}/{ev.tests.expected} passed</h3>
          <ul className="grid gap-1.5 font-mono text-[13px] sm:grid-cols-2">
            {Object.entries(ev.tests.results).map(([name, status]) => (
              <li key={name} className="flex items-center gap-2">
                {status === "PASSED" ? <CheckCircle2 className="size-3.5 shrink-0 text-emerald-300" /> : <XCircle className="size-3.5 shrink-0 text-red-300" />}
                <span className="truncate" title={name}>{name}</span>{status !== "PASSED" && <span className="text-xs text-muted-foreground">{status.toLowerCase()}</span>}
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-muted-foreground">Counted by code from your pytest output. The task passes only when every test passes.</p>
        </section>
      )}

      <section className="glass rounded-2xl p-5">
        <h3 className="eyebrow mb-4">Rubric breakdown</h3>
        <ul className="space-y-4">
          {ev.criteria.map((c, i) => {
            const frac = c.points / c.max_points;
            const bar = frac >= 1 ? "from-emerald-300 to-emerald-400" : frac > 0 ? "from-amber-300 to-amber-400" : "from-red-400 to-red-500";
            return (
              <li key={c.criterion}>
                <div className="mb-1.5 flex items-baseline justify-between gap-3"><span className="font-medium">{c.criterion}</span><span className="font-mono text-sm tabular-nums text-muted-foreground">{c.points}/{c.max_points}</span></div>
                <div className="h-1.5 overflow-hidden rounded-full bg-white/10"><motion.div className={cn("h-full rounded-full bg-gradient-to-r", bar)} initial={{ width: 0 }} animate={{ width: `${Math.max(frac * 100, 2)}%` }} transition={{ duration: 0.8, delay: 0.2 + i * 0.1 }} /></div>
                <p className="mt-1.5 text-sm text-muted-foreground">{c.evidence}</p>
              </li>
            );
          })}
        </ul>
      </section>

      <div className="grid gap-5 md:grid-cols-2">
        <section className="glass rounded-2xl p-5"><h3 className="eyebrow mb-3 !text-emerald-300">What went well</h3><List items={ev.strengths} tone="text-emerald-300" icon={CheckCircle2} /></section>
        <section className="glass rounded-2xl p-5"><h3 className="eyebrow mb-3 !text-red-300">What is wrong</h3>
          {ev.problems.length ? <List items={ev.problems} tone="text-red-300" icon={ShieldAlert} /> : <p className="text-sm text-muted-foreground">Nothing material.</p>}</section>
      </div>
      <section className="glass rounded-2xl p-5"><h3 className="eyebrow mb-3 !text-sky-300">How to improve</h3><List items={ev.how_to_improve} tone="text-sky-300" icon={Lightbulb} /></section>

      <details open className="group glass rounded-2xl">
        <summary className="cursor-pointer list-none px-5 py-4 font-medium">The professor&apos;s detailed notes</summary>
        <div className="border-t border-white/[0.07] px-5 pb-5 pt-4"><Markdown compact>{ev.feedback_markdown}</Markdown></div>
      </details>
    </motion.div>
  );
}
