"use client";

import { useState, useTransition } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { BookOpen, CheckCircle2, EyeOff, Loader2, Sparkles, TriangleAlert, Wand2 } from "lucide-react";
import { checkReviewAction, gradeReviewAction, revealReferenceAction, suspendReviewAction } from "@/app/actions";
import { Markdown } from "@/components/markdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { skillLabel } from "@/lib/skills";
import type { RecallCheck, ReviewCard } from "@/lib/types";
import { cn } from "@/lib/utils";

const SOURCE: Record<ReviewCard["source"], string> = { chapter: "Chapter question", teachback: "Teach-back", primary_source: "Primary source" };
const GRADES = [
  { g: 1, label: "Again", hint: "could not recall", cls: "border-red-400/40 hover:bg-red-400/10" },
  { g: 2, label: "Hard", hint: "with real effort", cls: "border-amber-300/40 hover:bg-amber-300/10" },
  { g: 3, label: "Good", hint: "recalled correctly", cls: "border-emerald-400/40 hover:bg-emerald-400/10" },
  { g: 4, label: "Easy", hint: "instant, certain", cls: "border-sky-400/40 hover:bg-sky-400/10" },
];
const VERDICT = { correct: "text-emerald-300", partial: "text-amber-300", incorrect: "text-red-300" } as const;

/**
 * One card at a time: answer FROM MEMORY first (that effort is the learning), then optionally ask the AI to check it
 * against the source, or reveal the source yourself, then grade. The grade schedules the next review (SM-2).
 */
export function ReviewSession({ initial }: { initial: ReviewCard[] }) {
  const [queue, setQueue] = useState(initial);
  const [answer, setAnswer] = useState("");
  const [check, setCheck] = useState<RecallCheck | null>(null);
  const [reference, setReference] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(0);
  const [pending, start] = useTransition();
  const card = queue[0];

  function reset() { setAnswer(""); setCheck(null); setReference(null); setError(null); }

  function runCheck() {
    if (!card || answer.trim().length < 3) return;
    setError(null);
    start(async () => {
      const r = await checkReviewAction(card.id, answer);
      if (r.ok) setCheck(r.data); else setError(r.error);
    });
  }
  function reveal() {
    if (!card) return;
    start(async () => {
      const r = await revealReferenceAction(card.id);
      if (r.ok) setReference(r.data); else setError(r.error);
    });
  }
  function grade(g: number) {
    if (!card) return;
    start(async () => {
      const r = await gradeReviewAction(card.id, g, answer, check);
      if (!r.ok) return setError(r.error);
      setDone((n) => n + 1);
      setQueue((q) => (g === 1 ? [...q.slice(1), { ...card, lapses: card.lapses + 1 }] : q.slice(1)));  // "again" comes back this session
      reset();
    });
  }
  function suspend() {
    if (!card) return;
    start(async () => {
      const r = await suspendReviewAction(card.id);
      if (!r.ok) return setError(r.error);
      setQueue((q) => q.slice(1));
      reset();
    });
  }

  if (!card) {
    return (
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="glass rounded-2xl p-8 text-center">
        <CheckCircle2 className="mx-auto mb-3 size-10 text-primary" />
        <p className="text-lg font-semibold">{done ? `Done: ${done} review${done === 1 ? "" : "s"} today.` : "Nothing is due."}</p>
        <p className="mt-1 text-sm text-muted-foreground">Cards come back just before you would forget them. New ones arrive with every chapter.</p>
        <Link href="/" className="mt-5 inline-block text-sm text-primary underline-offset-4 hover:underline">Back to today&apos;s mission</Link>
      </motion.div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>{queue.length} left{done ? ` · ${done} done` : ""}</span>
        <button onClick={suspend} disabled={pending} className="inline-flex items-center gap-1.5 text-xs hover:text-foreground"><EyeOff className="size-3.5" /> Suspend this card</button>
      </div>
      <AnimatePresence mode="wait">
        <motion.section key={card.id + card.lapses} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} className="glass rounded-2xl p-6">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Badge variant={card.source === "teachback" ? "violet" : card.source === "primary_source" ? "info" : "primary"}>{SOURCE[card.source]}</Badge>
            <Badge variant="outline">{skillLabel(card.skill_slug)}</Badge>
            {card.reps > 0 && <Badge variant="outline">interval {Math.round(card.interval_days)}d</Badge>}
            {card.lapses > 0 && <Badge variant="warn">{card.lapses} lapse{card.lapses === 1 ? "" : "s"}</Badge>}
          </div>
          <h2 className="text-xl font-semibold leading-snug">{card.prompt}</h2>

          <label className="mt-5 block">
            <span className="eyebrow mb-2 block">Your answer, from memory</span>
            <textarea value={answer} onChange={(e) => setAnswer(e.target.value)} rows={6} disabled={pending}
              placeholder="Explain it as if teaching a colleague. No notes, no searching: the effort of recalling is what makes it stick."
              className="w-full resize-y rounded-xl border border-white/10 bg-black/20 p-4 text-[15px] leading-relaxed outline-none focus:border-primary/50" />
          </label>

          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="outline" onClick={runCheck} disabled={pending || answer.trim().length < 3}>
              {pending && !check ? <Loader2 className="animate-spin" /> : <Wand2 />} Check with AI
            </Button>
            {card.has_reference && <Button variant="ghost" onClick={reveal} disabled={pending}><BookOpen /> Show the source</Button>}
            {card.task_id && card.source !== "teachback" && <Link href={`/read/${card.task_id}`} className="self-center text-xs text-muted-foreground underline-offset-4 hover:underline">open the chapter</Link>}
          </div>

          {error && <p role="alert" className="mt-3 flex items-start gap-2 text-sm text-destructive"><TriangleAlert className="mt-0.5 size-4 shrink-0" /> {error}</p>}

          {check && (
            <div className="mt-5 rounded-xl border border-white/10 bg-white/[0.03] p-4">
              <div className={cn("eyebrow mb-2 flex items-center gap-1.5 !text-xs", VERDICT[check.verdict])}><Sparkles className="size-3.5" /> {check.verdict}</div>
              <p className="text-[15px]">{check.feedback}</p>
              {check.missing_points.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted-foreground">{check.missing_points.map((p) => <li key={p}>{p}</li>)}</ul>}
              <details className="mt-3"><summary className="cursor-pointer text-sm text-muted-foreground">Model answer</summary><p className="mt-2 text-[15px] leading-relaxed">{check.model_answer}</p></details>
            </div>
          )}
          {reference && (
            <details open className="mt-5 rounded-xl border border-white/10 bg-white/[0.02]">
              <summary className="cursor-pointer px-4 py-3 text-sm text-muted-foreground">Source text</summary>
              <div className="max-h-96 overflow-y-auto border-t border-white/10 px-4 pb-4 pt-3"><Markdown compact>{reference}</Markdown></div>
            </details>
          )}

          <div className="mt-6">
            <div className="eyebrow mb-2">How well did you recall it?{check ? ` (AI suggests: ${GRADES[check.suggested_grade - 1].label})` : ""}</div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {GRADES.map(({ g, label, hint, cls }) => (
                <button key={g} onClick={() => grade(g)} disabled={pending}
                  className={cn("rounded-xl border bg-white/[0.02] px-3 py-2.5 text-left transition-colors disabled:opacity-50", cls, check?.suggested_grade === g && "ring-2 ring-white/30")}>
                  <div className="font-medium">{label}</div><div className="text-xs text-muted-foreground">{hint}</div>
                </button>
              ))}
            </div>
          </div>
        </motion.section>
      </AnimatePresence>
    </div>
  );
}
