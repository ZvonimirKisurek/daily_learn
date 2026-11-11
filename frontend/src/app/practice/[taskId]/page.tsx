import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { ArrowLeft, FlaskConical } from "lucide-react";
import { ApiProblem } from "@/components/api-problem";
import { Reveal } from "@/components/motion";
import { PracticeSkeleton } from "@/components/practice/skeletons";
import { SpecPanel } from "@/components/practice/spec-panel";
import { Workspace } from "@/components/practice/workspace";
import { Badge } from "@/components/ui/badge";
import { ApiError, attempt, getTask } from "@/lib/api";
import { withTrack } from "@/lib/tracks";
import { skillLabel } from "@/lib/skills";

export const metadata: Metadata = { title: "Hands-on lab" };
// Grading is an LLM call (10-60 s) made by a Server Action on this route; Vercel stops a function at its maxDuration.
export const maxDuration = 300;

export default function PracticePage({ params }: { params: Promise<{ taskId: string }> }) {
  return <Suspense fallback={<PracticeSkeleton />}><Content params={params} /></Suspense>;
}

async function Content({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = await params;
  const [detail, err] = await attempt(getTask(taskId));
  if (err instanceof ApiError && (err.status === 404 || err.status === 422)) notFound();
  if (!detail) return <ApiProblem error={err} />;
  const spec = detail.hands_on;
  if (!spec) notFound();
  return (
    <div>
      <Link href={withTrack(detail.archived ? `/day/${detail.day}` : "/", detail.track)} className="mb-6 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"><ArrowLeft className="size-4" /> {detail.archived ? "Back to that day" : "Today\u2019s mission"}</Link>
      <Reveal>
        <header className="mb-8 max-w-3xl">
          <div className="eyebrow mb-3 flex items-center gap-2 !text-primary"><FlaskConical className="size-3.5" /> Hands-on lab · {skillLabel(spec.skill_slug)}</div>
          <h1 className="text-4xl font-semibold leading-[1.1] tracking-tight sm:text-5xl"><span className="gradient-text">{spec.title}</span></h1>
          <div className="mt-4 flex flex-wrap gap-2"><Badge variant="outline">{spec.estimated_minutes} min</Badge><Badge variant="outline">graded by AI review</Badge>{detail.lesson && <Badge variant="violet">Level {detail.lesson.level} · {detail.lesson.title}</Badge>}{detail.task.completed && <Badge variant="primary">passed</Badge>}</div>
        </header>
      </Reveal>
      {detail.locked && !detail.task.completed && (
        <p role="status" className="mb-6 rounded-xl border border-warn/30 bg-warn/5 px-4 py-3 text-sm text-warn">
          Locked: {detail.locked} You can read the exercise now; submitting unlocks after the quiz.
        </p>
      )}
      <div className="grid gap-8 lg:grid-cols-2">
        <SpecPanel spec={spec} />
        <div className="lg:sticky lg:top-20 lg:self-start"><Workspace detail={detail} /></div>
      </div>
    </div>
  );
}
