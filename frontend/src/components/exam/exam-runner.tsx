"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowLeft, ArrowRight, CheckCircle2, GraduationCap, Loader2, PartyPopper, RotateCcw, XCircle } from "lucide-react";
import { startExamAction, submitExamAction } from "@/app/actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Exam, ExamKind, ExamResult } from "@/lib/types";
import { cn } from "@/lib/utils";

const LETTERS = ["A", "B", "C", "D", "E"];

/** One question per screen, Duolingo-style. Start -> answer -> submit -> review every answer with its explanation. */
export function ExamRunner({ kind, intro }: { kind: ExamKind; intro: React.ReactNode }) {
  const [exam, setExam] = useState<Exam | null>(null);
  const [answers, setAnswers] = useState<(number | null)[]>([]);
  const [i, setI] = useState(0);
  const [result, setResult] = useState<ExamResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();

  function begin() {
    setError(null);
    start(async () => {
      const res = await startExamAction(kind);
      if (!res.ok) return setError(res.error);
      setExam(res.data);
      setAnswers(res.data.questions.map(() => null));
      setI(0);
      setResult(null);
    });
  }

  function finish() {
    if (!exam) return;
    setError(null);
    start(async () => {
      const res = await submitExamAction(exam.id, answers as number[]);
      if (!res.ok) return setError(res.error);
      setResult(res.data);
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  if (result) return <ExamReport r={result} retry={kind === "level" && !result.passed ? begin : undefined} pending={pending} />;

  if (!exam)
    return (
      <div className="glass rounded-2xl p-6">
        {intro}
        {error && <p role="alert" className="mt-4 text-sm text-destructive">{error}</p>}
        <Button size="lg" className="mt-6" onClick={begin} disabled={pending}>
          {pending ? <Loader2 className="animate-spin" /> : <GraduationCap />} {pending ? (kind === "level" ? "Writing your exam (up to a minute)..." : "Preparing...") : "Start the exam"}
        </Button>
      </div>
    );

  const q = exam.questions[i];
  const last = i === exam.questions.length - 1;
  const answered = answers.filter((a) => a !== null).length;
  return (
    <div>
      <div className="mb-5 flex items-center gap-3">
        <span className="font-mono text-xs text-muted-foreground">{i + 1} / {exam.total}</span>
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/10" role="progressbar" aria-valuenow={answered} aria-valuemin={0} aria-valuemax={exam.total}>
          <motion.div className="h-full rounded-full bg-gradient-to-r from-emerald-300 to-sky-300" animate={{ width: `${(answered / exam.total) * 100}%` }} />
        </div>
        {q.tier && <Badge variant="outline">tier {q.tier}</Badge>}
      </div>
      <AnimatePresence mode="wait">
        <motion.div key={i} initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -24 }} transition={{ duration: 0.2 }} className="glass rounded-2xl p-6">
          <p className="mb-4 text-lg font-medium leading-snug">{q.question}</p>
          <div role="radiogroup" aria-label={`Question ${i + 1}`} className="grid gap-2.5">
            {q.options.map((o, oi) => (
              <button key={o} type="button" role="radio" aria-checked={answers[i] === oi}
                onClick={() => setAnswers((a) => a.map((x, k) => (k === i ? oi : x)))}
                className={cn("flex items-start gap-3 rounded-xl border px-4 py-3 text-left text-[15px] transition-colors",
                  answers[i] === oi ? "border-info/60 bg-info/10" : "border-white/10 hover:border-white/25 hover:bg-white/[0.04]")}>
                <span className={cn("grid size-6 shrink-0 place-items-center rounded-md border font-mono text-xs", answers[i] === oi ? "border-info text-info" : "border-white/20 text-muted-foreground")}>{LETTERS[oi]}</span>
                <span>{o}</span>
              </button>
            ))}
          </div>
        </motion.div>
      </AnimatePresence>
      {error && <p role="alert" className="mt-4 text-sm text-destructive">{error}</p>}
      <div className="mt-5 flex items-center gap-3">
        <Button variant="outline" onClick={() => setI(i - 1)} disabled={i === 0}><ArrowLeft /> Back</Button>
        {last ? (
          <Button className="ml-auto" onClick={finish} disabled={pending || answered < exam.total}>
            {pending && <Loader2 className="animate-spin" />} Submit {answered < exam.total && `(${exam.total - answered} unanswered)`}
          </Button>
        ) : (
          <Button className="ml-auto" onClick={() => setI(i + 1)} disabled={answers[i] === null}>Next <ArrowRight /></Button>
        )}
      </div>
    </div>
  );
}

function ExamReport({ r, retry, pending }: { r: ExamResult; retry?: () => void; pending: boolean }) {
  const up = r.new_level > r.previous_level;
  const good = r.kind === "placement" ? true : r.passed;
  return (
    <div>
      <motion.div initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }}
        className={cn("glass rounded-2xl p-6 text-center", good ? "ring-1 ring-primary/30" : "ring-1 ring-warn/30")}>
        {good ? <PartyPopper className="mx-auto mb-2 size-7 text-primary" /> : <RotateCcw className="mx-auto mb-2 size-7 text-warn" />}
        <p className="font-mono text-4xl font-semibold">{r.correct}<span className="text-muted-foreground">/{r.total}</span></p>
        <h2 className="mt-2 text-xl font-semibold">
          {r.kind === "placement"
            ? `You start at Level ${r.new_level}: ${r.new_level_title}${up ? "" : " (foundations first)"}`
            : r.passed ? `Level passed! Welcome to Level ${r.new_level}: ${r.new_level_title}` : `Not yet: you need ${r.pass_mark}/${r.total}.`}
        </h2>
        <p className="mx-auto mt-1 max-w-lg text-sm text-muted-foreground">
          {r.kind === "placement" ? "Levels below your start are marked as known. The placement can only ever move you up."
            : r.passed ? `+${r.xp} XP. New topics start with tomorrow's lesson.` : "Read the explanations below. Each retry is a brand-new exam, so understanding beats memorising."}
        </p>
        {r.placement && (
          <ul className="mx-auto mt-4 flex max-w-xl flex-wrap justify-center gap-2">
            {r.placement.tiers.map((t) => (
              <li key={t.tier}><Badge variant={t.tier <= r.placement!.known_tier ? "primary" : "outline"}>{t.name} {t.correct}/{t.total}</Badge></li>
            ))}
          </ul>
        )}
        <div className="mt-5 flex flex-wrap justify-center gap-3">
          <Button asChild><Link href="/">Today&apos;s lesson <ArrowRight /></Link></Button>
          <Button asChild variant="outline"><Link href="/path">See the path</Link></Button>
          {retry && <Button variant="outline" onClick={retry} disabled={pending}>{pending && <Loader2 className="animate-spin" />} Try a new exam</Button>}
        </div>
      </motion.div>
      <h3 className="eyebrow mb-3 mt-10">Review</h3>
      <ol className="space-y-3">
        {r.results.map((q, qi) => (
          <li key={qi} className="glass rounded-2xl p-5">
            <p className="flex items-start gap-2 font-medium">
              {q.correct ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-primary" /> : <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" />}
              <span>{q.question}</span>
            </p>
            <ul className="mt-2 space-y-1 pl-6 text-sm">
              {q.options.map((o, oi) => (
                <li key={o} className={cn(oi === q.answer ? "text-primary" : oi === q.chosen ? "text-destructive line-through" : "text-muted-foreground")}>
                  {LETTERS[oi]}. {o}
                </li>
              ))}
            </ul>
            {q.explanation && <p className="mt-2 pl-6 text-sm text-muted-foreground">{q.explanation}</p>}
          </li>
        ))}
      </ol>
    </div>
  );
}
