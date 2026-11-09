"use client";

import Link from "next/link";
import { useOptimistic, useState, useTransition } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, BookOpenText, ChevronDown, CircleCheck, Clock, FlaskConical, GraduationCap, Lightbulb, ListChecks, Lock, PartyPopper, TriangleAlert, Zap } from "lucide-react";
import { completeTaskAction } from "@/app/actions";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { skillLabel } from "@/lib/skills";
import type { Briefing, Task, TaskKind } from "@/lib/types";
import { cn } from "@/lib/utils";
import { GradedBadge, HandsOnSummary, InsightBody, ReadingSummary } from "./task-bodies";

const META: Record<TaskKind, { label: string; icon: typeof BookOpenText; tone: string; glow: string }> = {
  reading: { label: "Step 1 · Read + quiz", icon: BookOpenText, tone: "text-info", glow: "from-sky-400/20" },
  hands_on: { label: "Step 2 · Hands-on exercise", icon: FlaskConical, tone: "text-primary", glow: "from-emerald-400/20" },
  insight: { label: "Step 3 · Market essay", icon: Lightbulb, tone: "text-warn", glow: "from-amber-400/20" },
};

const Difficulty = ({ n }: { n: number }) => (
  <span className="inline-flex items-center gap-0.5" title={`Difficulty ${n}/3`} aria-label={`Difficulty ${n} of 3`}>
    {[1, 2, 3].map((i) => <span key={i} className={cn("size-1.5 rounded-full", i <= n ? "bg-foreground/70" : "bg-white/15")} />)}
  </span>
);

/**
 * Client Component: it owns interaction (optimistic ticks). Data arrives as props from a Server Component, so the first
 * paint already contains the real tasks.
 *
 * Optimistic UI: ticking a box runs `markDone` inside a transition so React shows the ticked state instantly; the Server
 * Action then does the real work and `refresh()`es the server data. If the action fails the props are unchanged, so the
 * tick visibly rolls back.
 *
 * Hands-on labs are different on purpose: they have NO checkbox. They are completed only by a PASSING graded submission
 * in the workspace, which is the only way that task can move your skill matrix.
 */
