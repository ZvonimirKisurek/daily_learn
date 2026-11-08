import { cn } from "@/lib/utils";

/**
 * The dashboard's basic surface: a near-black panel with a hairline border and a monospace header strip, like a
 * terminal window. Server Component (no interactivity), so it adds no client JS.
 */
export function TerminalPanel({ title, meta, className, bodyClassName, children }: {
  title: string; meta?: React.ReactNode; className?: string; bodyClassName?: string; children: React.ReactNode;
}) {
  return (
    <section className={cn("min-w-0 overflow-hidden rounded-lg border border-white/[0.08] bg-[#090a0e]/90 shadow-[0_20px_50px_-30px_rgba(0,0,0,0.9)]", className)}>
      <header className="flex items-center justify-between gap-3 border-b border-white/[0.07] bg-white/[0.02] px-4 py-2">
        <h2 className="flex items-center gap-2 font-mono text-[11px] font-semibold uppercase tracking-[0.16em] text-amber-300/90">
          <span aria-hidden className="size-1.5 rounded-full bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.8)]" />
          {title}
        </h2>
        {meta && <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{meta}</div>}
      </header>
      <div className={cn("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}

/** A label/value cell for the dense data grids. */
export function Stat({ label, value, valueClassName }: { label: React.ReactNode; value: React.ReactNode; valueClassName?: string }) {
  return (
    <div className="min-w-0">
      <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">{label}</dt>
      <dd className={cn("mt-0.5 truncate font-mono text-sm tabular-nums", valueClassName)}>{value}</dd>
    </div>
  );
}
