"use client";

import { useEffect, useState, useSyncExternalStore, useTransition } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, X, Zap } from "lucide-react";
import { generateBitcoinAction } from "@/app/actions";
import type { BitcoinRun } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * A 1-second clock as an external store. The server snapshot is null: "now" differs between the server render and the
 * browser, so anything time-based (elapsed seconds, "updated just now") renders only after hydration and never
 * mismatches. useSyncExternalStore is React's sanctioned way to read a value that changes outside React.
 */
let clock = Date.now();
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
function subscribe(cb: () => void) {
  listeners.add(cb);
  timer ??= setInterval(() => { clock = Date.now(); listeners.forEach((l) => l()); }, 1_000);
  return () => {
    listeners.delete(cb);
    if (!listeners.size && timer) { clearInterval(timer); timer = null; }
  };
}
const useNow = () => useSyncExternalStore<number | null>(subscribe, () => clock, () => null);

/**
 * Starts a forecast and reports its real status.
 *
 * The status is SERVER STATE: every render receives the latest `bitcoin_runs` row (`run`), so the button knows a run is
 * queued or running even after a page reload, and shows the stored failure reason if one failed. While a run is in
 * flight the component polls by re-rendering the Server Components (router.refresh() every 4 s). There is no
 * client-side guesswork about when the job finished.
 *
 * Errors that happen before a run exists (the API answering 503 "Analysis engine offline", a cooldown refusal) come back
 * from the Server Action and appear in the same popover.
 */
export function BitcoinRefreshButton({ run, size = "sm", label = "Refresh" }: { run: BitcoinRun | null; size?: "sm" | "lg"; label?: string }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [notice, setNotice] = useState<{ kind: "error" | "info"; text: string } | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const now = useNow();

  const active = run != null && (run.status === "running" || (run.status === "queued" && !run.stalled));
  const stalled = run?.status === "queued" && run.stalled;
  const failed = run?.status === "failed";

  // Poll while the worker is busy.
  useEffect(() => {
    if (!active) return;
    const poll = setInterval(() => router.refresh(), 4_000);
    return () => clearInterval(poll);
  }, [active, router]);

  function start() {
    setNotice(null);
    startTransition(async () => {
      const res = await generateBitcoinAction();
      if (!res.ok) setNotice({ kind: "error", text: res.error });
      else if (!res.data.queued && res.data.reason) setNotice({ kind: "info", text: res.data.reason });
    });
  }

  const busy = pending || active;
  const since = run && now ? Math.max(0, Math.round((now - new Date(run.started_at ?? run.created_at).getTime()) / 1000)) : 0;
  // Popover for: an action error/notice, or a RECENT (30 min) failed/stalled run the user hasn't dismissed. An old failure
  // shouldn't greet every future visit; the next scheduled run replaces it anyway.
  const recent = run && now != null && now - new Date(run.finished_at ?? run.created_at).getTime() < 30 * 60_000;
  const runProblem = run && recent && (failed || stalled) && dismissed !== run.id
    ? { kind: "error" as const, text: failed ? run.error ?? "The analysis failed." : "Queued for over 2 minutes and no worker picked it up. Is the Celery worker running? (celery -A app.celery_app worker)" }
    : null;
  const pop = notice ?? runProblem;

  return (
    <div className="relative flex items-center gap-3">
      <AnimatePresence mode="wait">
        {active && (
          <motion.span key="status" role="status" initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 8 }}
            className="hidden items-center gap-2 font-mono text-[10px] uppercase tracking-[0.14em] text-cyan-300 sm:flex">
            <span className="relative flex size-1.5"><span className="absolute size-full animate-ping rounded-full bg-cyan-300 opacity-70 motion-reduce:animate-none" /><span className="relative size-1.5 rounded-full bg-cyan-300" /></span>
            {run?.status === "queued" ? "Queued" : "Analysing"} · {since}s
          </motion.span>
        )}
        {!active && run?.status === "succeeded" && run.finished_at && now != null && now - new Date(run.finished_at).getTime() < 60_000 && (
          <motion.span key="ok" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="hidden items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-emerald-300 sm:flex">
            <CheckCircle2 className="size-3.5" aria-hidden /> Updated
          </motion.span>
        )}
      </AnimatePresence>

      <motion.button type="button" onClick={start} disabled={busy} whileTap={{ scale: 0.96 }}
        className={cn(
          "group relative inline-flex items-center gap-2 overflow-hidden rounded-lg border border-emerald-400/40 bg-emerald-400/10 font-mono font-semibold uppercase text-emerald-200 transition-[background,box-shadow] hover:bg-emerald-400/20 hover:shadow-[0_0_24px_-4px_rgba(52,211,153,0.6)] disabled:cursor-wait disabled:opacity-70",
          size === "lg" ? "px-5 py-2.5 text-xs tracking-[0.18em]" : "px-3.5 py-1.5 text-[11px] tracking-[0.16em]",
        )}>
        {/* a light sweep across the button while busy */}
        {busy && <span aria-hidden className="shimmer pointer-events-none absolute inset-0 bg-transparent" />}
        {busy ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : size === "lg" ? <Zap className="size-3.5" aria-hidden /> : <RefreshCw className="size-3.5 transition-transform group-hover:rotate-90" aria-hidden />}
        {busy ? "Analysing" : label}
      </motion.button>

      <AnimatePresence>
        {pop && (
          <motion.div role="alert" initial={{ opacity: 0, y: -6, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.2 }}
            className={cn("absolute top-full right-0 z-40 mt-2 w-[min(22rem,calc(100vw-2rem))] rounded-xl border p-3 pr-8 text-xs leading-relaxed shadow-2xl backdrop-blur-xl",
              pop.kind === "error" ? "border-rose-400/30 bg-[#140a0d]/95 text-rose-100" : "border-cyan-400/30 bg-[#08121a]/95 text-cyan-100")}>
            <p className="flex gap-2">
              <AlertTriangle className={cn("mt-0.5 size-3.5 shrink-0", pop.kind === "error" ? "text-rose-400" : "text-cyan-300")} aria-hidden />
              <span>{pop.text}</span>
            </p>
            <button type="button" aria-label="Dismiss" onClick={() => { setNotice(null); if (run) setDismissed(run.id); }}
              className="absolute top-2 right-2 rounded p-0.5 text-muted-foreground hover:text-foreground">
              <X className="size-3.5" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
