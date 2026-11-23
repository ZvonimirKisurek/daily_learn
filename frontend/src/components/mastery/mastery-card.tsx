import Link from "next/link";
import { BrainCircuit, CalendarCheck2, History, Lock, Sigma, Unlock } from "lucide-react";
import { skillLabel } from "@/lib/skills";
import type { CatchupItem, Mastery } from "@/lib/types";
import { cn } from "@/lib/utils";

const pct = (v: number | null) => (v === null ? "–" : `${Math.round(v * 100)}%`);

function Tile({ href, icon: Icon, title, value, sub, alert }: { href: string; icon: typeof Sigma; title: string; value: string; sub: string; alert?: boolean }) {
  return (
    <Link href={href} className={cn("group rounded-xl border p-3.5 transition-colors hover:bg-white/[0.04]", alert ? "border-warn/40 bg-warn/[0.06]" : "border-white/[0.08] bg-white/[0.02]")}>
      <div className="eyebrow mb-1.5 flex items-center gap-1.5"><Icon className="size-3.5" /> {title}</div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>
    </Link>
  );
}

const KIND = { reading: "Chapter", hands_on: "Lab", insight: "Essay" } as const;
const href = (t: CatchupItem) => (t.kind === "reading" ? `/read/${t.id}` : t.kind === "hands_on" ? `/practice/${t.id}` : `/day/${t.day}`);

/** Lessons you did not finish recently. They stay doable until they expire, oldest first. */
function Catchup({ items }: { items: CatchupItem[] }) {
  return (
    <div className="mt-4 rounded-xl border border-info/25 bg-info/[0.04] p-3.5">
      <div className="eyebrow mb-2 flex items-center gap-1.5 !text-info"><History className="size-3.5" /> Catch-up · {items.length} unfinished lesson{items.length === 1 ? "" : "s"}</div>
      <ul className="space-y-1.5 text-sm">
        {items.slice(0, 6).map((t) => (
          <li key={t.id} className="flex flex-wrap items-baseline justify-between gap-x-3">
            <Link href={href(t)} className="min-w-0 truncate underline-offset-4 hover:underline">
              <span className="mr-2 font-mono text-xs text-muted-foreground">{t.day.slice(5)} · {KIND[t.kind]}</span>{t.title}
            </Link>
            <span className="text-xs text-muted-foreground">{skillLabel(t.skill_slug)} · {t.expires_on ? `until ${t.expires_on.slice(5)}` : "any time"}</span>
          </li>
        ))}
      </ul>
      {items.length > 6 && <p className="mt-1.5 text-xs text-muted-foreground">+{items.length - 6} more in the Calendar</p>}
    </div>
  );
}

/**
 * The mastery loop at a glance (Server Component). The gate explains itself: when new work is locked, the reasons
 * come from the API, and every tile links to the page that unlocks it.
 */
export function MasteryCard({ m }: { m: Mastery }) {
  const r = m.reviews;
  const retroOwed = m.retro.required && !m.retro.submitted;
  return (
    <section className={cn("glass rounded-2xl p-5", m.gate.locked && "ring-1 ring-warn/40")} aria-label="Mastery loop">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold">
          {m.gate.locked ? <Lock className="size-4 text-warn" /> : <Unlock className="size-4 text-primary" />}
          {m.gate.locked ? "New work is locked" : "Mastery loop"}
        </h2>
        <span className="text-xs text-muted-foreground">retrieval before new material · spacing · objective feedback</span>
      </div>
      {m.gate.locked && (
        <ul className="mb-4 space-y-1.5 rounded-xl border border-warn/30 bg-warn/[0.06] p-3.5 text-sm">
          {m.gate.reasons.map((reason) => <li key={reason} className="leading-relaxed">{reason}</li>)}
        </ul>
      )}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Tile href="/review" icon={BrainCircuit} title="Review" alert={r.overdue > 0} value={r.due ? `${r.due} due` : "clear"}
          sub={`${r.reviewed_today} done today · retention ${pct(r.retention_30d)}`} />
        <Tile href="/math" icon={Sigma} title="Math drill" value={`${m.math.attempted}/${m.math.total}`}
          sub={`${m.math.correct} right today · 30-day ${pct(m.math.accuracy_30d)}`} />
        <Tile href={`/retro?week=${m.retro.week_start}`} icon={CalendarCheck2} title="Retrospective" alert={retroOwed}
          value={retroOwed ? "owed" : m.retro.submitted ? "done" : "–"} sub={`week of ${m.retro.week_start}`} />
      </div>
      {m.catchup && m.catchup.length > 0 && <Catchup items={m.catchup} />}
    </section>
  );
}
