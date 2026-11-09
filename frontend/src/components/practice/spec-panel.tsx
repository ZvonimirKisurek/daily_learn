import { FileCode2, Flag, ListOrdered, PenLine, Scale, Terminal } from "lucide-react";
import { CodeBlock } from "@/components/code-block";
import { Badge } from "@/components/ui/badge";
import { isWrittenExercise } from "@/lib/skills";
import type { HandsOnSpec } from "@/lib/types";

const Cmd = ({ lines }: { lines: string[] }) => {
  const text = lines.join("\n");
  return <CodeBlock lang="bash" text={text}><pre><code>{text}</code></pre></CodeBlock>;
};

const Block = ({ icon: Icon, title, children }: { icon: typeof Flag; title: string; children: React.ReactNode }) => (
  <section className="glass rounded-2xl p-5">
    <h3 className="eyebrow mb-3 flex items-center gap-2"><Icon className="size-3.5" /> {title}</h3>
    {children}
  </section>
);

/** The assignment, step by step. A Server Component: static content, no JS beyond the copy buttons. */
export function SpecPanel({ spec }: { spec: HandsOnSpec }) {
  return (
    <div className="space-y-5">
      <Block icon={Flag} title="Objective">
        <p className="leading-relaxed">{spec.objective}</p>
        {spec.prerequisites.length > 0 && <ul className="mt-3 flex flex-wrap gap-1.5">{spec.prerequisites.map((p) => <li key={p}><Badge variant="outline">{p}</Badge></li>)}</ul>}
      </Block>

      {isWrittenExercise(spec) ? (
        <Block icon={PenLine} title="How to do this exercise">
          <p className="leading-relaxed">No code and no files needed. Write your answers <strong>directly in the &quot;your submission&quot; box</strong> (on the right, or below on a phone), following the steps, then press <strong>Submit for evaluation</strong>.</p>
          <p className="mt-2 text-sm text-muted-foreground">Where a step mentions {spec.project_layout.map((f) => <code key={f} className="mx-1 rounded bg-white/10 px-1.5 py-0.5 font-mono text-xs">{f}</code>)}, that just means &quot;your answers&quot;: type them in the box.</p>
        </Block>
      ) : (
        <Block icon={FileCode2} title="Files to create on your computer">
          <ul className="space-y-1 font-mono text-sm">{spec.project_layout.map((f) => <li key={f} className="flex items-center gap-2 text-emerald-200/90"><FileCode2 className="size-3.5 text-muted-foreground" />{f}</li>)}</ul>
          <p className="mt-2 text-xs text-muted-foreground">Create these in a folder on your computer, follow the steps, then paste the code and its output into the submission box.</p>
        </Block>
      )}

      <Block icon={ListOrdered} title={`${spec.steps.length} steps`}>
        <ol className="space-y-6">
          {spec.steps.map((s, i) => (
            <li key={s.title} className="relative pl-11">
              <span className="absolute left-0 top-0 grid size-8 place-items-center rounded-full border border-primary/40 bg-primary/10 font-mono text-sm text-primary">{i + 1}</span>
              <h4 className="font-semibold leading-snug">{s.title}</h4>
              <p className="mt-1.5 text-[15px] leading-relaxed text-foreground/85">{s.instructions}</p>
              {s.commands.length > 0 && <div className="mt-3"><div className="eyebrow mb-1.5 flex items-center gap-1.5"><Terminal className="size-3" /> Run</div><Cmd lines={s.commands} /></div>}
              {s.expected_output && <div className="mt-3 rounded-lg border border-primary/20 bg-primary/[0.06] px-3.5 py-2.5 text-sm"><span className="eyebrow mr-2 !text-primary/80">You should see</span>{s.expected_output}</div>}
            </li>
          ))}
        </ol>
      </Block>

      <Block icon={Flag} title="Deliverable and what to paste">
        <p className="leading-relaxed">{spec.deliverable}</p>
        <p className="mt-2 text-[15px] leading-relaxed text-muted-foreground">{spec.submission_instructions}</p>
        {spec.docs_to_consult.length > 0 && <p className="mt-3 text-sm text-muted-foreground">Check the official docs for: {spec.docs_to_consult.map((d) => <code key={d} className="mx-1 rounded bg-white/10 px-1.5 py-0.5 font-mono text-xs">{d}</code>)}</p>}
        {spec.stretch_goal && <p className="mt-3 text-sm"><span className="eyebrow mr-2">Stretch</span><span className="text-muted-foreground">{spec.stretch_goal}</span></p>}
      </Block>

      <Block icon={Scale} title={`How you will be graded (pass mark ${spec.rubric.pass_score}/10)`}>
        <ul className="space-y-3">
          {spec.rubric.criteria.map((c) => (
            <li key={c.name}>
              <div className="flex items-center justify-between gap-3"><span className="font-medium">{c.name}{c.must_pass && <Badge variant="warn" className="ml-2">must pass</Badge>}</span><span className="font-mono text-xs text-muted-foreground">{c.max_points} pts</span></div>
              <p className="mt-0.5 text-sm text-muted-foreground">{c.description}</p>
            </li>
          ))}
        </ul>
        <p className="mt-4 text-xs text-muted-foreground">A must-pass criterion needs at least half its points or the task fails regardless of the total. The grader reads your code and output; it does not execute anything, so show your terminal output.</p>
      </Block>
    </div>
  );
}
