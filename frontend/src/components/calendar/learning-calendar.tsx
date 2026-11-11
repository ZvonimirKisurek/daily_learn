"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { withTrack, type Track } from "@/lib/tracks";
import { cn } from "@/lib/utils";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const pad = (n: number) => String(n).padStart(2, "0");
const monthLabel = (y: number, m: number) =>
  new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(Date.UTC(y, m, 1)));

/**
 * Month grid of learning days.
 *
 * Client Component because the visible month is state. The data is NOT fetched here: the page (a Server Component)
 * calls the API with the secret token and passes `days` / `today` down as props. Fetching "on mount" from the browser
 * would need the token in the browser (or a proxy route) plus a loading flash; this way the first paint is complete.
 *
 * `today` comes from the SERVER (the learner's configured TIMEZONE), never from `new Date()` in the browser, so a
 * calendar opened from another timezone still agrees with the scheduler about which day is "today".
 * All date maths is done on UTC calendar fields, so a browser's local DST/offset can never shift a cell by a day.
 */
export function LearningCalendar({ days, done, today, track = "ai" }: { days: string[]; done: string[]; today: string; track?: Track }) {
  const have = useMemo(() => new Set(days), [days]);
  const finished = useMemo(() => new Set(done), [done]);
  const [ty, tm] = [Number(today.slice(0, 4)), Number(today.slice(5, 7)) - 1];
  const [view, setView] = useState({ y: ty, m: tm });

  // Navigation bounds: from the earliest month that has a briefing (or this month) to the current month. Nothing exists in the future.
  const first = days[0] ?? today;
  const minIdx = Number(first.slice(0, 4)) * 12 + Number(first.slice(5, 7)) - 1;
  const idx = view.y * 12 + view.m;
  const step = (d: number) => setView(({ y, m }) => { const n = y * 12 + m + d; return { y: Math.floor(n / 12), m: n % 12 }; });

  const lead = (new Date(Date.UTC(view.y, view.m, 1)).getUTCDay() + 6) % 7; // blank cells before the 1st (weeks start Monday)
  const count = new Date(Date.UTC(view.y, view.m + 1, 0)).getUTCDate();
  const cells: (number | null)[] = [...Array<null>(lead).fill(null), ...Array.from({ length: count }, (_, i) => i + 1)];
  const inMonth = days.filter((d) => d.startsWith(`${view.y}-${pad(view.m + 1)}`)).length;

  return (
    <section aria-label="Learning calendar" className="glass mx-auto w-full max-w-md rounded-2xl p-5">
      <div className="mb-4 flex items-center justify-between">
        <button onClick={() => step(-1)} disabled={idx <= minIdx} aria-label="Previous month"
          className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground disabled:pointer-events-none disabled:opacity-30">
          <ChevronLeft className="size-5" />
        </button>
        <div className="text-center">
          <h2 className="font-semibold tracking-tight">{monthLabel(view.y, view.m)}</h2>
          <p className="font-mono text-[11px] text-muted-foreground">{inMonth} {inMonth === 1 ? "lesson" : "lessons"}</p>
        </div>
        <button onClick={() => step(1)} disabled={idx >= ty * 12 + tm} aria-label="Next month"
          className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground disabled:pointer-events-none disabled:opacity-30">
          <ChevronRight className="size-5" />
        </button>
      </div>

      <div className="grid grid-cols-7 gap-1.5 text-center">
        {WEEKDAYS.map((w) => <span key={w} className="pb-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{w}</span>)}
        {cells.map((d, i) => {
          if (d === null) return <span key={`b${i}`} aria-hidden />;
          const key = `${view.y}-${pad(view.m + 1)}-${pad(d)}`;
          const isToday = key === today;
          const has = have.has(key);
          const base = "relative grid aspect-square place-items-center rounded-lg text-sm tabular-nums";
          const ok = finished.has(key);  // green = whole lesson done, yellow = still open
          const dot = has && <span aria-hidden className={cn("absolute bottom-1.5 size-1 rounded-full", ok ? "bg-primary" : "bg-warn")} />;
          // Today always links home (its briefing may still be pending, and "/" handles that state). A past day is a link
          // only if a briefing exists; otherwise it is plain, dimmed text, so there is nothing to click.
          if (isToday || has)
            return (
              <Link key={key} href={withTrack(isToday ? "/" : `/day/${key}`, track)} aria-current={isToday ? "date" : undefined}
                aria-label={`${key}${isToday ? " (today)" : ""}${has ? (ok ? ", finished" : ", not finished") : ""}`}
                className={cn(base, "font-medium transition-colors",
                  !has ? "text-foreground hover:bg-white/10"
                    : ok ? "bg-primary/20 text-primary ring-1 ring-inset ring-primary/40 hover:bg-primary/30"
                    : "bg-warn/15 text-warn ring-1 ring-inset ring-warn/40 hover:bg-warn/25",
                  isToday && "ring-2 ring-foreground/70")}>
                {d}{dot}
              </Link>
            );
          return <span key={key} aria-label={`${key}, no briefing`} className={cn(base, "text-muted-foreground/35")}>{d}</span>;
        })}
      </div>

      <div className="mt-4 flex items-center justify-center gap-4 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5"><span className="size-3 rounded-[5px] bg-primary/30 ring-1 ring-primary/50" /> finished</span>
        <span className="inline-flex items-center gap-1.5"><span className="size-3 rounded-[5px] bg-warn/25 ring-1 ring-warn/50" /> not finished</span>
        <span className="inline-flex items-center gap-1.5"><span className="size-3 rounded-[5px] ring-2 ring-foreground/70" /> today</span>
      </div>
    </section>
  );
}
