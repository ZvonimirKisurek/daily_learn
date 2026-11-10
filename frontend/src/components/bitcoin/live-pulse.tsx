"use client";

import { useEffect, useRef, useState } from "react";
import { bitcoinPulseAction } from "@/app/actions";
import type { BitcoinPulse } from "@/lib/types";
import { cn } from "@/lib/utils";

const EVERY_MS = 60_000;

const say = (p: BitcoinPulse): string =>
  p.forecast === "started" ? `New forecast started: ${p.reason ?? "fresh news"}`
    : p.forecast === "running" ? "A new forecast is being written"
    : p.forecast === "cooldown" ? p.reason ?? "New headlines; forecast shortly"
    : p.news_added > 0 ? `${p.news_added} new headline${p.news_added === 1 ? "" : "s"}`
    : p.price_updated ? "Price updated" : "Up to date";

/**
 * Keeps the Bitcoin desk current WHILE YOU LOOK AT IT, and only then. On open, when the tab becomes visible again, and
 * once a minute in between, it asks the server to refresh the price and the news; the server starts a new AI forecast
 * only if fresh Bitcoin headlines arrived (rate-limited). A hidden or closed tab sends nothing, so nothing runs.
 */
export function LivePulse() {
  const [last, setLast] = useState<{ text: string; at: string; ok: boolean } | null>(null);
  const busy = useRef(false);

  useEffect(() => {
    let alive = true;
    async function tick() {
      if (busy.current || document.visibilityState !== "visible") return;
      busy.current = true;
      const res = await bitcoinPulseAction();
      busy.current = false;
      if (!alive) return;
      const at = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date());
      setLast(res.ok ? { text: say(res.data), at, ok: true } : { text: res.error, at, ok: false });
    }
    tick();
    const id = setInterval(tick, EVERY_MS);
    document.addEventListener("visibilitychange", tick);
    return () => { alive = false; clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, []);

  return (
    <span role="status" title="Updates only while this page is open and visible" className="inline-flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
      <span className="relative flex size-1.5">
        <span className={cn("absolute size-full rounded-full opacity-70 motion-safe:animate-ping", last?.ok === false ? "bg-amber-400" : "bg-emerald-400")} />
        <span className={cn("relative size-1.5 rounded-full", last?.ok === false ? "bg-amber-400" : "bg-emerald-400")} />
      </span>
      <span className={last?.ok === false ? "text-amber-300" : "text-emerald-300/90"}>Live</span>
      {last && <span className="normal-case tracking-normal">{last.text} · {last.at}</span>}
    </span>
  );
}
