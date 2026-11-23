import { FlaskConical, KeyRound, ShieldCheck, ShieldQuestion, Terminal } from "lucide-react";
import { CodeBlock } from "@/components/code-block";
import { Badge } from "@/components/ui/badge";
import type { AcceptanceTests } from "@/lib/types";

const Code = ({ lang, text }: { lang: string; text: string }) => <CodeBlock lang={lang} text={text}><pre><code>{text}</code></pre></CodeBlock>;

/**
 * The executable definition of "done" (Server Component). The suite was run against a hidden reference solution when
 * the task was generated ("verified"), so a failing test means your code, not the test, needs work.
 */
export function AcceptancePanel({ suite }: { suite: AcceptanceTests }) {
  return (
    <section className="glass rounded-2xl p-5">
      <h3 className="eyebrow mb-3 flex items-center gap-2"><FlaskConical className="size-3.5" /> Acceptance tests ({suite.test_names.length}) · they decide the pass</h3>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        {suite.status === "verified"
          ? <Badge variant="primary"><ShieldCheck className="size-3" /> verified against a reference solution</Badge>
          : <Badge variant="warn"><ShieldQuestion className="size-3" /> unverified</Badge>}
        <span className="text-xs text-muted-foreground">{suite.detail}</span>
      </div>
      <div className="mb-4">
        <div className="eyebrow mb-1.5">The interface your code must provide</div>
        <ul className="space-y-1 font-mono text-[13px]">{suite.interface.map((i) => <li key={i} className="text-emerald-200/90"><span className="mr-2 text-muted-foreground">▸</span>{i}</li>)}</ul>
      </div>
      <ol className="mb-4 list-decimal space-y-1.5 pl-5 text-[15px] leading-relaxed">
        <li>Save the file below as <code className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-xs">{suite.path}</code> (do not edit it).</li>
        <li>Run it and paste the whole output into &quot;pytest output&quot; when you submit:</li>
      </ol>
      <div className="mb-3"><div className="eyebrow mb-1.5 flex items-center gap-1.5"><Terminal className="size-3" /> Run</div><Code lang="bash" text={suite.run_command} /></div>
      <p className="mb-3 text-sm text-muted-foreground">
        Or let the helper fetch, run and submit for you: <code className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-xs">python tools/mle_check.py &lt;task-id&gt; --dir &lt;project&gt; --submit</code>
      </p>
      <details className="group">
        <summary className="cursor-pointer text-sm text-muted-foreground">Show {suite.path}</summary>
        <div className="mt-2"><Code lang="python" text={suite.code} /></div>
      </details>
    </section>
  );
}

/** Shown only after a pass or when every attempt is used: a reference to compare against, never a shortcut. */
export function ReferenceSolution({ files }: { files: { path: string; content: string }[] }) {
  return (
    <section className="glass rounded-2xl p-5">
      <h3 className="eyebrow mb-2 flex items-center gap-2"><KeyRound className="size-3.5" /> Reference solution</h3>
      <p className="mb-3 text-sm text-muted-foreground">A minimal solution that passes the suite. Compare it with yours: what did it do more simply, or handle that you did not?</p>
      <div className="space-y-3">{files.map((f) => <div key={f.path}><div className="mb-1 font-mono text-xs text-muted-foreground">{f.path}</div><Code lang="python" text={f.content} /></div>)}</div>
    </section>
  );
}
