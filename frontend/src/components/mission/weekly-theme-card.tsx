import { CalendarDays, ChevronDown, Compass, GraduationCap } from "lucide-react";
import { Markdown } from "@/components/markdown";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { skillLabel } from "@/lib/skills";
import type { WeeklyTheme } from "@/lib/types";
import { cn, isoWeekday } from "@/lib/utils";
import { ExternalLink } from "./task-bodies";

const DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Pure Server Component: no interactivity, so it ships zero JavaScript to the browser. */
export function WeeklyThemeCard({ weekly, today }: { weekly: WeeklyTheme; today: string }) {
  const t = weekly.theme;
  const todayIdx = isoWeekday(today);
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-widest text-violet">
          <Compass className="size-3.5" /> This week&apos;s theme
          {t.roadmap && <Badge variant="violet" className="ml-1">Roadmap · week {t.roadmap.index} of {t.roadmap.of}</Badge>}
        </div>
        <CardTitle className="text-lg leading-snug">{t.title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <p className="leading-relaxed text-muted-foreground">{t.rationale}</p>
        {t.seminar_overview && (
          <details className="group rounded-xl border border-white/[0.08] bg-white/[0.025]">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-4 py-3 text-sm font-medium">
              <span className="flex items-center gap-2"><GraduationCap className="size-4 text-violet" /> Read this week&apos;s seminar syllabus</span>
              <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
            </summary>
            <div className="border-t border-white/[0.07] px-4 pb-4 pt-3"><Markdown compact>{t.seminar_overview}</Markdown></div>
          </details>
        )}
        {t.core_questions?.length > 0 && (
          <div>
            <div className="eyebrow mb-1.5">Questions this week answers</div>
            <ol className="list-decimal space-y-1 pl-5 marker:font-mono marker:text-violet">{t.core_questions.map((q) => <li key={q}>{q}</li>)}</ol>
          </div>
        )}
        <div className="flex flex-wrap gap-1.5">
          {t.focus_skills.map((s) => <Badge key={s} variant="violet">{skillLabel(s)}</Badge>)}
        </div>
        {t.roadmap && (
          <div className="rounded-md border border-violet/25 bg-violet/5 p-3">
            <div className="mb-1 font-mono text-[10px] uppercase tracking-widest text-violet">Week deliverable</div>
            <p>{t.roadmap.deliverable}</p>
            <ul className="mt-2 space-y-1 text-muted-foreground">
              {t.roadmap.milestones.map((m) => <li key={m} className="flex gap-2"><span className="mt-2 size-1 shrink-0 rounded-full bg-violet" />{m}</li>)}
            </ul>
            {t.roadmap.quarter && (
              <p className="mt-3 border-t border-violet/20 pt-2 text-xs text-muted-foreground">
                <span className="font-mono uppercase tracking-widest text-violet">Quarter {t.roadmap.quarter.index} public deliverable</span> (week {t.roadmap.quarter.weeks[1]}): {t.roadmap.quarter.public_deliverable}
              </p>
            )}
            {t.math_topics && t.math_topics.length > 0 && <p className="mt-2 text-xs text-muted-foreground"><span className="font-mono uppercase tracking-widest text-violet">Math drill</span> {t.math_topics.join(" · ")}</p>}
          </div>
        )}
        <ul className="space-y-1">
          {t.learning_objectives.map((o) => (
            <li key={o} className="flex gap-2"><span className="mt-2 size-1 shrink-0 rounded-full bg-violet" />{o}</li>
          ))}
        </ul>
        <div>
          <div className="mb-2 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
            <CalendarDays className="size-3" /> Plan
          </div>
          <ol className="grid grid-cols-7 gap-1.5">
            {t.daily_plan.map((d) => (
              <li
                key={d.day}
                title={`${d.focus} (${skillLabel(d.skill_slug)}, ${d.intensity})`}
                className={cn("rounded-md border p-1.5 text-center", d.day === todayIdx ? "border-primary bg-primary/10" : "bg-muted/40", d.intensity === "light" && "opacity-70")}
              >
                <div className="font-mono text-[10px] text-muted-foreground">{DAY[d.day - 1]}</div>
                <div className="mt-0.5 truncate text-[10px] leading-tight">{skillLabel(d.skill_slug)}</div>
              </li>
            ))}
          </ol>
          {t.daily_plan.find((d) => d.day === todayIdx) && (
            <p className="mt-2 text-xs text-muted-foreground">Today: {t.daily_plan.find((d) => d.day === todayIdx)?.focus}</p>
          )}
        </div>
        {t.market_signals.length > 0 && (
          <div>
            <div className="mb-1.5 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">Market signals</div>
            <ul className="list-disc space-y-1 pl-5 marker:text-muted-foreground">{t.market_signals.map((s) => <li key={s}>{s}</li>)}</ul>
          </div>
        )}
        {t.key_articles.length > 0 && (
          <div>
            <div className="mb-1.5 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">Evidence</div>
            <ul className="space-y-1">
              {t.key_articles.map((a) => (
                <li key={a.article_uuid} className="flex gap-2"><span className="text-muted-foreground">{a.source}</span><ExternalLink href={a.url}>{a.title}</ExternalLink></li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
