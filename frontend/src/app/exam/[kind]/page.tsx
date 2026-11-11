import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { ArrowLeft, Lock } from "lucide-react";
import { ApiProblem } from "@/components/api-problem";
import { ExamRunner } from "@/components/exam/exam-runner";
import { Skeleton } from "@/components/ui/skeleton";
import { attempt, getProgress } from "@/lib/api";
import { parseTrack, withTrack, type Track } from "@/lib/tracks";

export const metadata: Metadata = { title: "Exam" };

type Props = { params: Promise<{ kind: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> };

export default function ExamPage(props: Props) {
  return (
    <div className="mx-auto max-w-2xl">
      <Suspense fallback={<Skeleton className="h-64 rounded-2xl" />}><Content {...props} /></Suspense>
    </div>
  );
}

async function Content({ params, searchParams }: Props) {
  const { kind } = await params;
  if (kind !== "placement" && kind !== "level") notFound();
  const track: Track = parseTrack((await searchParams).track);
  const [p, err] = await attempt(getProgress(track));
  if (!p) return <ApiProblem error={err} />;
  const back = <Link href={withTrack("/path", track)} className="mb-6 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"><ArrowLeft className="size-4" /> {p.track_label} path</Link>;

  if (kind === "placement") {
    return (
      <>
        {back}
        <h1 className="mb-6 text-3xl font-semibold tracking-tight"><span className="gradient-text">Placement exam</span></h1>
        {!p.placement.available_today ? <Blocked track={track} text="You already took the placement exam today. You can retake it tomorrow." /> : (
          <ExamRunner kind="placement" track={track} intro={
            <div className="space-y-2 text-sm leading-relaxed">
              <p className="text-base font-medium">{track === "management" ? "How much management and business do you already know?" : "How much AI and machine learning do you already know?"}</p>
              <p className="text-muted-foreground">18 questions, {track === "management" ? "from team lead to C-level topics" : "from \"What is AI?\" to expert topics"}, 3 per tier. Answer honestly; guessing only places you on lessons that are too hard.</p>
              <p className="text-muted-foreground">Get at least 2 of 3 right in a tier and that tier is skipped. You start at the first tier you do not know yet. It can only move you up (you are on Level {p.level.index} now).</p>
            </div>
          } />
        )}
      </>
    );
  }

  const need = p.level.lessons_required - p.lessons_done;
  return (
    <>
      {back}
      <h1 className="mb-6 text-3xl font-semibold tracking-tight"><span className="gradient-text">Level {p.level.index} exam: {p.level.title}</span></h1>
      {p.graduated ? <Blocked track={track} text="You have completed the whole path." />
        : !p.exam.unlocked ? <Blocked track={track} text={`Finish ${need} more lesson${need === 1 ? "" : "s"} on this level to unlock its exam.`} />
        : p.exam.attempts_today >= p.exam.attempts_max ? <Blocked track={track} text="No exam attempts left today. Review today's lesson and try again tomorrow." />
        : (
          <ExamRunner kind="level" track={track} intro={
            <div className="space-y-2 text-sm leading-relaxed">
              <p className="text-base font-medium">{p.exam.questions} questions covering: {p.level.topics.join(" · ")}</p>
              <p className="text-muted-foreground">Pass with {p.exam.pass_percent}% to unlock Level {Math.min(p.level.index + 1, p.levels_total)}. Every answer is explained afterwards. {p.exam.attempts_max - p.exam.attempts_today} attempt(s) left today; each retry is a brand-new exam.</p>
            </div>
          } />
        )}
    </>
  );
}

const Blocked = ({ text, track }: { text: string; track: Track }) => (
  <div className="glass flex items-center gap-3 rounded-2xl p-6 text-sm text-muted-foreground"><Lock className="size-4 shrink-0" />{text}
    <Link href={withTrack("/", track)} className="ml-auto shrink-0 text-info underline-offset-4 hover:underline">Today&apos;s lesson</Link>
  </div>
);
