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
import { TrackTabs } from "@/components/track-tabs";
import { attempt, getMastery, getProgress, getToday } from "@/lib/api";
import { parseTrack, TRACK_SHORT, TRACKS, type Track } from "@/lib/tracks";
import { longDate } from "@/lib/utils";

/**
 * Server vs Client split on this page:
 *   - This page and <CommandCenter> are Server Components: they call the API on the server (token stays secret,
 *     no client waterfall), and their output is HTML streamed to the browser.
 *   - <Suspense> lets the static shell paint immediately; the skeleton shows until /api/today resolves.
 *   - Only the pieces that need interactivity are Client Components: MissionBoard (optimistic checkboxes),
 *     PendingBriefing (polling), SkillProgress (Recharts). They receive server data as serialisable props.
 */
type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default function Page({ searchParams }: { searchParams: SearchParams }) {
  return (
    <Suspense fallback={<CommandCenterSkeleton />}>
      <CommandCenter searchParams={searchParams} />
    </Suspense>
  );
}

async function CommandCenter({ searchParams }: { searchParams: SearchParams }) {
  const track: Track = parseTrack((await searchParams).track);
  // parallel, not sequential; the mastery summary is optional (the page still works if it fails)
  const [[data, err], [mastery]] = await Promise.all([attempt(Promise.all([getToday(track), getProgress(track)])), attempt(getMastery())]);
  if (!data) return <ApiProblem error={err} />;
  const [today, progress] = data;
  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_380px]">
      <div className="space-y-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">{longDate(today.date)}</p>
          <TrackTabs track={track} base="/" />
        </div>
        <LevelCard progress={progress} />
        {mastery && <MasteryCard m={mastery} />}
        {today.state === "ready" && today.briefing ? <MissionBoard briefing={today.briefing} />
          : today.state === "rest" ? <RestDay track={track} /> : <PendingBriefing track={track} />}
        {today.weekly_theme && <WeeklyThemeCard weekly={today.weekly_theme} today={today.date} />}
      </div>
      {/* One skill graph per track; the track you are looking at comes first. */}
      <aside className="space-y-6 lg:self-start">
        {[track, ...TRACKS.filter((t) => t !== track)].map((t) => (
          <SkillProgress key={t} skills={today.skills.filter((s) => s.track === t)} title={`${TRACK_SHORT[t]} skill matrix`}
            accent={t === "management" ? "#fbbf24" : "var(--primary)"} />
        ))}
      </aside>
    </div>
  );
}
