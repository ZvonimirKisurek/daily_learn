"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { motion } from "framer-motion";
import { ArrowRight, CheckCircle2, ListChecks, Loader2, PartyPopper, XCircle } from "lucide-react";
import { submitQuizAction } from "@/app/actions";
import { Button } from "@/components/ui/button";
import type { PublicQuestion, QuizResult } from "@/lib/types";
import { cn } from "@/lib/utils";

const LETTERS = ["A", "B", "C", "D", "E"];

/**
 * The reading's gate: answer every question, pass (>= 80%) and the hands-on exercise unlocks. Grading happens on the
 * server against an answer key the browser never sees. A failed try marks WHICH answers are wrong, not the right
 * options, so the fix comes from re-reading, not from guessing.
 */
export function ChapterQuiz({ taskId, questions, completed, score, home = "/" }: { taskId: string; questions: PublicQuestion[]; completed: boolean; score: number | null; home?: string }) {
  const [answers, setAnswers] = useState<(number | null)[]>(() => questions.map(() => null));
  const [result, setResult] = useState<QuizResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const passed = completed || result?.passed;

  if (completed && !result)
    return (
      <div className="glass mt-14 rounded-2xl p-6 text-center">
        <CheckCircle2 className="mx-auto mb-2 size-6 text-primary" />
        <h3 className="text-lg font-semibold">Quiz passed{score !== null && <> · {score}/{questions.length}</>}</h3>
        <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">Tomorrow, try to explain today&apos;s idea in two sentences without looking.</p>
        <Button asChild variant="outline" className="mt-5"><Link href={home}>Back to today&apos;s lesson <ArrowRight /></Link></Button>
      </div>
    );

  function choose(q: number, o: number) {
    if (passed) return;
    setAnswers((a) => a.map((x, i) => (i === q ? o : x)));
    setResult((r) => (r && !r.passed ? { ...r, results: r.results.map((x, i) => (i === q ? { ...x, correct: false, chosen: -1 } : x)) } : r));
  }

  function submit() {
    setError(null);
    start(async () => {
      const res = await submitQuizAction(taskId, answers as number[]);
      if (!res.ok) return setError(res.error);
      setResult(res.data);
    });
  }

  const ready = answers.every((a) => a !== null);
  return (
    <section aria-labelledby="quiz-h" className="mt-14">
      <div className="mb-4 flex items-center gap-2">
        <ListChecks className="size-5 text-info" />
        <h2 id="quiz-h" className="text-2xl font-semibold tracking-tight">Quick check</h2>
      </div>
      <p className="mb-6 text-sm text-muted-foreground">Answer all {questions.length} questions. Pass to unlock today&apos;s hands-on exercise. You can retry as often as you need.</p>
      <ol className="space-y-5">
        {questions.map((q, qi) => {
          const r = result?.results[qi];
          const marked = r && r.chosen === answers[qi];
          return (
            <li key={q.question} className={cn("glass rounded-2xl p-5", marked && (r.correct ? "ring-1 ring-primary/40" : "ring-1 ring-destructive/40"))}>
              <p className="mb-3 font-medium"><span className="mr-2 font-mono text-xs text-muted-foreground">{qi + 1}.</span>{q.question}</p>
              <div role="radiogroup" aria-label={`Question ${qi + 1}`} className="grid gap-2">
                {q.options.map((o, oi) => {
                  const chosen = answers[qi] === oi;
                  const right = passed && r?.answer === oi;
                  return (
                    <button key={o} type="button" role="radio" aria-checked={chosen} disabled={!!passed} onClick={() => choose(qi, oi)}
                      className={cn("flex items-start gap-3 rounded-xl border px-3.5 py-2.5 text-left text-sm transition-colors",
                        right ? "border-primary/50 bg-primary/10" : chosen ? "border-info/60 bg-info/10" : "border-white/10 hover:border-white/25 hover:bg-white/[0.04]")}>
                      <span className={cn("grid size-5 shrink-0 place-items-center rounded-md border font-mono text-[11px]", chosen ? "border-info text-info" : "border-white/20 text-muted-foreground")}>{LETTERS[oi]}</span>
                      <span>{o}</span>
                    </button>
                  );
                })}
              </div>
              {marked && (
                <p className={cn("mt-3 flex items-start gap-1.5 text-sm", r.correct ? "text-primary" : "text-destructive")}>
                  {r.correct ? <CheckCircle2 className="mt-0.5 size-4 shrink-0" /> : <XCircle className="mt-0.5 size-4 shrink-0" />}
                  <span>{r.correct ? r.explanation ?? "Correct." : "Not quite. Re-read the section this question is about, then pick again."}</span>
                </p>
              )}
            </li>
          );
        })}
      </ol>

      {error && <p role="alert" className="mt-4 text-sm text-destructive">{error}</p>}
      {result && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
          className={cn("mt-6 rounded-2xl border px-5 py-4", result.passed ? "border-primary/30 bg-primary/10" : "border-warn/30 bg-warn/5")}>
          {result.passed ? (
            <div className="flex flex-wrap items-center gap-3">
              <PartyPopper className="size-5 text-primary" />
              <p className="font-medium text-primary">Passed {result.correct}/{result.total}{result.completion && <> · +{result.completion.xp} XP</>}. The hands-on exercise is unlocked.</p>
              <Button asChild className="ml-auto"><Link href={home}>Continue the lesson <ArrowRight /></Link></Button>
            </div>
          ) : (
            <p className="text-sm"><span className="font-medium text-warn">{result.correct}/{result.total} correct.</span> You need {result.pass_mark} to pass. Fix the answers marked in red and try again.</p>
          )}
        </motion.div>
      )}
      {!passed && (
        <Button size="lg" className="mt-6" onClick={submit} disabled={!ready || pending}>
          {pending && <Loader2 className="animate-spin" />} {result ? "Check again" : "Check my answers"}
        </Button>
      )}
    </section>
  );
}
