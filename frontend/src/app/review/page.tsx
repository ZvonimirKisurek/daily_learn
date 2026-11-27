import type { Metadata } from "next";
import { Suspense } from "react";
import { BrainCircuit } from "lucide-react";
import { ApiProblem } from "@/components/api-problem";
import { ReviewSession } from "@/components/mastery/review-session";
import { Skeleton } from "@/components/ui/skeleton";
import { attempt, getMastery, getReviews } from "@/lib/api";

export const metadata: Metadata = { title: "Review" };
export const maxDuration = 120; // the optional AI check is an LLM call made by a Server Action on this route

export default function ReviewPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <header className="mb-8">
        <div className="eyebrow mb-3 flex items-center gap-2 !text-violet"><BrainCircuit className="size-3.5" /> Spaced review</div>
        <h1 className="text-4xl font-semibold tracking-tight"><span className="gradient-text">Recall, then check</span></h1>
        <p className="mt-3 max-w-2xl text-muted-foreground">
          Questions from your chapters, primary sources and weekly teach-back, scheduled at growing intervals. Retrieving an answer from
          memory is what builds durable knowledge; rereading only feels like it does. Overdue cards lock new work until they are answered.
        </p>
      </header>
      <Suspense fallback={<Skeleton className="h-96 rounded-2xl" />}><Session /></Suspense>
    </div>
  );
}

async function Session() {
  const [[cards, err], [mastery]] = await Promise.all([attempt(getReviews(50)), attempt(getMastery())]);
  if (!cards) return <ApiProblem error={err} />;
  return (
    <>
      {mastery && (
        <p className="mb-4 text-sm text-muted-foreground">
          {mastery.reviews.active} active cards · {mastery.reviews.next_7_days} due in the next 7 days · 30-day retention{" "}
          {mastery.reviews.retention_30d === null ? "–" : `${Math.round(mastery.reviews.retention_30d * 100)}%`}
        </p>
      )}
      <ReviewSession initial={cards} />
    </>
  );
}
