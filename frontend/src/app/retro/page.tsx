import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { CalendarCheck2, Globe } from "lucide-react";
import { ApiProblem } from "@/components/api-problem";
import { RetroForm } from "@/components/mastery/retro-form";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { attempt, getMastery, getRetro } from "@/lib/api";
import type { Retro } from "@/lib/types";

export const metadata: Metadata = { title: "Weekly retrospective" };
type SearchParams = Promise<Record<string, string | string[] | undefined>>;

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const pct = (v: number | null) => (v === null ? "–" : `${Math.round(v * 100)}%`);
const shift = (iso: string, days: number) => {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};

export default function RetroPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <div className="mx-auto max-w-3xl">
      <header className="mb-8">
        <div className="eyebrow mb-3 flex items-center gap-2 !text-primary"><CalendarCheck2 className="size-3.5" /> Weekly retrospective · ~30 minutes, Sunday</div>
        <h1 className="text-4xl font-semibold tracking-tight"><span className="gradient-text">What did you actually learn?</span></h1>
        <p className="mt-3 max-w-2xl text-muted-foreground">
          Completing tasks is a behaviour, not proof of learning. This is where you prove it: answers from memory, evidence for each milestone,
          and a decision for next week. Next week&apos;s tasks stay locked until it is done.
        </p>
      </header>
      <Suspense fallback={<Skeleton className="h-[32rem] rounded-2xl" />}><Content searchParams={searchParams} /></Suspense>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3"><div className="eyebrow mb-1 !text-[10px]">{label}</div><div className="text-lg font-semibold tabular-nums">{value}</div></div>;
}

function Numbers({ r }: { r: Retro }) {
  const n = r.numbers;
  const h = n.by_kind.hands_on ?? { done: 0, total: 0 };
  return (
    <section className="glass mb-6 rounded-2xl p-5">
      <h2 className="eyebrow mb-3">The numbers</h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="tasks" value={`${n.tasks_done}/${n.tasks_total}`} />
        <Stat label="hands-on passed" value={`${n.hands_on_passed}/${h.total}`} />
        <Stat label="math first-try" value={`${n.math_correct}/${n.math_attempted}`} />
        <Stat label="review retention" value={pct(n.review_retention)} />
      </div>
      {n.click_bursts > 0 && <p className="mt-3 text-sm text-warn">{n.click_bursts} burst(s) of 3+ completions within 10 minutes: tick tasks when you finish them, not at the end of the day.</p>}
      {h.total > 0 && h.done / h.total < 0.6 && <p className="mt-2 text-sm text-warn">You passed fewer than 60% of the hands-on labs. Building is where most of the learning happens.</p>}
    </section>
  );
}

async function Content({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const raw = Array.isArray(sp.week) ? sp.week[0] : sp.week;
  let week = raw && DATE.test(raw) ? raw : null;
  if (!week) {
    const [m, err] = await attempt(getMastery());
    if (!m) return <ApiProblem error={err} />;
    week = m.retro.week_start;
  }
  const [retro, err] = await attempt(getRetro(week));
  if (!retro) return <ApiProblem error={err} />;
  return (
    <div>
      <nav className="mb-5 flex items-center justify-between text-sm">
        <Link href={`/retro?week=${shift(week, -7)}`} className="text-muted-foreground hover:text-foreground">← previous week</Link>
        <span className="font-medium">Week of {retro.week_start}{retro.roadmap_week ? ` · roadmap week ${retro.roadmap_week}` : ""}</span>
        <Link href={`/retro?week=${shift(week, 7)}`} className="text-muted-foreground hover:text-foreground">next week →</Link>
      </nav>
      {retro.theme_title && <p className="mb-2 text-lg font-semibold">{retro.theme_title}</p>}
      {retro.deliverable && <p className="mb-5 text-sm text-muted-foreground"><span className="eyebrow mr-2">Deliverable</span>{retro.deliverable}</p>}
      {retro.quarter && (
        <div className="mb-6 rounded-xl border border-violet/30 bg-violet/[0.06] p-4 text-sm">
          <div className="mb-1 flex items-center gap-2 font-medium"><Globe className="size-4 text-violet" /> Quarter {retro.quarter.index}: {retro.quarter.title} <Badge variant="violet">weeks {retro.quarter.weeks[0]}-{retro.quarter.weeks[1]}</Badge></div>
          <p className="text-muted-foreground">{retro.quarter.public_deliverable}</p>
        </div>
      )}
      <Numbers r={retro} />
      {retro.retro ? (
        <section className="glass rounded-2xl p-5">
          <h2 className="eyebrow mb-3">Submitted {new Date(retro.retro.submitted_at).toLocaleDateString("en-US", { dateStyle: "medium" })}</h2>
          <ul className="mb-4 space-y-2 text-[15px]">{retro.retro.milestone_evidence.map((e) => <li key={e.milestone}><span className="font-medium">{e.milestone}</span><br /><span className="text-muted-foreground">{e.evidence}</span></li>)}</ul>
          <p className="whitespace-pre-wrap text-[15px]"><span className="eyebrow mr-2">Reflection</span>{retro.retro.reflection}</p>
          <p className="mt-3 whitespace-pre-wrap text-[15px]"><span className="eyebrow mr-2">Decision</span>{retro.retro.decision}</p>
          {retro.retro.public_artifact_url && <p className="mt-3 text-sm"><a href={retro.retro.public_artifact_url} target="_blank" rel="noopener noreferrer" className="text-info underline-offset-4 hover:underline">{retro.retro.public_artifact_url}</a></p>}
        </section>
      ) : !retro.required && !retro.teachback.length ? (
        <p className="glass rounded-2xl p-5 text-sm text-muted-foreground">No briefings that week, so nothing to review.</p>
      ) : (
        <RetroForm key={retro.week_start} retro={retro} />
      )}
    </div>
  );
}
