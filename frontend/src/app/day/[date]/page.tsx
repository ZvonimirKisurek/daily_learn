import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { ArrowLeft, CalendarDays, History } from "lucide-react";
import { ApiProblem } from "@/components/api-problem";
import { MissionBoard } from "@/components/mission/mission-board";
import { PendingBriefing } from "@/components/mission/pending-briefing";
import { CommandCenterSkeleton } from "@/components/mission/skeletons";
import { ApiError, attempt, getDay } from "@/lib/api";
import { longDate } from "@/lib/utils";

export const metadata: Metadata = { title: "Past briefing" };

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Dynamic segment: the folder name `[date]` makes this one file serve /day/2026-10-28, /day/2026-10-29, ...
 * Next passes the matched text in `params` - a Promise in this Next version, hence `await params`. The value is
 * untrusted user input (it is whatever someone typed in the URL), so it is validated before it reaches the API.
 */
export default function DayPage({ params }: { params: Promise<{ date: string }> }) {
  return <Suspense fallback={<CommandCenterSkeleton />}><Content params={params} /></Suspense>;
}

async function Content({ params }: { params: Promise<{ date: string }> }) {
  const { date } = await params;
  if (!ISO_DAY.test(date)) notFound();
  const [day, err] = await attempt(getDay(date));
  if (err instanceof ApiError && (err.status === 404 || err.status === 422)) notFound(); // no briefing stored (or impossible date like 2026-02-31)
  if (!day) return <ApiProblem error={err} />;

  const past = date < day.date; // ISO dates compare correctly as strings; day.date is "today" in the learner's timezone
  // Learning-path lessons stay doable on later days (catch-up, still counts toward the level exam); legacy ones are read-only.
  const archived = past && day.briefing?.level == null;
  const catchUp = past && !archived;
  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link href="/" className="inline-flex items-center gap-1.5 rounded-lg bg-primary/15 px-3 py-1.5 text-sm font-medium text-primary ring-1 ring-primary/25 transition-colors hover:bg-primary/25">
          <ArrowLeft className="size-4" /> Back to Today
        </Link>
        <Link href="/calendar" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"><CalendarDays className="size-4" /> Calendar</Link>
      </div>
      <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">{longDate(date)}</p>
      {catchUp && (
        <p className="flex items-center gap-2 rounded-xl border border-violet/25 bg-violet/5 px-4 py-3 text-sm text-violet">
          <History className="size-4 shrink-0" /> Catch-up lesson: you can still finish it, and it counts toward your level exam.
        </p>
      )}
      {archived && (
        <p className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-sm text-muted-foreground">
          <History className="size-4 shrink-0" /> Archived day. You can still read the chapter, but this task is from the past and can no longer be submitted.
        </p>
      )}
      {day.state === "ready" && day.briefing
        ? <MissionBoard briefing={day.briefing} heading={archived ? "Mission archive" : catchUp ? "Catch-up lesson" : "Today's Lesson"} archived={archived} />
        : <PendingBriefing />}
    </div>
  );
}
