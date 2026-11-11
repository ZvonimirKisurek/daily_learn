import type { Metadata } from "next";
import { Suspense } from "react";
import { ApiProblem } from "@/components/api-problem";
import { LearningCalendar } from "@/components/calendar/learning-calendar";
import { Skeleton } from "@/components/ui/skeleton";
import { TrackTabs } from "@/components/track-tabs";
import { attempt, getDoneDays, getHistory, getToday } from "@/lib/api";
import { parseTrack, type Track } from "@/lib/tracks";

export const metadata: Metadata = { title: "Calendar" };

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default function CalendarPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <div>
      <div className="mb-8">
        <p className="eyebrow mb-2">History</p>
        <h1 className="text-4xl font-semibold tracking-tight"><span className="gradient-text">Learning calendar</span></h1>
        <p className="mt-2 text-sm text-muted-foreground">Green = lesson finished, yellow = not finished yet. Click a yellow day to catch up on it.</p>
      </div>
      <Suspense fallback={<Skeleton className="mx-auto h-[26rem] w-full max-w-md rounded-2xl" />}>
        <Content searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Content({ searchParams }: { searchParams: SearchParams }) {
  const track: Track = parseTrack((await searchParams).track);
  // In parallel: the list of generated days, and "today" in the learner's configured timezone (the browser's clock is not authoritative).
  const [[days, e1], [today, e2], [done, e3]] = await Promise.all([attempt(getHistory(track)), attempt(getToday(track)), attempt(getDoneDays(track))]);
  if (!days || !today || !done) return <ApiProblem error={e1 ?? e2 ?? e3} />;
  return (
    <div className="space-y-6">
      <div className="flex justify-center"><TrackTabs track={track} base="/calendar" /></div>
      <LearningCalendar key={track} days={days} done={done} today={today.date} track={track} />
    </div>
  );
}
