import { Suspense } from "react";
import { ApiProblem } from "@/components/api-problem";
import { MissionBoard } from "@/components/mission/mission-board";
import { PendingBriefing } from "@/components/mission/pending-briefing";
import { CommandCenterSkeleton } from "@/components/mission/skeletons";
import { WeeklyThemeCard } from "@/components/mission/weekly-theme-card";
import { LevelCard } from "@/components/progress/level-card";
import { SkillProgress } from "@/components/skills/skill-progress";
import { attempt, getProgress, getToday } from "@/lib/api";
import { longDate } from "@/lib/utils";

/**
 * Server vs Client split on this page:
 *   - This page and <CommandCenter> are Server Components: they call the API on the server (token stays secret,
 *     no client waterfall), and their output is HTML streamed to the browser.
 *   - <Suspense> lets the static shell paint immediately; the skeleton shows until /api/today resolves.
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
  const [data, err] = await attempt(Promise.all([getToday(), getProgress()])); // parallel, not sequential
  if (!data) return <ApiProblem error={err} />;
  const [today, progress] = data;
  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_380px]">
      <div className="space-y-8">
        <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">{longDate(today.date)}</p>
        <LevelCard progress={progress} />
        {today.state === "ready" && today.briefing ? <MissionBoard briefing={today.briefing} /> : <PendingBriefing />}
        {today.weekly_theme && <WeeklyThemeCard weekly={today.weekly_theme} today={today.date} />}
      </div>
      <aside className="lg:sticky lg:top-20 lg:self-start">
        <SkillProgress skills={today.skills} />
      </aside>
    </div>
  );
}
