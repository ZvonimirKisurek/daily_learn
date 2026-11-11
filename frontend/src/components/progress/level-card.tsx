import Link from "next/link";
import { ArrowRight, Award, Flame, Gauge, GraduationCap, Lock, Map, Sparkles, Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { withTrack } from "@/lib/tracks";
import type { Progress } from "@/lib/types";
import { cn } from "@/lib/utils";

export const Stat = ({ icon: Icon, value, label, tone }: { icon: typeof Zap; value: string; label: string; tone: string }) => (
  <div className="flex items-center gap-2.5 rounded-xl border border-white/[0.07] bg-white/[0.03] px-3 py-2">
    <Icon className={cn("size-4 shrink-0", tone)} />
    <div className="leading-tight">
      <div className="font-mono text-sm font-semibold">{value}</div>
      <div className="text-[11px] text-muted-foreground">{label}</div>
    </div>
  </div>
);

/** Duolingo-style status: where you are on the path, XP, streak, and what unlocks the next level. */
export function LevelCard({ progress: p }: { progress: Progress }) {
  const need = Math.max(0, p.level.lessons_required - p.lessons_done);
  const attemptsLeft = p.exam.attempts_max - p.exam.attempts_today;
  return (
    <section aria-labelledby="level-h" className="glass relative overflow-hidden rounded-2xl p-5">
      <div aria-hidden className="pointer-events-none absolute -right-20 -top-20 size-56 rounded-full bg-gradient-to-br from-violet-400/20 to-transparent blur-2xl" />
      <div className="relative">
        <div className="eyebrow mb-1.5 flex items-center gap-1.5 !text-violet"><GraduationCap className="size-3.5" /> {p.track_label || "Learning path"} · {p.level.tier_name}</div>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="level-h" className="text-2xl font-semibold tracking-tight">Level {p.level.index} <span className="text-muted-foreground">·</span> {p.level.title}</h2>
          <span className="font-mono text-xs text-muted-foreground">{p.level.index} / {p.levels_total}</span>
        </div>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{p.level.summary}</p>

        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat icon={Zap} value={`${p.xp_total} XP`} label={`+${p.xp_today} today`} tone="text-warn" />
          <Stat icon={Flame} value={`${p.streak_days} day${p.streak_days === 1 ? "" : "s"}`} label="streak" tone="text-orange-400" />
          <Stat icon={Gauge} value={`${p.knowledge_score}/100`} label={p.track === "management" ? "path proven" : "AI knowledge"} tone="text-info" />
          <Stat icon={Award} value={`${p.path.filter((l) => l.status === "completed").length}`} label="levels passed" tone="text-primary" />
        </div>

        <div className="mt-5">
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="font-medium">Lessons on this level</span>
            <span className="font-mono text-muted-foreground">{Math.min(p.lessons_done, p.level.lessons_required)} / {p.level.lessons_required}</span>
          </div>
          <ol className="flex gap-1.5" aria-label="Lessons finished on this level">
            {p.level.topics.map((t, i) => (
              <li key={t} title={t} className={cn("h-2 flex-1 rounded-full", i < p.lessons_done ? "bg-gradient-to-r from-emerald-300 to-sky-300" : "bg-white/10")} />
            ))}
            <li title="Level exam" className={cn("grid size-5 -translate-y-1.5 place-items-center rounded-full border", p.exam.unlocked ? "border-warn bg-warn/20 text-warn" : "border-white/15 text-muted-foreground")}>
              {p.exam.unlocked ? <Sparkles className="size-3" /> : <Lock className="size-2.5" />}
            </li>
          </ol>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          {p.graduated ? (
            <Badge variant="primary">You completed the whole path. Expert level reached.</Badge>
          ) : p.exam.unlocked && attemptsLeft <= 0 ? (
            <p className="flex items-center gap-1.5 text-sm text-muted-foreground"><Lock className="size-3.5" /> No exam attempts left today. Review today&apos;s lesson; the exam reopens tomorrow.</p>
          ) : p.exam.unlocked ? (
            <>
              <Button asChild><Link href={withTrack("/exam/level", p.track)}><GraduationCap /> Take the Level {p.level.index} exam <ArrowRight /></Link></Button>
              <span className="text-xs text-muted-foreground">
                {p.exam.questions} questions · pass with {p.exam.pass_percent}% · {Math.max(0, attemptsLeft)} attempt{attemptsLeft === 1 ? "" : "s"} left today
                {p.exam.last && !p.exam.last.passed && <> · last try {p.exam.last.correct}/{p.exam.last.total}</>}
              </span>
            </>
          ) : (
            <p className="flex items-center gap-1.5 text-sm text-muted-foreground"><Lock className="size-3.5" /> Finish {need} more lesson{need === 1 ? "" : "s"} to unlock the level exam. Each lesson = quiz passed + exercise passed + essay read.</p>
          )}
          <Link href={withTrack("/path", p.track)} className="ml-auto inline-flex items-center gap-1 text-sm text-info underline-offset-4 hover:underline"><Map className="size-3.5" /> Whole path</Link>
        </div>
        {!p.placement.taken && p.placement.available_today && (
          <p className="mt-3 text-xs text-muted-foreground">
            {p.track === "management" ? "Already managing people?" : "Already know some AI?"} <Link href={withTrack("/exam/placement", p.track)} className="text-info underline-offset-4 hover:underline">Take the placement exam</Link> to skip the levels you know.
          </p>
        )}
      </div>
    </section>
  );
}
