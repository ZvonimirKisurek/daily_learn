import Link from "next/link";
import { Briefcase, BrainCircuit } from "lucide-react";
import { TRACK_LABEL, TRACK_SHORT, TRACKS, withTrack, type Track } from "@/lib/tracks";
import { cn } from "@/lib/utils";

const ICON = { ai: BrainCircuit, management: Briefcase } as const;

/** Switch between learning tracks. Plain links: the track lives in the URL (?track=management), so it survives reloads. */
export function TrackTabs({ track, base }: { track: Track; base: string }) {
  return (
    <nav aria-label="Learning track" className="inline-flex max-w-full gap-1 rounded-xl border border-white/[0.08] bg-white/[0.03] p-1">
      {TRACKS.map((t) => {
        const Icon = ICON[t];
        const active = t === track;
        return (
          <Link key={t} href={withTrack(base, t)} aria-current={active ? "page" : undefined}
            className={cn("inline-flex items-center gap-2 rounded-lg px-3.5 py-1.5 text-sm font-medium transition-colors",
              active ? (t === "management" ? "bg-amber-400/15 text-amber-200 ring-1 ring-amber-400/30" : "bg-primary/15 text-primary ring-1 ring-primary/30") : "text-muted-foreground hover:text-foreground")}>
            <Icon className="size-4 shrink-0" />
            {/* phones get the short name, so both tabs always fit on one line */}
            <span className="sm:hidden">{TRACK_SHORT[t]}</span><span className="hidden sm:inline">{TRACK_LABEL[t]}</span>
          </Link>
        );
      })}
    </nav>
  );
}
