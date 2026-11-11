"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Sparkles } from "lucide-react";
import { generateBriefingAction } from "@/app/actions";
import { Button } from "@/components/ui/button";
import type { Track } from "@/lib/tracks";

type Phase = "idle" | "queued" | "timeout" | "error";

/**
 * Empty state for "the backend hasn't generated today's briefing yet" (before 06:30, or generation failed).
 * Generation is an LLM call that can take a minute, so it runs in a Celery worker: this button only
 * ENQUEUES the job (the API answers 202 at once), then we poll by re-rendering the server components with
 * router.refresh(). As soon as /api/today reports state="ready", the page swaps this component for the mission.
 */
export function PendingBriefing({ track = "ai" }: { track?: Track }) {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState("");
  const [pending, startTransition] = useTransition();

  useEffect(() => {
    if (phase !== "queued") return;
    const poll = setInterval(() => router.refresh(), 5_000);
    const giveUp = setTimeout(() => setPhase("timeout"), 3 * 60_000);
    return () => { clearInterval(poll); clearTimeout(giveUp); };
  }, [phase, router]);

  function generate() {
    startTransition(async () => {
      const res = await generateBriefingAction(track);
      if (!res.ok) { setMessage(res.error); setPhase("error"); }
      else if (res.data.queued) setPhase("queued");
    });
  }

  return (
    <div className="rounded-lg border border-dashed p-8 text-center">
      <Sparkles className="mx-auto mb-3 size-6 text-primary" />
      <h2 className="text-lg font-semibold">Today&apos;s {track === "management" ? "management lesson" : "lesson"} isn&apos;t ready yet</h2>
      <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
        It&apos;s generated every morning from the latest articles and your current level. You can also generate it now.
        {track === "management" && " The first time, the management sources must have been ingested (it runs every 6 hours)."}
      </p>
      {phase === "queued" ? (
        <p className="mt-4 inline-flex items-center gap-2 text-sm text-muted-foreground" role="status">
          <Loader2 className="size-4 animate-spin" /> Generating… this page updates automatically.
        </p>
      ) : (
        <Button className="mt-4" onClick={generate} disabled={pending}>
          {pending ? <Loader2 className="animate-spin" /> : <Sparkles />} Generate now
        </Button>
      )}
      {phase === "timeout" && <p className="mt-3 text-sm text-warn">Still not ready. Check the Celery worker logs, then try again.</p>}
      {phase === "error" && <p role="alert" className="mt-3 text-sm text-destructive">{message}. Are Redis and the Celery worker running?</p>}
    </div>
  );
}
