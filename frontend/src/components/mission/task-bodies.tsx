import Link from "next/link";
import { ArrowRight, ArrowUpRight, BookOpenText, FlaskConical, ListChecks, ShieldCheck } from "lucide-react";
import { Markdown } from "@/components/markdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ArticleRef, HandsOnSpec, InsightMeta, ReadingMeta, Task } from "@/lib/types";

export const H = ({ children }: { children: React.ReactNode }) => (
  <h4 className="eyebrow mb-2 mt-5 first:mt-0">{children}</h4>
);

export function ExternalLink({ href, children }: { href: string; children: React.ReactNode }) {
  // Links come from our database, never from LLM text. rel hardens against window.opener / referrer leaks.
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-info underline-offset-4 hover:underline">
      {children}
      <ArrowUpRight className="size-3.5" />
    </a>
  );
}

export const Evidence = ({ items }: { items: ArticleRef[] }) => (
  <ul className="space-y-1 text-sm">
    {items.map((a) => (
      <li key={a.article_uuid} className="flex gap-2">
        <span className="text-muted-foreground">{a.source}</span>
        <ExternalLink href={a.url}>{a.title}</ExternalLink>
      </li>
    ))}
  </ul>
);

export function ReadingSummary({ r, task }: { r: ReadingMeta; task: Task }) {
  return (
    <div className="text-sm leading-relaxed">
      <p className="mb-3 text-muted-foreground">
        A short, self-contained reading of about {r.word_count.toLocaleString("en-US")} words ({r.estimated_minutes} min)
        {r.quiz_questions ? <>, then a {r.quiz_questions}-question quiz you must pass to unlock the exercise.</> : ": no other reading needed."}
      </p>
      <H>After this chapter you can</H>
      <ul className="list-disc space-y-1 pl-5 marker:text-muted-foreground">{r.learning_goals.map((g) => <li key={g}>{g}</li>)}</ul>
      <H>In this chapter</H>
      <ol className="flex flex-wrap gap-1.5">
        {r.section_headings.map((h, i) => (
          <li key={h} className="rounded-full border bg-white/[0.03] px-2.5 py-0.5 text-xs text-muted-foreground"><span className="mr-1 font-mono opacity-60">{i + 1}</span>{h}</li>
        ))}
      </ol>
      <Button asChild className="mt-5"><Link href={`/read/${task.id}`}><BookOpenText /> {task.completed ? "Re-read" : "Open the reading"} <ArrowRight /></Link></Button>
    </div>
  );
}

export function HandsOnSummary({ h, task }: { h: HandsOnSpec; task: Task }) {
  const status = task.completed ? "Passed" : task.attempt_count > 0 ? `Attempt ${task.attempt_count}: ${task.evaluation_score}/10, not passed yet` : "Not started";
  return (
    <div className="text-sm leading-relaxed">
      <p>{h.objective}</p>
      <H>Deliverable</H>
      <p className="text-muted-foreground">{h.deliverable}</p>
      <H>Graded against</H>
      <ul className="space-y-1">
        {h.rubric.criteria.map((c) => (
          <li key={c.name} className="flex items-center justify-between gap-3">
            <span>{c.name}{c.must_pass && <Badge variant="warn" className="ml-2">must pass</Badge>}</span>
            <span className="font-mono text-xs text-muted-foreground">{c.max_points} pts</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-muted-foreground">Pass mark {h.rubric.pass_score}/10 · {h.steps.length} steps · status: {status}</p>
      <Button asChild className="mt-5"><Link href={`/practice/${task.id}`}><FlaskConical /> {task.completed ? "Review your work" : "Open the workspace"} <ArrowRight /></Link></Button>
    </div>
  );
}

export function InsightBody({ m }: { m: InsightMeta }) {
  return (
    <div>
      <p className="mb-4 rounded-lg border border-warn/20 bg-warn/5 px-3.5 py-2.5 text-sm text-warn/90"><span className="eyebrow mr-2 !text-warn/80">Thesis</span>{m.thesis}</p>
      <Markdown compact>{m.body}</Markdown>
      <H>Evidence</H>
      <Evidence items={m.evidence} />
    </div>
  );
}

export const GradedBadge = () => (
  <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><ShieldCheck className="size-3.5" /> graded by the professor</span>
);
export { ListChecks };
