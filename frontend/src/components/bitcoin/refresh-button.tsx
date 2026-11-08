"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2, RefreshCw } from "lucide-react";
import { generateBitcoinAction } from "@/app/actions";
import { cn } from "@/lib/utils";

type Phase = "idle" | "queued" | "done" | "skipped" | "timeout" | "error";

/**
 * Enqueues a fresh forecast, then polls by re-rendering the Server Components (router.refresh()) every 5 s until the
 * page receives a prediction id different from the one we started with. The worker needs 15-60 s: it fetches five
 * feeds, two price endpoints, and makes one LLM call, plus a repair round-trip if validation fails.
 */
export function BitcoinRefreshButton({ latestId }: { latestId: string | null }) {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState("");
  const [pending, startTransition] = useTransition();
  const startedWith = useRef<string | null>(null);

  // A new id arriving while we wait means the worker finished.
  useEffect(() => {
    if (phase === "queued" && latestId !== startedWith.current) setPhase("done");
  }, [latestId, phase]);

  useEffect(() => {
    if (phase !== "queued") return;
    const poll = setInterval(() => router.refresh(), 5_000);
    const giveUp = setTimeout(() => setPhase("timeout"), 3 * 60_000);
    return () => { clearInterval(poll); clearTimeout(giveUp); };
  }, [phase, router]);

  function run() {
    startedWith.current = latestId;
    startTransition(async () => {
      const res = await generateBitcoinAction();
      if (!res.ok) { setMessage(res.error); setPhase("error"); }
      else if (res.data.queued) setPhase("queued");
      else { setMessage(res.data.reason ?? "Already up to date"); setPhase("skipped"); }
    });
  }

  const busy = pending || phase === "queued";
  return (
    <div className="flex items-center gap-3">
      <p role="status" aria-live="polite" className={cn("hidden font-mono text-[10px] uppercase tracking-wider sm:block",
        phase === "error" || phase === "timeout" ? "text-rose-400" : "text-muted-foreground")}>
        {phase === "queued" && "Running analysis…"}
        {phase === "done" && "Updated"}
        {phase === "skipped" && message}
        {phase === "timeout" && "Still running. Check the worker logs"}
        {phase === "error" && message}
      </p>
      <button type="button" onClick={run} disabled={busy}
        className="inline-flex items-center gap-2 rounded border border-amber-400/40 bg-amber-400/10 px-3 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-amber-300 transition-colors hover:bg-amber-400/20 disabled:cursor-wait disabled:opacity-60">
        {busy ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <RefreshCw className="size-3.5" aria-hidden />}
        {busy ? "Analysing" : "Refresh"}
      </button>
    </div>
  );
}
