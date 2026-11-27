import { Suspense } from "react";
import { ApiProblem } from "@/components/api-problem";
import { MasteryCard } from "@/components/mastery/mastery-card";
import { MissionBoard } from "@/components/mission/mission-board";
import { PendingBriefing } from "@/components/mission/pending-briefing";
import { RestDay } from "@/components/mission/rest-day";
import { CommandCenterSkeleton } from "@/components/mission/skeletons";
import { WeeklyThemeCard } from "@/components/mission/weekly-theme-card";
import { LevelCard } from "@/components/progress/level-card";
import { SkillProgress } from "@/components/skills/skill-progress";
import { attempt, getMastery, getNext, getProgress } from "@/lib/api";
import { TRACK_LABEL, TRACK_SHORT, TRACKS, type Track } from "@/lib/tracks";
import type { NextLesson, Progress } from "@/lib/types";
import { cn, longDate } from "@/lib/utils";

/**
 * The Command Center: BOTH tracks, each at the lesson where you stopped. Lessons go in order: the oldest unfinished
 * day comes first, and today's lesson appears only once every earlier one is finished (/api/next).
 *   - This page and <CommandCenter> are Server Components: they call the API on the server (token stays secret,
 *     no client waterfall), and their output is HTML streamed to the browser.
 *   - Only the pieces that need interactivity are Client Components: MissionBoard (optimistic checkboxes),
 *     PendingBriefing (polling), SkillProgress (Recharts). They receive server data as serialisable props.
 */
export default function Page() {
  return (
    <Suspense fallback={<CommandCenterSkeleton />}>
      <CommandCenter />
    </Suspense>
  );
}

async function CommandCenter() {
  // five requests in parallel; the mastery summary is optional (the page still works if it fails)
  const [[data, err], [mastery]] = await Promise.all([
    attempt(Promise.all([getNext("ai"), getNext("management"), getProgress("ai"), getProgress("management")])), attempt(getMastery())]);
  if (!data) return <ApiProblem error={err} />;
  const [ai, mgmt, pAi, pMgmt] = data;
  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_380px]">
      <div className="space-y-12">
        <div className="space-y-6">
          <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">{longDate(ai.today)}</p>
          {mastery && <MasteryCard m={mastery} />}
        </div>
        <TrackLesson track="ai" next={ai} progress={pAi} />
        <TrackLesson track="management" next={mgmt} progress={pMgmt} />
        {ai.weekly_theme && <WeeklyThemeCard weekly={ai.weekly_theme} today={ai.today} />}
      </div>
      {/* One skill graph per track */}
      <aside className="space-y-6 lg:self-start">
        {TRACKS.map((t) => (
          <SkillProgress key={t} skills={ai.skills.filter((s) => s.track === t)} title={`${TRACK_SHORT[t]} skill matrix`}
            accent={t === "management" ? "#fbbf24" : "var(--primary)"} />
        ))}
      </aside>
    </div>
  );
}

function TrackLesson({ track, next, progress }: { track: Track; next: NextLesson; progress: Progress }) {
  const behind = next.unfinished - (next.mode === "today" ? 1 : 0);
  return (
    <section className="space-y-5" aria-label={TRACK_LABEL[track]}>
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-white/[0.07] pb-2">
        <h2 className={cn("text-lg font-semibold tracking-tight", track === "management" ? "text-amber-200" : "text-primary")}>{TRACK_LABEL[track]}</h2>
        <span className="font-mono text-[11px] text-muted-foreground">
          {next.mode === "continue" ? `continuing where you stopped${behind > 1 ? ` · ${behind} lessons to catch up` : ""}` : "up to date"}
        </span>
      </div>
      {/* the lesson first: it is what you came for; the level card (progress, exam) under it */}
      {next.state === "ready" && next.briefing
        ? <MissionBoard briefing={next.briefing} heading={next.mode === "continue" ? `Continue · ${longDate(next.date)}` : "Today's Lesson"} />
        : next.state === "rest" ? <RestDay track={track} /> : <PendingBriefing track={track} />}
      <LevelCard progress={progress} />
    </section>
  );
}
