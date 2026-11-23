"use client";

import { useState, useTransition } from "react";
import { CheckCircle2, Lightbulb, Loader2, Sparkles, XCircle } from "lucide-react";
import { answerMathAction } from "@/app/actions";
import { Markdown } from "@/components/markdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { MathAnswer, MathDrill, MathProblem } from "@/lib/types";
import { cn } from "@/lib/utils";

function fmt(x: number) {
  return Number.isInteger(x) ? String(x) : x.toFixed(4).replace(/0+$/, "");
}

function ProblemCard({ day, p }: { day: string; p: MathProblem }) {
  const [value, setValue] = useState("");
  const [hint, setHint] = useState(false);
  const [state, setState] = useState<{ correct: boolean | null; solution: string | null; expected: number | null; firstCorrect: boolean | null }>(
    { correct: p.correct, solution: p.solution, expected: p.expected, firstCorrect: p.correct });
  const [last, setLast] = useState<MathAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!value.trim()) return;
    setError(null);
    start(async () => {
      const r = await answerMathAction(day, p.idx, value);
      if (!r.ok) return setError(r.error);
      setLast(r.data);
      setState({ correct: r.data.correct, solution: r.data.solution, expected: r.data.expected, firstCorrect: r.data.counted_correct });
    });
  }

  const attempted = state.solution !== null;
  return (
    <section className={cn("glass rounded-2xl p-5", attempted && (state.firstCorrect ? "ring-1 ring-emerald-400/30" : "ring-1 ring-red-400/25"))}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground">#{p.idx + 1}</span>
        <Badge variant="outline">{p.area}</Badge>
        <Badge variant="outline">{p.topic}</Badge>
        {attempted && (state.firstCorrect ? <Badge variant="primary">correct first try</Badge> : <Badge variant="warn">missed: comes back in review</Badge>)}
      </div>
      <div className="text-[15px] leading-relaxed"><Markdown compact>{p.prompt}</Markdown></div>
      <form onSubmit={submit} className="mt-4 flex flex-wrap items-center gap-2">
        <input value={value} onChange={(e) => setValue(e.target.value)} inputMode="decimal" placeholder={attempted ? `your answer: ${p.answer_given ?? "…"}` : "e.g. 0.25, -3, 1/4"}
          aria-label={`Answer to problem ${p.idx + 1}`} className="h-10 w-44 rounded-lg border border-white/10 bg-black/25 px-3 font-mono outline-none focus:border-primary/50" />
        <Button type="submit" disabled={pending || !value.trim()}>{pending ? <Loader2 className="animate-spin" /> : null}{attempted ? "Try again" : "Check"}</Button>
        {!attempted && <Button type="button" variant="ghost" onClick={() => setHint((h) => !h)}><Lightbulb /> Hint</Button>}
      </form>
      {hint && !attempted && <p className="mt-2 text-sm text-muted-foreground">{p.hint}</p>}
      {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
      {last && (
        <p className={cn("mt-3 flex items-center gap-1.5 text-sm font-medium", last.correct ? "text-emerald-300" : "text-red-300")}>
          {last.correct ? <CheckCircle2 className="size-4" /> : <XCircle className="size-4" />}
          {last.correct ? "Correct." : `Not quite: the answer is ${fmt(last.expected)}.`}
          {last.skill && <span className="ml-2 inline-flex items-center gap-1 text-primary"><Sparkles className="size-3.5" /> Math +{last.skill.delta.toFixed(2)}</span>}
          {!last.first_attempt && <span className="ml-2 font-normal text-muted-foreground">(practice: only the first attempt counts)</span>}
        </p>
      )}
      {attempted && state.solution && (
        <details open={!state.firstCorrect} className="mt-3 rounded-xl border border-white/10 bg-white/[0.02]">
          <summary className="cursor-pointer px-4 py-2.5 text-sm text-muted-foreground">Worked solution{state.expected !== null ? ` (answer ${fmt(state.expected)})` : ""}</summary>
          <div className="border-t border-white/10 px-4 pb-3 pt-2 text-[15px]"><Markdown compact>{state.solution}</Markdown></div>
        </details>
      )}
    </section>
  );
}

/** Today's five problems. Generated and graded by code (exact answers), so the key is never wrong. */
export function MathDrillView({ drill }: { drill: MathDrill }) {
  return <div className="space-y-4">{drill.problems.map((p) => <ProblemCard key={`${drill.day}-${p.idx}`} day={drill.day} p={p} />)}</div>;
}
