import Link from "next/link";
import { ArrowLeft, Clock, Gauge, GraduationCap, Library, ScrollText } from "lucide-react";
import { Markdown } from "@/components/markdown";
import { Badge } from "@/components/ui/badge";
import { skillLabel } from "@/lib/skills";
import { slugify } from "@/lib/slug";
import { withTrack } from "@/lib/tracks";
import type { TaskDetail } from "@/lib/types";
import { ExternalLink } from "@/components/mission/task-bodies";
import { Reveal } from "@/components/motion";
import { ChapterQuiz } from "./chapter-quiz";
import { FinishChapter } from "./finish-chapter";
import { ReadingProgress } from "./reading-progress";

const LEVEL = ["", "From scratch", "Intermediate", "Advanced"];

/** The digital textbook page: a server-rendered chapter with a sticky table of contents. */
export function ReadingView({ detail }: { detail: TaskDetail }) {
  const r = detail.reading!;
  return (
    <div>
      <ReadingProgress />
      <Link href={withTrack("/", detail.track)} className="mb-6 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"><ArrowLeft className="size-4" /> Today&apos;s lesson</Link>

      <Reveal>
        <header className="mb-10 max-w-3xl">
          <div className="eyebrow mb-3 flex items-center gap-2 !text-info"><ScrollText className="size-3.5" /> {detail.lesson ? `Level ${detail.lesson.level} · ${detail.lesson.title}` : "Chapter"} · {skillLabel(r.skill_slug)}</div>
          <h1 className="text-4xl font-semibold leading-[1.1] tracking-tight sm:text-5xl"><span className="gradient-text">{r.chapter_title}</span></h1>
          <div className="mt-5 flex flex-wrap items-center gap-2.5 text-sm text-muted-foreground">
            <Badge variant="outline"><Clock className="size-3" /> {r.estimated_minutes} min</Badge>
            <Badge variant="outline"><Library className="size-3" /> {r.word_count.toLocaleString("en-US")} words</Badge>
            <Badge variant="outline"><Gauge className="size-3" /> {LEVEL[r.difficulty] ?? "Intermediate"}</Badge>
            {detail.task.completed && <Badge variant="primary">read</Badge>}
          </div>
          <div className="glass mt-6 rounded-2xl p-5">
            <div className="eyebrow mb-2">After this chapter you can</div>
            <ul className="list-disc space-y-1.5 pl-5 text-[15px] marker:text-primary">{r.learning_goals.map((g) => <li key={g}>{g}</li>)}</ul>
          </div>
        </header>
      </Reveal>

      <div className="grid gap-12 lg:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="Chapter contents" className="hidden lg:block">
          <div className="sticky top-24">
            <div className="eyebrow mb-3">Contents</div>
            <ol className="space-y-1 border-l border-white/10">
              {r.section_headings.map((h, i) => (
                <li key={h}><a href={`#${slugify(h)}`} className="-ml-px block border-l border-transparent py-1 pl-3 text-[13px] leading-snug text-muted-foreground transition-colors hover:border-primary hover:text-foreground">
                  <span className="mr-1.5 font-mono text-[11px] opacity-50">{i + 1}</span>{h}
                </a></li>
              ))}
            </ol>
          </div>
        </nav>

        <article>
          {r.content ? <Markdown>{r.content}</Markdown> : <p className="text-muted-foreground">This chapter has no content yet. Regenerate today&apos;s briefing.</p>}
          {r.inspiration.length > 0 && (
            <aside className="mt-12 max-w-[72ch] rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5">
              <div className="eyebrow mb-2">Case-study sources cited above</div>
              <ul className="space-y-1 text-sm">
                {r.inspiration.map((a, i) => <li key={a.article_uuid} className="flex gap-2"><span className="font-mono text-muted-foreground">[{a.ref_id ?? `A${i + 1}`}]</span><ExternalLink href={a.url}>{a.title}</ExternalLink></li>)}
              </ul>
              <p className="mt-2 text-xs text-muted-foreground">Optional: the chapter is self-contained. These are where the real-world examples came from.</p>
            </aside>
          )}
          {(detail.lesson?.canon?.length ?? 0) > 0 && (
            <aside className="mt-6 max-w-[72ch] rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5">
              <div className="eyebrow mb-2 flex items-center gap-2"><GraduationCap className="size-3.5" /> University reading list · Level {detail.lesson!.level}</div>
              <ul className="space-y-1.5 text-sm">
                {detail.lesson!.canon!.map((c) => <li key={c}><ExternalLink href={`https://www.google.com/search?q=${encodeURIComponent(c)}`}>{c}</ExternalLink></li>)}
              </ul>
              <p className="mt-2 text-xs text-muted-foreground">Optional: what a top university assigns for this level. Each link opens a search for the course, book or paper.</p>
            </aside>
          )}
          <div className="max-w-[72ch]">
            {detail.quiz ? <ChapterQuiz taskId={detail.task.id} questions={detail.quiz} completed={detail.task.completed} score={detail.task.quiz_score} home={withTrack("/", detail.track)} />
              : <FinishChapter taskId={detail.task.id} completed={detail.task.completed} skillSlug={r.skill_slug} />}
          </div>
        </article>
      </div>
    </div>
  );
}
