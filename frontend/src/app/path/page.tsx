import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { Award, Check, Flame, Gauge, GraduationCap, Lock, Zap } from "lucide-react";
import { ApiProblem } from "@/components/api-problem";
import { Stat } from "@/components/progress/level-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { attempt, getProgress } from "@/lib/api";
import { skillLabel } from "@/lib/skills";
import type { PathLevel, Progress } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Learning Path" };

export default function PathPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight"><span className="gradient-text">Learning Path</span></h1>
        <p className="mt-1 text-sm text-muted-foreground">From &quot;What is AI?&quot; to expert in 21 levels. Each level: daily lessons (reading + quiz, exercise, essay), then an exam you must pass to move on.</p>
      </div>
      <Suspense fallback={<div className="space-y-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-28 rounded-2xl" />)}</div>}>
        <Content />
      </Suspense>
    </div>
  );
}

async function Content() {
  const [p, err] = await attempt(getProgress());
  if (!p) return <ApiProblem error={err} />;
  const tiers = [...new Set(p.path.map((l) => l.tier))];
  return (
    <>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat icon={Gauge} value={`${p.knowledge_score}/100`} label="AI knowledge score" tone="text-info" />
        <Stat icon={Zap} value={`${p.xp_total} XP`} label={`+${p.xp_today} today`} tone="text-warn" />
        <Stat icon={Flame} value={`${p.streak_days}`} label="day streak" tone="text-orange-400" />
        <Stat icon={Award} value={`${p.path.filter((l) => l.status === "completed").length}/${p.levels_total}`} label="levels passed" tone="text-primary" />
      </div>
      {!p.placement.taken && (
        <div className="glass flex flex-wrap items-center gap-3 rounded-2xl p-4 text-sm">
          <GraduationCap className="size-4 text-violet" />
          <span>Not sure where you stand? An 18-question placement exam measures what you already know and skips those levels.</span>
          {p.placement.available_today && <Button asChild size="sm" variant="outline" className="ml-auto"><Link href="/exam/placement">Take the placement exam</Link></Button>}
        </div>
      )}
      {tiers.map((tier) => {
        const levels = p.path.filter((l) => l.tier === tier);
        const done = levels.filter((l) => l.status === "completed").length;
        return (
          <section key={tier} aria-labelledby={`tier-${tier}`}>
            <div className="mb-3 flex items-baseline justify-between">
              <h2 id={`tier-${tier}`} className="text-lg font-semibold">Tier {tier} · {levels[0].tier_name}</h2>
              <span className="font-mono text-xs text-muted-foreground">{done}/{levels.length}</span>
            </div>
            <ol className="relative space-y-3 border-l border-white/10 pl-6">
              {levels.map((l) => <LevelRow key={l.index} l={l} p={p} />)}
            </ol>
          </section>
        );
      })}
    </>
  );
}

function LevelRow({ l, p }: { l: PathLevel; p: Progress }) {
  const current = l.status === "current";
  return (
    <li className={cn("relative rounded-2xl border p-4", current ? "glass border-violet/40 ring-1 ring-violet/30" : "border-white/[0.07] bg-white/[0.02]", l.status === "locked" && "opacity-60")}>
      <span aria-hidden className={cn("absolute -left-[37px] top-4 grid size-6 place-items-center rounded-full border font-mono text-[11px]",
        l.status === "completed" ? "border-primary bg-primary text-primary-foreground" : current ? "border-violet bg-violet/20 text-violet" : "border-white/20 bg-background text-muted-foreground")}>
        {l.status === "completed" ? <Check className="size-3.5" strokeWidth={3} /> : l.status === "locked" ? <Lock className="size-3" /> : l.index}
      </span>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground">Level {l.index}</span>
        <h3 className="font-semibold">{l.title}</h3>
        <Badge variant="outline">{skillLabel(l.skill)}</Badge>
        {l.status === "completed" && <Badge variant="primary">{l.via === "exam" ? `exam ${l.score}/${l.total}` : "known (placement)"}</Badge>}
        {current && <Badge variant="violet">you are here</Badge>}
      </div>
      <p className="mt-1 text-sm text-muted-foreground">{l.summary}</p>
      {current && (
        <>
          <ol className="mt-3 grid gap-1.5 text-sm sm:grid-cols-2">
            {l.topics.map((t, i) => (
              <li key={t} className="flex items-start gap-2">
                <span className={cn("mt-1 size-2 shrink-0 rounded-full", i < p.lessons_done ? "bg-primary" : "bg-white/20")} />{t}
              </li>
            ))}
          </ol>
          <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
            {p.exam.unlocked && p.exam.attempts_today < p.exam.attempts_max
              ? <Button asChild size="sm"><Link href="/exam/level"><GraduationCap /> Take the level exam</Link></Button>
              : <span className="text-muted-foreground">{p.exam.unlocked ? "No exam attempts left today." : `${p.lessons_done}/${l.topics.length} lessons done · the exam unlocks after ${l.topics.length}.`}</span>}
            <Link href="/" className="text-info underline-offset-4 hover:underline">Today&apos;s lesson</Link>
          </div>
        </>
      )}
    </li>
  );
}
