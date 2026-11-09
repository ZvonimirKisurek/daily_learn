"use client";

import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, GraduationCap, Loader2, Send, TriangleAlert } from "lucide-react";
import { submitTaskAction } from "@/app/actions";
import { Button } from "@/components/ui/button";
import type { Evaluation, SubmissionResult, TaskDetail } from "@/lib/types";
import { cn } from "@/lib/utils";
import { EvaluationView } from "./evaluation-view";

const MAX = 20_000;
const PHASES = ["Reading your code…", "Comparing your output with what the code would print…", "Scoring each rubric criterion…", "Writing feedback…"];

/** Cosmetic progress text while the (real, 10-60 s) grading request is in flight. */
function Grading() {
  const [i, setI] = useState(0);
  useEffect(() => { const t = setInterval(() => setI((n) => Math.min(n + 1, PHASES.length - 1)), 5000); return () => clearInterval(t); }, []);
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} role="status" aria-live="polite"
      className="glass relative overflow-hidden rounded-2xl p-8 text-center">
      <motion.div aria-hidden className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-emerald-300 to-transparent" animate={{ x: ["-100%", "100%"] }} transition={{ repeat: Infinity, duration: 1.8, ease: "linear" }} />
      <motion.div animate={{ scale: [1, 1.12, 1] }} transition={{ repeat: Infinity, duration: 1.6 }} className="mx-auto mb-3 grid size-14 place-items-center rounded-2xl bg-primary/10 ring-1 ring-primary/30"><GraduationCap className="size-7 text-primary" /></motion.div>
      <p className="text-lg font-semibold">Professor is evaluating your work…</p>
      <AnimatePresence mode="wait"><motion.p key={i} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} className="mt-1.5 text-sm text-muted-foreground">{PHASES[i]}</motion.p></AnimatePresence>
      <p className="mt-4 text-xs text-muted-foreground">This usually takes 10-40 seconds. Your submission is saved.</p>
    </motion.div>
  );
}

/** The editor + submission + result column. Everything stateful lives here; the assignment text is a Server Component. */
export function Workspace({ detail }: { detail: TaskDetail }) {
  const { task } = detail;
  const draftKey = `mle-draft:${task.id}`;
  const [code, setCode] = useState(detail.submission ?? "");
  const [result, setResult] = useState<SubmissionResult | null>(null);
  const [evaluation, setEvaluation] = useState<Evaluation | null>(detail.evaluation);
  const [error, setError] = useState<string | null>(null);
  const [passed, setPassed] = useState(task.completed);
  const [used, setUsed] = useState(detail.attempts_used);
  const [pending, start] = useTransition();
  const area = useRef<HTMLTextAreaElement>(null);

  // Restore an unsent draft (localStorage may be unavailable: private mode, blocked storage).
  useEffect(() => {
    if (detail.submission) return;
    const t = setTimeout(() => { try { const d = localStorage.getItem(draftKey); if (d) setCode(d); } catch { /* ignore */ } }, 0);  // after hydration, outside the effect body
    return () => clearTimeout(t);
  }, [draftKey, detail.submission]);
  useEffect(() => {
    if (passed) return;
    const t = setTimeout(() => { try { localStorage.setItem(draftKey, code); } catch { /* ignore */ } }, 400);
    return () => clearTimeout(t);
  }, [code, draftKey, passed]);

  const left = detail.attempts_max - used;
  const lines = useMemo(() => Math.max(code.split("\n").length, 14), [code]);
  const tooShort = code.trim().length < 20;
  const archived = detail.archived;
  const locked = !passed && detail.locked;  // lesson order: the reading quiz comes first
  const canSubmit = !pending && !passed && !archived && !locked && !tooShort && code.length <= MAX && left > 0;

  function submit() {
    if (!canSubmit) return;
    setError(null);
    start(async () => {
      const res = await submitTaskAction(task.id, code);
      if (!res.ok) return setError(res.error);
      setResult(res.data);
      setEvaluation(res.data.evaluation);
      setUsed(res.data.attempts_used);
      if (res.data.passed) { setPassed(true); try { localStorage.removeItem(draftKey); } catch { /* ignore */ } }
    });
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Tab") {  // keep focus in the editor and indent, like a code editor
      e.preventDefault();
      const el = e.currentTarget, s = el.selectionStart;
      setCode(code.slice(0, s) + "    " + code.slice(el.selectionEnd));
      requestAnimationFrame(() => el.setSelectionRange(s + 4, s + 4));
    } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); }
  }

  return (
    <div className="space-y-5">
      <section className="glass overflow-hidden rounded-2xl">
        <div className="flex items-center justify-between border-b border-white/[0.07] bg-white/[0.03] px-4 py-2.5">
          <div className="flex items-center gap-1.5" aria-hidden><span className="size-2.5 rounded-full bg-red-400/70" /><span className="size-2.5 rounded-full bg-amber-300/70" /><span className="size-2.5 rounded-full bg-emerald-400/70" /></div>
          <span className="eyebrow">your submission</span>
          <span className={cn("font-mono text-xs tabular-nums", code.length > MAX ? "text-destructive" : "text-muted-foreground")}>{code.length.toLocaleString("en-US")} / {MAX.toLocaleString("en-US")}</span>
        </div>
        <div className="flex">
          <div aria-hidden className="select-none border-r border-white/[0.06] bg-black/20 px-3 py-4 text-right font-mono text-[13px] leading-[1.65] text-white/25">{Array.from({ length: lines }, (_, i) => <div key={i}>{i + 1}</div>)}</div>
          <textarea ref={area} value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={onKeyDown} readOnly={passed || archived} spellCheck={false} autoCapitalize="off" autoCorrect="off" rows={14}
            aria-label="Paste your code and the terminal output here" placeholder={"# Paste your code here, then the terminal output of running it.\n# Cmd/Ctrl+Enter submits."}
            className="min-h-[22rem] w-full resize-y bg-transparent px-4 py-4 font-mono text-[13px] leading-[1.65] text-[#e6edf3] outline-none placeholder:text-white/25 read-only:opacity-70" />
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-3">
        <Button size="lg" onClick={submit} disabled={!canSubmit}>{pending ? <Loader2 className="animate-spin" /> : passed ? <CheckCircle2 /> : <Send />}{passed ? "Passed" : "Submit for evaluation"}</Button>
        <span className="text-sm text-muted-foreground">{passed ? "Task complete." : locked ? locked : archived ? "This task is from the past and can no longer be submitted." : left > 0 ? <>{left} of {detail.attempts_max} attempts left</> : "No attempts left for this task."}</span>
        {!passed && tooShort && code.length > 0 && <span className="text-xs text-warn">Paste your code and its output first.</span>}
      </div>

      {error && <div role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive"><TriangleAlert className="mt-0.5 size-4 shrink-0" /> {error}</div>}

      <AnimatePresence mode="wait">
        {pending ? <Grading key="grading" /> : evaluation ? <EvaluationView key={`${used}-${evaluation.score}`} ev={evaluation} result={result} /> : null}
      </AnimatePresence>
    </div>
  );
}
