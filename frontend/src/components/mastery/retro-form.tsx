"use client";

import { useState, useTransition } from "react";
import Link from "next/link";
import { CheckCircle2, Circle, Globe, Loader2, Send, TriangleAlert } from "lucide-react";
import { submitRetroAction } from "@/app/actions";
import { Button } from "@/components/ui/button";
import type { Retro } from "@/lib/types";
import { cn } from "@/lib/utils";

const field = "w-full rounded-xl border border-white/10 bg-black/20 p-3.5 text-[15px] leading-relaxed outline-none focus:border-primary/50";

/**
 * The Sunday retrospective. It cannot be submitted until the week is over and every teach-back question has been
 * answered from memory in Review; every milestone needs evidence (or an honest "not done: why"). The decision you
 * write here is read by next week's planner.
 */
export function RetroForm({ retro }: { retro: Retro }) {
  const [evidence, setEvidence] = useState<Record<string, string>>(Object.fromEntries(retro.milestones.map((m) => [m, ""])));
  const [reflection, setReflection] = useState("");
  const [decision, setDecision] = useState("");
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<Retro | null>(null);
  const [pending, start] = useTransition();
  const closesQuarter = !!retro.quarter && retro.roadmap_week === retro.quarter.weeks[1];

  const missing = retro.milestones.filter((m) => evidence[m].trim().length < 3).length;
  const ready = retro.can_submit && !missing && reflection.trim().length >= 50 && decision.trim().length >= 10 && (!closesQuarter || /^https?:\/\/\S+$/.test(url.trim()));

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setError(null);
    start(async () => {
      const r = await submitRetroAction(retro.week_start, {
        milestone_evidence: retro.milestones.map((m) => ({ milestone: m, evidence: evidence[m].trim() })),
        reflection: reflection.trim(), decision: decision.trim(), public_artifact_url: url.trim() || null,
      });
      if (r.ok) setSaved(r.data); else setError(r.error);
    });
  }

  if (saved) {
    return (
      <div className="glass rounded-2xl p-6 text-center">
        <CheckCircle2 className="mx-auto mb-3 size-10 text-primary" />
        <p className="text-lg font-semibold">Retrospective saved.</p>
        <p className="mt-1 text-sm text-muted-foreground">Your decision will steer next week&apos;s plan, and this week&apos;s tasks are unlocked.</p>
        <Link href="/" className="mt-4 inline-block text-sm text-primary underline-offset-4 hover:underline">Back to today&apos;s mission</Link>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-6">
      {retro.blockers.length > 0 && (
        <ul className="space-y-1.5 rounded-xl border border-warn/30 bg-warn/[0.06] p-4 text-sm">
          {retro.blockers.map((b) => <li key={b}>{b}</li>)}
          {retro.teachback.some((t) => !t.answered) && <li><Link href="/review" className="text-primary underline-offset-4 hover:underline">Go to Review</Link></li>}
        </ul>
      )}

      <section className="glass rounded-2xl p-5">
        <h2 className="eyebrow mb-1">1 · Teach-back (answered from memory in Review)</h2>
        <p className="mb-3 text-sm text-muted-foreground">If you could not answer one, that is the most useful thing this page can tell you.</p>
        <ul className="space-y-2">
          {retro.teachback.map((t) => (
            <li key={t.id} className="flex gap-2.5 text-[15px]">
              {t.answered ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-primary" /> : <Circle className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}{t.prompt}
            </li>
          ))}
          {!retro.teachback.length && <li className="text-sm text-muted-foreground">No teach-back questions for this week.</li>}
        </ul>
      </section>

      <section className="glass rounded-2xl p-5">
        <h2 className="eyebrow mb-1">2 · Evidence audit</h2>
        <p className="mb-4 text-sm text-muted-foreground">For each milestone, point at an artifact: a commit, a notebook, a plot, a number. No artifact means not done; write &quot;not done: why&quot;.</p>
        <div className="space-y-4">
          {retro.milestones.map((m, i) => (
            <label key={m} className="block">
              <span className="mb-1.5 block text-[15px] font-medium"><span className="mr-2 font-mono text-xs text-muted-foreground">{i + 1}</span>{m}</span>
              <input value={evidence[m]} onChange={(e) => setEvidence({ ...evidence, [m]: e.target.value })} disabled={pending || !retro.can_submit}
                placeholder="https://github.com/you/rag-lab/commit/… · recall@5 0.81 · not done: ran out of time on Thursday" className={cn(field, "py-2.5")} />
            </label>
          ))}
          {!retro.milestones.length && <p className="text-sm text-muted-foreground">No milestones were set for this week.</p>}
        </div>
      </section>

      <section className="glass rounded-2xl p-5">
        <h2 className="eyebrow mb-3">3 · Reflection and decision</h2>
        <label className="block">
          <span className="mb-1.5 block text-sm text-muted-foreground">What worked, what did not, and what you still cannot explain (at least 50 characters)</span>
          <textarea value={reflection} onChange={(e) => setReflection(e.target.value)} rows={5} disabled={pending || !retro.can_submit} className={field} />
        </label>
        <label className="mt-4 block">
          <span className="mb-1.5 block text-sm text-muted-foreground">Your decision for next week: keep going, slow down, repeat a day, more of something. The planner reads this.</span>
          <textarea value={decision} onChange={(e) => setDecision(e.target.value)} rows={2} disabled={pending || !retro.can_submit} className={field} />
        </label>
        {(closesQuarter || retro.quarter) && (
          <label className="mt-4 block">
            <span className="mb-1.5 flex items-center gap-1.5 text-sm text-muted-foreground"><Globe className="size-3.5" /> Link to your public artifact{closesQuarter ? " (required: this week closes the quarter)" : " (optional)"}</span>
            <input value={url} onChange={(e) => setUrl(e.target.value)} disabled={pending || !retro.can_submit} placeholder="https://…" className={cn(field, "py-2.5")} />
          </label>
        )}
      </section>

      {error && <p role="alert" className="flex items-start gap-2 text-sm text-destructive"><TriangleAlert className="mt-0.5 size-4 shrink-0" /> {error}</p>}
      <div className="flex flex-wrap items-center gap-3">
        <Button size="lg" type="submit" disabled={!ready || pending}>{pending ? <Loader2 className="animate-spin" /> : <Send />} Submit retrospective</Button>
        {retro.can_submit && missing > 0 && <span className="text-sm text-muted-foreground">{missing} milestone{missing === 1 ? "" : "s"} without evidence</span>}
      </div>
    </form>
  );
}