export function MissionBoard({ briefing, heading = "Today's Lesson", archived = false }: { briefing: Briefing; heading?: string; archived?: boolean }) {
  const [tasks, markDone] = useOptimistic(briefing.tasks, (current: Task[], id: string) =>
    current.map((t) => (t.id === id ? { ...t, completed: true } : t)),
  );
  const [, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [gain, setGain] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<string | null>(null);

  const done = tasks.filter((t) => t.completed).length;
  const p = briefing.payload;

  function complete(task: Task) {
    setError(null);
    startTransition(async () => {
      markDone(task.id);
      const res = await completeTaskAction(task.id);
      if (!res.ok) return setError(res.error);
      if (!res.data.already_completed)
        setGain((g) => ({ ...g, [task.id]: `+${res.data.xp} XP · +${res.data.skill.delta.toFixed(2)} ${skillLabel(res.data.skill.slug)}` }));
    });
  }

  // Lesson order on the learning path (legacy briefings, level === null, are not gated): quiz -> exercise -> essay.
  const gated = briefing.level !== null;
  const readingDone = tasks.some((t) => t.kind === "reading" && t.completed);
  const handsOnDone = tasks.some((t) => t.kind === "hands_on" && t.completed);
  const lockReason = (t: Task): string | null =>
    !gated || t.completed ? null
      : t.kind === "hands_on" && !readingDone ? "Unlocks when you pass the reading quiz"
      : t.kind === "insight" && !(readingDone && handsOnDone) ? "Unlocks when the quiz and the exercise are passed" : null;
  const lesson = p.lesson;

  const title = (t: Task) => (t.kind === "reading" ? p.morning_reading.chapter_title : t.kind === "hands_on" ? p.hands_on_task.title : p.market_insight.headline);
  const minutes = (t: Task) => (t.kind === "reading" ? p.morning_reading.estimated_minutes : t.kind === "hands_on" ? p.hands_on_task.estimated_minutes : null);

  return (
    <section aria-labelledby="mission-h">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <h2 id="mission-h" className="text-3xl font-semibold tracking-tight"><span className="gradient-text">{heading}</span></h2>
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs text-muted-foreground">{done}/{tasks.length} complete</span>
          <div className="h-1.5 w-32 overflow-hidden rounded-full bg-white/10" role="progressbar" aria-valuenow={done} aria-valuemin={0} aria-valuemax={tasks.length}>
            <motion.div className="h-full rounded-full bg-gradient-to-r from-emerald-300 to-sky-300" initial={false} animate={{ width: `${(done / tasks.length) * 100}%` }} transition={{ type: "spring", stiffness: 120, damping: 20 }} />
          </div>
        </div>
      </div>

      {lesson && (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="violet"><GraduationCap className="size-3" /> Level {lesson.level} · {lesson.title}</Badge>
          <span className="text-muted-foreground">{lesson.review ? "Review" : `Lesson ${lesson.lesson_number} of ${lesson.lessons_required}`}: <span className="text-foreground">{lesson.topic}</span></span>
        </div>
      )}

      {error && (
        <div role="alert" className="mb-3 flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          <TriangleAlert className="size-4" /> Couldn&apos;t save that: {error}
        </div>
      )}

      <motion.ul className="space-y-4" initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.09 } } }}>
        {tasks.map((t) => {
          const m = META[t.kind];
          const Icon = m.icon;
          const min = minutes(t);
          const isOpen = open === t.id;
          const locked = lockReason(t);
          const quizGate = t.kind === "reading" && t.has_quiz;
          return (
            <motion.li key={t.id} variants={{ hidden: { opacity: 0, y: 18 }, show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: [0.22, 1, 0.36, 1] } } }}
              whileHover={{ y: -2 }} className={cn("glass glass-hover relative overflow-hidden rounded-2xl", t.completed && "opacity-75")}>
              <div aria-hidden className={cn("pointer-events-none absolute -left-16 -top-16 size-48 rounded-full bg-gradient-to-br to-transparent blur-2xl", m.glow)} />
              <div className="relative flex items-start gap-4 p-5">
                {t.kind === "hands_on" || quizGate ? (
                  <span className={cn("mt-0.5 grid size-5 shrink-0 place-items-center rounded-md border", t.completed ? "border-primary bg-primary text-primary-foreground" : "border-input text-muted-foreground")}
                    title={t.completed ? "Passed" : quizGate ? "Completed by passing the quiz" : "Completed by passing the graded submission"}>
                    {t.completed ? <CircleCheck className="size-3.5" strokeWidth={3} /> : locked ? <Lock className="size-3" /> : quizGate ? <ListChecks className="size-3" /> : <Lock className="size-3" />}
                  </span>
                ) : (
                  <Checkbox className="mt-0.5" checked={t.completed} disabled={t.completed || !!locked} onCheckedChange={(v) => v === true && complete(t)} aria-label={`Mark "${title(t)}" complete`} />
                )}
                <div className="min-w-0 flex-1">
                  <div className={cn("eyebrow mb-1.5 flex items-center gap-1.5 !text-[11px]", m.tone)}><Icon className="size-3.5" /> {m.label}</div>
                  <p className={cn("text-lg font-semibold leading-snug tracking-tight", t.completed && "text-muted-foreground")}>{title(t)}</p>
                  <div className="mt-2.5 flex flex-wrap items-center gap-2">
                    <Badge variant="outline">{skillLabel(t.skill_slug)}</Badge>
                    {t.kind !== "insight" && <Difficulty n={t.difficulty} />}
                    {min && <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><Clock className="size-3" />{min} min</span>}
                    {t.kind === "hands_on" && <GradedBadge />}
                    {t.kind === "hands_on" && t.attempt_count > 0 && !t.completed && <Badge variant="warn">last score {t.evaluation_score}/10 · attempt {t.attempt_count}</Badge>}
                    {quizGate && !t.completed && t.quiz_attempts > 0 && <Badge variant="warn">quiz {t.quiz_score}/{p.morning_reading.quiz_questions} · try again</Badge>}
                    {locked && <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><Lock className="size-3" />{locked}</span>}
                    {(gain[t.id] || (t.completed && (t.xp_awarded || t.points_awarded))) && (
                      <Badge variant="primary"><Zap className="size-3" />{gain[t.id] ?? (t.xp_awarded ? `+${t.xp_awarded} XP` : `+${t.points_awarded?.toFixed(2)} ${skillLabel(t.skill_slug)}`)}</Badge>
                    )}
                  </div>
                </div>
                {t.kind !== "insight" && !locked && (
                  <Link href={t.kind === "reading" ? `/read/${t.id}` : `/practice/${t.id}`} className="mt-0.5 hidden shrink-0 items-center gap-1.5 rounded-lg bg-primary/15 px-3 py-1.5 text-sm font-medium text-primary ring-1 ring-primary/25 transition-colors hover:bg-primary/25 sm:inline-flex">
                    {t.kind === "reading" ? (t.completed ? "Re-read" : quizGate ? "Read + quiz" : "Read") : t.completed || archived ? "Review" : t.attempt_count > 0 ? "Continue" : "Start"} <ArrowRight className="size-3.5" />
                  </Link>
                )}
                <button onClick={() => setOpen(isOpen ? null : t.id)} aria-expanded={isOpen} aria-label={isOpen ? "Hide details" : "Show details"}
                  className="mt-1 rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground">
                  <ChevronDown className={cn("size-5 transition-transform duration-300", isOpen && "rotate-180")} />
                </button>
              </div>
              <AnimatePresence initial={false}>
                {isOpen && (
                  <motion.div key="body" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }} className="relative overflow-hidden border-t border-white/[0.07]">
                    <div className="px-5 pb-5 pt-4">
                      {t.kind === "reading" && <ReadingSummary r={p.morning_reading} task={t} />}
                      {t.kind === "hands_on" && <HandsOnSummary h={p.hands_on_task} task={t} />}
                      {t.kind === "insight" && <InsightBody m={p.market_insight} />}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.li>
          );
        })}
      </motion.ul>

      {done === tasks.length && !archived && (
        <motion.p initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} className="mt-5 flex items-center gap-2 rounded-xl border border-primary/25 bg-primary/10 px-4 py-3 text-sm text-primary">
          <PartyPopper className="size-4" /> {gated ? "Lesson complete: it counts toward your level exam. See you tomorrow." : "Mission complete. See you tomorrow."}
        </motion.p>
      )}
    </section>
  );
}
