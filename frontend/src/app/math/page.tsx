import type { Metadata } from "next";
import { Suspense } from "react";
import { Sigma } from "lucide-react";
import { ApiProblem } from "@/components/api-problem";
import { MathDrillView } from "@/components/mastery/math-drill";
import { Skeleton } from "@/components/ui/skeleton";
import { attempt, getMath } from "@/lib/api";

export const metadata: Metadata = { title: "Math drill" };

export default function MathPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <header className="mb-8">
        <div className="eyebrow mb-3 flex items-center gap-2 !text-info"><Sigma className="size-3.5" /> Daily math drill · ~15 minutes</div>
        <h1 className="text-4xl font-semibold tracking-tight"><span className="gradient-text">The math under the models</span></h1>
        <p className="mt-3 max-w-2xl text-muted-foreground">
          Five small problems on this week&apos;s topics: linear algebra, calculus, probability and the ML formulas built on them. Work them on paper.
          They are generated and graded by code, so every answer key is exact. Topics you miss come back in the next days&apos; drills.
        </p>
      </header>
      <Suspense fallback={<div className="space-y-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-44 rounded-2xl" />)}</div>}><Drill /></Suspense>
    </div>
  );
}

async function Drill() {
  const [drill, err] = await attempt(getMath());
  if (!drill) return <ApiProblem error={err} />;
  return (
    <>
      <p className="mb-4 text-sm text-muted-foreground">{drill.attempted}/{drill.total} attempted · {drill.correct} correct on the first try</p>
      <MathDrillView drill={drill} />
    </>
  );
}
